# -*- coding: utf-8 -*-
"""全量数据导出 / 导入（跨机迁移）

背景：这两个功能后端一直有，但**前端从未实现** —— ACTIONS 里引用了
`exportData()` / `importData()`，两个函数在 index.html 里根本不存在，
`#import-file` 也没有任何监听。点下去只有 ReferenceError：
用户看到的是"按钮点了没反应"。E2E 用例抓到了它。

接口语义（沿用后端既有实现）：
- `GET /api/data/export` → `{app, version, exported_at, projects}`，**不含 AI 配置与密钥**
- `POST /api/data/import` → 收 `{content: <文件原文>}`，**覆盖**全部项目，导入前自动做快照
"""
from __future__ import annotations

import json

import pytest


def seed(api, *names: str) -> None:
    for n in names:
        api.create_project(n)


def dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


# ---------------- 导出 ----------------

def test_export_shape(app_client):
    seed(app_client, "导出项目一", "导出项目二")
    status, body = app_client.get("/api/data/export")
    assert status == 200
    assert body["app"] == "ai-kanban" and body["version"]
    assert body["exported_at"], "要有导出时间，换机/找回时靠它判断新旧"
    assert sorted(p["name"] for p in body["projects"]) == ["导出项目一", "导出项目二"]


def test_export_never_contains_secrets(app_client):
    """导出文件常被随手丢网盘或聊天窗口 —— 绝不能带 Key"""
    app_client.data_dir.mkdir(parents=True, exist_ok=True)
    (app_client.data_dir / "config.json").write_text(json.dumps({"ai": {
        "endpoints": [{"name": "端点甲", "base_url": "https://x.example.com/v1",
                       "api_keys": ["sk-secret-should-never-leak"], "models": ["m"]}],
        "active_model": "m"}}, ensure_ascii=False), encoding="utf-8")

    raw = dump(app_client.get("/api/data/export")[1])
    assert "sk-secret-should-never-leak" not in raw, "导出里带上了 API Key！"
    assert "base_url" not in raw, "导出里带上了端点配置"


def test_export_includes_tasks_and_new_fields(app_client):
    """任务的代码位置/依赖这些字段也得跟着走，否则换机后交接信息就丢了"""
    pid = app_client.create_project("带任务的项目")[1]["id"]
    app_client.post(f"/api/projects/{pid}/tasks",
                    {"title": "任务甲", "refs": ["app/main.py:120"], "due_date": "2026-10-01"})
    body = app_client.get("/api/data/export")[1]
    task = body["projects"][0]["tasks"][0]
    assert task["refs"] == ["app/main.py:120"] and task["due_date"] == "2026-10-01"


# ---------------- 导入 ----------------

def test_import_replaces_all_projects(app_client):
    """导入是覆盖语义（换机恢复该有的行为），旧数据不应残留"""
    seed(app_client, "会被覆盖掉的项目")
    payload = {"app": "ai-kanban", "projects": [
        {"id": "p1", "name": "导入的项目", "status": "进行中", "priority": "高", "progress": 30}]}

    status, body = app_client.post("/api/data/import", {"content": dump(payload)})
    assert status == 200
    names = [p["name"] for p in app_client.get("/api/projects")[1]["projects"]]
    assert names == ["导入的项目"], f"覆盖语义没生效：{names}"


def test_import_roundtrip_keeps_everything(app_client):
    """导出 → 导入，数据要原样回来（换机场景）"""
    api = app_client
    pid = api.create_project("往返项目", progress=66, next_action="继续")[1]["id"]
    api.post(f"/api/projects/{pid}/tasks", {"title": "往返任务", "refs": ["a/b.py:1"]})
    text = dump(api.get("/api/data/export")[1])

    assert api.post("/api/data/import", {"content": text})[0] == 200
    projects = api.get("/api/projects")[1]["projects"]
    assert len(projects) == 1
    p = projects[0]
    assert p["name"] == "往返项目" and p["progress"] == 66 and p["next_action"] == "继续"
    detail = api.get(f"/api/projects/{p['id']}")[1]
    assert detail["tasks"][0]["refs"] == ["a/b.py:1"]


def test_import_makes_backup_snapshot(app_client):
    """导入是破坏性的，导入前必须留快照 —— 这是唯一的后悔药"""
    api = app_client
    seed(api, "导入前的项目")
    api.post("/api/data/import", {"content": dump({"projects": [{"id": "x", "name": "新的"}]})})

    backups = list((api.data_dir / "backups").glob("backup_*"))
    assert backups, "导入前没有做快照"
    import json as _json
    saved = _json.loads((backups[0] / "projects.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in saved["projects"]] == ["导入前的项目"]


def test_import_normalizes_dirty_data(app_client):
    app_client.post("/api/data/import", {"content": dump({"projects": [
        {"id": "p1", "name": "脏数据项目", "status": "瞎写的状态", "priority": "特高", "progress": 999}]})})
    p = app_client.get("/api/projects")[1]["projects"][0]
    assert p["status"] == "规划中" and p["priority"] == "中" and p["progress"] == 100


def test_import_skips_projects_without_id_or_name(app_client):
    """缺 id 或名字的条目直接跳过，不要让半条数据进看板"""
    status, _ = app_client.post("/api/data/import", {"content": dump({"projects": [
        {"id": "ok", "name": "完整的"}, {"name": "缺 id"}, {"id": "no-name"}, "不是对象"]})})
    assert status == 200
    names = [p["name"] for p in app_client.get("/api/projects")[1]["projects"]]
    assert names == ["完整的"]


@pytest.mark.parametrize("content", [
    "不是 JSON",
    "[]",
    '{"projects": "不是列表"}',
    '{"foo": 1}',
])
def test_import_rejects_junk(app_client, content):
    before = app_client.get("/api/projects")[1]["projects"]
    status, _ = app_client.post("/api/data/import", {"content": content})
    assert status == 400
    assert app_client.get("/api/projects")[1]["projects"] == before, "被拒绝的导入不能动现有数据"


def test_import_rejects_empty_content(app_client):
    """content 太短会被 pydantic 拦下来（字段有 min_length）"""
    assert app_client.post("/api/data/import", {"content": ""})[0] == 422
    assert app_client.post("/api/data/import", {})[0] == 422
