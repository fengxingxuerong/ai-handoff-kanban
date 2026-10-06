# -*- coding: utf-8 -*-
"""
LLM 网关：多端点 + 多 Key 轮询 + 失败自动切换
- 端点来自本机配置 ~/.ai-kanban/config.json（可在应用设置界面修改）
- 调用策略：优先 active 端点，失败按序切换其他启用端点；同端点内多 Key 轮询
- 429 限流 / 401·403 Key 失效 / 402 余额不足 / 404·410 模型下线 / 5xx / 超时
  都算这次尝试失败，换下一个 Key，Key 用尽换下一个端点；全部失败抛 LLMError

**没有"滑动窗口限流"，也没有失败冷却**：失败就是当场换下一个，不记状态。
代价是端点×Key 全挂时最坏要各等一次超时（每端点 60s 默认），
`test_connection` 同理会把矩阵跑满 —— 这是有意的：单机单用户，宁可慢一次，
也不要在网关里藏一个"刚才失败过所以这轮不试"的自适应状态（那种状态最难测）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .filelock import blocking_lock

DEFAULT_TIMEOUT = 60
MAX_TOKENS = 3000

# 配置文件的读-改-写串行化：调度线程与设置界面可能同时写 config.json
_CFG_LOCK = threading.RLock()


class LLMError(Exception):
    pass


def config_file() -> Path:
    env = os.environ.get("KANBAN_DATA_DIR")
    if env:
        d = Path(env)
    else:
        home = os.environ.get("USERPROFILE") or str(Path.home())
        d = Path(home) / ".ai-kanban"
    d.mkdir(parents=True, exist_ok=True)
    return d / "config.json"


# 默认端点模板（Key 为空，由设置界面或本机初始化写入）
DEFAULT_ENDPOINTS = [
    {
        "name": "SenseNova",
        "base_url": "https://token.sensenova.cn/v1",
        "api_keys": [],
        "models": ["deepseek-v4-flash", "kimi-k3", "glm-5.2", "deepseek-v4-pro"],
        "enabled": True,
    },
    {
        "name": "AMD Radeon",
        "base_url": "https://developer.amd.com.cn/radeon/api/v1",
        "api_keys": [],
        "models": ["DeepSeek-V4-Flash"],
        "enabled": True,
    },
    {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_keys": [],
        "models": ["stealth/ox-alpha"],
        "enabled": True,
    },
    {
        "name": "NVIDIA",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_keys": [],
        "models": ["z-ai/glm-5.2"],
        "enabled": True,
    },
]


def default_config() -> dict:
    return {
        "ai": {
            "endpoints": DEFAULT_ENDPOINTS,
            "active_model": "deepseek-v4-flash",
            "schedule": {"enabled": False, "frequency": "daily", "time": "09:00", "last_run": ""},
            "session_sources": [],   # 自定义会话捕获源 [{name, path, type}]
            "mcp_readonly": False,   # MCP 只读模式（外部智能体只能读不能写）
            "budget_limit": 0.0,     # 月度用量预算上限（美元）；0 = 不启用预算告警
        }
    }


def real_config_path() -> Path:
    """真实数据目录里那份 config.json 的位置（`KANBAN_REAL_DATA_DIR` 可覆盖）。

    活体测试与发版回归都需要"拿得到 Key，但一个字节都别写进用户的真板子"，
    所以这个路径的解析口径必须只有一份 —— 两边各写一遍迟早漂成两种行为。
    """
    env = os.environ.get("KANBAN_REAL_DATA_DIR")
    home = Path(env) if env else Path(os.environ.get("USERPROFILE") or str(Path.home())) / ".ai-kanban"
    return home / "config.json"


def usable_endpoints(config_path: Path | None = None) -> list[dict]:
    """从某份 config 里取出**配了 Key** 的端点（原样返回，含 Key）。

    调用方拿到后是通过 `PUT /api/ai/config` 灌进一个隔离实例的 —— Key 因此只存在于
    那个临时目录里。读不到文件、没有端点都返回空列表：这是"跑不了就明说"，不是失败。
    """
    f = config_path or real_config_path()
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    eps = (raw.get("ai") or {}).get("endpoints") or []
    return [ep for ep in eps if isinstance(ep, dict) and ep.get("api_keys")]


def load_config() -> dict:
    with _CFG_LOCK:
        f = config_file()
        if not f.exists():
            return default_config()
        try:
            with open(f, "r", encoding="utf-8") as fp:
                cfg = json.load(fp)
            # 兼容旧配置：补齐缺失字段
            d = default_config()
            ai = cfg.get("ai", {})
            d["ai"].update({k: v for k, v in ai.items() if k in d["ai"]})
            if "endpoints" in ai and ai["endpoints"]:
                d["ai"]["endpoints"] = ai["endpoints"]
            if "active_model" in ai and ai["active_model"]:
                d["ai"]["active_model"] = ai["active_model"]
            return d
        except Exception:
            return default_config()


def save_config(cfg: dict) -> None:
    with _CFG_LOCK:
        f = config_file()
        tmp = f.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, f)


def update_config(mutator) -> dict:
    """在同一个临界区内读-改-写配置，避免并发覆盖"""
    with _CFG_LOCK:
        cfg = load_config()
        mutator(cfg)
        f = config_file()
        tmp = f.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, f)
        return cfg


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return key[:3] + "****"
    return key[:6] + "****" + key[-4:]


def mask_matches(real: str, masked: str) -> bool:
    """判断"掩码后的字符串"是不是指回这把真实 Key。

    掩码有两种形态（上面 mask_key）：长 key 是 前6****后4，短 key（≤12 字符）是 前3****。
    回填时只认长形态的话，用短 Key 的人在设置页改任何一项都会把自己的 Key 整条丢掉 ——
    掩码值匹配不到原值，就被当成"用户新输入的 Key"之外的东西丢弃了。
    """
    if "****" not in masked or not real:
        return False
    head, _, tail = masked.partition("****")
    if len(head) < 3:
        return False          # 掩码太短会把整池子都匹配上
    return real.startswith(head) and (tail == "" or real.endswith(tail))


def mask_config(cfg: dict) -> dict:
    """对外返回时掩码 Key"""
    import copy

    out = copy.deepcopy(cfg)
    for ep in out.get("ai", {}).get("endpoints", []):
        ep["api_keys"] = [mask_key(k) for k in ep.get("api_keys", [])]
        ep["key_count"] = len(ep.get("api_keys", []))
    return out


def _post(base_url: str, api_key: str, model: str, messages: list, temperature: float, timeout: float) -> tuple[str, dict | None]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": MAX_TOKENS,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    data = json.loads(raw)
    usage = data.get("usage") if isinstance(data, dict) else None
    return data["choices"][0]["message"]["content"], (usage if isinstance(usage, dict) else None)


def _usage_file() -> Path:
    return config_file().parent / "usage.jsonl"


_USAGE_LOCK = threading.Lock()

# 用量是流水账：每次 LLM 调用写一行，不裁就会一直涨（顺带让 /api/usage 的汇总越来越慢）。
# 超过上限就只留最近 KEEP_ON_TRIM 条 —— 老账没人翻，留着只是白占磁盘。
MAX_USAGE_LINES = 5000
KEEP_ON_TRIM = 2000


@contextmanager
def _usage_locked(timeout: float = 10.0):
    """usage.jsonl 的**跨进程**互斥。

    `_USAGE_LOCK` 只是线程锁，而真实写入方是两条进程：桌面 exe 里的 uvicorn，和
    MCP stdio 服务（它 import 的是同一份代码，但各有各的锁）。追加方与裁剪方不互斥时，
    `_trim_usage` 的「整读 → 重写 → os.replace」会把"读走之后别人刚追加的那一行"
    连文件一起替换掉 —— 实测裁剪一次正好吞掉并发写入的一条，账本从此长期偏小。
    """
    with blocking_lock(_usage_file().with_suffix(".lock"), timeout=timeout, what="用量账本"):
        yield


def _trim_usage(f: Path) -> None:
    """滚动裁剪 usage.jsonl，只保留最近 KEEP_ON_TRIM 条。

    必须只在**超过**上限时才改写文件：常态是纯追加，一次读+一次 replace 是白付的写放大。
    """
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    if len(lines) <= MAX_USAGE_LINES:
        return
    tmp = f.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines[-KEEP_ON_TRIM:]) + "\n", encoding="utf-8")
    os.replace(tmp, f)


def _record_usage(feature: str, endpoint: str, model: str, usage: dict | None, duration: float, ok: bool) -> None:
    """追加一条用量记录到 usage.jsonl（失败静默，不影响主流程）"""
    try:
        rec = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "feature": feature, "endpoint": endpoint, "model": model,
            "prompt_tokens": (usage or {}).get("prompt_tokens"),
            "completion_tokens": (usage or {}).get("completion_tokens"),
            "duration_s": round(duration, 2),
            "ok": ok,
        }
        with _USAGE_LOCK, _usage_locked():
            f = _usage_file()
            with open(f, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            _trim_usage(f)
    except Exception:
        pass


def _model_temp(model: str, temperature: float) -> float:
    """部分模型（如 kimi 系列）只允许 temperature=1，直接适配避免一次必败请求"""
    if "kimi" in (model or "").lower():
        return 1
    return temperature


def chat(cfg: dict, messages: list, temperature: float = 0.2, timeout: float = DEFAULT_TIMEOUT,
         feature: str = "") -> tuple[str, str]:
    """统一 LLM 调用入口：返回 (内容, 使用的端点名)。失败时抛出 LLMError。
    feature：调用用途标签（监管分析/自然语言指令/…），用于用量统计。"""
    endpoints = [ep for ep in cfg.get("ai", {}).get("endpoints", []) if ep.get("enabled")]
    if not endpoints:
        raise LLMError("没有可用的 AI 端点，请在 AI 设置中配置")

    active = cfg.get("ai", {}).get("active_model", "")
    # active 端点优先，其余按序
    ordered = sorted(endpoints, key=lambda ep: 0 if (ep.get("models") and active in ep["models"]) else 1)
    ordered = ordered or endpoints

    errors = []
    for ep in ordered:
        keys = [k for k in ep.get("api_keys", []) if k]
        models = ep.get("models", []) or [active]
        if not keys:
            errors.append(f"{ep['name']}: 未配置 Key")
            continue
        # 选定模型：优先 active，否则端点第一个
        model = active if active in models else models[0]
        temp = _model_temp(model, temperature)
        for key in keys:
            t0 = time.time()
            try:
                content, usage = _post(ep["base_url"], key, model, messages, temp, timeout)
                _record_usage(feature or "未标注", ep["name"], model, usage, time.time() - t0, True)
                return content, ep["name"]
            except urllib.error.HTTPError as e:
                code = e.code
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                # 模型不支持低 temperature（如 kimi 只允许 1）：用 1 重试一次。
                # 失败记账挪到分支末尾 —— 原来一进 except 就记一条 ok=False，
                # 重试成功又记一条 ok=True，于是"一次成功调用"在 /api/usage 里
                # 同时贡献成功与失败（实测 failed 虚高一倍），失败率就成了假数字。
                if code == 400 and "emperature" in body and temp != 1:
                    try:
                        t1 = time.time()
                        content, usage = _post(ep["base_url"], key, model, messages, 1, timeout)
                        _record_usage(feature or "未标注", ep["name"], model, usage, time.time() - t1, True)
                        return content, ep["name"]
                    except Exception as e2:
                        _record_usage(feature or "未标注", ep["name"], model, None, time.time() - t0, False)
                        errors.append(f"{ep['name']}/{model}: temp=1 重试仍失败 {type(e2).__name__}")
                        time.sleep(0.4)
                        continue
                _record_usage(feature or "未标注", ep["name"], model, None, time.time() - t0, False)
                if code == 429:
                    errors.append(f"{ep['name']}/{model}: 限流(429)")
                elif code in (401, 403):
                    errors.append(f"{ep['name']}/{model}: Key 无效({code})")
                elif code == 402:
                    errors.append(f"{ep['name']}/{model}: 余额不足(402)")
                elif code in (404, 410):
                    errors.append(f"{ep['name']}/{model}: 模型已下线({code})")
                elif code >= 500:
                    errors.append(f"{ep['name']}/{model}: 服务错误({code})")
                else:
                    errors.append(f"{ep['name']}/{model}: HTTP {code}")
                time.sleep(0.4)
            except Exception as e:  # 超时/网络/解析
                _record_usage(feature or "未标注", ep["name"], model, None, time.time() - t0, False)
                errors.append(f"{ep['name']}/{model}: {type(e).__name__}")
                time.sleep(0.4)
    raise LLMError("所有 AI 端点均调用失败：" + " | ".join(errors))


def test_connection(cfg: dict) -> dict:
    """轻量连通性测试，不占用推理限额的判断用最小请求"""
    try:
        text, ep_name = chat(cfg, [{"role": "user", "content": "回复 OK 两个字即可"}], temperature=0, timeout=30)
        return {"ok": True, "endpoint": ep_name, "reply": (text or "")[:50]}
    except LLMError as e:
        return {"ok": False, "error": str(e)}
