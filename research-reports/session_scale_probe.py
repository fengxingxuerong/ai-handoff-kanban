# -*- coding: utf-8 -*-
"""只读探针：量本机会话源规模。

AGENTS.md「下一步 5b」暂缓项⑦（会话历史全文检索）的前置条件是"先量会话文件规模"，
2026-10-06 实测结果（记录在 AGENTS.md 同一处）：本机默认三个源 + 自定义源共
3 个会话文件 / <0.1 MB / 有界遍历 2ms —— 引入 FTS5 索引层纯属负资产。

用法（只读，不改任何数据；导入 app.sessions 会读真实 config.json）：
    .venv\\Scripts\\python.exe research-reports\\session_scale_probe.py
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.sessions import _walk_bounded, session_sources  # noqa: E402
from app import llm  # noqa: E402


def main() -> None:
    cfg = llm.load_config()
    custom = [c.get("name") for c in (cfg.get("ai", {}).get("session_sources") or [])
              if isinstance(c, dict)]
    print("自定义会话源：", custom or "无（只有默认三个）")

    grand_files, grand_mb, grand_t = 0, 0.0, 0.0
    for src in session_sources():
        root, name = src.get("path"), src.get("name")
        t0 = time.perf_counter()
        files, total = [], 0
        for f in _walk_bounded(Path(root), budget=5000):
            files.append(f)
            try:
                total += f.stat().st_size
            except OSError:
                pass
        dt = time.perf_counter() - t0
        mb = total / 1e6
        print(f"{name:14s} 文件 {len(files):5d} 个 · {mb:9.1f} MB · 遍历 {dt * 1000:7.0f} ms"
              f" · 目录存在={os.path.isdir(root)}")
        grand_files += len(files)
        grand_mb += mb
        grand_t += dt
    print(f"{'合计':14s} 文件 {grand_files:5d} 个 · {grand_mb:9.1f} MB · 遍历 {grand_t * 1000:7.0f} ms")


if __name__ == "__main__":
    main()
