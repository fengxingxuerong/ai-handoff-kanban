# -*- coding: utf-8 -*-
"""
LLM 端点全量实测：把本地配置里的每个 端点×Key×模型 都发一个最小请求验证。
- Key 级：每把 Key 用端点默认模型发 1 个请求（验证 Key 有效性）
- 模型级：每端点用第一把可用 Key 遍历全部模型
- 请求极小（max_tokens=16），串行 + 间隔防限流

用法：.venv\\Scripts\\python.exe scripts\\test_all_llm.py
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.llm import config_file, mask_key  # noqa: E402

TIMEOUT = 45
MAX_TOKENS = 16


def call(base_url, key, model):
    """返回 (状态, 耗时秒, 回复摘要)"""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": "只回复两个字：OK"}],
               "temperature": 0, "max_tokens": MAX_TOKENS}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            dt = time.time() - t0
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return "OK", dt, (text or "").strip()[:24]
    except urllib.error.HTTPError as e:
        dt = time.time() - t0
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:120]
        except Exception:
            pass
        if e.code == 429:
            return "限流(429)", dt, body
        if e.code in (401, 403):
            return f"Key无效({e.code})", dt, body
        if e.code == 404:
            return "模型不存在(404)", dt, body
        return f"HTTP {e.code}", dt, body
    except Exception as e:
        return f"{type(e).__name__}", time.time() - t0, str(e)[:80]


def main():
    cfg = json.loads(config_file().read_text(encoding="utf-8"))
    eps = [ep for ep in cfg.get("ai", {}).get("endpoints", []) if ep.get("enabled")]
    rows = []
    ok_cnt = 0
    total = 0
    print("=" * 78)
    print("LLM 端点全量实测（最小请求，max_tokens=16）")
    print("=" * 78)
    for ep in eps:
        name = ep["name"]
        base = ep["base_url"]
        keys = [k for k in ep.get("api_keys", []) if k]
        models = ep.get("models", [])
        if not keys:
            print(f"\n【{name}】{base}\n  ⚠ 未配置 Key，跳过")
            continue
        print(f"\n【{name}】{base}")
        primary = keys[0]
        # 1) Key 级：每把 Key × 默认模型
        default_model = models[0] if models else ""
        for i, key in enumerate(keys, 1):
            st, dt, extra = call(base, key, default_model)
            total += 1
            ok_cnt += st == "OK"
            rows.append((name, default_model, mask_key(key), st, dt, extra))
            print(f"  Key#{i} {mask_key(key)} × {default_model:<28} {st:<14} {dt:5.1f}s  {extra}")
            time.sleep(1)
        # 2) 模型级：第一把 Key 遍历其余模型
        for m in models[1:]:
            st, dt, extra = call(base, primary, m)
            total += 1
            ok_cnt += st == "OK"
            rows.append((name, m, mask_key(primary), st, dt, extra))
            print(f"  Key#1 {mask_key(primary)} × {m:<28} {st:<14} {dt:5.1f}s  {extra}")
            time.sleep(1)

    print("\n" + "=" * 78)
    print(f"汇总：{ok_cnt}/{total} 可用")
    bad = [r for r in rows if r[3] != "OK"]
    if bad:
        print("不可用明细：")
        for name, model, key, st, dt, extra in bad:
            print(f"  - {name} × {model} × {key}: {st}  {extra}")
    else:
        print("全部端点 × Key × 模型均可用 ✓")


if __name__ == "__main__":
    main()
