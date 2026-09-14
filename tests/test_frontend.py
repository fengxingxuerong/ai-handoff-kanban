# -*- coding: utf-8 -*-
"""前端契约测试（静态解析 web/index.html，不需要浏览器）

守住的底线：
- 页面里不再有内联事件（拼串注入与撇号语法错误的根源）
- 每个 data-act 都能在 ACTIONS 表里找到对应实现（防止按钮点了没反应）
- 关键转义与白名单辅助仍然存在
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

HTML = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")

ACTIONS_BLOCK = re.search(r"const ACTIONS = \{(.*?)\n\};", HTML, re.S)
ACTIONS_KEYS = set(re.findall(r"^\s{2}([A-Za-z_$][\w$]*):", ACTIONS_BLOCK.group(1), re.M)) if ACTIONS_BLOCK else set()
DATA_ACTS = set(re.findall(r'data-act="([^"]+)"', HTML))


def test_no_inline_handlers():
    """页面里不该有任何内联事件属性（onclick / oninput / onchange ...）"""
    inline = re.findall(r'\son(?:click|input|change|submit|keyup|keydown|keypress|blur|focus|load|error)=\s*"', HTML)
    assert not inline, f"仍有内联事件属性：{inline}"
    assert not re.search(r'onclick="[^"]*\$\{', HTML), "仍有插值进内联事件"


def test_search_input_is_debounced():
    """搜索框改为防抖监听（卡片全量重渲染，逐字符触发会卡）"""
    assert 'id="search"' in HTML
    assert 'searchInput.addEventListener("input"' in HTML
    assert "setTimeout(renderOverview" in HTML


def test_actions_table_exists():
    assert ACTIONS_BLOCK, "未找到 ACTIONS 表"
    assert len(ACTIONS_KEYS) >= 30, f"ACTIONS 条目偏少：{len(ACTIONS_KEYS)}"


def test_every_data_act_is_registered():
    """卡片里的 edit/del 由 bindCardEvents 单独处理，其余必须注册"""
    card_only = {"edit", "del"}
    missing = {a for a in DATA_ACTS if a not in ACTIONS_KEYS and a not in card_only}
    assert not missing, f"这些 data-act 没有实现，点了会没反应：{sorted(missing)}"


def test_delegation_listeners_present():
    assert 'document.addEventListener("click"' in HTML
    assert 'document.addEventListener("keydown"' in HTML
    assert "bindCardEvents" in HTML


def test_escaping_and_whitelist_helpers_present():
    assert re.search(r"function esc\(", HTML)
    assert "STATUS_SET" in HTML and "PRIORITY_SET" in HTML
    # 卡片渲染必须对名称与状态做转义
    card = re.search(r"function cardHTML\(p\) \{(.*?)\n\}", HTML, re.S).group(1)
    assert "esc(p.name)" in card
    assert "esc(st)" in card and "esc(pri)" in card


def test_card_uses_data_attributes_not_inline_js():
    card = re.search(r"function cardHTML\(p\) \{(.*?)\n\}", HTML, re.S).group(1)
    assert 'data-pid="' in card
    assert "onclick" not in card
