# -*- coding: utf-8 -*-
"""套件自身的卫生检查：同名函数在同一个文件里被定义两次，Python 是静默覆盖的。

为什么需要它：本项目多次出现"新增用例被整块粘了两遍"（2026-09-18 一次清出 12 组），
表现是套件全绿、通过数看着也对，但实际少跑了 N 条用例 —— 只看结果发现不了。
"""
from __future__ import annotations

import ast
import collections
from pathlib import Path

TESTS = Path(__file__).resolve().parent


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
