# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _make_repo(repo: Path) -> None:
    repo.mkdir()
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    (repo / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=RepoTest", "-c", "user.email=repo@example.test",
         "commit", "-qm", "initial")


def test_repo_watch_detects_change_and_acks_with_audit(app_client, tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    status, project = app_client.post("/api/projects", {
        "name": "仓库变化验证", "progress": 30, "repo_path": str(repo),
    })
    assert status == 201, project
    pid = project["id"]
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "已完成任务", "status": "已完成"})
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "待办任务", "status": "待办"})

    status, first = app_client.get(f"/api/repo-watch?pid={pid}")
    assert status == 200, first
    assert first["changes"][0]["changed"] is False

    # 新提交：head 变化，进度仍 30；任务完成度 50 给出建议
    (repo / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=RepoTest", "-c", "user.email=repo@example.test",
         "commit", "-qm", "change app")

    status, changed = app_client.get(f"/api/repo-watch?pid={pid}")
    assert status == 200, changed
    ev = changed["changes"][0]
    assert ev["changed"] is True, ev
    assert ev["suggested_progress"] == 50, ev
    _, current = app_client.get(f"/api/projects/{pid}")
    assert current["progress"] == 30

    status, ack = app_client.post(f"/api/projects/{pid}/repo-ack", {"progress": 50, "note": "test"})
    assert status == 200, ack
    assert ack["before"] == 30 and ack["after"] == 50
    _, current = app_client.get(f"/api/projects/{pid}")
    assert current["progress"] == 50

    status, after = app_client.get(f"/api/repo-watch?pid={pid}")
    assert status == 200
    assert after["changes"][0]["changed"] is False

    data_dir = Path(os.environ["KANBAN_DATA_DIR"])
    audit = data_dir / "repo_audit.jsonl"
    assert audit.exists()
    rows = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows[-1]["before_progress"] == 30
    assert rows[-1]["after_progress"] == 50
    assert rows[-1]["actor"] == "human"


def test_first_sight_with_dirty_files_alerts_once_then_settles(app_client, tmp_path):
    """首次见到就带未提交文件的仓库要提醒一次；建立基线不等于假装没看见。"""
    repo = tmp_path / "repo-dirty"
    _make_repo(repo)
    (repo / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
    status, project = app_client.post("/api/projects", {
        "name": "首见脏仓库", "progress": 10, "repo_path": str(repo),
    })
    assert status == 201, project
    pid = project["id"]

    _, first = app_client.get(f"/api/repo-watch?pid={pid}")
    assert first["changes"][0]["changed"] is True, first
    _, second = app_client.get(f"/api/repo-watch?pid={pid}")
    assert second["changes"][0]["changed"] is False, second


def test_scan_never_suggests_lowering_progress(app_client, tmp_path):
    """任务完成度低于当前进度时不给建议 —— 点一次确认不会把 80% 往回拽成 50%。"""
    repo = tmp_path / "repo-high"
    _make_repo(repo)
    status, project = app_client.post("/api/projects", {
        "name": "高进度项目", "progress": 80, "repo_path": str(repo),
    })
    assert status == 201, project
    pid = project["id"]
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "已完成", "status": "已完成"})
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "待办", "status": "待办"})

    app_client.get(f"/api/repo-watch?pid={pid}")            # 建立基线
    (repo / "app.py").write_text("def main():\n    return 3\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=RepoTest", "-c", "user.email=repo@example.test",
         "commit", "-qm", "change app")

    _, resp = app_client.get(f"/api/repo-watch?pid={pid}")
    ev = resp["changes"][0]
    assert ev["changed"] is True, ev
    assert ev["task_completion"] == 50, ev
    assert ev["suggested_progress"] is None, ev


def test_scan_reports_unreadable_repo_without_raising(app_client, tmp_path):
    """代码路径不存在时如实回报（ok=False + 原因），不抛异常、不影响其它字段。"""
    status, project = app_client.post("/api/projects", {
        "name": "坏路径", "repo_path": str(tmp_path / "nope"),
    })
    assert status == 201, project
    pid = project["id"]
    status, resp = app_client.get(f"/api/repo-watch?pid={pid}")
    assert status == 200, resp
    ev = resp["changes"][0]
    assert ev["ok"] is False and ev["changed"] is False, ev
    assert "不存在" in ev["error"], ev


def test_repo_ack_note_is_length_capped_at_the_boundary(app_client, tmp_path):
    """备注进审计行：超限在边界被拒（422），不是静默裁。"""
    repo = tmp_path / "repo-note"
    _make_repo(repo)
    status, project = app_client.post("/api/projects", {"name": "备注上限", "repo_path": str(repo)})
    assert status == 201, project
    pid = project["id"]

    status, _ = app_client.post(f"/api/projects/{pid}/repo-ack", {"note": "x" * 501})
    assert status == 422
    status, ok = app_client.post(f"/api/projects/{pid}/repo-ack", {"note": "x" * 500})
    assert status == 200, ok


def test_repo_audit_is_rolled_up_at_the_cap(monkeypatch):
    """确认审计是追加型存储：超过上限滚动裁剪，行数有界（硬性约定 11）。"""
    from app import repo_watch as repo_metrics

    monkeypatch.setattr(repo_metrics, "MAX_AUDIT_LINES", 5)
    monkeypatch.setattr(repo_metrics, "KEEP_AUDIT_ON_TRIM", 3)
    for i in range(14):
        repo_metrics.ack("pid-x", {"signature": f"s{i}"}, 0, 1, note=f"n{i}")

    audit = Path(os.environ["KANBAN_DATA_DIR"]) / "repo_audit.jsonl"
    lines = [x for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) <= repo_metrics.MAX_AUDIT_LINES, f"裁剪失效：{len(lines)} 行"
    assert json.loads(lines[-1])["note"] == "n13"


def test_overview_poll_uses_snapshot_cache_but_named_scan_stays_fresh(app_client, tmp_path, monkeypatch):
    """总览巡检（不带 pid）吃 60s 快照缓存；点名单个项目（带 pid）绕过缓存要现值。"""
    from app import gitinfo

    repo = tmp_path / "repo-cache"
    _make_repo(repo)
    status, project = app_client.post("/api/projects", {"name": "缓存口径", "repo_path": str(repo)})
    assert status == 201, project
    pid = project["id"]

    calls = {"n": 0}
    orig = gitinfo._run_git

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(gitinfo, "_run_git", counting)
    monkeypatch.setattr(gitinfo, "_git_cache", {})

    app_client.get("/api/repo-watch")
    after_first = calls["n"]
    assert after_first > 0, "第一次巡检必须真扫"
    app_client.get("/api/repo-watch")
    assert calls["n"] == after_first, "30s 内的第二次巡检应命中缓存，不再起 git 子进程"
    app_client.get(f"/api/repo-watch?pid={pid}")
    assert calls["n"] > after_first, "点名看某个项目必须绕过缓存"


def test_deleting_projects_clears_their_watch_baselines(app_client, tmp_path):
    """删项目（单个与批量）都要清 repo_watch.json 的条目：孤儿既不显示又永远留着。"""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_repo(repo_a)
    _make_repo(repo_b)
    _, pa = app_client.post("/api/projects", {"name": "甲", "repo_path": str(repo_a)})
    _, pb = app_client.post("/api/projects", {"name": "乙", "repo_path": str(repo_b)})

    app_client.get("/api/repo-watch")                       # 建立两份基线
    state_file = Path(os.environ["KANBAN_DATA_DIR"]) / "repo_watch.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert pa["id"] in state and pb["id"] in state

    status, _ = app_client.delete(f"/api/projects/{pa['id']}")
    assert status == 200
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert pa["id"] not in state and pb["id"] in state

    status, _ = app_client.post("/api/projects/bulk-delete", {"ids": [pb["id"]], "confirm": True})
    assert status == 200
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert pb["id"] not in state
