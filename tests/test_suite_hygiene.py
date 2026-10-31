# -*- coding: utf-8 -*-
"""套件自身的卫生检查：同名函数在同一个文件里被定义两次，Python 是静默覆盖的。

为什么需要它：本项目多次出现"新增用例被整块粘了两遍"（2026-09-18 一次清出 12 组），
表现是套件全绿、通过数看着也对，但实际少跑了 N 条用例 —— 只看结果发现不了。
"""
from __future__ import annotations

import ast
import collections
import importlib.util
import json
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
    **2026-10-31 起**：清场与让位收进了 `scripts/exe_build.py`（产物可能正被用户的 AI 代理宿主
    当 MCP 服务跑着，杀不掉、只能改名让位），所以清场指针改为钉在那个模块 + 两次打包都走它
    （而不是钉门禁里那两行 `_sweep_dist(...)` 字面量）。
    """
    gate = (ROOT / "scripts" / "ci_local.py").read_text(encoding="utf-8")
    assert '"--preflight"' in gate, (
        "门禁没有先跑探针预检 —— 残渣要烧掉 pytest+探针共 9 分钟才以一行计数现形")
    assert gate.index('"--preflight"') < gate.index("pytest 全量"), "预检应排在 pytest 之前"
    build = (ROOT / "scripts" / "exe_build.py").read_text(encoding="utf-8")
    assert "sweep_stale_binaries" in build and "move_aside_if_locked" in build, (
        "打包前不清场/不让位：残留实例或正跑着的实例握着 exe 文件，PyInstaller 直接 WinError 5")
    assert gate.count("build_exe_step(") >= 2, (
        "两个 PyInstaller 步骤（GUI / MCP）都要走 exe_build.build_exe_step，绕过它等于没清场")
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
        f"把历史细节搬进 docs/（规则类的归宿是 docs/测试约定.md，逐轮叙事是 "
        f"docs/进度与版本档案.md）—— 内容搬走不是删掉，别砍规则与命令")


def test_gitignore_keeps_the_local_only_things_out():
    """`.gitignore` 是**纯文本约定**：删掉一行不会红、也没人会发现 —— 所以给它一条钉子。

    这个仓库是**公开的**，而它挡着的正是最不能外传的几类东西（2026-10-27 复核）：
      ① 密钥与运行期数据（`*.local.json` / `config.json` / `keys.local.json`）；
      ② 交付目录里的**二进制与日志**：`DELIVERY/` 下实测有 38MB（两枚 19MB 的 exe + 构建日志），
         一次 `git add -A` 就会进库；
      ③ 别的工具在本仓留下的目录（`.dumate/`、`.workbuddy/`）与临时目录（`.tmp/`）；
      ④ 构建产物（`.venv/`、`dist/`、`build/`、`*.egg-info/`）。
    同时钉**另一个方向**：`DELIVERY/` 本身**不许被整体忽略** —— 那里的文档是要入库的
    （历史 4 份 .md/.html 就在版本库里）。写成 `DELIVERY/` 一行会让"该入库的文档"静默消失，
    那是比漏挡更难发现的一类错。

    实测支撑（2026-10-27）：`git status -uall` 的 91 个未跟踪文件里**没有一个** .exe/.log ——
    说明这几条规则真在起作用；这条用例把它从"碰巧"变成"改坏了会红"。
    """
    ig = (ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = {ln.strip() for ln in ig.splitlines() if ln.strip() and not ln.strip().startswith("#")}
    required = {
        "*.local.json": "密钥/运行期数据",
        "config.json": "密钥配置",
        "keys.local.json": "按端点分文件存放的 Key",
        "DELIVERY/**/*.exe": "交付目录里的 exe（两枚 19MB）",
        "DELIVERY/**/*.log": "交付目录里的构建日志",
        ".venv/": "虚拟环境",
        "dist/": "打包产物",
        "*.egg-info/": "打包残渣",
        ".dumate/": "文档查看器缓存",
        ".workbuddy/": "别的 AI 工具的记忆/会话状态",
        ".tmp/": "本会话的临时脚本",
    }
    missing = [f"{pat}（{why}）" for pat, why in required.items() if pat not in lines]
    assert not missing, "`.gitignore` 少了这些规则（一次 git add -A 就可能进公开仓库）：\n  " + \
        "\n  ".join(missing)
    wholesale = [ln for ln in lines if ln.rstrip("/") == "DELIVERY"]
    assert not wholesale, (
        "`DELIVERY/` 被**整体**忽略了 —— 但那里的文档是要入库的（历史 4 份就在版本库里）；"
        "要挡具体类型就写 `DELIVERY/**/*.xxx`")


def test_agents_md_keeps_headroom_under_the_budget():
    """AGENTS.md 不只要「没超」，还得**留出余量** —— 只剩几百字节时，下一句话就会撞墙。

    为什么需要它（2026-10-25 实测）：撞墙那条门禁（≤65536）一直在，也确实没红 —— 但实测余量
    只剩 **436 字节**，而本轮每一处要补的说明都是 300~800 字节。于是"加一句话"这件事会
    突然变成"必须先搬走一整行"：**约束在最后一刻才现身**，而它本来完全可以在还剩 5KB 时就说。
    这和 v1.9.4 那条「探针残留要能预检」是同一个形状：把"迟早会撞"变成"现在就能看见"。

    余量低于阈值时的正确处置不是删信息，而是**把最占地的那一行整段搬进 docs/**（规则类的进
    docs/测试约定.md，逐轮叙事进 docs/进度与版本档案.md）—— 本用例就是那次搬迁的产物。
    """
    size = (ROOT / "AGENTS.md").stat().st_size
    headroom = 65536 - size
    assert headroom >= 1500, (
        f"AGENTS.md 只剩 {headroom} 字节余量（{size}/65536）—— 下一处补充说明就会撞上加载预算，"
        f"而超出的部分（状态/验证基线/下一步）恰好是接手方最需要的。"
        f"处置：把最占地的那一行整段搬进 docs/（规则类 → docs/测试约定.md，逐轮叙事 → "
        f"docs/进度与版本档案.md），内容搬走不是删掉")


def test_moved_history_is_still_reachable_from_agents_md():
    """搬走的历史必须**留一句指得到的指针** —— 没有门禁看着，「搬走」会退化成「删掉」。

    为什么需要它（2026-10-25）：常驻文件的正确瘦身是「内容搬走，不是删掉」。但"搬走"这件事
    如果没有门禁看着，很容易在下一轮变成"删掉"：接手方在 AGENTS.md 里看不到，就会以为
    从来没有过 —— 而逐轮史恰恰是"想查的时候查得到"才有价值的东西（哪一轮加了几条、
    为什么加、每条 killer 是谁）。

    判据两头都钉：AGENTS.md 里要有指向 docs 的指针句，docs 里要真有那一节。
    探针表本身另有两条钉子（语法闸、前端 JS 覆盖数），这里只管"搬走了还找得回来"。
    """
    a = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "变异探针：逐轮史" in a, "探针行里没有指针 —— 搬走的历史再也找不回来了"
    assert "docs/测试约定.md" in a, "指针没点名去哪看（只说「见文档」等于没说）"
    t = (ROOT / "docs" / "测试约定.md").read_text(encoding="utf-8")
    assert "## 变异探针：逐轮史" in t, "指针指向的那一节不存在 —— 搬走 ≠ 删掉"
    # 逐轮史至少要留得住"哪一轮加了几条"这种可查的事实
    assert "2026-09-29" in t and "2026-10-25" in t, "搬过去的内容不完整（首尾两轮都该在）"


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


def _gate_steps() -> dict:
    """从 `scripts/ci_local.py` 里取「步骤标签 → 它跑的那个脚本」（用 AST，不靠文本正则）。

    为什么用 AST：这一步的判据就是"清单本身"，若用正则去凑字符串，改个引号写法就会假红/假绿 ——
    而"守门的东西自己得先站得住"。
    """
    tree = ast.parse((ROOT / "scripts" / "ci_local.py").read_text(encoding="utf-8"))
    steps = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_step = isinstance(node.func, ast.Name) and node.func.id == "step"
        is_append = isinstance(node.func, ast.Attribute) and node.func.attr == "append"
        if not (is_step or is_append):
            continue
        label, args = None, []

        def _flat(nodes):
            """把参数摊平：`step(标签, [PY, "scripts/x.py", …], …)` 里脚本名在**列表**里。"""
            out = []
            for n in nodes:
                if isinstance(n, ast.Constant):
                    out.append(n.value)
                elif isinstance(n, ast.List):
                    out.extend(e.value for e in n.elts if isinstance(e, ast.Constant))
            return out

        if is_step and node.args and isinstance(node.args[0], ast.Constant):
            label = node.args[0].value
            args = _flat(node.args[1:])
        elif is_append:
            for a in node.args:
                if isinstance(a, ast.Tuple) and a.elts and isinstance(a.elts[0], ast.Constant):
                    label = a.elts[0].value
                    args = _flat(a.elts)
        if label:
            script = next((a for a in args if isinstance(a, str) and a.endswith(".py")), "")
            steps[label] = script
    return steps


def test_alias_table_is_consistent_with_the_canonical_lists():
    """英文别名表必须与规范值白名单**同进同退**，且不许有歧义。

    来由（2026-10-25）：契约以前只收中文（`enum: ["待办", …]`），英文 agent 直接撞 422 ——
    而"任何 agent 都能接手"是这个工具的卖点。加别名之后，两类隐患变成可机械检查的：
      ① **添了规范值却忘了给它英文名**：英文用户恰好在那一个状态上照样撞墙，
         而漏一种是**静默**的（别处都好，没人会注意到这一个）；
      ② **同一个别名指到两个规范值**：归一结果取决于字典遍历顺序 —— 那是本项目最忌的
         "两条路给不同答案"的雏形。
    再加两条形状判据：`with_aliases` 必须**规范值在前**（客户端与模型照抄第一项）且去重。
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from app import fields as F

    canonical = (list(F.DEFAULT_STATUS) + list(F.DEFAULT_TASK_STATUS)
                 + list(F.DEFAULT_PRIORITY) + ["决策", "坑", "约定", "事实"])

    missing = [c for c in canonical if not F.FIELD_ALIASES.get(c)]
    assert not missing, (
        f"这些规范值没有英文别名：{missing} —— 英文 agent 会恰好卡在这几个值上，"
        f"而别处都能用，属于静默的漏")

    reverse: dict[str, set[str]] = {}
    for value in canonical:
        for alias in F.FIELD_ALIASES.get(value, []):
            reverse.setdefault(alias, set()).add(value)
    ambiguous = {a: sorted(v) for a, v in reverse.items() if len(v) > 1}
    assert not ambiguous, (
        f"同一个别名指到多个规范值：{ambiguous} —— 归一会随字典顺序漂，先消歧再合并")

    for value in canonical:                     # 归一必须幂等（规范值进去还是它自己）
        assert F.canonical_status(value) == value, (
            f"{value!r} 经过归一变了样 —— 规范值必须是`不动点`")

    advertised = F.with_aliases(F.DEFAULT_TASK_STATUS)
    assert advertised[:len(F.DEFAULT_TASK_STATUS)] == F.DEFAULT_TASK_STATUS, (
        f"with_aliases 没把规范值排在最前：{advertised[:6]}")
    assert len(advertised) == len(set(advertised)), f"广告表里有重复：{advertised}"


def test_snapshot_dir_is_defined_in_one_place():
    """快照目录（`docs/kanban`）只能**定义**一处，而且**路径只能由 `SNAPSHOT_DIR` 拼**。

    来由（2026-10-25）：这个路径出现在渲染器（拼文件名）、覆盖策略、以及给用户看的说明里。
    散开写就是下一个"两处口径漂了"（本项目吃过几次：两份 `_projects_brief`、
    MCP 重算一遍 `/next`、写死三份的端口号）。

    **判据分三层，故意不把散文判成违规**（第一版一律禁字面量，于是把 MCP 工具描述里
    "默认写进 `docs/kanban/`" 这句**该有的说明**也判红了）：
      ① 定义只许一处：`SNAPSHOT_DIR = "docs/kanban"` 这种**赋值**只能出现在 repo_snapshot.py；
      ② 路径只许拼一处：字面量**不许出现在 f-string 或 `+` 拼接里** ——
         那才是"又写了一遍路径"的真形态；
      ③ 散文（文档字符串、工具描述）里提一句不算定义。
    """
    import ast

    literal = "docs/kanban"
    defines, builds = [], []
    for p in sorted((ROOT / "app").glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and any(
                    isinstance(v, ast.Constant) and isinstance(v.value, str) and literal in v.value
                    for v in node.values):
                builds.append(p.name)
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                    and any(isinstance(x, ast.Constant) and isinstance(x.value, str)
                            and literal in x.value for x in (node.left, node.right))):
                builds.append(p.name)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) and literal in node.value.value:
                defines.append(p.name)
    assert sorted(set(builds)) == [], (
        f"这些文件在 f-string / `+` 里又拼了一遍 `{literal}`：{sorted(set(builds))} —— "
        f"路径只能由 `repo_snapshot.SNAPSHOT_DIR` 拼出来")
    assert sorted(set(defines)) == ["repo_snapshot.py"], (
        f"`{literal}` 的定义出现在这些文件里：{sorted(set(defines))} —— 只能有一处")


def _coverage_omit() -> set[str]:
    """读 `.coveragerc` 的 `[run] omit`（多行值按行拆开）。

    为什么读这里而不是 `pytest.ini`（2026-10-25 实测）：pytest-cov 自己构造 source/开关，
    `pytest.ini` 的 `[coverage:run]` 段对运行时**不生效** —— omit 的四种写法（正斜杠、
    反斜杠、两种通配）全部无效，与那里记的 `branch = true` 是同一个坑。
    真正生效的是 coverage.py 自己读的配置文件（`--cov-config=.coveragerc`）。
    """
    import configparser

    cp = configparser.ConfigParser()
    cp.read_string((ROOT / ".coveragerc").read_text(encoding="utf-8"))
    raw = cp.get("run", "omit", fallback="")
    return {ln.strip().replace("\\", "/") for ln in raw.splitlines() if ln.strip()}


def test_coverage_exclusion_is_explicit_and_has_a_verifier():
    """进程内覆盖率量不到的模块只能**显式排除**，而且必须有人替它作证。

    来由（2026-10-25，ci_local 抓到的真红）：MCP 服务从 `scripts/` 搬进 `app/` 之后，
    第一次落进 `--cov=app` 的**分母**（655 条语句、进程内只覆盖 14%），总覆盖率 93.8% → 81%。
    而 `pytest.ini` 里其实**早就写着**「它是子进程跑的，行覆盖量不到它」—— 只是那时靠
    "它在 source 之外"这条**巧合**成立，搬进包里就让巧合失效了。所以现在写成显式 omit。

    三条判据（故意窄）：
      ① omit 表**只有** `app/mcp_server.py` —— 不许悄悄扩大（那才是"把红变绿"的常见手法）；
      ② 替它作证的两层必须还在：真 stdio 子进程用例（含"每个注册工具都真被调用过一次"）
         与 `mcp_frozen_smoke.py`；
      ③ 后者必须真的挂在发版门禁里（文件存在但没人跑 = 等于没有那一层）。
    """
    addopts = re.search(r"(?m)^addopts\s*=\s*(.+)$",
                        (ROOT / "pytest.ini").read_text(encoding="utf-8")).group(1)
    assert "--cov-config=.coveragerc" in addopts, (
        "addopts 没把 `.coveragerc` 指过来 —— 那么 omit 是**惰性配置**："
        "实测 pytest.ini 里写 omit 四种写法全无效，只有 coverage 自己的配置文件算数")

    omit = _coverage_omit()
    assert omit == {"app/mcp_server.py"}, (
        f"覆盖率排除表变了：{sorted(omit)} —— 排除**只能**是那个子进程跑的 MCP 服务；"
        f"若要排除别的，先补上「谁替它作证」的那一层，再改这条钉子")

    mcp_tests = (ROOT / "tests" / "test_mcp_server.py").read_text(encoding="utf-8")
    assert "test_every_registered_tool_is_advertised_and_actually_called" in mcp_tests, (
        "替覆盖率作证的那条「每个注册工具都真被调用过一次」不见了 —— "
        "MCP 服务的覆盖率就只剩一个被排除的空名字")

    smoke = ROOT / "scripts" / "mcp_frozen_smoke.py"
    assert smoke.exists(), "MCP 冻结版自查不见了"
    ci = (ROOT / "scripts" / "ci_local.py").read_text(encoding="utf-8")
    assert "mcp_frozen_smoke" in ci, (
        "mcp_frozen_smoke 没挂在发版门禁里 —— 那它就不是「替覆盖率作证的那一层」")


def _pyproject() -> dict:
    """读 pyproject.toml。

    3.11+ 用标准库 `tomllib`；3.10 需要 `tomli`。**两者都没有时不静默跳过** ——
    "读不到 = 没验"，所以红并给出可行动的处置（对 `importorskip` 的态度与本文件
    另一处一致：跳过与全绿同形，漏的恰好是门禁自己）。
    """
    raw = (ROOT / "pyproject.toml").read_bytes()
    try:
        import tomllib                                    # 3.11+
    except ModuleNotFoundError:                           # 3.10
        try:
            import tomli as tomllib                       # type: ignore
        except ModuleNotFoundError:
            raise AssertionError(
                "读 pyproject.toml 需要 tomllib（3.11+）或 tomli（3.10）——"
                "装一个再跑，别让这条钉子静默跳过") from None
    return tomllib.loads(raw.decode("utf-8"))


def test_sources_parse_under_the_declared_python_floor():
    """`requires-python` 声明的语法下限必须**真的**成立（`ast.parse(feature_version=…)`）。

    为什么需要它（2026-10-25，加 pyproject 时）：`requires-python` 是一条**对外承诺** ——
    写 `>=3.10` 就意味着"3.10 能装能跑"。而这个项目最忌"声称没验过的东西"，
    所以至少把**语法**那一半机械化：用 3.10 的语法规则去 parse 每一个源文件，
    用了 3.10 之后才有的语法就红。

    **这条测的是什么、不是什么**（写清楚，免得被当成更强的保证）：
      · 是：语法下限成立、`pyproject` 里那个数不是拍脑袋写的；
      · 不是：运行时行为。真门禁只跑 3.13（CI 与 lock 文件都在 3.13 上），
        `requires-python` 里的"运行时可用"没有被实测 —— 注释与本用例的理由里都这么写着。
    """
    floor = _pyproject()["project"]["requires-python"]
    nums = re.findall(r"(\d+)\.(\d+)", floor)
    assert nums, f"requires-python 解析不出下限：{floor!r}"
    feature = tuple(int(x) for x in nums[0])              # 只看第一个数（>=3.10）
    assert feature >= (3, 8), f"下限低得可疑：{floor!r}"

    bad = []
    for p in sorted((ROOT / "app").glob("*.py")) + [ROOT / "scripts" / "mcp_server.py"]:
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p), feature_version=feature)
        except SyntaxError as e:
            bad.append(f"{p.relative_to(ROOT)}:{e.lineno} {e.msg}")
    assert not bad, (
        f"这些文件在声明的 Python {feature[0]}.{feature[1]} 语法下 parse 不了，"
        f"而 pyproject 写着 {floor!r}（要么升下限、要么改代码）：{bad}")


def test_pyproject_dependencies_match_the_floor_file():
    """`pyproject` 的依赖与 `requirements.txt` 必须**同一套下限**（别造出第二份清单）。

    为什么需要它（2026-10-25）：加 pyproject 时最省事的写法是把四个依赖抄一遍，
    于是同一件事有了两处 —— 改一处忘一处就漂（本项目为此吃过几次代价，
    所以"清单式的东西要么只有一处、要么有钉子对账"，这里选后者：
    因为 `requirements.txt` 是**桌面应用**的安装路径，pyproject 是**包**的元数据，
    两者的读者不同，硬合成一份反而更差）。

    判据：两边每个包名与下限都一致（**不看 extras** —— uvicorn/pywebview 只在
    pyproject 的 `desktop` extra 里出现是**有意**的，见 pyproject 头部说明）。
    """
    req = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"([A-Za-z0-9_.\-]+)\s*([<>=!~].*)?$", line)
        assert m, f"requirements.txt 里这行解析不了：{line!r}"
        req[m.group(1).lower().replace("_", "-")] = (m.group(2) or "").strip()

    proj = _pyproject()["project"]
    deps = {}
    # dependencies ∪ 所有 extras：uvicorn/pywebview 有意只在 `desktop` extra 里，
    # 但它们**仍然必须在两边下限一致**（本题判的是"同一件事别有两套数"，不是"必须都进 core"）
    specs = list(proj["dependencies"]) + [s
                                         for group in (proj.get("optional-dependencies") or {}).values()
                                         for s in group]
    for spec in specs:
        m = re.match(r"([A-Za-z0-9_.\-]+)\s*([<>=!~].*)?$", spec.strip())
        assert m, f"pyproject 里这条依赖解析不了：{spec!r}"
        deps[m.group(1).lower().replace("_", "-")] = (m.group(2) or "").strip()

    missing = sorted(set(req) - set(deps))
    assert not missing, (
        f"requirements.txt 里有、pyproject（含 extras）里没有：{missing} ——"
        f" 桌面壳的依赖请放进 `[project.optional-dependencies].desktop`，别让它只活在 requirements 里")
    mismatched = {k: (req[k], deps[k]) for k in req if deps.get(k) != req[k]}
    assert not mismatched, f"两处下限不一致（前 requirements / 后 pyproject）：{mismatched}"


def _frontend_text() -> str:
    """整个前端的文本（与 `tests/frontend_src.py:frontend_source()` 同一口径的东西）。"""
    parts = [(ROOT / "web" / "index.html").read_text(encoding="utf-8"),
             (ROOT / "web" / "style.css").read_text(encoding="utf-8")]
    for p in sorted((ROOT / "web").glob("js-*.js")):
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_css_assertions_are_scoped_to_their_rule():
    """对 CSS 属性的断言必须**限定在那条规则内** —— 直接对整份前端断言会守到注释上。

    为什么需要它（2026-10-25 实测，被探针揪出来的）：`HTML` 是整个前端拼起来的字符串，
    而 `web/style.css` 的**文件头注释**里也抄了一遍重点规则（「硬约束（门禁钉死，勿动）：…
    toast 的 `max-width: min(760px)` 与 word-break」）。于是 `assert "max-width: min(760px" in HTML`
    这种写法，把真正的 `#toast` 规则改成 `9000px` **也照样通过** —— 门禁在守注释，不在守规则。
    探针 `js_toast_can_overflow_the_screen` 当时「存活」，就是它把这件事报出来的。

    判据（故意窄，免得误报）：测试源码里凡是**直接对 `HTML` 断言一条 CSS 声明**的写法，
    若那条声明在前端文本里出现不止一次，就红 —— 多出来的那次多半在注释里，
    也就是说这条断言指不到具体规则。已经切过规则的写法（`in rule` / `in modal` / `in mbody`
    或断言 `_css_rule(...)` 的返回值）不在判据范围内。
    """
    front = _frontend_text()
    src = (ROOT / "tests" / "test_frontend.py").read_text(encoding="utf-8")
    # 只认「断言字面量 + 直接 in HTML」这一种写法（正则/变量形式留给人工判断）
    literal = re.compile(r'assert\s+"([^"]+)"\s+in\s+HTML')
    decl = re.compile(r"^[a-z-]+:\s*[^;]{2,60};?$")
    risky = []
    for m in literal.finditer(src):
        frag = m.group(1)
        if decl.match(frag) and front.count(frag) > 1:
            risky.append((frag, front.count(frag)))
    assert not risky, (
        f"这些 CSS 断言对整份前端断言、而该声明在前端文本里出现多次（多半有注释在抄它）："
        f"{risky} —— 改用 `_css_rule(选择器)` 切出规则体再断言，否则门禁守的是注释")


def _ci_run_blocks() -> list[str]:
    """从 `ci.yml` 里取出每个 `run:` 块的内容（**标准库解析**，不引第三方依赖）。

    为什么不用 PyYAML（2026-10-25 实测）：它在这台机器上恰好装着（6.0.3），但**不在
    `requirements-dev.txt` 里** —— 是某个依赖的传递依赖。用 `importorskip("yaml")` 就等于
    让这条钉子在干净环境里**静默跳过**，而这正是本项目最忌的形状（跳过与全绿同形、
    漏的恰好是门禁自己）。所以：只靠标准库，只认这个工作流的**固定形状**
    （`run:` 后接 `|` 块标量，或同一行内联命令）。

    形状变了就会**红**而不是静默放过 —— 这是有意的：这条钉子的职责就是"CI 还在跑同一条入口"，
    形状变了正该有人看一眼。
    """
    lines = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").splitlines()
    blocks, i = [], 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("run:"):
            rest = stripped[len("run:"):].strip()
            if rest and rest != "|":                         # 内联写法
                blocks.append(rest)
                i += 1
                continue
            indent = len(line) - len(line.lstrip())
            body, i = [], i + 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break                                    # 块结束
                body.append(nxt)
                i += 1
            text = "\n".join(body).strip()
            if text:
                blocks.append(text)
            continue
        i += 1
    return blocks


def test_ci_container_smoke_carries_its_own_diagnostics():
    """容器冒烟那一步必须**自带现场**：容器的 stderr 不许被丢掉，断言数现读源码。

    为什么需要它（2026-10-31 实测）：这一条在 v1.10.0 的发版提交上红了，而**日志里看不到原因** ——
    原来是 `docker run ... > /tmp/mcp_out.jsonl 2>/dev/null` 把容器的 stderr（服务起不来时
    唯一会说人话的那一半）丢掉了。当时的现场只剩一句「Process completed with exit code 127」，
    而 127 是 **shell** 的「命令没找到」、与容器无关 —— 一行误导性的现场比没有现场更贵：
    它把排查引到了错的方向（我因此先去翻了 docker build 的日志）。
    判据（**行为面**，不钉字面量写法）：
      ① 容器 stderr 必须被落盘（`2> /tmp/mcp_err.txt`）并打进日志；
      ② 失败时把这两份的尾巴打出来（有 `cat /tmp/mcp_out.jsonl` 与 `cat /tmp/mcp_err.txt`）；
      ③ 断言数**从 app/mcp_server.py 现读**，不是手抄的常数；
      ④ 这个 run 块里不许出现 `2>/dev/null` 这类丢弃 stderr 的写法。
    上界另有钉子：`test_docs.test_readme_mcp_tool_table_lists_every_tool` 对着 TOOL_KIND 判，
    所以"工具数漂了"在那一侧就会被抓住 —— 这条只管"红了要看得出为什么"。
    """
    blocks = _ci_run_blocks()
    smoke = [b for b in blocks if "docker run" in b and "tools/list" in b]
    assert len(smoke) == 1, (
        f"应当恰好一个 run 块做容器 tools/list 冒烟，实测 {len(smoke)} 个：{smoke}")
    s = smoke[0]

    assert "2> /tmp/mcp_err.txt" in s, (
        "容器 stderr 被丢掉了 —— 服务起不来时唯一说人话的那一半就在那里，"
        "而 2026-10-31 的现场只剩一句与容器无关的 exit 127")
    assert "cat /tmp/mcp_err.txt" in s, "stderr 只落盘没打出来，CI 日志里照样看不到现场"
    assert "cat /tmp/mcp_out.jsonl" in s, "stdout 也要打出来：它可能为空，也可能不是协议行"
    assert "registered" in s and "TOOL_KIND" in s, (
        "断言数必须是**从源码现读**的（TOOL_KIND 登记数），手抄常数会随版本漂")
    assert "/dev/null" not in s, (
        "这一步里不许把任何一路丢进 /dev/null —— 丢弃现场正是本轮那次红的根因")


def test_dockerfile_cmd_is_a_clean_json_array():
    """Dockerfile 的 `CMD`（exec 形式）必须是干净的 JSON 数组 —— 里面**不能有注释**。

    为什么需要它（2026-10-31 实测的真事故）：那一行原本写成

        CMD ["python", "-m", "app.mcp_server"]          # 规范入口（= pyproject 的 …）

    exec 形式是 **JSON 数组，而 JSON 没有注释语法** —— Docker 把 `#` 后面那几个词
    一起当成参数传给 python，容器起来就死，stderr 只有一句
    `/bin/sh: 1: [python,: not found`，而 stdout 全空。**镜像构建完全成功**（11 层全绿、
    两条 tag 都打好了），所以本地怎么测源码都是绿的；干净 runner 上那一步 `tools/list`
    冒烟因此拿不到任何回包，红在「容器内 tools/list 没有回结果」——现场还是靠上一轮
    补的 stderr 落盘才看出来的。
    判据：`CMD` 行按 JSON 解析得通、每个 token 都不含空白与 shell 元字符（`#` / `;` / `&` / `|`）。
    `JSONArgsRecommended` 那条 buildkit 警告指的是「用 exec 形式」这个**建议**，
    它拦不住这种写坏 —— 只有把 token 逐个看一遍才拦得住。
    """
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    cmds = [ln for ln in text.splitlines() if ln.strip().startswith("CMD")]
    assert cmds, "Dockerfile 里找不到 CMD —— 入口没了，容器起不来"
    for line in cmds:
        payload = line.split("CMD", 1)[1].strip()
        try:
            arr = json.loads(payload)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"CMD 不是合法 JSON 数组：{line!r}（{exc}）—— exec 形式里写注释会变成参数") from exc
        assert isinstance(arr, list) and arr, f"CMD 必须是 JSON 数组：{line!r}"
        for tok in arr:
            assert not re.search(r"[\s;#&|]", str(tok)), (
                f"CMD 的 token {tok!r} 里有空白或 shell 元字符 —— 多半是把注释写进了同一个数组："
                f"{line!r}")


def test_ci_runs_the_same_release_gate_with_the_exe():
    """CI 必须跑**同一条** `ci_local.py --with-exe`，而**不能**退化成 `--quick` 或手敲清单。

    为什么需要它（2026-10-25 自查发现的缺口）：AGENTS.md 硬性约定 22 写着 CI「按锁文件安装、
    跑同一条 `ci_local.py --with-exe`」，`ci.yml` 开头自己也写着「刻意不复刻一份步骤清单 ——
    两处各写一遍必然漂」。**但没有门禁看着这句承诺**，于是三种坏法都不会红：
      ① 改成 `--quick`（"省点 CI 时间"）—— 跳过的恰好是「改了语义会不会红」那一层，
         而 CI 结论照样绿，与本地 `--quick` 结论分档打印的用心直接抵消；
      ② 删掉 `--with-exe` —— 不再打包、不再跑两个冻结自查，「打包才会坏」的那一类从此无人验；
      ③ 换成手敲的一串命令 —— 正是工作流开头明确拒绝的形状。
    判据：**恰好一个** run 块提到 `ci_local.py`，且它带 `--with-exe`、不带 `--quick`；
    全文件不出现 `--quick`。
    """
    blocks = _ci_run_blocks()
    assert blocks, "ci.yml 里没有解析出任何 run 块 —— 工作流结构变了，看一眼再改这条钉子"

    gate = [b for b in blocks if "ci_local.py" in b]
    assert len(gate) == 1, f"应当恰好一个 run 块跑发版门禁，实测 {len(gate)} 个：{gate}"
    assert "--with-exe" in gate[0], (
        f"CI 的发版门禁少了 --with-exe（不打包、不跑冻结自查）：{gate[0]!r} —— "
        f"「打包才会坏」的那一类（-w 下 stdout 为 None、内嵌前端过期、MCP 标准流）从此无人验")
    assert "--quick" not in gate[0], (
        "CI 跑的是 --quick：它跳过的恰好是「改了语义会不会红」那一层，而 CI 结论照样是绿")

    quick = [b for b in blocks if "--quick" in b]
    assert not quick, f"CI 里不该出现 --quick：{quick}"


def test_ci_installs_chromium_for_the_e2e_downgrade():
    """CI runner 没有系统浏览器，界面门禁的**第三级降级**要靠它装 chromium。

    与 `test_e2e_keeps_the_bundled_chromium_fallback` 是一对：那条钉住"代码里保留第三级"，
    这条钉住"CI 真的把它装上了" —— 只留代码不装浏览器，那一级仍然起不来，
    而 E2E 会**整份静默跳过**（本项目为此付过一次代价）。
    """
    blocks = _ci_run_blocks()
    assert any("playwright install chromium" in b for b in blocks), (
        "CI 没有安装 Playwright 自带 chromium —— runner 上没有系统浏览器，"
        "E2E 会整份静默跳过（跳过又和全绿同形）")


def test_release_gate_step_list_cannot_shrink_silently():
    """`ci_local` 的步骤清单必须**一步不少** —— 少了谁都不会红，只会少打印一行。

    为什么需要它（2026-10-25 自查发现的缺口）：守门禁自己的钉子已经有三条（覆盖率门槛必须显式传、
    跳过数按文件对账、打包前先预检+清场），**但没有一条管"步骤还在不在"**。于是删掉
    「变异探针 mutation_probe」那一步（十几分钟的语义门禁）之后，表格只会从 8 步变 7 步，
    结论照样印「全部通过，可以发版 ✓」—— 与当年 E2E 那 75+ 条整份静默跳过是同一个形状：
    **表格与结论长得一模一样，含义差一整层门禁**。

    判据两头：① 三组步骤（两种模式都跑的 / 只有 full 跑的 / 只有 --with-exe 跑的）一个不缺；
    ② 标签指向的脚本也要对得上 —— 标签留着、命令换成别的东西同样等于把门禁换掉。
    """
    steps = _gate_steps()
    always = ["探针预检（残渣/哑火）", "pytest 全量（含覆盖率门槛+跳过审计）",
              "只读体检 live_check --mcp", "真实浏览器 live_ui_check"]
    full_only = ["变异探针 mutation_probe", "场景测试 scenario_test",
                 "发版回归 release_regression", "交付演练 delivery_drill"]
    exe_only = ["生成构建戳", "生成 exe 版本资源", "打包 PyInstaller",
                "冻结版冒烟 frozen_smoke", "打包 PyInstaller（MCP 控制台产物）", "MCP 冻结版自查"]
    for group, name in ((always, "两种模式都跑"), (full_only, "--quick 有意跳过（full 才跑）"),
                        (exe_only, "--with-exe 才有")):
        missing = [l for l in group if l not in steps]
        assert not missing, (
            f"发版门禁少了这些步骤（{name}）：{missing} —— "
            f"少一步不会红，只会少打印一行，而结论照样写「全部通过，可以发版 ✓」")

    expect = {
        "探针预检（残渣/哑火）": "scripts/mutation_probe.py",
        "pytest 全量（含覆盖率门槛+跳过审计）": "",
        "变异探针 mutation_probe": "scripts/mutation_probe.py",
        "场景测试 scenario_test": "scripts/scenario_test.py",
        "发版回归 release_regression": "scripts/release_regression.py",
        "交付演练 delivery_drill": "scripts/delivery_drill.py",
        "只读体检 live_check --mcp": "scripts/live_check.py",
        "真实浏览器 live_ui_check": "scripts/live_ui_check.py",
        "冻结版冒烟 frozen_smoke": "scripts/frozen_smoke.py",
        "MCP 冻结版自查": "scripts/mcp_frozen_smoke.py",
    }
    wrong = {l: steps[l] for l, want in expect.items() if want and want not in (steps.get(l) or "")}
    assert not wrong, f"这些步骤标签还在、但跑的不是原来那个脚本了：{wrong}"


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
