# -*- coding: utf-8 -*-
"""一条命令跑完全部门禁 —— CI 的本地等价物。

为什么需要它：**发版判据只能有一条命令**。所有"全绿"如果依赖某个人记得依次敲六条命令，
那就是门禁最弱的形态（本项目已经为此加过 `release_regression` / `delivery_drill` 这类脚本）。
`--quick` 会跳过四道脚本门禁，所以它**只算"快检"**，结论文案与退出码都不许冒充发版判据。
这个入口做三件事：

1. **自己起一个隔离实例**（临时数据目录 + 空闲端口），`live_check` 与 `live_ui_check`
   才有东西可打，且**绝不可能写到用户真实的 ~/.ai-kanban**；
2. 按顺序跑完并把**每一步的真实退出码**收进一张表 —— 中途失败不短路，
   这样一次运行就能看到全部问题，而不是修一个再撞下一个；
3. 无论成功失败都关掉自己起的进程、删掉自己的临时目录（不留孤儿监听）。

用法：
    python scripts/ci_local.py              # 常规全量（不含重新打包）
    python scripts/ci_local.py --quick      # 只跑 pytest + 三个自起实例的脚本门禁
    python scripts/ci_local.py --with-exe   # 额外重新打包 exe 并跑冻结版冒烟（慢，发版前用）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 选空闲端口收成一份实现（`app/service.py`）：此前这里与 delivery_drill / scenario_test
# 各写了一遍，而 release_regression 干脆写死端口 —— 后果见 `app.service.free_port` 的说明。
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))                       # 打包那两步要用 exe_build
from app.service import free_port                               # noqa: E402
from exe_build import build_exe_step                            # noqa: E402
# 门禁输出里有中文与 ✓：重定向到文件时 Python 会按系统码页（本机 cp936）编码，
# 打第一个特殊字符就崩 —— 门禁必须能被管道与重定向。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

PY = sys.executable
MODE = "full"
for a in sys.argv[1:]:
    if a == "--quick":
        MODE = "quick"
    elif a == "--with-exe":
        MODE = "exe"


def coverage_floor() -> str:
    """覆盖率门槛的唯一事实来源：`pytest.ini` 里的 `fail_under`。

    **为什么必须显式取出来传给 pytest-cov**：`[coverage:report] fail_under` 单独放着
    **不拦任何东西** —— 2026-10-23 实测：只跑 `tests/test_version.py`、TOTAL 16%，pytest
    仍然 exit 0、连一句 fail 都不说；加上 `--cov-fail-under=90` 才 exit 1
    （`Coverage failure: total of 16 is less than fail-under=90`）。
    也就是说门槛此前只是一行装饰品：删掉几百条用例也不会有人红，而门禁的这一步
    标签上却写着"含覆盖率门槛" —— **标签在撒谎**，这比没有门槛更糟。

    `pytest.ini` 里那段早就写着"不要指望这里的 `branch = true` 生效（pytest-cov 自己构造
    开关）"，`fail_under` 是同一个坑，只是当时没顺手验它。数字只写在 pytest.ini 一处，
    这里读出来用；读不到就**停**，不拿一个兜底数字继续跑（那等于又埋一个会漂的门槛）。
    """
    m = re.search(r"^fail_under\s*=\s*(\d+)", (ROOT / "pytest.ini").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("pytest.ini 里找不到 fail_under —— 覆盖率门槛没了单一事实来源，停")
    return m.group(1)


# 允许跳过的用例：**只有** `test_ai.py` 那几条活体用例（需 KANBAN_LIVE=1 才跑，本门禁用干净 env）。
# 别的文件出现跳过 = 某一层能力在这次环境里**没验**（没有 node / 没装 playwright /
# 三级浏览器都不可用 / 没有 git …），而"整份静默跳过"在报告里与全绿长得一模一样 ——
# 2026-10-25 就是这么发现 E2E 那 75+ 条在 CI 上从来没跑过的（缺第三级浏览器，退出码照样 0）。
# conftest 早就把跳过的名字印在结尾，但**没有任何东西读它**；这条审计就是那个读者。
ALLOWED_SKIPS = {"test_ai.py": 5}


def _parse_skips(output: str) -> dict:
    """从 conftest 的收尾点名块里读出"每个文件跳过了几条"。

    格式（tests/conftest.py:pytest_terminal_summary）::

        ============ 跳过 N 条（没验，不是通过） ============
          test_ai.py: 5 条
          整份跳过的文件意味着这条能力在本机从未被验证过；…

    没有跳过时 conftest 直接 return，于是这里回空字典 —— **空 = 0 条**，不是"解析失败"。
    这个区分很要紧：把"没跳过"当解析失败会让每次全绿都误报（假红的下场是有人把门禁删掉）。
    """
    out: dict = {}
    for ln in output.splitlines():
        m = re.match(r"^\s+([\w.\-]+\.py):\s+(\d+)\s+条\s*$", ln)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def _skip_audit(observed: dict, allowed: dict) -> tuple:
    """按文件对账跳过数，返回 `(是否通过, 说明)`。**两个方向都查**，缺一即红。

    **适用范围**：只在"跑全量套件"时有定义 —— 允许表里的 `test_ai.py: 5` 之所以应当出现，
    是因为那一步会收集到那个文件。拿子集（`-k` / 单个文件）去套它必然误报"0 条"。
    门禁里那一步就是全量（`pytest -q --cov-fail-under …`），所以这个前提成立；
    谁要改那一步的范围，就得连 `ALLOWED_SKIPS` 一起改 —— 报错文案里已经把这条写出来了。

    为什么两个方向都查：只查"多出来的"留了个 fail-open 口子 —— conftest 的格式一旦变了，
    解析回空字典，"多出来"就成了空集，审计静默通过。所以"期望有的却没有"也算不符。
    """
    problems = []
    for name in sorted(set(observed) | set(allowed)):
        got, want = observed.get(name, 0), allowed.get(name, 0)
        if got == want:
            continue
        if want == 0:
            problems.append(f"{name} {got} 条（不该跳过）")
        elif got == 0:
            problems.append(f"{name} 0 条（期望 {want} 条：那几条没被跳过 —— 开了 KANBAN_LIVE？"
                            f"被删了？还是这一步不再跑全量了？）")
        else:
            problems.append(f"{name} {got} 条（期望 {want} 条）")
    if not problems:
        return True, f"跳过 {sum(observed.values())} 条，与允许表一致"
    return False, ("跳过数对不上：" + "；".join(problems) +
                   "。跳过 = 那一层能力这次**没验**（没有 node / 没装 playwright / "
                   "三级浏览器都不可用 / 没有 git），而整份静默跳过跟全绿同形 —— "
                   "要么补齐环境，要么像 test_ai.py 那样显式列进 ALLOWED_SKIPS 并写明理由")[:400]


def run_capture(argv: list[str], env: dict, timeout: int = 900) -> tuple:
    """跑一步并**把原始输出也带回来**：`(是否通过, 耗时, 摘要行, 原始输出)`。

    为什么要原始输出：跳过审计（见 `_skip_audit`）要读 conftest 印在结尾的点名块。
    摘要行那 300 字是为"红了要能一眼看出是哪一步"设计的，不适合做判据。
    """
    t0 = time.time()
    try:
        r = subprocess.run(argv, cwd=str(ROOT), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        code = r.returncode
    except subprocess.TimeoutExpired:
        out, code = f"超时（>{timeout}s）", -1
    dt = time.time() - t0
    # 摘要要带现场，不能只留最后两行：门禁报"15 通过 / 1 失败"而不说失败的是哪一条，
    # 等于把一次红变成还要再跑一遍才能开始查（2026-09-23 的 live_ui_check 就是这样）。
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    tail = [ln for ln in lines if "[FAIL]" in ln or "✗" in ln][:3]
    note = " ⏎ ".join(tail + lines[-1:])[:300] if tail else \
        (" ⏎ ".join(lines[-2:])[:170] if lines else f"exit={code}")
    return code == 0, dt, note, out


def run(argv: list[str], env: dict, timeout: int = 900) -> tuple:
    """跑一步，返回 (是否通过, 耗时, 摘要行)。失败时摘要里带日志尾，别只说"红了"。"""
    ok, dt, note, _ = run_capture(argv, env, timeout)
    return ok, dt, note


SEED = {
    "projects": [{
        "id": "ciseed000001", "name": "CI 演练种子板", "status": "进行中", "progress": 40,
        "priority": "高", "tags": ["门禁自用"], "next_action": "看第二条任务的依赖",
        "background": "这块板子只给 live_check / live_ui_check 有内容可点，不承载任何真实数据。",
        "handoff_note": "这块板子不放任何凭据形态的字符串 —— 种子文件是被跟踪的，"
                        "写一个像 Key 的字面量等于把它复制到更多地方。",
        "repo_path": "D:/not/a/real/repo", "created_at": "2026-09-23 09:00",
        "updated_at": "2026-09-23 09:00",
        "tasks": [
            {"id": "ciseedt00002", "title": "被前置挡住的那条", "desc": "", "status": "待办",
             "priority": "中", "agent": "", "due_date": "2026-01-05",
             "refs": [], "journal": [], "depends_on": ["ciseedt00001"]},
            {"id": "ciseedt00001", "title": "先做这条", "desc": "", "status": "进行中",
             "priority": "高", "agent": "", "due_date": "", "refs": [], "journal": [],
             "depends_on": []},
        ]}],
}


def seed_live_dir(path: str) -> None:
    """给常驻实例播一块小板子。

    live_check 以前的两条断言写成 `not projects or all(...)`，而这里的实例是**空**的，
    于是"项目字段齐全"与"Key 已掩码"永远绿。空实例上"没内容可验"不能算通过，
    所以要么播种子、要么让门禁自己说 SKIP —— 这里两样都做：种子保证真跑到，
    SKIP 分支保证不再假绿。

    config.json 里那把"Key"是**运行时拼出来的合成串**，源码里不留完整的 `sk-…` 形态：
    这个文件是被跟踪的，写一个字面量假 Key 等于往仓库里多放一处像凭据的东西。
    """
    with open(os.path.join(path, "projects.json"), "w", encoding="utf-8") as fp:
        json.dump(SEED, fp, ensure_ascii=False, indent=2)
    fake_key = "sk-" + "CIseed" + "0" * 14 + "f"
    with open(os.path.join(path, "config.json"), "w", encoding="utf-8") as fp:
        json.dump({"ai": {"endpoints": [{
            "name": "门禁自用合成端点", "base_url": "http://127.0.0.1:9/v1",
            "model": "not-a-real-model", "enabled": False, "api_keys": [fake_key],
        }]}}, fp, ensure_ascii=False, indent=2)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="kanban-ci-")
    live_dir = tempfile.mkdtemp(prefix="kanban-ci-live-")
    seed_live_dir(live_dir)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    # 两套 env，**不能合成一套**。这里踩过两次同一类坑（都是门禁自己造的噪声）：
    #   ① 把 KANBAN_NO_SCHEDULER 漏给 pytest → `test_scheduler_start_is_idempotent` 假红；
    #   ② 把 KANBAN_PORT 漏给 pytest → `test_context_tool_returns_live_link` 假红
    #      （它断言直链里的默认端口 18320）。
    # 规则：**被测的那一步用干净 env，只有"要起服务/要连服务"的门禁才带端口**。
    # KANBAN_DATA_DIR 两边都带 —— 它是安全网：测试自己会 monkeypatch 到 tmp_path，
    # 但万一哪天漏了，宁可写进临时目录，也绝不能碰到用户真实的 ~/.ai-kanban。
    env = dict(os.environ, KANBAN_DATA_DIR=tmp,
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    # 活体用例（`KANBAN_LIVE=1`）**必须关着**：这一步是"干净 env"档，它既不该去烧真实配额，
    # 也不该让跳过数随调用者的环境漂 —— 跳过审计（`_skip_audit`）要求这个数确定是 5。
    # 这正是"包装层的 env 要分两套"那条纪律的又一例：运行开关漏进被测 env，会造出与代码无关的红。
    env.pop("KANBAN_LIVE", None)
    # 端口不能省：桌面版默认占 18320，而 Windows 上 SO_REUSEADDR 允许两个进程
    # 同时"绑定成功"—— 抢不到端口的那个会静默存在，门禁就可能打到用户的真实实例上。
    srv_env = dict(env, KANBAN_PORT=str(port))
    live_env = dict(srv_env, KANBAN_DATA_DIR=live_dir)   # 常驻实例用已播种的那块目录
    print(f"隔离数据目录：{tmp}\n常驻实例数据目录（已播种）：{live_dir}\n"
          f"实例端口：{port}（不会碰 ~/.ai-kanban）\n")

    server = None
    results: list[tuple[str, bool, float, str]] = []
    # `--quick` 跳过的那几步要记名字：否则它在结尾照样印"可以发版"，
    # 而"跳过四道门禁"与"四道门禁都过了"在屏幕上长得一模一样。
    skipped: list[str] = []

    def step(label, argv, timeout=900, for_server=False, live=False):
        e = live_env if live else (srv_env if for_server else env)
        ok, dt, note = run(argv, e, timeout)
        results.append((label, ok, dt, note))
        print(f"  [{'OK' if ok else 'FAIL'}] {label:34s} {dt:6.1f}s  {note}", flush=True)

    try:
        # --basetemp 不是多余的：pytest 收尾会去删系统 temp 里历次会话的目录，
        # 本机（杀软/受管终端）那一步会抛 PermissionError 把整条 CI 掐在收尾阶段。
        # 指到自己的临时目录里，跑完整包连带一起删，不留跨会话残留。
        #
        # `--cov-fail-under` 必须**显式传**（见 coverage_floor 的说明）：pytest.ini 里的
        # `fail_under` 自己拦不住退出码，不传它，这一步的"含覆盖率门槛"就是一句空话。
        # 预检排在 pytest 之前：它 2 秒就能说出"pytest 要红 80 秒 + 探针要空跑 8 分钟
        # 才能绕出来"的事（2026-10-24 实测的残渣事故），两种模式的成本都可以忽略。
        step("探针预检（残渣/哑火）", [PY, "scripts/mutation_probe.py", "--preflight"], 120)
        # pytest 那一步不走 step()：它多一道**跳过审计**（见 `_skip_audit`）——
        # 报告里的"跳过"与"全绿"长得一样，而跳过意味着那一层能力这次没验。
        # 只读退出码的话，"整份文件被跳过"永远是绿的（E2E 那 75+ 条就这么在 CI 上消失了）。
        ok, dt, note, out = run_capture(
            [PY, "-m", "pytest", "-q", "--cov-fail-under", coverage_floor(),
             "--basetemp", os.path.join(tmp, "basetemp")], env, 1800)
        if ok:                                   # pytest 自己就红了的话，它的输出才是该看的现场
            aok, anote = _skip_audit(_parse_skips(out), ALLOWED_SKIPS)
            note, ok = (f"{note} ⏎ {anote}"[:300], aok)
            if not aok:
                note = anote                     # 红时只留可行动的那句，别被覆盖率数字挤掉
        results.append(("pytest 全量（含覆盖率门槛+跳过审计）", ok, dt, note))
        print(f"  [{'OK' if ok else 'FAIL'}] {'pytest 全量（含覆盖率门槛+跳过审计）':34s} "
              f"{dt:6.1f}s  {note}", flush=True)
        if MODE != "quick":
            # 覆盖率只证明"这行被执行过"，不证明"这行错了会被发现"。
            # 变异探针是唯一能量化后者的门禁：**改了语义还全绿 = 盲区**，直接判失败。
            # 放在场景测试之前：它会临时改 app/ 源码（跑完自己还原），
            # 不能和任何"起真实实例"的步骤并行。
            step("变异探针 mutation_probe", [PY, "scripts/mutation_probe.py"], 1800)
            step("场景测试 scenario_test", [PY, "scripts/scenario_test.py"], 900, True)
            step("发版回归 release_regression",
                 [PY, "scripts/release_regression.py", "--no-ai"], 900, True)
            step("交付演练 delivery_drill", [PY, "scripts/delivery_drill.py"], 900, True)
        else:
            skipped += ["变异探针 mutation_probe", "场景测试 scenario_test",
                        "发版回归 release_regression", "交付演练 delivery_drill"]

        # 起一个实例给 live_* 两条门禁打（它们的设计就是"对着在跑的实例只读体检"）。
        # 用的是**单独一块已播种的数据目录**：空实例会让 live_check 的字段断言变成
        # "没内容可验"，而"没内容可验"从来不算通过。
        srv_log = open(os.path.join(tmp, "server.log"), "wb")
        server = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                   "--port", str(port), "--log-level", "warning"],
                                  cwd=str(ROOT), env=live_env, stdout=srv_log, stderr=srv_log)
        for _ in range(60):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            results.append(("起隔离实例", False, 0.0, "60 次探测都没连上"))
            print("  [FAIL] 起隔离实例：端口没起来，live_* 两条只能跳过", flush=True)

        if server.poll() is None:
            # 端口通了不等于起对了：Windows 上 SO_REUSEADDR 能让两个进程都"绑定成功"。
            # 唯一可信的自证是问它自己读的是哪份数据 —— 必须是本脚本刚播的那块临时目录。
            who = _health_data_file(base)
            if who and os.path.normcase(who).startswith(os.path.normcase(live_dir)):
                print(f"  [OK] 实例自证数据目录：{who}", flush=True)
            else:
                results.append(("实例自证数据目录", False, 0.0,
                                f"它读的是 {who}，不是 {live_dir} → 立即停，绝不往下跑"))
                print(f"  [FAIL] 实例读的是 {who}，不是本次的临时目录 → 停", flush=True)
                _kill_tree(server.pid)
                server = None

        if server is not None and server.poll() is None:
            step("只读体检 live_check --mcp", [PY, "scripts/live_check.py", base, "--mcp"],
                 600, True, live=True)
            step("真实浏览器 live_ui_check", [PY, "scripts/live_ui_check.py", base], 600, True,
                 live=True)

        if MODE == "exe":
            step("生成构建戳", [PY, "scripts/make_stamp.py"], 120)
            step("生成 exe 版本资源", [PY, "scripts/make_versionfile.py"], 120)
            # 两个产物的打包都走 exe_build.build_exe_step：它有「清场 → 被占就让位 → 打 →
            # 失败再清一遍让一遍再打」的一套。**2026-10-31 实测的真事故**：`dist\AI交接看板-MCP.exe`
            # 被用户自己的 AI 代理宿主当成 MCP 服务在跑，清场杀掉它 → 宿主一秒内又拉起 →
            # 文件仍被握着 → PyInstaller 写它报 WinError 5。杀不掉的不是残留，是有人在正常用它；
            # 详见 scripts/exe_build.py 的模块说明（为什么不改用户配置、为什么改名能行）。
            def _run_here(argv, **kw):
                return subprocess.run(argv, cwd=str(ROOT), env=env, **kw)

            ok, dt, note = build_exe_step(
                [PY, "-m", "PyInstaller", "-F", "-w", "-n", "AI交接看板",
                 "scripts/desktop.py", "--hidden-import", "app.main",
                 "--hidden-import", "uvicorn",
                 "--add-data", str(ROOT / "web") + os.pathsep + "web",
                 "--paths", str(ROOT), "--icon", str(ROOT / "app.ico"),
                 "--version-file", str(ROOT / "build" / "version_info.txt"),
                 "--distpath", str(ROOT / "dist"),
                 "--workpath", str(ROOT / "build"),
                 "--specpath", str(ROOT / "build"), "--noconfirm"],
                ROOT / "dist" / "AI交接看板.exe", runner=_run_here)
            results.append(("打包 PyInstaller", ok, dt, note))
            print(f"  [{'OK' if ok else 'FAIL'}] {'打包 PyInstaller':34s} {dt:6.1f}s  {note}", flush=True)
            step("冻结版冒烟 frozen_smoke", [PY, "scripts/frozen_smoke.py"], 900, True)
            # 第二个产物：MCP 服务，**故意不加 -w** —— 它走 stdio，而 GUI 子系统程序拿不到标准流。
            # 只装 exe 的用户没有 .venv，"让代理直接读写看板"这个卖点此前对他不可用（2026-10-23 评测）。
            ok, dt, note = build_exe_step(
                [PY, "-m", "PyInstaller", "-F", "-n", "AI交接看板-MCP",
                 "scripts/mcp_server.py", "--hidden-import", "app.main",
                 "--paths", str(ROOT),
                 "--version-file", str(ROOT / "build" / "version_info.txt"),
                 "--distpath", str(ROOT / "dist"),
                 "--workpath", str(ROOT / "build"),
                 "--specpath", str(ROOT / "build"), "--noconfirm"],
                ROOT / "dist" / "AI交接看板-MCP.exe", runner=_run_here)
            results.append(("打包 PyInstaller（MCP 控制台产物）", ok, dt, note))
            print(f"  [{'OK' if ok else 'FAIL'}] {'打包 PyInstaller（MCP 控制台产物）':34s} "
                  f"{dt:6.1f}s  {note}", flush=True)
            step("MCP 冻结版自查", [PY, "scripts/mcp_frozen_smoke.py"], 600, True)
    finally:
        if server is not None and server.poll() is None:
            _kill_tree(server.pid)
        time.sleep(0.5)
        # 杀完必须**验证**端口真的释放：taskkill 偶发静默漏杀（2026-10-24 实测
        # 残留过一个 python.exe 占着本轮端口到下轮才发现）。补一刀；还占着就大声说。
        if server is not None and _port_listening(port):
            _kill_tree(server.pid)
            time.sleep(1.0)
            if _port_listening(port):
                print(f"警告：隔离实例端口 {port} 仍未释放，请排查占用进程", flush=True)
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(live_dir, ignore_errors=True)

    bad = [r for r in results if not r[1]]
    print("\n" + "=" * 78)
    print(f"门禁：{len(results)} 步    通过 {len(results) - len(bad)}    失败 {len(bad)}")
    for label, _, _, note in bad:
        print(f"  ✗ {label} —— {note}")
    if bad:
        print("结论：有步骤失败，别发版")
    elif skipped:
        # 跳过的东西必须在结论里说破：`--quick` 与全量在屏幕上长得一样，
        # 而它少跑的恰好是"改了语义会不会红"那一层。
        print(f"结论：已跑的 {len(results)} 步全过，但这是**快检**，跳过了 {len(skipped)} 道门禁：")
        for label in skipped:
            print(f"  ▸ {label}")
        print("      → 不能当发版判据；发版请跑 `python scripts/ci_local.py`（带 --with-exe 更全）")
    else:
        print("结论：全部通过，可以发版 ✓")
    return 1 if bad else 0


def _health_data_file(base: str) -> str:
    """问实例自己：你读的是哪份数据文件？拿不到就回空串（调用方按"没自证成"处理）。"""
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=10) as r:
            return str(json.loads(r.read().decode("utf-8", "replace")).get("data_file", ""))
    except Exception:                                          # noqa: BLE001
        return ""


def _port_listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_tree(pid: int) -> None:
    """按进程树杀。Windows 上 `Popen.kill()` 只杀壳、留下 uvicorn 子进程占着端口。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        else:
            subprocess.run(["pkill", "-TERM", "-P", str(pid)], capture_output=True, timeout=30)
    except Exception:                                          # noqa: BLE001
        pass


if __name__ == "__main__":
    raise SystemExit(main())
