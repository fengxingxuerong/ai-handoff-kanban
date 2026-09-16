# -*- coding: utf-8 -*-
"""应用版本与构建时间。

单一事实来源：桌面壳启动日志、`/api/health`（界面上的版本条也从这里读）。

`BUILD_TIME` 的口径分两种情况，**都不再手填**：

- 源码运行：取 `app/` + `web/` + `scripts/desktop.py` 里最新的文件修改时间 —— 它就是
  "这份代码最后一次改是什么时候"，和真实情况永远一致。
- 打包运行（frozen）：源码被 PyInstaller 解包到临时目录，那里所有文件的 mtime 都是解包时间，
  取不到源码时间。所以由打包流程事先写入 `app/_build_stamp.py`（见 `scripts/make_stamp.py`，
  `build.bat` 与 CI 都会调），这里只负责读。

历史上这里是手填常量（`"2026-09-15 12:00"`），于是启动日志报的时间和 exe 真实构建时间差了一整天 —— 
"靠它判断 exe 是否落后于源码"也就完全落空。手填的时间戳一定会漂，所以改成算出来的。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# 语义化版本：主版本.次版本.修订
__version__ = "1.1.0"

ROOT = Path(__file__).resolve().parent.parent

# 打包时由 scripts/make_stamp.py 生成；源码运行时这个模块不存在，属正常情况
try:
    from app._build_stamp import STAMP as _FROZEN_STAMP
except Exception:                                              # noqa: BLE001
    _FROZEN_STAMP = ""

_SOURCE_WATCH = ("app", "web", "scripts/desktop.py")
_SUFFIXES = {".py", ".html", ".js", ".css", ".bat"}
# 生成物不算"源码最后改动"：构建戳是打包时写出来的，
# 让它参与比较会把源码时间顶成构建时间，进而掩盖"exe 落后于源码"的信号
_GENERATED = {"_build_stamp.py"}


def _watched_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for rel in _SOURCE_WATCH:
        p = root / rel
        if p.is_file():
            if p.name not in _GENERATED:
                files.append(p)
        elif p.is_dir():
            files.extend(f for f in p.rglob("*")
                         if f.is_file() and f.suffix in _SUFFIXES and f.name not in _GENERATED)
    return files


def newest_source_mtime(root: Path = ROOT) -> float:
    """源码最后一次变更的时间戳（秒）；取不到任何文件时返回 0"""
    times = [f.stat().st_mtime for f in _watched_files(root)]
    return max(times) if times else 0.0


def source_build_time(root: Path = ROOT) -> str:
    ts = newest_source_mtime(root)
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "未知"


def resolve_build_time(*, frozen: bool, stamp: str, root: Path = ROOT) -> str:
    """打包版用构建期写死的戳，源码版实时算。

    打包版没有戳（老版本 exe、或漏跑 make_stamp）时，如实返回"未知"而不是编一个时间出来。
    """
    if frozen:
        return stamp or "未知"
    return source_build_time(root)


BUILD_TIME = resolve_build_time(frozen=bool(getattr(sys, "frozen", False)), stamp=_FROZEN_STAMP)

APP_VERSION = __version__
APP_NAME_VERSION = f"AI 交接看板 v{__version__}"
