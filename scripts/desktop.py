# -*- coding: utf-8 -*-
"""
AI 交接看板 - 桌面壳（PyWebview）
把本地 FastAPI 服务 + 单页前端封装成"双击即用"的原生桌面窗口。

打包命令（在项目根目录执行，注意 --add-data 必须用绝对路径，参见 build.bat）：
    pyinstaller -F -w -n "AI交接看板" scripts/desktop.py ^
        --hidden-import app.main --hidden-import uvicorn ^
        --add-data "%~dp0web;web" --paths "%~dp0"

排查说明：打包成 -w（无控制台）后，任何启动异常都会静默消失。
因此这里把启动全过程写进 <数据目录>\\startup.log，出问题先看这个文件。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

PORT = int(os.environ.get("KANBAN_PORT", "18320"))
HOST = "127.0.0.1"
WINDOW_TITLE = "AI 交接看板"

try:                                        # 冻结版里 app 是 hidden-import，取不到不能让启动挂掉
    from app.version import APP_VERSION, BUILD_TIME
except Exception:
    APP_VERSION, BUILD_TIME = "?", "?"


def data_dir() -> Path:
    env = os.environ.get("KANBAN_DATA_DIR")
    d = Path(env) if env else Path(os.environ.get("USERPROFILE") or Path.home()) / ".ai-kanban"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = Path(os.environ.get("TEMP") or ".") 
    return d


LOG_FILE = data_dir() / "startup.log"


def log(msg: str) -> None:
    """写启动日志；失败也不能影响主流程"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fp:
            fp.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, port)) == 0


def ensure_standard_streams() -> None:
    """PyInstaller -w（无控制台）打包后 sys.stdout / sys.stderr 是 None。

    而 uvicorn 在 configure_logging() 里会调用 sys.stdout.isatty()，
    于是 AttributeError → ValueError: Unable to configure formatter 'default'，
    整个后端线程直接死掉（现象就是：双击 exe 窗口空白/闪退，日志里啥也没有）。
    这里补上 devnull 兜底，保证任何依赖标准流的库都能正常工作。
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass


def wait_ready(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port):
            return True
        time.sleep(0.2)
    return False


def start_server() -> None:
    ensure_standard_streams()
    try:
        import uvicorn

        log(f"后端线程启动：uvicorn {getattr(uvicorn, '__version__', '?')} on {HOST}:{PORT}")
        uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning", access_log=False)
        log("后端线程退出（uvicorn.run 正常返回）")
    except BaseException:
        log("后端线程异常，改用 log_config=None 重试：\n" + traceback.format_exc())
        # 兜底：完全不配置日志，绕开任何与标准流相关的初始化
        try:
            import uvicorn

            uvicorn.run("app.main:app", host=HOST, port=PORT, log_config=None, access_log=False)
            log("后端线程退出（降级模式）")
        except BaseException:
            log("后端线程二次异常：\n" + traceback.format_exc())


def main() -> None:
    ensure_standard_streams()
    log("=" * 60)
    log(f"版本：v{APP_VERSION} build {BUILD_TIME}")
    log(f"启动：frozen={getattr(sys, 'frozen', False)} meipass={getattr(sys, '_MEIPASS', '')}")
    log(f"PORT={PORT} HOST={HOST} data_dir={data_dir()}")

    if port_in_use(PORT):
        log("端口已被占用，直接复用已有实例")
    else:
        t = threading.Thread(target=start_server, daemon=True, name="kanban-backend")
        t.start()
        if not wait_ready(PORT):
            log("后端 20 秒内未就绪：socket 未监听")
            raise SystemExit("后端服务启动超时，详情见 " + str(LOG_FILE))

    # 端口通了不代表能响应（uvicorn 可能只完成了 bind），再打一次真实请求确认
    if not http_alive():
        log("端口已监听但 HTTP 无响应：服务线程可能已崩溃")
        raise SystemExit("后端无响应，详情见 " + str(LOG_FILE))

    # 无窗口模式：只服务不开窗。给 CI 与服务器场景用 ——
    # （-w 打包的 exe 在无桌面会话的机器上开窗会失败，连带把整个进程带走）
    if os.environ.get("KANBAN_NO_WINDOW") == "1":
        log("KANBAN_NO_WINDOW=1：无窗口模式，仅提供 HTTP 服务（Ctrl+C 退出）")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            log("收到中断，退出")
        return

    log("后端就绪，打开窗口")
    import webview

    webview.create_window(
        WINDOW_TITLE,
        f"http://{HOST}:{PORT}",
        width=1280,
        height=820,
        min_size=(960, 640),
        background_color="#0e1320",
    )
    try:
        webview.start(gui="edgechromium")
    except Exception:
        log("窗口启动异常：\n" + traceback.format_exc())
        raise


def http_alive(timeout: float = 6.0) -> bool:
    """真实请求 /api/health，确认不是"只 bind 不响应"的半死状态"""
    import json
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with opener.open(f"http://{HOST}:{PORT}/api/health", timeout=2) as r:
                return bool(json.loads(r.read().decode("utf-8")).get("ok"))
        except Exception:
            time.sleep(0.3)
    return False


if __name__ == "__main__":
    main()
