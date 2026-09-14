# -*- coding: utf-8 -*-
"""数据层与杂项：备份、导出落盘、脏数据规范化、损坏文件恢复、交接 Markdown、404 边界"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.main import _build_handoff_markdown, _extract_json, _projects_brief, _sanitize


# ---------------- 数据目录与文件恢复 ----------------

def test_missing_data_file_returns_samples(app_client):
    """数据文件不存在时给示例数据（此时不落盘，用户一改才写）"""
    _, data = app_client.get("/api/projects")
    assert len(data["projects"]) >= 1
    assert not (app_client.data_dir / "projects.json").exists()


def test_corrupt_data_file_is_backed_up_and_reset(app_client):
    app_client.data_dir.mkdir(parents=True, exist_ok=True)
    (app_client.data_dir / "projects.json").write_text("{ 这不是 JSON", encoding="utf-8")

    status, data = app_client.get("/api/projects")
    assert status == 200 and data["projects"] == []
    assert (app_client.data_dir / "projects.json.bak").exists(), "损坏文件应被备份"


def test_legacy_junk_is_sanitized_on_load(app_client):
    """老数据里的非法状态/优先级/进度不能流到前端（会被拼进 class 与文本）"""
    app_client.data_dir.mkdir(parents=True, exist_ok=True)
    (app_client.data_dir / "projects.json").write_text(json.dumps({
        "projects": [{
            "id": "p1", "name": "脏数据项目", "status": "<script>", "priority": "超高",
            "progress": 999, "tags": "不是数组", "handoff_note": "n",
            "tasks": [{"id": "t1", "title": "任务", "status": "乱写", "priority": None}],
        }]
    }, ensure_ascii=False), encoding="utf-8")

    _, detail = app_client.get("/api/projects/p1")
    assert detail["status"] == "规划中" and detail["priority"] == "中"
    assert detail["progress"] == 100 and detail["tags"] == []
    assert detail["tasks"][0]["status"] == "待办" and detail["tasks"][0]["priority"] == "中"


def test_progress_negative_is_clamped(app_client):
    app_client.data_dir.mkdir(parents=True, exist_ok=True)
    (app_client.data_dir / "projects.json").write_text(json.dumps(
        {"projects": [{"id": "p1", "name": "x", "status": "进行中", "priority": "低",
                       "progress": -20, "tags": [], "tasks": []}]}, ensure_ascii=False),
        encoding="utf-8")
    assert app_client.get("/api/projects/p1")[1]["progress"] == 0


def test_sanitize_tolerates_wrong_shapes():
    assert _sanitize("不是字典") == {"projects": []}
    assert _sanitize({"projects": "不是列表"})["projects"] == []
    out = _sanitize({"projects": ["不是字典"]})
    assert out["projects"] == ["不是字典"]      # 非字典条目直接跳过，不炸


# ---------------- 备份 ----------------

def test_backup_creates_snapshot(app_client):
    app_client.create_project("备份用项目")
    status, body = app_client.post("/api/backup")
    assert status == 200 and body["ok"] is True
    assert "projects.json" in body["files"]
    assert Path(body["folder"]).exists()


def test_backup_listing_and_retention(app_client):
    for _ in range(12):
        assert app_client.post("/api/backup")[0] == 200
    status, body = app_client.get("/api/backups")
    assert status == 200 and len(body["backups"]) <= 10, "只保留最近 10 份"
    assert body["backups"][0]["name"].startswith("backup_")


# ---------------- 导出落盘 ----------------

def test_export_save_without_path_falls_back_to_desktop(app_client, monkeypatch, tmp_path):
    """没给路径时优先桌面，桌面写不了才退到数据目录 exports/"""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    status, body = app_client.post("/api/export/save", {"filename": "交接包.md", "content": "# hi"})
    assert status == 200 and body["ok"] is True
    assert Path(body["path"]).parent == fake_home / "Desktop"
    assert Path(body["path"]).read_text(encoding="utf-8") == "# hi"


def test_export_save_failure_returns_500(app_client, monkeypatch, tmp_path):
    """两个落点都写不进去时应报 500，而不是静默吞掉"""
    from fastapi.testclient import TestClient

    from app.main import app

    blocked = tmp_path / "blocker"
    blocked.write_text("我是文件，不是目录", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(blocked))      # 桌面路径会挂在文件下面
    monkeypatch.setenv("KANBAN_DATA_DIR", str(blocked))  # exports 也写不进去

    # 这里会抛异常（数据目录本身就不是目录），用不吞异常的客户端断言服务端返回 500
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        r = c.post("/api/export/save", json={"filename": "x.md", "content": "x"})
    assert r.status_code == 500


def test_export_save_reports_write_error(app_client, monkeypatch, tmp_path):
    """给了非法路径（父级是文件）时应返回带原因的 500"""
    blocked = tmp_path / "blocker2"
    blocked.write_text("文件", encoding="utf-8")
    target = blocked / "sub" / "x.md"
    status, body = app_client.post("/api/export/save",
                                   {"filename": "x.md", "content": "x", "path": str(target)})
    assert status == 500 and "写入失败" in str(body)


# ---------------- 交接 Markdown 与 JSON 容错 ----------------

def test_handoff_markdown_escapes_table_pipes():
    md = _build_handoff_markdown({
        "id": "p1", "name": "项目", "status": "进行中", "progress": 50, "priority": "高",
        "tags": ["a|b"], "repo_path": "", "background": "", "handoff_note": "", "next_action": "",
        "tasks": [{"title": "含|竖线", "status": "待办", "priority": "高", "agent": "", "desc": "多行\n描述"}],
    })
    assert "含\\|竖线" in md, "表格里的竖线必须转义"
    assert "多行 描述" in md, "描述里的换行要压平"


def test_handoff_markdown_minimal_project():
    md = _build_handoff_markdown({"id": "p1", "name": "空项目", "status": "规划中",
                                  "progress": 0, "priority": "低", "tasks": []})
    assert "# 空项目" in md and "任务清单" not in md
    assert "- **标签**：无" in md, "空标签应显示为「无」"


def test_projects_brief_truncates_long_fields():
    brief = _projects_brief([{
        "id": "p1", "name": "x", "status": "进行中", "progress": 1, "priority": "中", "tags": [],
        "next_action": "n" * 500, "background": "b" * 500,
        "tasks": [{"title": f"t{i}", "status": "待办", "priority": "中"} for i in range(60)],
    }])
    assert len(brief[0]["next_action"]) == 300
    assert len(brief[0]["background"]) == 300
    assert len(brief[0]["tasks"]) == 40, "任务只带前 40 条给模型"


def test_extract_json_from_noisy_prefix_and_array():
    assert _extract_json('模型说：{"a": 1} 完毕') == {"a": 1}
    assert _extract_json('前缀 [{"a": 1}] 后缀') == [{"a": 1}]


# ---------------- 404 边界 ----------------

def test_missing_resources_return_404(app_client):
    assert app_client.get("/api/projects/不存在")[0] == 404
    assert app_client.put("/api/projects/不存在", {"name": "x"})[0] == 404
    assert app_client.delete("/api/projects/不存在")[0] == 404
    assert app_client.put("/api/tasks/不存在", {"title": "x"})[0] == 404
    assert app_client.delete("/api/tasks/不存在")[0] == 404
    assert app_client.get("/api/export/不存在")[0] == 404
    assert app_client.post("/api/projects/不存在/tasks", {"title": "x"})[0] == 404


def test_creating_task_requires_existing_project(app_client):
    assert app_client.post("/api/projects/不存在/tasks", {"title": "任务"})[0] == 404


# ---------------- 调度 ----------------

def test_run_analyze_with_schedule_trigger(tmp_path, monkeypatch):
    """直接跑一次调度用的分析，确认 trigger 与报告落盘都对"""
    from fastapi.testclient import TestClient

    from app import main
    from llm_stub import StubLLM

    stub = StubLLM().start()
    try:
        data_dir = tmp_path / "kanban-sched"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "config.json").write_text(json.dumps(
            {"ai": {"endpoints": [stub.endpoint()], "active_model": "stub-model"}},
            ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("KANBAN_DATA_DIR", str(data_dir))
        with TestClient(main.app, base_url="http://127.0.0.1"):
            stub.push_json({"summary": "定时分析", "insights": [], "global_suggestions": []})
            report = main._run_analyze("schedule")
        assert report["trigger"] == "schedule"
        assert (data_dir / "reports" / f"{report['id']}.json").exists()
    finally:
        stub.stop()


def test_scheduler_start_is_idempotent():
    from app import main
    assert callable(main.start_scheduler)
    main.start_scheduler()      # 重复调用不应抛错，也不应再起线程
    assert main._SCHEDULER_STARTED is True
