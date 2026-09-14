# -*- coding: utf-8 -*-
"""应用版本。

单一事实来源：桌面壳启动日志、`/api/health`、PyInstaller 产物都从这里取值。
判断「dist 里的 exe 是否落后于源码」时看 BUILD_TIME —— AGENTS.md 记的那个坑
（改完源码忘了重打包）将来靠这个自动判，不用再比对时间戳。
"""
from __future__ import annotations

# 语义化版本：主版本.次版本.修订
__version__ = "0.9.0"

# 源码最后变更时间（每次发版时手填，用于发现"exe 比源码旧"）
BUILD_TIME = "2026-09-15 01:00"

APP_VERSION = __version__
APP_NAME_VERSION = f"AI 交接看板 v{__version__}"
