# -*- coding: utf-8 -*-
"""任务关联代码位置（refs）：交接时能说清"改哪里"

只当字符串存，**不解析路径也不校验存在** —— 它可能指向别人的机器，也可能还没创建。
重点在于：规范化（去重/限长/限量）、老数据兼容、交接包与给 AI 的数据都带上。
"""
from __future__ import annotations

import json

import pytest

from app.main import MAX_REFS, MAX_REF_LEN, clean_refs


# ---------------- 规范化 ----------------

def test_clean_refs_trims_dedupes_and_drops_blanks():
    got = clean_refs([" app/main.py:120 ", "", "tests/test_api.py", "app/main.py:120", "  "])
    assert got == ["app/main.py:120", "tests/test_api.py"]


def test_clean_refs_accepts_comma_separated_string():
    """前端那个输入框是逗号分隔的，后端要能吃下"""
    assert clean_refs("app/main.py:120, tests/test_api.py , ") == ["app/main.py:120", "tests/test_api.py"]


def test_clean_refs_strips_backticks():
    assert clean_refs(["`src/app.py`"]) == ["src/app.py"]


def test_clean_refs_caps_count_and_length():
    many = [f"f{i}.py" for i in range(50)]
    assert len(clean_refs(many)) == MAX_REFS
    assert clean_refs(["x" * (MAX_REF_LEN + 1)]) == []
    assert len(clean_refs(["x" * MAX_REF_LEN])) == 1


@pytest.mark.parametrize("raw", [None, 123, {}])
def test_clean_refs_tolerates_junk(raw):
    assert clean_refs(raw) == []


def test_sanitize_backfills_refs_for_legacy_data(app_client):
    """老数据里没有 refs 字段，读一次要补上（不能让前端拿到 undefined）"""
    app_client.data_dir.mkdir(parents=True, exist_ok=True)
    (app_client.data_dir / "projects.json").write_text(json.dumps({"projects": [{
        "id": "p1", "name": "老项目", "status": "进行中", "priority": "中", "progress": 10,
        "tasks": [{"id": "t1", "title": "老任务", "status": "待办", "priority": "中"}],
    }]}, ensure_ascii=False), encoding="utf-8")

    _, detail = app_client.get("/api/projects/p1")
    assert detail["tasks"][0]["refs"] == []


def test_sanitize_cleans_dirty_refs(app_client):
    app_client.data_dir.mkdir(parents=True, exist_ok=True)
    (app_client.data_dir / "projects.json").write_text(json.dumps({"projects": [{
        "id": "p1", "name": "x", "status": "进行中", "priority": "中", "progress": 10,
        "tasks": [{"id": "t1", "title": "t", "status": "待办", "priority": "中",
                   "refs": [" a.py ", "b.py", "b.py", ""]}],
    }]}, ensure_ascii=False), encoding="utf-8")

    _, detail = app_client.get("/api/projects/p1")
    assert detail["tasks"][0]["refs"] == ["a.py", "b.py"]


# ---------------- 接口 ----------------

def test_task_refs_roundtrip(app_client):
    pid = app_client.create_project("带位置的活")[1]["id"]
    st, task = app_client.post(f"/api/projects/{pid}/tasks", {
        "title": "修判定阈值", "status": "进行中", "priority": "高",
        "refs": ["app/main.py:120", "tests/test_api.py"],
    })
    assert st == 201 and task["refs"] == ["app/main.py:120", "tests/test_api.py"]

    st2, updated = app_client.put(f"/api/tasks/{task['id']}", {
        "title": "修判定阈值", "status": "已完成", "priority": "高",
        "refs": ["app/main.py:130"],
    })
    assert st2 == 200 and updated["refs"] == ["app/main.py:130"]


def test_refs_are_optional(app_client):
    pid = app_client.create_project("不带位置的活")[1]["id"]
    st, task = app_client.post(f"/api/projects/{pid}/tasks", {"title": "无位置"})
    assert st == 201 and task["refs"] == []


def test_refs_are_cleaned_on_write(app_client):
    pid = app_client.create_project("脏位置")[1]["id"]
    st, task = app_client.post(f"/api/projects/{pid}/tasks", {
        "title": "x", "refs": [" a.py ", "b.py", "b.py"]})
    assert st == 201 and task["refs"] == ["a.py", "b.py"]


# ---------------- 交接与 AI ----------------

def test_handoff_lists_code_locations(app_client):
    pid = app_client.create_project("交接带位置", next_action="干活")[1]["id"]
    app_client.post(f"/api/projects/{pid}/tasks", {
        "title": "改判定逻辑", "status": "进行中", "priority": "高",
        "refs": ["app/main.py:120", "tests/test_api.py"]})
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "无位置任务", "status": "待办"})

    _, md = app_client.get(f"/api/export/{pid}")
    assert "### 涉及代码位置" in md
    assert "改判定逻辑 → `app/main.py:120`、`tests/test_api.py`" in md
    assert "无位置任务 →" not in md, "没有 refs 的任务不该出现在这一节"


def test_handoff_has_no_section_when_no_refs(app_client):
    pid = app_client.create_project("没有位置")[1]["id"]
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "x"})
    _, md = app_client.get(f"/api/export/{pid}")
    assert "涉及代码位置" not in md


def test_agents_md_shows_refs(app_client):
    """本地模板版的 AGENTS.md 也要带位置，否则换工具接手时又得翻一遍"""
    pid = app_client.create_project("AGENTS.md 带位置")[1]["id"]
    app_client.post(f"/api/projects/{pid}/tasks", {
        "title": "改判定逻辑", "status": "进行中", "priority": "高", "refs": ["app/main.py:120"]})
    _, r = app_client.post(f"/api/export/agentsmd/{pid}")
    assert "app/main.py:120" in r["text"] and "涉及" in r["text"]


def test_analyze_prompt_carries_refs(ai_client):
    ai_client.create_project("带位置的活", status="进行中")
    pid = ai_client.get("/api/projects")[1]["projects"][0]["id"]
    ai_client.post(f"/api/projects/{pid}/tasks", {
        "title": "改判定逻辑", "refs": ["app/main.py:120"]})
    ai_client.stub.push_json({"summary": "ok", "insights": [], "global_suggestions": []})
    assert ai_client.post("/api/ai/analyze")[0] == 200
    assert "app/main.py:120" in str(ai_client.stub.calls[-1]["payload"])


# MCP 侧的 refs 用例在 tests/test_mcp_server.py（那里才有 stdio 客户端夹具）
