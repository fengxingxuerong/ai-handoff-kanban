# -*- coding: utf-8 -*-
"""文档对账：README / AGENTS.md 里写死的数字必须与代码事实一致。

为什么单独一个文件：这个项目的文档里散着一批"手写的事实"（工具数、模块数、探针数、
跳过项数），历史上漂过好几次 —— 探针从 45 涨到 54 而两处文案还写着 45、MCP 工具涨到
23 个而 README 的工具表停在 17 个、模块数从 21 涨到 22。**这些数字没有任何 IDE 或类型
系统看着**，只有一条把它们与"代码事实"钉在一起的用例才拦得住。

纪律（沿用硬性约定）：凡是"两边各写一份"的东西都必须有一张测试钉住。这里钉的是
"文案 ↔ 代码事实"，对账对象都从源文件现算，不写死第二份。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
MCP_SRC = (ROOT / "scripts" / "mcp_server.py").read_text(encoding="utf-8")
PROBE_SRC = (ROOT / "scripts" / "mutation_probe.py").read_text(encoding="utf-8")
VERSION_SRC = (ROOT / "app" / "version.py").read_text(encoding="utf-8")

# 打包时生成的构建戳与纯声明模块不算"拆出来的模块"
_NOT_A_MODULE = {"__init__.py", "main.py", "version.py", "_build_stamp.py"}


def _app_modules() -> set[str]:
    return {p.name for p in (ROOT / "app").glob("*.py")} - _NOT_A_MODULE


def _mcp_tools() -> set[str]:
    return set(re.findall(r'^\s+"(kanban_[a-z_]+)":\s*\(', MCP_SRC, re.M))


def _probe_ids() -> set[str]:
    return set(re.findall(r'"id":\s*"([a-z0-9_]+)"', PROBE_SRC))


def test_readme_mcp_tool_table_lists_every_tool():
    """README 的工具表必须把 TOOL_KIND 里的工具一个不漏地列出来（数量也要对得上）。

    表头写着 23 个而正文只有 17 行，是这类文档最典型的坏法：读者以为"就这么点能力"，
    而智能体实际能调的比写出来的多 6 个（含 delete_project 这种要确认的危险工具）。
    """
    listed = set(re.findall(r'`kanban_([a-z_]+)`', README))
    actual = {t[len("kanban_"):] for t in _mcp_tools()}
    missing = sorted(actual - listed)
    assert not missing, f"README 工具表漏列：{missing}"
    assert f"提供的工具（{len(actual)} 个）" in README, \
        f"README 写的工具数不是实测的 {len(actual)} 个"
    assert f"MCP {len(actual)} 个工具" in AGENTS, f"AGENTS.md 的工具数对不上（实测 {len(actual)}）"


def test_module_counts_in_both_docs_match_the_app_package():
    """两份文档声称的模块数必须等于 app/ 下实际的模块数（口径：去掉 main 与生成物）。"""
    n = len(_app_modules())
    assert f"拆在 app/ 下 {n} 个模块" in README, f"README 模块数应写 {n}"
    assert f"下表 {n} 个模块" in AGENTS, f"AGENTS.md 模块数应写 {n}"
    # 目录表里 app/ 的行数 = 模块数 + 1（main 自己那一行）
    table_rows = len(re.findall(r"^\| `app/[a-z_]+\.py` \|", AGENTS, re.M))
    assert table_rows == n + 1, f"目录表 app/ 行数 {table_rows} ≠ 模块数 {n} + 1"


def test_mutation_probe_count_matches_both_docs():
    """探针条数写在三处（README 注释、AGENTS.md 模块表、AGENTS.md 记录段），必须同数。

    这个数曾经从 45 涨到 54 而文案还停在 45 —— 数字漂了没人发现，因为它只出现在散文里。
    """
    n = len(_probe_ids())
    assert f"注入 {n} 处" in README, f"README 探针数应写 {n}"
    assert f"注入 {n} 处语义改动" in AGENTS, f"AGENTS.md 模块表探针数应写 {n}"


def test_documented_test_file_count_is_real():
    """AGENTS.md 写死的"测试文件数"必须等于 tests/ 下真实存在的 test_*.py 个数。

    加了测试文件却忘了改这一行，读者就会以为"还有哪些面没测" —— 这条顺手把
    「新增测试文件要同步改文档」变成机械可查的事。
    """
    actual = len(list((ROOT / "tests").glob("test_*.py")))
    m = re.search(r"(\d+) 个测试文件", AGENTS)
    assert m, "验证基线里应写着测试文件数"
    assert int(m.group(1)) == actual, f"文档写 {m.group(1)} 个测试文件，实际 {actual} 个"


def _current_version() -> str:
    """当前版本号从 `app/version.py` 现读（不写第二份）。"""
    m = re.search(r'^__version__ = "([^"]+)"', VERSION_SRC, re.M)
    assert m, "app/version.py 里应有 __version__ 定义"
    return m.group(1)


def test_version_in_docs_matches_version_py():
    """两份文档里写的版本号必须与 `app/version.py` 同数。

    为什么补这条：1.5.0 落地之后，AGENTS.md 与 README 里 `1.5.0` 各出现 **0 次** ——
    AGENTS.md 的「当前进度」还写着「状态：v1.4.0」，README 干脆通篇没有版本号。
    版本号是"单一事实来源"这句话在本项目已经落地（界面版本条 / `/api/health` /
    MCP `serverInfo` / exe 的 PE 版本资源都读它），唯独**文档这一侧没人看着** ——
    而这类"只出现在散文里的事实"正是本文件存在的理由（探针 45→54、MCP 工具 17→23、
    模块 21→22 都漂过）。判据取「状态行 + 版本叙事」两处，避免只改一处也算过。
    """
    v = _current_version()
    assert f"状态：v{v}" in AGENTS, (
        f"AGENTS.md「当前进度」的状态行必须写当前版本 v{v}（现读自 app/version.py）")
    assert f"v{v}" in README, f"README 里应写明当前版本 v{v}"
    assert AGENTS.count(v) >= 2, (
        f"AGENTS.md 里 {v} 只出现一次：状态行之外还应有一轮版本叙事（改了什么、为什么）")
