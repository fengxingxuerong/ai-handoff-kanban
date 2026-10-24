# -*- coding: utf-8 -*-
"""套件自身的卫生检查：同名函数在同一个文件里被定义两次，Python 是静默覆盖的。

为什么需要它：本项目多次出现"新增用例被整块粘了两遍"（2026-09-18 一次清出 12 组），
表现是套件全绿、通过数看着也对，但实际少跑了 N 条用例 —— 只看结果发现不了。
"""
from __future__ import annotations

import ast
import collections
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent


def _dup_funcs(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    counts = collections.Counter(
        n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return sorted(name for name, c in counts.items() if c > 1)


def test_no_duplicate_module_level_functions_in_tests():
    offenders = {p.name: _dup_funcs(p) for p in sorted(TESTS.glob("*.py"))}
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, (
        f"以下文件里有同名函数被定义了多次，后一份会静默覆盖前一份、用例根本不会跑：{offenders}")


def _conftest_fixtures() -> list[str]:
    tree = ast.parse((TESTS / "conftest.py").read_text(encoding="utf-8"))
    out = []
    for n in tree.body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in n.decorator_list:
            if "fixture" in ast.unparse(dec):
                out.append(n.name)
                break
    return out


def test_no_dead_fixture_in_conftest():
    """conftest 里定义的夹具必须被至少一个用例用到。

    为什么需要它：`token_server`（带 Token 的子进程实例）在 2026-09 的一次重构里
    失去了唯一的使用方 —— `test_security.py` 把 `server` 换成了进程内客户端。
    结果：**套件全绿、覆盖率也不掉，但"子进程那条路上的 Token 鉴权"从此没人验**
    （覆盖率量不到"没被用到的夹具"，它只会让那条路径安静地消失）。
    死夹具比没夹具更危险：它在那儿，让人以为验过了。
    """
    body = "\n".join(p.read_text(encoding="utf-8") for p in sorted(TESTS.glob("*.py")))
    dead = []
    for name in _conftest_fixtures():
        # 定义处算 1 次；装饰器和文档字符串里提到不算引用
        if len(re.findall(r"\b" + re.escape(name) + r"\b", body)) <= 1:
            dead.append(name)
    assert not dead, (
        f"conftest 里这些夹具没有任何用例在用：{dead}。"
        f"要么给它们补一条用例（钉住那条路径），要么删掉 —— 别留着假装验证过")


def test_every_test_file_actually_collects_tests():
    """防"文件名以 _ 开头但不是用例文件"或整份被 skip 掉导致的静默空档"""
    empty = []
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        has_case = any(isinstance(n, ast.FunctionDef) and n.name.startswith("test")
                       for n in tree.body)
        if not has_case:
            empty.append(path.name)
    assert not empty, f"这些 test_*.py 里一条用例都没有：{empty}"


def test_default_port_is_written_in_exactly_one_place():
    """端口兜底值只许有一处 —— 「同一条清单不许两份」的具体化。

    为什么需要它（2026-10-24 实测）：这个数曾被**写死三份** —— `app/main.py` 的
    `SERVICE_PORT` 兜底、`scripts/desktop.py` 的 `PORT` 兜底、`app/handoff_page.py`
    的 `port` 默认参数。改一处漏一处的后果不会报错，只会让桌面壳起在 18321、
    而交接包正文里的直链写 18320 —— 接手方去点一个没人听的端口。
    现在它们全部取自 `app/service.DEFAULT_PORT`，这条把"一处"钉住。

    口径（**别把它放宽成"全仓库"**）：只看产品代码 `app/*.py` 与 `scripts/desktop.py`。
    `scripts/live_check.py` 这类联调工具自带的 `http://127.0.0.1:18320` 是另一回事 ——
    它们每次都由门禁显式传 base_url，把它们算进来只会造出假红，而假红的下场是
    "有人把这条门禁删掉"。
    """
    targets = sorted((ROOT / "app").glob("*.py")) + [ROOT / "scripts" / "desktop.py"]
    hits = []
    for p in targets:
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = ln.split("#")[0]                       # 注释里讲历史是允许的
            if re.search(r"""["']18320["']""", code):      # 只认"真的当字面量用了"
                hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert len(hits) == 1, (
        f"端口兜底值应当只有一处（app/service.py 的 DEFAULT_PORT），实测写了 {len(hits)} 处：{hits}")
    assert "service.py" in hits[0], (
        f"那唯一一处应当在 app/service.py，实测在 {hits[0]}")


def test_coverage_floor_is_enforced_by_the_release_gate():
    """覆盖率门槛必须真的拦人，而且只能有一份数字。

    为什么需要它（2026-10-23 实测）：`pytest.ini` 的 `[coverage:report] fail_under = 90`
    **对 pytest 的退出码无效** —— 只跑 `tests/test_version.py`、TOTAL 16%，仍然 exit 0、
    连一句 fail 都不说；加 `--cov-fail-under=90` 才 exit 1。也就是说这行此前是**装饰品**：
    删掉几百条用例没人会红，而 `ci_local.py` 那一步的标签上写着"含覆盖率门槛"。

    这一条钉两件事，缺一即红：
    ① 发版门禁（`scripts/ci_local.py`）真的把 `--cov-fail-under` 传给了 pytest-cov；
    ② 数字来自 `pytest.ini` 的 `fail_under`（`coverage_floor()` 读它），
       门禁里不许再写死一个 —— 两处各写一份必然漂，而漂的那一刻门禁就开始撒谎。
    """
    ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    floors = re.findall(r"^fail_under\s*=\s*(\d+)", ini, re.M)
    assert len(floors) == 1, f"pytest.ini 里的 fail_under 应当有且只有一处，实测：{floors}"
    assert int(floors[0]) > 0, "门槛得是个正数"

    # 门槛属于"发版判据"，不属于每次跑 pytest 的默认行为：写进 addopts 会让文档里
    # 那些有意只跑一部分的命令（`pytest tests\test_frontend_e2e.py -v` 等）全部假红。
    addopts = re.search(r"^addopts\s*=\s*(.+)$", ini, re.M)
    assert addopts, "pytest.ini 里没有 addopts"
    assert "--cov-fail-under" not in addopts.group(1), (
        "覆盖率门槛不要塞进 addopts：单跑某个文件的开发命令会集体假红。"
        "它应当在 scripts/ci_local.py 里显式传（那里才是发版判据）")

    gate = (ROOT / "scripts" / "ci_local.py").read_text(encoding="utf-8")
    assert re.search(r'"--cov-fail-under"\s*,\s*coverage_floor\(\)', gate), (
        "发版门禁没有把 --cov-fail-under 传给 pytest —— pytest.ini 里的 fail_under 拦不住退出码，"
        "这一行就成了装饰品")
    assert not re.search(r'"--cov-fail-under=\d+"', gate), (
        "门槛数字不许在门禁里再写死一份：从 pytest.ini 的 fail_under 读（coverage_floor）")
