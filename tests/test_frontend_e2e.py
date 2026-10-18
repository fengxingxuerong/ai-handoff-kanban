# -*- coding: utf-8 -*-
"""真实浏览器点击测试：前端此前只有静态契约检查（内联事件归零 / data-act 已注册），
渲染与真实交互零覆盖 —— 这里点真页面、断言真 DOM。

用 Playwright 驱动**系统自带的 Edge/Chrome**（channel=msedge/chrome），不下载 Chromium。
没装 playwright、或机器上没有浏览器时自动跳过：常规套件与 CI 的确定性不受影响。

常见断言：卡片渲染、弹窗保存流程、任务落到看板列、全局搜索、特殊字符不炸按钮、
删除确认、任何 console error / pageerror 直接判失败。
"""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("playwright.sync_api", reason="未安装 playwright（pip install playwright）")

from playwright.sync_api import sync_playwright  # noqa: E402

CHANNELS = ["msedge", "chrome"]


def open_header_menu(page, act: str):
    """从顶栏「✨ AI」下拉里选一项（AI 指令 / AI 监管 / 会话捕获）。

    2026-10-17 顶栏分组后，这三个操作不再平铺在顶栏上，而是收进下拉菜单 ——
    直接 open_header_menu(page, "openAICmd") 会因为菜单项 hidden 而超时。
    先点开「✨ AI」再点菜单项，走的也正是用户真实的那条路径。
    """
    page.click("[data-act='toggleHeaderMenu']")
    page.click(f".hmenu-item[data-act='{act}']")


@pytest.fixture(scope="module")
def browser():
    pw = sync_playwright().start()
    b = None
    err = ""
    for ch in CHANNELS:
        try:
            b = pw.chromium.launch(channel=ch, headless=True)
            break
        except Exception as e:                                  # noqa: BLE001
            err = str(e)[:200]
            b = None
    if b is None:
        pw.stop()
        pytest.skip(f"没有可用的 Edge/Chrome：{err}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def ui(fresh_server, browser):
    """打开首页并收集 JS 报错；任何 console.error / pageerror 都会在收尾时判失败"""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
    page.goto(fresh_server.base + "/", wait_until="domcontentloaded")
    page.wait_for_selector("[data-act='openProjectModal']", timeout=20000)
    yield page, fresh_server, errors
    ctx.close()
    assert not errors, "前端出现 JS 报错：\n" + "\n".join(errors)


@pytest.fixture
def ui_lenient(fresh_server, browser):
    """与 ui 同款，但只放过 "Failed to load resource" 这一条 console.error。
    验证失败态时要故意 abort 请求 / 打一个不存在的项目，浏览器必然记一条资源错误；
    pageerror 仍然一律判失败 —— 放松的只是网络噪声，不是 JS 异常。"""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}")
            if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.goto(fresh_server.base + "/", wait_until="domcontentloaded")
    yield page, fresh_server, errors
    ctx.close()
    assert not errors, "前端出现 JS 报错：" + " ｜ ".join(errors)


def make_project(api, name, **kw):
    body = {"name": name, "status": "进行中", "progress": 10, "priority": "高"}
    body.update(kw)
    return api.post("/api/projects", body)[1]


# ---------------- 渲染 ----------------

def test_overview_renders_cards_and_stats(ui):
    page, api, _ = ui
    make_project(api, "渲染检查项目甲", progress=42, next_action="先做甲的下一个动作")
    make_project(api, "渲染检查项目乙", status="已完成", progress=100)
    page.reload(wait_until="domcontentloaded")

    page.wait_for_selector(".pcard", timeout=15000)
    assert page.locator(".pcard").count() == 2, "卡片数不对"
    # 卡片按更新时间倒序，别用 .first 猜顺序
    card_a = page.locator(".pcard:has-text('渲染检查项目甲')")
    card_b = page.locator(".pcard:has-text('渲染检查项目乙')")
    assert card_a.count() == 1 and card_b.count() == 1
    text_a = card_a.inner_text()
    assert "42%" in text_a, "进度没渲染出来"
    assert "先做甲的下一个动作" in text_a, "下一步没渲染出来"
    assert "100%" in card_b.inner_text()


def test_special_chars_in_name_do_not_break_buttons(ui):
    """历史事故：卡片按钮把项目名拼进内联事件，名字带撇号直接 JS 语法错误"""
    page, api, _ = ui
    weird = "张三's <b>项目</b>｜竖线\\反斜杠"
    make_project(api, weird)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    card = page.locator(".pcard:has-text('张三')").first
    assert weird in card.inner_text(), "名字被 HTML 转义破坏了"
    # 卡片自己有加粗标签，所以判据是：注入的 <b>项目</b> 不能变成元素
    assert page.locator(".pcard b", has_text="项目").count() == 0, "<b> 被当成标签渲染了（应原样显示）"

    card.click()                                        # 能点进详情 = 事件绑定正常
    page.wait_for_selector("#view-detail.active", timeout=10000)
    assert weird in page.locator("#view-detail").inner_text()


# ---------------- 交互流程 ----------------

def test_create_project_through_modal(ui):
    page, api, _ = ui
    page.click("[data-act='openProjectModal']")
    page.wait_for_selector("#f-name", timeout=10000)
    page.fill("#f-name", "界面新建的项目")
    page.select_option("#f-status", "进行中")
    page.fill("#f-progress", "35")
    page.fill("#f-next", "界面填写的下一步")
    page.click("[data-act='saveProject']")

    page.wait_for_selector(".pcard:has-text('界面新建的项目')", timeout=15000)
    _, data = api.get("/api/projects")
    created = next(p for p in data["projects"] if p["name"] == "界面新建的项目")
    assert created["progress"] == 35 and created["next_action"] == "界面填写的下一步"


def test_add_task_through_detail_page(ui):
    page, api, _ = ui
    make_project(api, "有任务的项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)

    page.click("[data-act='openTaskModal']")
    page.wait_for_selector("#t-title", timeout=10000)
    page.fill("#t-title", "界面加的活")
    page.fill("#t-desc", "验收：列里能看到")
    page.click("[data-act='saveTask']")

    page.wait_for_selector("#col-todo:has-text('界面加的活')", timeout=15000)
    _, data = api.get("/api/projects")
    pid = data["projects"][0]["id"]
    tasks = api.get(f"/api/projects/{pid}")[1]["tasks"]
    assert [t["title"] for t in tasks] == ["界面加的活"]


def test_global_search_jumps_to_project(ui):
    page, api, _ = ui
    pid = make_project(api, "搜索目标项目")["id"]
    api.post(f"/api/projects/{pid}/tasks", {"title": "独一无二的任务标题", "status": "进行中"})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    page.click("[data-act='openGlobalSearch']")
    page.wait_for_selector("#gs-input", timeout=10000)
    page.fill("#gs-input", "独一无二")
    page.wait_for_selector(".gs-item", timeout=10000)
    assert "独一无二的任务标题" in page.locator("#gs-results").inner_text()

    page.locator(".gs-item").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)


def test_palette_shows_commands_and_runs_new_project(ui):
    """Ctrl+K 现在是命令面板：空输入展示命令，点「新建项目」能真的开出项目弹窗"""
    page, api, _ = ui
    make_project(api, "面板项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    page.click("[data-act='openGlobalSearch']")
    page.wait_for_selector("#palette-commands", timeout=10000)
    assert "新建项目" in page.locator("#palette-commands").inner_text()
    assert "今日待办" in page.locator("#palette-commands").inner_text()

    # 命令面板关闭后 project-modal 应正常弹出（面板不能挡在后面）
    page.locator("[data-act='paletteRun'][data-arg='new-project']").click()
    page.wait_for_selector("#proj-modal.show", timeout=10000)
    assert not page.locator("#global-search-modal").is_visible(), "执行命令后面板应该已关闭"


def test_palette_filters_commands_with_gt_prefix(ui):
    """输入 `>` 后只留匹配的命令；普通关键词则走任务搜索"""
    page, api, _ = ui
    make_project(api, "过滤项目")
    api.post(f"/api/projects/{make_project(api, '过滤项目2')['id']}/tasks",
             {"title": "过滤关键词任务", "status": "待办"})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    page.click("[data-act='openGlobalSearch']")
    page.wait_for_selector("#palette-commands", timeout=10000)
    page.fill("#gs-input", ">导出")
    # 带 `>` 前缀 → 命令过滤；「导出交接包」在列表里，"新建项目"被滤掉
    page.wait_for_selector("#palette-commands .palette-item", timeout=10000)
    text = page.locator("#palette-commands").inner_text()
    assert "导出交接包" in text and "新建项目" not in text, f"过滤结果不对：{text}"
    assert page.locator("#gs-results").inner_text() == "", "命令模式下不该渲染搜索结果"

    page.fill("#gs-input", "过滤关键词任务")
    page.wait_for_selector(".gs-item", timeout=10000)
    assert "过滤关键词任务" in page.locator("#gs-results").inner_text()


def test_delete_project_asks_confirmation(ui):
    page, api, _ = ui
    make_project(api, "待删项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)

    # 删除现在收在详情页「更多」菜单里；先展开再点，验证的是菜单可发现性 + 原确认门。
    page.click(".d-more > summary")
    page.wait_for_selector(".d-more-pop")
    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    page.click("[data-act='deleteProject']")
    page.wait_for_selector(".pcard", state="detached", timeout=15000)
    assert seen, "删除没有二次确认（真实使用里这是误删的唯一防线）"
    assert api.get("/api/projects")[1]["projects"] == []


def test_detail_more_menu_closes_on_outside_click_escape_and_pick(ui):
    """详情页「更多」是原生 <details>：只有"再点标题"一条收起路径。

    实测最别扭的一幕：点「🗑 删除」走完二次确认、项目都没了，菜单还开着。
    这里钉住补齐的三条路 —— 点别处 / Esc / 选完菜单项都要收。
    """
    page, api, _ = ui
    make_project(api, "菜单收起项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)

    def menu_open() -> bool:
        return page.evaluate("() => document.querySelector('details.d-more').open === true")

    # 点标题能展开（原生行为不能被我们的收起逻辑打断）
    page.click(".d-more > summary")
    page.wait_for_function("() => document.querySelector('details.d-more').open === true")
    assert menu_open()

    # ① 点别处收起
    page.click("#d-name")
    page.wait_for_function("() => document.querySelector('details.d-more').open === false")

    # ② Esc 收起
    page.click(".d-more > summary")
    page.wait_for_function("() => document.querySelector('details.d-more').open === true")
    page.keyboard.press("Escape")
    page.wait_for_function("() => document.querySelector('details.d-more').open === false")

    # ③ 选完菜单项收起（以前它会挂在原地，看着像刚才那一下没生效）
    page.click(".d-more > summary")
    page.wait_for_selector(".d-more-pop", timeout=10000)
    page.click("[data-act='openAssignModal']")
    page.wait_for_function("() => document.querySelector('details.d-more').open === false")
    page.wait_for_selector("#assign-modal.show", timeout=10000)
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.querySelector('#assign-modal.show')")


def test_theme_toggle_switches_and_persists(ui):
    page, api, _ = ui
    before = page.evaluate("document.documentElement.dataset.theme || document.body.className")
    page.click("[data-act='toggleTheme']")
    page.wait_for_timeout(200)
    after = page.evaluate("document.documentElement.dataset.theme || document.body.className")
    assert after != before, "主题没切换"
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("[data-act='openProjectModal']", timeout=15000)
    again = page.evaluate("document.documentElement.dataset.theme || document.body.className")
    assert again == after, "刷新后主题没保持"


# ---------------- 面板与容错 ----------------

def test_settings_and_recover_panel_open(ui):
    page, _, _ = ui
    page.click("[data-act='openAISettings']")
    page.wait_for_selector("[data-act='showRecoverable']", timeout=10000)
    page.click("[data-act='showRecoverable']")
    page.wait_for_selector("#recover-list", timeout=10000)
    assert re.search(r"当前：\d+ 个项目", page.locator("#recover-list").inner_text())


# ---------------- AI 交互（起一个配了假端点的真服务） ----------------

@pytest.fixture(scope="module")
def ai_stub():
    from llm_stub import StubLLM

    stub = StubLLM().start()
    yield stub
    stub.stop()


@pytest.fixture
def ai_ui(tmp_path_factory, browser, ai_stub):
    """带假 LLM 端点的服务：AI 相关的交互（报告/指令/提炼/测连接）才能进浏览器层。

    桩跑在 pytest 进程里，服务在子进程里通过 127.0.0.1 调它 —— 走的是真 HTTP，
    既不消耗配额，也不改被测代码。
    """
    from conftest import _server_ctx

    ai_stub.reset()
    data_dir = tmp_path_factory.mktemp("kanban-e2e-ai")
    (data_dir / "config.json").write_text(json.dumps({"ai": {
        "endpoints": [ai_stub.endpoint("桩端点")],
        "active_model": "stub-model",
        "schedule": {"enabled": False, "frequency": "daily", "time": "09:00", "last_run": ""},
    }}, ensure_ascii=False), encoding="utf-8")

    with _server_ctx(data_dir, {"KANBAN_NO_SCHEDULER": "1"}) as (api, _proc):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []

        def on_console(m):
            # favicon 的 404 是浏览器自发的，与应用无关，别当失败
            if m.type == "error" and "favicon" not in m.text:
                errors.append(f"console.error: {m.text}")

        def on_response(r):
            if r.status >= 400 and "favicon" not in r.url:
                errors.append(f"HTTP {r.status}: {r.url}")

        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", on_console)
        page.on("response", on_response)
        page.goto(api.base + "/", wait_until="domcontentloaded")
        page.wait_for_selector("[data-act='openProjectModal']", timeout=20000)
        try:
            yield page, api, ai_stub, errors
        finally:
            ctx.close()
        assert not errors, "前端出现 JS 报错或请求失败：\n" + "\n".join(errors)


def test_ai_report_renders_into_panel(ai_ui):
    page, api, stub, _ = ai_ui
    pid = make_project(api, "报告里的项目")["id"]
    stub.push_json({
        "summary": "整体节奏正常，注意补文档",
        "insights": [{"id": pid, "name": "报告里的项目", "health": 88, "reason": "推进稳定",
                      "risks": ["文档滞后"], "suggestions": [{"text": "补交接文档", "apply_to": "next_action"}]}],
        "global_suggestions": ["统一收口"],
    })
    open_header_menu(page, "openReport")
    page.wait_for_selector("#report-panel.show", timeout=15000)

    body = page.locator("#report-body")
    body.locator("text=整体节奏正常，注意补文档").first.wait_for(timeout=15000)
    assert "88" in body.inner_text(), "健康分没渲染出来"
    assert "文档滞后" in body.inner_text(), "风险项没渲染出来"


def test_ai_command_needs_confirmation_before_writing(ai_ui):
    """人工闸门：AI 解析出操作后**不能自动落库**，必须等用户点确认"""
    page, api, stub, _ = ai_ui
    pid = make_project(api, "指令目标项目")["id"]
    stub.push_json({"actions": [{"type": "create_task", "project_id": pid,
                                 "title": "指令加的任务", "desc": "来自自然语言"}],
                    "reply": "好的"})

    open_header_menu(page, "openAICmd")
    page.wait_for_selector("#cmd-input", timeout=10000)
    page.fill("#cmd-input", "给指令目标项目加个任务：指令加的任务")
    page.click("#cmd-send-btn")
    page.wait_for_selector("#cmd-exec-row:visible", timeout=15000)

    tasks = api.get(f"/api/projects/{pid}")[1]["tasks"]
    assert tasks == [], "还没确认就把任务写进去了 —— 人工闸门失效"

    page.click("[data-act='executeActions']")
    page.wait_for_selector("#cmd-chat:has-text('指令加的任务')", timeout=15000)
    tasks = api.get(f"/api/projects/{pid}")[1]["tasks"]
    assert [t["title"] for t in tasks] == ["指令加的任务"], "确认后应该真的落库"


def test_executing_the_same_ai_command_twice_creates_once(ai_ui):
    """「▶ 执行」连点两次不能把那批操作做两遍。

    AI 指令的创建类动作以前不带幂等键：一次解析结果点两下执行，项目/任务就成对出现，
    而重复项会跟着进交接包与拓扑序，事后只能靠"标题一样"手工分辨。
    现在同一次解析共享一个 batch_ref（第 i 条 = `ref:i`），第二次只是重放。
    """
    page, api, stub, _ = ai_ui
    pid = make_project(api, "指令重复执行的项目")["id"]
    stub.push_json({"actions": [
        {"type": "create_task", "project_id": pid, "title": "指令建的一条"},
        {"type": "create_project", "name": "指令建的项目"},
    ], "reply": "好"})
    open_header_menu(page, "openAICmd")
    page.wait_for_selector("#cmd-input", timeout=10000)
    page.fill("#cmd-input", "建一条任务，再顺手开一个叫「指令建的项目」的项目")
    page.click("#cmd-send-btn")
    page.wait_for_selector("#cmd-exec-row:visible", timeout=15000)
    page.on("dialog", lambda d: d.accept())

    page.eval_on_selector("[data-act='executeActions']", "el => { el.click(); el.click(); }")
    page.wait_for_timeout(1800)
    names = [p["name"] for p in api.get("/api/projects")[1]["projects"]]
    assert names.count("指令建的项目") == 1, f"项目被建了两遍：{names}"
    tasks = api.get(f"/api/projects/{pid}")[1]["tasks"]
    assert [t["title"] for t in tasks].count("指令建的一条") == 1, tasks


def test_ai_command_update_sends_a_patch_not_a_read_modify_put(ai_ui):
    """AI 指令改一个字段，不能再"读整份 → 改一处 → PUT 整份"。

    那次读发生在临界区外：期间智能体或桌面端写进这个项目的其它字段，会被这次
    带回来的旧值反向抹掉（约定 1 的形状）。现在只 PATCH 点名的那一个字段。
    """
    page, api, stub, _ = ai_ui
    pid = make_project(api, "指令只改进度的项目", handoff_note="这一句不该被牵连")["id"]
    stub.push_json({"actions": [{"type": "update_project", "project_id": pid,
                                 "field": "progress", "value": 66}], "reply": "好"})
    open_header_menu(page, "openAICmd")
    page.wait_for_selector("#cmd-input", timeout=10000)
    page.fill("#cmd-input", "把「指令只改进度的项目」进度改成 66%")
    page.click("#cmd-send-btn")
    page.wait_for_selector("#cmd-exec-row:visible", timeout=15000)
    page.on("dialog", lambda d: d.accept())

    put_seen = []
    page.on("request", lambda r: put_seen.append(r.method + " " + r.url)
            if r.method == "PUT" else None)
    page.click("[data-act='executeActions']")
    page.wait_for_selector("#cmd-chat:has-text('执行完成')", timeout=15000)
    p = api.get(f"/api/projects/{pid}")[1]
    assert p["progress"] == 66, p["progress"]
    assert p["handoff_note"] == "这一句不该被牵连", f"改进度牵连了别的字段：{p['handoff_note']}"
    assert not [u for u in put_seen if f"/api/projects/{pid}" in u], \
        f"单字段改动却发了整份 PUT：{put_seen}"


def test_ai_summarize_then_apply(ai_ui):
    """会话粘贴提炼：提炼结果要能一键落到项目上"""
    page, api, stub, _ = ai_ui
    pid = make_project(api, "提炼目标项目")["id"]
    page.reload(wait_until="domcontentloaded")            # fixture 先开了页面，新数据要刷新才可见
    page.wait_for_selector(".pcard", timeout=15000)
    stub.push_json({
        "summary": "聊了部署与回滚",
        "new_tasks": [{"title": "写部署文档", "desc": "含回滚步骤", "priority": "高", "status": "待办"}],
        "next_action": "先补部署文档",
        "handoff_note_updates": "部署走 CI",
    })

    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.click("[data-act='openSummarize']")
    page.wait_for_selector("#summarize-text", timeout=10000)
    page.fill("#summarize-text", "我们决定用 CI 部署，回滚步骤要写进文档，下周一交付。" * 2)
    page.click("#summarize-btn")
    page.wait_for_selector("#summarize-result:visible", timeout=20000)
    assert "写部署文档" in page.locator("#summarize-result").inner_text()

    # 落地前有 confirm（人工闸门）；Playwright 默认会自动取消对话框，必须显式接受
    page.on("dialog", lambda d: d.accept())
    page.click("#summarize-apply-btn")
    page.wait_for_selector("#col-todo:has-text('写部署文档')", timeout=15000)
    detail = api.get(f"/api/projects/{pid}")[1]
    assert [t["title"] for t in detail["tasks"]] == ["写部署文档"]
    assert detail["next_action"] == "先补部署文档"


def test_applying_a_summary_twice_builds_the_tasks_once(ai_ui):
    """连点两次「✅ 应用到项目」不能把那批任务建两遍。

    2026-09-23 实盘查到的形状：`applySummarize` 结尾写死 `closeModal("summarize-modal")`，
    而会话捕获那个按钮在 **session-modal** 里 —— 弹窗不关、按钮还在，再点一次就是第二遍，
    交接说明也被追加两遍；而这条链建任务时不带幂等键。两层一起修：
    关的是发起那次应用的弹窗，任务键 = 本次提炼的 batch_ref + 序号。
    """
    page, api, stub, _ = ai_ui
    pid = make_project(api, "双击会建两遍的项目")["id"]
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    stub.push_json({
        "summary": "两件事", "new_tasks": [{"title": "接入回调", "priority": "高"},
                                           {"title": "补迁移脚本", "priority": "中"}],
        "next_action": "先接回调", "memories": [], "handoff_note_updates": "",
    })
    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.click("[data-act='openSummarize']")
    page.wait_for_selector("#summarize-text", timeout=10000)
    page.fill("#summarize-text", "决定用事件驱动补回调，迁移脚本单独一轮交付，下周一验收。" * 2)
    page.click("#summarize-btn")
    page.wait_for_selector("#summarize-result:visible", timeout=20000)
    page.on("dialog", lambda d: d.accept())

    # 同一次事件循环里连发两次点击 = 手抖双击的真实形状（第一次还在 await 里）
    page.eval_on_selector("#summarize-apply-btn", "el => { el.click(); el.click(); }")
    page.wait_for_selector("#col-todo:has-text('接入回调')", timeout=15000)
    page.wait_for_timeout(1200)                     # 给第二次点击落盘的机会：它不该建出东西
    titles = [t["title"] for t in api.get(f"/api/projects/{pid}")[1]["tasks"]]
    assert sorted(titles) == ["接入回调", "补迁移脚本"], f"建出了重复任务：{titles}"
    # 而且应用完必须把弹窗关掉：按钮一直留在屏幕上，下一次误点只是时间问题
    assert page.locator("#summarize-modal").get_attribute("class").find("show") < 0, "弹窗没关"
    """设置面板的「测试连接」要走通（端点挂了很常见，这个按钮是排查入口）"""
    page, _, _, _ = ai_ui
    page.click("[data-act='openAISettings']")
    page.wait_for_selector("#ai-test-btn", timeout=10000)
    page.click("#ai-test-btn")
    page.wait_for_selector("#toast:has-text('连接成功')", timeout=15000)


# ---------------- 导出 / 导入（此前是死按钮） ----------------

def test_export_button_really_downloads(ui, tmp_path):
    """历史事故：导出按钮注册了 ACTIONS，但 exportData() 从未定义 —— 点了只有 ReferenceError。
    这条用例点真按钮、等真下载、再验文件内容（含"不能带密钥"）。"""
    page, api, _ = ui
    make_project(api, "要导出的项目")
    page.reload(wait_until="domcontentloaded")
    page.click("[data-act='openAISettings']")
    page.wait_for_selector("[data-act='exportData']", timeout=10000)

    with page.expect_download(timeout=15000) as dl:
        page.click("[data-act='exportData']")
    download = dl.value
    assert download.suggested_filename.startswith("kanban-data_")
    assert download.suggested_filename.endswith(".json")

    path = tmp_path / "downloaded.json"
    download.save_as(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [p["name"] for p in data["projects"]] == ["要导出的项目"]
    assert "api_keys" not in path.read_text(encoding="utf-8"), "导出文件里带了密钥字段"


def test_import_button_really_replaces_data(ui, tmp_path):
    """导入同样是死按钮：这个用例走完"选文件 → 确认 → 数据真的被替换"全流程"""
    page, api, _ = ui
    make_project(api, "导入前的项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    payload = {"app": "ai-kanban", "version": "1.0.0", "exported_at": "2026-09-15 10:00",
               "projects": [{"id": "imp1", "name": "导入进来的项目", "status": "进行中",
                             "priority": "高", "progress": 20, "tasks": []}]}
    file = tmp_path / "kanban-data.json"
    file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    page.on("dialog", lambda d: d.accept())          # 覆盖前会弹确认
    page.click("[data-act='openAISettings']")
    page.wait_for_selector("[data-act='importData']", timeout=10000)
    page.click("[data-act='importData']")             # 触发隐藏的文件选择框
    page.set_input_files("#import-file", str(file))

    page.wait_for_selector(".pcard:has-text('导入进来的项目')", timeout=15000)
    assert api.get("/api/projects")[1]["projects"][0]["name"] == "导入进来的项目"


# ---------------- 拖拽改状态 ----------------

def test_dragging_a_card_does_not_wipe_what_an_agent_wrote_in_between(ui):
    """详情页开着 → 智能体经 MCP 给这条任务补 refs/desc → 用户拖一列。

    以前拖拽发的是"从页面缓存里挑出的整条任务"做 PUT（`current` 只在打开详情时取过，
    页面也不轮询），于是对方刚写进去的 refs / depends_on / desc 被手里这份旧快照
    整体抹掉，界面只提示"已移至进行中 ✓"，journal 里也只看得到一次状态变化。
    实盘复现过（refs=[a.py] + desc → 拖一次 → refs=[] desc=''），修法是改发 PATCH。
    """
    page, api, _ = ui
    pid = make_project(api, "拖拽会牵连别人的项目")["id"]
    tid = api.post(f"/api/projects/{pid}/tasks", {"title": "被拖的这条"})[1]["id"]
    page.reload(wait_until="domcontentloaded")
    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.wait_for_selector(".kcol.k-todo .tcard", timeout=10000)

    # 页面快照取过之后，另一个写入方（智能体走的就是这条 PATCH/patch_task 路径）补了内容
    api.patch(f"/api/tasks/{tid}", {"refs": ["app/main.py:42"], "desc": "智能体补的上下文"})
    page.drag_and_drop(".kcol.k-todo .tcard", ".kcol.k-doing")
    page.wait_for_selector(".kcol.k-doing:has-text('被拖的这条')", timeout=15000)

    after = api.get("/api/tasks/" + tid)[1]["task"]
    assert after["status"] == "进行中", f"拖拽没生效：{after['status']}"
    assert after["refs"] == ["app/main.py:42"], f"拖一次卡片把别人写的 refs 抹了：{after['refs']}"
    assert after["desc"] == "智能体补的上下文", after["desc"]


def test_drag_task_between_columns(ui):
    """四列看板拖拽：把任务从「待办」拖到「进行中」，状态要真的落到服务端"""
    page, api, _ = ui
    pid = make_project(api, "拖拽项目")["id"]
    api.post(f"/api/projects/{pid}/tasks", {"title": "拖我一把", "status": "待办"})
    page.reload(wait_until="domcontentloaded")
    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.wait_for_selector(".kcol.k-todo .tcard", timeout=10000)

    # 列是 class 不是 id：k-todo / k-doing 挂在 .kcol 上
    page.drag_and_drop(".kcol.k-todo .tcard", ".kcol.k-doing")
    page.wait_for_selector(".kcol.k-doing:has-text('拖我一把')", timeout=15000)
    tasks = api.get(f"/api/projects/{pid}")[1]["tasks"]
    assert tasks[0]["status"] == "进行中", f"拖拽没落库：{tasks[0]['status']}"


def test_pages_handle_empty_board(ui):
    """空看板（用户把示例删光）不能让界面报错或白屏"""
    page, api, _ = ui
    for p in api.get("/api/projects")[1]["projects"]:
        api.delete(f"/api/projects/{p['id']}")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("[data-act='openProjectModal']", timeout=15000)
    page.wait_for_timeout(500)
    assert "还没有项目" in page.locator("body").inner_text() or page.locator(".pcard").count() == 0


def test_search_with_no_match_does_not_claim_the_board_is_empty(ui):
    """手里有项目、搜索打了个不匹配的词，界面以前大字宣布「还没有项目，点右上角新建」
    —— 看着就像数据被谁清空了。三种"屏幕上没卡片"（真没项目 / 筛选空 / 搜索不匹配）
    必须分开说。
    """
    page, api, _ = ui
    make_project(api, "对账用甲项目")
    make_project(api, "对账用乙项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.fill("#search", "zzz这个词不可能匹配zzz")
    page.wait_for_timeout(900)                      # 搜索是防抖的
    body = page.locator("body").inner_text()
    assert page.locator(".pcard").count() == 0
    assert "还没有项目" not in body, "搜不到却宣布用户没有项目：" + body[:200]
    assert "没有匹配" in body, "没说出「是没有匹配」这件事：" + body[:200]


def test_progress_vs_tasks_verdict_shows_without_a_repo_path(ui):
    """「进度写 90% 却一条任务没完成」这条判据一个 git 字段都不碰。

    以前 `realityVerdicts` 开头就是 `if (!p.repo_path) return [...]`，把这条与 git
    无关的判据一起挡掉了 —— 而"没填代码路径"恰恰是多数项目的常态，
    最需要有人指出进度对不上的那些项目反而永远听不到。
    """
    page, api, _ = ui
    p = make_project(api, "没填仓库路径却写了90", progress=90)
    for title in ("还没动的任务一", "还没动的任务二", "还没动的任务三"):
        api.post(f"/api/projects/{p['id']}/tasks",
                 {"title": title, "status": "待办", "priority": "中"})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.wait_for_function(
        "() => { const v = document.querySelectorAll('#reality-verdicts .verdict');"
        "  return v.length >= 1 && !/正在核对|读取中/.test(v[0].innerText); }", timeout=15000)
    txt = page.locator("#reality-verdicts").inner_text()
    assert "进度条写 90%" in txt, f"与 git 无关的那句判据没出现：{txt[:200]}"
    assert "没填代码路径" in txt, f"没交代清楚这一条只对着看板自己核：{txt[:200]}"


def test_task_sort_reorders_cards(ui):
    """切换排序后卡片顺序要真的变（默认顺序是新建在前）"""
    page, api, _ = ui
    p = make_project(api, "排序检查项目")
    api.post(f"/api/projects/{p['id']}/tasks", {"title": "高优先任务", "status": "待办", "priority": "高"})
    api.post(f"/api/projects/{p['id']}/tasks", {"title": "低优先任务", "status": "待办", "priority": "低"})
    page.reload(wait_until="domcontentloaded")

    page.click(".pcard:has-text('排序检查项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.wait_for_selector("#col-todo .tcard", timeout=10000)

    first_before = page.locator("#col-todo .tcard .tt").first.inner_text()
    assert "低优先" in first_before, f"默认应是新建在前，实际首张：{first_before}"

    page.select_option("#task-sort", "priority")
    first_after = page.locator("#col-todo .tcard .tt").first.inner_text()
    assert "高优先" in first_after, f"按优先级排序后高优先应在前，实际首张：{first_after}"


def test_saved_view_roundtrip_save_apply_delete(ui):
    """保存筛选为视图 → 切换筛选 → 应用视图恢复 → 删除视图。
    视图菜单是总览卡片的过滤条件快照，保存/应用/删除三条路径都要真的能用。"""
    page, api, _ = ui
    make_project(api, "视图检查甲", status="进行中")
    make_project(api, "视图检查乙", status="已完成")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    # 1) 保存当前筛选（已完成）+ 搜索词为命名视图
    page.click(".filter-bar .chip:has-text('已完成')")
    page.fill("#search", "视图检查")
    page.wait_for_function("() => document.querySelectorAll('.pcard').length === 1", timeout=10000)
    page.click("[data-act='toggleViewForm']")
    page.fill("#view-name", "只看已完成")
    page.click("[data-act='saveView']")
    page.wait_for_function("() => document.getElementById('toast').classList.contains('show') "
                           "&& document.getElementById('toast').textContent.includes('已保存')", timeout=10000)
    assert page.locator("#view-sel option").count() == 2, "下拉里应有「视图」占位 + 新视图"

    # 记录该视图的 option value，后面应用与删除都靠它
    vid = page.locator("#view-sel option").nth(1).get_attribute("value")
    assert vid

    # 2) 换一个筛选（全部——甲乙两张卡都回来）并清空搜索，再从视图下拉切回
    #    → chip、搜索词、卡片数都该回到已保存的快照（q 维度也要恢复）
    page.click(".filter-bar .chip:has-text('全部')")
    page.fill("#search", "")
    page.wait_for_function("() => document.querySelectorAll('.pcard').length === 2", timeout=10000)

    page.select_option("#view-sel", vid)
    page.wait_for_function("() => document.querySelector('.filter-bar .chip.active').textContent === '已完成'",
                           timeout=10000)
    page.wait_for_function("() => document.getElementById('search').value === '视图检查'", timeout=10000)
    page.wait_for_function("() => document.querySelectorAll('.pcard').length === 1", timeout=10000)

    page.click("[data-act='toggleViewForm']")        # 删除按钮在折叠的表单里
    page.on("dialog", lambda d: d.accept())          # deleteView 前有 confirm
    page.click("[data-act='deleteView']")
    page.wait_for_function("() => document.querySelectorAll('#view-sel option').length === 1", timeout=10000)
    assert "只看已完成" not in page.locator("#view-sel").inner_html(), "视图删除后选项仍在"
    page.wait_for_function("() => document.getElementById('toast').classList.contains('show') "
                           "&& document.getElementById('toast').textContent.includes('视图已删除')", timeout=10000)


def test_today_panel_lists_tasks_and_jumps_to_project(ui):
    """跨项目待办：点开能看到任务，点一条直接跳到它的项目"""
    page, api, _ = ui
    p = make_project(api, "今日面板项目")
    api.post(f"/api/projects/{p['id']}/tasks", {"title": "今日可做任务", "status": "待办", "priority": "高"})
    page.reload(wait_until="domcontentloaded")

    page.click("[data-act='openToday']")
    page.wait_for_selector("#today-modal.show", timeout=10000)
    assert "今日可做任务" in page.locator("#today-body").inner_text()

    page.click("[data-act='gotoTask']")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    assert "今日面板项目" in page.locator("#view-detail").inner_text()


def test_task_statuses_land_in_their_own_column(ui):
    """五列各归各位。

    曾经 renderDetail 拿「项目」状态白名单（规划中/进行中/已暂停/已完成）去筛「任务」状态，
    于是「阻塞」不在集合里 → 被兜底塞进待办列，阻塞列永远是空的；
    而套件里此前没有任何用例看过 #col-blocked，所以每次回归都全绿。
    2026-10-01 加第五列「待审查」：Agent 干完活到人验收之间的中间态，应与其余状态一样落在自己的列。
    """
    page, api, _ = ui
    p = make_project(api, "五列归位项目")
    for title, st in [("待办的", "待办"), ("在做的", "进行中"),
                      ("等验收的", "待审查"), ("做完的", "已完成"), ("卡住的", "阻塞")]:
        api.post(f"/api/projects/{p['id']}/tasks", {"title": title, "status": st})
    page.reload(wait_until="domcontentloaded")

    page.click(".pcard:has-text('五列归位项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    for col, title in (("col-todo", "待办的"), ("col-doing", "在做的"),
                       ("col-review", "等验收的"), ("col-done", "做完的"), ("col-blocked", "卡住的")):
        page.wait_for_selector("#" + col + " .tcard", timeout=10000)
        text = page.locator("#" + col).inner_text()
        assert title in text, f"「{title}」应落在 #{col}，该列实际内容：{text[:80]}"


def test_delete_task_from_task_modal(ui):
    """任务只有编辑入口、没有删除入口 —— 后端 DELETE /api/tasks/{tid} 早就有了，UI 却点不到"""
    page, api, _ = ui
    p = make_project(api, "可删任务项目")
    tid = api.post(f"/api/projects/{p['id']}/tasks", {"title": "多余的任务"})[1]["id"]
    page.reload(wait_until="domcontentloaded")
    page.click(".pcard:has-text('可删任务项目')")
    page.wait_for_selector("#col-todo .tcard", timeout=10000)

    page.click("#col-todo .tcard")
    page.wait_for_selector("#task-modal.show", timeout=10000)
    page.wait_for_selector("#t-delete:visible", timeout=10000)

    # 新建态不给删除入口（没有可删的对象，也容易误点）
    page.click("#task-modal [data-act='closeModal']")
    page.click("[data-act='openTaskModal']")
    page.wait_for_selector("#task-modal.show", timeout=10000)
    assert not page.locator("#t-delete").is_visible(), "新建任务时不该出现删除按钮"
    page.click("#task-modal [data-act='closeModal']")

    page.click("#col-todo .tcard")
    page.wait_for_selector("#task-modal.show", timeout=10000)
    seen: list[str] = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    page.click("[data-act='deleteTask']")
    page.wait_for_selector("#col-todo .tcard", state="detached", timeout=15000)
    assert seen, "删除任务没有二次确认"
    assert api.get(f"/api/projects/{p['id']}")[1]["tasks"] == []


def test_ai_action_type_cannot_inject_markup(ai_ui):
    """AI 返回的 op type 会被拼进弹窗标签：不在白名单里也必须只是文字，不能变成节点"""
    page, api, stub, _ = ai_ui
    pid = make_project(api, "指令注入靶")["id"]
    stub.push_json({"actions": [{"type": '"><img id=tag-probe src=x>',
                                 "project_id": pid, "title": "注入探针"}], "reply": "r"})
    open_header_menu(page, "openAICmd")
    page.wait_for_selector("#cmd-input", timeout=10000)
    page.fill("#cmd-input", "随便下个指令")
    page.click("#cmd-send-btn")
    page.wait_for_selector("#cmd-exec-row:visible", timeout=15000)
    assert page.locator("#tag-probe").count() == 0, "模型返回的 type 被当 HTML 渲染了"
    text = page.locator("#cmd-chat").inner_text()
    assert "未知操作" in text, f"没走到未知 type 的渲染分支，用例是空转：{text[:150]}"
    assert "注入探针" in text, "确认列表里没有这条操作，用例同样是空转"


def test_ai_suggested_priority_cannot_inject_markup(ai_ui):
    """提炼建议里的优先级会拼进 class 名（pri-高）：过白名单后只能是 高/中/低"""
    page, api, stub, _ = ai_ui
    make_project(api, "提炼注入靶")
    stub.push_json({
        "summary": "s", "next_action": "n",
        "new_tasks": [{"title": "带毒优先级的任务",
                       "priority": '"><img id=pri-probe src=x>'}],
    })
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.click("[data-act='openSummarize']")
    page.wait_for_selector("#summarize-text", timeout=10000)
    page.fill("#summarize-text", "决定用 CI 部署，回滚步骤写进文档，下周一交付。" * 2)
    page.click("#summarize-btn")
    page.wait_for_selector("#summarize-result:visible", timeout=20000)
    assert page.locator("#pri-probe").count() == 0, "AI 给的优先级被当 HTML 渲染了"
    assert "带毒优先级的任务" in page.locator("#summarize-result").inner_text()


def test_package_modal_shows_context_budget(ui):
    """交接包弹窗要报"约 N 行 / X KB"，超阈值变红：模型读不完的东西不该越长越负责"""
    page, api, _ = ui
    p = make_project(api, "预算条项目", background="背景说明一行\n" * 400)
    page.reload(wait_until="domcontentloaded")
    page.click(f".pcard:has-text('预算条项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)

    page.click("[data-act='openHandoffPackage']")
    page.wait_for_selector("#package-modal.show", timeout=15000)
    text = page.locator("#package-budget").inner_text()
    assert "行" in text and "KB" in text, text
    assert page.locator("#package-content").input_value().count("\n") > 300
    # 过载档要变红（inline style 上就是 var(--red)，不依赖计算样式的写法差异）
    page.wait_for_function(
        "() => document.getElementById('package-budget').style.color === 'var(--red)'",
        timeout=5000)


def test_package_budget_stays_quiet_for_short_package(ui):
    """正常长度不该在弹窗里堆一句废话"""
    page, api, _ = ui
    make_project(api, "短包项目", background="就一行背景。")
    page.reload(wait_until="domcontentloaded")
    page.click(".pcard:has-text('短包项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.click("[data-act='openHandoffPackage']")
    page.wait_for_selector("#package-modal.show", timeout=15000)
    text = page.locator("#package-budget").inner_text()
    assert "行" in text and "KB" in text, text
    assert "过长" not in text and "偏长" not in text, text
    assert page.locator("#package-budget").evaluate("e => e.style.color") == "var(--text-faint)"


def test_reminder_banner_does_not_break_viewport_height(ui):
    """横幅出现时页面应当仍然恰好占满视口：以前两个视图按 calc(100vh - 定值) 算高度，
    横幅一出现就多出一条页面级滚动条，看板最底下一排被裁到看不见。"""
    from datetime import date, timedelta

    page, api, _ = ui
    past = (date.today() - timedelta(days=3)).isoformat()
    p = make_project(api, "逾期布局项目")
    api.post(f"/api/projects/{p['id']}/tasks", {"title": "早该做完的", "due_date": past})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#remind-banner:visible", timeout=15000)

    var = page.evaluate("getComputedStyle(document.documentElement)"
                        ".getPropertyValue('--banner-h')").strip()
    assert var.endswith("px") and float(var[:-2]) > 20, f"横幅高度没写回变量：{var!r}"
    overflow = page.evaluate("document.documentElement.scrollHeight - window.innerHeight")
    assert overflow <= 1, f"横幅出现后整页溢出 {overflow}px（第二条滚动条）"

    page.click("[data-act='dismissReminder']")
    page.wait_for_selector("#remind-banner", state="hidden", timeout=10000)
    assert page.evaluate("getComputedStyle(document.documentElement)"
                         ".getPropertyValue('--banner-h')").strip() == "0px"
    assert page.evaluate("document.documentElement.scrollHeight - window.innerHeight") <= 1


def test_detail_view_skips_rebuilding_the_hidden_overview(ui):
    """详情页里每次保存任务/改状态都会调 loadOverview()。
    以前它无条件把 N 张项目卡全删全建 —— 那正是隐藏中的视图，纯白干。"""
    page, api, _ = ui
    make_project(api, "延迟渲染项目")
    page.reload(wait_until="domcontentloaded")
    page.click(".pcard:has-text('延迟渲染项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    card = page.locator(".pcard:has-text('延迟渲染项目')")
    before = card.text_content()

    page.click("[data-act='openTaskModal']")
    page.fill("#t-title", "在详情里加的任务")
    page.click("[data-act='saveTask']")
    page.wait_for_selector("#col-todo .tcard:has-text('在详情里加的任务')", timeout=15000)
    # 隐藏元素取文本要用 text_content()，inner_text() 会返回空
    assert card.text_content() == before, "总览还藏着，项目卡却被重建了一遍"

    page.click("[data-act='showOverview']")
    page.wait_for_selector("#view-detail", state="hidden", timeout=10000)
    # 补渲染是**异步**的：showOverview 里 loadOverview() 要等一次 /api/projects 回来才重建卡片。
    # 以前这里点完立刻读文本，等于赌"本机 fetch 比断言快" —— 2026-10-18 的 CI run
    # （push fa9bfac）就是这么红的：本地全绿、runner 上慢一拍读到旧数据。
    # 判据改成"等到它真的补渲染了"，与机器快慢无关；没等到就让下面那条断言给出人话的错误。
    try:
        page.wait_for_function(
            """(before) => {
                 const el = [...document.querySelectorAll('.pcard')]
                   .find(c => c.textContent.includes('延迟渲染项目'));
                 return !!el && el.textContent !== before;
               }""",
            arg=before, timeout=15000)
    except Exception:                                          # noqa: BLE001
        pass
    after = page.locator(".pcard:has-text('延迟渲染项目')").text_content()
    assert after != before, "切回总览时没有补渲染，看到的是旧数据"
    assert "在详情里加的任务" not in (after or "")   # 卡片只显示统计不显示标题，顺手确认没渲染串


def test_offline_changes_appear_when_the_window_is_shown_again(ui):
    """MCP 服务是另一个进程，桌面端也可能开着第二个窗口：
    别人改了看板，切回来得看得见，而不是非要手点刷新。"""
    page, api, _ = ui
    p = make_project(api, "外部改动项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard:has-text('外部改动项目')", timeout=15000)

    api.put(f"/api/projects/{p['id']}", {"name": "外部改动项目", "status": "进行中",
                                         "progress": 66, "priority": "高"})
    assert "66%" not in page.locator(".pcard:has-text('外部改动项目')").text_content(), \
        "界面已经自己变了，这条用例就没意义了"

    page.evaluate("""() => {
      Object.defineProperty(document, 'hidden', { value: false, configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    page.wait_for_selector(".pcard:has-text('66%')", timeout=15000)


def test_modal_reopens_with_animation_and_esc_closes_it(ui):
    page, api, _ = ui
    make_project(api, "关闭路径项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    page.click("[data-act='openProjectModal']")
    page.wait_for_selector("#proj-modal.show", timeout=10000)
    box = page.locator("#proj-modal .modal")
    assert box.evaluate("e => e.style.animation") == "", "打开时被留下了 animation:none"
    page.keyboard.press("Escape")
    page.wait_for_selector("#proj-modal", state="hidden", timeout=10000)

    page.click("[data-act='openProjectModal']")
    page.wait_for_selector("#proj-modal.show", timeout=10000)
    assert page.locator("#proj-modal").is_visible()

    # 点遮罩空白处关闭（点在弹窗本体上不该关）
    page.locator("#proj-modal .modal").click(position={"x": 5, "y": 5})
    assert page.locator("#proj-modal").is_visible(), "点弹窗内部不该关掉"
    page.locator("#proj-modal").click(position={"x": 5, "y": 5})
    page.wait_for_selector("#proj-modal", state="hidden", timeout=10000)


def test_enter_key_opens_the_focused_card(ui):
    page, api, _ = ui
    p = make_project(api, "键盘打开项目")
    api.post(f"/api/projects/{p['id']}/tasks", {"title": "键盘打开任务"})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard:has-text('键盘打开项目')", timeout=15000)

    page.locator(".pcard:has-text('键盘打开项目')").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.wait_for_selector("#col-todo .tcard", timeout=10000)

    page.locator("#col-todo .tcard").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#task-modal.show", timeout=10000)
    assert page.input_value("#t-title") == "键盘打开任务"


def test_closing_command_modal_abandons_pending_actions(ai_ui):
    """关掉指令窗口=放弃这一批操作。以前 Esc 直接摘 .show，
    pendingActions 还留着，重开窗口点「确认执行」就会执行上一次的解析结果。"""
    page, api, stub, _ = ai_ui
    make_project(api, "放弃操作项目")
    stub.push_json({"actions": [{"type": "create_project", "name": "不该被建出来"}], "reply": "r"})
    open_header_menu(page, "openAICmd")
    page.wait_for_selector("#cmd-input", timeout=10000)
    page.fill("#cmd-input", "随便建个项目")
    page.click("#cmd-send-btn")
    page.wait_for_selector("#cmd-exec-row:visible", timeout=15000)

    page.keyboard.press("Escape")
    page.wait_for_selector("#ai-cmd-modal", state="hidden", timeout=10000)

    open_header_menu(page, "openAICmd")
    page.wait_for_selector("#ai-cmd-modal.show", timeout=10000)
    assert not page.locator("#cmd-exec-row").is_visible(), "重开还挂着上一次的确认按钮"
    names = [x["name"] for x in api.get("/api/projects")[1]["projects"]]
    assert "不该被建出来" not in names


def test_custom_tags_are_visible_and_reset_between_projects(ui):
    """项目自带的自定义标签以前可能"在 selTags 里但界面上没有 chip"，用户看不见也取消不掉；
    切到另一个项目时上一次的自定义 chip 又留在原地可被点。"""
    page, api, _ = ui
    a = make_project(api, "标签甲项目", tags=["自定义甲"])
    make_project(api, "标签乙项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard:has-text('标签甲项目')", timeout=15000)

    page.click(".pcard:has-text('标签甲项目') [data-act='edit']")
    page.wait_for_selector("#proj-modal.show", timeout=10000)
    active = page.locator("#f-tag-chips .chip-tag.active")
    assert active.count() == 1 and "自定义甲" in active.first.inner_text(),         f"项目自带的自定义标签必须看得见：{active.all_inner_texts()}"
    assert page.locator("#f-tag-chips .chip-tag", has_text="自定义甲").count() == 1, "同名 chip 重复了"

    page.click("#proj-modal [data-act='closeModal']")
    page.click(".pcard:has-text('标签乙项目') [data-act='edit']")
    page.wait_for_selector("#proj-modal.show", timeout=10000)
    assert page.locator("#f-tag-chips .chip-tag.active").count() == 0, "上个项目的标签还高着"

    # 在乙上加一个自定义标签并保存：看得见的就必须是会被存的
    page.fill("#f-tag-input", "自定义乙")
    page.press("#f-tag-input", "Enter")
    assert page.locator("#f-tag-chips .chip-tag.active").all_inner_texts() == ["自定义乙"]
    page.fill("#f-name", "标签乙项目")
    page.click("[data-act='saveProject']")
    page.wait_for_selector("#proj-modal", state="hidden", timeout=10000)
    page.wait_for_selector(".pcard:has-text('标签乙项目')", timeout=15000)

    tags_a = api.get(f"/api/projects/{a['id']}")[1]["tags"]
    assert tags_a == ["自定义甲"], f"编辑乙却动了甲的标签：{tags_a}"


def test_session_scan_button_clears_loading_state(ui):
    """扫描到 0 条会话时以前直接 return，跳过收尾 → 按钮永久停在转圈态"""
    page, api, _ = ui
    open_header_menu(page, "openSessionCapture")
    page.wait_for_selector("#session-modal.show", timeout=10000)
    page.click("[data-act='scanSessions']")
    # 空结果那条分支以前直接 return、跳过收尾；无论扫到几条，收尾都必须执行
    page.wait_for_selector("#scan-btn:not(.ai-loading-btn)", timeout=30000)
    assert page.locator("#session-list").inner_text().strip() != ""


def test_enter_key_submits_the_two_forms(ui):
    page, api, _ = ui
    page.click("[data-act='openProjectModal']")
    page.wait_for_selector("#proj-modal.show", timeout=10000)
    page.fill("#f-name", "回车建的项目")
    page.press("#f-name", "Enter")
    page.wait_for_selector("#proj-modal", state="hidden", timeout=15000)
    names = [p["name"] for p in api.get("/api/projects")[1]["projects"]]
    assert "回车建的项目" in names

    page.click(".pcard:has-text('回车建的项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.click("[data-act='openTaskModal']")
    page.fill("#t-title", "回车加的任务")
    page.press("#t-title", "Enter")
    page.wait_for_selector("#task-modal", state="hidden", timeout=15000)
    page.wait_for_selector("#col-todo .tcard:has-text('回车加的任务')", timeout=15000)


def test_memory_inline_form_replaces_prompt(ui):
    page, api, _ = ui
    p = make_project(api, "记忆表单项目")
    page.reload(wait_until="domcontentloaded")
    page.click(".pcard:has-text('记忆表单项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)

    page.click("[data-act='toggleMemoryForm']")
    page.wait_for_selector("#memory-form", state="visible", timeout=10000)
    page.select_option("#memory-type", "坑")
    page.fill("#memory-text", "大文件导出必须流式写")
    page.press("#memory-text", "Enter")            # Enter 直接存（复用 data-enter 机制）
    page.wait_for_selector(".memory-item:has-text('大文件导出必须流式写')", timeout=15000)
    mems = api.get("/api/memories?pid=" + p["id"])[1]["memories"]
    assert [m["type"] for m in mems] == ["坑"] and mems[0]["source"] == "手动"

    page.click("[data-act='saveMemory']")          # 空内容不该发请求
    assert "先写一条内容" in page.locator("#toast").text_content()


def test_ctrl_k_steps_back_instead_of_stacking(ui):
    page, api, _ = ui
    make_project(api, "CtrlK 项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    page.click("[data-act='openProjectModal']")
    page.wait_for_selector("#proj-modal.show", timeout=10000)
    page.keyboard.press("Control+k")
    page.wait_for_selector("#proj-modal", state="hidden", timeout=10000)
    assert not page.locator("#global-search-modal").is_visible(), "搜索浮层被架在弹窗之上"

    page.keyboard.press("Control+k")
    page.wait_for_selector("#global-search-modal.show", timeout=10000)


def test_detail_error_state_offers_retry(ui_lenient):
    page, api, _ = ui_lenient
    make_project(api, "失败重试项目")
    page.reload(wait_until="domcontentloaded")
    page.evaluate("() => openDetail('不存在的项目id')")
    page.wait_for_selector("#detail-state", state="visible", timeout=10000)
    assert "打开失败" in page.locator("#detail-state").inner_text()
    assert page.locator("#detail-state [data-act='retryDetail']").count() == 1

    page.evaluate("() => setDetailState(null)")     # 成功路径要把遮罩收掉
    page.wait_for_selector("#detail-state", state="hidden", timeout=10000)


def test_overview_failure_shows_retry_not_a_blank_grid(ui_lenient):
    page, api, _ = ui_lenient
    page.route("**/api/projects", lambda r: r.abort())
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#grid [data-act='loadOverview']", timeout=15000)
    assert "加载失败" in page.locator("#grid").inner_text()
    page.unroute("**/api/projects")


def test_modal_focus_trap_and_return(ui):
    page, api, _ = ui
    make_project(api, "焦点项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    opener = page.locator("header [data-act='openProjectModal']")
    opener.click()
    page.wait_for_selector("#proj-modal.show", timeout=10000)
    # 打开后焦点在弹窗内（优先第一个输入框）
    assert page.evaluate("() => document.getElementById('proj-modal').contains(document.activeElement)")
    assert page.evaluate("() => document.activeElement.id") == "f-name"

    # Tab 一整圈不许跑出弹窗
    for _ in range(25):
        page.keyboard.press("Tab")
    assert page.evaluate("() => document.getElementById('proj-modal').contains(document.activeElement)"), \
        "焦点从最后一个控件跑出去了"
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("() => document.getElementById('proj-modal').contains(document.activeElement)")

    page.keyboard.press("Escape")
    page.wait_for_selector("#proj-modal", state="hidden", timeout=10000)
    # 用参数传元素：别在 evaluate 的字符串里再套引号，转义极易写坏
    assert page.evaluate("(el) => document.activeElement === el", opener.element_handle()), "关闭后焦点没还给触发它的按钮"


def test_long_modal_reopens_at_its_previous_scroll(ui):
    page, api, _ = ui
    page.click("header [data-act='openAISettings']")
    page.wait_for_selector("#ai-settings-modal.show", timeout=10000)
    mask = page.locator("#ai-settings-modal")
    page.evaluate("() => { const m = document.getElementById('ai-settings-modal');"
                  "m.scrollTop = m.scrollHeight; }")
    scrolled = mask.evaluate("e => e.scrollTop")
    assert scrolled > 40, f"这个弹窗不够长，测不出滚动记忆（scrollTop={scrolled}）"

    page.keyboard.press("Escape")
    page.wait_for_selector("#ai-settings-modal", state="hidden", timeout=10000)
    page.click("header [data-act='openAISettings']")
    page.wait_for_selector("#ai-settings-modal.show", timeout=10000)
    assert mask.evaluate("e => e.scrollTop") >= scrolled - 5, "重开回到了顶部，滚动位置没记住"

    # 钉住的标题在滚到底时仍然在视口里
    head_top = page.locator("#ai-settings-modal .mhead").bounding_box()["y"]
    mask_box = mask.bounding_box()
    assert head_top >= mask_box["y"] - 1 and head_top <= mask_box["y"] + 60, \
        f"标题滚出视野了（head y={head_top}, mask y={mask_box['y']}）"


# ---------------- 2026-10-06 竞品吸取轮：验收标准 / 待拍板 / 记忆时效 / journal 折叠 ----------------

def test_acceptance_roundtrip_card_badge_and_modal_fill(ui):
    """验收标准：卡片上有计数徽章、任务弹窗按行回填、加一条保存后徽章跟着涨"""
    page, api, _ = ui
    p = make_project(api, "验收流转项目")
    api.post(f"/api/projects/{p['id']}/tasks",
             {"title": "带验收的任务", "acceptance": ["全部用例通过", "README 补全"]})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.click(".pcard:has-text('验收流转项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)

    card = page.locator(".tcard:has-text('带验收的任务')")
    assert "☑ 2" in card.inner_text(), "卡片上没有验收计数徽章"

    card.click()
    page.wait_for_selector("#task-modal.show", timeout=10000)
    assert page.locator("#t-acceptance").input_value() == "全部用例通过\nREADME 补全", \
        "编辑时没有按行回填验收标准"

    page.fill("#t-acceptance", "全部用例通过\nREADME 补全\n界面人工核对")
    page.click("[data-act='saveTask']")
    page.wait_for_function(
        "() => document.getElementById('toast').textContent.includes('已更新')", timeout=10000)
    page.wait_for_selector("#task-modal", state="hidden", timeout=10000)
    assert "☑ 3" in page.locator(".tcard:has-text('带验收的任务')").inner_text()


def test_inbox_badge_modal_and_jump(ui):
    """待拍板：徽标计数、弹窗分组列出、点一条跳到项目"""
    from datetime import datetime, timedelta
    page, api, _ = ui
    p = make_project(api, "拍板面板项目")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    api.post(f"/api/projects/{p['id']}/tasks", {"title": "逾期任务", "due_date": yesterday})
    api.post(f"/api/projects/{p['id']}/tasks", {"title": "验收任务", "status": "待审查"})
    page.reload(wait_until="domcontentloaded")

    page.wait_for_function(
        "() => document.getElementById('inbox-badge') "
        "&& document.getElementById('inbox-badge').style.display !== 'none'", timeout=15000)
    assert page.locator("#inbox-badge").inner_text() == "2"

    page.click("[data-act='openInbox']")
    page.wait_for_selector("#inbox-modal.show", timeout=10000)
    body = page.locator("#inbox-body").inner_text()
    assert "逾期任务" in body and "验收任务" in body, body

    page.click("#inbox-body .tcard:has-text('验收任务')")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    assert "拍板面板项目" in page.locator("#view-detail").inner_text()


def test_expired_memory_is_marked_in_the_panel(ui):
    """过期记忆：面板里划线 + ⚠️ 标注，没设时效的不受影响"""
    from datetime import datetime, timedelta
    page, api, _ = ui
    p = make_project(api, "时效记忆面板项目")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    api.post("/api/memories", {"pid": p["id"], "type": "事实",
                               "text": "已经过期的方案", "expires": yesterday})
    api.post("/api/memories", {"pid": p["id"], "type": "约定", "text": "长期有效的约定"})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.click(".pcard:has-text('时效记忆面板项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.wait_for_selector(".memory-item", timeout=10000)

    expired = page.locator(".memory-item.expired:has-text('已经过期的方案')")
    assert expired.count() == 1, "过期记忆没有拿到 .expired 样式"
    assert "⚠️ 已过期" in expired.inner_text()
    fresh = page.locator(".memory-item:has-text('长期有效的约定')")
    assert "expired" not in (fresh.get_attribute("class") or ""), "未设时效的记忆不该被标过期"

    # 条目级尺寸徽章（2026-10-11 对标 claude-mem 渐进式披露的最小落地）：
    # 面板上要看得到"这一条读进上下文占多少"，且数字必须与后端 text_size 同口径（按正文长度）
    badge = expired.locator(".m-size")
    assert badge.count() == 1, "记忆行里没有尺寸徽章"
    assert badge.inner_text() == "≈7 字", f"「已经过期的方案」应为 7 字：{badge.inner_text()}"
    assert "tok" in (badge.get_attribute("title") or ""), "token 估算应放在 title 里（近似值，不喧哗）"
    _, mrows = api.get(f"/api/memories?pid={p['id']}")
    row = next(m for m in mrows["memories"] if m["text"] == "已经过期的方案")
    assert row["chars"] == len("已经过期的方案"), "接口给的 chars 应与正文长度一致"


def test_journal_overflow_folds_in_task_modal(ui):
    """过程记录超上限后，任务弹窗里能看到折叠摘要行（不是无声消失）"""
    page, api, _ = ui
    p = make_project(api, "折叠面板项目")
    t = api.post(f"/api/projects/{p['id']}/tasks", {"title": "高频流转任务"})[1]
    for i in range(55):                      # 默认 MAX_JOURNAL=50，55 次状态流转必然溢出
        api.put(f"/api/tasks/{t['id']}",
                {"title": "高频流转任务", "status": "进行中" if i % 2 == 0 else "待办"})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.click(".pcard:has-text('折叠面板项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.click(".tcard:has-text('高频流转任务')")
    page.wait_for_selector("#task-modal.show", timeout=10000)

    folded = page.locator("#t-journal-list .j-folded")
    assert folded.count() == 1, "折叠摘要没有渲染"
    assert "已折叠" in folded.inner_text() and "状态：" in folded.inner_text()
    # 折叠摘要的 time 是日期区间（2026-XX-XX~2026-XX-XX），不能按普通条目 slice(5,16)
    # 截成 [09-01~2026-] 这样的乱码（2026-10-06 审查发现）
    assert "[2026-" in folded.inner_text() and "~2026-" in folded.inner_text(), folded.inner_text()
    rows = page.locator("#t-journal-list .j-row")
    assert rows.count() <= 50, "折叠后总条数必须压回上限内"


# ---------------- 路线图 #3：入口文件写盘 / 备份恢复动作 / 健康趋势图 ----------------

def test_agentsmd_preview_then_write_nine_entries(ui, tmp_path):
    """入口文件：预览不落盘 → 写盘真的落 9 个（含第 9 个 .claude/skills SKILL.md）。
    ui 夹具没配 LLM，生成走本地模板兜底 —— 这条链路本身就该断网可用。"""
    page, api, _ = ui
    make_project(api, "入口文件落盘项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.click(".pcard:has-text('入口文件落盘项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)

    page.click("[data-act='openAgentsMD']")
    page.wait_for_selector("#agentsmd-modal.show", timeout=10000)
    repo = tmp_path / "entry-repo"
    repo.mkdir()

    # ① 纯预览：dir 留空 = 只预览不写盘（后端语义：填了 dir 连预览都会落盘，带覆盖保护）
    page.click("[data-act='previewAgentFiles']")
    page.wait_for_function(
        "() => document.getElementById('agentsmd-files').style.display === 'block'", timeout=15000)
    files_box = page.locator("#agentsmd-files").inner_text()
    assert "预览（未写盘）" in files_box, files_box
    assert ".claude/skills/ai-kanban/SKILL.md" in files_box, "第 9 个投影没出现在预览里"
    assert not (repo / "AGENTS.md").exists(), "纯预览不该落盘"

    # ② 写盘：9 个入口全部落到位
    page.fill("#agentsmd-dir", str(repo))
    page.click("[data-act='writeAgentFiles']")
    page.wait_for_function(
        "() => document.getElementById('toast').textContent.includes('已写入 9 个文件')",
        timeout=15000)
    assert (repo / "AGENTS.md").exists()
    assert "@AGENTS.md" in (repo / "CLAUDE.md").read_text(encoding="utf-8"), "CLAUDE.md 必须是桥接"
    skill = repo / ".claude" / "skills" / "ai-kanban" / "SKILL.md"
    assert skill.exists(), "嵌套目录的 Skill 入口没有写出"
    assert skill.read_text(encoding="utf-8").startswith("---\n"), "SKILL.md 缺 frontmatter"


def test_backup_then_restore_roundtrip(ui):
    """备份与恢复面板的实际动作：拍快照 → 快照之后再造一个项目 → 恢复 → 它消失"""
    page, api, _ = ui
    make_project(api, "恢复前项目甲")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)

    page.click("header [data-act='openAISettings']")
    page.wait_for_selector("#ai-settings-modal.show", timeout=10000)
    page.click("[data-act='backupNow']")
    page.wait_for_function(
        "() => document.getElementById('toast').textContent.includes('备份成功')", timeout=15000)
    page.click("[data-act='showRecoverable']")
    page.wait_for_function(
        "() => document.getElementById('recover-list').textContent.includes('恢复前项目甲')",
        timeout=15000)

    # 快照之后才建的项目乙：恢复后应该消失
    make_project(api, "恢复后才建的项目乙")
    page.click("#ai-settings-modal .mhead [data-act='closeModal']")   # 关掉再重开，让面板拿到新数据
    page.click("header [data-act='openAISettings']")
    page.wait_for_selector("#ai-settings-modal.show", timeout=10000)
    page.click("[data-act='showRecoverable']")
    page.wait_for_function(
        "() => document.getElementById('recover-list').textContent.includes('2 个项目')",
        timeout=15000)

    page.on("dialog", lambda d: d.accept())          # 恢复前有 confirm
    # 恢复来源有多个（last-good / 手动快照…），必须点「手动快照」那一个 ——
    # 点第一个恢复的是 last-good（当前数据的别名），那就不是"回滚到快照"了
    page.click("#recover-list [data-arg^='backup_']")
    page.wait_for_function(
        "() => document.getElementById('toast').textContent.includes('已恢复 1 个项目')",
        timeout=15000)

    page.click("#ai-settings-modal .mhead [data-act='closeModal']")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    assert page.locator(".pcard:has-text('恢复前项目甲')").count() == 1
    assert page.locator(".pcard:has-text('恢复后才建的项目乙')").count() == 0, \
        "恢复没有回滚：快照之后建的项目还在"


def test_trends_modal_renders_chart_after_a_report(ai_ui):
    """健康趋势：有过一次监管报告后，仪表盘的趋势按钮能画出 SVG 折线"""
    page, api, stub, _ = ai_ui
    pid = make_project(api, "趋势项目")["id"]
    stub.push_json({
        "summary": "节奏正常",
        "insights": [{"id": pid, "name": "趋势项目", "health": 76, "reason": "推进稳定",
                      "risks": [], "suggestions": []}],
        "global_suggestions": [],
    })
    open_header_menu(page, "openReport")
    page.wait_for_selector("#report-panel.show", timeout=15000)
    page.locator("#report-body").locator("text=节奏正常").first.wait_for(timeout=15000)
    # 遮罩与返回按钮都是 closeReport，必须点面板里那个 —— 点遮罩会被面板挡住点不到
    page.click("#report-panel .r-head [data-act='closeReport']")
    page.wait_for_selector("#report-panel", state="hidden", timeout=10000)

    page.click("#dash [data-act='openTrends']")
    page.wait_for_selector("#trend-modal.show", timeout=10000)
    page.wait_for_function(
        "() => !document.querySelector('#trend-body .spinner')", timeout=15000)
    assert page.locator("#trend-body .trend-wrap svg").count() >= 1, "趋势图没有画出来"
    assert "趋势项目" in page.locator("#trend-body").inner_text()


def test_handoff_page_download_is_self_contained(ui, tmp_path):
    """接手页下载：拿到的文件含依赖图与任务清单，且不引任何第三方资源（离线可用）"""
    page, api, _ = ui
    p = make_project(api, "接手页下载项目", handoff_note="先读交接说明再动手")
    t1 = api.post(f"/api/projects/{p['id']}/tasks", {"title": "前置任务"})[1]
    api.post(f"/api/projects/{p['id']}/tasks",
             {"title": "后置任务", "depends_on": [t1["id"]], "acceptance": ["用例全绿"]})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.click(".pcard:has-text('接手页下载项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)

    target = tmp_path / "handoff.html"
    with page.expect_download(timeout=15000) as dl:
        page.click("[data-act='downloadHandoffPage']")
    dl.value.save_as(str(target))

    html = target.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>"), "拿到的不是完整 HTML 文件"
    assert "接手页下载项目" in html and "先读交接说明再动手" in html
    assert "<svg" in html, "依赖图没有内联渲染"
    assert "☐ 用例全绿" in html, "验收标准没进接手页"
    # 自包含硬约定：不引任何第三方资源（数据不出本机）
    third_party = [ln for ln in html.splitlines()
                   if re.search(r'(src|href)="https?://', ln)
                   and "127.0.0.1" not in ln]
    assert not third_party, f"接手页里有第三方外链：{third_party[:3]}"


def test_session_capture_full_flow_to_project(ai_ui, tmp_path):
    """会话捕获全流程：扫描发现本机会话 → 预览 → AI 提炼 → 人工确认入库（任务/下一步/记忆）"""
    page, api, stub, _ = ai_ui
    src = tmp_path / "captured_sessions"
    src.mkdir()
    (src / "chat.json").write_text(json.dumps([
        {"say": "user", "text": "我们决定登录方案换成 JWT"},
        {"say": "assistant", "text": "好的，先改签发端再改校验端"},
    ], ensure_ascii=False), encoding="utf-8")
    _, cfg = api.put("/api/ai/config", {"session_sources": [
        {"name": "测试源", "path": str(src), "type": "text"}]})
    pid = make_project(api, "会话入库项目")["id"]
    stub.push_json({
        "summary": "决定换 JWT 方案",
        "new_tasks": [{"title": "迁移登录到 JWT", "priority": "高"}],
        "next_action": "先改签发端",
        "handoff_note_updates": "登录方案已定为 JWT",
        "memories": [{"type": "决策", "text": "登录用 JWT"}],
    })
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("[data-act='openProjectModal']", timeout=20000)

    open_header_menu(page, "openSessionCapture")
    page.wait_for_selector("#session-modal.show", timeout=10000)
    page.wait_for_selector(".sess-item", timeout=15000)
    # 本机真实的 Claude Code 会话也会被扫进来，必须点名自己的那个文件
    page.click(".sess-item:has-text('chat.json')")
    page.wait_for_function(
        "() => document.getElementById('session-preview').value.includes('JWT')", timeout=15000)
    page.select_option("#session-pid", pid)
    page.click("[data-act='ingestSession']")
    page.wait_for_function(
        "() => document.getElementById('session-result').textContent.includes('迁移登录到 JWT')",
        timeout=20000)

    page.on("dialog", lambda d: d.accept())      # 应用前有 confirm
    page.click("[data-act='applySummarize'][data-arg='session-modal']")
    page.wait_for_function(
        "() => document.getElementById('toast').textContent.includes('应用完成')", timeout=15000)

    _, proj = api.get(f"/api/projects/{pid}")
    titles = [t["title"] for t in proj["tasks"]]
    assert "迁移登录到 JWT" in titles, "提炼任务没入库"
    assert proj["next_action"] == "先改签发端", "下一步行动没更新"
    _, mems = api.get(f"/api/memories?pid={pid}")
    assert any(m["text"] == "登录用 JWT" for m in mems["memories"]), "决策记忆没沉淀"


def test_stats_panel_counts_review_only_project(ui):
    """P1 回归：项目只有「待审查」任务时，统计面板必须报 1 个任务而不是"还没有任务"
    （面板求和曾漏第五列，2026-10-06 审查发现）"""
    page, api, _ = ui
    p = make_project(api, "纯待审查项目")
    api.post(f"/api/projects/{p['id']}/tasks", {"title": "待验收的任务", "status": "待审查"})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.click(".pcard:has-text('纯待审查项目')")
    page.wait_for_selector("#view-detail.active", timeout=10000)
    page.wait_for_function(
        "() => document.getElementById('stats-panel').style.display === 'block' "
        "&& !document.getElementById('stats-body').textContent.includes('统计读取中')",
        timeout=15000)
    # 「共 N 个任务」在 note 里，分布与图例在 body 里 —— 两处都要对
    assert page.locator("#stats-note").inner_text() == "共 1 个任务",         page.locator("#stats-note").inner_text()
    body = page.locator("#stats-body").inner_text()
    assert "待审查1" in body, body


def test_milestone_row_delete_works_after_earlier_deletion(ui):
    """P2 回归：删过一行后剩余行的 data-arg 与 DOM 下标错位，第二个 ✕ 必须仍删它自己
    （按下标删会删错行，2026-10-06 审查发现）"""
    page, api, _ = ui
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("[data-act='openProjectModal']", timeout=20000)
    page.click("[data-act='openProjectModal']")
    page.wait_for_selector("#proj-modal.show", timeout=10000)
    page.click("[data-act='addMilestoneRow']")
    page.click("[data-act='addMilestoneRow']")
    rows = page.locator("#f-milestones .ms-row")
    rows.nth(0).locator(".ms-name").fill("里程碑甲")
    rows.nth(1).locator(".ms-name").fill("里程碑乙")
    rows.nth(0).locator("[data-act='removeMilestoneRow']").click()      # 删掉甲
    assert rows.count() == 1 and rows.nth(0).locator(".ms-name").input_value() == "里程碑乙"
    rows.nth(0).locator("[data-act='removeMilestoneRow']").click()      # 乙的 data-arg 仍是 1
    assert page.locator("#f-milestones .ms-row").count() == 0, \
        "第二个 ✕ 没删掉自己的行（按下标删的错位又回来了）"


def test_project_names_are_not_visually_truncated(fresh_server, browser):
    """文字在 DOM 里不等于用户看得见：CSS ellipsis 不改 innerText。"""
    names = [
        "示例项目 A · 后台系统",
        "示例项目 B · 内容流水线",
        "示例项目 C · 文本润色工具",
        "示例项目 D · 扫描器",
    ]
    for name in names:
        make_project(fresh_server, name)

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(fresh_server.base + "/", wait_until="domcontentloaded")
        page.wait_for_selector(".pcard", timeout=15000)
        for width in (1280, 1440, 1920):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(150)
            bad = page.eval_on_selector_all(
                ".pcard .name",
                "els => els.filter(e => e.scrollWidth > e.clientWidth + 1).length",
            )
            assert bad == 0, f"{width}px 下有 {bad} 个项目名被视觉截断"
    finally:
        ctx.close()


def test_token_mode_desktop_ui_can_write(token_server, browser):
    """开 KANBAN_TOKEN 后，桌面 UI 也必须能写；否则安全加固等于把界面变只读。"""
    ctx = browser.new_context(viewport={"width": 1280, "height": 820})
    page = ctx.new_page()
    try:
        page.goto(token_server.base + "/", wait_until="domcontentloaded")
        page.wait_for_selector("[data-act='openProjectModal']", timeout=15000)
        page.click("[data-act='openProjectModal']")
        page.fill("#f-name", "Token 模式项目")
        page.click("[data-act='saveProject']")
        page.wait_for_selector(".pcard:has-text('Token 模式项目')", timeout=15000)
        status, data = token_server.get("/api/projects")
        assert status == 200
        assert any(p["name"] == "Token 模式项目" for p in data["projects"])
    finally:
        ctx.close()

