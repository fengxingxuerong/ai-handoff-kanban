# -*- coding: utf-8 -*-
"""生成 PyInstaller 的 --version-file 内容（build/version_info.txt）。

为什么要有这个：以前打出来的 exe 右键"属性 → 详细信息"是空的，于是判断
"这个 exe 到底是不是当前源码打的"只能靠看文件修改时间 —— 而这件事在这个项目里
已经真出过事（启动日志报的时间和 exe 真实构建时间差一整天）。把版本与构建戳
写进 PE 资源，任何人在任何机器上双击前就能核对。

版本号的唯一来源仍是 `app/version.py`（与 /api/health、界面版本条同一处），
这里只搬运、不另写一份 —— 两处各写一遍必然漂。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.version import APP_VERSION, BUILD_TIME            # noqa: E402

# 版本资源里要写的是**构建时间**，不是"源码最后修改时间"。
# `version.BUILD_TIME` 在源码态算的是后者（这是对的：界面上"这份代码最后一次改是什么时候"
# 就该跟着源码走），而打包流程刚由 make_stamp.py 写下的 STAMP 才是"这个 exe 是几点打的"。
# 之前这里直接用 BUILD_TIME，结果同一次构建里出现两个时间：
# 构建戳 17:30、exe 版本资源 15:40 —— 右键属性与 /api/health 报的不是同一件事，
# 正是这个项目立誓要防的形状。所以优先读戳，读不到才退回源码时间并如实标注。
try:
    from app._build_stamp import STAMP as _STAMP               # noqa: E401
except Exception:                                          # noqa: BLE001
    _STAMP = ""
BUILD_STAMP = _STAMP or (BUILD_TIME + "（源码时间，未写构建戳）")

OUT = ROOT / "build" / "version_info.txt"
PRODUCT = "AI 交接看板"
FILE_DESC = "AI 交接看板 - 多项目任务中枢，切换智能体无缝交付"


def _tuple4(v: str) -> str:
    parts = []
    for chunk in (v.split(".") + ["0", "0", "0"])[:4]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return "(" + ", ".join(str(p) for p in parts) + ")"


def build() -> str:
    return (
        "# -*- coding: utf-8 -*-\n"
        "# 由 scripts/make_versionfile.py 从 app/version.py 生成，别手改。\n"
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={_tuple4(APP_VERSION)}, prodvers={_tuple4(APP_VERSION)},\n"
        "                    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,\n"
        "                    date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('080404b0', [\n"
        f"        StringStruct('CompanyName', ''),\n"
        f"        StringStruct('FileDescription', '{FILE_DESC}'),\n"
        f"        StringStruct('FileVersion', '{APP_VERSION}'),\n"
        "        StringStruct('InternalName', 'ai-kanban'),\n"
        f"        StringStruct('LegalCopyright', ''),\n"
        f"        StringStruct('OriginalFilename', 'AI交接看板.exe'),\n"
        f"        StringStruct('ProductName', '{PRODUCT}'),\n"
        f"        StringStruct('ProductVersion', '{APP_VERSION}'),\n"
        f"        StringStruct('PrivateBuild', '{BUILD_STAMP}'),\n"
        f"        StringStruct('SpecialBuild', '{BUILD_STAMP}'),\n"
        "      ])]),\n"
        "    VarFileInfo([VarStruct('Translation', [2052, 1200])])\n"
        "  ]\n"
        ")\n"
    )


def main() -> int:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"写入 {OUT.relative_to(ROOT)}：v{APP_VERSION} · 构建 {BUILD_STAMP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
