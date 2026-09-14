# -*- coding: utf-8 -*-
"""基础接口：项目/任务 CRUD、交接导出、删除后 404"""
from __future__ import annotations

import pytest


@pytest.fixture
def server(app_client):
    """进程内客户端（原为子进程实例）：更快，且能被覆盖率统计"""
    return app_client


def test_health_and_index(server):
    status, body = server.get("/api/health")
    assert status == 200 and body["ok"] is True
    # 版本号是判断"dist 里的 exe 是否落后于源码"的唯一依据，不能缺
    assert body["version"] and body["build_time"]
    assert body["frozen"] is False          # 测试里一定跑在源码模式下

    status, html = server.get("/")
    assert status == 200 and "AI 交接看板" in html


def test_project_list_shape(server):
    status, data = server.get("/api/projects")
    assert status == 200 and isinstance(data["projects"], list)


def test_project_crud_roundtrip(server):
    status, project = server.create_project(
        "冒烟测试项目", status="规划中", progress=10, priority="高",
        tags=["测试"], repo_path=r"D:\test\demo",
        background="用于 API 冒烟测试的项目",
        handoff_note="这是一个测试交接说明，包含中文与标点！",
        next_action="跑通冒烟测试",
    )
    assert status == 201 and project["name"] == "冒烟测试项目"
    pid = project["id"]

    status, detail = server.get(f"/api/projects/{pid}")
    assert status == 200 and detail["name"] == "冒烟测试项目"

    status, task = server.post(f"/api/projects/{pid}/tasks", {
        "title": "联调登录模块", "desc": "前后端联调，验收标准为可正常登录",
        "status": "待办", "priority": "高", "agent": "cline",
    })
    assert status == 201 and task["title"] == "联调登录模块"
    tid = task["id"]

    status, updated = server.put(f"/api/tasks/{tid}", {
        "title": "联调登录模块", "desc": "前后端联调，验收标准为可正常登录",
        "status": "进行中", "priority": "高", "agent": "cline",
    })
    assert status == 200 and updated["status"] == "进行中"

    status, md = server.get(f"/api/export/{pid}")
    assert status == 200 and "冒烟测试项目" in md and "交接说明" in md

    assert server.delete(f"/api/tasks/{tid}")[0] == 200
    assert server.delete(f"/api/projects/{pid}")[0] == 200
    assert server.get(f"/api/projects/{pid}")[0] == 404


def test_stats_and_readiness(server):
    status, stats = server.get("/api/stats")
    assert status == 200
    for key in ("project_total", "status_dist", "task_dist", "total_tasks",
                "blocked_tasks", "avg_progress", "avg_readiness", "readiness"):
        assert key in stats


def test_export_all_markdown(server):
    status, md = server.get("/api/export-all")
    assert status == 200 and "全项目交接包" in md


def test_sample_data_is_desensitized(server):
    """示例数据不能含真实项目名/本机路径 —— 它会随 exe 分发"""
    _, data = server.get("/api/projects")
    blob = str(data)
    for leak in ("sqli-scanner", "longmao", "QuAiWei", "墨匠", "EduSmart"):
        assert leak not in blob, f"示例数据仍泄露 {leak}"


# ---------------- 截止日期 ----------------

def test_task_due_date_roundtrip(server):
    status, proj = server.create_project("截止日期项目")
    pid = proj["id"]
    status, task = server.post(f"/api/projects/{pid}/tasks", {
        "title": "有截止的任务", "due_date": "2026-12-31",
    })
    assert status in (200, 201) and task["due_date"] == "2026-12-31"
    # 非法日期被清洗为空，不报错
    status, task2 = server.post(f"/api/projects/{pid}/tasks", {
        "title": "非法日期", "due_date": "明天下午",
    })
    assert status in (200, 201) and task2["due_date"] == ""
    # 更新截止日期
    status, updated = server.put(f"/api/tasks/{task['id']}", {
        "title": "有截止的任务", "due_date": "2027-01-15",
    })
    assert status == 200 and updated["due_date"] == "2027-01-15"


def test_stats_due_counters(server):
    from datetime import date, timedelta
    status, proj = server.create_project("临期逾期项目")
    pid = proj["id"]
    today = date.today()
    server.post(f"/api/projects/{pid}/tasks", {"title": "逾期任务", "due_date": (today - timedelta(days=3)).isoformat()})
    server.post(f"/api/projects/{pid}/tasks", {"title": "临期任务", "due_date": (today + timedelta(days=1)).isoformat()})
    server.post(f"/api/projects/{pid}/tasks", {"title": "远期任务", "due_date": (today + timedelta(days=30)).isoformat()})
    server.post(f"/api/projects/{pid}/tasks", {"title": "已完成不算逾期", "due_date": (today - timedelta(days=5)).isoformat(), "status": "已完成"})
    status, stats = server.get("/api/stats")
    assert status == 200
    assert stats["overdue_tasks"] >= 1, "逾期任务应被统计（已完成的不算）"
    assert stats["duesoon_tasks"] >= 1, "2 天内临期任务应被统计"


# ---------------- 全量数据导入导出 ----------------

def test_data_export_import_roundtrip(server):
    status, proj = server.create_project("迁移项目")
    pid = proj["id"]
    server.post(f"/api/projects/{pid}/tasks", {"title": "迁移任务", "due_date": "2026-12-01"})
    status, exported = server.get("/api/data/export")
    assert status == 200 and exported.get("app") == "ai-kanban"
    assert isinstance(exported.get("projects"), list) and len(exported["projects"]) >= 1
    import json as _json
    content = _json.dumps(exported, ensure_ascii=False)
    # 删掉后用导出内容恢复
    assert server.delete(f"/api/projects/{pid}")[0] == 200
    _, after_del = server.get("/api/projects")
    assert all(p["id"] != pid for p in after_del["projects"])
    status, r = server.post("/api/data/import", {"content": content})
    assert status == 200 and r.get("ok") is True and r.get("imported", 0) >= 1
    _, restored = server.get("/api/projects")
    assert any(p["id"] == pid for p in restored["projects"]), "导入后项目应恢复"
    status, detail = server.get(f"/api/projects/{pid}")
    assert status == 200 and detail["tasks"], "导入后任务应恢复"
    assert detail["tasks"][0]["title"] == "迁移任务"
    assert detail["tasks"][0]["due_date"] == "2026-12-01"


def test_data_import_rejects_garbage(server):
    assert server.post("/api/data/import", {"content": "不是json"})[0] == 400
    assert server.post("/api/data/import", {"content": "[1,2,3]"})[0] == 400
    assert server.post("/api/data/import", {"content": '{"projects": "不是列表"}'})[0] == 400
