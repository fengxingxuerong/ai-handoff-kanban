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

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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

        # 2. 首页与前端资源
        with opener.open(BASE + "/", timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
        check("首页可访问且含看板标记", "AI 交接看板" in html)
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
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
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
