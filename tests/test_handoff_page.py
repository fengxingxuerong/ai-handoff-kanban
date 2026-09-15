# -*- coding: utf-8 -*-
"""自包含接手页（/api/export/page/{pid}）与交接包里的实时直链

守住的底线：
- 页面必须自包含（不引 CDN、不联网），离线双击可用
- 项目名称等字段必须转义，不能把任务内容当 HTML 执行
- 直链只能是 127.0.0.1（数据不出本机）
"""
from __future__ import annotations

import pytest


@pytest.fixture
def project(app_client):
    """一个带背景/交接说明/下一步的项目"""
    status, p = app_client.create_project(
        "接手页测试项目", status="进行中", progress=40, priority="高",
        tags=["测试"], repo_path=r"D:\work\demo",
        background="用于验证接手页的项目背景",
        handoff_note="先跑测试再改代码；改动前读 AGENTS.md",
        next_action="补齐接手页的边界用例",
    )
    assert status == 201
    return p


def add_task(api, pid: str, title: str, depends_on=None, refs=None):
    body = {"title": title, "status": "待办", "priority": "中"}
    if depends_on:
        body["depends_on"] = depends_on
    if refs:
        body["refs"] = refs
    return api.request("POST", f"/api/projects/{pid}/tasks", body)


def test_page_is_complete_html(project, app_client):
    status, html = app_client.get(f"/api/export/page/{project['id']}")
    assert status == 200
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "接手页测试项目" in html
    assert "先跑测试再改代码" in html          # 交接说明
    assert "补齐接手页的边界用例" in html      # 下一步
    assert "用于验证接手页的项目背景" in html


def test_page_is_self_contained(project, app_client):
    """离线可用：不得引用任何外部资源"""
    _, html = app_client.get(f"/api/export/page/{project['id']}")
    lowered = html.lower()
    for bad in ("http://cdn", "https://cdn", "src=\"http", "href=\"http://", "@import"):
        assert bad not in lowered, f"接手页不该依赖外部资源：{bad}"


def test_page_escapes_html_in_fields(app_client):
    _, p = app_client.create_project("<script>alert(1)</script>", status="进行中")
    add_task(app_client, p["id"], "<img src=x onerror=alert(2)>")
    _, html = app_client.get(f"/api/export/page/{p['id']}")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x onerror=alert(2)>" not in html


def test_page_has_local_only_links(project, app_client):
    """实时直链只能指向本机 —— 数据不出本机是硬边界"""
    _, html = app_client.get(f"/api/export/page/{project['id']}")
    assert f"127.0.0.1:18320/api/context/{project['id']}" in html
    assert f"127.0.0.1:18320/api/export/page/{project['id']}" in html
    assert "0.0.0.0" not in html


def test_page_draws_dependency_graph(project, app_client):
    st1, t1 = add_task(app_client, project["id"], "先做这个")
    st2, t2 = add_task(app_client, project["id"], "再做这个", depends_on=[t1["id"]])
    assert st1 == 201 and st2 == 201
    _, html = app_client.get(f"/api/export/page/{project['id']}")
    assert "<svg" in html, "有依赖就要画出依赖图"
    assert "再做这个" in html


def test_page_says_no_deps_when_flat(project, app_client):
    add_task(app_client, project["id"], "独立任务甲")
    add_task(app_client, project["id"], "独立任务乙")
    _, html = app_client.get(f"/api/export/page/{project['id']}")
    assert "<svg" not in html
    assert "没有依赖关系" in html


def test_page_survives_empty_project(app_client):
    _, p = app_client.create_project("空项目", status="规划中")
    status, html = app_client.get(f"/api/export/page/{p['id']}")
    assert status == 200 and "空项目" in html


def test_page_404_for_unknown_project(app_client):
    status, _ = app_client.get("/api/export/page/不存在的id")
    assert status == 404


def test_markdown_carries_live_links(project, app_client):
    """Markdown 交接包同样要带直链：粘给智能体时它才知道有活数据可取"""
    _, md = app_client.get(f"/api/export/{project['id']}")
    assert "## 实时上下文" in md
    assert f"/api/context/{project['id']}" in md
    assert f"/api/export/page/{project['id']}" in md
