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
import json
import os
import re
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# 显式把 mtime 拨到一小时前，判据才与"文件系统时间戳粒度"无关：
# Windows 上文件时间约 15.6ms 一跳，同一次 tick 里的两次写会拿到同一个值 ——
# 直接比"改前/改后"会让这条用例在缺陷还在时也变绿（A/B 实测过）。
OLD = time.time() - 3600


@pytest.fixture(autouse=True)
def _no_other_probe_running():
    """另一份探针正在跑时，**本文件必须先说出这件事**，而不是报一堆假红。

    为什么需要它（2026-10-25 实测，我自己撞的）：探针在后台跑的时候我顺手跑了
    `pytest tests/test_mutation_probe.py` —— 那一刻源码正被变异着，三条契约用例报出
    「表里的 old 不在文件里」这种**与真故障无关**的 ERROR。这正是本项目最忌的形状：
    噪音会训练人对红脱敏。护栏在探针侧（`_probe_is_running()`），这里把它变成一句人话。
    """
    why = _load_probe()._probe_is_running()
    assert not why, f"此时跑本文件会得到与真故障无关的红：{why}"


def test_probe_ids_are_lowercase_snake_case():
    """探针 id 一律全小写 snake_case —— 违了当场红，别让它去影响别处的数字。

    为什么单列一条（2026-10-25 实测）：我加了一条 `js_esc_close_bypasses_closeOneModal`，
    而 `test_docs.py` 的条数对账正则是 `[a-z0-9_]+` —— 它**静默**数不进来，于是
    「表里 102、文档被要求写 101」。一个命名违规把**别处**的数字带错，红还是红在文档上，
    排查要绕一圈。规矩要么别立，立了就得有钉子。
    """
    probe = _load_probe()
    bad = [m["id"] for m in probe.MUTATIONS if not re.fullmatch(r"[a-z0-9_]+", m["id"])]
    assert not bad, f"这些探针 id 不是全小写 snake_case：{bad}"


def test_stale_probe_lock_is_cleared_when_that_process_is_gone(tmp_path, monkeypatch):
    """进程已经不在的锁 = **陈锁**，必须顺手清掉，否则下一次探针永远起不来。

    外部信号杀掉探针时走不到 `finally`（本仓实测过三次），锁文件就留下来了 ——
    所以"认进程在不在"比"有没有锁文件"更重要。判据用 monkeypatch 把「活着吗」定死，
    免得测试依赖本机恰好有/没有某个 PID。
    """
    probe = _load_probe()
    lock = tmp_path / "fake.lock"
    lock.write_text(json.dumps({"pid": 424242, "started": "2026-01-01 00:00:00"}), encoding="utf-8")
    monkeypatch.setattr(probe, "running_lock", lambda: lock)
    monkeypatch.setattr(probe, "_pid_alive", lambda pid: False)

    assert probe._probe_is_running() == "", "进程已不在的锁不该算「正在跑」"
    assert not lock.exists(), "陈锁没有被清掉 —— 下一次探针会被它挡住"


def test_live_probe_lock_stops_a_second_run_with_a_readable_reason(tmp_path, monkeypatch, capsys):
    """真有另一份探针在跑时：说人话、并且**不开始跑**（退出码 3，不是 1 —— 那是"探针红了"）。"""
    probe = _load_probe()
    lock = tmp_path / "fake.lock"
    lock.write_text(json.dumps({"pid": 424242, "todo": 7, "started": "2026-01-01 00:00:00"}),
                    encoding="utf-8")
    monkeypatch.setattr(probe, "running_lock", lambda: lock)
    monkeypatch.setattr(probe, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(probe.sys, "argv", ["mutation_probe.py"])

    why = probe._probe_is_running()
    assert "正在跑" in why and "424242" in why, why

    code = probe.main()
    out = capsys.readouterr().out
    assert code == 3, f"同时跑两份探针应当拒绝开跑（拿到 {code}）"
    assert "不跑" in out and "正在跑" in out, out
    assert lock.exists(), "拒绝开跑时不该删掉别人的锁"


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


def test_apply_and_run_restores_bytes_for_both_line_endings(tmp_path, monkeypatch):
    """还原必须**逐字节**，而且两种行尾都得管 —— 这两个方向都真被咬过。

    为什么需要它（2026-10-25 实测，我自己踩的两遍）：
      ① 第一版用 `Path.write_text`（文本模式）：Windows 上把 `\\n` 翻成 `\\r\\n`，
         **LF 文件被写成 CRLF**（`git ls-files --eol` 从 `w/lf` 变 `w/crlf`）；
      ② 只加 `write_text(..., newline="")` 补的是**写**侧，而 `read_text` 在读的时候
         （universal newlines）已经把 CRLF 归一成 LF —— 于是反过来把 **CRLF 文件写成 LF**。
    两头都治只有一条路：**读原始字节、还原时把原始字节原样写回**（构造上就逐字节相等）。

    判据是 `read_bytes` 相等 —— `read_text` 两次读回来是一样的，抓不到这类污染；
    它唯一的症状是 `git status` 里多出几个"内容没变"的幽灵改动，而幽灵改动会训练人
    对 status 脱敏。用例特意两种行尾各来一条，并且让 old/new **跨行** ——
    这样 CRLF 那条必须真的把表里的 LF 适配成文件的行尾才匹配得上（否则 status 是 inconclusive）。
    """
    probe = _load_probe()
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    monkeypatch.setattr(probe, "run_tests", lambda files, timeout: ("killed", 0.0))

    cases = {
        "lf": b"const a = 1;\nconst b = 2;\n",
        "crlf": b"const a = 1;\r\nconst b = 2;\r\n",
    }
    for name, raw in cases.items():
        target = tmp_path / f"fake_{name}.js"
        target.write_bytes(raw)
        _pin_mtime(target)

        res = probe.apply_and_run(
            {"id": "t", "file": target.name, "old": "const a = 1;\nconst b = 2;",
             "new": "const a = 9;\nconst b = 3;", "why": "测试用", "tests": []}, timeout=1)

        assert res["status"] == "killed", f"{name} 行尾那条没被杀：{res}"
        assert target.read_bytes() == raw, (
            f"{name} 行尾的文件没有逐字节还原：现在是 {target.read_bytes()!r}，原本 {raw!r}")


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


def _pre_js(mid: str) -> dict:
    return {"id": mid, "file": "web/fake.js", "old": "const A = 1;",
            "new": "const A = 2;", "why": "测试用", "tests": []}


def test_js_mutation_cannot_count_as_killed_by_breaking_syntax(tmp_path, monkeypatch):
    """JS 探针必须先在「语法仍合法」上过闸，否则"被杀"可能只是"页面整个坏了"。

    为什么需要它（2026-10-25，随第一批 js 探针一起）：Python 侧的对应情形是导入失败 →
    pytest 收集错误 → 状态是 `error` 而不是 `killed`，天然被挡住；而 JS 语法错会让整页
    脚本不执行、E2E 全红 —— 看起来正是"被杀"，测的却是"页面坏了"。这两件事混起来，
    探针就会拿假功劳：真正语义正确但没人覆盖的写法，会被记成"有覆盖"。

    三件事都钉：
    ① 闸**接在** `apply_and_run` 里（只定义不接线等于没有）；
    ② 语法坏的变异 → `error`，且说明里点名"语法错"；
    ③ 本机没有 node → 也是 `error`（无法判定 === 没验，不是通过）。
    """
    probe = _load_probe()

    src = (ROOT / "scripts" / "mutation_probe.py").read_text(encoding="utf-8")
    assert '_js_syntax_ok(path)' in src, "语法闸没接在 apply_and_run 里"
    assert 'path.suffix == ".js"' in src, "语法闸没有按扩展名生效"

    good = tmp_path / "good.js"
    good.write_text("const a = 1;\nexport default a;\n", encoding="utf-8")
    ok, why = probe._js_syntax_ok(good)
    assert ok and why == "", f"合法 JS 被判坏了（假红）：{why}"

    bad = tmp_path / "bad.js"
    bad.write_text("const a = ;\n", encoding="utf-8")
    ok2, why2 = probe._js_syntax_ok(bad)
    assert not ok2 and "语法错" in why2, f"语法坏的 JS 没被拦住：{ok2} {why2}"

    monkeypatch.setattr(probe.shutil, "which", lambda _n: None)
    ok3, why3 = probe._js_syntax_ok(good)
    assert not ok3 and "node" in why3, "没有 node 时应当判「无法判定」并算 error，不能放行"


def test_probe_table_keeps_covering_the_frontend_js():
    """前端 JS（交互逻辑所在）必须在探针表里有位置，且条数不能悄悄缩水。

    为什么需要它（2026-10-25 核对）：`web/` 下早有 3 条探针，但全在 `style.css` /
    `index.html` 的**布局形状**上；10 个 `js-*.js`（全部交互逻辑）此前是零覆盖 ——
    而覆盖率回答"这行跑过没有"，只有探针回答"这行错了会不会红"。
    少数几条也要看得见：低于 5 条要么是漏了，要么是有人删了探针，两种都得人看一眼。
    """
    probe = _load_probe()
    js = [m for m in probe.MUTATIONS if str(m["file"]).endswith(".js")]
    assert len(js) >= 5, (
        f"前端 js 探针只剩 {len(js)} 条（id: {[m['id'] for m in js]}）—— "
        "交互逻辑这一层没有'错了会不会红'的守卫了")
    for m in js:
        assert m["tests"], f"{m['id']} 没有 killer 用例 —— 探针跑了也判不出"
        assert str(m["file"]).startswith("web/"), f"{m['id']} 的 target 不在 web/ 下"
