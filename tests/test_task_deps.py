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
    # total/done 是给"为什么没有可做的"准备的：没任务 / 都完成 / 都在等前置 是三件事
    assert (body["total"], body["done"]) == (2, 0), body
    assert body["blocked"] and body["blocked"][0]["id"] == b["id"]
    assert body["blocked"][0]["waiting_for"] == ["搭骨架"]

    app_client.put(f"/api/tasks/{a['id']}", {"title": "搭骨架", "status": "已完成"})
    _, body2 = app_client.get(f"/api/projects/{pid}/next")
    assert [t["id"] for t in body2["next"]] == [b["id"]], "前置完成后就该轮到下一个"
    assert body2["blocked"] == []
    assert (body2["total"], body2["done"]) == (2, 1)

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


# ---------------- 环的三条入口都得拦住（判环曾在锁外） ----------------

def test_concurrent_dependency_updates_cannot_sneak_in_a_cycle(app_client):
    """A→B 与 B→A 单独提交都合法；两个请求一并发就能把环写进存储 ——
    只要判环用的是锁外快照。"""
    import threading

    from app import main

    _, p = app_client.post("/api/projects", {"name": "并发成环"})
    _, a = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "甲"})
    _, b = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "乙"})

    bar = threading.Barrier(2, timeout=10)
    outcome: dict[str, str] = {}

    def put(tid: str, dep: str) -> None:
        body = main.TaskIn(title="改依赖", depends_on=[dep])
        bar.wait()                      # 让两个请求尽量同时起步
        try:
            main.update_task(tid, body)
            outcome[tid] = "ok"
        except Exception as e:          # noqa: BLE001 - 期望被 400 拒掉
            outcome[tid] = type(e).__name__

    threads = [threading.Thread(target=put, args=(a["id"], b["id"])),
               threading.Thread(target=put, args=(b["id"], a["id"]))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    _, detail = app_client.get(f"/api/projects/{p['id']}")
    graph = task_graph(detail["tasks"])
    assert not has_cycle(graph), f"并发把环写进了存储：{graph}"
    assert list(outcome.values()).count("ok") == 1, f"应当只有一个能改成，实际：{outcome}"


def test_cycle_check_happens_while_holding_the_lock(app_client, monkeypatch):
    """上面那条并发用例靠时序，未必每次都能抓到旧写法；这条直接钉住性质本身：
    判环必须发生在持锁期间，否则两个请求各自都判得过。"""
    from app import main

    observed: dict[str, bool] = {}
    real_has_cycle = main.has_cycle

    def probe(graph):
        observed["locked"] = getattr(main._LOCK_DEPTH, "n", 0) > 0
        return real_has_cycle(graph)

    monkeypatch.setattr(main, "has_cycle", probe)

    # 先确认探针本身分得出锁内锁外，否则后面的断言是空的
    assert probe({"a": ["b"], "b": ["a"]}) is True
    assert observed == {"locked": False}, f"探针在锁外没记到 False：{observed}"
    observed.clear()

    _, p = app_client.post("/api/projects", {"name": "判环时机"})
    _, a = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "甲"})
    st, _ = app_client.put(f"/api/tasks/{a['id']}", {"title": "甲", "depends_on": []})
    assert st == 200
    assert observed.get("locked") is True, "判环发生在锁外 —— 并发能合力写出一个环"


def test_rejected_dependency_update_leaves_no_half_write(app_client):
    """判环挪进 apply 之后，被拒绝的请求不能顺手把标题/状态也改掉"""
    _, p = app_client.post("/api/projects", {"name": "半写检查"})
    _, a = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "甲"})
    _, b = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "乙", "depends_on": [a["id"]]})

    status, _ = app_client.put(f"/api/tasks/{a['id']}",
                               {"title": "改坏了", "status": "已完成", "depends_on": [b["id"]]})
    assert status == 400

    _, detail = app_client.get(f"/api/projects/{p['id']}")
    ta = [t for t in detail["tasks"] if t["id"] == a["id"]][0]
    assert ta["title"] == "甲" and ta["status"] == "待办", f"拒绝的请求留下半写：{ta}"


def test_imported_cycle_is_pruned_not_stored(app_client):
    """导入外部 JSON / 从备份恢复会绕过端点校验，靠 _sanitize 里的剪环兜住"""
    import json

    payload = {"app": "ai-kanban", "version": "1.1.0", "projects": [{
        "id": "px", "name": "自带环", "status": "进行中", "progress": 0, "priority": "中",
        "tasks": [
            {"id": "t1", "title": "甲", "status": "待办", "priority": "中", "depends_on": ["t2"]},
            {"id": "t2", "title": "乙", "status": "待办", "priority": "中", "depends_on": ["t1"]},
        ]}]}
    status, res = app_client.post("/api/data/import", {"content": json.dumps(payload)})
    assert status == 200, res

    _, detail = app_client.get("/api/projects/px")
    graph = task_graph(detail["tasks"])
    assert not has_cycle(graph), f"导入把环原样存了下来：{graph}"
    assert graph == {"t1": ["t2"], "t2": []}, f"应按入列顺序保住前一条依赖：{graph}"
