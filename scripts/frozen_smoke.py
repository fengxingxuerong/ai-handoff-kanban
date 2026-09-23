# -*- coding: utf-8 -*-
"""
冻结版自查：启动 dist\\AI交接看板.exe，用真实 HTTP 打一遍关键接口，最后自动关掉。

为什么需要它：PyInstaller -w 打包后没有控制台，后端如果启动失败（例如
标准流为 None 导致 uvicorn 日志初始化报错），现象只是"双击没反应/窗口空白"，
光看源码完全看不出来。这个脚本能在 30 秒内给出结论。

用法：
    python scripts/frozen_smoke.py                    # 默认 dist\\AI交接看板.exe
    python scripts/frozen_smoke.py 路径\\到\\x.exe 18395
退出码 0 表示通过。
"""
from __future__ import annotations

import hashlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 输出里有 ✓ 和中文：重定向到文件时 Python 用系统 ANSI 码页（本机 cp936），
# 打第一个 ✓ 就 UnicodeEncodeError 崩掉 —— 门禁脚本必须能被重定向与管道。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001  被替换掉的 stdout 可能没有 reconfigure
    pass

EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "AI交接看板.exe"
PORT = sys.argv[2] if len(sys.argv) > 2 else "18395"
BASE = f"http://127.0.0.1:{PORT}"
TIMEOUT = 45

# 显式不走代理：本机 Clash 之类的代理会把 127.0.0.1 的请求也拦掉，造成"服务超时"的假象
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

passed, failed = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (passed if cond else failed).append(name)
    print(("  [OK] " if cond else "  [FAIL] ") + name + ("" if cond else "  " + extra))


def main() -> int:
    if not EXE.exists():
        print(f"找不到 {EXE}，先运行 build.bat")
        return 1

    data_dir = Path(tempfile.gettempdir()) / "kanban_frozen_smoke"
    if data_dir.exists():
        for f in data_dir.iterdir():
            f.unlink()
    data_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["KANBAN_PORT"] = PORT
    env["KANBAN_DATA_DIR"] = str(data_dir)

    print(f"启动 {EXE.name}（端口 {PORT}，数据目录 {data_dir}）")
    proc = subprocess.Popen([str(EXE)], env=env)

    try:
        # 1. 等服务就绪（真实 HTTP，不只是端口通）
        deadline = time.time() + TIMEOUT
        health = None
        last = ""
        while time.time() < deadline:
            try:
                with opener.open(BASE + "/api/health", timeout=3) as r:
                    health = json.loads(r.read().decode("utf-8"))
                    break
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(1.0)
        check("后端 HTTP 就绪", health is not None, last)
        if health is None:
            return report(data_dir)

        # 1b. 版本戳这条链必须真的一致：exe 自报的版本与构建时间要和仓库里那两份对得上。
        # 以前这八项里没有一条看 build_time，"exe 是哪次构建的"只能靠人比 mtime ——
        # 2026-09-20 就漂过一次（版本资源写的是源码 mtime，戳却是另一个时间点）。
        def _read(p: Path) -> str:
            try:
                return p.read_text(encoding="utf-8")
            except Exception:                                      # noqa: BLE001
                return ""
        stamp = re.search(r'STAMP = "([^"]*)"', _read(ROOT / "app" / "_build_stamp.py"))
        ver = re.search(r'^__version__ = "([^"]*)"', _read(ROOT / "app" / "version.py"), re.M)
        check("exe 自报版本与构建戳同源",
              bool(stamp) and bool(ver)
              and health.get("build_time") == stamp.group(1)
              and health.get("version") == ver.group(1),
              f"exe={health.get('version')}/{health.get('build_time')} "
              f"仓库={ver and ver.group(1)}/{stamp and stamp.group(1)}")

        # 2. 首页与前端资源
        with opener.open(BASE + "/", timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
        check("首页可访问且含看板标记", "AI 交接看板" in html)
        # 产物里嵌的前端必须就是仓库里这一份：改完前端不重打包，测试全绿而用户看到的
        # 还是旧界面 —— 这条比对是"重打 exe"这件事本身的门禁（不一致就得重新打包）。
        src_html = ROOT / "web" / "index.html"
        want = hashlib.sha256(src_html.read_bytes()).hexdigest() if src_html.exists() else ""
        got = hashlib.sha256(html.encode("utf-8")).hexdigest()
        check("exe 内嵌的前端就是仓库里这一份（不一致说明该重打包）",
              bool(want) and got == want,
              f"sha256 产物={got[:12]} 仓库={want[:12]}")
        check("内嵌前端为事件委托版本", "const ACTIONS = {" in html and 'data-act="' in html)
        check("内嵌前端无内联事件", "onclick=" not in html and "onkeydown=" not in html)

        # 3. 写读删闭环
        body = json.dumps({"name": "冻结版冒烟", "status": "进行中", "progress": 20, "priority": "高"},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(BASE + "/api/projects", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=10) as r:
            created = json.loads(r.read().decode("utf-8"))
        check("创建项目", created.get("name") == "冻结版冒烟")

        with opener.open(BASE + "/api/projects", timeout=10) as r:
            total = len(json.loads(r.read().decode("utf-8"))["projects"])
        check("项目列表可读", total >= 1, f"total={total}")

        dr = urllib.request.Request(BASE + f"/api/projects/{created['id']}", method="DELETE")
        with opener.open(dr, timeout=10) as r:
            check("删除项目", json.loads(r.read().decode("utf-8")).get("ok") is True)

        # 4. 安全防护在冻结版里同样生效
        try:
            rq = urllib.request.Request(BASE + "/api/health", headers={"Origin": "http://evil.example.com"})
            opener.open(rq, timeout=5)
            check("跨站 Origin 被拒绝", False, "竟然放行了")
        except urllib.error.HTTPError as e:
            check("跨站 Origin 被拒绝", e.code == 403, f"code={e.code}")

        return report(data_dir)
    finally:
        # PyInstaller -F 单文件模式：主进程会拉起子进程跑实际服务，
        # terminate 只能杀父进程，子进程会残留（占用端口与 exe 文件）。
        # 必须用 taskkill /T 杀整棵进程树；杀完 socket 释放有延迟，需等待后复查。
        import socket
        import time as _time

        def port_listening(port: int) -> bool:
            s = socket.socket()
            s.settimeout(0.5)
            r = s.connect_ex(("127.0.0.1", port)) == 0
            s.close()
            return r

        try:
            port = int(BASE.rsplit(":", 1)[1])
        except Exception:
            port = 18395
        for _ in range(3):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            _time.sleep(1.2)  # 等 socket 释放
            if not port_listening(port):
                break
        if port_listening(port):
            print("警告：端口仍在监听，进程树未清理干净")
        else:
            print("已关闭应用")


def report(data_dir: Path) -> int:
    print(f"\n结果：{len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + " | ".join(failed))
        log = data_dir / "startup.log"
        if log.exists():
            print("\n--- startup.log ---")
            print(log.read_text(encoding="utf-8", errors="replace"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
