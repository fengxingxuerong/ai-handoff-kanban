# -*- coding: utf-8 -*-
"""在**模拟 CI 的浏览器环境**下真跑一遍 E2E：只用 Playwright 自带 chromium。

为什么需要这个脚本（2026-10-25）：
`tests/test_frontend_e2e.py` 的浏览器链是三级降级（系统 Edge → 系统 Chrome → 自带 chromium），
而 `.github/workflows/ci.yml` 自己写着「Windows runner 上没有系统 Edge/Chrome」——
**CI 上跑的就是第三级**。此前那级根本不存在，于是这 75+ 条真浏览器用例在 CI 上整份静默跳过、
退出码照样 0，而这个项目最强的验证层从没在唯一自动化环境里跑过。

本机开发时系统浏览器都在，走的是第一级 —— 也就是说"第三级到底行不行"**在本机默认跑不到**。
这个脚本把那条路补上：进程内把浏览器链强制成 `[__not_a_browser__, None]`（第一级必然失败，
只有真会 fallback 才起得来），并**记录实际使用的 channel** 来证明它真的用了第三级，
而不是"跑绿了就算"——后者在系统浏览器可用时恒真，是典型的假绿。

**不改源码**：用 pytest 插件在进程内改模块属性。这一点是有意的 —— "改文件再还原"的工具
一旦被外部信号打断就会把变异留在源码里（2026-10-22 与 10-24 各发生过一次，
后者促成 `mutation_probe --preflight`）。

用法：
    .venv\\Scripts\\python.exe scripts/e2e_ci_browser_check.py           # 全量 E2E
    .venv\\Scripts\\python.exe scripts/e2e_ci_browser_check.py -k overview   # 透传给 pytest
退出码 0 = 在只有自带 chromium 的环境里全过，且确实用的是那一级。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

PLUGIN = '''# -*- coding: utf-8 -*-
"""临时插件：把 E2E 的浏览器链强制成"只有自带 chromium 那级"，并记录实际 channel。"""
import pathlib, sys

MARK = pathlib.Path(__file__).with_name("forced_channel.txt")


def _find_e2e_module():
    # ⚠️ 不能用 `from tests import test_frontend_e2e`：tests/ 没有 __init__.py，
    # 那条 import 走命名空间包会造出**第二个**模块对象，patch 它等于 patch 一个没人读的副本
    # （实测症状：78 条全绿但标记文件写着 channel='msedge'）。
    for mod in list(sys.modules.values()):
        f = (getattr(mod, "__file__", "") or "").replace("\\\\", "/")
        if f.endswith("tests/test_frontend_e2e.py"):
            return mod
    return None


def pytest_collection_finish(session):
    from playwright.sync_api import BrowserType
    m = _find_e2e_module()
    assert m is not None, "没找到 test_frontend_e2e —— 插件没接上，别拿假绿当结论"
    orig = BrowserType.launch

    def launch(self, **kw):
        try:
            MARK.write_text(f"channel={kw.get('channel')!r} module={m.__name__}\\n", encoding="utf-8")
        except Exception:
            pass
        return orig(self, **kw)

    BrowserType.launch = launch
    m.CHANNELS = ["__not_a_browser__", None]
    print(f"\\n[插件] {m.__name__} 的 CHANNELS 已强制为 {m.CHANNELS}（模拟 CI：无系统浏览器）", flush=True)
'''


def main() -> int:
    extra = sys.argv[1:]
    with tempfile.TemporaryDirectory(prefix="kanban-e2e-ci-") as tmp:
        plug = Path(tmp) / "e2e_force_ci_browser.py"
        plug.write_text(PLUGIN, encoding="utf-8")
        marker = Path(tmp) / "forced_channel.txt"

        env = dict(os.environ)
        env["PYTHONPATH"] = tmp + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"

        cmd = [sys.executable, "-m", "pytest", "tests/test_frontend_e2e.py",
               "-q", "--no-cov", "-p", "e2e_force_ci_browser", *extra]
        print("跑：只在自带 chromium 的环境下" + ("（透传：" + " ".join(extra) + "）" if extra else "") + "\n")
        r = subprocess.run(cmd, cwd=str(ROOT), env=env)

        used = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
        if "channel=None" not in used:
            print(f"\n[FAIL] 没能证明用的是自带 chromium（标记文件：{used or '不存在'}）"
                  "—— 可能浏览器链没被强制成功，结论作废")
            return 1
        print(f"\n[OK] 实际使用的浏览器：{used}")
        if r.returncode != 0:
            print("[FAIL] 自带 chromium 这一级有失败用例（CI 上同样会红）")
            return 1
        print("[OK] 在只有自带 chromium 的环境里全过 —— CI 那一级可用")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())