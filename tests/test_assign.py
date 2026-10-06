# -*- coding: utf-8 -*-
"""P1 任务自动分配：优先级 + 就绪口径 + 跳过/覆盖 + 接口契约（2026-10-01 新增）

分配口径守三条（与 project_stats / next 同源，见 app/assign.py 头注释）：
- 就绪判定复用 `executable_tasks`（未完成 + 依赖已满足），不在这里重写第二遍；
- 默认跳过已有负责人的任务，`overwrite=True` 才覆盖（保护手工指派）；
- 空 agent / 无候选时不落盘（幂等，`changed=False`，一次字节都不写）。
"""
from __future__ import annotations

import pytest

from app.assign import DEFAULT_PRIORITY, PRIORITY_SET, allocatable, plan_assign


# ---------------- 纯函数：allocatable ----------------

def _task(tid: str, priority: str = "高", agent: str | None = None,
          depends_on: list[str] | None = None, done: bool = False) -> dict:
    t = {"id": tid, "title": f"任务{tid}", "priority": priority,
         "status": "已完成" if done else "待办", "agent": agent}
    if depends_on:
        t["depends_on"] = depends_on
    return t


def test_allocatable_matches_priority_and_readiness():
    """只认『指定优先级 + 未完成 + 依赖已就绪』"""
    tasks = [
        _task("a", priority="高"),
        _task("b", priority="高", done=True),          # 已完成：不算
        _task("c", priority="中"),                     # 优先级不匹配
        _task("d", priority="高", depends_on=["a"]),   # 依赖未就绪（a 未完成）
    ]
    got = [t["id"] for t in allocatable(tasks, "高")]
    assert got == ["a"], got


def test_allocatable_dep_satisfied_by_done_task():
    """前置已完成则算就绪；依赖指向不存在的任务按已满足（executable_tasks 口径）"""
    tasks = [
        _task("前置", priority="高", done=True),
        _task("后续", priority="高", depends_on=["前置"]),
        _task("孤儿", priority="高", depends_on=["不存在"]),
    ]
    got = [t["id"] for t in allocatable(tasks, "高")]
    assert got == ["后续", "孤儿"], got


def test_allocatable_done_tasks_in_no_status():
    """executable_tasks 只要『进行中』或『待办』——已完成/阻塞都不在候选"""
    tasks = [_task("w", priority="高", done=False), _task("x", priority="高", done=True)]
    assert all(t["id"] == "w" for t in allocatable(tasks, "高"))


def test_allocatable_unknown_priority_raises():
    with pytest.raises(ValueError):
        allocatable([], "紧急")


def test_allocatable_review_tasks_are_not_candidates():
    """待审查任务不能再分配：Agent 干完活等人工验收，不该被又派给谁"""
    tasks = [
        {"id": "r", "title": "验收中", "priority": "高", "status": "待审查", "agent": "cline"},
        _task("f", priority="高"),
    ]
    got = [t["id"] for t in allocatable(tasks, "高")]
    assert got == ["f"], f"待审查任务不该进候选：{got}"


def test_priority_set_matches_task_whitelist():
    """优先级白名单与接口的 Literal 一致（改一边要两边一起改）"""
    assert set(PRIORITY_SET) == {"高", "中", "低"}
    assert DEFAULT_PRIORITY == "高"


# ---------------- 纯函数：plan_assign ----------------

def test_plan_assign_default_skips_taken_and_assigns_free():
    tasks = [
        _task("free", priority="高"),
        _task("taken", priority="高", agent="手工分配"),
    ]
    assign_tasks, skipped = plan_assign(tasks, "cline", priority="高")
    assert [t["id"] for t in assign_tasks] == ["free"]
    assert len(skipped) == 1 and skipped[0]["id"] == "taken"
    assert "手工分配" in skipped[0]["reason"]
    # 纯函数：输入原样，一个字段都没改
    assert tasks[0].get("agent") is None and tasks[1].get("agent") == "手工分配"


def test_plan_assign_blank_agent_assigns_nothing():
    tasks = [_task("a", priority="高"), _task("b", priority="中")]
    assign_tasks, skipped = plan_assign(tasks, "   ")   # 空串防呆
    assert assign_tasks == [] and len(skipped) == 1
    assert skipped[0]["reason"].startswith("没有指定")


def test_plan_assign_idempotent():
    """全部已分配后再次调用结果为空 —— 接口据此不落盘"""
    tasks = [_task("a", priority="高"), _task("b", priority="高")]
    assign_tasks, _ = plan_assign(tasks, "cline")
    assert len(assign_tasks) == 2
    for t in assign_tasks:
        t["agent"] = "cline"                      # 模拟第一轮写盘
    again, skipped = plan_assign(tasks, "cline")
    # 第二轮：没有可分配的（幂等），但已分配的都进 skipped『已有负责人』
    assert again == []
    assert [s["id"] for s in skipped] == ["a", "b"]
    assert all("已有负责人" in s["reason"] for s in skipped)


def test_plan_assign_overwrite_reassigns():
    tasks = [_task("a", priority="高", agent="旧代理")]
    assign_tasks, skipped = plan_assign(tasks, "新代理", overwrite=True)
    assert [t["id"] for t in assign_tasks] == ["a"]
    assert skipped == []


def test_plan_assign_partial_overwrite_keeps_others():
    """overwrite 只重指派本轮候选里已有主人的；未完成更高优先级不在候选、旧负责人不动"""
    tasks = [
        _task("hi-free", priority="高"),
        _task("hi-taken", priority="高", agent="手工A"),
        _task("mid-taken", priority="中", agent="手工B"),
    ]
    assign_tasks, skipped = plan_assign(tasks, "cline", priority="高", overwrite=True)
    assert sorted(t["id"] for t in assign_tasks) == ["hi-free", "hi-taken"]
    # 中优先级整个不在本轮候选 —— skipped 不该把中优先级任务也算上
    assert skipped == []


# ---------------- 接口契约 ----------------

def _seed_board(server):
    status, project = server.create_project("自动分配测试", priority="高")
    assert status == 201
    pid = project["id"]
    tid_map = {}
    for title, pri, agent in [
        ("高优先级空闲", "高", None),
        ("高优先级有主", "高", "旧代理"),
        ("中优先级空闲", "中", None),
        ("已完成的高", "高", None),
    ]:
        body = {"title": title, "priority": pri, "status": "已完成" if "已完成" in title else "待办"}
        if agent:
            body["agent"] = agent
        status, t = server.post(f"/api/projects/{pid}/tasks", body)
        assert status == 201
        tid_map[title] = t["id"]
    return pid, tid_map


def test_assign_writes_agent_and_reports(server):
    pid, tid_map = _seed_board(server)
    status, res = server.post(f"/api/projects/{pid}/assign",
                              {"agent": "cline", "priority": "高"})
    assert status == 200
    assert res["count"] == 1 and res["skipped_count"] == 1
    assert res["assigned"] == [{"id": tid_map["高优先级空闲"], "title": "高优先级空闲"}]
    assert res["skipped"][0]["id"] == tid_map["高优先级有主"]
    assert "旧代理" in res["skipped"][0]["reason"]
    assert res["priority"] == "高" and res["agent"] == "cline"

    # 真的写进去了
    status, detail = server.get(f"/api/projects/{pid}")
    by_title = {t["title"]: t for t in detail["tasks"]}
    assert by_title["高优先级空闲"]["agent"] == "cline"
    assert by_title["高优先级有主"]["agent"] == "旧代理"     # 跳过 = 没动


def test_assign_is_idempotent_when_all_done(server):
    """第一轮后所有高优先级都有主；第二轮 count=0、不落盘，但跳过清单带上原因"""
    pid, _ = _seed_board(server)
    server.post(f"/api/projects/{pid}/assign", {"agent": "cline"})
    status, res = server.post(f"/api/projects/{pid}/assign", {"agent": "cline"})
    assert status == 200
    assert res["count"] == 0 and res["assigned"] == []
    # 两个高优先级此时都有主：一个本来就是旧代理，一个是上一轮刚分的
    assert res["skipped_count"] == 2
    assert all("已有负责人" in s["reason"] for s in res["skipped"])


def test_assign_overwrite_flag(server):
    pid, tid_map = _seed_board(server)
    status, res = server.post(f"/api/projects/{pid}/assign",
                              {"agent": "新代理", "priority": "高", "overwrite": True})
    assert status == 200
    assert sorted(a["id"] for a in res["assigned"]) == sorted(
        [tid_map["高优先级空闲"], tid_map["高优先级有主"]])
    status, detail = server.get(f"/api/projects/{pid}")
    by_title = {t["title"]: t for t in detail["tasks"]}
    assert by_title["高优先级有主"]["agent"] == "新代理"


def test_assign_priority_filter(server):
    pid, tid_map = _seed_board(server)
    status, res = server.post(f"/api/projects/{pid}/assign",
                              {"agent": "cline", "priority": "中"})
    assert status == 200
    assert [a["id"] for a in res["assigned"]] == [tid_map["中优先级空闲"]]
    assert res["skipped"] == []


def test_assign_dep_blocked_is_excluded(server):
    """依赖未就绪的高优先级任务不分配（与 /next 同口径）"""
    status, project = server.create_project("依赖阻塞分配", priority="高")
    pid = project["id"]
    _, t = server.post(f"/api/projects/{pid}/tasks",
                       {"title": "前置", "priority": "高"})
    _, blocked = server.post(f"/api/projects/{pid}/tasks",
                             {"title": "被阻塞", "priority": "高", "depends_on": [t["id"]]})
    status, res = server.post(f"/api/projects/{pid}/assign", {"agent": "cline"})
    assert status == 200
    assigned_ids = {a["id"] for a in res["assigned"]}
    assert blocked["id"] not in assigned_ids, "被前置阻塞的任务不应被分配"
    assert res["count"] == 1


def test_assign_validation_and_404(server):
    status, project = server.create_project("校验")
    pid = project["id"]

    status, _ = server.post(f"/api/projects/{pid}/assign",
                            {"agent": "", "priority": "高"})
    assert status == 422, "空 agent 必须 422"

    status, _ = server.post(f"/api/projects/{pid}/assign",
                            {"agent": "cline", "priority": "紧急"})
    assert status == 422, "非法优先级必须 422"

    status, _ = server.post("/api/projects/no-such-project-xyz/assign",
                            {"agent": "cline", "priority": "高"})
    assert status == 404, "项目不存在必须 404"


def test_assign_no_candidate_writes_nothing(server):
    """项目里没有达标候选时，接口返回空结果且不落盘（updated_at 不变）"""
    status, project = server.create_project("无候选")
    pid = project["id"]
    status, before = server.get(f"/api/projects/{pid}")
    status, res = server.post(f"/api/projects/{pid}/assign", {"agent": "cline"})
    assert status == 200 and res["count"] == 0
    status, after = server.get(f"/api/projects/{pid}")
    assert before["updated_at"] == after["updated_at"], "无候选不应落盘"