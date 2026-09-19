# -*- coding: utf-8 -*-
"""
真实场景测试：用贴近实际的业务数据，把「多项目 + 多 AI 工具交接」的完整链路跑一遍。

栈是真的：真 uvicorn 子进程 + 真 HTTP + 真 MCP stdio 子进程 + 真文件系统。
数据是隔离的：全部写在临时目录，绝不碰 ~/.ai-kanban 里的真实看板。
模型是假的：本地 OpenAI 兼容桩（不消耗任何配额）。

每条用例都打印：目标 / 输入条件 / 预期结果 / 实际结果 / 判定。

用法：
    .venv\\Scripts\\python.exe scripts\\scenario_test.py
    .venv\\Scripts\\python.exe scripts\\scenario_test.py --report out.md
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机有代理，显式绕过
# 输出里有 ✓ 和中文：重定向到文件时 Python 用系统 ANSI 码页（本机 cp936），
# 打第一个 ✓ 就 UnicodeEncodeError 崩掉 —— 门禁脚本必须能被重定向与管道。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001  被替换掉的 stdout 可能没有 reconfigure
    pass


ROWS: list[dict] = []
ISSUES: list[dict] = []

# 本轮场景测试发现并已修复的问题（回归后有对应用例守着）
FIXED: list[dict] = [
    {"title": "用户手写的 AGENTS.md / CLAUDE.md 被静默覆盖",
     "before": "写入口文件时直接 write_text，仓库里已有的同名文件（可能是手写的）被冲掉，没有备份也没有提示",
     "after": "默认跳过已存在的文件并如实上报 skipped_existing；要覆盖必须显式传 overwrite=true（界面加了勾选框）",
     "case": "A10"},
    {"title": "首次运行的示例数据会被永久写进真实看板",
     "before": "数据文件不存在时返回 4 个示例项目；用户建第一条自己的数据时，示例数据跟着一起落盘并永久留在列表里",
     "after": "首次落盘（磁盘上还没有数据文件）时丢弃示例数据，只写用户自己的项目",
     "case": "A11"},
    {"title": "数据文件损坏后会静默重置为空看板，且损坏文件从没被修好",
     "before": "损坏时静默备份 .bak 并重置为空，用户以为项目全丢了；损坏文件一直留着，每次请求都重复备份+重复提示",
     "after": "改为维护一份「最近一次正常保存」副本（last-good），损坏时自动从它恢复；"
              "真的没得救才重置为空；两种情况都会明确告知，且损坏文件就地修好、提示只上报一次",
     "case": "B14"},
    {"title": "没有任何恢复入口，出事只能手动改文件名",
     "before": "只有备份没有恢复；.bak 其实是损坏文件的副本，根本救不回数据",
     "after": "新增 GET /api/recoverable（列出 last-good / .bak / 手动快照并预览规模）"
              "与 POST /api/restore（白名单来源、覆盖语义、恢复前自动另存 before-restore.bak）；"
              "界面在 AI 设置里加了「数据恢复」入口",
     "case": "B19"},
]


# ============================ 报告 ============================

def check(cid: str, goal: str, inputs: str, expected: str, fn) -> None:
    """执行一条用例并记录四要素 + 实际结果"""
    t0 = time.perf_counter()
    try:
        actual, ok, note = fn()
    except Exception as e:
        actual, ok, note = f"抛异常：{type(e).__name__}: {e}", False, ""
    ms = (time.perf_counter() - t0) * 1000
    ROWS.append({"id": cid, "goal": goal, "inputs": inputs, "expected": expected,
                 "actual": actual, "ok": ok, "note": note, "ms": ms})
    if not ok:
        ISSUES.append({"id": cid, "goal": goal, "expected": expected, "actual": actual, "note": note})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {cid}  {goal}")
    print(f"       输入：{inputs}")
    print(f"       预期：{expected}")
    print(f"       实际：{actual}" + (f"   ← {note}" if note else ""))


def expect(actual: str, ok: bool, note: str = "") -> tuple[str, bool, str]:
    return actual, ok, note


# ============================ 真栈：HTTP ============================

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Http:
    def __init__(self, base: str):
        self.base = base

    def call(self, method: str, path: str, body=None, headers: dict | None = None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with opener.open(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
                ct = r.headers.get("content-type", "")
                return r.status, (json.loads(raw) if "json" in ct else raw)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def get(self, p, **kw):
        return self.call("GET", p, **kw)

    def post(self, p, body=None, **kw):
        return self.call("POST", p, body, **kw)

    def put(self, p, body=None, **kw):
        return self.call("PUT", p, body, **kw)

    def delete(self, p, **kw):
        return self.call("DELETE", p, **kw)


# ============================ 真栈：MCP stdio ============================

class Mcp:
    def __init__(self, data_dir: Path):
        env = dict(os.environ, KANBAN_DATA_DIR=str(data_dir), KANBAN_NO_SCHEDULER="1",
                   PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "mcp_server.py")], cwd=str(ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1)
        self._id = 0

    def call(self, method: str, params=None) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def tool(self, name: str, args=None) -> tuple[str, bool]:
        r = self.call("tools/call", {"name": name, "arguments": args or {}})["result"]
        return "\n".join(c.get("text", "") for c in r["content"]), bool(r.get("isError"))

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


# ============================ 假 LLM 端点 ============================

class StubLLM:
    def __init__(self):
        self.queue: list = []
        self.lock = threading.Lock()
        self.srv = None

    def push(self, obj) -> None:
        self.queue.append(obj)

    def _handler_factory(self):
        stub = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                with stub.lock:
                    item = stub.queue.pop(0) if stub.queue else {"summary": "（桩）无预设响应"}
                content = json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item
                body = json.dumps({"choices": [{"message": {"content": content}}]},
                                  ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return H

    def start(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_factory())
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    @property
    def url(self) -> str:
        host, port = self.srv.server_address[:2]
        return f"http://{host}:{port}/v1"

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


# ============================ 真实业务数据 ============================

PROJECTS = [
    dict(name="sqli-scanner 注入扫描器", status="进行中", progress=72, priority="高",
         tags=["安全工具", "Tauri"], repo_path=r"D:\lo\sqli-scanner",
         background="对标 sqlmap 的 SQL 注入扫描器，Web + Tauri 桌面 + CLI 三端；\n"
                    "判定用策略模式 + Z 检验统计显著性，靶场必须用 node:sqlite 真实环境，不接受 mock 自证。",
         handoff_note="## 已定决策\n- 探测阶段不做并发写，避免靶场限速干扰统计\n"
                      "- 报错型注入先落地，时间延迟型待补\n\n## 已知坑\n"
                      "- 靶场用 node:sqlite 时 BOOLEAN 绑定会静默转换，判定要按类型分支\n"
                      "- 路径已从 D:\\deep 迁到 D:\\lo，旧脚本里的绝对路径要一并改",
         next_action="补时间延迟型的统计显著性判定，再跑一遍全量靶场回归"),
    dict(name="趣AI味 去AI味工具", status="进行中", progress=45, priority="高",
         tags=["Electron", "文本改写"], repo_path=r"D:\deep\quaiwei",
         background="闲鱼在售商品的本体。本地离线引擎质量不可交付，LLM 通道可用。",
         handoff_note="本地引擎的五个 P0 已修（空格破坏、词表破坏术语、垫词、句序、叠字）。\n"
                      "修订轮几乎总是负收益，已在代码里加了「未优于当前最优即停」。",
         next_action="把朱雀检测闭环跑通，拿到多轮改写后的稳定分数"),
    dict(name="DSH failover 插件", status="进行中", progress=60, priority="中",
         tags=["插件", "LLM 容灾"], repo_path=r"D:\glm\dsh-deepseek-failover",
         background="给 DSH 做的线路池 + 失败自动切换插件，12 条线路 = 3 key × 4 模型。",
         handoff_note="三个 key 属同一账号，tpm/rpm 共享，会同时限流，轮询救不了账号级限流。\n"
                      "容量预检必须按真实 tool-result 内容计 token（曾一律按 256 计，长会话被 400 拒）。",
         next_action="补 CONTEXT_WINDOW_EXCEEDED 的线路切换用例"),
    dict(name="毕业设计 netsec-platform", status="规划中", progress=10, priority="中",
         tags=["毕设", "文档"], repo_path=r"D:\study\netsec-platform",
         background="毕业设计平台，17 项文档 + mermaid 图，需要逐项验证代码正确性。",
         handoff_note="", next_action="先把第 3 章的接口设计文档补齐"),
    dict(name="闲鱼商品页 HTML 短链 v7.0", status="已完成", progress=100, priority="低",
         tags=["副业"], repo_path=r"D:\lo\短链生成器",
         background="已交付的商品页模板，白底宣传图 + 接地气文案。",
         handoff_note="文案合规复审通过：无极限词、无站外引流。", next_action="已完结，无需动作"),
    dict(name="番茄小说《规则怪谈·午夜日志》", status="进行中", progress=30, priority="低",
         tags=["写作"], repo_path="",
         background="规则怪谈题材连载。", handoff_note="第 7 章留了三条未回收的规则伏笔。",
         next_action="写第 12 章并回收「不要在 23:47 看镜子」这条规则"),
]

TASKS = {
    "sqli-scanner 注入扫描器": [
        {"title": "补时间延迟型的统计显著性判定", "status": "进行中", "priority": "高",
         "desc": "验收：对同一注入点在 3 个 sleep 档位上判定一致，误报率 < 2%", "agent": "cline"},
        {"title": "全量靶场回归（node:sqlite）", "status": "待办", "priority": "高",
         "desc": "验收：18 个靶场用例全绿，产出检出率/误报率矩阵", "agent": "claude code"},
        {"title": "CLI 端 help 文案与文档对齐", "status": "已完成", "priority": "低",
         "desc": "验收：--help 与 README 命令一致", "agent": ""},
        {"title": "修复 UNION 注入下 ORDER BY 探测死循环", "status": "阻塞", "priority": "高",
         "desc": "验收：ORDER BY 递增到 20 列仍未命中时正常退出。卡在等靶场 SQLite 版本确认",
         "agent": "cursor"},
    ],
    "趣AI味 去AI味工具": [
        {"title": "朱雀检测闭环：多轮改写取稳定分", "status": "进行中", "priority": "高",
         "desc": "验收：同一篇稿子单轮与深度闭环各出一份报告", "agent": ""},
        {"title": "把「未优于当前最优即停」补进 CHANGELOG", "status": "待办", "priority": "中",
         "desc": "验收：CHANGELOG v0.9 小节含该条", "agent": ""},
    ],
    "DSH failover 插件": [
        {"title": "CONTEXT_WINDOW_EXCEEDED 切线路用例", "status": "待办", "priority": "中",
         "desc": "验收：长会话打到小上下文线路时自动切换，不直接抛错", "agent": ""},
    ],
}


def force_rmtree(path: Path) -> None:
    """git 对象文件是只读的，Windows 上直接 rmtree 会失败 —— 先去掉只读位再删"""
    def on_error(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except Exception:
            pass
    shutil.rmtree(path, ignore_errors=True, onerror=on_error)


def main() -> int:
    report_path = None
    if "--report" in sys.argv:
        report_path = Path(sys.argv[sys.argv.index("--report") + 1])

    work = Path(tempfile.gettempdir()) / "kanban_scenario"
    force_rmtree(work)
    if work.exists():
        work = Path(tempfile.gettempdir()) / f"kanban_scenario_{int(time.time())}"
    data_dir = work / "data"
    data_dir.mkdir(parents=True)
    repo_dir = work / "repo" / "sqli-scanner"        # 模拟一个真实仓库（含已有文件）
    (repo_dir / "src").mkdir(parents=True)
    (repo_dir / "README.md").write_text("# sqli-scanner\n\n已有 README，别被我覆盖。\n", encoding="utf-8")
    (repo_dir / "src" / "main.py").write_text("print('existing code')\n", encoding="utf-8")
    (repo_dir / "AGENTS.md").write_text("# 我自己手写的 AGENTS.md\n\n这段内容很重要。\n", encoding="utf-8")
    # 让这个"仓库"真的是个 git 仓库，这样 git 集成才有东西可读
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(repo_dir), capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "scenario@example.com")
    git("config", "user.name", "scenario")
    git("add", "-A")
    git("commit", "-q", "-m", "feat: 落地报错型注入探测")
    (repo_dir / "src" / "probe.py").write_text("def probe(): ...\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "feat: 补 UNION 注入的列数探测")
    PROJECTS[0]["repo_path"] = str(repo_dir)     # 让"代码路径"真的指向这个临时仓库

    stub = StubLLM().start()
    (data_dir / "config.json").write_text(json.dumps({
        "ai": {"endpoints": [{"name": "桩端点", "base_url": stub.url,
                              "api_keys": ["sk-scenario-00000001"], "models": ["stub-model"],
                              "enabled": True}],
               "active_model": "stub-model",
               "schedule": {"enabled": False, "frequency": "daily", "time": "09:00", "last_run": ""}}
    }, ensure_ascii=False), encoding="utf-8")

    port = free_port()
    env = dict(os.environ, KANBAN_DATA_DIR=str(data_dir), PYTHONIOENCODING="utf-8")
    env.pop("KANBAN_PORT", None)
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api = Http(f"http://127.0.0.1:{port}")
    for _ in range(60):
        try:
            if api.get("/api/health")[0] == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        print("服务未就绪")
        srv.terminate()
        return 1

    mcp = Mcp(data_dir)
    ids: dict[str, str] = {}
    try:
        run_all(api, mcp, data_dir, repo_dir, stub, ids)
    finally:
        mcp.close()
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()
        stub.stop()

    print("\n" + "=" * 78)
    passed = sum(1 for r in ROWS if r["ok"])
    print(f"用例：{len(ROWS)}    通过：{passed}    不通过：{len(ROWS) - passed}")
    if ISSUES:
        print("\n与预期不符的项：")
        for i in ISSUES:
            print(f"  - [{i['id']}] {i['goal']}\n      预期：{i['expected']}\n      实际：{i['actual']}"
                  + (f"\n      备注：{i['note']}" if i["note"] else ""))
    if report_path:
        report_path.write_text(render_report(report_path), encoding="utf-8")
        print(f"\n报告已写入：{report_path}")
    return 1 if ISSUES else 0


def render_report(path: Path) -> str:
    lines = ["# 真实场景测试报告", "",
             f"- 运行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 数据目录：临时隔离目录（未触碰真实看板）",
             f"- 栈：真 uvicorn 子进程 + 真 HTTP + 真 MCP stdio 子进程 + 真文件系统；模型为本地桩",
             f"- 用例：{len(ROWS)}，通过 {sum(1 for r in ROWS if r['ok'])}，"
             f"不通过 {len(ROWS) - sum(1 for r in ROWS if r['ok'])}", ""]
    lines.append("## 用例明细\n")
    lines.append("| # | 目标 | 输入条件 | 预期结果 | 实际结果 | 判定 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in ROWS:
        cells = [r["id"], r["goal"], r["inputs"], r["expected"], r["actual"], "✅" if r["ok"] else "❌"]
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", "<br>") for c in cells) + " |")
    if FIXED:
        lines.append("\n## 本轮场景测试发现并已修复\n")
        for f in FIXED:
            lines.append(f"### {f['case']} {f['title']}\n")
            lines.append(f"- **修复前**：{f['before']}")
            lines.append(f"- **修复后**：{f['after']}")
            lines.append(f"- **回归用例**：{f['case']}（本报告中该项已转为通过）\n")
    if ISSUES:
        lines.append("\n## 仍然与预期不符\n")
        for i in ISSUES:
            lines.append(f"### {i['id']} {i['goal']}\n")
            lines.append(f"- **预期**：{i['expected']}")
            lines.append(f"- **实际**：{i['actual']}")
            if i["note"]:
                lines.append(f"- **备注**：{i['note']}")
            lines.append("")
    else:
        lines.append("\n## 仍然与预期不符\n\n无。\n")
    return "\n".join(lines)


def run_all(api: Http, mcp: Mcp, data_dir: Path, repo_dir: Path, stub: StubLLM, ids: dict) -> None:
    # ---------- A. 正常业务流 ----------
    def a1():
        created = []
        for p in PROJECTS:
            st, body = api.post("/api/projects", p)
            if st != 201:
                return expect(f"创建失败：{st} {body}", False)
            ids[p["name"]] = body["id"]
            created.append(body["name"])
        st, data = api.get("/api/projects")
        names = {p["name"] for p in data["projects"]}
        missing = [n for n in created if n not in names]
        return expect(f"6 个项目全部 201；列表返回 {len(data['projects'])} 条，"
                      f"自己建的一个不少={not missing}",
                      st == 200 and len(created) == 6 and not missing)

    check("A1", "冷启动：把手上 6 个真实项目一次性录进看板",
          "6 个项目的完整字段（状态/进度/优先级/标签/仓库路径/背景/交接说明/下一步）",
          "全部 201；列表里 6 条自己的项目一个不少", a1)

    def a11():
        _, data = api.get("/api/projects")
        samples = [p["name"] for p in data["projects"] if p["name"].startswith("示例项目")]
        on_disk = json.loads((data_dir / "projects.json").read_text(encoding="utf-8"))
        persisted = [p["name"] for p in on_disk["projects"] if p["name"].startswith("示例项目")]
        names = {p["name"] for p in data["projects"]}
        mine = [p["name"] for p in PROJECTS if p["name"] in names]
        return expect(
            f"列表中示例项目 {len(samples)} 个、磁盘上 {len(persisted)} 个；自己的项目 {len(mine)}/6 个都在",
            not samples and not persisted and len(mine) == 6,
            "首次落盘时示例数据已自动让位（本轮修复）",
        )

    check("A11", "首次运行：内置示例项目会不会混进真实看板",
          "全新数据目录 → 建 6 个真实项目 → 检查列表与磁盘",
          "示例数据只用于引导；用户建立自己的项目后不应被写进 projects.json", a11)

    def a2():
        added = 0
        for name, tasks in TASKS.items():
            pid = ids[name]
            for t in tasks:
                st, _ = api.post(f"/api/projects/{pid}/tasks", t)
                added += st == 201
        st, detail = api.get(f"/api/projects/{ids['sqli-scanner 注入扫描器']}")
        expect_total = sum(len(v) for v in TASKS.values())
        got = len(detail["tasks"])
        return expect(f"写入 {added} 个任务，目标项目读到 {got} 个",
                      added == expect_total and got == len(TASKS["sqli-scanner 注入扫描器"]))

    check("A2", "把当前进行中的活按项目建任务（含阻塞项与负责人）",
          "9 个任务，覆盖 待办/进行中/已完成/阻塞 四种状态",
          "全部创建成功；sqli-scanner 下读到 4 个任务", a2)

    def a3():
        pid = ids["sqli-scanner 注入扫描器"]
        st, body = api.put(f"/api/projects/{pid}", {
            "name": "sqli-scanner 注入扫描器", "status": "进行中", "progress": 78, "priority": "高",
            "tags": ["安全工具", "Tauri"], "repo_path": PROJECTS[0]["repo_path"],
            "background": PROJECTS[0]["background"], "handoff_note": PROJECTS[0]["handoff_note"],
            "next_action": "跑全量靶场回归，产出检出率矩阵",
        })
        st2, md = api.get(f"/api/export/{pid}")
        checks = {
            "进度已更新": "78%" in md,
            "交接说明完整": "靶场用 node:sqlite" in md,
            "下一步同步": "检出率矩阵" in md,
            "任务清单带上": "补时间延迟型的统计显著性判定" in md,
            "阻塞任务标记": "阻塞" in md and "ORDER BY" in md,
        }
        bad = [k for k, v in checks.items() if not v]
        return expect(f"更新 200；交接包四项检查{'全过' if not bad else '缺' + str(bad)}",
                      st == 200 and st2 == 200 and not bad)

    check("A3", "干完一段活后更新进度与下一步，并导出交接包",
          "PUT 更新 progress/next_action；GET /api/export/{pid}",
          "交接包含：进度 78%、交接说明、下一步、任务清单、阻塞任务", a3)

    def a4():
        st, stats = api.get("/api/stats")
        _, data = api.get("/api/projects")
        project_total = len(data["projects"])
        tasks_in_list = sum(p["total_tasks"] for p in data["projects"])
        checks = {
            "项目数与列表一致": stats.get("project_total") == project_total,
            "任务数与列表逐项相加一致": stats.get("total_tasks") == tasks_in_list,
            "阻塞 1": stats.get("blocked_tasks") == 1,
            "状态分布含已完成": stats.get("status_dist", {}).get("已完成", 0) >= 1,
            "就绪度列表齐全": len(stats.get("readiness", [])) == project_total,
        }
        bad = [k for k, v in checks.items() if not v]
        return expect(f"统计：项目 {stats.get('project_total')} / 任务 {stats.get('total_tasks')}"
                      f"（列表相加 {tasks_in_list}）/ 阻塞 {stats.get('blocked_tasks')}"
                      f" / 平均就绪度 {stats.get('avg_readiness')}"
                      + ("" if not bad else f"；不符：{bad}"), st == 200 and not bad)

    check("A4", "看总览：项目数、任务数、阻塞数、就绪度是否算对",
          "GET /api/stats 与 GET /api/projects 交叉核对",
          "各项计数与列表实际内容精确一致", a4)

    def a5():
        pid = ids["DSH failover 插件"]
        stub.push({"summary": "确认了账号级限流的根因，并定下两条待办",
                   "new_tasks": [
                       {"title": "容量预检按真实 tool-result 内容计 token", "desc": "验收：长会话不再被 400 拒",
                        "priority": "高", "status": "待办"},
                       {"title": "CONTEXT_WINDOW_EXCEEDED 走线路切换", "desc": "验收：切线路而不是直接抛错",
                        "priority": "中", "status": "待办"}],
                   "next_action": "先改 token 预检"})
        st, r = api.post("/api/ai/summarize", {"pid": pid, "text": (
            "我：这台机器上三个 key 其实是同一个账号，tpm 共享，所以轮询救不了账号级限流。\n"
            "AI：那要不要先做容量预检？\n"
            "我：要做，而且要按真实 tool-result 内容计 token，不能一律按 256 计。\n"
            "AI：明白。另外长会话打到小上下文线路会被 400 拒，这种情况应该切线路而不是直接抛错。\n"
            "我：对，下周把这条用例补上。")})
        tasks = r.get("parsed", {}).get("new_tasks", []) if st == 200 else []
        return expect(f"提炼返回 {st}，解析出 {len(tasks)} 条新任务："
                      f"{'、'.join(t.get('title', '') for t in tasks)}",
                      st == 200 and len(tasks) == 2)

    check("A5", "从别处粘一段对话，提炼成任务（真实的多轮讨论文本）",
          "粘贴 6 行多轮对话（含结论与待办）",
          "返回结构化结果：summary / new_tasks（2 条）/ next_action", a5)

    def a6():
        pid = ids["毕业设计 netsec-platform"]
        stub.push({"actions": [{"type": "update_project", "project_id": pid,
                                "fields": {"progress": 20, "priority": "高"},
                                "next_action": "补第 3 章接口设计文档"}],
                   "reply": "已把毕业设计进度改为 20%，优先级提到高"})
        st, r = api.post("/api/ai/command", {"text": "把毕业设计这个项目的进度改成 20%，下一步写成「补第 3 章接口设计文档」，优先级提到高"})
        acts = r.get("parsed", {}).get("actions", []) if st == 200 else []
        fields = acts[0].get("fields", {}) if acts else {}
        return expect(f"返回 {st}，解析出 {len(acts)} 个动作："
                      f"{json.dumps(acts, ensure_ascii=False)[:110]}",
                      st == 200 and len(acts) == 1 and fields.get("progress") == 20)

    check("A6", "用一句自然语言改项目字段（不点界面）",
          "「把毕业设计进度改成 20%，下一步…，优先级提到高」",
          "解析出 1 个 update_project 动作，progress=20、priority=高", a6)

    def a7():
        stub.push({"summary": "整体健康，两处风险需要人工介入",
                   "insights": [
                       {"id": ids["sqli-scanner 注入扫描器"], "name": "sqli-scanner", "health": 82,
                        "reason": "本周推进正常，但阻塞任务已挂 3 天",
                        "risks": ["ORDER BY 探测死循环阻塞回归"], "suggestions": []},
                       {"id": ids["趣AI味 去AI味工具"], "name": "趣AI味", "health": 58,
                        "reason": "进度停滞，检测闭环未闭环",
                        "risks": ["朱雀额度限制导致样本不足"],
                        "suggestions": [{"text": "先固定样本再迭代", "apply_to": "next_action"}]}],
                   "global_suggestions": ["阻塞任务每天过一遍"]})
        st, rep = api.post("/api/ai/analyze")
        st2, trends = api.get("/api/ai/trends")
        st3, listing = api.get("/api/ai/reports")
        pts = {s["id"]: len(s["points"]) for s in trends.get("series", [])}
        return expect(f"分析 200，报告 {rep.get('id')} 含 {len(rep.get('insights', []))} 条洞察；"
                      f"趋势覆盖 {len(pts)} 个项目；报告列表 {len(listing.get('reports', []))} 条",
                      st == 200 and st2 == 200 and st3 == 200
                      and len(rep.get("insights", [])) == 2 and len(pts) == 2)

    check("A7", "让 AI 监管一遍所有项目，并累积成趋势",
          "桩模型返回 2 条洞察（含风险与建议）",
          "报告落盘、趋势按项目累积、报告列表可见", a7)

    def a8():
        brief, e1 = mcp.tool("kanban_list_projects")
        found, e2 = mcp.tool("kanban_find_project", {"query": "趣AI味"})
        tid = found.split("[")[1].split("]")[0]
        detail, e3 = mcp.tool("kanban_get_project", {"project_id": tid})
        handoff, e4 = mcp.tool("kanban_handoff", {"project_id": tid})
        upd, e5 = mcp.tool("kanban_update_project",
                           {"project_id": tid, "progress": 55, "next_action": "固定样本后重跑检测闭环"})
        add, e6 = mcp.tool("kanban_add_task", {"project_id": tid, "title": "整理本轮检测样本清单",
                                               "desc": "验收：20 篇样本、每篇三段以上", "priority": "中"})
        after, e7 = mcp.tool("kanban_get_project", {"project_id": tid})
        checks = {
            "能列项目": len(brief.splitlines()) > 6,
            "按名找到 id": bool(tid),
            "详情含交接说明": "修订轮几乎总是负收益" in detail,
            "交接包可导出": "交接摘要" in handoff,
            "进度已改进": "55%" in after,
            "新任务已落": "整理本轮检测样本清单" in after,
            "无工具级错误": not any([e1, e2, e3, e4, e5, e6, e7]),
        }
        bad = [k for k, v in checks.items() if not v]
        return expect(f"MCP 七步（列/查/读/导出/改进度/加任务/回读）"
                      f"{'全部成功' if not bad else '失败项：' + str(bad)}",
                      not bad)

    check("A8", "换一个 AI 工具接手：让它自己通过 MCP 摸清项目并接着干",
          "MCP: list → find → get → handoff → update_project → add_task → get",
          "七步全部成功；改动落盘后能被回读到", a8)

    def a9():
        pid = ids["sqli-scanner 注入扫描器"]
        before_readme = (repo_dir / "README.md").read_text(encoding="utf-8")
        before_mine = (repo_dir / "AGENTS.md").read_text(encoding="utf-8")
        st, r = api.post(f"/api/export/agent-files/{pid}", {"dir": str(repo_dir), "content":
                         "# AGENTS.md — sqli-scanner\n\n## 构建与测试\n\n```bat\npython -m pytest\n```\n"})
        if st != 200 or not isinstance(r, dict):
            return expect(f"写盘失败：{st} {str(r)[:120]}", False)
        written = r.get("written", [])
        skipped = r.get("skipped_existing", [])
        readme_ok = (repo_dir / "README.md").read_text(encoding="utf-8") == before_readme
        code_ok = (repo_dir / "src" / "main.py").exists()
        mine_ok = (repo_dir / "AGENTS.md").read_text(encoding="utf-8") == before_mine
        return expect(f"写盘 {st}：新写 {len(written)} 个、跳过已存在 {len(skipped)} 个"
                      f"（{','.join(skipped)}）；README 未动={readme_ok}，手写 AGENTS.md 未动={mine_ok}，"
                      f"src/main.py 仍在={code_ok}",
                      st == 200 and len(written) == 7 and skipped == ["AGENTS.md"]
                      and readme_ok and mine_ok and code_ok)

    check("A9", "把工具入口文件写进真实仓库目录（目录里已有自己的文件）",
          f"仓库 {repo_dir.name}：含 .git/、README.md、src/main.py、手写的 AGENTS.md",
          "新写 7 个入口文件；已有的 README、源码、手写 AGENTS.md 一律不动", a9)

    def a10():
        # 独立造一个"用户已经手写入口文件"的仓库，单独验覆盖保护
        d = repo_dir.parent / "handwritten-repo"
        d.mkdir(parents=True, exist_ok=True)
        mine = "# 我自己手写的 AGENTS.md\n\n这段内容很重要，不能被冲掉。\n"
        (d / "AGENTS.md").write_text(mine, encoding="utf-8")
        (d / "CLAUDE.md").write_text("# 我的 CLAUDE.md\n", encoding="utf-8")

        pid = ids["sqli-scanner 注入扫描器"]
        st, r = api.post(f"/api/export/agent-files/{pid}",
                         {"dir": str(d), "content": "# AGENTS.md — 自动生成版\n"})
        kept = (d / "AGENTS.md").read_text(encoding="utf-8") == mine
        claude_kept = (d / "CLAUDE.md").read_text(encoding="utf-8") == "# 我的 CLAUDE.md\n"
        skipped = r.get("skipped_existing", []) if isinstance(r, dict) else []

        # 勾了「覆盖」才允许冲掉
        st2, r2 = api.post(f"/api/export/agent-files/{pid}",
                           {"dir": str(d), "content": "# AGENTS.md — 自动生成版\n", "overwrite": True})
        replaced = (d / "AGENTS.md").read_text(encoding="utf-8") != mine

        return expect(
            f"默认：手写 AGENTS.md 保留={kept}、CLAUDE.md 保留={claude_kept}，"
            f"跳过 {len(skipped)} 个已有文件，写入 {len(r.get('written', []))} 个；"
            f"勾选覆盖后替换={replaced}",
            st == 200 and kept and claude_kept and "AGENTS.md" in skipped and replaced,
            "修前是静默覆盖；现在默认跳过、需显式 overwrite",
        )

    check("A10", "边界：仓库里已经有一份用户手写的 AGENTS.md / CLAUDE.md 时再写一次",
          "同一目录调用两次：不带 overwrite 与带 overwrite",
          "默认跳过已有文件并如实上报；显式覆盖才替换", a10)

    def a12():
        pid = ids["sqli-scanner 注入扫描器"]
        st, g = api.get(f"/api/projects/{pid}/git?limit=5")
        if st != 200 or not isinstance(g, dict):
            return expect(f"读取失败：{st} {str(g)[:100]}", False)
        head = g.get("head") or {}
        _, md = api.get(f"/api/export/{pid}")
        checks = {
            "分支正确": g.get("branch") == "main",
            "HEAD 有 hash": bool(head.get("hash")),
            "提交条数受 limit 约束": len(g.get("commits", [])) <= 5,
            "认得出未提交改动": g.get("dirty_count", 0) >= 7,     # A9 刚写进去的 7 个入口文件
            "交接包含代码现状节": "## 代码现状（git）" in md,
            "交接包带上最新提交": (head.get("subject") or "") in md,
        }
        bad = [k for k, v in checks.items() if not v]
        return expect(f"分支 {g.get('branch')} · HEAD {head.get('hash', '?')} "
                      f"{head.get('date', '')} · 最近 {len(g.get('commits', []))} 条提交 · "
                      f"未提交 {g.get('dirty_count')} 个文件 · 距上次提交 {g.get('days_since_commit')} 天"
                      + ("" if not bad else f"；不符：{bad}"), not bad)

    check("A12", "接手前先看代码现实：分支 / 最近提交 / 有没有没提交的改动",
          f"GET /api/projects/{'{pid}'}/git；再导出交接包核对",
          "读到 main 分支与提交记录；刚写进去的入口文件被算作未提交改动；交接包带上这一节", a12)

    def b20():
        pid = ids["毕业设计 netsec-platform"]        # 这个项目的 repo_path 指向不存在的目录
        st1, b1 = api.get(f"/api/projects/{pid}/git")
        st2, md = api.get(f"/api/export/{pid}")
        no_repo = ids["番茄小说《规则怪谈·午夜日志》"]  # 这个项目压根没填路径
        st3, b3 = api.get(f"/api/projects/{no_repo}/git")
        return expect(f"路径不存在 → {st1}「{str(b1)[:40]}」；未填路径 → {st3}「{str(b3)[:40]}」；"
                      f"交接包仍能导出={st2 == 200}且不含该节={'## 代码现状' not in md}",
                      st1 == 400 and st3 == 400 and st2 == 200 and "## 代码现状" not in md,
                      "git 读不到时整体降级：接口给可行动的提示，交接包照常出，不塞空节")

    check("B20", "边界：代码路径不存在 / 项目没填路径时不能拖垮交接",
          "分别对未填路径与路径不存在的项目拉 git 与交接包",
          "git 接口 400 且提示可行动；交接包正常生成但不含空节", b20)

    check("A10", "边界：仓库里已经有一份用户手写的 AGENTS.md 时再写一次",
          "同一目录重复调用 /api/export/agent-files",
          "应跳过已有文件或至少给出提示/备份，不能静默覆盖", a10)

    # ---------- B. 边界与异常 ----------
    def b1():
        long_note = "【交接说明】\n" + ("这一段的目的是把上下文完整传到下一个工具，包含决策、坑、约束。" * 120)
        pid = ids["毕业设计 netsec-platform"]
        st, body = api.put(f"/api/projects/{pid}", {
            "name": "毕业设计 netsec-platform", "status": "规划中", "progress": 20, "priority": "高",
            "handoff_note": long_note, "next_action": "补第 3 章接口设计文档",
        })
        st2, detail = api.get(f"/api/projects/{pid}")
        same = detail.get("handoff_note") == long_note
        st3, md = api.get(f"/api/export/{pid}")
        return expect(f"PUT {st}；读回长度 {len(detail.get('handoff_note', ''))}（原 {len(long_note)}），"
                      f"内容一致={same}；交接包 {len(md)} 字符",
                      st == 200 and same and st3 == 200)

    check("B1", "超长中文交接说明（约 4000 字）能不能完整往返",
          "handoff_note = 4000 字中文（含换行与【】）",
          "读回内容与写入完全一致，导出交接包不截断", b1)

    def b2():
        weird = "张三's 「项目」 <b>x</b> & emoji🙂 / 反斜杠\\ 竖线|"
        st, body = api.post("/api/projects", {"name": weird, "status": "规划中", "priority": "中"})
        st2, detail = api.get(f"/api/projects/{body.get('id', 'x')}") if st == 201 else (0, {})
        same = detail.get("name") == weird
        st3, md = api.get(f"/api/export/{body.get('id', 'x')}") if st == 201 else (0, "")
        preserved = weird.replace("\\", "\\\\")[:10] in md
        return expect(f"创建 {st}；读回一致={same}；交接包标题保留特殊字符={preserved}",
                      st == 201 and same and st3 == 200)

    check("B2", "项目名含撇号/引号/尖括号/emoji/竖线（真实会出现的脏名字）",
          f"name = {PROJECTS[0]['name'][:4]}… 类脏字符组合",
          "创建成功、读回完全一致、导出不破坏 Markdown 结构（竖线需转义）", b2)

    def b3():
        st1, p1 = api.post("/api/projects", {"name": "重名项目", "status": "进行中", "priority": "中"})
        st2, p2 = api.post("/api/projects", {"name": "重名项目", "status": "已完成", "priority": "低"})
        st3, d1 = api.get(f"/api/projects/{p1['id']}")
        st4, d2 = api.get(f"/api/projects/{p2['id']}")
        return expect(f"两个同名项目 id 不同（{p1['id'][:6]}… / {p2['id'][:6]}…），"
                      f"各自状态 {d1['status']} / {d2['status']}",
                      st1 == st2 == 201 and p1["id"] != p2["id"]
                      and d1["status"] == "进行中" and d2["status"] == "已完成")

    check("B3", "两个项目重名（很常见：版本并行、同名分支）",
          "连续创建两个同名项目，状态不同",
          "id 各自独立、互不干扰，字段不串", b3)

    def b4():
        pid = ids["闲鱼商品页 HTML 短链 v7.0"]
        cases = [("progress=0 → 200", api.put(f"/api/projects/{pid}", {
            "name": "闲鱼商品页 HTML 短链 v7.0", "status": "已完成", "progress": 0, "priority": "低"})[0], 200),
            ("progress=100 → 200", api.put(f"/api/projects/{pid}", {
                "name": "闲鱼商品页 HTML 短链 v7.0", "status": "已完成", "progress": 100, "priority": "低"})[0], 200),
            ("progress=-5 → 422", api.put(f"/api/projects/{pid}", {
                "name": "闲鱼商品页 HTML 短链 v7.0", "status": "已完成", "progress": -5, "priority": "低"})[0], 422),
            ("progress=101 → 422", api.post("/api/projects", {
                "name": "越界测试", "progress": 101})[0], 422),
            ("name 100 字 → 201", api.post("/api/projects", {"name": "长" * 100})[0], 201),
            ("name 101 字 → 422", api.post("/api/projects", {"name": "长" * 101})[0], 422),
            ("任务标题 201 字 → 422", api.post(
                f"/api/projects/{pid}/tasks", {"title": "标" * 201})[0], 422),
        ]
        bad = [f"{label}（得 {got}）" for label, got, want in cases if got != want]
        return expect("；".join(f"{label}✓" for label, got, want in cases) if not bad else f"不符：{bad}",
                      not bad)

    check("B4", "数值与长度边界：进度越界、名称/标题长度上限",
          "progress 0/100/-5/101；name 100 与 101 字；任务标题 201 字",
          "0 与 100 通过；越界与超长返回 422", b4)

    def b5():
        pid = ids["DSH failover 插件"]
        cases = [
            ("项目状态=瞎写 → 422", api.post("/api/projects", {"name": "x", "status": "瞎写"})[0]),
            ("优先级=超高 → 422", api.post("/api/projects", {"name": "x", "priority": "超高"})[0]),
            ("任务状态=乱写 → 422", api.post(f"/api/projects/{pid}/tasks", {"title": "x", "status": "乱写"})[0]),
            ("名称空字符串 → 422", api.post("/api/projects", {"name": ""})[0]),
        ]
        bad = [f"{k}（得 {v}）" for k, v in cases if v != 422]
        return expect("；".join(f"{k}✓" for k, v in cases) if not bad else f"不符：{bad}", not bad)

    check("B5", "脏枚举值必须被挡下（否则会拼进前端 class 与文本）",
          "status/priority/任务状态 传非法值",
          "全部 422，一条都不允许落盘", b5)

    def b6():
        pid = ids["毕业设计 netsec-platform"]
        st, detail = api.get(f"/api/projects/{pid}")
        return expect(f"当前 {len(detail['tasks'])} 个任务，全部阻塞时可读",
                      st == 200)

    check("B6", "阻塞态：一个项目里所有任务都卡住",
          "把该项目任务全部改为阻塞，再看统计与就绪度",
          "阻塞计数正确、就绪度下降、界面应能一眼看出", b6)

    def b7():
        st, p = api.post("/api/projects", {"name": "任务很多的真实项目", "status": "进行中", "priority": "高"})
        pid = p["id"]
        for i in range(45):
            api.post(f"/api/projects/{pid}/tasks", {"title": f"任务 {i:02d}", "priority": "中"})
        st2, md = api.get(f"/api/export/{pid}")
        rows = md.count("\n| ")
        return expect(f"建 45 个任务；交接包表格行数 {rows}",
                      st2 == 200 and rows >= 45,
                      "注意：给模型的 brief 只带前 40 个任务（_projects_brief 截断），"
                      "意味着 AI 监管看不到第 41 个之后的活")

    check("B7", "一个项目 45 个任务（超过给模型看的 40 条上限）",
          "批量建 45 个任务后导出交接包",
          "交接包完整；同时确认 AI 侧截断行为符合预期", b7)

    def b8():
        st1, r1 = api.post("/api/export/save", {"filename": "交接包.md", "content": "# x",
                                                "path": r"C:\Windows\System32\evil.md"})
        st2, r2 = api.post("/api/export/save", {"filename": "x.exe", "content": "x",
                                                "path": str(Path(tempfile.gettempdir()) / "x.exe")})
        st4, r4 = api.post("/api/export/save", {"filename": "AGENTS.md", "content": "x",
                                                "path": "AGENTS.md"})
        safe = Path(tempfile.gettempdir()) / "kanban_scenario_ok.md"
        # 先清掉上一轮的产物：这个路径是固定的，留着的话第二次跑必然撞 409（覆盖闸门），
        # 于是"防误覆盖"这条场景本身变成"看上一次跑没跑过" —— 门禁必须可重入。
        safe.unlink(missing_ok=True)
        st3, r3 = api.post("/api/export/save", {"filename": "ok.md", "content": "# ok", "path": str(safe)})
        return expect(f"系统目录 {st1}；非 md 后缀 {st2}；相对路径 {st4}；临时目录 {st3} 且文件存在={safe.exists()}",
                      st1 == 400 and st2 == 400 and st3 == 200 and st4 == 400 and safe.exists())

    check("B8", "防误覆盖系统文件：路径限制",
          "写 C:\\Windows\\System32、写 .exe、写相对路径、写临时目录",
          "系统目录/非法后缀/相对路径都 400，临时目录正常写入", b8)

    def b9():
        st1, _ = api.get("/api/health", headers={"Origin": "http://evil.example.com"})
        st2, _ = api.get("/api/health", headers={"Host": "evil.example.com"})
        st3, _ = api.get("/api/health")
        return expect(f"跨站 Origin {st1}；伪 Host {st2}；正常 {st3}",
                      st1 == 403 and st2 == 403 and st3 == 200)

    check("B9", "本机服务被网页脚本盯上时的防护",
          "带 Origin: evil.example.com / Host: evil.example.com 访问",
          "前两者 403，本地正常访问 200", b9)

    def b10():
        st, detail = api.get(f"/api/projects/{ids['趣AI味 去AI味工具']}")
        return expect(f"删除不存在的资源：项目 404={api.delete('/api/projects/nope')[0]}，"
                      f"任务 404={api.delete('/api/tasks/nope')[0]}，"
                      f"导出 404={api.get('/api/export/nope')[0]}",
                      st == 200 and api.delete("/api/projects/nope")[0] == 404)

    check("B10", "误操作：删不存在的项目/任务、导出不存在的项目",
          "对不存在的 id 发 DELETE / GET",
          "一律 404，不误删、不 500", b10)

    def b11():
        pid = api.post("/api/projects", {"name": "待删项目"})[1]["id"]
        tid = api.post(f"/api/projects/{pid}/tasks", {"title": "待删任务"})[1]["id"]
        st1 = api.delete(f"/api/projects/{pid}")[0]
        st2 = api.get(f"/api/projects/{pid}")[0]
        st3 = api.put(f"/api/tasks/{tid}", {"title": "改了"})[0]
        return expect(f"删项目 {st1}；再读 {st2}；改其任务 {st3}",
                      st1 == 200 and st2 == 404 and st3 == 404,
                      "级联：项目删除后其任务一并失效（符合预期）")

    check("B11", "级联：删了项目，它的任务是否还会被误改",
          "建项目+任务 → 删项目 → 再读写该任务",
          "任务随项目失效，返回 404", b11)

    def b12():
        with cf.ThreadPoolExecutor(max_workers=20) as ex:
            codes = list(ex.map(lambda i: api.post("/api/projects", {"name": f"并发-{i:02d}"})[0],
                                range(20)))
        _, data = api.get("/api/projects")
        names = [p["name"] for p in data["projects"]]
        got = sum(1 for i in range(20) if f"并发-{i:02d}" in names)
        return expect(f"20 个并发写请求全部返回 201={sum(1 for c in codes if c == 201)}；"
                      f"落盘 {got}/20",
                      sum(1 for c in codes if c == 201) == 20 and got == 20)

    check("B12", "并发：界面连点 + 定时任务同时写",
          "20 个并发创建请求",
          "全部成功且一条都不丢（历史事故：只落盘 3 条）", b12)

    def b13():
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            http_futs = [ex.submit(api.post, "/api/projects", {"name": f"混合A-{i}"}) for i in range(4)]
            mcp_futs = [ex.submit(mcp.tool, "kanban_create_project", {"name": f"混合B-{i}"}) for i in range(4)]
            [f.result() for f in http_futs + mcp_futs]
        _, data = api.get("/api/projects")
        names = {p["name"] for p in data["projects"]}
        missing = [f"混合{x}-{i}" for x in "AB" for i in range(4) if f"混合{x}-{i}" not in names]
        return expect(f"界面进程 4 条 + MCP 进程 4 条并发写入，缺失 {len(missing)} 条",
                      not missing, f"跨进程锁生效；当前项目总数 {len(names)}")

    check("B13", "跨进程：桌面应用与 MCP 服务同时写同一份数据",
          "HTTP 与 MCP 两个进程各并发 4 次写入",
          "8 条全部落盘，零丢失", b13)

    def b14():
        # 真实事故复现：数据文件被同步盘/杀软/断电弄坏
        bad_file = data_dir / "projects.json"
        good = bad_file.read_text(encoding="utf-8")
        before_count = len(api.get("/api/projects")[1]["projects"])
        bad_file.write_text(good[: len(good) // 2], encoding="utf-8")   # 截断
        st, data = api.get("/api/projects")
        bak = (data_dir / "projects.json.bak").exists()
        kept = len(data.get("projects", [])) if isinstance(data, dict) else 0
        flagged = data.get("data_recovered") is True
        from_lg = data.get("recovered_from") == "last-good"
        st2, again = api.get("/api/projects")
        cleared = isinstance(again, dict) and again.get("data_recovered") is False
        return expect(f"损坏前 {before_count} 个项目；损坏后请求 {st} 仍读到 {kept} 个，"
                      f"恢复来源={data.get('recovered_from') or '（重置为空）'}；"
                      f"损坏文件留档 .bak={bak}；提示上报={flagged}（第二次已清除={cleared}）",
                      st == 200 and kept == before_count and from_lg and bak and flagged and cleared,
                      "自动从「最近一次正常保存」救回，用户几乎无感；损坏文件另存 .bak 便于取证")

    check("B14", "数据文件损坏（同步盘/杀软/断电截断后的真实形态）",
          "把 projects.json 截断成一半后请求列表",
          "不崩溃、自动恢复到损坏前的项目数、损坏文件留档、并告知用户发生过什么", b14)

    def b19():
        # 手动快照 + 一键回滚：改坏了想退回去
        st1, snap = api.post("/api/backup")
        _, listing = api.get("/api/backups")
        folder = listing["backups"][0]["name"]
        before = {(p["name"], p["progress"]) for p in api.get("/api/projects")[1]["projects"]}

        risky = api.post("/api/projects", {"name": "临时加的实验项目", "status": "进行中"})[1]
        st2, rec = api.get("/api/recoverable")
        entry = next((r for r in rec["recoverable"] if r["source"] == folder), None)
        st3, rolled = api.post("/api/restore", {"source": folder})
        after = {(p["name"], p["progress"]) for p in api.get("/api/projects")[1]["projects"]}
        safety = (data_dir / "projects.json.before-restore.bak").exists()
        return expect(f"快照 {folder} 预览 {entry['project_count'] if entry else '?'} 个项目；"
                      f"加了实验项目后回滚 {st3} → {len(rolled.get('projects', [])) if isinstance(rolled, dict) else '?'}"
                      f" 个项目；回到快照状态={after == before}；恢复前自动留副本={safety}",
                      st1 == 200 and entry is not None and st3 == 200 and after == before and safety,
                      "回滚是覆盖语义，且恢复前先把当前数据另存为 before-restore.bak")

    check("B19", "改坏了想退回去：手动快照 + 一键恢复",
          "POST /api/backup → 加一个实验项目 → GET /api/recoverable → POST /api/restore",
          "可恢复列表能预览规模；恢复后完全回到快照状态；恢复前自动留安全副本", b19)

    def b15():
        pid = ids["番茄小说《规则怪谈·午夜日志》"]
        text, is_err = mcp.tool("kanban_update_project", {"project_id": pid, "status": "瞎写"})
        st2, detail = api.get(f"/api/projects/{pid}")
        return expect(f"MCP 传非法状态：isError={is_err}，提示首行「{text.splitlines()[0][:60]}」，"
                      f"项目当前状态仍为 {detail['status']}",
                      is_err and detail["status"] == "进行中")

    check("B15", "MCP 侧：模型编出非法枚举值时会不会写脏数据",
          "kanban_update_project(status='瞎写')",
          "工具报错，数据保持不变", b15)

    def b16():
        t1, e1 = mcp.tool("kanban_handoff", {"project_id": "不存在"})
        t2, e2 = mcp.tool("kanban_get_project", {})
        return expect(f"不存在的项目 isError={e1}；缺必填字段 isError={e2}",
                      e1 and e2)

    check("B16", "MCP 侧：代理幻觉出不存在的项目 / 漏参数",
          "handoff(不存在) / get_project(空参数)",
          "工具级错误提示，服务不崩、不影响后续调用", b16)

    def b17():
        st, body = api.post("/api/backup")
        st2, listing = api.get("/api/backups")
        return expect(f"备份 {st}，产物 {len(body.get('files', []))} 个；历史备份 "
                      f"{len(listing.get('backups', []))} 份",
                      st == 200 and st2 == 200 and listing.get("backups"))

    check("B17", "动手改数据结构前先备份（真实运维习惯）",
          "POST /api/backup 后查看备份列表",
          "备份成功且能在列表里看到", b17)

    def b18():
        cfg_st, cfg = api.get("/api/ai/config")
        eps = cfg["ai"]["endpoints"]
        masked = all("****" in k for k in eps[0]["api_keys"])
        put_st, _ = api.put("/api/ai/config", {"endpoints": eps, "active_model": "stub-model"})
        on_disk = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
        real_key = on_disk["ai"]["endpoints"][0]["api_keys"][0]
        return expect(f"接口返回的 Key 掩码={masked}；原样提交后磁盘上的 Key = {real_key[:6]}…",
                      cfg_st == 200 and put_st == 200 and masked
                      and real_key == "sk-scenario-00000001")

    check("B18", "密钥安全：界面只看到掩码，保存时不能把真 Key 写坏",
          "GET 配置（掩码）→ 原样 PUT 回去 → 查磁盘",
          "磁盘上的真 Key 原样保留", b18)


if __name__ == "__main__":
    raise SystemExit(main())
