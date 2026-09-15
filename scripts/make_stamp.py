# -*- coding: utf-8 -*-
"""打包前生成构建戳 `app/_build_stamp.py`。

为什么需要它：打包后源码被解包到临时目录，文件 mtime 全是解包时间，取不到"代码最后改于何时"。
所以在**打包之前**把当时的时间写成一个模块，跟着包一起分发；`app/version.py` 在 frozen 态读它。

`build.bat` 与 CI 都会自动调用。手动打包时别忘了这一步（忘记的后果是版本条显示"未知"，
而不是显示一个错的时间 —— 这是刻意设计的）。

用法：
    python scripts/make_stamp.py            # 写入当前时间
    python scripts/make_stamp.py --print    # 只打印不写
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "_build_stamp.py"
TEMPLATE = '''# -*- coding: utf-8 -*-
"""构建戳：由 scripts/make_stamp.py 生成，**不要手改，也不要提交**（见 .gitignore）。

打包流程在 PyInstaller 之前写它，`app/version.py` 在 frozen 态读它，
用来回答"这个 exe 是什么时候打出来的"。
"""

STAMP = "{stamp}"
'''


def main() -> int:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if "--print" in sys.argv:
        print(stamp)
        return 0
    TARGET.write_text(TEMPLATE.format(stamp=stamp), encoding="utf-8")
    try:
        shown = TARGET.relative_to(ROOT)
    except ValueError:                     # 目标被指到项目外时别把脚本搞崩
        shown = TARGET
    print(f"构建戳已写入 {shown}：{stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
