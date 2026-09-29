# -*- coding: utf-8 -*-
"""一条命令跑完全部门禁 —— CI 的本地等价物。

为什么需要它：这个仓库**没有远端，所以 CI 从未执行过一次**。所有"全绿"都依赖某个人
记得依次敲六条命令 —— 而"依赖人记得"正是门禁最弱的形态（本项目已经为此加过
`release_regression` / `delivery_drill` 这类脚本）。这个入口做三件事：

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
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(argv: list[str], env: dict, timeout: int = 900) -> tuple:
    """跑一步，返回 (是否通过, 耗时, 摘要行)。失败时摘要里带日志尾，别只说"红了"。"""
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
    return code == 0, dt, note


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
    # 端口不能省：桌面版默认占 18320，而 Windows 上 SO_REUSEADDR 允许两个进程
    # 同时"绑定成功"—— 抢不到端口的那个会静默存在，门禁就可能打到用户的真实实例上。
    srv_env = dict(env, KANBAN_PORT=str(port))
    live_env = dict(srv_env, KANBAN_DATA_DIR=live_dir)   # 常驻实例用已播种的那块目录
    print(f"隔离数据目录：{tmp}\n常驻实例数据目录（已播种）：{live_dir}\n"
          f"实例端口：{port}（不会碰 ~/.ai-kanban）\n")

    server = None
    results: list[tuple[str, bool, float, str]] = []

    def step(label, argv, timeout=900, for_server=False, live=False):
        e = live_env if live else (srv_env if for_server else env)
        ok, dt, note = run(argv, e, timeout)
        results.append((label, ok, dt, note))
        print(f"  [{'OK' if ok else 'FAIL'}] {label:34s} {dt:6.1f}s  {note}", flush=True)

    try:
        # --basetemp 不是多余的：pytest 收尾会去删系统 temp 里历次会话的目录，
        # 本机（杀软/受管终端）那一步会抛 PermissionError 把整条 CI 掐在收尾阶段。
        # 指到自己的临时目录里，跑完整包连带一起删，不留跨会话残留。
        step("pytest 全量（含覆盖率门槛）",
             [PY, "-m", "pytest", "-q", "--basetemp", os.path.join(tmp, "basetemp")], 1800)
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
            step("打包 PyInstaller", [PY, "-m", "PyInstaller", "-F", "-w", "-n", "AI交接看板",
                                      "scripts/desktop.py", "--hidden-import", "app.main",
                                      "--hidden-import", "uvicorn",
                                      "--add-data", str(ROOT / "web") + os.pathsep + "web",
                                      "--paths", str(ROOT), "--icon", str(ROOT / "app.ico"),
                                      "--version-file", str(ROOT / "build" / "version_info.txt"),
                                      "--distpath", str(ROOT / "dist"),
                                      "--workpath", str(ROOT / "build"),
                                      "--specpath", str(ROOT / "build"), "--noconfirm"], 1500, True)
            step("冻结版冒烟 frozen_smoke", [PY, "scripts/frozen_smoke.py"], 900, True)
    finally:
        if server is not None and server.poll() is None:
            _kill_tree(server.pid)
        time.sleep(0.5)
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(live_dir, ignore_errors=True)

    bad = [r for r in results if not r[1]]
    print("\n" + "=" * 78)
    print(f"门禁：{len(results)} 步    通过 {len(results) - len(bad)}    失败 {len(bad)}")
    for label, _, _, note in bad:
        print(f"  ✗ {label} —— {note}")
    print("结论：" + ("全部通过，可以发版 ✓" if not bad else "有步骤失败，别发版"))
    return 1 if bad else 0


def _health_data_file(base: str) -> str:
    """问实例自己：你读的是哪份数据文件？拿不到就回空串（调用方按"没自证成"处理）。"""
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=10) as r:
            return str(json.loads(r.read().decode("utf-8", "replace")).get("data_file", ""))
    except Exception:                                          # noqa: BLE001
        return ""


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
