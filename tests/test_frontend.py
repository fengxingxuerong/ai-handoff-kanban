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


def test_version_is_displayed():
    """版本号必须在界面上看得见——否则判断 exe 是否落后于源码又得靠比对时间戳"""
    assert 'id="app-version"' in HTML
    assert "async function loadVersion()" in HTML
    assert "/api/health" in HTML


def test_due_date_ui():
    """截止日期：弹窗输入、任务卡徽章、仪表盘统计、AI brief 都要接上"""
    assert 'id="t-due"' in HTML, "任务弹窗缺截止日期输入"
    assert "function dueInfo" in HTML, "缺日期状态计算"
    assert "due-overdue" in HTML and "due-soon" in HTML, "缺逾期/临期徽章样式"
    assert "overdue_tasks" in HTML and "duesoon_tasks" in HTML, "仪表盘缺临期/逾期统计"


def test_kanban_drag_and_drop():
    """四列看板拖拽改状态"""
    assert "el.draggable = true" in HTML, "任务卡应可拖拽"
    assert 'addEventListener("drop"' in HTML, "看板缺 drop 处理"
    assert "moveTaskTo" in HTML, "缺拖拽落点处理函数"


def test_data_import_export_ui():
    """导入导出按钮存在且注册进 ACTIONS"""
    assert 'data-act="exportData"' in HTML
    assert 'data-act="importData"' in HTML
    assert "exportData" in ACTIONS_KEYS
    assert "importData" in ACTIONS_KEYS
    assert 'id="import-file"' in HTML, "缺导入文件选择器"


GLOBALS = {
    "parseInt", "parseFloat", "Number", "String", "Boolean", "Array", "Object", "JSON", "Date",
    "Math", "Set", "Map", "Promise", "fetch", "confirm", "alert", "prompt", "isNaN", "setTimeout",
    "clearTimeout", "setInterval", "clearInterval", "encodeURIComponent", "decodeURIComponent",
    "if", "for", "while", "switch", "catch", "return", "typeof", "await", "new", "function",
}


def test_every_action_target_is_actually_defined():
    """**注册了 ≠ 存在**：ACTIONS 里调用的函数必须在页面里真的有定义。

    历史事故：`exportData` / `importData` 两个按钮注册进了 ACTIONS，
    但这两个函数在 index.html 里从未定义过 —— 点下去只有一条 ReferenceError，
    用户看到的是"按钮点了没反应"。而原来的契约测试只查注册、不查存在，全程绿灯。
    """
    calls = set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", ACTIONS_BLOCK.group(1)))
    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", HTML))
    defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", HTML))
    missing = sorted(c for c in calls if c not in defined and c not in GLOBALS)
    assert not missing, f"ACTIONS 里调用了不存在的函数（点了只有 JS 报错）：{missing}"


def test_global_search_ui():
    """全局任务搜索：顶栏入口 + 弹窗 + 防抖 + Ctrl+K"""
    assert 'data-act="openGlobalSearch"' in HTML
    assert "openGlobalSearch" in ACTIONS_KEYS
    assert 'id="gs-input"' in HTML
    assert "runGlobalSearch" in HTML
    assert 'addEventListener("input"' in HTML
    assert "ctrlKey" in HTML, "应有 Ctrl+K 唤起"


def test_theme_toggle_ui():
    """深浅色主题：按钮 + light 变量集 + localStorage 记忆"""
    assert 'data-act="toggleTheme"' in HTML
    assert "toggleTheme" in ACTIONS_KEYS
    assert 'data-theme="light"' in HTML, "应定义 light 主题变量"
    assert "kanban-theme" in HTML, "主题偏好应存 localStorage"


def test_task_sort_control_exists():
    """任务只能靠创建顺序翻找是真实痛点：必须能按优先级/截止/最近更新重排"""
    assert 'id="task-sort"' in HTML
    assert "function sortTasks(" in HTML
    assert "bindTaskSort" in HTML, "切换排序要用 addEventListener，不能写内联 onchange"
