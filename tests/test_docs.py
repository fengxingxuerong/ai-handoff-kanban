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

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
# 2026-10-24：AGENTS.md 按「是否每次都要读」切分了（154KB → 63KB，见它开头的「去哪看」表），
# 参考资料搬进了 docs/。**数字跟着小节搬走了，门禁指针就得跟着搬** —— 这一条在探针上已经吃过
# 一次教训（探针盯的 `old` 串随代码搬走就哑火）。所以判据分两类：
#   · 必须常驻 AGENTS.md 的（模块表/探针数/状态行/测试文件数）：照旧只查 AGENTS；
#   · 讲某件事本身的（工具数、版本叙事）：查**文档集合**，搬去哪都算数。
DOCS = AGENTS + "\n".join(
    p.read_text(encoding="utf-8") for p in sorted((ROOT / "docs").glob("*.md")))
README = (ROOT / "README.md").read_text(encoding="utf-8")
# 读**实现**（app/mcp_server.py）；scripts/mcp_server.py 只是兼容壳（2026-10-25 搬的）
MCP_SRC = (ROOT / "app" / "mcp_server.py").read_text(encoding="utf-8")
PROBE_SRC = (ROOT / "scripts" / "mutation_probe.py").read_text(encoding="utf-8")
VERSION_SRC = (ROOT / "app" / "version.py").read_text(encoding="utf-8")

# 打包时生成的构建戳与纯声明模块不算"拆出来的模块"
_NOT_A_MODULE = {"__init__.py", "main.py", "version.py", "_build_stamp.py"}


def _app_modules() -> set[str]:
    return {p.name for p in (ROOT / "app").glob("*.py")} - _NOT_A_MODULE


def _mcp_tools() -> set[str]:
    return set(re.findall(r'^\s+"(kanban_[a-z_]+)":\s*\(', MCP_SRC, re.M))


def _probe_ids() -> set[str]:
    """探针表里的 id 集合。

    字符类**必须含大写**（2026-10-25 实测）：原先是 `[a-z0-9_]`，于是带驼峰的那条
    （`js_esc_close_bypasses_closeOneModal`）**静默数不进来** —— 而本用例是拿这个数
    去对账文档的，结果是"表里 102、文档被要求写 101"：一个命名违规把**别处**的数字带错，
    而红的是文档。命名本身另有钉子（`test_mutation_probe.py` 里那条全小写断言）。
    """
    return set(re.findall(r'"id":\s*"([A-Za-z0-9_]+)"', PROBE_SRC))


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
    assert f"MCP {len(actual)} 个工具" in DOCS, f"AGENTS.md/ 文档集 的工具数对不上（实测 {len(actual)}）"


def test_every_relative_doc_link_points_at_a_real_file():
    """文档里的相对链接必须指向真实存在的文件。

    为什么需要它：这类失效引用**没人会红**，只会让接手方点空 ——
    2026-10-23 就抓到过一处：AGENTS.md 写着「见 README「和同类工具比」」，
    而 README 里**根本没有这一节**，它静默挂了好几个版本（`test_docs` 对账的是数字，
    对账不了引用）。同一天文档被切成 AGENTS.md + docs/ 五份，一次多出十几条链接 ——
    链接存在性从此必须是机械可查的。

    只查**相对路径**：http(s)/mailto/纯锚点跳过（那些是外部世界的事）。
    """
    missing = []
    files = [ROOT / "AGENTS.md", ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md"))
    for f in files:
        for target in re.findall(r"\]\(([^)\s]+)\)", f.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            rel = target.split("#", 1)[0]
            if not rel:
                continue
            if not (f.parent / rel).exists():
                missing.append(f"{f.relative_to(ROOT)} → {target}")
    assert not missing, "这些文档链接指向不存在的文件（点空 = 接手方以为没写）：\n  " + "\n  ".join(missing)


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


def test_server_json_registry_entry_matches_the_repo():
    """server.json（官方 MCP Registry 的发布元数据）必须与仓库事实同源。

    为什么需要它：server.json 的 version 与 packages[].version 是**手写的第二份版本号**，
    不钉住就会在升版时静默漂掉（本项目为"散文里的数字"付过 N 次学费）；OCI 标识符
    （ghcr.io 路径）在 server.json 与 ci.yml 里各出现一处，仓库更名/改名镜像时两处必须
    一起动 —— 2026-10-24 就经历过一次仓库改名，钉住它。
    """
    sj = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    v = _current_version()
    assert sj["version"] == v, f"server.json 版本 {sj['version']} ≠ version.py 的 {v}，升版时两处一起动"
    assert sj["name"].startswith("io.github.fengxingxuerong/"), (
        "Registry 命名空间 io.github.<owner> 必须与 GitHub 账号一致，否则所有权验证过不了")
    assert sj["repository"]["url"] == "https://github.com/fengxingxuerong/ai-handoff-kanban"
    pkg = sj["packages"][0]
    assert pkg["registryType"] == "oci" and pkg["transport"]["type"] == "stdio"
    assert pkg["identifier"] == "ghcr.io/fengxingxuerong/ai-handoff-kanban-mcp", (
        "OCI 标识符与 CI 推送目标漂了 —— 客户端会拉到不存在的镜像")
    assert pkg["version"] == v, "包版本与 server.json 顶层版本不一致"
    wf = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert pkg["identifier"] in wf, (
        "ci.yml 的镜像构建/推送没有用 server.json 里那份 OCI 标识符 —— 两处各写一份必然漂")


def test_version_in_docs_matches_version_py():
    """两份文档里写的版本号必须与 `app/version.py` 同数。

    为什么补这条：1.5.0 落地之后，AGENTS.md 与 README 里 `1.5.0` 各出现 **0 次** ——
    AGENTS.md 的「当前进度」还写着「状态：v1.4.0」，README 干脆通篇没有版本号。
    版本号是"单一事实来源"这句话在本项目已经落地（界面版本条 / `/api/health` /
    MCP `serverInfo` / exe 的 PE 版本资源都读它），唯独**文档这一侧没人看着** ——
    而这类"只出现在散文里的事实"正是本文件存在的理由（探针 45→54、MCP 工具 17→23、
    模块 21→22 都漂过）。判据取「状态行 + 版本叙事」两处，避免只改一处也算过。
    **2026-10-24 起**：状态行仍必须常驻 AGENTS.md（接手方第一眼要看的就是它），
    但版本叙事跟着「进度档案」搬进了 `docs/` —— 所以后者查**文档集合**。
    """
    v = _current_version()
    assert f"状态：v{v}" in AGENTS, (
        f"AGENTS.md「当前进度」的状态行必须写当前版本 v{v}（现读自 app/version.py）")
    assert f"v{v}" in README, f"README 里应写明当前版本 v{v}"
    assert DOCS.count(v) >= 2, (
        f"文档集合里 {v} 只出现一次：状态行之外还应有一轮版本叙事（改了什么、为什么），"
        f"现在它在 docs/进度与版本档案.md 里")


def test_agents_directory_table_rows_are_single_lines():
    """AGENTS.md 的表格行必须是**一整行** —— 中途换行会把表格拆坏（后几行渲染成普通段落）。

    为什么需要它（2026-10-25 实测，我自己犯的）：往 `scripts/mutation_probe.py` 那一行里
    补一段文字时手滑带了换行，那一行被拆成 **6 行**，表格从那里起散架 ——
    而**所有文档门禁都还是绿的**：`test_module_counts…` 只数 `app/*.py` 的行数、
    版本/条数/链接那几条只查关键词在不在。markdown 的规则是「一行就是一行」，
    这种坏法要翻到那一段才看得出，能机械查就别靠人翻。

    判据：从目录表表头起、到第一个空行为止，每一行都必须以 `|` 开头、以 `|` 结尾。
    """
    lines = AGENTS.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("| 路径 | 职责 |"))
    rows = []
    for l in lines[start:]:
        if not l.strip():
            break
        rows.append(l)
    assert len(rows) > 10, f"目录表只有 {len(rows)} 行 —— 表头锚点找错了？"
    bad = [l[:80] for l in rows if not (l.startswith("|") and l.rstrip().endswith("|"))]
    assert not bad, f"目录表里有被换行拆坏的行（表格要求一行就是一行）：{bad}"
