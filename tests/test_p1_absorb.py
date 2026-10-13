# -*- coding: utf-8 -*-
"""2026-10 竞品优点吸取（P1）五项的契约钉子

对标来源（research-reports/github-competitor-benchmark-2026-10-06）：
- Backlog.md 三评审检查点 → 任务 `acceptance` 验收标准（本文件 + 接手包/接手页/AGENTS.md 投影）
- Beads 旧记录衰减摘要 → journal 超限**折叠成摘要**而不是静默丢弃
- OKM 知识代谢（timeless/dated） → 记忆 `expires` 时效：过期不召回、不进交接包，且省略要留痕
- Multica Inbox 哲学 → /api/inbox 只列"等人的"（逾期/待审查/阻塞），依赖挡住的不算
- AAIF / Agent Skills 生态 → 第 9 个入口文件投影 .claude/skills/ai-kanban/SKILL.md
  （Skill 投影的契约在 tests/test_agent_files.py，这里不重复）

2026-10-11 第四轮（对标 claude-mem Awareness，研究档案 research-reports/competitor-absorb-2026-10-11/）：
- claude-mem 渐进式上下文披露 / Awareness push → 记忆召回按类型加权：
  决策/坑/约定优先于「事实」（recall_memories，供 AGENTS.md 与 AI 简报两个截断出口共用）
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import app.journal as journal  # MAX_JOURNAL* 的 monkeypatch 点 2026-10-07 起在 app.journal
import app.main as kanban
from app.main import clean_acceptance, clean_journal, compress_journal  # 走 main 的转发别名


def _d(days: int) -> str:
    """相对日期：用例不许依赖墙钟的绝对值（测试约定 5）"""
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------- 验收标准（Backlog.md DoD） ----------------

def test_acceptance_is_capped_deduped_and_trimmed(app_client):
    _, p = app_client.create_project("验收上限项目")
    items = [f"标准{i}" for i in range(kanban.MAX_ACCEPTANCE_ITEMS + 5)]
    st, t = app_client.post(f"/api/projects/{p['id']}/tasks", {
        "title": "带验收标准的任务",
        "acceptance": items + ["", "标准0", "   标准1   "],   # 空串丢弃、重复去重
    })
    assert st == 201
    assert len(t["acceptance"]) == kanban.MAX_ACCEPTANCE_ITEMS, "超过上限必须截断"
    assert t["acceptance"][0] == "标准0" and t["acceptance"][-1] == f"标准{kanban.MAX_ACCEPTANCE_ITEMS - 1}"


def test_acceptance_text_is_trimmed_per_item():
    out = clean_acceptance(["x" * (kanban.MAX_ACCEPTANCE_TEXT + 30), "  合法  ", None, 42])
    assert out[0] == "x" * kanban.MAX_ACCEPTANCE_TEXT, "单条超长要截断"
    assert out[1] == "合法"
    assert "42" in out, "非字符串条目转写保留（比整条丢弃更接近调用方本意）"
    assert None not in out


def test_acceptance_string_input_splits_by_lines():
    assert clean_acceptance("甲\n乙\n\n甲") == ["甲", "乙"], "字符串按行拆、去空去重"
    assert clean_acceptance(123) == [] and clean_acceptance(None) == []


def test_acceptance_patch_ops_and_conflict(app_client):
    _, p = app_client.create_project("验收增量项目")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks",
                           {"title": "增量验收", "acceptance": ["用例绿"]})
    tid = t["id"]
    st, body = app_client.patch(f"/api/tasks/{tid}", {"acceptance_add": ["README 补全", "用例绿"]})
    assert st == 200 and body["acceptance"] == ["用例绿", "README 补全"], "增量要去重地追加"
    st, body = app_client.patch(f"/api/tasks/{tid}", {"acceptance_remove": ["用例绿"]})
    assert st == 200 and body["acceptance"] == ["README 补全"]
    # 整体值与增量同时给 = 语义冲突，报 400 而不是猜（与 tags/refs 同一口径）
    st, body = app_client.patch(f"/api/tasks/{tid}",
                                {"acceptance": ["x"], "acceptance_add": ["y"]})
    assert st == 400 and "acceptance" in str(body)
    # 拼错的动词必须报错，不许静默被丢（硬性约定：不认识的增量动词直接报错）
    st, body = app_client.patch(f"/api/tasks/{tid}", {"accept_add": ["y"]})
    assert st == 400 and "不认识的操作" in str(body)


def test_acceptance_flows_into_context_handoff_and_page(app_client):
    _, p = app_client.create_project("验收流转项目")
    _, t1 = app_client.post(f"/api/projects/{p['id']}/tasks",
                            {"title": "还没做完的", "acceptance": ["全部用例通过", "README 补用法"]})
    _, t2 = app_client.post(f"/api/projects/{p['id']}/tasks",
                            {"title": "已经做完的", "status": "已完成", "acceptance": ["老标准"]})
    pid = p["id"]

    _, ctx = app_client.get(f"/api/context/{pid}")
    todo = next(x for x in ctx["tasks"]["todo"] if x["id"] == t1["id"])
    assert todo["acceptance"] == ["全部用例通过", "README 补用法"], "接手包 slim 必须带验收标准"
    # 已完成的不进任何 slim 分组（它不需要验收清单了）

    _, md = app_client.get(f"/api/export/{pid}")
    assert "### 验收标准" in md and "- [ ] 全部用例通过" in md
    assert "- [ ] 老标准" not in md, "已完成的任务不再列验收标准（它已经验过了）"

    _, page = app_client.get(f"/api/export/page/{pid}")
    assert "☐ 全部用例通过" in page and "☐ 老标准" not in page


def test_sanitize_cleans_dirty_acceptance_on_every_read(app_client):
    """导入/手改的脏数据在每次读上自愈（与 refs 同一纪律）"""
    _, created = app_client.create_project("脏验收项目")
    app_client.post(f"/api/projects/{created['id']}/tasks", {"title": "占位"})
    df = app_client.data_dir / "projects.json"
    data = json.loads(df.read_text(encoding="utf-8"))
    data["projects"][0]["tasks"] = [{
        "id": "t-dirty", "title": "脏验收",
        "acceptance": ["正常的", "", "正常的", 33, "超" * (kanban.MAX_ACCEPTANCE_TEXT + 9)],
    }]
    df.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _, p = app_client.get(f"/api/projects/{created['id']}")
    acc = p["tasks"][0]["acceptance"]
    assert acc[0] == "正常的" and acc.count("正常的") == 1, "去空去重"
    assert any(a.startswith("超") for a in acc) and all(
        len(a) <= kanban.MAX_ACCEPTANCE_TEXT for a in acc), "单条截断"
    assert "33" in acc, "数字条目转写保留"


# ---------------- journal 语义压缩（Beads） ----------------

def test_journal_overflow_folds_into_summary_not_dropped(app_client, monkeypatch):
    """超限的过程记录不再无声消失：折成一条带计数的摘要留在最前面"""
    monkeypatch.setattr(journal, "MAX_JOURNAL", 5)
    _, p = app_client.create_project("折叠项目", status="进行中")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "反复改状态"})
    for i in range(12):
        status = "进行中" if i % 2 == 0 else "待办"
        st, body = app_client.put(f"/api/tasks/{t['id']}",
                                  {"title": "反复改状态", "status": status})
        assert st == 200
    j = body["journal"]
    assert len(j) == journal.MAX_JOURNAL, "总条数必须压回上限内"
    head, newest = j[0], j[-1]
    assert head.get("folded") == 8, f"12 条里溢出的 8 条应被折叠，实际 {head.get('folded')}"
    assert "已折叠" in head["text"], "摘要正文要说明自己是什么"
    assert "状态：" in head["text"], "摘要里要能认出折进去的旧记录内容"
    assert "→" in newest["text"], "最新一条必须原样保留"


def test_compress_journal_keeps_within_cap_for_any_overflow(monkeypatch):
    monkeypatch.setattr(journal, "MAX_JOURNAL", 3)
    entries = [{"time": f"2026-10-0{i} 10:00", "text": f"第{i}条"} for i in range(1, 6)]
    out = compress_journal(entries)
    assert len(out) == 3
    assert out[0]["folded"] == 3 and out[0]["time"] == "2026-10-01~2026-10-03"
    assert [e["text"] for e in out[1:]] == ["第4条", "第5条"]

    out2 = compress_journal(entries[:3])          # 没超限原样返回
    assert out2 == entries[:3]


def test_compress_journal_absorbs_previous_summary(monkeypatch):
    """再次溢出时，旧摘要按它折进来的原始条数累计（folded 永远指原始记录数）"""
    monkeypatch.setattr(journal, "MAX_JOURNAL", 3)
    entries = [{"text": f"第{i}条"} for i in range(5)]
    first = compress_journal(entries)             # folded=3
    more = first + [{"text": "第5条"}, {"text": "第6条"}]
    second = compress_journal(more)
    assert len(second) == 3
    assert second[0]["folded"] == 5, "3 条原始 + 旧摘要里的 3 条 = 5 条原始记录"


# ---------------- 记忆时效（OKM timeless/dated） ----------------

def test_memory_expiry_is_annotated_on_read(app_client):
    _, p = app_client.create_project("时效记忆项目")
    pid = p["id"]
    _, r1 = app_client.post("/api/memories", {"pid": pid, "type": "事实",
                                              "text": "昨天的方案", "expires": _d(-1)})
    assert r1["memory"]["expires"] == _d(-1)
    app_client.post("/api/memories", {"pid": pid, "type": "约定", "text": "今天到期仍有效",
                                      "expires": _d(0)})
    app_client.post("/api/memories", {"pid": pid, "type": "决策", "text": "长期有效"})
    _, lst = app_client.get(f"/api/memories?pid={pid}")
    by_text = {m["text"]: m for m in lst["memories"]}
    assert by_text["昨天的方案"]["expired"] is True
    assert by_text["今天到期仍有效"]["expired"] is False, "到期当天仍算有效"
    assert by_text["长期有效"]["expired"] is False
    assert lst["expired_count"] == 1
    assert "expires" not in by_text["长期有效"], "没设时效不给老数据凭空加键"


def test_bad_expires_format_is_dropped_not_stored(app_client):
    _, p = app_client.create_project("坏日期项目")
    _, r = app_client.post("/api/memories", {"pid": p["id"], "type": "事实",
                                             "text": "下周三过期", "expires": "下周三"})
    assert "expires" not in r["memory"], "认不出的日期清空（变成长期有效），不留脏值"


def test_expired_memory_excluded_from_handoff_and_context_with_note(app_client):
    _, p = app_client.create_project("过期记忆交接项目")
    pid = p["id"]
    # 交接包的记忆小节挂在任务清单下（没有任务时整段不出现——既有行为），先放一条任务
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "占位任务"})
    app_client.post("/api/memories", {"pid": pid, "type": "坑", "text": "已过期的坑",
                                      "expires": _d(-1)})
    app_client.post("/api/memories", {"pid": pid, "type": "约定", "text": "仍然有效的约定"})
    _, md = app_client.get(f"/api/export/{pid}")
    assert "仍然有效的约定" in md
    assert "已过期的坑" not in md, "过期记忆不能作为当前事实混进交接包"
    assert "已过时效" in md, "省了几个要说话：接手方不能把『没写』读成『没有』"

    _, ctx = app_client.get(f"/api/context/{pid}")
    assert ctx["expired_memory_count"] == 1
    assert all(m["text"] != "已过期的坑" for m in ctx["memories"])


def test_expired_memory_not_recalled_into_agents_md(app_client):
    _, p = app_client.create_project("过期记忆召回项目")
    app_client.post("/api/memories", {"pid": p["id"], "type": "决策", "text": "过期决策",
                                      "expires": _d(-1)})
    app_client.post("/api/memories", {"pid": p["id"], "type": "决策", "text": "有效决策"})
    local = kanban._build_agents_md_local(kanban.find_project(p["id"])[1])
    assert "有效决策" in local
    assert "过期决策" not in local, "AGENTS.md 召回的是『现在还成立』的记忆"


# ---------------- 待拍板 Inbox（Multica） ----------------

def test_inbox_lists_only_tasks_waiting_for_a_human(app_client):
    _, p = app_client.create_project("拍板项目")
    pid = p["id"]
    _, overdue = app_client.post(f"/api/projects/{pid}/tasks",
                                 {"title": "逾期任务", "due_date": _d(-1)})
    _, review = app_client.post(f"/api/projects/{pid}/tasks",
                                {"title": "待验收任务", "status": "待审查"})
    _, blocked = app_client.post(f"/api/projects/{pid}/tasks",
                                 {"title": "阻塞任务", "status": "阻塞"})
    app_client.post(f"/api/projects/{pid}/tasks",
                    {"title": "还没到期的", "due_date": _d(3)})
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "没设截止的"})
    app_client.post(f"/api/projects/{pid}/tasks",
                    {"title": "做完的不逾期", "status": "已完成", "due_date": _d(-9)})
    # 这条钉子必须"非空转"：waiter 同时踩中两类（阻塞 + 逾期）。2026-10-07 交叉验收
    # 发现原用例的 waiter 没状态、没截止——不管实现有没有依赖排除它都不会上榜，
    # 断言恒真；而实现恰恰漏了"依赖没满足的不算"这条过滤（AGENTS.md 约定 27 写了三处）。
    _, waiter = app_client.post(f"/api/projects/{pid}/tasks",
                                {"title": "被前置挡住的", "status": "阻塞", "due_date": _d(-2)})
    app_client.patch(f"/api/tasks/{waiter['id']}", {"depends_on": [overdue["id"]]})

    _, box = app_client.get("/api/inbox")
    assert box["counts"] == {"review": 1, "blocked": 1, "overdue": 1}, box["counts"]
    kinds = {i["title"]: i["kind"] for i in box["items"]}
    assert kinds == {"逾期任务": "overdue", "待验收任务": "review", "阻塞任务": "blocked"}
    assert kinds.get("被前置挡住的") is None, "依赖没满足会自然流转，不是等人拍板"
    assert box["total"] == 3
    # 排序：待审查（最接近完成）在最前
    assert box["items"][0]["kind"] == "review"


def test_inbox_empty_board(app_client):
    _, box = app_client.get("/api/inbox")
    assert box["items"] == [] and box["total"] == 0
    assert box["counts"] == {"review": 0, "blocked": 0, "overdue": 0}


def test_inbox_overdue_check_reuses_normalized_due_dates(app_client, monkeypatch):
    """逾期判定必须建立在归一后的 due_date 上：`2026-1-2` 这类脏值在读取时已归一
    （数据层约定 8），归一失败（不存在/乱格式）的任务绝不能凭空变成逾期。"""
    _, p = app_client.create_project("脏截止项目")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks",
                           {"title": "乱格式截止", "due_date": "not-a-date"})
    assert t["due_date"] == "", "清洗失败的截止日期应留空"
    _, box = app_client.get("/api/inbox")
    assert box["total"] == 0, "没有可信的截止日期就没有逾期，不能拿脏串跟今天比"


# ---------------- 记忆加权召回（claude-mem Awareness，2026-10-11） ----------------

def test_recall_memories_prioritizes_high_value_types_then_newest():
    """`recall_memories`：决策/坑/约定永远在「事实」前面，同类内时间新的在前。
    对标 claude-mem Awareness —— 高价值观察优先注入，晚写的普通事实不能把
    更早的坑/决策挤出截断窗口（交接时挤掉一条坑比挤掉一条背景事实严重得多）。"""
    from app.due import recall_memories
    rows = [
        {"type": "事实", "text": "新事实", "time": "2026-10-11 10:00"},
        {"type": "坑", "text": "早坑", "time": "2026-10-01 09:00"},
        {"type": "事实", "text": "旧事实", "time": "2026-10-02 08:00"},
        {"type": "决策", "text": "中决策", "time": "2026-10-05 11:00"},
    ]
    out = recall_memories(rows, 3)
    assert [m["text"] for m in out] == ["中决策", "早坑", "新事实"], \
        "高价值类型（决策/坑/约定）优先，同类内时间新在前；截断窗口让位给高价值而不是背景"


def test_recall_memories_treats_unknown_types_as_facts_and_is_stable():
    from app.due import recall_memories
    rows = [
        {"type": "什么鬼类型", "text": "怪类型", "time": "2026-10-11 09:00"},
        {"type": "坑", "text": "坑", "time": "2026-10-01 08:00"},
    ]
    assert [m["text"] for m in recall_memories(rows, 2)] == ["坑", "怪类型"], \
        "未知类型按事实对待（_sanitize 归一后的兜底同口径），且不吞条目"


def test_agents_md_recalls_high_value_memories_inside_the_window(app_client):
    """AGENTS.md 的记忆召回：决策/坑/约定先于晚写的普通「事实」。

    旧实现是纯时间倒序取前 12 条：先记下一条坑、再记 15 条背景事实，
    坑就被挤出 12 条窗口 —— 恰恰是交接时最亏的情况（接手方看不见踩过的坑）。
    加权后坑回到窗口内。"""
    _, p = app_client.create_project("加权召回项目")
    pid = p["id"]
    app_client.post("/api/memories", {"pid": pid, "type": "坑", "text": "早记录的坑",
                                      "source": "test"})
    for i in range(15):
        app_client.post("/api/memories", {"pid": pid, "type": "事实",
                                          "text": f"后记背景{i}", "source": "test"})
    md = kanban._build_agents_md_local(kanban.find_project(pid)[1])
    assert "早记录的坑" in md, "高价值坑被后记背景挤出 AGENTS.md 召回窗口"
    assert "后记背景0" in md, "窗口不为一条坑让位而清空，事实照常回填剩余名额"


def test_projects_brief_memory_recall_is_weighted_and_masked():
    """AI 简报的记忆段与 AGENTS.md 同套召回（recall_memories），且仍过脱敏。

    顺带修掉原实现的隐性不一致：它直接取 bucket 的 `[-10:]`，而 bucket 是
    存储序（读盘顺序）不是时间序 —— 现在先喂进 recall_memories 再截取。"""
    import app.aiprompt as aiprompt
    brief = aiprompt.projects_brief(
        [{"id": "p1", "name": "甲", "status": "进行中", "progress": 10,
          "priority": "中", "tags": [], "tasks": []}],
        memories_by_pid_fn=lambda: {
            "p1": [
                {"type": "事实", "text": "后记背景", "time": "2026-10-11 09:00"},
                {"type": "坑", "text": "早坑且密钥 sk-briefmem123456", "time": "2026-10-01 08:00"},
            ]},
        git_snapshot_fn=lambda *a, **kw: {"ok": False},
    )
    texts = [m["text"] for m in brief[0]["memories"]]
    assert texts[0].startswith("早坑且密钥"), f"坑优先于晚写事实：{texts}"
    assert texts[1] == "后记背景", f"事实落后于坑：{texts}"
    assert "sk-briefmem123456" not in texts[0], "模型简报的记忆依然要脱敏（外出边界）"
    assert "sk-********" in texts[0], "掩码保留，只盖凭据那一段"


# ---------------- 条目级尺寸估算（渐进式披露的最小落地，2026-10-11） ----------------
# claude-mem 用"索引层 50-100 token / 详情层按需 500-1000 token"换上下文节省；本项目只有
# 一层，能落地的是**把每条的成本报出来**，让消费方自己决定要不要读进去。

def test_text_size_counts_cjk_and_ascii_and_rounds_up():
    """尺寸口径：CJK 1 字 ≈ 1 token、其余 4 字符 ≈ 1 token 且**上取整**（宁高不低）。"""
    from app.handoff_meta import text_size, estimate_tokens
    assert text_size("") == {"chars": 0, "kb": 0.0, "est_tokens": 0}
    assert text_size("中文四字")["est_tokens"] == 4, "CJK 按 1 字 1 token"
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2, "上取整：宁可略高，也别让消费方以为还装得下"
    mixed = text_size("甲乙丙def")
    assert mixed["chars"] == 6 and mixed["est_tokens"] == 4, f"混合文本口径：{mixed}"
    from app.handoff_meta import size_note
    assert size_note("中文四字") == "≈4 字/≈4 tok", "给模型看的那行标注格式要稳定"


def test_memories_api_reports_per_item_size(app_client):
    """列表接口给每条记忆带上尺寸 —— 以前只有"整包多少行"这一层（context_budget），
    单条有多贵没人知道：一条 300 字的坑和一条 12 字的约定在列表里长得一样长。"""
    _, p = app_client.create_project("记忆尺寸")
    pid = p["id"]
    app_client.post("/api/memories", {"pid": pid, "type": "坑", "text": "坑" * 300})
    app_client.post("/api/memories", {"pid": pid, "type": "约定", "text": "短约定"})
    _, body = app_client.get("/api/memories?pid=" + pid)
    by_text = {m["text"]: m for m in body["memories"]}
    big = by_text["坑" * 300]
    small = by_text["短约定"]
    assert big["chars"] == 300 and big["est_tokens"] >= 300, f"大条目的尺寸：{big}"
    assert small["chars"] == 3 and small["est_tokens"] < big["est_tokens"], \
        f"小条目也要如实报，且明显小于大条目：{small}"
    assert big["kb"] > small["kb"] > 0, "KB 也按 UTF-8 字节算（中文约 3 字节/字）"

