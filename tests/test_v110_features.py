# -*- coding: utf-8 -*-
"""1.1.0 六个新功能面的补测

背景：1.1.0 一次加了会话捕获 / 过程轨迹 / 记忆层 / 用量追踪 / 提醒 / 只读模式，
语句数从 1630 涨到 1943，覆盖率从 94% 掉到 92%（门槛 90，余量只剩 2 点）。
这里把**逻辑真**的分支补上，不为数字硬凑异常分支。
"""
from __future__ import annotations

import json
import time

import pytest

from app.main import _extract_session_text


# ---------------- 用量追踪 ----------------


def test_today_orders_by_priority_before_due_date(app_client):
    """今日待办先按优先级、同级再按截止日（2026-09-29 补）。

    变异 today_loses_priority_sort（丢掉 pri_rank 那一级排序）曾存活：
    既有用例只断言"谁在谁不在"，从没断言**顺序**。丢了优先级排序后，
    高优先任务会被埋在无截止的杂事后面 —— 今日待办的第一屏就是错的。
    """
    _, p = app_client.create_project("今日排序")
    # 同一天、不同优先级：顺序必须 高→中→低
    for pri in ("低", "高", "中"):
        app_client.post(f"/api/projects/{p['id']}/tasks",
                        {"title": f"任务-{pri}", "priority": pri})
    _, body = app_client.get("/api/today")
    titles = [x["title"] for x in body["tasks"]]
    assert titles == ["任务-高", "任务-中", "任务-低"], titles


def test_usage_stats_with_no_records(app_client):
    status, body = app_client.get("/api/usage")
    assert status == 200
    assert body["totals"]["calls"] == 0 and body["totals"]["failed"] == 0


def test_usage_stats_aggregates_by_feature_and_model(app_client):
    import app.llm as llm

    f = llm._usage_file()
    recs = [
        {"time": "2026-09-01 10:00:00", "feature": "监管分析", "endpoint": "A", "model": "m1",
         "prompt_tokens": 100, "completion_tokens": 50, "duration_s": 1.0, "ok": True},
        {"time": "2026-09-01 11:00:00", "feature": "监管分析", "endpoint": "A", "model": "m2",
         "prompt_tokens": 10, "completion_tokens": 5, "duration_s": 1.0, "ok": True},
        {"time": "2026-09-01 12:00:00", "feature": "指令解析", "endpoint": "A", "model": "m1",
         "prompt_tokens": None, "completion_tokens": None, "duration_s": 1.0, "ok": False},
    ]
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")

    status, body = app_client.get("/api/usage")
    assert status == 200
    assert body["totals"]["calls"] == 2, "只统计成功的调用"
    assert body["totals"]["failed"] == 1
    assert body["totals"]["prompt_tokens"] == 110
    assert body["by_feature"]["监管分析"]["calls"] == 2
    assert body["by_model"]["m1"]["calls"] == 1
    assert len(body["recent"]) == 3


def test_usage_stats_skips_corrupt_lines(app_client):
    """文件被人手改坏或写到一半 —— 跳过坏行，不能让整个设置页白屏"""
    import app.llm as llm

    good = {"time": "2026-09-01 10:00:00", "feature": "x", "endpoint": "A", "model": "m",
            "prompt_tokens": 1, "completion_tokens": 1, "duration_s": 1.0, "ok": True}
    llm._usage_file().write_text(
        "这不是 JSON\n" + json.dumps(good, ensure_ascii=False) + "\n", encoding="utf-8")

    status, body = app_client.get("/api/usage")
    assert status == 200 and body["totals"]["calls"] == 1


# ---------------- 项目记忆层 ----------------

def test_memory_crud_and_filters(app_client):
    _, p = app_client.create_project("记忆项目", status="进行中")
    st, m = app_client.post("/api/memories", {"pid": p["id"], "type": "坑", "text": "ffmpeg 8k 要重采样"})
    assert st == 201 and m["memory"]["type"] == "坑"

    app_client.post("/api/memories", {"pid": p["id"], "type": "约定", "text": "提交前跑 npm test"})
    # 以前这里往一个**不存在**的 pid 上挂记忆来验"按项目过滤"—— 那条根本不会被召回，
    # 断言就退化成"少了一条本来就不存在的东西"（恒绿）。现在挂到真实存在的第二个项目上，
    # 过滤才真的在过滤。（另一面：`add_memory` 从今天起拒绝未知 pid，见 test_api）
    _, other = app_client.create_project("另一个真实项目")
    st_orphan, _ = app_client.post("/api/memories",
                                   {"pid": other["id"], "type": "事实", "text": "不属于这个项目的记忆"})
    assert st_orphan == 201, "写到别的项目应当成功，被挡的是按项目过滤的结果"

    _, body = app_client.get(f"/api/memories?pid={p['id']}")
    assert len(body["memories"]) == 2, "按项目过滤"

    _, body = app_client.get(f"/api/memories?pid={p['id']}&mtype=约定")
    assert len(body["memories"]) == 1 and "npm test" in body["memories"][0]["text"]

    _, body = app_client.get(f"/api/memories?pid={p['id']}&q=ffmpeg")
    assert len(body["memories"]) == 1 and "重采样" in body["memories"][0]["text"]


def test_memory_dedupes_same_text_in_same_project(app_client):
    _, p = app_client.create_project("去重项目", status="进行中")
    st1, _ = app_client.post("/api/memories", {"pid": p["id"], "text": "同一条记忆"})
    st2, body = app_client.post("/api/memories", {"pid": p["id"], "text": "同一条记忆"})
    assert st1 == 201 and st2 == 201
    assert body.get("deduped") is True, "同项目同文本不该重复入库"

    _, listing = app_client.get(f"/api/memories?pid={p['id']}")
    assert len(listing["memories"]) == 1


def test_memory_unknown_type_falls_back_to_fact(app_client):
    _, p = app_client.create_project("类型项目", status="进行中")
    _, body = app_client.post("/api/memories", {"pid": p["id"], "type": "瞎写的类型", "text": "某条事实"})
    assert body["memory"]["type"] == "事实"


def test_memory_delete_then_404(app_client):
    _, p = app_client.create_project("删除记忆项目", status="进行中")
    _, m = app_client.post("/api/memories", {"pid": p["id"], "text": "待删除"})
    mid = m["memory"]["id"]
    assert app_client.delete(f"/api/memories/{mid}")[0] == 200
    assert app_client.delete(f"/api/memories/{mid}")[0] == 404


def test_memories_file_corrupt_returns_empty(app_client):
    """文件坏了要能继续用，而不是整个设置页报错"""
    from app.main import memories_file

    memories_file().write_text("{ 不是合法 JSON", encoding="utf-8")
    status, body = app_client.get("/api/memories")
    assert status == 200 and body["memories"] == []


# ---------------- 会话捕获 ----------------

def test_scan_sessions_finds_files_in_custom_source(seeded_client):
    src = seeded_client.data_dir / "scan_src"
    src.mkdir(exist_ok=True)
    (src / "a.json").write_text(json.dumps([{"text": "你好", "say": "user"}]), encoding="utf-8")
    (src / "b.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "在吗"}}) + "\n", encoding="utf-8")

    st, _ = seeded_client.put("/api/ai/config", {
        "session_sources": [{"name": "扫描源", "path": str(src), "type": "text"}]})
    assert st == 200

    status, body = seeded_client.get("/api/sessions/scan")
    assert status == 200
    paths = [str(s.get("path", "")) for s in body.get("sessions", [])]
    assert any(p.endswith("a.json") or p.endswith("b.jsonl") for p in paths), f"扫描结果：{paths}"


def test_scan_sessions_skips_missing_sources(seeded_client):
    """源目录不存在（工具没装）不该报错，跳过即可"""
    missing = seeded_client.data_dir / "根本不存在"
    st, _ = seeded_client.put("/api/ai/config", {
        "session_sources": [{"name": "空源", "path": str(missing), "type": "text"}]})
    assert st == 200
    status, body = seeded_client.get("/api/sessions/scan")
    assert status == 200 and isinstance(body.get("sessions"), list)


def test_extract_session_text_claude_jsonl(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "user", "message": {"role": "user", "content": "帮我看这个 bug"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                     "content": [{"type": "text", "text": "先跑测试"}]}}),
        "坏行",
    ]), encoding="utf-8")
    text = _extract_session_text(str(f))
    assert "帮我看这个 bug" in text and "先跑测试" in text
    assert "用户" in text and "助手" in text, "要能区分角色"


def test_extract_session_text_cline_json(tmp_path):
    f = tmp_path / "ui_messages.json"
    f.write_text(json.dumps([
        {"say": "user", "text": "加一个导出按钮"},
        {"say": "api_req_started", "text": "忽略我"},
        {"say": "assistant", "text": "已加上"},
    ], ensure_ascii=False), encoding="utf-8")
    text = _extract_session_text(str(f))
    assert "加一个导出按钮" in text and "已加上" in text
    assert "忽略我" not in text, "工具调用类的中间消息要过滤掉"


def test_extract_session_text_falls_back_to_raw_and_truncates(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("纯文本会话记录" * 2000, encoding="utf-8")
    text = _extract_session_text(str(f), max_chars=500)
    assert len(text) == 500, "必须按上限截断，别把超大文件整个读进上下文"


def test_extract_session_text_missing_file(tmp_path):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _extract_session_text(str(tmp_path / "不存在.jsonl"))
    assert ei.value.status_code == 404


def test_extract_session_text_never_reads_the_whole_file(tmp_path, monkeypatch):
    """会话文件动辄几百 MB：提取只能读尾部，整份 read_text 等于把内存搭进去。

    断言方式是把 `Path.read_text` 换成"一调就炸" —— 老实现三条分支全用它，
    所以这条用例天然把"有没有改成按需读"钉住。
    """
    from pathlib import Path as _P

    def boom(self, *a, **kw):
        raise AssertionError("不该整份读文件")

    monkeypatch.setattr(_P, "read_text", boom)
    f = tmp_path / "big.jsonl"
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": f"第 {i} 轮"}})
             for i in range(300)]
    f.write_text("\n".join(lines), encoding="utf-8")
    text = _extract_session_text(str(f))
    assert "第 299 轮" in text, "尾部内容要拿到"
    assert "第 0 轮" not in text, "从头读起就说明读超了"


def test_walk_bounded_caps_entries_and_depth(tmp_path):
    """自定义会话源被指到很宽的目录时，扫描必须自带预算与深度上限"""
    from app.main import _walk_bounded

    for d in range(6):
        cur = tmp_path.joinpath(*[f"l{i}" for i in range(d)]) if d else tmp_path
        cur.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (cur / f"f{i}.txt").write_text("x", encoding="utf-8")
    found = list(_walk_bounded(tmp_path, budget=5, max_depth=2))
    assert len(found) <= 5, f"预算 5 却吐了 {len(found)} 个"
    deep = [f for f in found if len(f.relative_to(tmp_path).parts) > 2]
    assert not deep, f"深度上限 2 却扫到了 {deep[:2]}"


def test_newest_files_tolerates_vanished_file(tmp_path):
    """扫描期间文件被删/被占用：跳过它，而不是让整次 /api/sessions/scan 500"""
    from app.main import _newest_files

    live = tmp_path / "live.jsonl"
    live.write_text("x", encoding="utf-8")
    gone = tmp_path / "gone.jsonl"
    got = _newest_files([gone, live], n=8)
    assert got == [live], f"应当只返回还在的那个，实际 {got}"


def test_ingest_session_applies_extracted_results(ai_client):
    """完整链路：会话文件 → 提炼 → 返回待确认的结果（不直接落库）"""
    from pathlib import Path

    src = Path(ai_client.data_dir) / "ingest_src"
    src.mkdir(exist_ok=True)
    f = src / "chat.jsonl"
    f.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "把部署文档补了"}}),
                 encoding="utf-8")

    _, p = ai_client.create_project("会话入库项目", status="进行中")
    st, _ = ai_client.put("/api/ai/config", {
        "session_sources": [{"name": "入库源", "path": str(src), "type": "text"}]})
    assert st == 200

    ai_client.stub.push_json({
        "summary": "聊了部署文档",
        "new_tasks": [{"title": "写部署文档", "desc": "含回滚", "priority": "高", "status": "待办"}],
        "next_action": "先补部署文档",
        "handoff_note_updates": "部署走 CI",
    })
    status, body = ai_client.post("/api/sessions/ingest", {"pid": p["id"], "path": str(f)})
    assert status == 200, body
    assert body["parsed"]["new_tasks"][0]["title"] == "写部署文档"
    assert body["path"] == str(f)


# ---------------- 过程轨迹（journal）在交接物里露出来 ----------------

def test_journal_appears_in_context_and_handoff(app_client):
    """轨迹要能被接手的人看到 —— 否则记了等于没记"""
    _, p = app_client.create_project("轨迹可见项目", status="进行中",
                                     handoff_note="交接说明", next_action="下一步")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "带轨迹的任务"})
    app_client.post(f"/api/tasks/{t['id']}/journal", {"text": "先把 ffmpeg 参数调对"})

    _, ctx = app_client.get(f"/api/context/{p['id']}")
    blob = json.dumps(ctx, ensure_ascii=False)
    assert "ffmpeg" in blob, "机器可读接手包要带上最近轨迹"

    _, md = app_client.get(f"/api/export/{p['id']}")
    assert "ffmpeg" in md, "Markdown 交接包同样要带上"


# ---------------- 记忆层的持久化语义 ----------------
# memories.json 是独立文件，projects 上的那套锁与备份策略一点也管不到它。

def test_memory_writes_are_serialized(app_client, monkeypatch):
    """读-改-写必须关在一个临界区里。把"读完还没写"的窗口拉宽后并发三条：
    不加锁的话三次都从空列表出发、互相覆盖，最后只剩一条。"""
    import threading

    from app import main

    real_load = main._load_memories

    def slow_load():
        items = real_load()
        time.sleep(0.2)
        return items

    monkeypatch.setattr(main, "_load_memories", slow_load)

    def add(i: int) -> None:
        # pid 留空 = 跨项目的通用记忆（这三条测的是备份/并发/上限，不测归属；
        # 以前它们用 "p1" 这种不存在的项目 id，而 `add_memory` 从今天起会拦那种写法）
        main.add_memory(main.MemoryIn(pid="", text=f"并发记忆 {i}"))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    texts = {m["text"] for m in real_load()}
    assert texts == {"并发记忆 0", "并发记忆 1", "并发记忆 2"}, f"并发写丢了记忆：{texts}"


def test_memories_are_capped(app_client, monkeypatch):
    """追加型存储必须有上限（硬性约定 11）"""
    from app import main

    monkeypatch.setattr(main, "MAX_MEMORIES", 5)
    monkeypatch.setattr(main, "KEEP_MEMORIES_ON_TRIM", 3)
    for i in range(8):
        main.add_memory(main.MemoryIn(pid="", text=f"记忆 {i}"))
    items = main._load_memories()
    assert len(items) <= 5, f"超过上限还没裁：{len(items)} 条"
    # 超上限那一次裁到 KEEP，之后又攒回 5 条 —— 留余量是为了不必每次写都裁
    assert [m["text"] for m in items] == [f"记忆 {i}" for i in (3, 4, 5, 6, 7)], \
        f"该裁掉最旧的、留下入列最晚的，实际：{[m['text'] for m in items]}"


def test_backup_covers_the_memory_layer(app_client):
    from app import main

    main.add_memory(main.MemoryIn(pid="", text="备份要带上记忆"))
    main.create_backup()
    latest = sorted((main.data_dir() / "backups").iterdir())[-1]
    assert (latest / "memories.json").exists(), "备份里没有记忆层，回滚后决策/坑会全丢"


def test_restore_brings_memories_back_with_projects(app_client):
    """只回滚 projects.json 会让旧项目带着新记忆，两边对不上"""
    from app import main

    _, p = app_client.post("/api/projects", {"name": "记忆随项目回滚"})
    main.add_memory(main.MemoryIn(pid=p["id"], text="快照里的决策"))
    main.create_backup()
    latest = sorted((main.data_dir() / "backups").iterdir())[-1]

    main.add_memory(main.MemoryIn(pid=p["id"], text="快照之后才写的"))
    status, res = app_client.post("/api/restore", {"source": latest.name})
    assert status == 200, res
    assert {m["text"] for m in main._load_memories()} == {"快照里的决策"}
    assert res.get("memory_count") == 1


def test_export_import_roundtrip_carries_memories(app_client):
    from app import main

    _, p = app_client.post("/api/projects", {"name": "换机迁移"})
    main.add_memory(main.MemoryIn(pid=p["id"], text="导出必须带上记忆"))
    _, dump = app_client.get("/api/data/export")
    assert [m["text"] for m in dump["memories"]] == ["导出必须带上记忆"]

    main._save_memories([])                      # 模拟新机器上还没有记忆
    status, res = app_client.post("/api/data/import", {"content": json.dumps(dump)})
    assert status == 200, res
    assert res.get("memories") == 1
    assert {m["text"] for m in main._load_memories()} == {"导出必须带上记忆"}


def test_import_of_older_export_does_not_wipe_memories(app_client):
    """1.1.0 之前导出的文件里没有 memories 键 —— 那种情况必须"不动"而不是清空"""
    from app import main

    _, p = app_client.post("/api/projects", {"name": "老导出文件"})
    main.add_memory(main.MemoryIn(pid=p["id"], text="本机已有的记忆"))
    legacy = {"app": "ai-kanban", "version": "1.0.0",
              "projects": [dict(p, name="老导出文件-改")]}
    status, res = app_client.post("/api/data/import", {"content": json.dumps(legacy)})
    assert status == 200 and "memories" not in res, res
    assert {m["text"] for m in main._load_memories()} == {"本机已有的记忆"}


def test_deleting_project_drops_its_memories(app_client):
    """孤儿记忆界面上看不到，却永久占着上限名额，还会跟着导出跑到别的机器上"""
    from app import main

    _, gone = app_client.post("/api/projects", {"name": "会被删掉的项目"})
    _, stay = app_client.post("/api/projects", {"name": "留下的项目"})
    main.add_memory(main.MemoryIn(pid=gone["id"], text="挂在它上面的记忆"))
    main.add_memory(main.MemoryIn(pid=stay["id"], text="该留下的记忆"))

    assert app_client.delete(f"/api/projects/{gone['id']}")[0] == 200
    kept = {m["text"] for m in main._load_memories()}
    assert kept == {"该留下的记忆"}, f"删项目没带走它的记忆：{kept}"
