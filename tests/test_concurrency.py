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


def test_cross_process_lock_is_reentrant_within_the_same_thread():
    """自愈写盘发生在 mutate() 持锁期间（_load_data 里）。
    Windows 的字节锁按句柄生效，同进程再开一个句柄也会互相冲突 ——
    不可重入的话这里不会报错，而是等满 timeout 再抛 TimeoutError。"""
    import time

    from app.main import _cross_process_lock

    t0 = time.time()
    with _cross_process_lock(timeout=3.0):
        with _cross_process_lock(timeout=3.0):
            pass
    assert time.time() - t0 < 2.0, "嵌套取锁被当成争用处理了（会拖满 timeout）"


def test_cross_process_lock_still_blocks_another_thread():
    """重入只对同线程放行；换个线程仍必须排队，否则这层锁就白加了"""
    import threading
    import time

    from app.main import _cross_process_lock

    entered = threading.Event()
    order: list[str] = []

    def worker() -> None:
        with _cross_process_lock(timeout=8.0):
            order.append("second")
            entered.set()

    with _cross_process_lock(timeout=8.0):
        order.append("first")
        th = threading.Thread(target=worker)
        th.start()
        assert not entered.wait(0.4), "另一线程拿到了同一把锁 —— 重入放行放到了别人身上"
    th.join(timeout=15)      # 必须在释放之后再等，否则是自己跟自己要锁
    assert order == ["first", "second"]
    assert not th.is_alive()
