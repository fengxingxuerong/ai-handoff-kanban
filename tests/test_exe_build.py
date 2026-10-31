# -*- coding: utf-8 -*-
"""打包时「产物被别人占着」那一套的处理（`scripts/exe_build.py`）。

**为什么值得单独一份用例**：2026-10-31 的真事故里，产物 `dist\\AI交接看板-MCP.exe` 被用户
自己的 AI 代理宿主当成 MCP 服务跑着 —— 清场杀掉它、宿主立刻又拉起、PyInstaller 写文件报
`WinError 5`。这一套逻辑修的就是它，而它守的是**打包这一步本身**：错了的代价是"门禁红在
一个与代码无关的地方"，或者更糟 —— 悄悄把产物写成旧的还报绿。

覆盖三件事：① 没被占时**不许白改名**（否则每轮打包都往 .stale/ 塞一个 19MB 的垃圾）；
② 被占时让位到 `.stale/`（不是删、不是原地覆盖）；③ 打包一次失败会重试一次，
且**两次失败的理由都要留在摘要里**（"重试过了也没用"与"压根没重试"不能长一样）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from exe_build import (                                            # noqa: E402
    STALE_SUBDIR, build_exe_step, clean_stale_binaries, move_aside_if_locked,
)


class _Res:
    """够用的 subprocess.CompletedProcess 替身。"""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _fake_exe(dist: Path, name: str = "AI交接看板-MCP.exe", size: int = 32) -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    p = dist / name
    p.write_bytes(b"MZ" * size)
    return p


def test_missing_exe_is_not_an_error(tmp_path):
    """产物还不存在时（第一次打包）什么都不用做，也不许报错或造目录。"""
    assert move_aside_if_locked(tmp_path / "dist" / "nope.exe",
                                stale_dir=tmp_path / "dist" / STALE_SUBDIR) == ""
    assert not (tmp_path / "dist" / STALE_SUBDIR).exists()


def test_unlocked_exe_is_left_alone(tmp_path):
    """**没被占就一个字都不许动**：探针（删一次写回来）之后文件内容原样，且不产生 .stale 垃圾。

    这条是防"每轮打包白留一份 19MB 旧产物"的 —— 探针只许探，不许手滑留在旁边。
    """
    dist = tmp_path / "dist"
    exe = _fake_exe(dist)
    note = move_aside_if_locked(exe, stale_dir=dist / STALE_SUBDIR)
    assert note == ""
    assert exe.exists() and exe.read_bytes() == b"MZ" * 32
    assert not (dist / STALE_SUBDIR).exists(), "没被占却造了 .stale 目录"
    assert [p.name for p in dist.iterdir()] == [exe.name], "原地多出了探针文件"


def test_locked_exe_moves_aside_into_stale_dir(tmp_path, monkeypatch):
    """被占着时：原地让开（打包才能把新产物写到契约路径），旧文件挪进 .stale/ 而不是删。

    「被占」按**真实约束**模拟：删不掉（PyInstaller 要做的也是删/覆盖；改名在 Windows 上
    对运行中的 exe 是放行的，所以拿改名当判据会一路假绿）。
    """
    dist = tmp_path / "dist"
    exe = _fake_exe(dist)
    real_unlink = Path.unlink

    def locked_unlink(self, *a, **k):
        if self.name == exe.name and self.parent == exe.parent:
            raise PermissionError(5, "拒绝访问。")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    note = move_aside_if_locked(exe, stale_dir=dist / STALE_SUBDIR)
    assert "改名让位" in note and exe.name in note
    assert not exe.exists(), "旧产物还在原地 —— 打包照样 WinError 5"
    moved = list((dist / STALE_SUBDIR).iterdir())
    assert len(moved) == 1 and moved[0].read_bytes() == b"MZ" * 32
    assert moved[0].name.startswith("AI交接看板-MCP.old-"), moved[0].name


def test_locked_exe_that_cannot_be_renamed_says_so(tmp_path, monkeypatch):
    """连改名都不行（极端锁定）时如实回一句，别装作让开了 —— 打包那步会用上这句。"""
    exe = _fake_exe(tmp_path / "dist")

    def all_locked(self, *a, **k):
        raise PermissionError(5, "拒绝访问。")

    monkeypatch.setattr(Path, "unlink", all_locked)
    monkeypatch.setattr(Path, "rename", all_locked)
    note = move_aside_if_locked(exe, stale_dir=tmp_path / "dist" / STALE_SUBDIR)
    assert "让不开" in note and exe.exists()


def test_clean_stale_removes_leftovers_and_keeps_locked_ones(tmp_path, monkeypatch):
    """上一轮挪走的旧产物：能删就删；还被人握着的留着（下轮再清），不许因此报错。"""
    stale = tmp_path / "dist" / STALE_SUBDIR
    stale.mkdir(parents=True)
    (stale / "AI交接看板-MCP.old-20261031-224500.exe").write_bytes(b"old")
    locked = stale / "AI交接看板-MCP.old-20261031-224600.exe"
    locked.write_bytes(b"locked")
    real_unlink = Path.unlink

    def fake_unlink(self, *a, **k):
        if self.name.endswith("224600.exe"):
            raise PermissionError(5, "拒绝访问。")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    left = clean_stale_binaries(stale)
    assert left == [locked.name]
    assert sorted(p.name for p in stale.iterdir()) == [locked.name]


def test_build_retries_once_with_a_different_reason(tmp_path, monkeypatch):
    """第一次红 → 重新清场让位再打一轮：第二轮绿就算过，且摘要里说得出"重试过"。"""
    calls: list[dict] = []
    monkeypatch.setattr("exe_build.sweep_stale_binaries", lambda *a, **k: [4242])

    def runner(argv, **kw):
        calls.append(kw)
        return _Res(1, stderr="PermissionError: [WinError 5] 拒绝访问。") if len(calls) == 1 \
            else _Res(0, stdout="Build complete! 产物已更新")

    exe = _fake_exe(tmp_path / "dist")
    ok, dt, note = build_exe_step(["pyinstaller"], exe, runner=runner)
    assert ok and len(calls) == 2, "只打了一轮 —— 那就等于没有重试"
    assert "第 1 轮失败" in note and "再打一轮" in note and "已更新" in note
    assert "4242" in note, "清不掉的 PID 要报出来（它就是那个外部占用者）"
    assert all("cwd" not in kw for kw in calls), "runner 的 cwd 由调用方注入，步骤自己不许另设"


def test_build_fails_loudly_when_the_retry_also_fails(tmp_path, monkeypatch):
    """两轮都红：把两次的理由都带出来，别让"重试过了也没用"退化成一句"打包失败"。"""
    monkeypatch.setattr("exe_build.sweep_stale_binaries", lambda *a, **k: [])

    def runner(argv, **kw):
        return _Res(1, stderr="PermissionError: [WinError 5] 拒绝访问。")

    exe = _fake_exe(tmp_path / "dist")
    ok, _, note = build_exe_step(["pyinstaller"], exe, runner=runner)
    assert not ok
    assert note.count("第 ") == 2 and "第 1 轮失败" in note and "第 2 轮失败" in note


def test_build_honours_a_real_lock_end_to_end(tmp_path, monkeypatch):
    """真锁一次（不是 mock 假锁）：被占的产物要**真的**让开，打包才能把新产物写进契约路径。"""
    monkeypatch.setattr("exe_build.sweep_stale_binaries", lambda *a, **k: [])
    dist = tmp_path / "dist"
    exe = _fake_exe(dist, size=8)
    real_unlink = Path.unlink

    def locked_unlink(self, *a, **k):
        if self.name == exe.name and self.parent == exe.parent:
            raise PermissionError(5, "拒绝访问。")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked_unlink)

    def runner(argv, **kw):
        exe.write_bytes(b"NEW-PRODUCT")           # 新产物写进契约路径
        return _Res(0, stdout="Build complete!")

    ok, _, _ = build_exe_step(["pyinstaller"], exe, runner=runner)
    assert ok and exe.read_bytes() == b"NEW-PRODUCT"
    stale = dist / STALE_SUBDIR
    assert stale.is_dir() and list(stale.iterdir()), "旧产物应该被留在 .stale/ 里"


def test_gate_packaging_goes_through_this_helper():
    """门禁的两步打包必须都走 `build_exe_step`，而不是各自裸调 PyInstaller。

    这条是"接线"钉子：`exe_build.py` 自己测绿、而 `ci_local.py` 从没调它，是最容易的假绿形态
    （本项目已抓过同型：标签写着覆盖率高、覆盖率门槛却没传下去）。
    """
    src = (ROOT / "scripts" / "ci_local.py").read_text(encoding="utf-8")
    flat = " ".join(ln.strip() for ln in src.splitlines())
    assert "from exe_build import build_exe_step" in src
    assert flat.count("build_exe_step( [PY, \"-m\", \"PyInstaller\"") == 2, \
        "两次打包（GUI + MCP）都必须走 build_exe_step，裸调 PyInstaller 没有清场与让位"
    # 挪走的旧产物不许留在 dist/ 根下（那是"dist 里有什么"的检查与 PyInstaller 自己会扫的面）
    assert STALE_SUBDIR == ".stale"
