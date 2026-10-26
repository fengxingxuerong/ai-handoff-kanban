# -*- coding: utf-8 -*-
"""基础接口：项目/任务 CRUD、交接导出、删除后 404"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def server(app_client):
    """进程内客户端（原为子进程实例）：更快，且能被覆盖率统计"""
    return app_client


def test_health_and_index(server):
    status, body = server.get("/api/health")
    assert status == 200 and body["ok"] is True
    # 版本号是判断"dist 里的 exe 是否落后于源码"的唯一依据，不能缺
    assert body["version"] and body["build_time"]
    assert body["frozen"] is False          # 测试里一定跑在源码模式下

    status, html = server.get("/")
    assert status == 200 and "AI 交接看板" in html


def test_project_list_shape(server):
    status, data = server.get("/api/projects")
    assert status == 200 and isinstance(data["projects"], list)


def test_english_status_and_priority_aliases_are_normalized(server):
    """英文别名（`in_progress` / `high` / `todo` …）在**入参**上要收，落盘必须是规范值。

    为什么（2026-10-25）：这个工具的卖点是「**任何** agent 都能接手」，而契约以前只收中文
    （`Literal["规划中", …]`）—— 英文 agent 传 `status: "in_progress"` 直接 422，
    等于要求调用方懂中文。那是承诺里唯一货不对板的地方。
    两条判据：① 别名（含大小写/连字符变体）能被收下并归一；② **库里只有规范值一种写法**
    （否则同一状态会有「待办」与「todo」两行，召回、统计、界面全乱）。
    """
    status, proj = server.create_project("别名项目", status="in_progress", priority="High")
    assert status == 201, proj
    assert proj["status"] == "进行中" and proj["priority"] == "高", proj

    status, task = server.post(f"/api/projects/{proj['id']}/tasks", {
        "title": "用英文别名建的任务", "status": "to-do", "priority": "medium"})
    assert status == 201, task
    assert task["status"] == "待办" and task["priority"] == "中", task

    # 读回来还是规范值（不是只在回执里翻译了一下）
    _, detail = server.get(f"/api/projects/{proj['id']}")
    got = [x for x in detail["tasks"] if x["id"] == task["id"]][0]
    assert got["status"] == "待办", got

    # 不认识的值仍要被拒，而且报出它认识的那些（归一那层不许把话说过头）
    status, err = server.create_project("乱填的", status="wtf")
    assert status == 422, (status, err)
    # err 是 JSON 错误体（dict），不是字符串 —— 断言要看序列化后的内容
    assert "规划中" in json.dumps(err, ensure_ascii=False), err


def test_repo_snapshot_previews_redacts_and_never_clobbers(tmp_path, server):
    """看板快照进仓库：默认只预览、**默认脱敏**、**不是我们生成的同名文件一动不动**。

    为什么这三条要一起钉（2026-10-25，选项 A）：这条路的去处是**用户的仓库**，
    而仓库通常会被 push —— 它是把整块板（每条任务标题/描述 + 每条记忆）带出本机的动作。
    规矩与 `/api/export/agent-files` 同源但两处**有意不同**：
      ① 默认脱敏（那边默认关）：那份只写一份 AGENTS.md，这份是整块板的批量导出；
      ② 覆盖认「标记」而不是「存在就跳过」：快照是生成物，重复导出必须能更新自己，
         而用户手写的同名文件默认一动不动 —— 冲掉它就是丢用户的东西。
    """
    repo = tmp_path / "repo"
    (repo / "docs" / "kanban").mkdir(parents=True)
    foreign = repo / "docs" / "kanban" / "README.md"
    foreign.write_text("# 我手写的目录\n", encoding="utf-8")

    st, proj = server.create_project("快照项目", repo_path=str(repo),
                                     handoff_note="轮换 sk-abcdef123456 这把")
    assert st == 201, proj
    url = f"/api/projects/{proj['id']}/repo-snapshot"

    # ① 预览：只报计划，一个字节都不写
    st, plan = server.post(url, {"dry_run": True})
    assert st == 200 and plan["preview"] and not plan["written"], plan
    assert not (repo / "docs" / "kanban" / "board.json").exists(), "预览居然写了盘"
    states = {f["path"]: f["state"] for f in plan["files"]}
    assert states["docs/kanban/README.md"] == "skip-foreign", states

    # ② 写盘：我们的文件落地；别人手写的那份一动不动
    st, res = server.post(url, {"dry_run": False})
    assert st == 200, res
    assert "docs/kanban/board.json" in res["written"]
    assert "docs/kanban/README.md" in res["skipped_foreign"], res
    assert foreign.read_text(encoding="utf-8") == "# 我手写的目录\n", "手写文件被覆盖了"

    # ③ 默认脱敏，而且**报出命中数**（用户得先知道里面有凭据）
    board = (repo / "docs" / "kanban" / "board.json").read_text(encoding="utf-8")
    assert "sk-abcdef123456" not in board and "********" in board, board[:200]
    assert res["redacted"] and res["credential_warning"]["hits"] >= 1, res["credential_warning"]

    # ④ 我们自己生成的那份**能**被更新（否则重复导出没有意义）
    st, again = server.post(url, {"dry_run": False})
    assert "docs/kanban/board.json" in again["written"], again

    # ⑤ 覆盖手写文件必须**显式**要求
    st, forced = server.post(url, {"dry_run": False, "overwrite_foreign": True})
    assert "docs/kanban/README.md" in forced["written"], forced
    assert "我手写的目录" not in foreign.read_text(encoding="utf-8")

    # ⑥ 没有 repo_path 也没给目录 → 400，而且说清该怎么办
    st, other = server.create_project("没仓库的项目")
    st_bad, err = server.post(f"/api/projects/{other['id']}/repo-snapshot", {})
    assert st_bad == 400, (st_bad, err)
    assert "repo_path" in json.dumps(err, ensure_ascii=False), err


def test_repo_snapshot_commit_is_optin_and_touches_only_our_paths(tmp_path, server):
    """默认不碰 git；要提交就**只提交本次写的路径** —— 不许把仓库里别人的改动一起带走。

    这是这个动作敢存在的前提：我们碰的是**用户的仓库**。用 `git commit -- <paths>`
    （指定路径的提交），并顺手钉住"别人的暂存改动提交后仍在暂存区"。
    """
    import subprocess

    repo = tmp_path / "gitrepo"
    repo.mkdir()

    def git(*args):
        # `-c core.quotePath=false`：git 默认把非 ASCII 路径写成 `"\344\272\214..."` 那种
        # 带引号的转义形式，断言会莫名其妙地失败（第一版就栽在这，行为其实是对的）
        return subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(repo), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)

    assert git("init").returncode == 0, git("init").stderr
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "别人的文件.txt").write_text("别人的改动\n", encoding="utf-8")
    assert git("add", "--", "别人的文件.txt").returncode == 0      # 别人的改动**已暂存**

    st, proj = server.create_project("提交项目", repo_path=str(repo))
    assert st == 201
    url = f"/api/projects/{proj['id']}/repo-snapshot"

    st, res = server.post(url, {})
    assert st == 200 and res["commit"] is None, res
    assert git("rev-parse", "HEAD").returncode != 0, "默认不该产生任何提交"

    st, res2 = server.post(url, {"commit": True})
    assert st == 200 and res2["commit"] and res2["commit"]["ok"], res2.get("commit")
    names = git("show", "--name-only", "--format=", "HEAD").stdout
    assert "docs/kanban/board.json" in names, names
    assert "别人的文件.txt" not in names, f"提交把仓库里别人的改动一起带走了：{names}"
    assert "别人的文件.txt" in git("diff", "--cached", "--name-only").stdout, \
        "别人的暂存改动被这次提交吞掉了"


def test_project_crud_roundtrip(server):
    status, project = server.create_project(
        "冒烟测试项目", status="规划中", progress=10, priority="高",
        tags=["测试"], repo_path=r"D:\test\demo",
        background="用于 API 冒烟测试的项目",
        handoff_note="这是一个测试交接说明，包含中文与标点！",
        next_action="跑通冒烟测试",
    )
    assert status == 201 and project["name"] == "冒烟测试项目"
    pid = project["id"]

    status, detail = server.get(f"/api/projects/{pid}")
    assert status == 200 and detail["name"] == "冒烟测试项目"

    status, task = server.post(f"/api/projects/{pid}/tasks", {
        "title": "联调登录模块", "desc": "前后端联调，验收标准为可正常登录",
        "status": "待办", "priority": "高", "agent": "cline",
    })
    assert status == 201 and task["title"] == "联调登录模块"
    tid = task["id"]

    status, updated = server.put(f"/api/tasks/{tid}", {
        "title": "联调登录模块", "desc": "前后端联调，验收标准为可正常登录",
        "status": "进行中", "priority": "高", "agent": "cline",
    })
    assert status == 200 and updated["status"] == "进行中"

    status, md = server.get(f"/api/export/{pid}")
    assert status == 200 and "冒烟测试项目" in md and "交接说明" in md

    assert server.delete(f"/api/tasks/{tid}")[0] == 200
    assert server.delete(f"/api/projects/{pid}")[0] == 200
    assert server.get(f"/api/projects/{pid}")[0] == 404


def test_stats_and_readiness(server):
    status, stats = server.get("/api/stats")
    assert status == 200
    for key in ("project_total", "status_dist", "task_dist", "total_tasks",
                "blocked_tasks", "avg_progress", "avg_readiness", "readiness"):
        assert key in stats


def test_export_all_markdown(server):
    status, md = server.get("/api/export-all")
    assert status == 200 and "全项目交接包" in md


def test_sample_data_is_desensitized(server):
    """示例数据不能含真实项目名/本机路径 —— 它会随 exe 分发"""
    _, data = server.get("/api/projects")
    blob = str(data)
    for leak in ("sqli-scanner", "longmao", "QuAiWei", "墨匠", "EduSmart"):
        assert leak not in blob, f"示例数据仍泄露 {leak}"


# ---------------- 截止日期 ----------------

def test_task_due_date_roundtrip(server):
    status, proj = server.create_project("截止日期项目")
    pid = proj["id"]
    status, task = server.post(f"/api/projects/{pid}/tasks", {
        "title": "有截止的任务", "due_date": "2026-12-31",
    })
    assert status in (200, 201) and task["due_date"] == "2026-12-31"
    # 非法日期被清洗为空，不报错
    status, task2 = server.post(f"/api/projects/{pid}/tasks", {
        "title": "非法日期", "due_date": "明天下午",
    })
    assert status in (200, 201) and task2["due_date"] == ""
    # 更新截止日期
    status, updated = server.put(f"/api/tasks/{task['id']}", {
        "title": "有截止的任务", "due_date": "2027-01-15",
    })
    assert status == 200 and updated["due_date"] == "2027-01-15"


def test_stats_due_counters(server):
    from datetime import date, timedelta
    status, proj = server.create_project("临期逾期项目")
    pid = proj["id"]
    today = date.today()
    server.post(f"/api/projects/{pid}/tasks", {"title": "逾期任务", "due_date": (today - timedelta(days=3)).isoformat()})
    server.post(f"/api/projects/{pid}/tasks", {"title": "临期任务", "due_date": (today + timedelta(days=1)).isoformat()})
    server.post(f"/api/projects/{pid}/tasks", {"title": "远期任务", "due_date": (today + timedelta(days=30)).isoformat()})
    server.post(f"/api/projects/{pid}/tasks", {"title": "已完成不算逾期", "due_date": (today - timedelta(days=5)).isoformat(), "status": "已完成"})
    status, stats = server.get("/api/stats")
    assert status == 200
    assert stats["overdue_tasks"] >= 1, "逾期任务应被统计（已完成的不算）"
    assert stats["duesoon_tasks"] >= 1, "2 天内临期任务应被统计"


# ---------------- 全量数据导入导出 ----------------

def test_data_export_import_roundtrip(server):
    status, proj = server.create_project("迁移项目")
    pid = proj["id"]
    server.post(f"/api/projects/{pid}/tasks", {"title": "迁移任务", "due_date": "2026-12-01"})
    status, exported = server.get("/api/data/export")
    assert status == 200 and exported.get("app") == "ai-kanban"
    assert isinstance(exported.get("projects"), list) and len(exported["projects"]) >= 1
    import json as _json
    content = _json.dumps(exported, ensure_ascii=False)
    # 删掉后用导出内容恢复
    assert server.delete(f"/api/projects/{pid}")[0] == 200
    _, after_del = server.get("/api/projects")
    assert all(p["id"] != pid for p in after_del["projects"])
    status, r = server.post("/api/data/import", {"content": content})
    assert status == 200 and r.get("ok") is True and r.get("imported", 0) >= 1
    _, restored = server.get("/api/projects")
    assert any(p["id"] == pid for p in restored["projects"]), "导入后项目应恢复"
    status, detail = server.get(f"/api/projects/{pid}")
    assert status == 200 and detail["tasks"], "导入后任务应恢复"
    assert detail["tasks"][0]["title"] == "迁移任务"
    assert detail["tasks"][0]["due_date"] == "2026-12-01"


def test_data_import_rejects_garbage(server):
    assert server.post("/api/data/import", {"content": "不是json"})[0] == 400
    assert server.post("/api/data/import", {"content": "[1,2,3]"})[0] == 400
    assert server.post("/api/data/import", {"content": '{"projects": "不是列表"}'})[0] == 400


# ---------------- 全局任务搜索 ----------------

def test_global_task_search(server):
    status, proj = server.create_project("搜索项目")
    pid = proj["id"]
    server.post(f"/api/projects/{pid}/tasks", {"title": "登录模块联调", "desc": "修复 token 过期问题"})
    server.post(f"/api/projects/{pid}/tasks", {"title": "报表导出", "agent": "cline"})
    # 按负责人搜
    status, r = server.get("/api/search/tasks?q=cline")
    assert status == 200 and len(r["results"]) == 1
    assert r["results"][0]["title"] == "报表导出"
    assert r["results"][0]["project_name"] == "搜索项目"
    # 中文描述（percent-encoded：联调）
    status, r2 = server.get("/api/search/tasks?q=%E8%81%94%E8%B0%83")
    assert status == 200 and len(r2["results"]) == 1 and r2["results"][0]["title"] == "登录模块联调"
    # 无匹配 / 空查询
    status, r3 = server.get("/api/search/tasks?q=zzzzz_not_exist")
    assert status == 200 and r3["results"] == []
    status, r4 = server.get("/api/search/tasks")
    assert status == 200 and r4["results"] == []


# ---------------- 机器可读上下文 ----------------

def test_project_context_endpoint(server):
    status, proj = server.create_project("上下文项目")
    pid = proj["id"]
    server.post(f"/api/projects/{pid}/tasks", {"title": "先做这个", "status": "待办", "priority": "高"})
    server.post(f"/api/projects/{pid}/tasks", {"title": "被阻塞的", "status": "待办",
                                               "depends_on": []})
    status, ctx = server.get(f"/api/context/{pid}")
    assert status == 200
    assert ctx["app"] == "ai-kanban"
    assert ctx["project"]["name"] == "上下文项目"
    assert "handoff_note" in ctx["project"] and "next_action" in ctx["project"]
    assert isinstance(ctx["start_here"], list) and len(ctx["start_here"]) >= 1
    assert ctx["readiness"]["score"] >= 0
    assert ctx["usage_hint"]


def test_project_context_404(server):
    assert server.get("/api/context/not_exist_pid")[0] == 404


def test_context_sections_returns_only_requested_parts(app_client):
    """`sections` 是白名单：省上下文可以，但 project 与元数据必须在，
    且省略了什么要写进 `slice.omitted` —— 否则调用方会把"没给"读成"没有"。"""
    import app.main as kanban
    _, p = app_client.create_project("切片项目")
    pid = p["id"]
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "一条任务"})

    s, ctx = app_client.get(f"/api/context/{pid}?sections=tasks,memories")
    assert s == 200, ctx
    assert set(ctx) == {"app", "schema_version", "generated_at", "usage_hint", "project",
                        "slice", "tasks", "memories", "expired_memory_count"}
    assert ctx["slice"]["sections"] == ["memories", "tasks"]
    assert ctx["slice"]["omitted"] == ["code_reality", "start_here", "readiness"]
    assert ctx["project"]["name"] == "切片项目"

    # 空串 = 一个可选小节都不要（仍保留 project/slice）
    s, empty = app_client.get(f"/api/context/{pid}?sections=")
    assert s == 200 and set(empty) == {"app", "schema_version", "generated_at", "usage_hint",
                                       "project", "slice"}
    assert empty["slice"]["sections"] == [] and empty["slice"]["omitted"] == list(kanban.CONTEXT_SECTIONS)

    # 不认识的名字必须 400：静默忽略等于给了一份"看起来对"的另一份包
    s, err = app_client.get(f"/api/context/{pid}?sections=background")
    assert s == 400 and "background" in str(err)


def test_context_sections_skip_git_when_code_reality_is_omitted(app_client, monkeypatch):
    """裁掉 code_reality 就该真的省掉那次 git 探测（不是只把结果藏起来）。"""
    import app.main as kanban
    _, p = app_client.create_project("不探 git", repo_path="D:\\definitely-not-a-repo")
    calls = []
    monkeypatch.setattr(kanban, "git_snapshot",
                        lambda *a, **k: calls.append(1) or {"ok": False, "error": "不应被调用"})
    s, ctx = app_client.get(f"/api/context/{p['id']}?sections=tasks,start_here")
    assert s == 200 and "code_reality" not in ctx
    assert calls == [], "裁掉 code_reality 仍然跑了 git 子进程"


def test_context_since_returns_only_changed_tasks_and_memories(app_client):
    """`since` 只过滤 tasks / memories；start_here、done_count 仍全量 ——
    增量包如果连"现在能做什么"都裁掉，代理会以为没变化就是没事可做。"""
    import app.main as kanban
    _, p = app_client.create_project("增量项目")
    pid = p["id"]
    _, old = app_client.post(f"/api/projects/{pid}/tasks", {"title": "旧待办"})
    _, done = app_client.post(f"/api/projects/{pid}/tasks",
                              {"title": "旧的已完成", "status": "已完成"})
    _, new = app_client.post(f"/api/projects/{pid}/tasks", {"title": "新待办"})
    stamps = {old["id"]: "2026-10-20 08:00", done["id"]: "2026-10-20 08:00",
              new["id"]: "2026-10-21 09:30"}

    def set_task_times(proj, _data):
        for t in proj.get("tasks") or []:
            if t.get("id") in stamps:
                t["updated_at"] = stamps[t["id"]]

    kanban.mutate_project(pid, set_task_times)

    app_client.post("/api/memories", {"pid": pid, "type": "坑", "text": "旧记忆"})
    app_client.post("/api/memories", {"pid": pid, "type": "坑", "text": "新记忆"})

    def set_memory_times(items):
        for m in items:
            if m.get("pid") == pid:
                m["time"] = "2026-10-20 08:00" if m["text"] == "旧记忆" else "2026-10-21 09:30"
        return True, None

    kanban._mutate_memories(set_memory_times)

    s, ctx = app_client.get(f"/api/context/{pid}?since=2026-10-21%2009:00")
    assert s == 200, ctx
    todo_titles = [t["title"] for t in ctx["tasks"]["todo"]]
    assert todo_titles == ["新待办"], f"只应留下 since 之后的任务：{todo_titles}"
    assert ctx["tasks"]["done_count"] == 1, "done_count 是全量，不随 since 缩水"
    assert [m["text"] for m in ctx["memories"]] == ["新记忆"]
    assert {t["title"] for t in ctx["start_here"]} == {"旧待办", "新待办"}, \
        "start_here 必须保持全量（否则代理以为没活干）"
    assert ctx["slice"]["delta"] is True
    assert set(ctx["slice"]) == {"sections", "omitted", "since", "delta", "note",
                                 "changed_tasks", "unchanged_tasks", "changed_memories",
                                 "unchanged_memories", "unknown_timestamp_tasks",
                                 "unknown_timestamp_memories"}
    assert ctx["slice"]["changed_tasks"] == 1 and ctx["slice"]["unchanged_tasks"] == 2
    assert ctx["slice"]["changed_memories"] == 1 and ctx["slice"]["unchanged_memories"] == 1
    assert ctx["slice"]["unknown_timestamp_tasks"] == 0
    assert "分钟" in ctx["slice"]["note"], "分钟精度会重复不会漏，要在回执里说清"

    # 含边界：since 正好等于新任务的分钟 → 仍然要给（重复可忍，漏不可忍）
    s, same_minute = app_client.get(f"/api/context/{pid}?since=2026-10-21%2009:30")
    assert s == 200
    assert [t["title"] for t in same_minute["tasks"]["todo"]] == ["新待办"]

    # 只有日期：按当天 00:00 算
    s, by_day = app_client.get(f"/api/context/{pid}?since=2026-10-21")
    assert s == 200 and [t["title"] for t in by_day["tasks"]["todo"]] == ["新待办"]

    # 未来时间：任务/记忆清空，但 start_here 与 done_count 仍是全量
    s, future = app_client.get(f"/api/context/{pid}?since=2999-01-01")
    assert s == 200 and future["tasks"]["todo"] == [] and future["memories"] == []
    assert future["tasks"]["done_count"] == 1 and len(future["start_here"]) == 2

    # 格式不对要说清，不能当"没给"
    s, err = app_client.get(f"/api/context/{pid}?since=下周三")
    assert s == 400 and "YYYY-MM-DD" in str(err)


def test_context_since_fails_open_for_entries_without_timestamps(app_client):
    """手工导入/老数据可能没有 updated_at：增量里必须保留并计数，不能永久消失。"""
    import app.main as kanban
    _, p = app_client.create_project("老数据项目")
    pid = p["id"]
    _, t = app_client.post(f"/api/projects/{pid}/tasks", {"title": "没有时间戳的老任务"})

    def drop_time(proj, _data):
        for task in proj.get("tasks") or []:
            if task.get("id") == t["id"]:
                task.pop("updated_at", None)

    kanban.mutate_project(pid, drop_time)
    s, ctx = app_client.get(f"/api/context/{pid}?since=2999-01-01")
    assert s == 200
    assert [x["title"] for x in ctx["tasks"]["todo"]] == ["没有时间戳的老任务"]
    assert ctx["slice"]["unknown_timestamp_tasks"] == 1, "fail-open 的条目要报数"



# ---------------- 交接内容脱敏 ----------------

def test_export_redact_hides_secrets(server):
    status, proj = server.create_project("脱敏项目")
    pid = proj["id"]
    secret_note = "配置如下：API_KEY=sk-abcd1234efgh5678，联系 admin@company.com，密码 password: P@ssw0rd9"
    status, updated = server.put(f"/api/projects/{pid}", {
        "name": "脱敏项目", "status": "进行中", "progress": 10, "priority": "高",
        "handoff_note": secret_note,
    })
    assert status == 200
    # 不脱敏：原文在
    _, raw = server.get(f"/api/export/{pid}")
    assert "sk-abcd1234efgh5678" in raw and "admin@company.com" in raw
    # 脱敏：敏感内容被掩码
    _, safe = server.get(f"/api/export/{pid}?redact=1")
    assert "sk-abcd1234efgh5678" not in safe, "API Key 应被脱敏"
    assert "admin@company.com" not in safe, "邮箱应被脱敏"
    assert "********" in safe
    # 全项目导出同样支持
    _, safe_all = server.get("/api/export-all?redact=1")
    assert "sk-abcd1234efgh5678" not in safe_all


@pytest.mark.parametrize("secret", [
    '{"api_key": "abcdef1234567890", "model": "gpt-4o"}',       # JSON 形态（键名带引号）
    "gitlab: glpat-AbCdEf123456",
    "AKIA1234567890ABCDEF",                                      # AWS 长期凭据
    "ASIA1234567890ABCDEF",                                      # AWS 临时凭据
    "postgres://app:S3cr3tPass@db.internal:5432/prod",           # 连接串
    "header: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij1234567890",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEAabcdefg\n-----END RSA PRIVATE KEY-----",
])
def test_redact_covers_common_secret_shapes(secret):
    """脱敏的用途决定它的覆盖面：交接物里最常见的是 JSON 配置和连接串，不是裸 sk-。

    老规则只认 `key: value`（键名不带引号），于是 `"api_key": "xxx"` 整段原样导出；
    JWT / PEM / AWS 那几种形态压根没有规则。
    """
    from app.main import redact_text

    out = redact_text(secret)
    assert "********" in out, f"没有任何掩码：{out}"
    for frag in ("abcdef1234567890", "AbCdEf123456", "1234567890ABCDEF",
                 "S3cr3tPass", "abcdefghij1234567890", "MIIEpAIBAAKCAQEAabcdefg",
                 "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"):
        assert frag not in out, f"{frag} 没被盖住：{out}"


def test_redact_leaves_ordinary_handoff_text_alone():
    """别把有用信息一起盖掉：短提交号、文件路径、中文人名都得原样留着"""
    from app.main import redact_text

    text = "最新提交 3f2a1b9c 修好了登录；文档在 docs/handoff.md；负责人张三；先跑 pytest -n auto"
    assert redact_text(text) == text


def test_summarize_uploads_redacted_text_only(ai_client):
    """会话提炼会把整段对话发到模型那边 —— 贴进来的凭据必须在这一步之前被盖掉"""
    pid = ai_client.create_project("提炼脱敏")[1]["id"]
    ai_client.stub.push_json({"summary": "ok", "new_tasks": [], "memories": [],
                              "next_action": "", "handoff_note_updates": ""})
    status, _ = ai_client.post("/api/ai/summarize", {
        "pid": pid, "text": "帮我改下配置：api_key=abcd1234efgh5678，改完重启服务"})
    assert status == 200
    sent = json.dumps(ai_client.stub.calls[-1]["payload"], ensure_ascii=False)
    assert "abcd1234efgh5678" not in sent, "密钥原文被发出去了"
    assert "********" in sent


def test_analyze_prompt_keeps_ids_but_redacts_free_text(ai_client):
    """监管分析的 prompt：自由文本脱敏，但项目 id / 标题不能一起盖掉（建议要回传对齐）"""
    status, proj = ai_client.post("/api/projects", {
        "name": "脱敏分析项目", "status": "进行中",
        "background": "登录模块用的第三方密钥 secret: zzzz9998887776666，先跑联调"})
    assert status == 201
    ai_client.stub.push_json({"summary": "整体正常", "insights": [], "global_suggestions": []})
    assert ai_client.post("/api/ai/analyze")[0] == 200
    sent = json.dumps(ai_client.stub.calls[-1]["payload"], ensure_ascii=False)
    assert "zzzz9998887776666" not in sent, "背景里的密钥被原样送进模型"
    assert proj["id"] in sent and "脱敏分析项目" in sent


def test_idempotent_replay_writes_nothing_at_all(app_client, monkeypatch):
    """命中幂等键时**一次落盘都不许发生**，不是"写回同样的内容"。

    `mutate_project` 现在认 `fn` 返回 False 表示"没改过"，这条钉子保证以后有人把它
    改回无条件写盘时立刻变红 —— 否则重试虽然不产生重复任务，却仍然在刷 last-good、
    刷 updated_at、并和另一个真实写入抢锁。
    """
    import app.main as kanban

    _, proj = app_client.create_project("回放不落盘")
    pid = proj["id"]
    app_client.post(f"/api/projects/{pid}/tasks?client_ref=replay-1", {"title": "一条"})

    writes = {"n": 0}
    real_save = kanban._save_data

    def counted(*a, **kw):
        writes["n"] += 1
        return real_save(*a, **kw)

    monkeypatch.setattr(kanban, "_save_data", counted)
    s, again = app_client.post(f"/api/projects/{pid}/tasks?client_ref=replay-1", {"title": "一条"})
    assert s == 201 and again["title"] == "一条"
    assert writes["n"] == 0, f"幂等回放仍然写了 {writes['n']} 次盘"


# ---------------- 部分更新（PATCH）与接手包脱敏 ----------------

def test_patch_project_keeps_fields_that_were_not_sent(app_client):
    """`patch_*` 的"没给＝保持原值"发生在临界区内，语义要与 MCP 侧一贯的行为一致"""
    import app.main as kanban

    status, proj = app_client.create_project("部分更新项目")
    assert status == 201
    pid = proj["id"]
    kanban.patch_project(pid, {"progress": 42, "next_action": "先跑测试"})
    _, after = app_client.get(f"/api/projects/{pid}")
    assert after["progress"] == 42 and after["next_action"] == "先跑测试"
    assert after["name"] == "部分更新项目", "没传 name 不能把项目名改掉"

    kanban.patch_project(pid, {"handoff_note": ""})   # 显式空串＝清空
    _, after = app_client.get(f"/api/projects/{pid}")
    assert after["handoff_note"] == "" and after["progress"] == 42


def test_patch_invalid_enum_returns_422_not_500(app_client):
    """PATCH 路由吃裸 dict，`_merge_into` 的 ValidationError 曾以 500 逃逸（黑盒探索
    2026-10-08）。约定 6：非法状态/优先级必须 422，部分更新不是例外；同时校验失败
    绝不能写坏盘上数据。MCP 的 kanban_update_project 走同一个 patch_project，一并受益。"""
    _, proj = app_client.create_project("补丁校验项目")
    pid = proj["id"]
    _, task = app_client.post(f"/api/projects/{pid}/tasks", {"title": "补丁校验任务"})
    tid = task["id"]

    for bad in ({"status": "瘫痪"}, {"progress": -1}, {"priority": "特高"},
                {"tags": {"不是": "数组"}}):
        code, _b = app_client.patch(f"/api/projects/{pid}", bad)
        assert code == 422, f"{bad} 应 422，实际 {code}"
    for bad in ({"status": "瘫痪"}, {"priority": "特高"}):
        code, _b = app_client.patch(f"/api/tasks/{tid}", bad)
        assert code == 422, f"{bad} 应 422，实际 {code}"

    # depends_on 是宽容清洗口径（约定同 milestones）：字符串先拆、未知 id 丢弃，不 422
    code, fixed = app_client.patch(f"/api/tasks/{tid}", {"depends_on": "不是数组"})
    assert code == 200 and fixed["depends_on"] == [], (code, fixed.get("depends_on"))

    _, after = app_client.get(f"/api/projects/{pid}")
    # conftest.create_project 默认 status=进行中 / progress=10；断言的是"没被坏请求改掉"
    assert after["status"] == "进行中" and after["progress"] == 10, "校验失败不许改数据"
    assert after["tasks"][0]["status"] == "待办"


def test_patch_task_rejects_illegal_values_and_reads_once(app_client, monkeypatch):
    """补全必须只做一次整份读：在锁外多读一份快照，就是丢更新的入口"""
    import app.main as kanban

    _, proj = app_client.create_project("读一次项目")
    pid = proj["id"]
    _, task = app_client.post(f"/api/projects/{pid}/tasks",
                              {"title": "读一次", "status": "待办", "priority": "高",
                               "due_date": "2030-01-02"})
    tid = task["id"]

    reads = {"n": 0}
    real = kanban._load_data

    def counted():
        reads["n"] += 1
        return real()

    monkeypatch.setattr(kanban, "_load_data", counted)
    reads["n"] = 0
    updated = kanban.patch_task(tid, {"status": "进行中"})
    assert updated["status"] == "进行中"
    assert reads["n"] == 1, f"一次部分更新读了 {reads['n']} 遍整份数据"
    assert updated["due_date"] == "2030-01-02" and updated["priority"] == "高"

    reads["n"] = 0
    import pytest as _pt
    with _pt.raises(Exception):
        kanban.patch_task(tid, {"status": "乱写"})
    _, back = app_client.get(f"/api/projects/{pid}")
    assert back["tasks"][0]["status"] == "进行中", "校验失败不能写坏数据"


def test_context_redact_covers_task_titles_too(app_client):
    """`/api/context/{pid}` 是**推荐给智能体直接取**的入口，以前偏偏没有脱敏开关。

    开了 `?redact=1` 之后：自由文本盖掉，id / 状态 / 分支原样留着 —— 模型要靠这些
    把建议对回项目。标题曾经被当成"结构化字段"整条放行，于是把密钥写在任务标题里
    （最常见的写法）就从 redact 开关底下漏出去了。掩码只替换凭据那一段，
    所以"干净标题逐字不变"和"脏标题认得出来"要同时成立。
    """
    secret = "api_key=abcd1234efgh5678"
    status, proj = app_client.create_project("接手包脱敏")
    pid = proj["id"]
    app_client.put(f"/api/projects/{pid}", {
        "name": "接手包脱敏", "status": "进行中", "progress": 20, "priority": "高",
        "background": f"第三方服务 {secret}，联系 ops@corp.example.com",
        "next_action": "带 key 的任务"})
    app_client.post(f"/api/projects/{pid}/tasks",
                    {"title": "配密钥", "desc": f"用 {secret} 配置", "status": "待办"})
    app_client.post(f"/api/projects/{pid}/tasks",
                    {"title": "轮换 sk-abcd1234efgh5678 这把", "desc": "标题里带 Key",
                     "status": "待办"})

    _, raw = app_client.get(f"/api/context/{pid}")
    assert "abcd1234efgh5678" in str(raw), "默认行为不变（不传 redact 就是原文）"

    _, safe = app_client.get(f"/api/context/{pid}?redact=1")
    blob = str(safe)
    assert "abcd1234efgh5678" not in blob, "背景/描述/**标题**里的密钥都漏了"
    assert "ops@corp.example.com" not in blob
    assert safe["project"]["id"] == pid and safe["project"]["name"] == "接手包脱敏"
    titles = [t["title"] for t in safe["tasks"]["todo"]]
    assert "配密钥" in titles, "不带凭据的标题必须逐字不变（模型要用它对任务）"
    dirty = [t for t in titles if t != "配密钥"]
    assert dirty and dirty[0] == "轮换 sk-******** 这把", dirty[0]
    assert safe["tasks"]["todo"][1]["id"], "标题盖了也不能把 id 一起弄丢"


def test_context_reports_why_code_reality_is_missing(app_client):
    """读不到代码现状要说话：整节省掉等于把"没填路径"和"git 坏了"伪装成同一件事"""
    _, proj = app_client.create_project("没有代码路径")
    _, ctx = app_client.get(f"/api/context/{proj['id']}")
    cr = ctx["code_reality"]
    assert cr["available"] is False and cr["reason"] == "未填代码路径", cr

    pid = proj["id"]
    app_client.put(f"/api/projects/{pid}", {
        "name": "没有代码路径", "status": "进行中", "repo_path": "D:\\这个路径不存在"})
    _, ctx = app_client.get(f"/api/context/{pid}")
    cr = ctx["code_reality"]
    assert cr["available"] is False and "不存在" in cr["reason"], cr


# ---------------- 任务过程日志 ----------------

def test_task_journal_and_status_transition(server):
    status, proj = server.create_project("日志项目")
    pid = proj["id"]
    status, task = server.post(f"/api/projects/{pid}/tasks", {"title": "有日志的任务", "status": "待办"})
    tid = task["id"]
    # 手动追加
    status, entry = server.post(f"/api/tasks/{tid}/journal", {"text": "完成了接口设计评审"})
    assert status == 201 and entry["text"] == "完成了接口设计评审"
    # 状态变化自动记录
    server.put(f"/api/tasks/{tid}", {"title": "有日志的任务", "status": "进行中"})
    _, detail = server.get(f"/api/projects/{pid}")
    t = detail["tasks"][0]
    assert t["journal"] and t["journal"][-1]["text"] == "状态：待办 → 进行中"
    assert t["journal"][0]["text"] == "完成了接口设计评审"


def test_blocked_reason_lives_and_dies_with_the_blocked_status(server):
    """阻塞原因与验收时间戳是一对「只在该状态里存在」的字段（2026-10-19 第六轮竞品吸取）：
    进入阻塞记录、离开自动清（历史留在 journal 的状态流转条目里）；done_at 同理。
    不清的话，改回进行中的卡片仍会带着过时原因到处跑。"""
    _, proj = server.create_project("阻塞语义")
    pid = proj["id"]
    _, t = server.post(f"/api/projects/{pid}/tasks",
                       {"title": "卡住的活", "status": "阻塞", "blocked_reason": "  在等上游接口文档  "})
    assert t["blocked_reason"] == "在等上游接口文档", "首尾空白没清"
    assert "done_at" not in t
    tid = t["id"]

    # PATCH 改别的字段：原因保留（部分更新语义）
    _, t2 = server.patch(f"/api/tasks/{tid}", {"priority": "高"})
    assert t2["blocked_reason"] == "在等上游接口文档"

    # PATCH 换原因
    _, t3 = server.patch(f"/api/tasks/{tid}", {"blocked_reason": "换成在等部署窗口"})
    assert t3["blocked_reason"] == "换成在等部署窗口"

    # 离开阻塞：字段清掉，但「为什么卡过」留在 journal（交接材料）
    _, t4 = server.patch(f"/api/tasks/{tid}", {"status": "进行中"})
    assert "blocked_reason" not in t4
    assert "换成在等部署窗口" in " ".join(e["text"] for e in t4["journal"])

    # 进入阻塞但没给原因：允许（不拒绝），字段保持不存在
    _, t5 = server.patch(f"/api/tasks/{tid}", {"status": "阻塞"})
    assert "blocked_reason" not in t5

    # 阻塞中补原因
    _, t6 = server.patch(f"/api/tasks/{tid}", {"blocked_reason": "卡在证书"})
    assert t6["blocked_reason"] == "卡在证书"

    # 流转到已完成：原因随状态清、done_at 落上
    _, t7 = server.patch(f"/api/tasks/{tid}", {"status": "已完成"})
    assert "blocked_reason" not in t7
    assert t7["done_at"]

    # 反复保存已完成的其它字段：done_at 不被刷新（首次完成时间才是验收时刻）
    _, t8 = server.patch(f"/api/tasks/{tid}", {"priority": "中"})
    assert t8["done_at"] == t7["done_at"]

    # 重开再做完：done_at 换成第二次的完成时间
    server.patch(f"/api/tasks/{tid}", {"status": "进行中"})
    _, t9 = server.patch(f"/api/tasks/{tid}", {"status": "已完成"})
    assert t9["done_at"] >= t7["done_at"]

    # PUT 整体覆盖同样受这套语义管辖：编辑弹窗把状态改回去，无论带了什么都清
    _, t10 = server.put(f"/api/tasks/{tid}", {"title": "卡住的活", "status": "待办",
                                              "blocked_reason": "过时的原因"})
    assert "blocked_reason" not in t10 and "done_at" not in t10


def test_blocked_reason_travels_with_handoff_surfaces(app_client):
    """原因要跟着交接出口走：inbox（决定怎么办）、接手包 slim、交接包小节；
    `?redact=1` 时必须盖掉 —— 它是自由文本，和 desc/journal 同一档。"""
    import app.main as kanban
    _, p = app_client.create_project("卡点出口", status="进行中")
    pid = p["id"]
    app_client.post(f"/api/projects/{pid}/tasks",
                    {"title": "等接口文档", "status": "阻塞",
                     "blocked_reason": "在等上游 key=sk-live-abc1234567890 的接口文档"})

    _, inbox = app_client.get("/api/inbox")
    item = next(x for x in inbox["items"] if x["kind"] == "blocked")
    assert item["blocked_reason"] == "在等上游 key=sk-live-abc1234567890 的接口文档", \
        "Inbox 用途就是决定怎么办，原因就是决策材料"

    _, ctx = app_client.get(f"/api/context/{pid}")
    bt = next(t for t in ctx["tasks"]["blocked"] if t["title"] == "等接口文档")
    assert bt["blocked_reason"] == "在等上游 key=sk-live-abc1234567890 的接口文档"

    _, ctx_r = app_client.get(f"/api/context/{pid}?redact=1")
    btr = next(t for t in ctx_r["tasks"]["blocked"] if t["title"] == "等接口文档")
    assert "sk-live-abc" not in btr["blocked_reason"], "自由文本没走脱敏"
    assert kanban.MASK_MARK in btr["blocked_reason"]

    _, md = app_client.get(f"/api/export/{pid}")
    assert "阻塞中的任务（为什么卡着）" in md and "在等上游" in md, "交接包没带卡点原因"

    # 非阻塞任务不进这一节（字段随状态存在）
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "没卡的活", "status": "待办"})
    _, md2 = app_client.get(f"/api/export/{pid}")
    assert "没卡的活" in md2, "任务清单该照常带上它"
    block_sec = md2.split("阻塞中的任务（为什么卡着）")[1].split("###")[0]
    assert "没卡的活" not in block_sec


# ---------------- 项目记忆层 ----------------

def test_memories_crud_and_recall(server):
    status, proj = server.create_project("记忆项目")
    pid = proj["id"]
    s, m1 = server.post("/api/memories", {"pid": pid, "type": "坑", "text": "并发写必须加锁"})
    assert s == 201 and m1["ok"] is True
    # 去重：命中时返回的必须是**盘上已有那条**（它的 id 真实可删）。
    # 以前返回的是刚构造的新 entry —— id 是盘上不存在的，拿去 DELETE 会 404，
    # 调用方拿着一个假身份还以为记住了。
    s, m2 = server.post("/api/memories", {"pid": pid, "type": "坑", "text": "并发写必须加锁"})
    assert m2.get("deduped") is True
    assert m2["memory"]["id"] == m1["memory"]["id"], "去重返回了新造的假 id，不是盘上那条"
    # 列表与过滤
    _, lst = server.get("/api/memories?pid=" + pid)
    assert len(lst["memories"]) == 1
    _, lst2 = server.get("/api/memories?q=加锁")
    assert len(lst2["memories"]) == 1
    # 召回进 AI brief
    _, brief = server.get("/api/projects")
    s, _ = server.get(f"/api/context/{pid}")
    assert s == 200
    # 删除
    assert server.delete("/api/memories/" + m1["memory"]["id"])[0] == 200
    _, lst3 = server.get("/api/memories?pid=" + pid)
    assert lst3["memories"] == []


# ---------------- 会话捕获 ----------------

def test_session_scan_and_extract(server, tmp_path):
    # 造一个假的 Claude Code 会话目录
    src = tmp_path / "claude-projects" / "demo-proj"
    src.mkdir(parents=True)
    jsonl = src / "abc123.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "帮我修复登录超时"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "已定位：token 校验逻辑缺少刷新"}}),
    ]
    jsonl.write_text("\n".join(lines), encoding="utf-8")
    # 用环境变量把源指过去
    import os
    from app import llm
    cfg = llm.load_config()
    cfg["ai"]["session_sources"] = [{"name": "测试源", "path": str(tmp_path / "claude-projects"), "type": "claude_jsonl"}]
    llm.save_config(cfg)
    try:
        status, r = server.get("/api/sessions/scan")
        assert status == 200 and len(r["sessions"]) >= 1
        assert r["sessions"][0]["source"] == "测试源"
        from urllib.parse import quote
        status, pv = server.get("/api/sessions/preview?path=" + quote(str(jsonl)))
        assert status == 200 and "登录超时" in pv["text"]
        # ingest 走真实提炼（stub 端点场景外，这里只验证结构校验）
        status, r2 = server.post("/api/sessions/ingest", {"path": str(jsonl)})
        assert status == 400, "缺 pid 应被拒"
    finally:
        cfg["ai"]["session_sources"] = []
        llm.save_config(cfg)


# ---------------- 用量追踪 ----------------

def test_usage_endpoint(app_client):
    status, u = app_client.get("/api/usage")
    assert status == 200
    assert "totals" in u and "by_feature" in u and "by_model" in u
    assert u["totals"]["calls"] >= 0


# ---------------- MCP 只读开关 ----------------

def test_config_accepts_mcp_readonly(app_client):
    status, cfg = app_client.put("/api/ai/config", {"mcp_readonly": True})
    assert status == 200
    assert cfg["ai"]["mcp_readonly"] is True
    status, cfg2 = app_client.put("/api/ai/config", {"mcp_readonly": False})
    assert cfg2["ai"]["mcp_readonly"] is False


def test_today_lists_only_unblocked_tasks(server):
    """跨项目"现在就能动手"：前置没完成的不该出现"""
    _, p = server.create_project("今日项目", status="进行中")
    _, t1 = server.post(f"/api/projects/{p['id']}/tasks", {"title": "前置任务", "priority": "高"})
    _, _t2 = server.post(f"/api/projects/{p['id']}/tasks", {"title": "被阻塞任务", "priority": "高",
                                                           "depends_on": [t1["id"]]})
    server.post(f"/api/projects/{p['id']}/tasks", {"title": "已完成的任务", "status": "已完成"})

    status, body = server.get("/api/today")
    assert status == 200
    titles = [t["title"] for t in body["tasks"]]
    assert "前置任务" in titles
    assert "被阻塞任务" not in titles, "前置没完成，不该出现在今日待办"
    assert "已完成的任务" not in titles
    assert body["project_count"] == 1


def test_today_skips_finished_projects(server):
    _, p = server.create_project("已完结项目", status="已完成")
    server.post(f"/api/projects/{p['id']}/tasks", {"title": "收尾任务"})
    _, body = server.get("/api/today")
    assert all(t["project_name"] != "已完结项目" for t in body["tasks"])


def test_today_skips_projects_with_nothing_actionable(server):
    """只剩已完成任务的项目、空项目：整条跳过，不占 project_count 也不报错"""
    _, spent = server.create_project("只剩已完成任务", status="进行中")
    server.post(f"/api/projects/{spent['id']}/tasks", {"title": "已经做完的", "status": "已完成"})
    server.create_project("空项目", status="规划中")
    _, live = server.create_project("有活干", status="进行中")
    server.post(f"/api/projects/{live['id']}/tasks", {"title": "唯一可做的"})

    _, body = server.get("/api/today")
    assert [t["title"] for t in body["tasks"]] == ["唯一可做的"]
    assert body["project_count"] == 1
    assert body["total"] == 1


def test_handoff_links_follow_the_same_redact_choice(app_client):
    """`?redact=1` 的交接包，正文里那三条实时直链必须同样带 `?redact=1`。

    正文盖住了、链接却指向原文版，等于递出一份"已脱敏"的交接物、同时递三条取原文的路。
    """
    secret = "sk-handoffabcdef123456"
    _, proj = app_client.create_project("直链口径")
    pid = proj["id"]
    app_client.put(f"/api/projects/{pid}", {
        "name": "直链口径", "status": "进行中", "background": f"网关密钥 {secret}"})

    _, plain = app_client.get(f"/api/export/{pid}")
    assert secret in plain and "?redact=1" not in plain, "默认口径不该被改动"

    _, safe = app_client.get(f"/api/export/{pid}?redact=1")
    assert secret not in safe, "正文要盖"
    for path in (f"/api/context/{pid}", f"/api/export/{pid}", f"/api/export/page/{pid}"):
        assert path + "?redact=1" in safe, f"直链 {path} 没跟上脱敏口径"

    # 全项目包同样：它是逐个项目渲染的，漏一个项目就是漏一条
    _, all_pkg = app_client.get("/api/export-all?redact=1")
    assert f"/api/context/{pid}?redact=1" in all_pkg


def test_handoff_page_links_follow_the_same_redact_choice(app_client):
    """接手页是"发给别人看"的那一份，它的直链更要跟正文同一口径"""
    _, proj = app_client.create_project("接手页直链")
    pid = proj["id"]
    _, plain = app_client.get(f"/api/export/page/{pid}")
    assert f"/api/context/{pid}" in plain and "?redact=1" not in plain
    _, safe = app_client.get(f"/api/export/page/{pid}?redact=1")
    assert f"/api/context/{pid}?redact=1" in safe
    assert f"/api/export/page/{pid}?redact=1" in safe


def test_long_client_refs_do_not_collide(app_client):
    """幂等键以前静默截到 80 字符：两条前 80 相同的请求会撞成同一个键，
    第二次直接拿回**别人那条**的 id —— 静默返回错的东西比报错严重。"""
    ref_a = "R" * 80 + "-first-request"
    ref_b = "R" * 80 + "-SECOND-request"
    _, p1 = app_client.request("POST", "/api/projects?client_ref=" + ref_a, {"name": "长键甲"})
    _, p2 = app_client.request("POST", "/api/projects?client_ref=" + ref_b, {"name": "长键乙"})
    assert p1["id"] != p2["id"], "两条不同请求撞成了同一个键"
    assert p2["name"] == "长键乙", "撞车时会返回第一条的名字"
    assert "client_ref_replayed" not in p1, "首次创建不该带标记"

    _, again = app_client.request("POST", "/api/projects?client_ref=" + ref_a, {"name": "重试甲"})
    assert again["id"] == p1["id"] and again["client_ref_replayed"] is True, "重放要能被认出来"
    _, stored = app_client.get(f"/api/projects/{p1['id']}")
    assert "client_ref_replayed" not in stored, "标记只能出现在响应里，不能落盘"


def test_apply_suggestions_reports_every_item(app_client):
    """一批建议里 5 条只落地 1 条时，光给 `applied=1` 等于没说**是哪四条、为什么**。

    逐条回执顺带补上以前静默跳过的两类：字段不在清单、项目不存在。
    """
    _, p = app_client.create_project("建议回执")
    pid = p["id"]
    s, body = app_client.post("/api/ai/apply-suggestions", [
        {"project_id": pid, "field": "next_action", "value": "正常一条"},
        {"project_id": pid, "field": "name", "value": "清单外字段"},
        {"project_id": "不存在的项目", "field": "next_action", "value": "没这条项目"},
        {"project_id": pid, "field": "background", "value": "带着 ******** 的抄回来"},
        {"project_id": pid, "field": "handoff_note", "value": "长" * 2001},
    ])
    assert s == 200, body
    assert body["applied"] == 1 and body["skipped_masked"] == 1 and body["skipped_other"] == 3
    reasons = {r["index"]: r.get("reason", "") for r in body["results"]}
    assert len(body["results"]) == 5
    assert "清单" in reasons[1] and "项目不存在" in reasons[2]
    assert "掩码" in reasons[3] and "过长" in reasons[4], reasons
    _, back = app_client.get(f"/api/projects/{pid}")
    assert back["next_action"] == "正常一条"
    assert back["name"] == "建议回执", "清单外字段不能被动过"
    assert not back.get("handoff_note"), "超长的值不该落盘"


def test_apply_suggestions_dry_run_writes_nothing(app_client):
    """`?dry_run=1`：先看"会改成什么"，一个字节都不写"""
    _, p = app_client.create_project("试运行")
    pid = p["id"]
    s, body = app_client.request("POST", "/api/ai/apply-suggestions?dry_run=1",
                                 [{"project_id": pid, "field": "next_action",
                                   "value": "会改成这个"}])
    assert s == 200 and body["applied"] == 1 and body["dry_run"] is True
    assert body["results"][0]["would_write"] is False
    _, back = app_client.get(f"/api/projects/{pid}")
    assert back["next_action"] == "", "试运行连 updated_at 都不该推进"
    s, body = app_client.post("/api/ai/apply-suggestions",
                              [{"project_id": pid, "field": "next_action", "value": "会改成这个"}])
    assert body["results"][0]["would_write"] is True
    _, back = app_client.get(f"/api/projects/{pid}")
    assert back["next_action"] == "会改成这个"


def test_machine_readable_packages_declare_schema_version(app_client):
    """接手包与换机导出文件都要声明**结构版本**：那是"字段形状是哪一版"，
    与发布号 APP_VERSION 不是一回事。导入更新的导出文件时只说话、不挡人。"""
    import app.main as kanban
    _, p = app_client.create_project("契约版本")
    pid = p["id"]
    _, ctx = app_client.get(f"/api/context/{pid}")
    assert ctx["schema_version"] == kanban.SCHEMA_VERSION
    _, doc = app_client.get("/api/data/export")
    assert doc["schema_version"] == kanban.SCHEMA_VERSION

    future = {"app": "ai-kanban", "schema_version": kanban.SCHEMA_VERSION + 1,
              "projects": [{"id": "x1", "name": "未来的项目", "status": "进行中", "tasks": []}]}
    s, res = app_client.post("/api/data/import",
                             {"content": json.dumps(future, ensure_ascii=False)})
    assert s == 200 and res["imported"] == 1, "不能因为版本号更高就把导入挡在门外"
    assert "结构" in res.get("schema_note", ""), res


def test_context_package_field_set_is_pinned(app_client):
    """接手包是给别的程序读的：字段集合变了必须是有意为之，不能顺手。
    v2（2026-10-06，对标 Backlog.md/OKM 后新增）：任务 slim 带 `acceptance`（验收标准）、
    顶层带 `expired_memory_count`（过期记忆省略必须留痕）。
    v3（2026-10-19）：`start_here` 条目带 `status`，阻塞中的带 `blocked_reason` ——
    实测"可做"列表不带标记时，代理把阻塞任务当普通待办直接开工。
    v4（2026-10-21）：顶层带 `slice`（本次给了哪些小节、since 增量计数与说明）——
    `sections` / `since` 让代理省上下文，`project` 始终返回。"""
    import app.main as kanban
    _, p = app_client.create_project("字段快照")
    _, ctx = app_client.get(f"/api/context/{p['id']}")
    assert set(ctx) == {"app", "schema_version", "generated_at", "usage_hint", "project",
                        "memories", "expired_memory_count", "code_reality", "start_here",
                        "tasks", "readiness", "slice"}
    assert set(ctx["project"]) == {"id", "name", "status", "progress", "priority", "tags",
                                   "repo_path", "background", "handoff_note", "next_action"}
    assert set(ctx["tasks"]) == {"todo", "in_progress", "review", "blocked", "done_count"}
    assert set(ctx["slice"]) == {"sections", "omitted", "since", "delta", "note"}
    assert ctx["schema_version"] == kanban.SCHEMA_VERSION

    app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "快照任务"})
    _, ctx = app_client.get(f"/api/context/{p['id']}")
    assert set(ctx["start_here"][0]) == {"id", "title", "priority", "refs", "acceptance",
                                         "status"}, "start_here 条目形状变了？改 v3 快照要同步注释"


def test_handoff_sections_can_be_omitted_over_http(app_client):
    """`_budget_note` 一直说"本包偏长，建议裁剪"，但以前没有任何一条路能真的裁 ——
    光有警告没有解法。现在 `?omit_sections=` 能裁，且裁了什么必须在正文里留痕。"""
    _, p = app_client.create_project("裁剪小节", background="背景正文")
    pid = p["id"]
    app_client.post(f"/api/projects/{pid}/tasks",
                    {"title": "做完的", "status": "已完成", "refs": ["a.py:1"]})
    app_client.post(f"/api/projects/{pid}/tasks", {"title": "还没做的", "status": "待办"})
    app_client.post("/api/memories", {"pid": pid, "type": "坑", "text": "记忆正文"})

    _, full = app_client.get(f"/api/export/{pid}")
    assert "做完的" in full and "记忆正文" in full and "a.py:1" in full

    _, cut = app_client.get(f"/api/export/{pid}?omit_sections=done,memories,refs")
    assert "做完的" not in cut and "还没做的" in cut, "该裁的没裁、不该裁的裁了"
    assert "记忆正文" not in cut and "a.py:1" not in cut
    assert "省略" in cut and "已完成任务" in cut, "裁掉的东西要在开头留痕，否则接手方会读成「没有」"

    s, body = app_client.get(f"/api/export/{pid}?omit_sections=背景")
    assert s == 400 and "omit_sections" in str(body), "不认识的名字要报错，不能静默整包发出去"

    _, all_full = app_client.get("/api/export-all")
    _, all_cut = app_client.get("/api/export-all?omit_sections=memories")
    assert "记忆正文" in all_full and "记忆正文" not in all_cut
    assert all_cut.count("按调用方请求省略") == 1, "每个项目都要各自留痕"


def test_incremental_ops_reject_fields_the_object_does_not_have(app_client):
    """`refs_add` 用在项目上、`tags_add` 用在任务上，都是调用方的错，
    必须明确报错 —— 静默收下就是"看起来成功了，其实什么都没落"。"""
    import app.main as kanban
    _, p = app_client.create_project("增量守卫")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "甲"})

    with pytest.raises(kanban.HTTPException) as e:
        kanban.patch_project(p["id"], {"refs_add": ["a.py:1"]})
    assert e.value.status_code == 400 and "refs" in str(e.value.detail)

    with pytest.raises(kanban.HTTPException) as e:
        kanban.patch_task(t["id"], {"tags_add": ["x"]})
    assert e.value.status_code == 400 and "tags" in str(e.value.detail)

    # 空白项与不认识的名字：一个该被跳过，一个必须报错，都不能静默整包发出去
    assert kanban.parse_handoff_sections("git, ,journal", param="omit_sections") == {"git", "journal"}
    with pytest.raises(kanban.HTTPException):
        kanban.parse_handoff_sections("git,背景", param="omit_sections")


def test_graph_endpoint_reports_empty_reason_and_escapes(app_client):
    """依赖图端点是给应用内渲染用的（SVG 走 innerHTML），所以两件事必须钉住：
    ①"没有依赖"与"读不到"要能分清；② 任务标题里的 HTML 不能原样进 SVG。"""
    _, empty = app_client.create_project("图-空项目")
    _, r = app_client.get(f"/api/projects/{empty['id']}/graph")
    assert r["empty"] is True and r["reason"] == "还没有任务", r

    _, notask = app_client.create_project("图-无依赖")
    app_client.post(f"/api/projects/{notask['id']}/tasks", {"title": "各干各的"})
    _, r = app_client.get(f"/api/projects/{notask['id']}/graph")
    assert r["empty"] is True and "前置依赖" in r["reason"], r

    _, p = app_client.create_project("图-有依赖")
    _, a = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "前置"})
    _, b = app_client.post(f"/api/projects/{p['id']}/tasks",
                           {"title": "后置", "depends_on": [a["id"]]})
    _, r = app_client.get(f"/api/projects/{p['id']}/graph")
    assert r["empty"] is False and r["nodes"] == 2 and r["shown"] == 2
    assert r["truncated"] is False and r["svg"].lstrip().startswith("<svg")
    assert "url(#a)" in r["svg"], "依赖连线要有箭头，否则看不出方向"

    # 浅色主题要真的换色（同一份实现服务两种主题，不复制第二份）
    _, dark = app_client.get(f"/api/projects/{p['id']}/graph")
    _, light = app_client.get(f"/api/projects/{p['id']}/graph?theme=light")
    assert "#1E1E22" in dark["svg"] and "#1E1E22" not in light["svg"]
    assert "#FFFFFF" in light["svg"]

    # 标题里的 HTML 必须被转义：这张图会被前端塞进 innerHTML
    _, c = app_client.post(f"/api/projects/{p['id']}/tasks",
                           {"title": '<img src=x onerror=alert(1)>', "depends_on": [b["id"]]})
    _, r = app_client.get(f"/api/projects/{p['id']}/graph")
    assert "<img src=x" not in r["svg"] and "&lt;img" in r["svg"], r["svg"][:200]

    assert app_client.get("/api/projects/nope-xyz/graph")[0] == 404


def test_graph_reports_when_layers_get_collided(app_client):
    """超过 4 层的节点被并进末列，图上那两条看着像并行、其实有前后关系 ——
    不标注就是图在撒谎，所以要报 `collapsed`。"""
    _, p = app_client.create_project("图-深链")
    prev = None
    for i in range(6):
        body = {"title": f"第{i + 1}步", "status": "待办"}
        if prev:
            body["depends_on"] = [prev]
        _, t = app_client.post(f"/api/projects/{p['id']}/tasks", body)
        prev = t["id"]
    _, r = app_client.get(f"/api/projects/{p['id']}/graph")
    assert r["empty"] is False and r["collapsed"] is True, r

    _, s = app_client.create_project("图-浅链")
    _, a = app_client.post(f"/api/projects/{s['id']}/tasks", {"title": "甲", "status": "进行中"})
    app_client.post(f"/api/projects/{s['id']}/tasks", {"title": "乙", "depends_on": [a["id"]]})
    _, r = app_client.get(f"/api/projects/{s['id']}/graph")
    assert r["collapsed"] is False, "两层不该报折叠，否则这句提示会一直亮着变成噪声"


def test_task_can_be_looked_up_by_id_alone(app_client):
    """`GET /api/tasks/{tid}` 以前不存在：只知道任务 id 时反查不到所属项目
    （列表接口不含任务），于是明明手里有 id 却报"找不到对应任务"。"""
    _, p = app_client.create_project("按id找任务")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "唯一的那条"})
    s, r = app_client.get("/api/tasks/" + t["id"])
    assert s == 200 and r["task"]["title"] == "唯一的那条"
    assert r["project_id"] == p["id"] and r["project_name"] == "按id找任务"
    assert app_client.get("/api/tasks/nope-tid")[0] == 404


# ---------------- PATCH：只改点名的字段 ----------------

def test_patch_task_keeps_fields_it_was_not_given(app_client):
    """`PUT` 要求调用方先读一份再整份写回，而那次读在临界区**外**。

    实盘复现过的形状：详情页开着 → 智能体经 MCP 给任务补了 refs/desc →
    用户把卡片拖一列（前端当时发的是整份 PUT）→ 只提示"已移至进行中 ✓"，
    对方刚写的 refs/desc 被旧快照抹掉，journal 里只留下"状态：待办→进行中"，
    事后从痕迹上看不出丢过东西。PATCH 把补全挪进锁内，改一个字段就只动一个字段。
    """
    _, p = app_client.create_project("拖拽会抹字段的项目")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "会被波及的任务"})
    tid = t["id"]
    app_client.patch(f"/api/tasks/{tid}", {"refs": ["D:/x/a.py"], "desc": "智能体刚补的上下文"})

    st, after = app_client.patch(f"/api/tasks/{tid}", {"status": "进行中"})
    assert st == 200, after
    assert after["status"] == "进行中"
    assert after["refs"] == ["D:/x/a.py"], f"点名字段之外的东西被抹掉了：{after}"
    assert after["desc"] == "智能体刚补的上下文"
    assert after["title"] == "会被波及的任务"


def test_patch_project_keeps_fields_it_was_not_given(app_client):
    """同一个道理在项目侧：改 progress 不该把别人写的交接说明清成空串。"""
    _, p = app_client.create_project("改进度会牵连别的项目")
    pid = p["id"]
    app_client.put(f"/api/projects/{pid}", {**app_client.get(f"/api/projects/{pid}")[1],
                                           "handoff_note": "必须活下来的一段话"})
    st, after = app_client.patch(f"/api/projects/{pid}", {"progress": 77})
    assert st == 200, after
    assert after["progress"] == 77
    assert after["handoff_note"] == "必须活下来的一段话", after


def test_collection_ops_work_over_http_too(app_client):
    """`tags_add` / `refs_remove` 这些增量操作以前**只有 MCP 能用**，
    HTTP 侧只能整份覆盖 —— 同一个能力两条路两套口径，前端就永远拿不到"只加一条"。
    现在两条路共用 `patch_*`，所以 HTTP 上也得能用，且行为一致。
    """
    _, p = app_client.create_project("增量操作", tags=["全栈"])
    pid = p["id"]
    st, after = app_client.patch(f"/api/projects/{pid}", {"tags_add": ["待复核"]})
    assert st == 200 and set(after["tags"]) == {"全栈", "待复核"}, after
    st, after = app_client.patch(f"/api/projects/{pid}", {"tags_remove": ["全栈"]})
    assert st == 200 and after["tags"] == ["待复核"], after
    # 用在没有这个字段的结构上必须报错，而不是静默成功
    assert app_client.patch(f"/api/projects/{pid}", {"nonsense_add": ["x"]})[0] == 400


def test_empty_patch_is_rejected(app_client):
    """空 PATCH 什么都不点名 —— 那不是"部分更新"，是调用方写错了。

    放它过去就等于给"整份读出来再原样写回"留了个入口，而那正是 PATCH 要消灭的形状。
    """
    _, p = app_client.create_project("空 PATCH")
    st, body = app_client.patch(f"/api/projects/{p['id']}", {})
    assert st == 400, (st, body)
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "一条"})
    assert app_client.patch(f"/api/tasks/{t['id']}", {})[0] == 400


# ---------------- 批次 F：三条"声明与执行不一致"的收口 ----------------

def test_search_reports_truncation_instead_of_making_the_caller_guess(app_client):
    """命中到上限与"总共就这么些"必须分开说。

    以前响应里只有 `results`：代理拿 30 条就以为全板只有 30 条匹配，
    第 31 条在它的认知里不存在 —— 而 MCP 那句"已到上限 50"又是按另一个数字猜的。
    """
    _, p = app_client.create_project("搜索截断")
    for i in range(6):
        app_client.post(f"/api/projects/{p['id']}/tasks", {"title": f"联调任务{i}", "desc": "都含同一个词"})
    st, body = app_client.get("/api/search/tasks?q=联调&limit=3")
    assert st == 200 and len(body["results"]) == 3
    assert body["truncated"] is True and body["limit"] == 3, body
    st2, all_of_them = app_client.get("/api/search/tasks?q=联调&limit=50")
    assert all_of_them["truncated"] is False and len(all_of_them["results"]) == 6, all_of_them
    # 空查询也要带同一组键：调用方不该为"有没有搜到"写两套分支
    _, empty = app_client.get("/api/search/tasks?q=")
    assert empty["results"] == [] and empty["truncated"] is False


def test_local_url_follows_the_serving_port(monkeypatch):
    """交接包直链端口必须来自 SERVICE_PORT，不许写死（2026-09-29 补）。

    变异 live_url_wrong_port（端口写死 18321）曾存活：test_handoff_page.py
    钉的是默认端口 18320 下的字面量，写死成另一个常量后默认部署恰好等值。
    这里在**非默认端口**下断言 _local_url 的输出 —— 读环境与写死常量的分水岭。
    """
    import app.main as kanban

    monkeypatch.setenv("KANBAN_PORT", "18999")
    monkeypatch.setattr(kanban, "SERVICE_PORT", "18999")
    url = kanban._local_url("/api/context/p1")
    assert url == "http://127.0.0.1:18999/api/context/p1", url
    assert not url.startswith("http://127.0.0.1:18321"), "写死的历史缺陷端口"


def test_search_limit_is_clamped_to_the_ceiling(app_client):
    """limit 超上限必须被夹回 50（2026-09-29 补）。

    变异 search_limit_ceiling_raised（50→5000）曾存活：既有用例只试了
    limit=3（截断）与 limit=50（恰好全量），没人验"更大的数也会被夹回封顶"。
    上限是前端 Ctrl+K 与 MCP schema 共同声明的封顶口径 —— 接口自己先松掉，
    一次请求就能把整份看板拖出去。回执里的 limit 必须报**实际生效**的那个数。
    """
    _, p = app_client.create_project("搜索封顶")
    for i in range(3):
        app_client.post(f"/api/projects/{p['id']}/tasks", {"title": f"压测词{i}", "desc": "封顶取样"})
    st, body = app_client.get("/api/search/tasks?q=封顶&limit=5000")
    assert st == 200 and body["limit"] == 50, body
    assert body["truncated"] is False and len(body["results"]) == 3, body
    st2, zero = app_client.get("/api/search/tasks?q=封顶&limit=0")
    assert st2 == 200 and zero["limit"] == 1, zero


def test_memory_on_a_project_that_does_not_exist_is_refused(app_client):
    """回执说"已记下 [决策] 到 <pid>"，而那条记忆永远进不了交接包 —— 就是撒谎。

    交接包/接手包都按 pid 过滤，挂在不存在项目上的记忆谁也读不到，
    只有 `delete_project` 会把它当孤儿清掉。留空 pid 是另一回事（通用记忆）。
    """
    st, body = app_client.post("/api/memories", {"pid": "根本没有这个项目", "text": "一条决策"})
    assert st == 400, (st, body)
    assert "项目不存在" in str(body), body
    _, listed = app_client.get("/api/memories")
    assert not any(m.get("text") == "一条决策" for m in listed["memories"])

    st2, gen = app_client.post("/api/memories", {"pid": "", "text": "跨项目的通用记忆"})
    assert st2 == 201, (st2, gen)      # 留空 = 通用记忆，是有意支持的另一档


def test_journal_limit_is_one_number_everywhere(app_client):
    """过程记录的上限以前有三个数：入参收 2000、落盘截到 500、MCP schema 写 2000。

    于是智能体写一条 1200 字的记录，落盘只剩 500，回执还说"已记录"——
    少掉的那 700 字（往往正是"为什么这么决定"）没人知道。
    现在只有一个数：`MAX_JOURNAL_TEXT`，超限在边界就被挡。
    """
    import app.main as kanban

    declared = [m.max_length for m in kanban.JournalIn.model_fields["text"].metadata
                if hasattr(m, "max_length")]
    assert declared == [kanban.MAX_JOURNAL_TEXT], (
        f"声明的上限 {declared} 与真正落盘的 {kanban.MAX_JOURNAL_TEXT} 不是同一个数")

    _, p = app_client.create_project("过程记录上限")
    _, t = app_client.post(f"/api/projects/{p['id']}/tasks", {"title": "一条任务"})
    ok_text = "决" * kanban.MAX_JOURNAL_TEXT
    st, _ = app_client.post(f"/api/tasks/{t['id']}/journal", {"text": ok_text})
    assert st == 201, st
    st2, body2 = app_client.post(f"/api/tasks/{t['id']}/journal", {"text": ok_text + "多一个字"})
    assert st2 == 422, (st2, body2)       # 超一个也不行：静默截断就是这一条要消灭的东西
    _, detail = app_client.get("/api/tasks/" + t["id"])
    assert len(detail["task"]["journal"]) == 1, "被拒的那次不该留下半条记录"
    assert len(detail["task"]["journal"][0]["text"]) == kanban.MAX_JOURNAL_TEXT


def test_bulk_delete_projects_is_atomic_and_backs_up(app_client):
    _, p1 = app_client.create_project("批量删除甲")
    _, p2 = app_client.create_project("批量删除乙")
    assert app_client.post("/api/memories", {"pid": p1["id"], "text": "会被一起清掉的记忆"})[0] == 201

    status, body = app_client.post(
        "/api/projects/bulk-delete",
        {"ids": [p1["id"], p2["id"]], "confirm": True},
    )
    assert status == 200, body
    assert set(body["deleted"]) == {p1["id"], p2["id"]}
    assert body["backup"] and Path(body["backup"]).exists(), "删除前没有留下快照"

    _, data = app_client.get("/api/projects")
    assert not any(p["id"] in {p1["id"], p2["id"]} for p in data["projects"])
    _, memories = app_client.get("/api/memories")
    assert not any(m["pid"] == p1["id"] for m in memories["memories"]), "孤儿记忆没清掉"


def test_bulk_delete_refuses_missing_id_without_deleting(app_client):
    _, p = app_client.create_project("批量删除保留项")
    status, body = app_client.post(
        "/api/projects/bulk-delete",
        {"ids": [p["id"], "missing-id"], "confirm": True},
    )
    assert status == 404, body
    _, data = app_client.get("/api/projects")
    assert any(x["id"] == p["id"] for x in data["projects"]), "部分 id 不存在时仍然删了项目"


# ---------------- 2026-10-22 用户视角收口：详情就绪度 / 今日列表 ----------------

def test_project_detail_readiness_tracks_task_changes(server):
    """详情页改完任务，就绪度必须跟着这一份详情刷新。

    以前就绪度只从 /api/stats 的全局快照取，而详情页保存任务后只重取单项目；
    结果任务卡已经在下面了，左侧还写着「已有任务清单 ✗ / 任务有推进 ✗」，
    要重新进项目才恢复 —— 用户会以为任务没存上。
    """
    _, p = server.create_project("就绪度刷新项目", background="背景",
                                 handoff_note="交接说明", next_action="下一步")
    _, detail = server.get(f"/api/projects/{p['id']}")
    assert detail["readiness"]["score"] == 75
    assert {it["key"] for it in detail["readiness"]["items"]} == {
        "handoff", "next", "bg", "tasks", "active"}

    _, t = server.post(f"/api/projects/{p['id']}/tasks",
                       {"title": "进行中的活", "status": "进行中"})
    _, detail = server.get(f"/api/projects/{p['id']}")
    assert detail["readiness"]["score"] == 100

    server.patch(f"/api/tasks/{t['id']}", {"status": "待办"})
    _, detail = server.get(f"/api/projects/{p['id']}")
    assert detail["readiness"]["score"] == 90, "任务改回待办后「任务有推进」应当立刻灭掉"
    active = next(it for it in detail["readiness"]["items"] if it["key"] == "active")
    assert active["ok"] is False


def test_today_items_carry_task_status_and_blocked_reason(server):
    """「现在能做什么」列表里，状态说的是**任务**状态，不是项目状态；
    阻塞任务还要带上原因，否则人会把它当成普通待办直接开工。"""
    _, p = server.create_project("今日状态项目", status="进行中")
    server.post(f"/api/projects/{p['id']}/tasks",
                {"title": "今天要做的事", "status": "待办"})
    server.post(f"/api/projects/{p['id']}/tasks",
                {"title": "今天卡住的事", "status": "阻塞",
                 "blocked_reason": "在等接口文档"})

    _, body = server.get("/api/today")
    rows = {t["title"]: t for t in body["tasks"]}
    assert rows["今天要做的事"]["status"] == "待办"
    assert rows["今天要做的事"]["blocked_reason"] == ""
    assert rows["今天卡住的事"]["status"] == "阻塞"
    assert rows["今天卡住的事"]["blocked_reason"] == "在等接口文档"
