# -*- coding: utf-8 -*-
"""兼容入口 —— 实现已搬进 `app/mcp_server.py`（2026-10-25，为了能被 uvx/pip 装）。

**为什么还留着这个文件**：`build.bat` / `Dockerfile` / `ci_local` / `mcp_frozen_smoke` /
`release_regression` / `live_check` / 测试与文档里都写着 `scripts/mcp_server.py` 这条路径。
改十几处调用点换不来任何功能，而"路径还在、行为不变"是零风险的。

**为什么用 `sys.modules` 别名，而不是 `from app.mcp_server import *`**：
后者会造出**第二个模块对象** —— 于是 `monkeypatch.setattr(mcp_server, ...)` 改的是壳，
真正在跑的那份 `app.mcp_server` 的全局一点没变，测试绿了却没测到东西。
别名让两条导入路径拿到**同一个对象**（`import mcp_server` / `import scripts.mcp_server` /
`from app import mcp_server` 全等）。

直接运行（`python scripts/mcp_server.py`）走下面的 `serve()`，与
`python -m app.mcp_server` 完全等价。
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:              # 源码态：直接跑脚本时包不在 path 上
    sys.path.insert(0, str(_HERE.parent))

from app import mcp_server as _impl                # noqa: E402

if __name__ == "__main__":
    _impl.serve()
else:
    sys.modules[__name__] = _impl                  # 透明别名：两条路是同一個模块对象
