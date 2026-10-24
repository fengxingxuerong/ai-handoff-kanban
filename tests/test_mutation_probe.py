# -*- coding: utf-8 -*-
"""探针自身的契约：跑完必须"不留痕"（内容与 mtime 都要还原），启动要能识破残渣。

为什么值得单独一个文件：`frozen_smoke` 判断"这个 exe 是不是落后于源码"，靠的
`app/*.py` 的 mtime 与构建戳比较（后端被 PyInstaller 打进 .pyc，没法逐字节比对）。
而探针每跑一次都会把探过的文件改写两遍（注入、还原）。**只还原内容不还原时间戳**的话，
每次全量探针之后那条门禁都会稳定假红 —— 实测于 2026-10-18：
`frozen_smoke` 10 通过 / 1 失败，失败项点名 aiprompt.py、assign.py、due.py、
handoff_meta.py、idem.py、journal.py 一串"刚改过"的文件，而同一次里
"exe 内嵌的前端就是仓库里这一份"是过的，说明产物其实并不旧。

这条门禁一旦长期假红，人就会对它脱敏 —— 真出现"改了源码忘重打包"时也就没人看了。
2026-10-24 起预检也是本文件的契约：上次中断留的残渣必须第 0 秒被识破
（当日实测：残渣让 pytest 先红 80 秒、探针再空跑 8.5 分钟，结论只有一行计数）。
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


def _pre(mid: str, old: str, new: str) -> dict:
    return {"id": mid, "file": "fake_module.py", "old": old, "new": new,
            "why": "测试用", "tests": []}


def test_preflight_flags_leftover_debris_from_interrupted_run(tmp_path):
    """old 缺、new 在 = 上次中断的残渣：要指名文件、说破成因并给 git checkout 的处置。

    A/B 依据是 2026-10-24 的真实事故：`repo_watch.py` 里留着一处逐字等于
    `repo_watch_audit_uncapped` 注入文本的残渣，pytest 红了 80 秒、探针空跑 8.5 分钟，
    最后只有一行"存活/异常 1"。预检的价值就是把诊断挪到第 0 秒、把修法写在报错里。
    """
    probe = _load_probe()
    (tmp_path / "fake_module.py").write_text("OLD = 2\n", encoding="utf-8")

    problems = probe.preflight([_pre("t", "OLD = 1", "OLD = 2")], root=tmp_path)

    assert len(problems) == 1
    m, why = problems[0]
    assert m["id"] == "t"
    assert "残渣" in why
    assert "git checkout" in why and "fake_module.py" in why


def test_preflight_flags_unsynced_table_and_empty_new_deletion(tmp_path):
    """old、new 都缺 = 表没同步（哑火）；删除型变异 new 为空、残渣判不出，落同一档。

    空串 `in` 任何文本都为真 —— 残渣判定必须先排除空 new，否则每条删除型变异
    都会在干净源码上被诬告成"残渣"。
    """
    probe = _load_probe()
    (tmp_path / "fake_module.py").write_text("SOMETHING_ELSE = 3\n", encoding="utf-8")

    problems = probe.preflight(
        [_pre("a", "OLD = 1", "OLD = 2"), _pre("b", "OLD = 1", "")], root=tmp_path)

    assert [m["id"] for m, _ in problems] == ["a", "b"]
    assert all("MUTATIONS 没同步" in why for _, why in problems)
    assert all("git checkout" not in why for _, why in problems), (
        "没有 new 文本佐证就不能开'残渣'的药方 —— 万一文件里是用户的手工改动，"
        "照方抓药会把它们一起 checkout 掉")


def test_preflight_clean_when_every_old_matches(tmp_path):
    probe = _load_probe()
    (tmp_path / "fake_module.py").write_text("OLD = 1\n", encoding="utf-8")

    assert probe.preflight([_pre("t", "OLD = 1", "OLD = 2")], root=tmp_path) == []
