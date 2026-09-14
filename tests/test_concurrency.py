# -*- coding: utf-8 -*-
"""并发正确性：读-改-写必须在一个临界区内（历史事故：20 并发只落盘 3 条）"""
from __future__ import annotations

import concurrent.futures as cf

import pytest


@pytest.mark.parametrize("n", [20, 30])
def test_concurrent_create_keeps_all(fresh_server, n):
    before = set(fresh_server.project_ids())
    with cf.ThreadPoolExecutor(max_workers=n) as ex:
        codes = list(ex.map(lambda i: fresh_server.create_project(f"并发-{i}")[0], range(n)))
    after = set(fresh_server.project_ids())
    new = after - before

    assert sum(1 for c in codes if c == 201) == n, "并非所有请求都返回 201"
    assert len(new) == n, f"并发丢失数据：201 有 {n} 个，实际落盘 {len(new)} 个"


def test_concurrent_task_create_keeps_all(fresh_server):
    _, project = fresh_server.create_project("并发任务项目")
    pid = project["id"]

    def add(i):
        return fresh_server.post(f"/api/projects/{pid}/tasks",
                                 {"title": f"任务-{i}", "status": "待办", "priority": "中"})[0]

    n = 20
    with cf.ThreadPoolExecutor(max_workers=n) as ex:
        codes = list(ex.map(add, range(n)))

    _, detail = fresh_server.get(f"/api/projects/{pid}")
    assert sum(1 for c in codes if c == 201) == n
    assert len(detail["tasks"]) == n, f"任务并发丢失：{n} 个请求只落盘 {len(detail['tasks'])} 个"


def test_mixed_write_does_not_lose_project(fresh_server):
    """一边改项目字段一边加任务，双方都应生效（最小临界区验证）"""
    _, project = fresh_server.create_project("混合写入")
    pid = project["id"]

    def update_next(i):
        return fresh_server.put(f"/api/projects/{pid}", {
            "name": "混合写入", "status": "进行中", "progress": i % 101,
            "priority": "中", "next_action": f"第 {i} 次更新"})[0]

    def add_task(i):
        return fresh_server.post(f"/api/projects/{pid}/tasks",
                                 {"title": f"混-{i}", "status": "待办", "priority": "中"})[0]

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(update_next, i) for i in range(10)] + [ex.submit(add_task, i) for i in range(10)]
        codes = [f.result() for f in futs]

    assert all(c in (200, 201) for c in codes)
    _, detail = fresh_server.get(f"/api/projects/{pid}")
    assert len(detail["tasks"]) == 10
    assert detail["next_action"].startswith("第 ")
