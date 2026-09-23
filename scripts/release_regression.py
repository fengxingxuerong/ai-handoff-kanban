# -*- coding: utf-8 -*-
"""
发版回归测试（Release Regression）：v1.1+ 新功能全链路验证。

自起一个独立实例（专用端口 18399 + 临时数据目录），完全不碰真实数据与配置：
  1. 会话捕获全链路（假源 → 扫描 → 预览 → AI 提炼 → 应用 → 任务/记忆/下一步）
  2. 任务过程轨迹（状态流转自动记 journal）
  3. 项目记忆（CRUD / 去重 / 交接包召回）
  4. LLM 用量追踪（提炼调用被记账）
  5. 逾期统计（stats 计数）
  6. MCP 只读模式（写拒读通）

用法：
    .venv\\Scripts\\python.exe scripts\\release_regression.py            # 含真实 AI 提炼（消耗少量配额）
    .venv\\Scripts\\python.exe scripts\\release_regression.py --no-ai    # 跳过真实 AI 步骤
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 借 app.llm 里那两个"取真实 config / 筛带 Key 的端点"的函数：活体测试与发版回归
# 要做的是同一件事，读法写两遍迟早漂成两种行为（同 scripts/mcp_server.py 的引法）
sys.path.insert(0, str(ROOT))
from app import llm                                    # noqa: E402
PORT = 18399
BASE = f"http://127.0.0.1:{PORT}"
NO_AI = "--no-ai" in sys.argv
DATA_DIR = Path(tempfile.mkdtemp(prefix="kanban_regression_"))

# 控制台默认 cp936，而这个脚本要打印 ✓ / 中文，也要读 MCP 子进程的 UTF-8 输出。
# 不显式收口的话，同一条命令在 PowerShell 里绿、在 GBK 控制台里红 —— 假失败会让人怀疑产品坏了。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                     # noqa: BLE001  重定向到管道时可能没有 reconfigure
    pass
# 本机常年挂着代理：127.0.0.1 必须直连，否则演练请求被代理拦下，全线超时
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

passed, failed, skipped = [], [], []


def req(method, path, body=None, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            ct = resp.headers.get("content-type", "")
            return resp.status, (json.loads(raw) if "json" in ct else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check(name, cond, extra="", skip=False):
    """skip=True 表示这一项**本轮没验**，不是"验过了"。
    以前 `--no-ai` 模式用 `check(..., True)` 占位，于是"18 通过"里混着两条无断言项，
    结尾还能印出"新功能链路全部可用"。跳过要跳得看得见。"""
    if skip:
        skipped.append(name)
        print(f"  [SKIP] {name}  {extra}")
    elif cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {extra}")


def port_listening(port):
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    r = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    return r


# ---------------- 服务生命周期 ----------------

def start_server() -> subprocess.Popen:
    env = dict(os.environ)
    env["KANBAN_DATA_DIR"] = str(DATA_DIR)
    env["KANBAN_PORT"] = str(PORT)
    env["PYTHONIOENCODING"] = "utf-8"     # 子进程日志里有中文，别让父进程按 cp936 解
    python = str(ROOT / ".venv" / "Scripts" / "python.exe")
    if not Path(python).exists():
        python = sys.executable
    proc = subprocess.Popen([python, "-m", "uvicorn", "app.main:app",
                             "--host", "127.0.0.1", "--port", str(PORT),
                             "--log-level", "warning"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if port_listening(PORT):
            return proc
        time.sleep(0.3)
    raise RuntimeError("回归实例启动超时")


def stop_server(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    for _ in range(3):
        if not port_listening(PORT):
            break
        time.sleep(1.0)


# ---------------- 用例 ----------------

def make_fake_session() -> tuple[Path, Path]:
    """造一个 Claude Code 风格的两层会话结构：<源>/<项目目录>/<uuid>.jsonl"""
    tmp = DATA_DIR / "captured-sessions" / "demo-proj"
    tmp.mkdir(parents=True, exist_ok=True)
    jsonl = tmp / "session-regression.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"role": "user",
                    "content": "回归演练：发现导出模块在大文件时内存占用过高，下一轮要改成流式写入。已定决策：导出格式统一用 UTF-8 无 BOM。"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant",
                    "content": "收到。注意坑：流式写入改造后必须补并发场景的回归测试，否则容易漏掉锁问题。下一步我先做流式写入的方案设计。"}}),
    ]
    jsonl.write_text("\n".join(lines), encoding="utf-8")
    return jsonl.parent.parent, jsonl  # 源目录（供 claude_jsonl 两层 glob）+ 会话文件


def sync_keys_to_instance() -> bool:
    """把真实 config 里的端点与 Key 同步给回归实例（仅写入临时数据目录，不落地项目）。
    真实 config 路径：--real-data 参数 > KANBAN_REAL_DATA_DIR 环境变量 > USERPROFILE/.ai-kanban"""
    real_cfg = None
    if "--real-data" in sys.argv:
        real_cfg = Path(sys.argv[sys.argv.index("--real-data") + 1]) / "config.json"
    if real_cfg is None:
        real_cfg = llm.real_config_path()
    eps = llm.usable_endpoints(real_cfg)
    if not eps:
        print(f"  （提示：未找到可用的端点与 Key（{real_cfg}），AI 步骤将无 Key 可用）")
        return False
    s, _ = req("PUT", "/api/ai/config", {"endpoints": eps})
    return s == 200


def t1_session_capture(pid_hint: str | None):
    print("\n[1] 会话捕获全链路")
    src_dir, jsonl = make_fake_session()
    status, _ = req("PUT", "/api/ai/config", {
        "session_sources": [{"name": "回归源", "path": str(src_dir), "type": "claude_jsonl"}]})
    check("自定义会话源写入配置", status == 200)
    status, r = req("GET", "/api/sessions/scan")
    found = any("session-regression" in s["path"] for s in r.get("sessions", []))
    check("扫描发现假会话", status == 200 and found)
    status, pv = req("GET", "/api/sessions/preview?path=" + urllib.parse.quote(str(jsonl)))
    check("预览解析出对话", status == 200 and "流式写入" in pv.get("text", ""))
    # 选择演练项目入库
    status, projects = req("GET", "/api/projects")
    pid = next(p["id"] for p in projects["projects"] if p["name"] == "回归演练-临时")

    if NO_AI:
        check("会话捕获 → AI 提炼 → 入库", False, "本轮 --no-ai 未跑", skip=True)
        return pid

    status, r = req("POST", "/api/sessions/ingest", {"pid": pid, "path": str(jsonl)}, timeout=180)
    ok = status == 200 and isinstance(r, dict) and r.get("parsed")
    parsed = r.get("parsed", {}) if ok else {}
    check("AI 提炼成功（真实调用）", ok, str(r)[:120])
    if not ok:
        return pid
    check("提炼出任务与记忆", len(parsed.get("new_tasks", [])) >= 1 or bool(parsed.get("memories")))
    # 应用：创建任务 + 记忆 + 下一步
    for t in parsed.get("new_tasks", []):
        req("POST", f"/api/projects/{pid}/tasks", {
            "title": t.get("title", "回归任务"), "desc": t.get("desc", ""),
            "priority": t.get("priority", "中"), "status": "待办"})
    p_now = req("GET", f"/api/projects/{pid}")[1]
    if parsed.get("next_action"):
        p_now["next_action"] = parsed["next_action"]
        req("PUT", f"/api/projects/{pid}", p_now)
    for m in parsed.get("memories", []):
        if m.get("text"):
            req("POST", "/api/memories", {"pid": pid, "type": m.get("type", "事实"),
                                          "text": m["text"], "source": "会话提炼"})
    _, detail = req("GET", f"/api/projects/{pid}")
    created = any("流式" in t["title"] or "导出" in t["title"] for t in detail.get("tasks", []))
    check("提炼任务已入库", created)
    return pid


def t2_journal(pid):
    print("\n[2] 任务过程轨迹")
    _, detail = req("GET", f"/api/projects/{pid}")
    task = detail["tasks"][0]
    tid = task["id"]
    status, entry = req("POST", f"/api/tasks/{tid}/journal", {"text": "回归演练：手动记录一条"})
    check("手动追加过程记录", status == 201 and entry.get("text") == "回归演练：手动记录一条")
    old_status = task["status"]
    new_status = "进行中" if old_status != "进行中" else "待办"
    task.update({"status": new_status})
    status, _ = req("PUT", f"/api/tasks/{tid}", task)
    _, detail2 = req("GET", f"/api/projects/{pid}")
    t2 = next(t for t in detail2["tasks"] if t["id"] == tid)
    auto = [e["text"] for e in (t2.get("journal") or [])]
    check("状态流转自动记 journal", f"状态：{old_status} → {new_status}" in auto, str(auto))


def t3_memories(pid):
    print("\n[3] 项目记忆层")
    s, m1 = req("POST", "/api/memories", {"pid": pid, "type": "决策", "text": "回归决策：接口统一返回 {code,data}"})
    check("记忆写入", s == 201)
    s, m2 = req("POST", "/api/memories", {"pid": pid, "type": "决策", "text": "回归决策：接口统一返回 {code,data}"})
    check("同文去重", m2.get("deduped") is True)
    _, lst = req("GET", f"/api/memories?pid={pid}&q=code")
    check("按关键词检索", len(lst["memories"]) >= 1)
    _, md = req("GET", f"/api/export/{pid}")
    check("交接包召回记忆节", "已沉淀记忆" in md and "回归决策" in md)
    _, ctx = req("GET", f"/api/context/{pid}")
    check("context 召回记忆", any("回归决策" in m.get("text", "") for m in ctx.get("memories", [])))
    return m1["memory"]["id"]


def t4_usage():
    print("\n[4] LLM 用量追踪")
    if NO_AI:
        check("LLM 用量记账", False, "本轮 --no-ai 未跑", skip=True)
        return
    _, u = req("GET", "/api/usage")
    check("提炼调用被记账", u["totals"]["calls"] >= 1)
    check("功能标签正确", "会话提炼" in u.get("by_feature", {}))
    check("模型维度有记录", len(u.get("by_model", {})) >= 1)


def t5_overdue(pid):
    print("\n[5] 逾期统计")
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    _, detail = req("GET", f"/api/projects/{pid}")
    task = detail["tasks"][0]
    task["due_date"] = yesterday
    status, _ = req("PUT", f"/api/tasks/{task['id']}", task)
    _, stats = req("GET", "/api/stats")
    check("逾期任务被统计", stats["overdue_tasks"] >= 1)


def t6_mcp_readonly():
    print("\n[6] MCP 只读模式")
    env = dict(os.environ)
    env["KANBAN_DATA_DIR"] = str(DATA_DIR)
    env["KANBAN_PORT"] = str(PORT)
    # mcp_server 用 ensure_ascii=False 往 stdout 写 JSON，中文（"只读模式"那句拒绝理由）就在里面。
    # 不钉 UTF-8，子进程会按控制台 cp936 编码、父进程按 UTF-8 解码 → 判成乱码，
    # 于是这条用例只在 GBK 控制台里红，跟产品没关系。
    env["PYTHONIOENCODING"] = "utf-8"
    req("PUT", "/api/ai/config", {"mcp_readonly": True})
    call = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "kanban_add_task",
                                  "arguments": {"project_id": "regression", "title": "不应创建"}}})
    python = str(ROOT / ".venv" / "Scripts" / "python.exe")
    if not Path(python).exists():
        python = sys.executable
    p = subprocess.run([python, str(ROOT / "scripts" / "mcp_server.py")],
                       input=call.encode("utf-8"), capture_output=True, timeout=60, env=env)
    out = p.stdout.decode("utf-8", "replace")
    line = [l for l in out.splitlines() if "jsonrpc" in l]
    d = json.loads(line[-1]) if line else {}
    denied = d.get("result", {}).get("isError") is True and "只读" in str(d.get("result", {}).get("content"))
    check("只读模式下写操作被拒", denied)
    # 读仍可用
    call2 = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "kanban_stats", "arguments": {}}})
    p2 = subprocess.run([python, str(ROOT / "scripts" / "mcp_server.py")],
                        input=call2.encode("utf-8"), capture_output=True, timeout=60, env=env)
    out2 = p2.stdout.decode("utf-8", "replace")
    line2 = [l for l in out2.splitlines() if "jsonrpc" in l]
    d2 = json.loads(line2[-1]) if line2 else {}
    check("只读模式下读操作正常", d2 and not d2.get("result", {}).get("isError", False))
    req("PUT", "/api/ai/config", {"mcp_readonly": False})


def main():
    print("=" * 66)
    print("发版回归测试（v1.1+ 新功能链路）· 独立实例 · 不碰真实数据")
    print(f"AI 提炼步骤：{'跳过（--no-ai）' if NO_AI else '真实调用（消耗少量配额）'}")
    print("=" * 66)
    proc = start_server()
    mem_id = None
    try:
        status, _ = req("GET", "/api/health")
        check("回归实例就绪", status == 200)
        if not NO_AI:
            check("真实端点 Key 同步至回归实例", sync_keys_to_instance())
        # 建演练项目（放最前，供会话捕获入库），并预置一个任务供 t2 使用
        s, proj = req("POST", "/api/projects", {
            "name": "回归演练-临时", "status": "进行中", "progress": 30,
            "background": "回归演练用项目。", "handoff_note": "演练交接说明。",
            "next_action": "演练下一步"})
        check("演练项目创建", s == 201)
        pid = proj["id"]
        req("POST", f"/api/projects/{pid}/tasks",
            {"title": "预置的初始任务", "status": "待办", "priority": "中"})

        pid = t1_session_capture(pid)
        t2_journal(pid)
        mem_id = t3_memories(pid)
        t4_usage()
        t5_overdue(pid)
        t6_mcp_readonly()

        print("\n[7] 清理演练数据")
        req("DELETE", f"/api/projects/{pid}")
        if mem_id:
            req("DELETE", "/api/memories/" + mem_id)
        _, lst = req("GET", "/api/memories")
        clean = not any("回归决策" in m.get("text", "") for m in lst.get("memories", []))
        check("演练数据已清理", clean)
    finally:
        stop_server(proc)
        import shutil
        shutil.rmtree(DATA_DIR, ignore_errors=True)

    print("\n" + "=" * 66)
    print(f"发版回归结果：{len(passed)} 通过 / {len(failed)} 失败"
          + (f" / {len(skipped)} 未验（{'、'.join(skipped)}）" if skipped else ""))
    if failed:
        print("失败项：", "；".join(failed))
        sys.exit(1)
    if skipped:
        print(f"结论：已验的 {len(passed)} 项可用；{len(skipped)} 项本轮跳过，**不能说全部可用**")
        print("      要拿「新功能链路全部可用」这句话，得跑一次不带 --no-ai 的（会消耗少量配额）")
    else:
        print("结论：v1.1 新功能链路全部可用，可发版 ✓")


if __name__ == "__main__":
    main()
