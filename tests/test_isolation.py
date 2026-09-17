# -*- coding: utf-8 -*-
"""测试不得写用户真实数据目录

背景（2026-09-17 实测复现）：`tests/test_llm_gateway.py` 直接调 `llm.chat()`，
其中的 `_record_usage()` 按 `KANBAN_DATA_DIR` 解析落地位置 —— 那些用例没设这个变量，
于是每次跑测试都往 `%USERPROFILE%\\.ai-kanban\\usage.jsonl` 追加假记录
（endpoint「甲」/model「stub-model」），用户点开"用量"看到的是被测试掺过的数字。

夹具级隔离（conftest 的 autouse `_isolate_data_dir`）是修复；这里守住它别被改回去。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app import llm

REAL_DIR = (Path(os.environ.get("USERPROFILE") or Path.home()) / ".ai-kanban").resolve()


def _real_usage() -> Path:
    return REAL_DIR / "usage.jsonl"


def test_autouse_isolation_points_away_from_real_dir():
    """夹具把数据目录指到了临时位置（不是真实目录）"""
    assert llm.config_file().parent.resolve() != REAL_DIR


def test_record_usage_lands_in_isolated_dir():
    """写用量时落的文件在隔离目录里，且真实目录不被触碰"""
    real = _real_usage()
    before = real.read_text(encoding="utf-8").count("\n") if real.exists() else 0

    llm._record_usage("测试用途", "甲", "stub-model", {"prompt_tokens": 3}, 0.01, True)

    f = llm._usage_file()
    assert f.exists(), "用量没写下来（隔离目录应该可写）"
    assert f.parent.resolve() != REAL_DIR, f"用量写到了真实目录：{f}"
    rec = json.loads(f.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["feature"] == "测试用途" and rec["ok"] is True

    after = real.read_text(encoding="utf-8").count("\n") if real.exists() else 0
    assert after == before, f"真实用量账本被测试改动了（{before} → {after} 行）"


def test_mcp_and_main_resolve_the_same_isolated_dir():
    """llm 与 main 必须从同一处解析数据目录，否则隔离会有漏洞"""
    from app.main import data_dir

    assert data_dir().resolve() == llm.config_file().parent.resolve()
