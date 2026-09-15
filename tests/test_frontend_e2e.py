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


def test_delete_project_asks_confirmation(ui):
    page, api, _ = ui
    make_project(api, "待删项目")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".pcard", timeout=15000)
    page.locator(".pcard").first.click()
    page.wait_for_selector("#view-detail.active", timeout=10000)

    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    page.click("[data-act='deleteProject']")
    page.wait_for_selector(".pcard", state="detached", timeout=15000)
    assert seen, "删除没有二次确认（真实使用里这是误删的唯一防线）"
    assert api.get("/api/projects")[1]["projects"] == []


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
