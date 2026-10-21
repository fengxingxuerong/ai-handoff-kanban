# -*- coding: utf-8 -*-
"""新拆出的纯模块直接单测（2026-09-26 大文件拆分配套）。

为什么单独有一份：`app/main.py` 拆成领域模块之前，这些边界**只能靠端到端间接验证** ——
要起真服务、建真项目、打真接口，才能摸到"依赖上限是不是 10"这种一行常量。
独立成模块后可以直接穷举，断言也能钉到具体值而不是"接口返回 200"。

纪律（与拆分同源）：这里的每条断言都要能被缺陷注入证伪 ——
**删掉被测那一行，这条断言必须红**。写不出来的就不写。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agentfiles import AGENT_ENTRY_TARGETS, build_agent_entries
from app.context_slice import (CONTEXT_SECTIONS, changed_since, has_timestamp,
                               parse_context_sections, parse_since)
from app.due import _clean_due_date, _due_state
from app.handoff_meta import HANDOFF_SECTIONS, context_budget, parse_handoff_sections
from app.idem import _clean_ref
from app.redact import MASK_MARK, redact_text
from app.sessions import _read_tail_text, is_allowed_session_file, _walk_bounded
from app.taskgraph import (MAX_DEPS, clean_deps, executable_tasks, has_cycle,
                           topo_order, _dedupe_ids)


# ---------------- 任务依赖图 ----------------

def test_clean_deps_drops_unknown_duplicate_and_self():
    ids = {"a", "b", "c"}
    got = clean_deps(["a", "a", "b", "不存在", "", "c"], ids)
    assert got == ["a", "b", "c"], f"去重/去空/去不存在：{got}"


def test_clean_deps_caps_at_max():
    ids = {f"t{i}" for i in range(30)}
    got = clean_deps(list(ids), ids)
    # 硬编码 10 而不是 == MAX_DEPS：自引用断言在常量被改小时永远成立
    # （变异 max_deps_shrunk 10→3 曾因此存活，2026-09-29 收口）。
    assert len(got) == 10, f"依赖上限应当生效且是 10：{len(got)}"
    assert MAX_DEPS == 10


def test_clean_deps_accepts_comma_string():
    assert clean_deps("a,b", {"a", "b"}) == ["a", "b"]


def test_has_cycle_sees_self_loop_and_longer_cycle():
    assert has_cycle({"a": ["a"]}) is True
    assert has_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) is True
    assert has_cycle({"a": ["b"], "b": []}) is False


def test_topo_order_keeps_original_order_within_a_level():
    tasks = [
        {"id": "c", "depends_on": []},
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["c"]},
    ]
    assert [t["id"] for t in topo_order(tasks)] == ["c", "a", "b"], "层内必须保持原序"


def test_topo_order_with_a_cycle_still_returns_every_task():
    """有环时不能吞掉任务 —— 交接包少一条任务是静默的数据丢失"""
    tasks = [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]},
             {"id": "c", "depends_on": []}]
    out = topo_order(tasks)
    assert sorted(t["id"] for t in out) == ["a", "b", "c"], f"环兜底丢了任务：{out}"


def test_executable_tasks_ghost_dependency_does_not_block_forever():
    tasks = [{"id": "a", "status": "待办", "depends_on": ["已删掉的前置"]}]
    assert [t["id"] for t in executable_tasks(tasks)] == ["a"], "幽灵前置按已满足处理"


def test_executable_tasks_excludes_done_and_unmet():
    tasks = [
        {"id": "a", "status": "已完成", "depends_on": []},
        {"id": "b", "status": "待办", "depends_on": ["a"]},
        {"id": "c", "status": "待办", "depends_on": ["b"]},
    ]
    assert [t["id"] for t in executable_tasks(tasks)] == ["b"]


def test_dedupe_ids_is_deterministic():
    items = [{"id": "t1"}, {"id": "t1"}, {"id": "t1"}]
    _dedupe_ids(items)
    assert [i["id"] for i in items] == ["t1", "t1~2", "t1~3"], "新 id 必须可预测（不是随机）"


# ---------------- 接手包切片与增量 ----------------

def test_context_sections_accepts_known_names_and_rejects_unknown():
    assert parse_context_sections(None) is None, "没给参数 = 不裁剪"
    assert parse_context_sections("") == set(), "空串 = 一个可选小节都不要"
    assert parse_context_sections("memories, tasks") == {"memories", "tasks"}
    assert parse_context_sections(["tasks", "tasks"]) == {"tasks"}
    assert parse_context_sections("memories，readiness") == {"memories", "readiness"}, "中文逗号也要认"
    with pytest.raises(Exception) as exc:
        parse_context_sections("background")
    assert "background" in str(exc.value) and "project" in str(exc.value), \
        "不认识的 name 要 400 并列出可选值（静默忽略会让调用方以为拿到了）"
    with pytest.raises(Exception) as exc:
        parse_context_sections(123)
    assert "字符串" in str(exc.value)


@pytest.mark.parametrize("raw,want", [
    ("", ""),
    (None, ""),
    ("2026-10-21", "2026-10-21 00:00"),
    ("2026-10-21 09:30", "2026-10-21 09:30"),
    ("2026-10-21T09:30", "2026-10-21 09:30"),
    ("2026-10-21T09:30:45Z", "2026-10-21 09:30"),
])
def test_parse_since_normalizes_common_shapes(raw, want):
    assert parse_since(raw) == want


def test_parse_since_rejects_free_text():
    with pytest.raises(Exception) as exc:
        parse_since("下周三")
    assert "YYYY-MM-DD" in str(exc.value), "日期不合法要说清格式，别静默当没给"


def test_changed_since_is_inclusive_and_fails_open():
    assert changed_since("2026-10-21 09:30", "2026-10-21 09:30") is True, \
        "含边界：同一分钟宁可重复，不能漏"
    assert changed_since("2026-10-21 09:29", "2026-10-21 09:30") is False
    assert changed_since("2026-10-21", "2026-10-21 09:30") is True, "只有日期时整天都算"
    assert changed_since("2026-10-20", "2026-10-21 09:30") is False
    assert changed_since("2026-10-21T09:30:00Z", "2026-10-21 09:30") is True
    assert changed_since("", "2026-10-21 09:30") is True, "缺时间戳 fail-open，不能永久消失"
    assert changed_since("看不懂", "2026-10-21 09:30") is True
    assert changed_since("2026/10/21", "2026-10-21 09:30") is True, \
        "长度够但认不出的时间戳也要 fail-open，不能按字符串大小猜"
    assert changed_since("2026-10-20 09:30", "") is True, "since 为空 = 全量"
    assert has_timestamp("2026-10-21") is True
    assert has_timestamp("2026-10-21T09:30:00Z") is True
    assert has_timestamp("2026/10/21") is False, "认不出的不算有时间戳（要报进 unknown 计数）"


def test_context_sections_constant_is_the_documented_whitelist():
    assert CONTEXT_SECTIONS == ("memories", "code_reality", "start_here", "tasks", "readiness")


# ---------------- 截止日期 ----------------

@pytest.mark.parametrize("raw,want", [
    ("2026-09-05", "2026-09-05"),
    ("2026/9/5", "2026-09-05"),
    ("2026.09.05", "2026-09-05"),
    ("不是日期", ""),
    ("", ""),
])
def test_clean_due_date_normalizes_or_clears(raw, want):
    assert _clean_due_date(raw) == want, f"{raw!r} 应当归一成 {want!r}"


def test_due_state_boundaries():
    today = date.today()
    fmt = lambda d: d.isoformat()                                # noqa: E731
    assert _due_state({"due_date": fmt(today - timedelta(days=1)), "status": "待办"}) == "overdue"
    assert _due_state({"due_date": fmt(today), "status": "待办"}) == "soon", "今天到期算临期不算逾期"
    assert _due_state({"due_date": fmt(today + timedelta(days=2)), "status": "待办"}) == "soon"
    assert _due_state({"due_date": fmt(today + timedelta(days=3)), "status": "待办"}) == "ok"
    assert _due_state({"due_date": fmt(today - timedelta(days=9)), "status": "已完成"}) == "", \
        "已完成的任务不该报逾期（否则仪表盘凭空多出来）"
    assert _due_state({"due_date": "", "status": "待办"}) == ""
    assert _due_state({"due_date": "2026-13-45", "status": "待办"}) == "", "解析不出的按未设处理"


# ---------------- 脱敏 ----------------

@pytest.mark.parametrize("secret", [
    "sk-abcdef1234567890",
    "ghp_AbCdEf1234567890",
    "AKIAIOSFODNN7EXAMPLE",
    "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefgh",
    "postgres://app:S3cr3tPass@db.internal:5432/prod",
    "张三是谁谁 <zhangsan@example.com>",
])
def test_redact_covers_credential_shapes(secret):
    out = redact_text(secret)
    # 邮箱那条规则盖成 `****@****`（不带 MASK_MARK），所以判据统一写"原文凭据不在输出里"
    assert "example.com" not in out or MASK_MARK in out, f"这类凭据没被盖掉：{secret} -> {out}"
    assert secret != out, f"整串原样返回，等于没脱敏：{secret}"


@pytest.mark.parametrize("secret", [
    "密码=Sup3rSecret!",
    "管理密码：Sup3rSecret!",
    "我的密钥=abc12345",
    "口令 = pa55w0rd12",
    "授权码：zhli-2024-key",
])
def test_redact_covers_chinese_keyword_secrets(secret):
    """黑盒探索 2026-10-08：kv 规则的 \b 与中文相邻不成边界，「密码=xxx」这类最常见的
    中文写法整条漏出。中文键名必须与英文 password= 同一待遇。"""
    out = redact_text(secret)
    assert out != secret, f"中文键名凭据没被脱敏：{secret}"
    assert MASK_MARK in out, f"脱敏了但没盖值：{secret} -> {out}"


def test_redact_leaves_ordinary_text_alone():
    text = "下一步：把 app/main.py 的 _due_state 挪到 app/due.py，截止 2026-09-26"
    assert redact_text(text) == text, "正常交接文本不该被动到"


# ---------------- 幂等键 ----------------

def test_long_client_refs_never_collide():
    """超长键**不能简单截断**：两条前 80 字符相同的请求会撞成同一个键，
    第二次直接拿回第一次那条记录的 id —— 静默返回错东西，比报错严重得多。"""
    a = "A" * 80 + "-first"
    b = "A" * 80 + "-SECOND"
    assert _clean_ref(a) != _clean_ref(b)
    assert _clean_ref(a) != _clean_ref(a)[:48], "超长键必须带哈希尾巴，不能只是前缀"


def test_short_client_ref_is_untouched():
    assert _clean_ref("op-123 ") == "op-123"
    assert _clean_ref("") == ""
    # 带内区间（2026-09-29 补）：49~80 字符此前没有任何断言触达，
    # 变异 idem_ref_truncate（≤80 分支改回 ref[:48]）曾因此存活 ——
    # "短键原样保留"必须对**整个 ≤80 区间**成立，不是只对极短键成立。
    assert _clean_ref("x" * 80) == "x" * 80, "80 字符（含）以内必须原样返回"
    assert _clean_ref("y" * 81).startswith("y" * 48) and "~" in _clean_ref("y" * 81), "81 字符起才走哈希"


# ---------------- 交接包小节与尺寸 ----------------

def test_parse_handoff_sections_rejects_unknown_names():
    with pytest.raises(Exception) as e:
        parse_handoff_sections("journal,nonsense", param="omit_sections")
    assert "nonsense" in str(e.value), "不认识的名字要指名道姓地报错，不能静默忽略"


def test_parse_handoff_sections_tolerates_spaces_and_case():
    assert parse_handoff_sections(" journal , Git ", param="omit") == {"journal", "git"}
    assert parse_handoff_sections("", param="omit") == set()
    assert parse_handoff_sections(None, param="omit") == set()


def test_context_budget_levels():
    line = lambda n: "\n".join("x" for _ in range(n))            # noqa: E731
    assert context_budget(line(150))["level"] == "ok"
    assert context_budget(line(151))["level"] == "warn"
    assert context_budget(line(300))["level"] == "warn", "300 行仍在 warn 档"
    assert context_budget(line(301))["level"] == "over"
    # kb 按 UTF-8 字节算：500 个汉字 = 1500 字节 ≈ 1.5KB，按字符数算会只有 0.5
    assert context_budget("字" * 500)["kb"] == 1.5, "必须是 UTF-8 字节，不是字符数"


def test_handoff_sections_are_all_known():
    assert set(HANDOFF_SECTIONS) == {"git", "journal", "memories", "refs", "done"}


# ---------------- 会话扫描 ----------------

def test_walk_bounded_caps_depth_and_entries(tmp_path):
    deep = tmp_path
    for i in range(6):
        deep = deep / f"l{i}"
        deep.mkdir()
    (deep / "a.txt").write_text("x", encoding="utf-8")
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    found = list(_walk_bounded(tmp_path, budget=5, max_depth=2))
    assert len(found) <= 5, f"条目上限没生效：{len(found)}"
    assert not any(p == deep / "a.txt" for p in found), "深度上限没生效"


def test_read_tail_text_reads_only_the_tail(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("A" * 5000 + "ENDING", encoding="utf-8")
    got = _read_tail_text(f, limit_bytes=64)
    assert got.endswith("ENDING"), "尾部必须读得到（这里才是最近的对话）"
    assert len(got) <= 64 + 100


def test_is_allowed_session_file_respects_configured_sources(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    inside = src / "a.jsonl"
    inside.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("app.sessions.session_sources",
                        lambda: [{"name": "s", "path": str(src), "type": "text"}])
    assert is_allowed_session_file(str(inside)) is True
    assert is_allowed_session_file(str(tmp_path / "outside.jsonl")) is False, \
        "源外的文件必须拒绝（否则一次导入能读整块盘）"


# ---------------- 多智能体入口文件 ----------------

def test_build_agent_entries_only_emits_requested_targets():
    p = {"name": "示例项目", "next_action": "跑测试", "status": "进行中"}
    got = build_agent_entries(p, "正文", ["claude"])
    assert list(got) == [AGENT_ENTRY_TARGETS["claude"]], f"只要了一个却给了这些：{list(got)}"
    # claude 那份是"桥接文件"（指向 AGENTS.md），不是正文 —— 各入口形态不同正是要点
    assert got[AGENT_ENTRY_TARGETS["claude"]] != "正文\n"


def test_build_agent_entries_defaults_to_every_known_target():
    p = {"name": "示例项目", "next_action": "", "status": "规划中"}
    got = build_agent_entries(p, "正文")
    assert set(got) == set(AGENT_ENTRY_TARGETS.values()), "不指定时应当覆盖全部已知入口"
    # 路径来自白名单，不接受调用方传入 —— 所以返回值里不可能出现白名单外的路径
    got.update(build_agent_entries(p, "正文", ["../../evil"]))
    assert set(got) <= set(AGENT_ENTRY_TARGETS.values()), "未知目标名被写成了文件"
