# -*- coding: utf-8 -*-
"""存储"只增不减"与读取边界

1.1.0 新增了 usage.jsonl（流水账）、journal（存在 projects.json 里）、会话捕获（读本机文件送 LLM），
这三处都会随时间或输入持续增长/扩大影响面。这里把上限与边界钉住，防止悄悄变胖或越界。
"""
from __future__ import annotations

import json
import threading

import pytest


@pytest.fixture
def isolated_llm_dir(tmp_path, monkeypatch):
    """usage 用例直接调 llm 层，不走 app_client 夹具 —— 必须自己隔离数据目录。

    否则 `_usage_file()` 会落进用户真实的 ~/.ai-kanban/，把测试数据写进真账本
    （2026-09-16 实际踩到：9 条 "功能N" 记录被追加进真实 usage.jsonl）。
    """
    monkeypatch.setenv("KANBAN_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------------- usage.jsonl：滚动裁剪 ----------------

def test_usage_file_is_trimmed_when_over_limit(monkeypatch, isolated_llm_dir):
    """只留最近 KEEP_ON_TRIM 条 —— 老账没人翻，不裁就是白占磁盘"""
    import app.llm as llm

    monkeypatch.setattr(llm, "MAX_USAGE_LINES", 5)
    monkeypatch.setattr(llm, "KEEP_ON_TRIM", 2)
    f = llm._usage_file()
    assert f.parent == isolated_llm_dir, "必须写在隔离目录，不能碰用户真实数据"
    if f.exists():
        f.unlink()

    for i in range(9):
        llm._record_usage(f"功能{i}", "端点", "模型", None, 0.01, True)

    lines = f.read_text(encoding="utf-8").splitlines()
    # 裁剪是"超上限时才做一次"，不是每写一条都读全文件：9 条进来后应被压到 ≤5 条，
    # 且必须保留最新的（写入过程中会裁一次到 2 条，再追加到 5 条）
    assert len(lines) <= 5, f"应被压回上限内，实际 {len(lines)}"
    assert len(lines) < 9, "裁剪必须真的发生过"
    assert json.loads(lines[-1])["feature"] == "功能8", "裁剪必须保留最新的"


def test_usage_writes_are_serialized(isolated_llm_dir):
    """并发追加不能把行写乱（多线程同时写同一个文件）"""
    import app.llm as llm

    f = llm._usage_file()
    assert f.parent == isolated_llm_dir, "必须写在隔离目录，不能碰用户真实数据"
    if f.exists():
        f.unlink()

    def job(n: int):
        for i in range(20):
            llm._record_usage(f"t{n}", "端点", "模型", None, 0.01, True)

    threads = [threading.Thread(target=job, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = f.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100, f"100 条一条都不能丢，实际 {len(lines)}"
    for ln in lines:
        assert json.loads(ln), "每一行都必须是完整 JSON（不能出现行交错）"


# ---------------- journal：条数与长度上限 ----------------

def test_journal_is_capped_per_task(app_client, monkeypatch):
    """journal 存在 projects.json 里，不设上限会让核心数据文件悄悄变胖"""
    import app.main as kanban

    monkeypatch.setattr(kanban, "MAX_JOURNAL", 5)
    _, p = app_client.create_project("轨迹上限项目", status="进行中")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "反复改状态的任务"})

    for i in range(12):
        status = "进行中" if i % 2 == 0 else "待办"
        st, body = app_client.put(f"/api/tasks/{t['id']}", {"title": "反复改状态的任务", "status": status})
        assert st == 200

    assert len(body["journal"]) == 5, f"应只留最近 5 条，实际 {len(body['journal'])}"
    assert "→" in body["journal"][-1]["text"]


def test_journal_text_is_truncated(app_client, monkeypatch):
    import app.main as kanban

    monkeypatch.setattr(kanban, "MAX_JOURNAL_TEXT", 20)
    _, p = app_client.create_project("轨迹长度项目", status="进行中")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "带长轨迹的任务"})

    long_text = "这是一段很长的过程记录" * 20
    st, body = app_client.post(f"/api/tasks/{t['id']}/journal", {"text": long_text})
    assert st == 201
    assert len(body["text"]) <= 20


def test_clean_journal_normalizes_dirty_data():
    """导入的脏数据也要收口：非字典条目丢掉、超长截断、超量只留最近"""
    from app.main import MAX_JOURNAL_TEXT, clean_journal

    dirty = ["不是字典", {"time": "2026-01-01 00:00", "text": "正常"},
             {"time": "x", "text": "长" * (MAX_JOURNAL_TEXT + 50)}]
    out = clean_journal(dirty)
    assert len(out) == 2 and all(isinstance(x, dict) for x in out)
    assert len(out[-1]["text"]) == MAX_JOURNAL_TEXT


# ---------------- 会话捕获：只能读已配置的源 ----------------

def test_preview_rejects_file_outside_configured_sources(seeded_client):
    """读到的内容会送进 LLM —— 任意路径等于把本机文件搬到外部模型"""
    cfg = seeded_client.data_dir / "config.json"
    assert cfg.exists(), "预置配置文件应存在（它里面就有 API Key）"
    status, body = seeded_client.get(f"/api/sessions/preview?path={cfg}")
    assert status == 400
    assert "已配置的会话源" in str(body)


def test_preview_allows_file_inside_custom_source(seeded_client):
    """自定义源目录里的文件放行"""
    src = seeded_client.data_dir / "my_sessions"
    src.mkdir(exist_ok=True)
    f = src / "chat.json"
    f.write_text(json.dumps([{"text": "你好", "say": "user"}], ensure_ascii=False), encoding="utf-8")

    st, _ = seeded_client.put("/api/ai/config", {
        "session_sources": [{"name": "测试源", "path": str(src), "type": "text"}]})
    assert st == 200

    status, body = seeded_client.get(f"/api/sessions/preview?path={f}")
    assert status == 200 and "你好" in body.get("text", "")


def test_ingest_rejects_file_outside_configured_sources(seeded_client):
    outside = seeded_client.data_dir / "config.json"
    status, body = seeded_client.post("/api/sessions/ingest", {"pid": "x", "path": str(outside)})
    assert status == 400


# ---------------- MCP 只读模式的作用域 ----------------

def test_mcp_readonly_does_not_block_http_writes(seeded_client):
    """语义钉子：这个开关只管 MCP。本机 HTTP（含给 AI 的直链）与手动操作不受影响。
    把它写成测试，是为了防止后来者"顺手"扩大或误解它的作用域。"""
    st, _ = seeded_client.put("/api/ai/config", {"mcp_readonly": True})
    assert st == 200

    status, body = seeded_client.create_project("只读模式下手动建的项目", status="规划中")
    assert status == 201, body


def test_reports_dir_is_capped_and_keeps_the_newest(tmp_path, monkeypatch):
    """监管报告只写不删 = 无界增长，而列表接口是逐文件读的。
    开了定时监管每天一份，一年就是几百份。"""
    from app import main

    monkeypatch.setattr(main, "MAX_REPORTS", 5)
    for i in range(9):
        main._save_report({"id": f"2026{i:04d}", "summary": "s", "insights": []})
    names = sorted(p.stem for p in main.reports_dir().glob("*.json"))
    assert len(names) == 5, f"报告没裁剪：{names}"
    assert names[-1] == "20260008", "必须留下最新的一份"
    assert "20260000" not in names, "该被裁掉的最旧一份还在"
