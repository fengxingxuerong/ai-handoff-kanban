from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

# Scenario references, adapted rather than copied:
# Plane: contract/api/test_non_object_json_body.py, test_issue_read_only_fields.py,
#        unit/utils/test_order_by_sanitize.py, unit/serializers/test_mass_assignment.py
# Vikunja: pkg/models/tasks_test.go, task_search_test.go, bulk_task_test.go,
#          pkg/routes/api/v2/errors_test.go
# Kanboard: units/Model/TaskCreationModelTest.php, units/Filter/TaskSearchFilterTest.php,
#           units/Core/CsvTest.php (CSV is not applicable to this JSON/Markdown exporter)


@pytest.mark.parametrize("method,path,raw", [
    ("POST", "/api/projects", '"hello"'),
    ("POST", "/api/projects", "42"),
    ("POST", "/api/projects", "true"),
    ("POST", "/api/projects", "null"),
    ("POST", "/api/projects", "[]"),
    ("POST", "/api/memories", '"hello"'),
    ("PUT", "/api/ai/config", "42"),
])
def test_scalar_json_write_bodies_are_rejected_without_server_errors(app_client, method, path, raw):
    response = app_client.client.request(method, path, content=raw.encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
    assert 400 <= response.status_code < 500, response.text


def test_scalar_json_patch_body_is_rejected_without_server_errors(app_client):
    _, project = app_client.create_project("标量补丁项目")
    response = app_client.client.request(
        "PATCH", f"/api/projects/{project['id']}", content=b'"hello"',
        headers={"Content-Type": "application/json"})
    assert 400 <= response.status_code < 500, response.text


def test_identity_audit_and_nested_fields_cannot_be_mass_assigned(app_client):
    _, project = app_client.create_project("只读字段项目")
    pid = project["id"]
    _, task = app_client.post(f"/api/projects/{pid}/tasks", {"title": "原任务"})
    tid = task["id"]
    before_project = app_client.get(f"/api/projects/{pid}")[1]
    before_task = app_client.get(f"/api/tasks/{tid}")[1]["task"]

    status, _ = app_client.patch(f"/api/projects/{pid}", {
        "name": "改名后的项目", "id": "attacker-id", "created_at": "1999-01-01",
        "is_sample": True, "tasks": [{"id": "injected"}],
    })
    assert status == 200
    after_project = app_client.get(f"/api/projects/{pid}")[1]
    assert after_project["id"] == pid
    assert after_project["name"] == "改名后的项目"
    assert after_project["tasks"] == before_project["tasks"]
    assert after_project.get("is_sample", False) is False

    status, _ = app_client.patch(f"/api/tasks/{tid}", {
        "title": "改名后的任务", "id": "attacker-task", "done_at": "1999-01-01",
        "created_at": "1999-01-01", "journal": [{"text": "injected"}],
    })
    assert status == 200
    after_task = app_client.get(f"/api/tasks/{tid}")[1]["task"]
    assert after_task["id"] == tid
    assert after_task["title"] == "改名后的任务"
    assert "done_at" not in after_task
    assert after_task.get("journal", []) == before_task.get("journal", [])


@pytest.mark.parametrize("payload", [
    "created_by__password", "-updated_by__token", "not_a_field",
    "id; drop table", "--created_at", "name__secret",
])
def test_unknown_order_by_values_do_not_reach_an_ordering_oracle(app_client, payload):
    status, body = app_client.get("/api/projects?order_by=" + quote(payload))
    assert status == 200, body
    assert isinstance(body["projects"], list)


def test_task_search_matches_title_description_and_unicode_casefold(app_client):
    _, project = app_client.create_project("搜索契约项目")
    pid = project["id"]
    _, title_task = app_client.post(f"/api/projects/{pid}/tasks", {"title": "Änderung Config"})
    _, desc_task = app_client.post(f"/api/projects/{pid}/tasks", {
        "title": "Description task", "desc": "needle description"})
    _, unicode_task = app_client.post(f"/api/projects/{pid}/tasks", {"title": "Привет мир"})

    def search(query):
        status, body = app_client.get("/api/search/tasks?q=" + quote(query))
        assert status == 200, body
        return {row["task_id"] for row in body["results"]}

    assert search("änderung") == {title_task["id"]}
    assert search("ÄNDERUNG") == {title_task["id"]}
    assert search("config") == {title_task["id"]}
    assert search("needle") == {desc_task["id"]}
    assert search("привет") == {unicode_task["id"]}
    assert search("МИР") == {unicode_task["id"]}
    assert search("xylophone") == set()


def test_task_dependency_rejects_ids_from_another_project(app_client):
    _, first = app_client.create_project("依赖项目甲")
    _, second = app_client.create_project("依赖项目乙")
    _, foreign = app_client.post(f"/api/projects/{second['id']}/tasks", {"title": "外国前置"})
    _, local = app_client.post(f"/api/projects/{first['id']}/tasks", {"title": "本地任务"})
    status, task = app_client.patch(f"/api/tasks/{local['id']}", {"depends_on": [foreign["id"]]})
    assert status == 200
    assert task["depends_on"] == []


def test_far_future_due_date_roundtrips_without_32_bit_limits(app_client):
    _, project = app_client.create_project("远未来日期项目")
    status, task = app_client.post(f"/api/projects/{project['id']}/tasks", {
        "title": "2099 截止", "due_date": "2099-12-31"})
    assert status == 201
    assert task["due_date"] == "2099-12-31"
    _, stored = app_client.get(f"/api/tasks/{task['id']}")
    assert stored["task"]["due_date"] == "2099-12-31"


@pytest.mark.parametrize("field", ["due_date", "milestone", "blocked_reason"])
@pytest.mark.parametrize("bad", [1, [], {}])
def test_task_before_validators_reject_non_string_values(app_client, field, bad):
    _, project = app_client.create_project("任务字段类型项目")
    status, body = app_client.post(f"/api/projects/{project['id']}/tasks", {
        "title": "类型边界", field: bad})
    assert status == 422, f"{field}={bad!r} 应 422，实际 {status}: {body}"


@pytest.mark.parametrize("field", ["due_date", "milestone", "blocked_reason"])
def test_null_task_text_fields_keep_the_existing_unset_contract(app_client, field):
    _, project = app_client.create_project("空值兼容项目")
    status, task = app_client.post(f"/api/projects/{project['id']}/tasks", {
        "title": "null 兼容", field: None})
    assert status == 201
    assert task.get(field, "") == ""


def test_duplicate_ids_in_bulk_delete_are_processed_once(app_client):
    _, first = app_client.create_project("去重删除甲")
    _, second = app_client.create_project("保留项目")
    status, body = app_client.post("/api/projects/bulk-delete", {
        "ids": [first["id"], first["id"]], "confirm": True})
    assert status == 200, body
    assert body["deleted"] == [first["id"]]
    _, listed = app_client.get("/api/projects")
    assert [p["id"] for p in listed["projects"]] == [second["id"]]


def test_noop_task_patch_does_not_append_a_state_journal(app_client):
    _, project = app_client.create_project("无变化更新项目")
    _, task = app_client.post(f"/api/projects/{project['id']}/tasks", {"title": "保持原样"})
    before = app_client.get(f"/api/tasks/{task['id']}")[1]["task"]
    status, after = app_client.patch(f"/api/tasks/{task['id']}", {"title": "保持原样"})
    assert status == 200
    assert after.get("journal", []) == before.get("journal", [])


def test_internal_error_response_does_not_leak_exception_details(app_client, monkeypatch):
    import app.main as kanban

    secret = "dial tcp 127.0.0.1:6390: credential=do-not-leak"

    def fail(_data=None):
        raise RuntimeError(secret)

    monkeypatch.setattr(kanban, "get_projects_data", fail)
    with TestClient(kanban.app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        response = client.get("/api/projects")
    assert response.status_code == 500
    assert secret not in response.text
    assert "Internal Server Error" in response.text
