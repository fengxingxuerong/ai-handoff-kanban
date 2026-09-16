# -*- coding: utf-8 -*-
"""基础接口：项目/任务 CRUD、交接导出、删除后 404"""
from __future__ import annotations

import json

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


# ---------------- 全局任务搜索 ----------------

def test_global_task_search(server):
    status, proj = server.create_project("搜索项目")
    pid = proj["id"]
    server.post(f"/api/projects/{pid}/tasks", {"title": "登录模块联调", "desc": "修复 token 过期问题"})
    server.post(f"/api/projects/{pid}/tasks", {"title": "报表导出", "agent": "cline"})
    # 按负责人搜
    status, r = server.get("/api/search/tasks?q=cline")
    assert status == 200 and len(r["results"]) == 1
    assert r["results"][0]["title"] == "报表导出"
    assert r["results"][0]["project_name"] == "搜索项目"
    # 中文描述（percent-encoded：联调）
    status, r2 = server.get("/api/search/tasks?q=%E8%81%94%E8%B0%83")
    assert status == 200 and len(r2["results"]) == 1 and r2["results"][0]["title"] == "登录模块联调"
    # 无匹配 / 空查询
    status, r3 = server.get("/api/search/tasks?q=zzzzz_not_exist")
    assert status == 200 and r3["results"] == []
    status, r4 = server.get("/api/search/tasks")
    assert status == 200 and r4["results"] == []


# ---------------- 机器可读上下文 ----------------

def test_project_context_endpoint(server):
    status, proj = server.create_project("上下文项目")
    pid = proj["id"]
    server.post(f"/api/projects/{pid}/tasks", {"title": "先做这个", "status": "待办", "priority": "高"})
    server.post(f"/api/projects/{pid}/tasks", {"title": "被阻塞的", "status": "待办",
                                               "depends_on": []})
    status, ctx = server.get(f"/api/context/{pid}")
    assert status == 200
    assert ctx["app"] == "ai-kanban"
    assert ctx["project"]["name"] == "上下文项目"
    assert "handoff_note" in ctx["project"] and "next_action" in ctx["project"]
    assert isinstance(ctx["start_here"], list) and len(ctx["start_here"]) >= 1
    assert ctx["readiness"]["score"] >= 0
    assert ctx["usage_hint"]


def test_project_context_404(server):
    assert server.get("/api/context/not_exist_pid")[0] == 404


# ---------------- 交接内容脱敏 ----------------

def test_export_redact_hides_secrets(server):
    status, proj = server.create_project("脱敏项目")
    pid = proj["id"]
    secret_note = "配置如下：API_KEY=sk-abcd1234efgh5678，联系 admin@company.com，密码 password: P@ssw0rd9"
    status, updated = server.put(f"/api/projects/{pid}", {
        "name": "脱敏项目", "status": "进行中", "progress": 10, "priority": "高",
        "handoff_note": secret_note,
    })
    assert status == 200
    # 不脱敏：原文在
    _, raw = server.get(f"/api/export/{pid}")
    assert "sk-abcd1234efgh5678" in raw and "admin@company.com" in raw
    # 脱敏：敏感内容被掩码
    _, safe = server.get(f"/api/export/{pid}?redact=1")
    assert "sk-abcd1234efgh5678" not in safe, "API Key 应被脱敏"
    assert "admin@company.com" not in safe, "邮箱应被脱敏"
    assert "********" in safe
    # 全项目导出同样支持
    _, safe_all = server.get("/api/export-all?redact=1")
    assert "sk-abcd1234efgh5678" not in safe_all


# ---------------- 任务过程日志 ----------------

def test_task_journal_and_status_transition(server):
    status, proj = server.create_project("日志项目")
    pid = proj["id"]
    status, task = server.post(f"/api/projects/{pid}/tasks", {"title": "有日志的任务", "status": "待办"})
    tid = task["id"]
    # 手动追加
    status, entry = server.post(f"/api/tasks/{tid}/journal", {"text": "完成了接口设计评审"})
    assert status == 201 and entry["text"] == "完成了接口设计评审"
    # 状态变化自动记录
    server.put(f"/api/tasks/{tid}", {"title": "有日志的任务", "status": "进行中"})
    _, detail = server.get(f"/api/projects/{pid}")
    t = detail["tasks"][0]
    assert t["journal"] and t["journal"][-1]["text"] == "状态：待办 → 进行中"
    assert t["journal"][0]["text"] == "完成了接口设计评审"


# ---------------- 项目记忆层 ----------------

def test_memories_crud_and_recall(server):
    status, proj = server.create_project("记忆项目")
    pid = proj["id"]
    s, m1 = server.post("/api/memories", {"pid": pid, "type": "坑", "text": "并发写必须加锁"})
    assert s == 201 and m1["ok"] is True
    # 去重
    s, m2 = server.post("/api/memories", {"pid": pid, "type": "坑", "text": "并发写必须加锁"})
    assert m2.get("deduped") is True
    # 列表与过滤
    _, lst = server.get("/api/memories?pid=" + pid)
    assert len(lst["memories"]) == 1
    _, lst2 = server.get("/api/memories?q=加锁")
    assert len(lst2["memories"]) == 1
    # 召回进 AI brief
    _, brief = server.get("/api/projects")
    s, _ = server.get(f"/api/context/{pid}")
    assert s == 200
    # 删除
    assert server.delete("/api/memories/" + m1["memory"]["id"])[0] == 200
    _, lst3 = server.get("/api/memories?pid=" + pid)
    assert lst3["memories"] == []


# ---------------- 会话捕获 ----------------

def test_session_scan_and_extract(server, tmp_path):
    # 造一个假的 Claude Code 会话目录
    src = tmp_path / "claude-projects" / "demo-proj"
    src.mkdir(parents=True)
    jsonl = src / "abc123.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "帮我修复登录超时"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "已定位：token 校验逻辑缺少刷新"}}),
    ]
    jsonl.write_text("\n".join(lines), encoding="utf-8")
    # 用环境变量把源指过去
    import os
    from app import llm
    cfg = llm.load_config()
    cfg["ai"]["session_sources"] = [{"name": "测试源", "path": str(tmp_path / "claude-projects"), "type": "claude_jsonl"}]
    llm.save_config(cfg)
    try:
        status, r = server.get("/api/sessions/scan")
        assert status == 200 and len(r["sessions"]) >= 1
        assert r["sessions"][0]["source"] == "测试源"
        from urllib.parse import quote
        status, pv = server.get("/api/sessions/preview?path=" + quote(str(jsonl)))
        assert status == 200 and "登录超时" in pv["text"]
        # ingest 走真实提炼（stub 端点场景外，这里只验证结构校验）
        status, r2 = server.post("/api/sessions/ingest", {"path": str(jsonl)})
        assert status == 400, "缺 pid 应被拒"
    finally:
        cfg["ai"]["session_sources"] = []
        llm.save_config(cfg)


# ---------------- 用量追踪 ----------------

def test_usage_endpoint(app_client):
    status, u = app_client.get("/api/usage")
    assert status == 200
    assert "totals" in u and "by_feature" in u and "by_model" in u
    assert u["totals"]["calls"] >= 0


# ---------------- MCP 只读开关 ----------------

def test_config_accepts_mcp_readonly(app_client):
    status, cfg = app_client.put("/api/ai/config", {"mcp_readonly": True})
    assert status == 200
    assert cfg["ai"]["mcp_readonly"] is True
    status, cfg2 = app_client.put("/api/ai/config", {"mcp_readonly": False})
    assert cfg2["ai"]["mcp_readonly"] is False
