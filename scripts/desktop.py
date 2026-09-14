# -*- coding: utf-8 -*-
"""
AI 交接看板 - 桌面壳（PyWebview）
把本地 FastAPI 服务 + 单页前端封装成"双击即用"的原生桌面窗口。

PyInstaller 打包命令（在项目根目录执行）：
    pyinstaller -F -w -n "AI交接看板" scripts/desktop.py ^
        --hidden-import app.main --hidden-import uvicorn ^
        --add-data "web;web" --paths .
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

PORT = int(os.environ.get("KANBAN_PORT", "18320"))
HOST = "127.0.0.1"
WINDOW_TITLE = "AI 交接看板"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, port)) == 0


def wait_ready(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port):
            return True
        time.sleep(0.2)
    return False


def start_server() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning", access_log=False)


def main() -> None:
    if not port_in_use(PORT):
        t = threading.Thread(target=start_server, daemon=True)
        t.start()
        if not wait_ready(PORT):
            raise SystemExit("后端服务启动超时，请检查依赖是否完整")

    import webview

    window = webview.create_window(
        WINDOW_TITLE,
        f"http://{HOST}:{PORT}",
        width=1280,
        height=820,
        min_size=(960, 640),
        background_color="#0e1320",
    )
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
