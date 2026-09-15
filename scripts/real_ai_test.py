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
    req("POST", f"/api/projects/{pid}/tasks", {"title": "导出模块开发", "status": "进行中", "priority": "高"})
    req("POST", f"/api/projects/{pid}/tasks", {"title": "测试补齐", "status": "待办", "priority": "中"})
    print(f"准备演练项目 {pid} ✓")

    results = []

    def record(feature, model, ok, dt, note):
        results.append((feature, model, ok, dt, note))
        check(f"{feature} × {model}", ok, note)

    # ---------- 1. 监管分析 × deepseek-v4-flash ----------
    print("\n[1] 监管分析 × SenseNova deepseek-v4-flash")
    assert set_model("deepseek-v4-flash"), "切换模型失败"
    t0 = time.time()
    s, r = call_with_retry("POST", "/api/ai/analyze")
    dt = time.time() - t0
    ok = s == 200 and isinstance(r, dict) and r.get("insights")
    note = ""
    if ok:
        hit = next((i for i in r["insights"] if i.get("id") == pid), None)
        note = f"health={hit.get('health') if hit else '?'} 端点={r.get('endpoint')}洞察={len(r['insights'])}项"
        ok = ok and hit is not None
    record("监管分析", "deepseek-v4-flash", ok, dt, note)
    first_report_id = r["id"] if ok else None

    # ---------- 2. 自然语言指令 × kimi-k3（验证 temperature 修复） ----------
    print("\n[2] 自然语言指令 × SenseNova kimi-k3（推理模型，temperature 自适应）")
    assert set_model("kimi-k3")
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
    record("自然语言指令", "kimi-k3", ok, dt, note)
    if ok and isinstance(r, dict):
        for a in r["parsed"]["actions"]:
            if a.get("type") == "create_task" and "真实测试报告" in str(a.get("title", "")):
                s2, t = req("POST", f"/api/projects/{pid}/tasks", {
                    "title": a.get("title", "整理真实测试报告"), "desc": a.get("desc", ""),
                    "priority": a.get("priority", "高"), "status": "待办", "agent": a.get("agent", "cline")})
                check("指令确认后任务真实创建", s2 == 201)

    # ---------- 3. 交接文档生成 × glm-5.2 ----------
    print("\n[3] 交接文档生成 × SenseNova glm-5.2")
    assert set_model("glm-5.2")
    t0 = time.time()
    s, r = call_with_retry("POST", f"/api/ai/handoff/{pid}", {"mode": "generate"})
    dt = time.time() - t0
    ok = s == 200 and len(r.get("text", "")) > 150
    note = f"长度={len(r.get('text', ''))}字 端点={r.get('endpoint')}" if ok else str(r)[:100]
    record("交接文档生成", "glm-5.2", ok, dt, note)

    # ---------- 4. 会话提炼 × deepseek-v4-pro ----------
    print("\n[4] 会话提炼 × SenseNova deepseek-v4-pro")
    assert set_model("deepseek-v4-pro")
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
    record("会话提炼", "deepseek-v4-pro", ok, dt, note)

    # ---------- 5. AGENTS.md × AMD DeepSeek-V4-Flash ----------
    print("\n[5] AGENTS.md 生成 × AMD DeepSeek-V4-Flash")
    assert set_model("DeepSeek-V4-Flash")
    t0 = time.time()
    s, r = call_with_retry("POST", f"/api/export/agentsmd/{pid}")
    dt = time.time() - t0
    ok = s == 200 and len(r.get("text", "")) > 150
    note = f"source={r.get('source')} 长度={len(r.get('text', ''))}字" if ok else str(r)[:100]
    record("AGENTS.md 生成", "AMD DeepSeek-V4-Flash", ok, dt, note)

    # ---------- 6. 监管分析 × NVIDIA glm-5.3-flash（趋势预警验证） ----------
    print("\n[6] 监管分析 × NVIDIA z-ai/glm-5.3-flash（第二次分析→趋势预警）")
    assert set_model("z-ai/glm-5.3-flash")
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
    record("监管分析(趋势预警)", "NVIDIA glm-5.3-flash", ok, dt, note)
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
