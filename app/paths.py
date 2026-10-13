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


# ---- 数据文件的三个"救命路径"（2026-10-14 从 main 下沉到这里，实现逐字未改） ----
# 下沉理由与 data_dir 同：它们是**存储布局**的一部分，而 main 的读/写/恢复、备份白名单
# 都要用同一份定义。以前它们散在 main 里，文档说的"所有存储模块的共同根在 paths.py"
# 对这三个路径并不成立。

LAST_GOOD_SUFFIX = ".json.last-good"


def last_good_file() -> Path:
    """最近一次正常保存的数据副本。

    损坏时把当前文件复制成 .bak 是没用的 —— 那时的文件**本身就是坏的**。
    真正能救命的是一份"上次写成功时的样子"。
    """
    return data_file().with_suffix(LAST_GOOD_SUFFIX)


def before_restore_file() -> Path:
    """每次恢复**之前**把当时的数据另存到这里 —— 恢复错了的那条退路。

    单独一个函数是因为这个路径以前只在写入处硬编码过一次，于是"可恢复来源"的白名单
    根本不认识它：界面向用户承诺"覆盖前会自动另存"，实际没有任何入口能把它拿回来。
    只有一份（下一次恢复会覆盖），所以对外要说清是"最近一次恢复前"。
    """
    return data_dir() / "projects.json.before-restore.bak"
