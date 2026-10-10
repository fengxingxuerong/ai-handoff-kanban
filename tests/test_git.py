# -*- coding: utf-8 -*-
"""git 集成：只读读取仓库现状，接进交接包与 AI 监管

用真实临时 git 仓库测（git init + commit），不做桩。
重点在三条：不加 shell（仓库路径不可注入）、读不到时降级而不是报错、能看出"看板在推进但代码停了"。
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from app.main import git_snapshot

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="本机没有 git",
)


@pytest.fixture(autouse=True)
def _isolate_from_ancestor_repo(monkeypatch, tmp_path):
    """别让用例受"自己所在的目录"影响。

    受限环境（杀软/沙箱）下要用 `--basetemp .pytest_tmp` 把临时目录放进项目里，
    于是 tmp_path 成了本仓库的子目录 —— 这时 `git rev-parse` 会往上找到项目的 `.git`，
    "这个目录不是仓库"的断言就会假红（2026-09-16 实际踩到：两条用例红了，但产品行为是对的）。
    `GIT_CEILING_DIRECTORIES` 把向上查找钉在 tmp_path，用例从此与位置无关。
    """
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def make_repo(tmp_path, name="demo-repo", commits=3, dirty=False):
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "tester")
    for i in range(commits):
        (repo / f"file{i}.txt").write_text(f"内容 {i}\n", encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", f"第 {i + 1} 次提交：完成模块 {i}")
    if dirty:
        (repo / "file0.txt").write_text("改了点东西\n", encoding="utf-8")
    return repo


# ---------------- 纯函数层 ----------------

def test_snapshot_reads_branch_head_and_commits(tmp_path):
    repo = make_repo(tmp_path, commits=3)
    snap = git_snapshot(str(repo))
    assert snap["ok"] is True
    assert snap["branch"] == "main"
    assert snap["head"]["hash"] and snap["head"]["subject"].startswith("第 3 次提交")
    assert len(snap["commits"]) == 3
    assert snap["dirty_count"] == 0
    assert snap["days_since_commit"] == 0


def test_snapshot_counts_uncommitted_changes(tmp_path):
    repo = make_repo(tmp_path, dirty=True)
    snap = git_snapshot(str(repo))
    assert snap["ok"] is True and snap["dirty_count"] >= 1
    assert any("file0.txt" in f for f in snap["dirty_files"])


def test_snapshot_on_non_git_dir(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    snap = git_snapshot(str(plain))
    assert snap["ok"] is False and "不是 git 仓库" in snap["error"]


@pytest.mark.parametrize("path,keyword", [
    ("", "没有填代码路径"),
    (r"C:\definitely\not\here\xyz", "不存在"),
    (__file__, "不是目录"),
])
def test_snapshot_bad_paths_are_reported(path, keyword):
    snap = git_snapshot(path)
    assert snap["ok"] is False and keyword in snap["error"]


def test_repo_path_cannot_inject_commands(tmp_path):
    """仓库路径里塞 shell 元字符也只是路径 —— 因为我们从不经过 shell"""
    marker = tmp_path / "pwned.txt"
    evil = tmp_path / ("demo; touch " + marker.name)
    evil.mkdir()
    snap = git_snapshot(str(evil))
    assert snap["ok"] is False, "不该把非 git 目录当仓库"
    assert not marker.exists(), "命令被拼接执行了！"


def test_prefetch_snapshots_warms_cache_concurrently(tmp_path):
    """export-all 的 git 串行热点（冷态中位 5.8s）靠并发预取收口：预取后同仓快照
    必须命中缓存（不再 spawn 5 个子进程），去重与不存在路径都不发起。"""
    from app import gitinfo

    repos = [str(make_repo(tmp_path, name=f"repo-{i}", commits=1)) for i in range(6)]
    repos.append(repos[0])                      # 重复路径应去重
    repos.append(str(tmp_path / "不存在的仓"))   # 不存在路径不发起
    n = gitinfo.prefetch_snapshots(repos)
    assert n == 6, f"应发起 6 个（去重后），实际 {n}"
    hits = gitinfo._git_cache.get(repos[0])
    assert hits and hits[1]["ok"] is True, "预取后缓存里必须有该仓库快照"
    # 预取写入后，常规调用直接命中缓存（写个新提交也不会反映出来）
    repo0 = tmp_path / "repo-0"
    subprocess.run(["git", "add", "-A"], cwd=str(repo0), capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "预取后再提交"], cwd=str(repo0), capture_output=True)
    (repo0 / "late.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo0), capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "预取后再提交2"], cwd=str(repo0), capture_output=True)
    snap = git_snapshot(str(repo0))
    assert snap["head"]["subject"] == "第 1 次提交：完成模块 0", "预取后应命中缓存而非重新扫"


def test_export_all_prefetches_repo_snapshots(app_client, tmp_path, monkeypatch):
    """钉住调用行为本身：/api/export-all 渲染前必须对全部 repo_path 并发预取。
    只钉 prefetch_snapshots 直连调用的钉子杀不掉"预取被从 builder 里删掉"的回归。"""
    import app.main as kanban

    repo = make_repo(tmp_path, commits=1)
    app_client.create_project("导出预取项目", repo_path=str(repo))
    called = {}

    def spy(paths, use_cache=True):
        called["paths"] = list(paths)
        return 1

    monkeypatch.setattr(kanban, "prefetch_snapshots", spy)
    _, md = app_client.get("/api/export-all")
    assert "导出预取项目" in md
    assert called.get("paths") and str(repo) in called["paths"], \
        "export-all 渲染前必须预取全部仓库快照（串行探测冷态 5.8s 的收口）"


def test_snapshot_is_cached_briefly(tmp_path):
    repo = make_repo(tmp_path, commits=1)
    first = git_snapshot(str(repo))
    (repo / "new.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "再来一次"], cwd=str(repo), capture_output=True)
    cached = git_snapshot(str(repo))
    assert cached["head"]["hash"] == first["head"]["hash"], "短时间内应命中缓存"
    fresh = git_snapshot(str(repo), use_cache=False)
    assert fresh["head"]["hash"] != first["head"]["hash"], "绕过缓存应读到最新"


# ---------------- 接口层 ----------------

def test_git_endpoint_returns_snapshot(app_client, tmp_path):
    repo = make_repo(tmp_path, commits=2)
    pid = app_client.create_project("带代码的项目", repo_path=str(repo))[1]["id"]
    status, body = app_client.get(f"/api/projects/{pid}/git")
    assert status == 200 and body["ok"] is True
    assert body["branch"] == "main" and len(body["commits"]) == 2


def test_git_endpoint_limit(app_client, tmp_path):
    repo = make_repo(tmp_path, commits=5)
    pid = app_client.create_project("限制条数", repo_path=str(repo))[1]["id"]
    assert len(app_client.get(f"/api/projects/{pid}/git?limit=2")[1]["commits"]) == 2
    # 越界的 limit 被夹到 1..50，而不是报错
    assert len(app_client.get(f"/api/projects/{pid}/git?limit=999")[1]["commits"]) == 5


def test_git_endpoint_errors_are_actionable(app_client, tmp_path):
    no_path = app_client.create_project("没填路径")[1]["id"]
    st1, b1 = app_client.get(f"/api/projects/{no_path}/git")
    assert st1 == 400 and "没有填代码路径" in str(b1)

    missing = app_client.create_project("路径不存在", repo_path=r"C:\nope\nope")[1]["id"]
    assert app_client.get(f"/api/projects/{missing}/git")[0] == 400

    assert app_client.get("/api/projects/不存在/git")[0] == 404


def test_git_endpoint_is_never_stale(app_client, tmp_path):
    """接口是给人点的：刚提交完点开必须看到新状态，不能吃缓存"""
    repo = make_repo(tmp_path, commits=1)
    pid = app_client.create_project("新鲜度", repo_path=str(repo))[1]["id"]
    first = app_client.get(f"/api/projects/{pid}/git")[1]

    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=str(repo), capture_output=True)
    (repo / "hot.txt").write_text("刚改的\n", encoding="utf-8")
    fresh = app_client.get(f"/api/projects/{pid}/git")[1]
    assert fresh["dirty_count"] >= 1, "接口返回了缓存里的旧状态"
    assert fresh["head"]["hash"] == first["head"]["hash"]


def test_handoff_markdown_includes_git_section(app_client, tmp_path):
    repo = make_repo(tmp_path, commits=2, dirty=True)
    pid = app_client.create_project("交接含代码现状", repo_path=str(repo),
                                    next_action="继续写")[1]["id"]
    _, md = app_client.get(f"/api/export/{pid}")
    assert "## 代码现状（git）" in md
    assert "分支 `main`" in md
    assert "未提交改动" in md
    assert "第 2 次提交" in md


def test_handoff_still_works_without_git(app_client):
    pid = app_client.create_project("没有仓库", repo_path=r"C:\nope")[1]["id"]
    status, md = app_client.get(f"/api/export/{pid}")
    assert status == 200 and "## 代码现状（git）" not in md, "读不到仓库时不该塞空节"


def test_analyze_prompt_carries_git_facts(ai_client, tmp_path):
    """监管要能看到代码现实，否则发现不了「看板在推进、代码停了」"""
    repo = make_repo(tmp_path, commits=1)
    ai_client.create_project("带仓库的项目", repo_path=str(repo), status="进行中", progress=60)
    ai_client.stub.push_json({"summary": "ok", "insights": [], "global_suggestions": []})
    assert ai_client.post("/api/ai/analyze")[0] == 200

    prompt = str(ai_client.stub.calls[-1]["payload"])
    assert '"git"' in prompt and "days_since_commit" in prompt
    assert "main" in prompt


def test_brief_skips_git_when_unavailable(ai_client):
    ai_client.create_project("没有仓库的项目", repo_path=r"C:\nope\nope")
    brief = ai_client.get("/api/projects")[1]["projects"]
    assert brief, "列表接口不依赖 git"
