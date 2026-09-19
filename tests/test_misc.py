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


def test_corrupt_file_is_healed_so_the_notice_is_not_repeated(app_client):
    """损坏文件必须就地修好：否则每次请求都重复备份 + 重复提示"""
    app_client.data_dir.mkdir(parents=True, exist_ok=True)
    target = app_client.data_dir / "projects.json"
    target.write_text("坏掉了", encoding="utf-8")

    _, first = app_client.get("/api/projects")
    assert first["data_recovered"] is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"projects": []}, "文件应已被修好"

    _, second = app_client.get("/api/projects")
    assert second["data_recovered"] is False, "提示只应上报一次"


def test_first_write_retires_sample_data(app_client):
    """首次落盘时示例数据应让位，不能永久留在真实看板里"""
    _, before = app_client.get("/api/projects")
    assert any(p["name"].startswith("示例项目") for p in before["projects"]), "新装应先看到示例"

    app_client.create_project("我自己的项目")
    _, after = app_client.get("/api/projects")
    names = [p["name"] for p in after["projects"]]
    assert names == ["我自己的项目"], f"示例数据应已让位，实际：{names}"


def test_editing_a_sample_project_keeps_it(app_client):
    """用户要是真动了示例项目（比如拿它当模板改），就不能再把它丢掉"""
    sample_id = app_client.get("/api/projects")[1]["projects"][0]["id"]
    app_client.put(f"/api/projects/{sample_id}", {"name": "改过的示例", "status": "进行中"})
    app_client.create_project("新项目")
    names = [p["name"] for p in app_client.get("/api/projects")[1]["projects"]]
    assert "改过的示例" in names and "新项目" in names


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


def test_rapid_backups_do_not_collapse_into_one_folder(app_client):
    """连着拍两份必须真的留下两份。

    目录名只精确到秒，而"备份"恰恰最容易被连着调（代理批量改动前拍一份、又拍一份，
    导入流程自己也先备份一次）。同名会落进同一个目录，第二次把第一次覆盖掉：
    用户以为有两份退路，其实只剩一份，而且全程没有任何提示。
    """
    folders = {app_client.post("/api/backup")[1]["folder"] for _ in range(4)}
    assert len(folders) == 4, f"四次备份只剩 {len(folders)} 个目录：{sorted(folders)}"
    listing = app_client.get("/api/backups")[1]["backups"]
    assert len({b["name"] for b in listing}) == len(listing)
    # 同秒重入的那份要看得出来，不能显示成同一个时刻的两条同名记录
    assert any("-2" in b["name"] for b in listing), [b["name"] for b in listing]


def test_backup_label_reads_the_suffix(app_client):
    from app.main import _backup_label

    assert _backup_label("backup_20260919_182610") == "2026-09-19 18:26:10"
    assert _backup_label("backup_20260919_182610-2") == "2026-09-19 18:26:10（第 2 次）"
    # 手出来的目录名不该把接口打崩，原样回显即可
    assert _backup_label("backup_完全不是时间") == "完全不是时间"


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


# ---------------- 交接物尺寸预算（接手方是模型，上下文窗口才是稀缺资源） ----------------

def test_context_budget_levels():
    from app.main import context_budget

    b = context_budget("一行\n" * 10)
    assert b == {"lines": 10, "kb": round(len(("一行\n" * 10).encode("utf-8")) / 1024, 1), "level": "ok"}
    assert context_budget("x\n" * 150)["level"] == "ok"
    assert context_budget("x\n" * 151)["level"] == "warn"
    assert context_budget("x\n" * 300)["level"] == "warn"
    assert context_budget("x\n" * 301)["level"] == "over"


def test_context_budget_counts_utf8_bytes_not_chars():
    """KB 必须按 UTF-8 字节算：中文一字三字节，按字符算会把包"报小"三倍"""
    from app.main import context_budget

    text = "中文" * 1536          # 3072 字符 = 9216 字节
    b = context_budget(text)
    assert b["kb"] > 8, f"KB 看着像按字符算的：{b}"


def test_budget_note_stays_quiet_when_short_but_warns_when_long():
    from app.main import _budget_note

    assert _budget_note("短包\n" * 20) == "", "正常长度不该往交接物里塞废话"
    note = _budget_note("长行\n" * 400)
    assert "400 行" in note and "KB" in note, note
    assert "读不完" in note, f"过载档要给明确动作：{note}"


def test_handoff_markdown_still_renders_without_budget_noise(app_client):
    """预算只加在 MCP 出口与界面提示上，_build_handoff_markdown 本体不该被改长"""
    pid = app_client.post("/api/projects", {"name": "干净交接包"})[1]["id"]
    md = _build_handoff_markdown(app_client.get(f"/api/projects/{pid}")[1])
    assert "本包约" not in md
