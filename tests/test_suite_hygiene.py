# -*- coding: utf-8 -*-
"""套件自身的卫生检查：同名函数在同一个文件里被定义两次，Python 是静默覆盖的。

为什么需要它：本项目多次出现"新增用例被整块粘了两遍"（2026-09-18 一次清出 12 组），
表现是套件全绿、通过数看着也对，但实际少跑了 N 条用例 —— 只看结果发现不了。
"""
from __future__ import annotations

import ast
import collections
import importlib.util
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


def test_release_gate_preflights_probes_and_sweeps_before_packaging():
    """发版门禁的两道便宜保险必须真的接上了：探针预检 + 打包前清场。

    为什么需要它（2026-10-24 一晚实测的两类事故，都真发生在了同一次发版评估里）：
    ① 上次探针被外部信号打断留下残渣，pytest 先红 80 秒、探针再空跑 8.5 分钟，
       结论只有一行"存活/异常 1" —— 预检 2 秒就能说破"是残渣、怎么修"；
    ② 上轮 frozen_smoke 没清干净的 exe 进程握着产物文件，下一轮打包直接
       WinError 5 拒绝访问 —— 清场必须挂在打包**前**，而不是打包失败后靠人排查。
    另钉清扫的过滤口径：按**可执行路径**不按映像名 —— 用户可能正开着装在别处的副本。
    """
    gate = (ROOT / "scripts" / "ci_local.py").read_text(encoding="utf-8")
    assert '"--preflight"' in gate, (
        "门禁没有先跑探针预检 —— 残渣要烧掉 pytest+探针共 9 分钟才以一行计数现形")
    assert gate.index('"--preflight"') < gate.index("pytest 全量"), "预检应排在 pytest 之前"
    assert '"--sweep"' in gate, "打包前不清场：残留实例握着 exe 文件，PyInstaller 直接 WinError 5"
    assert '_sweep_dist("AI交接看板.exe")' in gate and '_sweep_dist("AI交接看板-MCP.exe")' in gate, (
        "两个 PyInstaller 步骤（GUI / MCP）前都要清场，只清一个等于没清")
    fs = (ROOT / "scripts" / "frozen_smoke.py").read_text(encoding="utf-8")
    assert "-ieq" in fs, (
        "残留清扫必须按可执行路径过滤：按映像名杀会误杀用户装在别处的副本")


def test_stale_pid_parse_only_trusts_integer_lines():
    """PID 解析只信纯数字行：PowerShell 的空行/告警/本地化文案混进来不能变成 PID 被杀。"""
    spec = importlib.util.spec_from_file_location(
        "frozen_smoke_under_test", ROOT / "scripts" / "frozen_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._parse_pid_lines("123\nabc\n 456 \n警告：xxx\n") == [123, 456]
    assert mod._parse_pid_lines("") == []


def test_agents_md_stays_inside_the_loading_budget():
    """AGENTS.md 必须装进它自己声明的加载预算（文件头写着实测 65536 字节）。

    为什么需要它（2026-10-24 实测）：文件头承诺「常驻部分能整体装进预算」，而
    v1.9.3/v1.9.4 两轮各自加几百字、没有任何门禁看着，累积到 66485 字节 —— 超出
    949 字节，尾部（状态/验证基线/下一步）恰好是接手方最需要的部分被静默截断。
    「给自己写的加载预算」和「给别人写的 >300 行要裁剪」是同一条纪律，对自己没有
    门禁就是装饰品。超了就压缩：历史细节的归宿是 docs/进度与版本档案.md，不是删信息。
    """
    size = (ROOT / "AGENTS.md").stat().st_size
    assert size <= 65536, (
        f"AGENTS.md {size} 字节，超出加载预算 65536 —— 接手方只能读到前一半。"
        f"把历史细节压进 docs/进度与版本档案.md（内容搬走不是删掉），别砍规则与命令")


def test_e2e_keeps_the_bundled_chromium_fallback():
    """E2E 的浏览器探测必须保留「自带 chromium」这一级（`channel=None`）。

    为什么需要它（2026-10-25 复核）：E2E 原先只有 `["msedge", "chrome"]`，而
    `.github/workflows/ci.yml` 自己写着「Windows runner 上没有系统 Edge/Chrome」——
    于是**这 75 条真浏览器用例在唯一自动化环境里整份静默跳过**（退出码照样 0），
    而隔壁 `live_ui_check.py` 早就有第三级、CI 也真装了 chromium。
    「同一个环境里两套探测不均等」正是这个项目最忌的形状：最强的一层覆盖从没在 CI 跑过。
    判定它是"静默"而不是"没验"：conftest 只会在收尾印一句「跳过 N 条」，
    而文档里的基线和人眼看到的都是"全绿"。

    判据两条：① 最后一级必须是 `None`（自带 chromium，唯一与系统无关的那级）；
    ② 系统浏览器排在前面（本地优先用用户真在用的那个，自带 chromium 殿后）。
    只查形状，真启动由 E2E 自己验（本机已用 `playwright install chromium` 验过三级都能起）。
    """
    src = (TESTS / "test_frontend_e2e.py").read_text(encoding="utf-8")
    m = re.search(r"^CHANNELS\s*=\s*(\[[^\]]*\])", src, re.M)
    assert m, "test_frontend_e2e.py 里找不到 CHANNELS —— 改名请连这条门禁一起改"
    channels = ast.literal_eval(m.group(1))
    assert channels[-1] is None, (
        f"CHANNELS 最后一级必须是 None（Playwright 自带 chromium），实测 {channels!r} —— "
        "少了它，CI runner（无系统 Edge/Chrome）上这批 E2E 会整份静默跳过")
    assert channels[:2] == ["msedge", "chrome"], (
        f"系统浏览器应排在前面（本地优先用用户真在用的那个），实测 {channels!r}")


def _ci_local_module():
    """把 scripts/ci_local.py 当模块加载（它模块级只有常量与 argv 解析，无副作用）。"""
    spec = importlib.util.spec_from_file_location(
        "ci_local_under_test", ROOT / "scripts" / "ci_local.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_release_gate_audits_the_skip_count():
    """发版门禁必须**读** conftest 那份"跳过点名"，而且允许表要与事实一致。

    为什么需要它（2026-10-25）：跳过在报告里与全绿同形 —— E2E 那 75+ 条因缺第三级浏览器
    在 CI 上整份跳过，而门禁只看 pytest 的退出码，于是"最强的一层从没在 CI 跑过"这件事
    既没人看见也没人拦。conftest 早就把跳过的**名字**印在结尾了，缺的是**读者**。
    现在门禁按文件对账：只有 `test_ai.py` 允许跳过（需 `KANBAN_LIVE=1`）。

    四件事都钉：
    ① 门禁真的调了审计，且**排在 pytest 那一步的执行里**（不是写着好看的辅助函数）；
    ② 允许表里只有 `test_ai.py`，条数等于该文件的用例数 —— 数字只有一处（从文件数出来）；
    ③ `KANBAN_LIVE` 必须在干净 env 里被显式摘掉：否则"跳过 5 条"这个前提本身是环境的函数，
       开着它的人会得到 0 条 → 审计误红（伪造的红比没门禁更坏，下场是有人删门禁）；
    ④ 解析与判定真的会红：多一个文件、期望的却没出现，两个方向都拦。
    """
    gate = (ROOT / "scripts" / "ci_local.py").read_text(encoding="utf-8")
    assert "_skip_audit(_parse_skips(out), ALLOWED_SKIPS)" in gate, (
        "发版门禁没有对账跳过数 —— 整份文件被静默跳过时它仍然是绿的")
    assert gate.index("_skip_audit(_parse_skips(out), ALLOWED_SKIPS)") < gate.index("if MODE != \"quick\""), \
        "跳过审计必须在 pytest 那一步里（在 --quick 分支之前），别挪到后面去"
    assert 'env.pop("KANBAN_LIVE", None)' in gate, (
        "干净 env 里没摘掉 KANBAN_LIVE —— 跳过数会随调用者环境漂，审计的前提就不成立了")

    mod = _ci_local_module()
    allowed = mod.ALLOWED_SKIPS
    assert set(allowed) == {"test_ai.py"}, f"允许跳过只该有 test_ai.py，实测 {allowed}"
    live = len(re.findall(r"^def test_", (TESTS / "test_ai.py").read_text(encoding="utf-8"), re.M))
    assert allowed["test_ai.py"] == live, (
        f"允许表写 {allowed['test_ai.py']} 条，而 test_ai.py 有 {live} 条 —— 数字只有一处：数文件")

    good = ("============ 跳过 5 条（没验，不是通过） ============\n"
            "  test_ai.py: 5 条\n"
            "  整份跳过的文件意味着这条能力在本机从未被验证过；跑 CI 或补齐环境后再看这个数。\n")
    assert mod._parse_skips(good) == {"test_ai.py": 5}, "conftest 的点名块读不出来"
    assert mod._parse_skips("全绿，没有跳过\n") == {}, "没跳过 = 空字典，不是解析失败"
    ok, note = mod._skip_audit(mod._parse_skips(good), allowed)
    assert ok, f"允许表内的跳过被判红了（假红）：{note}"

    # 多出一整个文件（E2E 缺浏览器时的真实形状）必须红，并点名是哪个文件
    bad = good + "  test_frontend_e2e.py: 78 条\n"
    ok2, note2 = mod._skip_audit(mod._parse_skips(bad), allowed)
    assert not ok2 and "test_frontend_e2e.py" in note2 and "78" in note2, \
        f"多出来的跳过必须红且点名：{note2}"

    # 反向：期望的 5 条一条都没跳过（开着 KANBAN_LIVE 或那几条被删了）也要红
    ok3, note3 = mod._skip_audit({}, allowed)
    assert not ok3 and "test_ai.py" in note3, f"期望的跳过没出现也必须红：{note3}"


def _script_module(name: str):
    """按路径加载 scripts/ 下的脚本（它们模块级只有常量定义，import 无副作用）。"""
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_regression_instance_verifies_it_started_its_own_server():
    """起实例的脚本必须**自证**「这个端口上是我的服务」—— 端口通了不等于服务是我们的。

    为什么需要它（2026-10-25 实测，一次假失败的真根因）：`release_regression` 的 `PORT`
    写死 18399，而 readiness 只等 `port_listening()`。端口上**已经有别人**（上次跑挂留下的
    残留 uvicorn，"残留进程握着东西"在本项目是实测过的坑）时它会立刻"以为起来了"，
    随后所有 HTTP 都打到那个别人的实例上（实测它回的 `data_file` 指向上一轮的临时目录），
    而 MCP 子进程拿的是**本轮的空目录** → 没有 config.json → `mcp_readonly` 默认 False →
    写没被拒 → 报出来的是「只读模式下写操作被拒」。**真原因（端口被占）与报出来的那一条
    隔了十万八千里**，还会让人去查编码、查只读开关 —— 门禁自伤里最难查的一类。

    纪律本身早写在 `docs/测试约定.md` 第 15 条（"起实例后必须问它自己读的是哪份数据"），
    `delivery_drill` 与 `ci_local` 都照做了，**只有这个脚本没照做**。这条把它钉上：
    ① 本轮的 data_file → 放行；② 别人的 → 拦住并点名是别人的、给出怎么清；
    ③ 连 data_file 都不报 → 也算认不出，不放行；④ 自证必须排在 `return proc` **之前**。
    """
    mod = _script_module("release_regression")
    own = {"data_file": str(Path(mod.DATA_DIR) / "projects.json")}
    assert mod._own_instance_guard(own) == "", "本轮的实例被误拦（假红）"

    foreign = {"data_file": r"C:\Temp\kanban_regression_OLD\projects.json"}
    why = mod._own_instance_guard(foreign)
    assert why, "别人的实例没被拦住 —— 那就会拿它的回包当本轮结论"
    assert "别人的" in why and "data_file=" in why, f"没点名是别人的、也没给现场：{why}"
    assert "taskkill" in why or "清掉" in why, f"没说怎么处置：{why}"
    assert mod._own_instance_guard({}) != "", "连 data_file 都不报也算认不出，不能放行"

    src = (ROOT / "scripts" / "release_regression.py").read_text(encoding="utf-8")
    assert "_own_instance_guard(health" in src, "自证没接在 start_server 里（只定义不接线等于没有）"
    assert src.index("_own_instance_guard(health") < src.index("return proc"), \
        "自证必须排在 return proc 之前 —— 排在后面等于没验"


def test_free_port_has_one_implementation_and_every_gate_uses_it():
    """「挑空闲端口」只许有一份实现，且自起实例的门禁都要用它 —— **别写死端口**。

    为什么需要它（2026-10-25 实测）：这段选端口此前在 `ci_local` / `delivery_drill` /
    `scenario_test` 里**各写了一遍**（三份近似副本），而 `release_regression` 干脆写死
    `18399` —— 于是它成了唯一会被"残留实例"咬的那一个：端口上站着别人时，readiness
    只等 `port_listening()` 就"以为起来了"，之后**所有请求都打到那个别人身上**
    （实测它报的 `data_file` 指向上一轮的临时目录），而报出来的失败
    （「只读模式下写操作被拒」）与真原因（端口被占）隔了十万八千里。

    标准配方是两条一起用：**挑空闲端口**（让残留无处可撞）+ **起完自证**
    （`/api/health` 的 `data_file` 指向本轮数据目录，挡 `SO_REUSEADDR` 那个窗口）。
    钉三件事：① 全仓只有一处 `def free_port`；② 四个自起实例的门禁都从它取、且不再写死端口；
    ③ `frozen_smoke` 例外（它测的是**打包产物**，固定端口有它的理由）—— 但它必须自证
    那个端口上是本轮实例，否则同样会拿别人的回包当结论。
    """
    for name in ("ci_local.py", "delivery_drill.py", "scenario_test.py", "release_regression.py"):
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "from app.service import free_port" in src, f"{name} 没从唯一实现取端口"
        assert re.search(r"(?:PORT|port) = free_port\(\)", src), f"{name} 没用 free_port() 取端口"
        assert not re.search(r"^\s*PORT\s*=\s*\d{4,5}\s*$", src, re.M), \
            f"{name} 又把实例端口写死了 —— 残留实例会占着它，门禁就会打到别人身上"

    hits = [p.name for p in list((ROOT / "app").glob("*.py")) + list((ROOT / "scripts").glob("*.py"))
            if re.search(r"^def _?free_port", p.read_text(encoding="utf-8"), re.M)]
    assert hits == ["service.py"], f"free_port 的实现应当只有一处（app/service.py），实测 {hits}"

    fs = (ROOT / "scripts" / "frozen_smoke.py").read_text(encoding="utf-8")
    assert "是本轮实例" in fs and "data_file" in fs, \
        "frozen_smoke 的就绪判定只问了「答没答」—— 端口上是别人时会拿它的回包当本轮结论"
