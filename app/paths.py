# -*- coding: utf-8 -*-
"""数据目录定位。

**从 `app/main.py` 整体搬来（2026-10-06 大文件继续拆分），内容逐字未改。**
`app/main.py` 用 `from .paths import data_dir, data_file` 保住既有名字 ——
测试里的 `from app.main import data_dir` / `main.data_file()` 仍然有效。

为什么独立成模块：数据目录是**所有存储模块的共同根**（projects.json、memories.json、
reports/、backups/、usage.jsonl），它自己不依赖任何业务模块 —— 放在 main 里会让
后续拆出的模块（如 aireports）只能反向 import main。环境变量在**每次调用时**读
（autouse 夹具靠这个把用例隔离进临时目录），这里保持同一口径。
"""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    env = os.environ.get("KANBAN_DATA_DIR")
    if env:
        d = Path(env)
    else:
        # Windows 下优先 USERPROFILE，避免受 HOME 环境变量干扰
        home = os.environ.get("USERPROFILE") or str(Path.home())
        d = Path(home) / ".ai-kanban"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_file() -> Path:
    return data_dir() / "projects.json"
