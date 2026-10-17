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
