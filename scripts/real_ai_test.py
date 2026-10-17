# -*- coding: utf-8 -*-
"""
真实 AI 功能测试：用真实 LLM 端点把看板全部 AI 功能端到端跑一遍。
覆盖：监管分析 / 自然语言指令 / 交接文档 / 会话提炼 / AGENTS.md，
交叉使用不同端点与模型，并验证健康分趋势预警在真实链路生效。

注意：会消耗真实配额（每次调用都是最小化输入）；对运行中的服务执行。
用法：.venv\\Scripts\\python.exe scripts\\real_ai_test.py [base_url]
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18320"
DRILL_NAME = "真实AI测试-临时项目"

passed, failed = [], []


def req(method, path, body=None, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            ct = resp.headers.get("content-type", "")
            return resp.status, (json.loads(raw) if "json" in ct else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check(name, cond, extra=""):
    if cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {extra}")


def set_model(model):
    s, cfg = req("GET", "/api/ai/config")
    eps = {ep["name"]: ep for ep in cfg["ai"]["endpoints"]}
    for ep in eps.values():
        if model in ep.get("models", []):
            s2, _ = req("PUT", "/api/ai/config", {"active_model": model})
            return s2 == 200
    return False


def _enabled_endpoints():
    """当前配置里 enabled 的端点（读不到就返回空表，由调用方按「未验」处理）"""
    s, cfg = req("GET", "/api/ai/config")
    if s != 200 or not isinstance(cfg, dict):
        return []
    return [ep for ep in cfg.get("ai", {}).get("endpoints", []) if ep.get("enabled")]


def pick_model(preferred, endpoint=None):
    """在**当前配置里真实存在**的模型中挑一个，返回 (模型名, 是否降级)。

    为什么要有这个：模型名原来是硬编码的，等于把「某台机器上恰好有这几个模型」
    写进了脚本 —— 配置里少一个，`assert set_model(...)` 就直接抛，整轮测试崩在
    半路，后面几步连结果都出不来（2026-10-17 实测：本机 SenseNova 没有
    deepseek-v4-pro，第 4 步 AssertionError，会话提炼/AGENTS.md/趋势三项全没跑到）。

    现在：优先用 preferred；缺了就在**同一端点**里挑替代（保住「验证的就是这个
    端点」的意图）并标降级；连替代都没有才返回 None，由调用方记一条「未验」。
    """
    eps = _enabled_endpoints()
    if any(preferred in (ep.get("models") or []) for ep in eps):
        return preferred, False
    if endpoint:
        for ep in eps:
            if ep.get("name") == endpoint and ep.get("models"):
                return ep["models"][0], True
    return None, False


def _degrade_note(degraded, preferred):
    return f" ⚠原定模型 {preferred} 不在当前配置里，已降级" if degraded else ""


def call_with_retry(method, path, body=None, tries=2, timeout=180):
    """SenseNova 限流自动等待重试"""
    for i in range(tries):
        s, r = req(method, path, body, timeout=timeout)
        if s == 502 and "429" in str(r):
            print(f"    …限流，等待 35s 后重试（{i+1}/{tries-1}）")
            time.sleep(35)
            continue
        return s, r
    return s, r


def main():
    print("=" * 66)
    print("真实 AI 功能测试：5 大功能 × 多模型交叉（消耗真实配额）")
    print("=" * 66)

    eps = _enabled_endpoints()
    if not eps:
        print("⚠ 没有 enabled 的 AI 端点 —— 先在本实例配置端点与 Key，否则每一步都会记「未验」")
    else:
        print("当前配置里可用的模型：")
        for ep in eps:
            print(f"   {ep.get('name'):<14} {ep.get('models')}")

    # ---------- 0. 准备演练项目 ----------
    for p in req("GET", "/api/projects")[1]["projects"]:
        if p["name"] == DRILL_NAME:
            req("DELETE", "/api/projects/" + p["id"])
    s, proj = req("POST", "/api/projects", {
        "name": DRILL_NAME, "status": "进行中", "progress": 35, "priority": "高",
        "tags": ["真实测试"],
        "repo_path": "",
        "background": "真实 AI 测试项目：模拟一个进行中的工具类项目，用于验证看板 AI 功能在真实模型下的表现。",
        "handoff_note": "技术栈 Python + FastAPI；约定：接口返回统一 JSON；已知坑：并发写需要加锁。",
        "next_action": "完成导出模块并补齐测试",
    })
    pid = proj["id"]
    # 预置任务故意与下面会话提炼用的文本**不重叠**：两者撞名时，模型会正确地
    # 不重复建任务（只更新下一步），而第 4 步的断言要求 new_tasks≥1 —— 冲突会造出
    # 「看着像功能坏了」的假红。2026-10-17 实测：预置「导出模块开发」「测试补齐」，
    # 而会话里讲的正好是这两件事，于是提炼任务 0 个、下一步有值。
    req("POST", f"/api/projects/{pid}/tasks", {"title": "接口鉴权中间件", "status": "进行中", "priority": "高"})
    req("POST", f"/api/projects/{pid}/tasks", {"title": "日志埋点与轮转", "status": "待办", "priority": "中"})
    print(f"准备演练项目 {pid} ✓")

    results = []

    def record(feature, model, ok, dt, note):
        results.append((feature, model, ok, dt, note))
        check(f"{feature} × {model}", ok, note)

    # ---------- 1. 监管分析 × SenseNova ----------
    model, degraded = pick_model("deepseek-v4-flash", endpoint="SenseNova")
    print(f"\n[1] 监管分析 × SenseNova {model or '(配置里没有可用模型)'}")
    if model is None:
        check("监管分析", False, "SenseNova 下没有可用模型 —— 这一步无法验证")
    else:
        set_model(model)
        t0 = time.time()
        s, r = call_with_retry("POST", "/api/ai/analyze")
        dt = time.time() - t0
        ok = s == 200 and isinstance(r, dict) and r.get("insights")
        note = ""
        if ok:
            hit = next((i for i in r["insights"] if i.get("id") == pid), None)
            note = f"health={hit.get('health') if hit else '?'} 端点={r.get('endpoint')}洞察={len(r['insights'])}项"
            ok = ok and hit is not None
        record("监管分析", model, ok, dt, note + _degrade_note(degraded, "deepseek-v4-flash"))

    # ---------- 2. 自然语言指令 × kimi-k3（验证 temperature 修复） ----------
    model, degraded = pick_model("kimi-k3", endpoint="SenseNova")
    print(f"\n[2] 自然语言指令 × SenseNova {model or '(配置里没有可用模型)'}（推理模型，temperature 自适应）")
    if model is None:
        check("自然语言指令", False, "SenseNova 下没有可用模型 —— 这一步无法验证")
    else:
        set_model(model)
        t0 = time.time()
        s, r = call_with_retry("POST", "/api/ai/command",
                               {"text": "给这个项目加一个任务：整理真实测试报告，优先级高，负责人 cline"})
        dt = time.time() - t0
        ok = s == 200 and isinstance(r, dict)
        note = ""
        if ok:
            parsed = r.get("parsed", {})
            acts = parsed.get("actions", [])
            note = f"解析出 {len(acts)} 个操作"
            ok = len(acts) >= 1 and any("真实测试报告" in str(a.get("title", "")) for a in acts)
        record("自然语言指令", model, ok, dt, note + _degrade_note(degraded, "kimi-k3"))
        if ok and isinstance(r, dict):
            for a in r["parsed"]["actions"]:
                if a.get("type") == "create_task" and "真实测试报告" in str(a.get("title", "")):
                    s2, t = req("POST", f"/api/projects/{pid}/tasks", {
                        "title": a.get("title", "整理真实测试报告"), "desc": a.get("desc", ""),
                        "priority": a.get("priority", "高"), "status": "待办", "agent": a.get("agent", "cline")})
                    check("指令确认后任务真实创建", s2 == 201)

    # ---------- 3. 交接文档生成 × glm-5.2 ----------
    model, degraded = pick_model("glm-5.2", endpoint="SenseNova")
    print(f"\n[3] 交接文档生成 × SenseNova {model or '(配置里没有可用模型)'}")
    if model is None:
        check("交接文档生成", False, "SenseNova 下没有可用模型 —— 这一步无法验证")
    else:
        set_model(model)
        t0 = time.time()
        s, r = call_with_retry("POST", f"/api/ai/handoff/{pid}", {"mode": "generate"})
        dt = time.time() - t0
        ok = s == 200 and len(r.get("text", "")) > 150
        note = f"长度={len(r.get('text', ''))}字 端点={r.get('endpoint')}" if ok else str(r)[:100]
        record("交接文档生成", model, ok, dt, note + _degrade_note(degraded, "glm-5.2"))

    # ---------- 4. 会话提炼 ----------
    # 原定 deepseek-v4-pro，但那个名字是「某台机器上恰好有」的写法：本机 SenseNova
    # 没有它（2026-10-17 实测），旧写法会在这里 assert 崩，后面三项全跑不到。
    model, degraded = pick_model("deepseek-v4-pro", endpoint="SenseNova")
    print(f"\n[4] 会话提炼 × SenseNova {model or '(配置里没有可用模型)'}")
    if model is None:
        check("会话提炼", False, "SenseNova 下没有可用模型 —— 这一步无法验证")
    else:
        set_model(model)
        t0 = time.time()
        s, r = call_with_retry("POST", "/api/ai/summarize", {
            "pid": pid,
            "text": "刚才和 cline 对齐了：导出模块改用流式写入避免大文件内存溢出；测试补齐要在本周五前完成，重点覆盖并发场景。另外发现一个坑：Windows 下路径分隔符要统一用 pathlib 处理。",
        })
        dt = time.time() - t0
        ok = s == 200 and isinstance(r, dict) and isinstance(r.get("parsed", {}).get("new_tasks"), list)
        note = ""
        if ok:
            parsed = r["parsed"]
            note = f"提炼任务{len(parsed['new_tasks'])}个 下一步={'有' if parsed.get('next_action') else '无'}"
            ok = len(parsed["new_tasks"]) >= 1 and bool(parsed.get("next_action"))
        record("会话提炼", model, ok, dt, note + _degrade_note(degraded, "deepseek-v4-pro"))

    # ---------- 5. AGENTS.md × AMD ----------
    model, degraded = pick_model("DeepSeek-V4-Flash", endpoint="AMD Radeon")
    print(f"\n[5] AGENTS.md 生成 × AMD Radeon {model or '(配置里没有可用模型)'}")
    if model is None:
        check("AGENTS.md 生成", False, "AMD Radeon 下没有可用模型 —— 这一步无法验证")
    else:
        set_model(model)
        t0 = time.time()
        s, r = call_with_retry("POST", f"/api/export/agentsmd/{pid}")
        dt = time.time() - t0
        ok = s == 200 and len(r.get("text", "")) > 150
        note = f"source={r.get('source')} 长度={len(r.get('text', ''))}字" if ok else str(r)[:100]
        record("AGENTS.md 生成", f"AMD {model}", ok, dt, note + _degrade_note(degraded, "DeepSeek-V4-Flash"))

    # ---------- 6. 监管分析 × NVIDIA（趋势预警验证） ----------
    model, degraded = pick_model("z-ai/glm-5.3-flash", endpoint="NVIDIA")
    print(f"\n[6] 监管分析 × NVIDIA {model or '(配置里没有可用模型)'}（第二次分析→趋势预警）")
    if model is None:
        check("监管分析(趋势预警)", False, "NVIDIA 下没有可用模型 —— 这一步无法验证")
    else:
        set_model(model)
        # 先改低进度，让健康分有变化空间
        proj_now = req("GET", f"/api/projects/{pid}")[1]
        req("PUT", f"/api/projects/{pid}", {**proj_now, "progress": 15, "next_action": ""})
        t0 = time.time()
        s, r = call_with_retry("POST", "/api/ai/analyze", timeout=240)
        dt = time.time() - t0
        ok = s == 200 and isinstance(r, dict) and r.get("insights")
        note = ""
        if ok:
            hit = next((i for i in r["insights"] if i.get("id") == pid), None)
            trend = (hit or {}).get("trend", {})
            note = f"health={hit.get('health') if hit else '?'} 趋势={trend.get('level')} 上次={trend.get('prev')}"
            # 趋势验证：上一份报告存在时，本次应有趋势标注（level 非 new）
            ok = hit is not None and trend.get("level") in ("down", "up", "flat")
        record("监管分析(趋势预警)", f"NVIDIA {model}", ok, dt, note + _degrade_note(degraded, "z-ai/glm-5.3-flash"))
        if ok:
            hit = next((i for i in r["insights"] if i.get("id") == pid), None)
            check("趋势数值合理（上次=首轮健康分）",
                  hit["trend"].get("prev") is not None and hit["trend"]["delta"] == hit["health"] - hit["trend"]["prev"])

    # ---------- 7. 清理 ----------
    print("\n[7] 清理")
    # 恢复默认模型
    set_model("deepseek-v4-flash")
    s, _ = req("DELETE", f"/api/projects/{pid}")
    check("演练项目清理 + 默认模型恢复", s == 200)

    # ---------- 汇总 ----------
    print("\n" + "=" * 66)
    print(f"{'功能':<14} {'模型':<26} {'结果':<6} {'耗时':>7}  备注")
    for f, m, ok, dt, note in results:
        print(f"{f:<14} {m:<26} {'✓' if ok else '✗':<6} {dt:6.1f}s  {note}")
    print("=" * 66)
    print(f"真实 AI 测试结果：{len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：", "；".join(failed))
        sys.exit(1)
    print("结论：全部 AI 功能在真实模型下端到端可用 ✓")


if __name__ == "__main__":
    main()
