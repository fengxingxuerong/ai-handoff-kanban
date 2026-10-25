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


def test_stale_done_tasks_flags_completions_older_than_last_commit():
    """纯函数口径（第六轮吸取）：done_at 早于最近提交才算「验收后代码又变了」。
    没有 done_at、非已完成、仓库没提交时间，一律不报；截断要留痕（count 是全部）。"""
    from app.repo_watch import stale_done_tasks
    tasks = [
        {"id": "a", "title": "旧验收", "status": "已完成", "done_at": "2026-10-01 10:00"},
        {"id": "b", "title": "新验收", "status": "已完成", "done_at": "2026-10-19 12:00"},
        {"id": "c", "title": "同分钟", "status": "已完成", "done_at": "2026-10-18 16:00"},
        {"id": "d", "title": "没时间戳", "status": "已完成"},
        {"id": "e", "title": "没完成", "status": "进行中", "done_at": "2026-01-01 00:00"},
    ]
    res = stale_done_tasks(tasks, {"date": "2026-10-18 16:00"})
    assert res["count"] == 1 and [s["id"] for s in res["items"]] == ["a"], res
    # 同分钟不算旧（验收与提交谁先谁后说不清，宁可不报）；无提交时间 → 空
    assert stale_done_tasks(tasks, {})["count"] == 0

    many = [{"id": f"x{i}", "title": f"t{i}", "status": "已完成",
             "done_at": "2026-01-01 00:00"} for i in range(15)]
    res2 = stale_done_tasks(many, {"date": "2026-10-18 16:00"}, limit=10)
    assert res2["count"] == 15 and len(res2["items"]) == 10, "截断必须留痕：count 是全部条数"


def test_stale_done_reaches_both_exits_without_touching_status(app_client, tmp_path):
    """两个出口都要带「验收后代码又变了」：/api/projects/{pid}/git 与 /api/repo-watch。
    而且它只是提示 —— 不允许因此改动任何状态或进度。"""
    import app.main as kanban
    repo = tmp_path / "repo-stale"
    _make_repo(repo)
    _, project = app_client.post("/api/projects", {
        "name": "验收后变化", "progress": 50, "repo_path": str(repo),
    })
    pid = project["id"]
    _, task = app_client.post(f"/api/projects/{pid}/tasks",
                              {"title": "已完成任务", "status": "已完成"})
    assert task["done_at"], "状态=已完成却没记 done_at"
    # 回拨验收时间：新提交的时间戳是"现在"，不回拨两边同分钟、按宁可不报的口径不算旧
    kanban.mutate_task(task["id"], lambda t, p: t.__setitem__("done_at", "2026-01-01 09:00"))

    (repo / "app.py").write_text("def main():\n    return 4\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=RepoTest", "-c", "user.email=repo@example.test",
         "commit", "-qm", "later commit")

    status, snap = app_client.get(f"/api/projects/{pid}/git")
    assert status == 200, snap
    assert snap["stale_done"]["count"] == 1, snap["stale_done"]
    assert snap["stale_done"]["items"][0]["id"] == task["id"]

    _, watch = app_client.get(f"/api/repo-watch?pid={pid}")
    assert watch["changes"][0]["stale_done"]["count"] == 1

    _, cur = app_client.get(f"/api/projects/{pid}")
    assert cur["tasks"][0]["status"] == "已完成" and cur["progress"] == 50, \
        "提示不许自动改任何状态 —— 复核只能由人/代理显式写回"


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


def test_baseline_write_is_cross_process_locked(tmp_path, monkeypatch):
    """基线 `repo_watch.json` 的读-改-写必须挡住**第二条进程**，不只是同进程线程。

    为什么需要它（2026-10-25）：这份文件此前只有线程锁（`threading.RLock`），而同一个数据目录
    **可以同时被第二个服务进程打开**（残留实例 + 新实例换端口起、或手工 `uvicorn app.main:app`
    调试 —— "残留进程握着文件"在本项目是实测过的坑）。只挡线程时的丢更新形状是
    "A 读 → B 读 → A 写 → B 写"：A 的改动静默消失，**没有任何报错**。
    同一模块里 `repo_audit.jsonl` 的追加+裁减早就用了跨进程锁，基线这份却没有。

    判据是**行为证据**，不是"代码里写了 blocking_lock"（那是断言动作，不是断言效果）：
      ① 父进程先占住那把侧车锁；
      ② 子进程去 ack —— 它先写一个"我已开始"标记（证明 import 已完成，免得把"启动慢"
         误当成"被挡住"）；
      ③ 占着锁的这段时间里，子进程**必须出不来**；
      ④ 放锁后它必须成功，且落盘的基线里能看到它写的那条。
    没有跨进程互斥时，②③之间子进程会立刻写完退出 → 这条红。
    """
    import sys
    import time

    from app.filelock import blocking_lock

    root = Path(__file__).resolve().parent.parent
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("KANBAN_DATA_DIR", str(data))

    mark = tmp_path / "child_started.txt"
    child = tmp_path / "child_ack.py"
    child.write_text(
        "import sys\n"
        "import pathlib\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "from app import repo_watch as rw\n"
        f"pathlib.Path({str(mark)!r}).write_text('started', encoding='utf-8')\n"
        "rw.ack('proj-a', {'signature': 'sig-1'}, None, None)\n"
        "print('acked')\n", encoding="utf-8")

    env = dict(os.environ, KANBAN_DATA_DIR=str(data),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    lock = data / "repo_watch.lock"

    with blocking_lock(lock, timeout=5.0, what="测试：占住仓库雷达基线锁"):
        p = subprocess.Popen([sys.executable, str(child)], cwd=str(root), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline and not mark.exists() and p.poll() is None:
            time.sleep(0.05)
        assert mark.exists(), "子进程没能走到 ack（先看它自己吐了什么）"
        time.sleep(1.2)                     # 占着锁的这段时间里，它不该把基线写完
        blocked = p.poll() is None

    assert blocked, ("父进程占着侧车锁时，子进程竟然把基线写完了 —— 说明基线的读-改-写"
                     "没有跨进程互斥（只挡得住同进程线程）")

    out = p.communicate(timeout=30)[0].decode("utf-8", "replace")
    assert p.returncode == 0 and "acked" in out, f"放锁后子进程应当成功：rc={p.returncode}\n{out}"
    state = json.loads((data / "repo_watch.json").read_text(encoding="utf-8"))
    assert state.get("proj-a", {}).get("last_seen_signature") == "sig-1", \
        "子进程确认过的基线没落盘"
