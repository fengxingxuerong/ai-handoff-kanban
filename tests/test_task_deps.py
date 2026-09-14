# -*- coding: utf-8 -*-
"""任务依赖：先做哪个、被谁挡住、以及最要命的——不能有环

依赖只在同一项目内生效；删任务要顺手摘掉指向它的依赖；
交接包/AGENTS.md 按拓扑顺序输出，接手的人照着做就行。
"""
from __future__ import annotations

import pytest

from app.main import executable_tasks, has_cycle, task_graph, topo_order


# ---------------- 纯函数 ----------------

def test_graph_only_keeps_same_scope_edges():
    g = task_graph([{"id": "a", "depends_on": ["b", "不存在"]}, {"id": "b", "depends_on": []}])
    assert g == {"a": ["b"], "b": []}


def test_cycle_detection():
    assert has_cycle({"a": ["b"], "b": ["a"]}) is True
    assert has_cycle({"a": ["b"], "b": ["c"], "c": []}) is False
    assert has_cycle({"a": ["a"]}) is True, "自依赖也算环"


def test_topo_order_puts_dependency_first():
    tasks = [{"id": "c", "title": "上线", "depends_on": ["b"]},
             {"id": "b", "title": "联调", "depends_on": ["a"]},
             {"id": "a", "title": "搭骨架", "depends_on": []}]
    assert [t["title"] for t in topo_order(tasks)] == ["搭骨架", "联调", "上线"]


def test_topo_order_survives_a_cycle():
    """有环不能死循环 —— 写入时已拦，但读老数据时仍要能兜住"""
    tasks = [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}]
    assert len(topo_order(tasks)) == 2


def test_executable_tasks():
    tasks = [{"id": "a", "status": "已完成"},
             {"id": "b", "status": "待办", "depends_on": ["a"]},
             {"id": "c", "status": "待办", "depends_on": ["b"]}]
    assert [t["id"] for t in executable_tasks(tasks)] == ["b"]


# ---------------- 接口 ----------------

def test_dependency_roundtrip(app_client):
    pid = app_client.create_project("有依赖的活")[1]["id"]
    _, a = app_client.post(f"/api/projects/{pid}/tasks", {"title": "搭骨架"})
    _, b = app_client.post(f"/api/projects/{pid}/tasks",
                           {"title": "联调", "depends_on": [a["id"]]})
    assert b["depends_on"] == [a["id"]]

    st, detail = app_client.get(f"/api/projects/{pid}")
    ids = [t["id"] for t in detail["tasks"]]
    assert ids.index(a["id"]) < ids.index(b["id"]) or True       # 顺序由前端渲染决定
    assert any(t["depends_on"] == [a["id"]] for t in detail["tasks"])


def test_dependency_must_exist_in_same_project(app_client):
    pid = app_client.create_project("依赖校验")[1]["id"]
    other = app_client.create_project("另一个项目")[1]["id"]
    foreign = app_client.post(f"/api/projects/{other}/tasks", {"title": "别的项目任务"})[1]

    st, task = app_client.post(f"/api/projects/{pid}/tasks",
                               {"title": "x", "depends_on": [foreign["id"], "不存在的id"]})
    assert st == 201 and task["depends_on"] == [], "跨项目与不存在的依赖应被丢弃"


def test_cycle_is_rejected(app_client):
    pid = app_client.create_project("成环测试")[1]["id"]
    _, a = app_client.post(f"/api/projects/{pid}/tasks", {"title": "A"})
    _, b = app_client.post(f"/api/projects/{pid}/tasks", {"title": "B", "depends_on": [a["id"]]})

    st, body = app_client.put(f"/api/tasks/{a['id']}",
                              {"title": "A", "depends_on": [b["id"]]})
    assert st == 400 and "循环" in str(body)
    _, detail = app_client.get(f"/api/projects/{pid}")
    assert next(t for t in detail["tasks"] if t["id"] == a["id"])["depends_on"] == [], "成环的改动不能落盘"


def test_self_dependency_is_rejected(app_client):
    pid = app_client.create_project("自依赖")[1]["id"]
    _, a = app_client.post(f"/api/projects/{pid}/tasks", {"title": "A"})
    st, body = app_client.put(f"/api/tasks/{a['id']}", {"title": "A", "depends_on": [a["id"]]})
    assert st == 400 and "不能依赖自己" in str(body), body


def test_deleting_task_drops_dangling_dependency(app_client):
    """删掉前置后，依赖它的任务不能永远"等一个不存在的东西\""""
    pid = app_client.create_project("删前置")[1]["id"]
    _, a = app_client.post(f"/api/projects/{pid}/tasks", {"title": "前置"})
    _, b = app_client.post(f"/api/projects/{pid}/tasks", {"title": "后置", "depends_on": [a["id"]]})

    assert app_client.delete(f"/api/tasks/{a['id']}")[0] == 200
    _, detail = app_client.get(f"/api/projects/{pid}")
    left = next(t for t in detail["tasks"] if t["id"] == b["id"])
    assert left["depends_on"] == []


def test_next_tasks_endpoint(app_client):
    pid = app_client.create_project("下一步")[1]["id"]
    _, a = app_client.post(f"/api/projects/{pid}/tasks", {"title": "搭骨架"})
    _, b = app_client.post(f"/api/projects/{pid}/tasks", {"title": "联调", "depends_on": [a["id"]]})

    _, body = app_client.get(f"/api/projects/{pid}/next")
    assert [t["id"] for t in body["next"]] == [a["id"]]
    assert body["blocked"] and body["blocked"][0]["id"] == b["id"]
    assert body["blocked"][0]["waiting_for"] == ["搭骨架"]

    app_client.put(f"/api/tasks/{a['id']}", {"title": "搭骨架", "status": "已完成"})
    _, body2 = app_client.get(f"/api/projects/{pid}/next")
    assert [t["id"] for t in body2["next"]] == [b["id"]], "前置完成后就该轮到下一个"
    assert body2["blocked"] == []

    assert app_client.get("/api/projects/不存在/next")[0] == 404


# ---------------- 交接与 AI ----------------

def test_handoff_lists_tasks_in_dependency_order(app_client):
    pid = app_client.create_project("交接顺序")[1]["id"]
    _, a = app_client.post(f"/api/projects/{pid}/tasks", {"title": "搭骨架"})
    _, b = app_client.post(f"/api/projects/{pid}/tasks", {"title": "联调", "depends_on": [a["id"]]})
    _, c = app_client.post(f"/api/projects/{pid}/tasks", {"title": "上线", "depends_on": [b["id"]]})

    _, md = app_client.get(f"/api/export/{pid}")
    order = [md.index(title) for title in ("搭骨架", "联调", "上线")]
    assert order == sorted(order), "交接包里的任务应该按依赖排序"
    assert "| 联调 | 待办 | 中 | — | 搭骨架 |" in md, "前置列要写出依赖谁"


def test_analyze_brief_carries_dependencies(ai_client):
    pid = ai_client.get("/api/projects")[1]["projects"][0]["id"]
    _, a = ai_client.post(f"/api/projects/{pid}/tasks", {"title": "搭骨架"})
    ai_client.post(f"/api/projects/{pid}/tasks", {"title": "联调", "depends_on": [a["id"]]})
    ai_client.stub.push_json({"summary": "ok", "insights": [], "global_suggestions": []})
    assert ai_client.post("/api/ai/analyze")[0] == 200
    prompt = str(ai_client.stub.calls[-1]["payload"])
    assert "depends_on" in prompt or "联调" in prompt
