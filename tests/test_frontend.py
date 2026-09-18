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


def test_actions_table_has_no_duplicate_keys():
    """对象字面量里的同名键是静默覆盖的：注册两遍只算一遍，且不会有任何报错。
    （2026-09-18 实测 `applySummarize` 就重复了两行。）"""
    keys = re.findall(r"^\s{2}([A-Za-z_$][\w$]*):", ACTIONS_BLOCK.group(1), re.M)
    dup = sorted({k for k in keys if keys.count(k) > 1})
    assert not dup, f"ACTIONS 有重复键，后一份静默顶掉前一份：{dup}"


def test_status_and_priority_never_reach_class_names_unsanitized():
    """状态/优先级会被拼进 class 名（st-进行中 / pri-高），必须走白名单
    （priCls / taskStCls）或已过白名单的局部变量，否则等于给属性注入开门。"""
    offenders = re.findall(r'class="[^"]*\$\{\s*(?:t|item|task)\.(?:priority|status)\b', HTML)
    assert not offenders, f"未过白名单就把任务字段拼进 class 名：{offenders}"
    assert "function priCls(" in HTML and "function taskStCls(" in HTML, "白名单函数被删了"


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


# ---------------- 样式层技术债：布局算错、窄屏无断点、写死色值 ----------------

def test_view_heights_subtract_the_reminder_banner():
    """两个视图的高度都是 calc(100vh - 定值)，而提醒横幅站在正常流里；
    不把它扣掉，横幅一出现页面就多出第二条滚动条、看板底部被裁。"""
    assert "--banner-h: 0px;" in HTML, "没有横幅高度的 CSS 变量"
    for sel in ("#view-overview", "#view-detail"):
        rule = re.search(re.escape(sel) + r"\s*\{[^}]*height: calc\((?:[^()]|\([^()]*\))*\)", HTML)
        assert rule, f"没找到 {sel} 的高度规则"
        assert "--banner-h" in rule.group(0), f"{sel} 的高度没扣横幅：{rule.group(0)}"
    assert "function setBanner(" in HTML, "横幅高度得由 JS 量出来写回变量"


def test_narrow_viewport_collapses_to_one_column():
    block = HTML[HTML.index("@media (max-width: 700px)"):]
    assert ".kanban { grid-template-columns: 1fr" in block[:300], "窄屏没把四列看板收成单列"
    assert ".row3 { grid-template-columns: 1fr" in block[:300], "窄屏没把三列表单收成单列"


def test_theme_colors_come_from_variables_not_hardcoded_values():
    """写死的色值在另一套主题下会直接看不见：趋势图数据点的描边曾是深色的 #0e1320，
    浅色主题下等于给浅点描了浅边；失败提示的 #e08080 也不跟随 --red。"""
    assert "#e08080" not in HTML, "还有写死的红色，改主题时会不一致"
    assert 'stroke="#0e1320"' not in HTML, "SVG 描边还写死成深色背景"
    assert "style=\"stroke:var(--bg)\"" in HTML, "描边应走 --bg（随主题变）"


def test_keyboard_focus_ring_is_defined():
    """卡片/条目都是 div+click，键盘用户至少要看得见焦点在哪"""
    assert ":focus-visible" in HTML
    for sel in (".btn", ".pcard", ".tcard", ".gs-item", ".sess-item"):
        assert re.search(r"[^{}]*" + re.escape(sel) + r":focus-visible", HTML), f"{sel} 缺焦点环"


# ---------------- 关闭路径与键盘可达性 ----------------

def test_all_modal_close_paths_share_one_implementation():
    """✕、「完成」、点遮罩、Esc 以前是四套写法各漏一半：遮罩与 Esc 直接摘 .show，
    跳过了 closeModal 的动画复位（于是弹窗只有第一次打开有 pop），也不清待确认操作。"""
    assert "closeOneModal(m.id)" in HTML, "点遮罩没走统一关闭"
    assert "closeOneModal(arg)" in HTML, "✕/完成 按钮没走统一关闭"
    assert re.search(r'e\.key === "Escape"\) \{ closeTopModal\(\)', HTML), "Esc 没走统一关闭"
    assert '.modal-mask.show").forEach' not in HTML, "还留着「一次关全部」的旧写法"
    # 直接摘 show 的地方只该剩：toast、closeModal 本体、报告面板与它的遮罩
    assert len(re.findall(r'classList\.remove\("show"\)', HTML)) == 4, \
        "又多了一处绕过统一关闭的写法"
    assert 'box.style.animation = ""' in HTML, "重开弹窗要把动画复位回来"


def test_clickable_cards_are_keyboard_reachable():
    """卡片都是 div，鼠标能用键盘不能用；至少要能 Tab 到、Enter/空格能触发"""
    assert 'tabindex="0" role="button" data-keyact="1"' in HTML
    assert 'el.dataset.keyact = "1"' in HTML, "任务卡是 createElement 出来的，要单独加"
    assert re.search(r'e\.key !== "Enter" && e\.key !== " ".*?el\.click\(\)', HTML, re.S), \
        "缺 Enter/空格 激活"


def test_scan_button_uses_finally_for_its_loading_state():
    """扫描结果为空时函数是直接 return 的，收尾必须放在 finally 里，
    否则按钮永久停在转圈态（本机有真实会话，走不到空分支，所以只能静态钉）"""
    body = HTML[HTML.index("async function scanSessions()"):]
    body = body[:body.index("\n}\n")]
    assert "finally {" in body and 'btn.classList.remove("ai-loading-btn")' in body


def test_toast_is_announced_to_screen_readers():
    assert 'id="toast" role="status" aria-live="polite"' in HTML


def test_no_browser_prompt_dialogs_left():
    """prompt() 是不能编辑、不校验、也套不上主题的录入方式，全站已经清零"""
    assert not re.search(r"=\s*prompt\(|await\s*prompt\(", HTML), "又有人用 prompt() 收输入"


def test_toast_gives_long_messages_time_to_be_read():
    assert re.search(r"Math\.min\(7000, 2200 \+", HTML), "toast 还是固定 2.2 秒"
    assert "max-width: min(760px" in HTML and "word-break" in HTML, "长路径会把 toast 撑出屏幕"


def test_detail_view_has_loading_and_error_state():
    assert 'id="detail-state"' in HTML and "function setDetailState(" in HTML
    assert 'data-act="retryDetail"' in HTML, "失败态要能原地重试"
