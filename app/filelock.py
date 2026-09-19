# -*- coding: utf-8 -*-
"""跨进程文件锁（msvcrt / fcntl 字节锁）。

单独一个模块的原因是用到它的两边不能互相 import：`app.llm` 要给 usage.jsonl 上锁，
而 `app.main` 已经 import 了 `llm`（llm 反向 import main 就成环）。
两边各抄一份 msvcrt/fcntl 写法的后果不是"重复"这么简单：锁的语义（超时、重入、
解锁失败要不要抛）一旦两处漂移，最难查的就是"只在其中一条写入路径上失效"。
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


def _acquire(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def blocking_lock(path, *, timeout: float = 15.0, what: str = "数据文件", poll: float = 0.05):
    """对 `path` 这把侧车锁文件取**进程间**互斥；拿不到就轮询等到超时，然后抛 TimeoutError。

    锁加在侧车文件而不是被保护的那份数据上：Windows 下 `os.replace()` 换不掉一个
    还被别的进程开着的文件，而写入方的套路正是"写临时文件 → 原子替换"，
    那一步必须随时做得成。

    同线程重入这里不管（字节锁按句柄生效，重开句柄就是重新抢锁）——
    需要重入的调用方自己在外面记深度，见 `main._cross_process_lock`。
    """
    path = Path(path)
    fh = open(path, "a+b")
    try:
        deadline = time.time() + timeout
        while True:
            try:
                _acquire(fh)
                break
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(f"等待{what}锁超时（{timeout}s）：{path}")
                time.sleep(poll)
        try:
            yield
        finally:
            # 解锁失败也要把句柄关掉，否则泄漏的是文件句柄而不是一个警告
            try:
                _release(fh)
            except Exception:
                pass
    finally:
        fh.close()
