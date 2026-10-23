# -*- coding: utf-8 -*-
"""跨进程文件锁的直测。

以前 `app/filelock.py` 只靠 `main` / `llm` 的写入路径**间接**覆盖（78%）——
这个模块的风险恰恰不在"顺手能跑到的那条路"，而在三处只在出事时才走的分支：

1. 抢不到锁 → 轮询到超时 → `TimeoutError`（写错就变成死锁或静默跳过互斥）；
2. 解锁抛异常 → 必须吞掉，但**句柄仍要关**（吞异常吞掉了 close 就是句柄泄漏，
   Windows 上表现为锁文件删不掉、原子替换失败）；
3. 锁文件不存在时能自己建出来（侧车锁的第一个用户没有"先创建"这一步）。

这三条都在这里钉住。POSIX 分支（`fcntl`）在本机不可达，**不伪造 os.name 去凑覆盖率**：
`os.name` 是解释器级常量，改它等于让整个进程假装成另一个系统，测出来的东西比不测更可疑。
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from app import filelock


def test_lock_is_exclusive_between_two_handles(tmp_path):
    """一个句柄持锁期间，另一个必须抢不到，并在超时后如实报错。"""
    path = tmp_path / "data.lock"
    holding = threading.Event()
    release = threading.Event()

    def holder():
        with filelock.blocking_lock(path, timeout=5, what="看板数据"):
            holding.set()
            release.wait(5)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert holding.wait(5), "后台线程没拿到锁"

    started = time.time()
    with pytest.raises(TimeoutError) as ei:
        with filelock.blocking_lock(path, timeout=0.3, what="看板数据"):
            pytest.fail("别人持锁时不该拿到锁")
    elapsed = time.time() - started
    release.set()
    t.join(5)

    assert "看板数据" in str(ei.value), f"超时信息没说清是哪把锁：{ei.value}"
    assert str(path) in str(ei.value), f"超时信息里没有锁文件路径：{ei.value}"
    assert elapsed >= 0.25, f"没等到超时就放弃了（{elapsed:.2f}s），互斥形同虚设"


def test_lock_is_reusable_after_release(tmp_path):
    """退出上下文就解锁：同一路径能连续取到，不会自己把自己锁死。"""
    path = tmp_path / "data.lock"
    for _ in range(3):
        with filelock.blocking_lock(path, timeout=1):
            pass


def test_lock_release_failure_is_swallowed_but_handle_is_closed(tmp_path, monkeypatch):
    """解锁失败只吞异常，不许吞掉 close。

    句柄没关的话，Windows 上这个锁文件删不掉 —— 而调用方（`mutate()` 的原子替换）
    正是靠"能随时换掉文件"活着。
    """
    path = tmp_path / "data.lock"

    def boom(fh):
        raise OSError("解锁失败（模拟）")

    monkeypatch.setattr(filelock, "_release", boom)
    with filelock.blocking_lock(path, timeout=1):
        pass
    monkeypatch.undo()

    os.remove(path)          # 句柄泄漏时这一步会 PermissionError，用例就红在这里
    assert not path.exists()


def test_lock_file_is_created_on_first_use(tmp_path):
    """侧车锁文件不存在时自己建，不要求调用方先 touch。"""
    path = tmp_path / "nested" / "data.lock"
    path.parent.mkdir()
    assert not path.exists()
    with filelock.blocking_lock(path, timeout=1):
        assert path.exists(), "锁文件没有按需创建"


def test_lock_does_not_disturb_file_content(tmp_path):
    """锁文件里万一有残留内容，取锁和解锁都不该把它改掉（只锁第 0 字节）。"""
    path = tmp_path / "data.lock"
    path.write_bytes(b"stale\n")
    with filelock.blocking_lock(path, timeout=1):
        pass
    assert path.read_bytes().startswith(b"stale")
