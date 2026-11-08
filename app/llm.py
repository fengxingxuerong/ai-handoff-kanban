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
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .filelock import blocking_lock

DEFAULT_TIMEOUT = 60
MAX_TOKENS = 3000
# 连接阶段单独限时：网关没启动时 TCP 重试在 Windows 上能拖 20~30s，
# 用户会对着"AI 转圈"干等。5s 连不上就当场给"网关未启动"的中文结论。
CONNECT_TIMEOUT = 5

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


def _normalize_config(cfg: dict) -> dict:
    """兼容旧配置：补齐缺失字段。读旧文件与从 last-good 恢复共用这一处。"""
    d = default_config()
    ai = cfg.get("ai", {})
    d["ai"].update({k: v for k, v in ai.items() if k in d["ai"]})
    if "endpoints" in ai and ai["endpoints"]:
        d["ai"]["endpoints"] = ai["endpoints"]
    if "active_model" in ai and ai["active_model"]:
        d["ai"]["active_model"] = ai["active_model"]
    return d


def config_last_good_file() -> Path:
    """最近一次成功写入的配置副本；与 projects.json 的 last-good 同一口径。"""
    return config_file().with_suffix(".json.last-good")


def _write_config(cfg: dict) -> None:
    f = config_file()
    tmp = f.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(cfg, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, f)
    try:
        shutil.copy2(f, config_last_good_file())
    except Exception:
        pass


def load_config() -> dict:
    with _CFG_LOCK:
        f = config_file()
        if not f.exists():
            return default_config()
        try:
            with open(f, "r", encoding="utf-8") as fp:
                return _normalize_config(json.load(fp))
        except Exception:
            # 与 projects.json 的自愈对齐：先留证，再试 last-good；都没有才回默认值。
            # 这样“设置文件坏了一次”不会静默把 Key 清空。
            try:
                shutil.copy2(f, f.with_suffix(".json.bak"))
            except Exception:
                pass
            lf = config_last_good_file()
            if lf.exists():
                try:
                    cfg = _normalize_config(json.loads(lf.read_text(encoding="utf-8")))
                    _write_config(cfg)
                    return cfg
                except Exception:
                    pass
            return default_config()


def save_config(cfg: dict) -> None:
    with _CFG_LOCK:
        _write_config(cfg)


def update_config(mutator) -> dict:
    """在同一个临界区内读-改-写配置，避免并发覆盖"""
    with _CFG_LOCK:
        cfg = load_config()
        mutator(cfg)
        _write_config(cfg)
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


def merge_keys(old_keys: list[str], new_keys: list[str],
               fallback_pool: list[str] | None = None) -> list[str]:
    """合并 Key：掩码形式(含 ****)的提交项从旧配置找回真实 Key。

    查找顺序：本端点的旧 Key → fallback_pool（全部端点的旧 Key）。
    后者用于端点改名/换位置的场景 —— 否则掩码找不到匹配，会被当成真实 Key 写回配置。
    仍然找不到就丢弃：宁可少一个 Key，也不能把 sk-abc****xyz 这种掩码写进配置。
    （2026-10-07 从 main 迁入：掩码找 Key 与 mask_matches 同域）
    """
    pool = list(old_keys or []) + list(fallback_pool or [])
    out = []
    for k in new_keys or []:
        k = (k or "").strip()
        if not k:
            continue
        if "****" in k:
            found = None
            for ok in pool:
                if mask_matches(ok, k):
                    found = ok
                    break
            if found:
                out.append(found)
            continue
        out.append(k)
    return out


def _post(base_url: str, api_key: str, model: str, messages: list, temperature: float, timeout: float,
          max_tokens: int = MAX_TOKENS) -> tuple[str, dict | None, str]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
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
    choice = (data.get("choices") or [{}])[0]
    finish = choice.get("finish_reason") or ""
    return choice.get("message", {}).get("content", ""), (usage if isinstance(usage, dict) else None), finish


def _preflight(base_url: str, timeout: float = CONNECT_TIMEOUT) -> str:
    """连接预检：返回错误文案，空串表示可以连。

    只做 TCP 握手，不发请求、不耗额度。目的是把"网关没开"从 30~60s 的超时
    变成 ≤5s 的明确提示 —— 实测网关不可达时 analyze 偶发 33.53s 白屏。
    """
    try:
        u = urllib.parse.urlsplit(base_url or "")
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "https" else 80)
        if not host:
            return ""
        with socket.create_connection((host, port), timeout=timeout):
            return ""
    except socket.timeout:
        return f"连不上 {host}:{port}（{timeout}s 超时）—— AI 网关未启动或端口不通"
    except OSError as e:
        return f"连不上 {host}:{port}（{type(e).__name__}）—— AI 网关未启动或端口不通"


def _upstream_detail(body: str) -> tuple[str, str]:
    """拆上游错误体 → (message, code)，取不到就返回 (原文截断, "")。"""
    try:
        data = json.loads(body)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            return (str(err.get("message") or "")[:180],
                    str(err.get("code") or err.get("type") or ""))
        if isinstance(data, dict):
            return (str(data.get("message") or data.get("detail") or "")[:180], "")
    except Exception:
        pass
    return ((body or "").strip()[:180], "")


def _error_text(code: int, body: str) -> str:
    """把上游 HTTP 错误翻译成用户能照做的中文，而不是统一一句「HTTP 400」。

    分类顺序按"用户下一步动作"排：改 Key → 充值 → 换模型 → 等一会儿。
    括号里保留原始状态码/上游 code，方便贴给网关管理员排查。
    """
    msg, ecode = _upstream_detail(body)
    low = (msg + " " + ecode).lower()
    if code == 429:
        head = "额度用尽/限流(429)：请稍后重试，或在 AI 设置里换一个端点"
    elif code in (401, 403):
        head = f"Key 无效({code})：请到 AI 设置里检查/更换 API Key"
    elif code == 402:
        head = "余额不足(402)：请充值，或换用其他端点"
    elif code in (404, 410):
        head = f"模型已下线({code})：请换一个模型"
    elif code >= 500:
        head = f"服务错误({code})：上游异常，请稍后重试"
    elif "model_not_found" in low or ("model" in low and ("not found" in low or "no usable" in low or "不存在" in low)):
        head = "模型名不存在或未开通(model_not_found)：请在 AI 设置里检查模型 id"
    elif "model" in low and ("format" in low or "invalid" in low):
        head = "模型名格式不正确：网关要求 provider/model 形式（例如 raccoon/xxx）"
    elif "insufficient" in low or "balance" in low or "余额" in msg:
        head = "余额不足(402)：请充值，或换用其他端点"
    elif "quota" in low or "rate limit" in low or "额度" in msg:
        head = "额度用尽/限流(429)：请稍后重试，或在 AI 设置里换一个端点"
    elif code in (400, 422):
        head = f"请求被上游拒绝({code})：请检查模型名/参数"
    else:
        head = f"HTTP {code}"
    return f"{head}｜上游：{msg}" if msg else head


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


def _empty_reply(text) -> bool:
    """空回复（或纯空白）不算成功。

    推理型模型（deepseek-v4 等）会把 `max_tokens` 全烧在 `reasoning_content` 上、
    `content` 返回空串，而 HTTP 仍是 200 —— 2026-10-19 实测：网关把它记成 ok=true
    并直接返回，上层拿到空文本（监管分析 500「LLM 返回为空」、AGENTS.md 静默退回
    本地版），同时其它可用端点**根本没机会被尝试**。空 = 失败，继续走降级链。
    """
    return not (text or "").strip()


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
        # 先做 5s TCP 预检：网关没开就别进 60s 的读超时
        pre = _preflight(ep.get("base_url") or "")
        if pre:
            errors.append(f"{ep['name']}: {pre}")
            continue
        # 选定模型：优先 active，否则端点第一个
        model = active if active in models else models[0]
        temp = _model_temp(model, temperature)
        for key in keys:
            t0 = time.time()
            try:
                content, usage, finish = _post(ep["base_url"], key, model, messages, temp, timeout)
                if _empty_reply(content) or finish == "length":
                    # 推理型模型（deepseek-v4 等）会把 3000 的预算烧在 reasoning 上：要么
                    # content 空串，要么 JSON 写到一半被截断（finish_reason=length，内容
                    # 非空但解析必败）—— 2026-10-24 实测监管分析因此 4 连败。同 Key 立即
                    # 用 4 倍预算重试：max_tokens 只是封顶、按实际用量计费，正常调用不多
                    # 花一分钱，只有原本颗粒无收的调用才值得多花。
                    content, usage, finish = _post(ep["base_url"], key, model, messages, temp,
                                                   timeout, max_tokens=MAX_TOKENS * 4)
                if _empty_reply(content) or finish == "length":
                    _record_usage(feature or "未标注", ep["name"], model, usage, time.time() - t0, False)
                    errors.append(f"{ep['name']}/{model}: 返回为空/截断"
                                  f"（提升 max_tokens={MAX_TOKENS * 4} 重试仍失败 finish={finish}）")
                    time.sleep(0.4)
                    continue
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
                        content, usage, finish = _post(ep["base_url"], key, model, messages, 1, timeout)
                        if _empty_reply(content) or finish == "length":
                            content, usage, finish = _post(ep["base_url"], key, model, messages, 1,
                                                           timeout, max_tokens=MAX_TOKENS * 4)
                        if _empty_reply(content) or finish == "length":
                            _record_usage(feature or "未标注", ep["name"], model, usage, time.time() - t1, False)
                            errors.append(f"{ep['name']}/{model}: temp=1 重试返回为空/截断")
                            time.sleep(0.4)
                            continue
                        _record_usage(feature or "未标注", ep["name"], model, usage, time.time() - t1, True)
                        return content, ep["name"]
                    except Exception as e2:
                        _record_usage(feature or "未标注", ep["name"], model, None, time.time() - t0, False)
                        errors.append(f"{ep['name']}/{model}: temp=1 重试仍失败 {type(e2).__name__}")
                        time.sleep(0.4)
                        continue
                _record_usage(feature or "未标注", ep["name"], model, None, time.time() - t0, False)
                errors.append(f"{ep['name']}/{model}: {_error_text(code, body)}")
                time.sleep(0.4)
            except Exception as e:  # 超时/网络/解析
                _record_usage(feature or "未标注", ep["name"], model, None, time.time() - t0, False)
                if isinstance(e, (socket.timeout, TimeoutError)):
                    msg = f"响应超时（>{int(timeout)}s）—— 网关未启动或响应过慢"
                elif isinstance(e, OSError):
                    msg = f"连接失败（{type(e).__name__}）—— 网关未启动或端口不通"
                else:
                    msg = type(e).__name__
                errors.append(f"{ep['name']}/{model}: {msg}")
                time.sleep(0.4)
    raise LLMError("所有 AI 端点均调用失败：" + " | ".join(errors))


def _sse_post(base_url: str, api_key: str, model: str, messages: list, temperature: float,
              timeout: float, max_tokens: int = MAX_TOKENS):
    """流式 POST：逐段 yield ("delta", 文本) / ("usage", dict) / ("finish", 原因)。

    上游不支持 SSE（回了普通 JSON）时退化成一次性 delta —— 调用方语义不变，
    前端照样能拿到最终结果。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": True}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" not in ctype:
            raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
            usage = data.get("usage") if isinstance(data, dict) else None
            choice = (data.get("choices") or [{}])[0]
            content = choice.get("message", {}).get("content", "")
            if content:
                yield "delta", content
            if isinstance(usage, dict):
                yield "usage", usage
            yield "finish", (choice.get("finish_reason") or "")
            return
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                chunk = line[5:].strip()
            elif line.startswith("{"):
                chunk = line          # 个别网关省略 data: 前缀
            else:
                continue
            if chunk == "[DONE]":
                break
            try:
                data = json.loads(chunk)
            except Exception:
                continue
            if isinstance(data.get("usage"), dict):
                yield "usage", data["usage"]
            for ch in (data.get("choices") or []):
                piece = (ch.get("delta") or {}).get("content")
                if piece:
                    yield "delta", piece
                if ch.get("finish_reason"):
                    yield "finish", ch["finish_reason"]


def stream_chat(cfg: dict, messages: list, temperature: float = 0.2, timeout: float = DEFAULT_TIMEOUT,
                feature: str = "", max_tokens: int = MAX_TOKENS):
    """流式对话：yield ("delta", 文本增量)，结束时 yield ("done", {endpoint, model, finish})。

    与 `chat` 共用端点/Key 降级链。差别：**已经吐过字就不再换端点** —— 换端点会把
    两个模型的输出拼在一起，用户看到的是一段精神分裂的回答；宁可报"中途断开"。
    """
    endpoints = [ep for ep in cfg.get("ai", {}).get("endpoints", []) if ep.get("enabled")]
    if not endpoints:
        raise LLMError("没有可用的 AI 端点，请在 AI 设置中配置")

    active = cfg.get("ai", {}).get("active_model", "")
    ordered = sorted(endpoints, key=lambda ep: 0 if (ep.get("models") and active in ep["models"]) else 1)

    errors = []
    for ep in ordered:
        keys = [k for k in ep.get("api_keys", []) if k]
        models = ep.get("models", []) or [active]
        if not keys:
            errors.append(f"{ep['name']}: 未配置 Key")
            continue
        pre = _preflight(ep.get("base_url") or "")
        if pre:
            errors.append(f"{ep['name']}: {pre}")
            continue
        model = active if active in models else models[0]
        temp = _model_temp(model, temperature)
        for key in keys:
            t0 = time.time()
            parts: list[str] = []
            usage = None
            finish = ""
            try:
                for kind, payload in _sse_post(ep["base_url"], key, model, messages, temp,
                                               timeout, max_tokens):
                    if kind == "delta":
                        parts.append(payload)
                        yield "delta", payload
                    elif kind == "usage":
                        usage = payload
                    else:
                        finish = payload or finish
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                _record_usage(feature or "未标注", ep["name"], model, usage, time.time() - t0, False)
                msg = _error_text(e.code, body)
                errors.append(f"{ep['name']}/{model}: {msg}")
                if parts:
                    raise LLMError("AI 输出中途断开：" + msg)
                time.sleep(0.4)
                continue
            except Exception as e:
                _record_usage(feature or "未标注", ep["name"], model, usage, time.time() - t0, False)
                if isinstance(e, (socket.timeout, TimeoutError)):
                    msg = f"响应超时（>{int(timeout)}s）—— 网关未启动或响应过慢"
                elif isinstance(e, OSError):
                    msg = f"连接失败（{type(e).__name__}）—— 网关未启动或端口不通"
                else:
                    msg = type(e).__name__
                errors.append(f"{ep['name']}/{model}: {msg}")
                if parts:
                    raise LLMError("AI 输出中途断开：" + msg)
                time.sleep(0.4)
                continue
            text = "".join(parts)
            if _empty_reply(text):
                _record_usage(feature or "未标注", ep["name"], model, usage, time.time() - t0, False)
                errors.append(f"{ep['name']}/{model}: 返回为空")
                time.sleep(0.4)
                continue
            _record_usage(feature or "未标注", ep["name"], model, usage, time.time() - t0, True)
            yield "done", {"endpoint": ep["name"], "model": model, "finish": finish}
            return
    raise LLMError("所有 AI 端点均调用失败：" + " | ".join(errors))


def test_connection(cfg: dict) -> dict:
    """轻量连通性测试，不占用推理限额的判断用最小请求"""
    try:
        text, ep_name = chat(cfg, [{"role": "user", "content": "回复 OK 两个字即可"}], temperature=0, timeout=30)
        return {"ok": True, "endpoint": ep_name, "reply": (text or "")[:50]}
    except LLMError as e:
        return {"ok": False, "error": str(e)}


# ---- LLM 输出容错提取（2026-10-06 从 app/main.py 移入：解析的是模型输出，职责在网关侧） ----

def _extract_json(text: str) -> dict | list:
    """从 LLM 输出中容错提取 JSON（去除 markdown 代码块、前后缀文本）"""
    if not text:
        raise ValueError("LLM 返回为空")
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # 前后缀有噪声时，取「出现位置更靠前」的那个候选：
    # 否则像「前缀 [{"a":1}] 后缀」这种会先命中内层对象，把数组吞掉。
    candidates = []
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = s.find(open_ch)
        end = s.rfind(close_ch)
        if start != -1 and end > start:
            candidates.append((start, s[start:end + 1]))
    for _, chunk in sorted(candidates, key=lambda x: x[0]):
        try:
            return json.loads(chunk)
        except Exception:
            continue
    raise ValueError("无法从 LLM 输出中解析 JSON")
