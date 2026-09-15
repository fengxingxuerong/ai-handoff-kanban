# -*- coding: utf-8 -*-
"""版本与构建时间：`BUILD_TIME` 必须是"算出来的"，不能手填

为什么要专门测这个：这里原本是个手填常量（`"2026-09-15 12:00"`），而源码当天改到 23:26、
exe 是 23:46 打的 —— 启动日志和版本条却报 12:00。手填的时间戳一定会漂，
"靠它判断 exe 是否落后于源码"的意图也就落空了。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

from app import version


def test_source_build_time_tracks_newest_source_file(tmp_path):
    """源码态：应当等于被监视文件里最新的 mtime（不是某个写死的常量）"""
    (tmp_path / "app").mkdir()
    (tmp_path / "web").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "app" / "main.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "web" / "index.html").write_text("<html>", encoding="utf-8")
    (tmp_path / "scripts" / "desktop.py").write_text("y = 2", encoding="utf-8")
    # 把 web/index.html 的 mtime 设成明天，它应当胜出
    future = datetime.now().timestamp() + 86400
    import os
    os.utime(tmp_path / "web" / "index.html", (future, future))

    got = version.source_build_time(tmp_path)
    assert got == datetime.fromtimestamp(future).strftime("%Y-%m-%d %H:%M")
    assert version.newest_source_mtime(tmp_path) == pytest.approx(future, abs=1)


def test_source_build_time_ignores_unrelated_files(tmp_path):
    """数据目录里的文件、README 之类不该影响"代码最后改于何时" """
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "version.py").write_text("v", encoding="utf-8")
    old = datetime(2020, 1, 1).timestamp()
    import os
    os.utime(tmp_path / "app" / "version.py", (old, old))
    (tmp_path / "projects.json").write_text("{}", encoding="utf-8")     # 无关
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")         # 无关

    assert version.source_build_time(tmp_path) == "2020-01-01 00:00"


def test_generated_stamp_is_not_treated_as_source(tmp_path):
    """构建戳是打包产物，不该影响"源码最后改于何时" —— 否则它会顶掉真实时间，
    把"exe 落后于源码"的信号掩盖掉"""
    import os

    (tmp_path / "app").mkdir()
    src = tmp_path / "app" / "main.py"
    src.write_text("x = 1", encoding="utf-8")
    old = datetime(2026, 1, 1, 12, 0).timestamp()
    os.utime(src, (old, old))

    stamp = tmp_path / "app" / "_build_stamp.py"
    stamp.write_text('STAMP = "2099-01-01 00:00"', encoding="utf-8")

    assert version.source_build_time(tmp_path) == "2026-01-01 12:00"


def test_missing_sources_do_not_crash(tmp_path):
    assert version.newest_source_mtime(tmp_path) == 0.0
    assert version.source_build_time(tmp_path) == "未知"


def test_frozen_uses_the_stamp():
    """打包态：用构建期写死的戳（源码被解包到 _MEIPASS，mtime 没有意义）"""
    assert version.resolve_build_time(frozen=True, stamp="2026-09-15 23:46") == "2026-09-15 23:46"


def test_frozen_without_stamp_says_unknown_not_a_lie():
    """漏跑 make_stamp 时如实说"未知"，而不是编一个时间出来骗用户"""
    assert version.resolve_build_time(frozen=True, stamp="") == "未知"


def test_module_level_build_time_is_sane():
    """当前进程里这个值要能解析回一个真实时间（源码态就该是最近改动的日子）"""
    parsed = datetime.strptime(version.BUILD_TIME, "%Y-%m-%d %H:%M")
    assert parsed.year >= 2024, version.BUILD_TIME
    assert version.APP_VERSION == version.__version__


def test_health_reports_build_time_and_frozen_flag(app_client):
    status, body = app_client.get("/api/health")
    assert status == 200
    assert body["build_time"] == version.BUILD_TIME
    assert body["version"] == version.APP_VERSION
    assert body["frozen"] is bool(getattr(sys, "frozen", False))


def test_make_stamp_script_writes_importable_module(tmp_path, monkeypatch):
    """生成器要真的产出一个能被 import 的模块（口径与 version.py 读的一致）"""
    import importlib.util

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location(
            "make_stamp", Path(__file__).resolve().parent.parent / "scripts" / "make_stamp.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)

    target = tmp_path / "_build_stamp.py"
    monkeypatch.setattr(mod, "TARGET", target)
    assert mod.main() == 0
    assert target.exists()

    text = target.read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(text, str(target), "exec"), ns)          # noqa: S102
    stamp = ns["STAMP"]
    datetime.strptime(stamp, "%Y-%m-%d %H:%M")            # 能解析 = 口径一致
