# -*- coding: utf-8 -*-
"""前端契约测试（静态解析 web/index.html，不需要浏览器）

守住的底线：
- 页面里不再有内联事件（拼串注入与撇号语法错误的根源）
- 每个 data-act 都能在 ACTIONS 表里找到对应实现（防止按钮点了没反应）
- 关键转义与白名单辅助仍然存在
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

# 2026-09-28 前端拆成 index.html + style.css + 多份 js-*.js；2026-10-18 按关注点重新分成 10 份。
# 契约测的是"整个前端"而不是某一个文件，所以读拼接后的源码（顺序与浏览器加载一致）。
from frontend_src import WEB, frontend_source  # noqa: E402

HTML = frontend_source()

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
    # 门槛钉在实测值上（2026-09-23 实测 60 个动作）。原来写 >=30，
    # 砍掉一半处理器它照样绿，等于没门。要减少动作面就得连带改这里，这正是想要的摩擦。
    assert len(ACTIONS_KEYS) >= 60, f"ACTIONS 条目偏少：{len(ACTIONS_KEYS)}"


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


def test_command_palette_ui_and_targets_are_defined():
    """Ctrl+K 命令面板：容器 + 15 条命令 + 每条 run 引用的函数必须真有定义。

    与 test_every_action_target_is_actually_defined 同一价值观：注册了 ≠ 存在。
    命令面板的按钮走 data-act="paletteRun" 统一下发（ACTIONS 注册那条已被
    test_every_action_registered 覆盖），这里单独钉命令本体 —— 少了任何一条 run
    引用的函数，点命令就是 ReferenceError，而全部搜索测试照样绿。
    """
    assert 'id="palette-commands"' in HTML, "命令面板容器应在搜索弹窗内"
    PALETTE_BLOCK = re.search(r"const PALETTE_CMDS = \[(.*?)\n\];", HTML, re.S)
    assert PALETTE_BLOCK, "未找到 PALETTE_CMDS"
    assert len(re.findall(r"id: \"", PALETTE_BLOCK.group(1))) >= 15, "命令面板命令数偏少"
    # run 里调用的函数必须存在。配合 GLOBALS（const/let/var/函数声明之外的全局名）
    calls = set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", PALETTE_BLOCK.group(1)))
    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", HTML))
    defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", HTML))
    missing = sorted(c for c in calls if c not in defined and c not in GLOBALS)
    assert not missing, f"PALETTE_CMDS 的 run 调用了不存在的函数：{missing}"
    # need:"detail" 的命令在没有 currentPid 时应禁用（paletteEnabled 判据必须存在）
    assert "paletteEnabled" in defined, "paletteEnabled 判据函数缺失"
    assert "paletteRun" in defined, "paletteRun 分发函数缺失"


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


def _css_rule(selector: str) -> str:
    """从整个前端里切出**某条 CSS 规则的规则体**（按选择器定位，取到配对的 `}`）。

    为什么需要它（2026-10-25 实测）：`HTML` 是整个前端拼起来的字符串，同一个串往往在
    **注释里也出现一遍** —— 于是"串在不在"这种判据会变成**在守注释，不在守规则**。
    实测案例：`web/style.css` 里 `max-width: min(760px` 出现两次，一次是真规则、
    一次是文件头注释里那句「硬约束（门禁钉死，勿动）…」；把 #toast 的规则改成 9000px，
    原断言照样通过 —— 是探针 `js_toast_can_overflow_the_screen` 存活才把它揪出来的。
    """
    at = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{", HTML)
    assert at, f"找不到 {selector} 这条规则"
    end = HTML.index("\n}", at.end())
    return HTML[at.start():end]


def test_toast_gives_long_messages_time_to_be_read():
    assert re.search(r"Math\.min\(7000, 2200 \+", HTML), "toast 还是固定 2.2 秒"
    # 只看 **#toast 这条规则**：注释里也提过同一串，按整份文本找等于在守注释
    rule = _css_rule("#toast")
    assert "max-width: min(760px" in rule, f"toast 的宽度上限没了（长路径会撑出屏幕）：{rule}"
    assert "word-break" in rule, f"toast 缺 word-break（长路径不换行）：{rule}"


def test_detail_view_has_loading_and_error_state():
    assert 'id="detail-state"' in HTML and "function setDetailState(" in HTML
    assert 'data-act="retryDetail"' in HTML, "失败态要能原地重试"


# ---------------- 弹窗语义、焦点与滚动 ----------------

def test_every_modal_is_a_labeled_dialog():
    """15 个弹窗都要能被读屏认出来；有标题 id 的用 aria-labelledby，没有的用 aria-label
    （2026-10-06 新增第 14 个：inbox-modal 待拍板；2026-10-19 新增第 15 个：blocked-modal
    —— 拖到「阻塞」时问一句原因）"""
    masks = re.findall(r'<div class="modal-mask" id="([^"]+)">\s*<div class="modal"[^>]*>', HTML)
    assert len(masks) == 15, f"弹窗数量变了（{len(masks)}），确认是否有新弹窗漏加语义"
    for mid in masks:
        block = HTML[HTML.index(f'<div class="modal-mask" id="{mid}">'):]
        tag = block.split("\n", 2)[1] if 'id="%s">' % mid in block else ""
        modal_tag = re.search(r'<div class="modal"[^>]*>', block).group(0)
        assert 'role="dialog"' in modal_tag and 'aria-modal="true"' in modal_tag, mid
        assert "aria-labelledby=" in modal_tag or re.search(r'aria-label="[^"]+"', modal_tag), mid


def test_blocked_reason_limit_is_one_number_everywhere():
    """阻塞原因的上限只有一个数（`MAX_BLOCKED_REASON`）：界面两处 maxlength（任务弹窗 +
    拖动弹窗）必须与后端同数 —— 两处各写一遍必然漂（对账钉子，模式同验收标准提示数字）。"""
    import app.main as kanban
    n = kanban.MAX_BLOCKED_REASON
    assert HTML.count(f'maxlength="{n}"') >= 2, "任务弹窗与阻塞原因弹窗的 maxlength 与后端不同数"


def test_modal_head_and_foot_are_sticky():
    """长弹窗（AI 设置、交接包）里标题和按钮条会滚出视野，两头必须钉住且有实色背景"""
    for sel, edge in ((".mhead", "top: 0"), (".mfoot", "bottom: 0")):
        rule = re.search(r"\.modal " + re.escape(sel) + r" \{[^}]*\}", HTML).group(0)
        assert "position: sticky" in rule and edge in rule, f"{sel} 没有钉住"
        assert "background: var(--bg-soft)" in rule, f"{sel} 透明背景会让滚过的内容透出来"


def test_modal_body_is_the_scroll_container():
    """2026-10-18：滚动容器从遮罩挪进 .mbody —— 遮罩上滚时 sticky 页脚会被钉在
    遮罩「内容盒底」（视口底部再往上 50px 的遮罩内边距），正文从按钮条下面透出来，
    长弹窗的观感就是页脚浮在半空。弹窗整框定高 + 内容在框内滚才治本。"""
    modal = re.search(r"\.modal \{[^}]*\}", HTML).group(0)
    assert "max-height: calc(100vh - 100px)" in modal, "弹窗没框定高，长内容会整页滚"
    mbody = re.search(r"\.modal \.mbody \{[^}]*\}", HTML).group(0)
    assert "overflow-y: auto" in mbody and "min-height: 0" in mbody, ".mbody 不是滚动容器"


def test_focus_is_trapped_and_returned():
    assert "function trapTab(" in HTML and "trapTab(e)" in HTML
    assert "function focusInto(" in HTML and "backToOpener()" in HTML, "缺焦点进出管理"
    assert "modalScroll" in HTML, "缺弹窗滚动位置记忆"


def test_action_results_are_announced_outside_the_modal():
    """执行结果只写在聊天气泡里 = 关掉弹窗就没人知道成没成，读屏也收不到"""
    body = HTML[HTML.index("async function executeActions"):]
    body = body[:body.index("\n}\n")]
    assert "cmdAddMsg(" in body and "toast(" in body, "结果没有同时播报到 aria-live 区域"


def _js_array(name: str) -> list[str]:
    """取 `const NAME = ["a", "b", ...]` 里的字符串项（跨行也要能拼起来）"""
    m = re.search(r"const " + name + r" = \[(.*?)\]", HTML, re.S)
    assert m, f"页面里找不到 const {name}"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_ai_command_prompt_fields_match_frontend_whitelist():
    """提示词里告诉模型的"能改哪些字段"＝前端真放行的白名单，一个字都不能差。

    两边分开写必然漂：模型以为能改的字段被前端拒掉，用户看到的就是
    "AI 说改了但看板上没变"；反过来前端放行而提示词没写的字段，等于给
    注入内容留了一条模型不会主动走、但走一次就生效的通道。
    """
    import app.main as kanban

    assert _js_array("AI_PROJECT_FIELDS") == kanban.AI_EDITABLE_PROJECT_FIELDS
    assert _js_array("AI_TASK_FIELDS") == kanban.AI_EDITABLE_TASK_FIELDS
    assert "field ∈ " + ", ".join(kanban.AI_EDITABLE_PROJECT_FIELDS) in kanban._CMD_TYPES
    assert "field ∈ " + ", ".join(kanban.AI_EDITABLE_TASK_FIELDS) in kanban._CMD_TYPES


def test_repo_path_is_never_ai_editable():
    """repo_path 决定"去哪个本地目录跑 git"，改了它等于让外部内容远程探测本机。

    所以它既不能出现在 AI 白名单里，也不能出现在给模型的指令声明里。
    """
    import app.main as kanban

    joined = " ".join(kanban.AI_EDITABLE_PROJECT_FIELDS + kanban.AI_EDITABLE_TASK_FIELDS)
    assert "repo_path" not in joined and "repo_path" not in kanban._CMD_TYPES
    assert "repo_path" not in " ".join(_js_array("AI_PROJECT_FIELDS"))


def test_save_extension_whitelist_matches_the_backend():
    """前端挡扩展名、后端也挡 —— 两份清单必须一字不差。

    以前前端少一条 `.markdown`：后端明明允许，用户却被告知"路径需以 .md / .txt 结尾"，
    而那句提示同时也漏了 `.agentsmd`。这是"两条路给不同答案"最小的一例。
    """
    import app.main as kanban
    m = re.search(r"if \(!/\\.\(([^)]+)\)\$/i\.test\(path\)\)", HTML)
    assert m, "找不到前端那条扩展名白名单正则（改法了？连同这条测试一起改）"
    fe = {("." + e.lower()) for e in m.group(1).split("|")}
    assert fe == set(kanban.EXPORT_SUFFIXES), (fe, kanban.EXPORT_SUFFIXES)
    for suffix in kanban.EXPORT_SUFFIXES:
        assert suffix in HTML, f"给用户的提示里没提到 {suffix}"


def test_inline_script_is_syntactically_valid_js(tmp_path):
    """整页只有一段内联脚本。它一崩，所有 `data-act` 全部失效、界面变成一块死面板，
    而**任何 Python 测试都不会红**。

    2026-09-20 实测发生过：在 JS 里写了 Python 风格的相邻字符串拼接
    （`"甲" "乙"`），pytest 500 多条全绿，直到真开浏览器才现形为
    `ReferenceError: openDetail is not defined`。`node --check` 一句话就能挡住。
    """
    import shutil
    import subprocess

    from frontend_src import web_dir

    # 2026-09-28 起 JS 不再是内联块，而是 web/js-*.js 若干文件 ——
    # 逐文件 `node --check`（比原来的"整段内联脚本"更细：能指出是哪个文件）。
    files = sorted(web_dir().glob("js-*.js"))
    assert files, "web/ 下没有 JS 文件了（拆分回归？）"
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node")
    for f in files:
        r = subprocess.run([node, "--check", str(f)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"{f.name} 语法错误：\n{(r.stderr or r.stdout)[:600]}"


def test_project_edit_form_is_filled_from_the_single_project_endpoint():
    """总览列表接口**不含** `background` / `handoff_note`，而 `PUT` 是整体覆盖语义。
    所以从列表项回填编辑表单 = 一保存就把用户手写的这两段清空（2026-09-20 实测复现）。

    这条是静态钉子：真正的行为验证要开浏览器，但"别再从列表回填"这个约束
    用一眼能看懂的正则守住最划算。
    """
    m = re.search(r"async function openProjectModal\(id\)(.*?)\n  openModal\(\"proj-modal\"\)",
                  HTML, re.S)
    assert m, "openProjectModal 不再是 async，或函数结构变了（连同这条测试一起看）"
    body = m.group(1)
    assert 'api("/api/projects/" + id)' in body, "编辑表单没有从单项目接口取权威数据回填"
    assert "projects.find" not in body, "又改用总览列表回填了 —— 那会静默吞掉背景与交接说明"
    assert "阻止打开编辑" in body, "取不到详情时必须拒绝打开这个表单，而不是开一个保存即吞内容的表单"


def test_actions_that_receive_data_arg_actually_forward_it():
    """分发器是 `fn(el.dataset.arg, ev, el)` —— 处理器若写成 `() => X()`，
    `data-arg` 就被静默丢掉：按钮看着"有反应"，执行的却是另一件事。

    2026-09-20 真实踩到：报告面板的 `openReport: () => openReport()` 让"回看某份历史报告"
    变成"重新跑一次分析"（rid 没了）。截图甚至看不出差别。
    """
    tagged = set(re.findall(r"data-act=\"([A-Za-z_$][\w$]*)\"[^>]*data-arg=", HTML))
    assert tagged, "全页找不到任何带 data-arg 的按钮 —— 这条测试就失去意义了，请改写它"
    bad = []
    for name in sorted(tagged):
        m = re.search(r"^\s*%s: \(([^)]*)\)" % re.escape(name), HTML, re.M)
        if m is None:
            continue          # 不在 ACTIONS 表里的（比如直接 addEventListener 的）由别的测试管
        if not m.group(1).strip():
            bad.append(f"{name}: ACTIONS 处理器不收参数，data-arg 会被丢掉")
    assert not bad, bad


def test_apply_summarize_buttons_name_their_own_modal():
    """「✅ 应用到项目」有两个按钮，分别住在 `summarize-modal`（粘贴提炼）和
    `session-modal`（会话捕获）里，共用同一个 `applySummarize`。

    2026-09-23 的缺陷：处理器写死 `closeModal("summarize-modal")` —— 从捕获弹窗应用完，
    自己的弹窗不关、按钮还在，再点一次同一批任务又建一遍。截图与人工点一遍都很难看出
    "关的是另一个窗"，所以这里静态对账：**带 applySummarize 的按钮，data-arg 必须
    等于它所在弹窗的 id**。少一个按钮、写错 id、又冒出第三个按钮，都会红。
    """
    chunks = HTML.split('<div class="modal-mask" id="')[1:]
    found: list[tuple[str, str | None]] = []
    for chunk in chunks:
        modal_id = chunk.split('"', 1)[0]
        # 下一个 modal-mask 之前的内容都属于这个弹窗（弹窗不嵌套）
        body = chunk.split('<div class="modal-mask"')[0]
        for m in re.finditer(r'<button[^>]*data-act="applySummarize"[^>]*>', body):
            arg = re.search(r'data-arg="([^"]+)"', m.group(0))
            found.append((modal_id, arg.group(1) if arg else None))
    assert len(found) == 2, f"应恰好两个「应用到项目」按钮，找到 {len(found)} 个：{found}"
    wrong = [(where, arg) for where, arg in found if arg != where]
    assert not wrong, f"这些按钮要关的弹窗不是它自己所在的那个：{wrong}"


def test_session_ingest_does_not_reassign_current_pid():
    """会话捕获里选"入库到哪个项目"以前直接写 `currentPid = pid`。

    屏幕还停在 A，全局指针却去了 B —— 之后在 A 上点「＋ 新建任务」、拖卡片、
    自动刷新，每一件都落在 B 上，而且没有任何提示。入库目标属于"这次提炼"，
    不属于"当前正在看谁"，所以它必须是另一个变量。这条静态判据防的是有人改回去。
    """
    fn = re.search(r"async function ingestSession\(\).*?\n}", HTML, re.S)
    assert fn, "找不到 ingestSession"
    # 先剥掉行注释：函数里那段说明**引用了旧写法本身**，不剥的话这条判据会被
    # 自己的注释绊倒（第一版就是这么红的 —— 断言没错，错在拿全文做子串匹配）。
    code = re.sub(r"//[^\n]*", "", fn.group(0))
    assert "currentPid =" not in code, "入库时不要动全局 currentPid（改它要连着排查所有读它的地方）"
    assert "summarizeTargetPid" in code, "入库目标应记在 summarizeTargetPid 上"


# 输入框上那句提示里的中文标签 → 过滤器实际读的字段
SEARCH_LABELS = {"项目名称": "name", "标签": "tags", "下一步": "next_action", "代码路径": "repo_path"}


def test_search_placeholder_claims_exactly_what_the_filter_reads():
    """搜索框写着「搜索项目名称 / 标签 / **背景 / 交接说明**」，而过滤器只拼了
    name / tags / next_action / repo_path —— 总览列表接口本来就不含背景与交接说明。

    后果不是文案难看：有人在背景里写了"金蝶对接"，搜"金蝶"搜出来是空的，
    只能理解成"我写的内容丢了"。所以提示里承诺的字段集合必须与代码里读的**完全相等**，
    两侧任何一边单独改动都会红。
    """
    ph = re.search(r'id="search"[^>]*placeholder="([^"]+)"', HTML)
    assert ph, "找不到搜索框的 placeholder"
    claimed = [x.strip() for x in ph.group(1).replace("搜索", "", 1).rstrip("…").split("/")]
    unknown = [c for c in claimed if c not in SEARCH_LABELS]
    assert not unknown, f"placeholder 里有没登记映射的标签（加进 SEARCH_LABELS 或改掉文案）：{unknown}"

    hay = re.search(r"const hay = \((.*?)\)\.toLowerCase", HTML, re.S)
    assert hay, "找不到 renderOverview 里的 hay 拼接"
    read = set(re.findall(r"p\.([a-z_]+)", hay.group(1)))
    assert read == {SEARCH_LABELS[c] for c in claimed}, (
        f"提示说搜 {sorted(claimed)}（={sorted(map(SEARCH_LABELS.get, claimed))}），"
        f"实际读的是 {sorted(read)}")
    assert "background" not in read and "handoff_note" not in read, \
        "列表接口不含这两个字段，要真想搜就得先让接口给（那是另一件事）"


def test_journal_input_box_and_backend_share_one_limit():
    """界面上的 `maxlength` 与后端的 `MAX_JOURNAL_TEXT` 必须是同一个数。

    以前是"输入框收 2000、落盘截到 500"：用户/代理都能提交一条看起来成功、
    实际少掉 700 字的记录。跨语言的两个数只靠人对齐迟早会漂，所以钉在这里。
    """
    import app.main as kanban

    m = re.search(r'id="t-journal-input"[^>]*maxlength="(\d+)"', HTML)
    assert m, "过程记录输入框没有 maxlength —— 用户会撞到 422 却不知道原因"
    assert int(m.group(1)) == kanban.MAX_JOURNAL_TEXT, (
        f"界面允许 {m.group(1)} 字，后端只留 {kanban.MAX_JOURNAL_TEXT} 字")


def test_global_search_tells_the_user_the_list_was_cut():
    """Ctrl+K 命中到上限时必须说"后面还有"，否则第 31 条在界面上就是不存在。

    和 MCP 那条同源（接口现在回 `truncated`/`limit`），这边管的是人看到的版本。
    """
    fn = re.search(r"async function runGlobalSearch\(q\).*?\n}", HTML, re.S)
    assert fn, "找不到 runGlobalSearch"
    body = fn.group(0)
    assert "r.truncated" in body, "没读接口的截断标记，等于替用户猜"
    assert "后面还有" in body, "截断了却没说"
    assert "gs-note" in body, "提示行不能复用 .gs-item —— 下面给结果行绑了点击"


def test_acceptance_hint_numbers_match_the_backend():
    """任务弹窗里验收标准的提示数字（条数 / 单条长度）与后端常量必须是同一对数。

    跨语言的两份文本只靠人对齐迟早会漂（journal 上限的教训），钉在这里。
    """
    import app.main as kanban

    m = re.search(r'id="t-acceptance"[^>]*>.*?</textarea>\s*<div[^>]*>([^<]*)</div>', HTML, re.S)
    assert m, "找不到验收标准输入框的提示行"
    hint = m.group(1)
    assert f"最多 {kanban.MAX_ACCEPTANCE_ITEMS} 条" in hint, f"提示没写清条数上限：{hint}"
    assert f"{kanban.MAX_ACCEPTANCE_TEXT} 字" in hint, f"提示没写清单条长度：{hint}"
    # 保存路径要真的把文本框内容拆行发给后端（正则里 \\n 匹配 JS 源码里的字面 \n）
    assert re.search(
        r'acceptance:\s*document\.getElementById\("t-acceptance"\)\.value\.split\("\\n"\)',
        HTML), "saveTask 没有按行读取验收标准，界面上填了也传不出去"


def test_stats_panel_counts_all_five_statuses():
    """任务统计面板的求和与堆叠条必须含全部五个状态 —— 曾漏「待审查」：
    项目只有待审查任务时面板显示"该项目还没有任务"，与看板第五列直接矛盾
    （2026-10-06 对抗性审查发现，10-01 加状态时漏改的前端死角）。"""
    m = re.search(r'const n = \[(.*?)\]\.reduce', HTML)
    assert m, "找不到统计面板的状态求和"
    statuses = re.findall(r'"([^"]+)"', m.group(1))
    assert statuses == ["待办", "进行中", "待审查", "已完成", "阻塞"], statuses
    seg = re.search(r'const seg = \[(.*?)\];', HTML, re.S)
    assert seg and "待审查" in seg.group(1), "堆叠条图例漏了待审查"


def test_milestone_rows_delete_by_element_not_stale_index():
    """里程碑行删除按元素删：data-arg 是渲染时写死的序号，删过一行后剩余行的
    序号与 DOM 下标错位，按下标会删错行甚至删不到（2026-10-06 审查发现）。"""
    fn = re.search(r"function removeMilestoneRow\(btn\)\s*\{(.*?)\n\}", HTML, re.S)
    assert fn, "removeMilestoneRow 不再按元素删除？"
    assert 'closest(".ms-row")' in fn.group(1), fn.group(0)
    assert "children[" not in fn.group(1), "又退回按下标删了"


def test_set_filter_detaches_the_saved_view_in_the_dropdown_too():
    """手动改筛选脱离已保存视图时，视图下拉必须同步回占位 —— 搜索框路径早就同步了，
    chip 路径漏了：下拉还挂着视图名，用户会以为当前筛选仍是那个视图。"""
    fn = re.search(r"function setFilter\(el, f\)\s*\{(.*?)\n\}", HTML, re.S)
    assert fn and "syncViewSelect()" in fn.group(1), fn.group(0)


# ---------- 2026-10-22 用户体验轮：三处改动各钉一条契约 ----------

class _DivNest(HTMLParser):
    """只跟 <div> 的嵌套关系，用来回答「某个 div 是不是另一个 div 的后代」。

    整份前端拼起来再解析（见 `frontend_src`），不写死文件名。
    """

    def __init__(self):
        super().__init__()
        self.stack = []
        self.hits = []

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if "d-head" in classes:
            self.hits.append((list(self.stack), a.get("id") or ""))
        self.stack.append((a.get("id") or "", classes))

    def handle_endtag(self, tag):
        if tag == "div" and self.stack:
            self.stack.pop()


def test_detail_head_is_outside_the_scroll_area():
    """详情头栏不能落在 .detail-scroll 里面。

    2026-10-22 用 `position: sticky` 把它钉在滚动区顶部，滚动后头栏浮在内容之上，
    压住「存」「新建任务」这些按钮 —— 浏览器实测报
    `<div class="d-head"> intercepts pointer events`，真人同样点不到。
    正确形态只有一个：头栏是 #view-detail 的直接子元素，滚动区是它的兄弟。
    """
    p = _DivNest()
    # 只喂 index.html：CSS 注释里也写着 `<div class="d-head">` 这种字样，
    # 拿整份拼接源码喂 HTMLParser 会把它当成真的标签，凭空多出一个"头栏"。
    p.feed((WEB / "index.html").read_text(encoding="utf-8"))
    assert len(p.hits) == 1, f"详情头栏应只有一个，实际 {len(p.hits)}"
    ancestors, _ = p.hits[0]
    assert not any("detail-scroll" in cls for _, cls in ancestors), \
        "头栏又回到滚动区里了：sticky/absolute 都会让它在滚动时挡住内容的点击"
    assert ancestors and ancestors[-1][0] == "view-detail", \
        f"头栏的父级应是 #view-detail，实际 {ancestors[-1] if ancestors else None}"

    css = (WEB / "style.css").read_text(encoding="utf-8")
    rules = re.findall(r"([^{}]*\.d-head[^{}]*)\{([^{}]*)\}", css)
    sticky = [sel.strip() for sel, body in rules if re.search(r"position\s*:\s*sticky", body)]
    assert not sticky, f"这些规则又给头栏加了 sticky（滚动时会遮住并吃掉点击）：{sticky}"


def test_memory_form_wraps_instead_of_overflowing_the_panel():
    """侧栏 328px 面板里塞四个控件，必须换行且允许输入框收缩。

    曾经 flex 不换行、输入框保留默认 min-width，整行往右溢出 88px：「存」按钮跑到
    隔壁列下面，鼠标点上去命中隔壁列（E2E 的 elementFromPoint 实测）。
    """
    css = (WEB / "style.css").read_text(encoding="utf-8")
    m = re.search(r"#memory-form\s*\{([^}]*)\}", css)
    assert m, "找不到 #memory-form 的样式（是不是又搬回内联了？）"
    assert re.search(r"flex-wrap\s*:\s*wrap", m.group(1)), f"表单又不会换行了：{m.group(1)}"
    t = re.search(r"#memory-form\s+#memory-text\s*\{([^}]*)\}", css)
    assert t and re.search(r"min-width\s*:\s*0", t.group(1)), \
        "输入框要 min-width:0 才能被压缩，否则会把按钮挤到面板外面"


def test_search_clear_button_appears_only_with_input():
    """清空按钮：默认隐藏、有输入才出现、点了清空并回到全部。"""
    tag = re.search(r"<button[^>]*id=\"search-clear\"[^>]*>", HTML)
    assert tag, "找不到清空搜索按钮"
    assert "hidden" in tag.group(0), "没输入内容时清空按钮不该占位"
    assert 'data-act="clearSearch"' in tag.group(0), "清空按钮没有接到动作分发"

    fn = re.search(r"function clearSearch\(\)\s*\{(.*?)\n\}", HTML, re.S)
    assert fn, "clearSearch 不见了？"
    body = fn.group(1)
    assert 'searchInput.value = ""' in body, body
    assert "renderOverview()" in body, "清空后没有重渲染，卡片还停在筛选结果上"
    assert "updateSearchClear()" in body, "清空后没同步按钮自身的显隐"

    hook = re.search(r'searchInput\.addEventListener\("input", \(\) => \{(.*?)\n  \}\);', HTML, re.S)
    assert hook and "updateSearchClear()" in hook.group(1), \
        "输入时没有刷新按钮显隐：按钮会一直不出现"


def test_empty_state_onboarding_shows_the_three_steps():
    """一个项目都没有时给三步引导；有项目、只是搜不到/筛不到时不能出现。"""
    box = re.search(r'id="empty-onboard"(.*?)</div>\s*</div>', HTML, re.S)
    assert box, "找不到空态引导块"
    steps = re.findall(r"<b>(\d)</b>\s*([^<]+)", box.group(1))
    assert [n for n, _ in steps] == ["1", "2", "3"], f"三步引导不完整：{steps}"
    assert any("新建项目" in t for _, t in steps), f"第一步不是新建项目：{steps}"

    render = re.search(r"function renderOverview\(\)\s*\{(.*?)\n\}", HTML, re.S)
    assert render, "找不到 renderOverview"
    src = render.group(1)
    blank = re.search(r"else if \(!projects\.length\) \{(.*?)\} else if", src, re.S)
    assert blank, "找不到「真的没有项目」那个分支"
    assert "onboard.hidden = false" in blank.group(1), "真没项目时没显示引导"
    others = re.findall(r"onboard\.hidden = (true|false)", src)
    assert others.count("false") == 1, f"引导只该在「一个项目都没有」时出现，实际 false 出现 {others.count('false')} 次"
