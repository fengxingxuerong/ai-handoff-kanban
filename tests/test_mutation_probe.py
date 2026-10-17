# -*- coding: utf-8 -*-
"""探针自身的契约：跑完必须"不留痕" —— 内容与 mtime 都要还原。

为什么值得单独一个文件：`frozen_smoke` 判断"这个 exe 是不是落后于源码"，靠的是
`app/*.py` 的 mtime 与构建戳比较（后端被 PyInstaller 打进 .pyc，没法逐字节比对）。
而探针每跑一次都会把探过的文件改写两遍（注入、还原）。**只还原内容不还原时间戳**的话，
每次全量探针之后那条门禁都会稳定假红 —— 实测于 2026-10-18：
`frozen_smoke` 10 通过 / 1 失败，失败项点名 aiprompt.py、assign.py、due.py、
handoff_meta.py、idem.py、journal.py 一串"刚改过"的文件，而同一次里
"exe 内嵌的前端就是仓库里这一份"是过的，说明产物其实并不旧。

这条门禁一旦长期假红，人就会对它脱敏 —— 真出现"改了源码忘重打包"时也就没人看了。
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 显式把 mtime 拨到一小时前，判据才与"文件系统时间戳粒度"无关：
# Windows 上文件时间约 15.6ms 一跳，同一次 tick 里的两次写会拿到同一个值 ——
# 直接比"改前/改后"会让这条用例在缺陷还在时也变绿（A/B 实测过）。
OLD = time.time() - 3600


def _pin_mtime(path: Path) -> None:
    os.utime(path, (OLD, OLD))


def _load_probe():
    """按路径加载探针脚本（它不是包的一部分，import 会顺带执行模块级定义，无副作用）。"""
    spec = importlib.util.spec_from_file_location(
        "mutation_probe_under_test", ROOT / "scripts" / "mutation_probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mut(mid: str = "t") -> dict:
    return {"id": mid, "file": "fake_module.py", "old": "OLD = 1",
            "new": "OLD = 2", "why": "测试用", "tests": []}


def test_apply_and_run_restores_content_and_mtime(tmp_path, monkeypatch):
    probe = _load_probe()
    target = tmp_path / "fake_module.py"
    target.write_text("OLD = 1\n", encoding="utf-8")
    _pin_mtime(target)

    # 指到临时目录 + 假跑测试：这条用例要验的是"还原"，不是真的去变异源码
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    monkeypatch.setattr(probe, "run_tests", lambda files, timeout: ("survived", 0.0))

    res = probe.apply_and_run(_mut(), timeout=1)
    after = target.stat()

    assert res["status"] == "survived"
    assert target.read_text(encoding="utf-8") == "OLD = 1\n", "还原必须把内容写回去"
    assert after.st_mtime == OLD, (
        "还原必须连 mtime 一起拨回注入前，否则 frozen_smoke 的"
        "「产物不比源码旧」门禁会在每次跑完探针后假红")


def test_apply_and_run_touches_nothing_when_mutation_text_missing(tmp_path, monkeypatch):
    """变异文本没匹配上（源码改过、表没更新）时必须原样留着文件 —— 这是 inconclusive 那一档。"""
    probe = _load_probe()
    target = tmp_path / "fake_module.py"
    target.write_text("SOMETHING_ELSE = 1\n", encoding="utf-8")
    _pin_mtime(target)

    monkeypatch.setattr(probe, "ROOT", tmp_path)
    monkeypatch.setattr(probe, "run_tests",
                        lambda files, timeout: (_ for _ in ()).throw(AssertionError("不该跑到测试")))

    res = probe.apply_and_run(_mut(), timeout=1)

    assert res["status"] == "inconclusive"
    assert "没匹配上" in res["note"]
    assert target.read_text(encoding="utf-8") == "SOMETHING_ELSE = 1\n"
    assert target.stat().st_mtime == OLD
