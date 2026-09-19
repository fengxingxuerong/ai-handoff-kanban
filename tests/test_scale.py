# -*- coding: utf-8 -*-
"""规模钉子：数据一多就必须仍然成立的性质（顺序、次数、以及"别退化成平方"）

以前这套件只在小数据上断言过语义（`topo_order` / `executable_tasks` / `/api/today`
全是个位数任务的用例），于是"逐个项目多读一次盘""每个任务重建一次 id 集合"
这类**只在规模下才暴露**的问题完全没有钉子。

复杂度钉子用**全量遍历的遍数**来判，不用耗时：`-n auto` 下同一段代码空载 0.33ms、
跑批时能超 4ms，耗时预算只会造出假红（2026-09-19 实测踩过）。遍数是确定的数，
旧实现的平方形状一眼可辨，实测值写在用例注释里备查。
"""
from __future__ import annotations

import json
import pytest


def _chain(n: int) -> list[dict]:
    """一条长依赖链：t0 ← t1 ← … ← t(n-1)，列表里按逆序放（最难排的一种）"""
    return [{"id": f"t{i}", "title": f"任务 {i}", "status": "待办", "priority": "中",
             "depends_on": [f"t{i - 1}"] if i else []} for i in range(n)][::-1]


class _Counting(list):
    """数"整个列表被完整走了几遍"。

    复杂度钉子用**遍历次数**而不是耗时：耗时断言在 -n auto 的机器上会假红
    （实测同一份代码：空载 0.33ms、并行跑批时超过 4ms 预算），
    而"全量扫描的遍数不随规模增长"是可以在任何负载下判定的性质。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.passes = 0

    def __iter__(self):
        self.passes += 1
        return super().__iter__()


def test_topo_order_does_not_rescan_per_level():
    """1200 长的依赖链要排 1200 层：旧实现每层重扫一遍全列表（实测 252ms / 1200 次全量遍历）"""
    from app.main import topo_order

    tasks = _Counting(_chain(1200))
    out = topo_order(tasks)
    assert [t["id"] for t in out] == [f"t{i}" for i in range(1200)], "链必须整个排开"
    assert tasks.passes < 60, f"全量遍历了 {tasks.passes} 遍，层数越多扫得越勤 —— 又退回平方了"


def test_topo_order_keeps_cycle_fallback_and_original_order():
    """有环时"剩下的按原序补上"这条兜底不能因为换算法而变"""
    from app.main import topo_order

    tasks = [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]},
             {"id": "c", "depends_on": []}, {"id": "d", "depends_on": ["ghost"]}]
    assert [t["id"] for t in topo_order(tasks)] == ["c", "d", "a", "b"]


def test_executable_tasks_hoists_the_known_id_set():
    """旧实现在**每个依赖**上重建一次全量 id 集合：1000 任务 22ms、约 100 万次遍历元素"""
    from app.main import executable_tasks

    tasks = _Counting({"id": f"t{i}", "title": f"任务 {i}",
                       "status": "已完成" if i % 3 == 0 else "待办",
                       "priority": "中", "depends_on": [f"t{i - 1}"] if i else []}
                      for i in range(1000))
    ready = executable_tasks(tasks)
    assert [t["id"] for t in ready] == [f"t{i}" for i in range(1, 1000) if i % 3 == 1], \
        "可执行的应当是「自己没完成、前置正好完成」那一档"
    assert tasks.passes < 60, f"全量遍历了 {tasks.passes} 遍，id 集合又在按任务重建"


def test_executable_tasks_treats_ghost_dependency_as_satisfied():
    """前置不存在时按"不用等"处理（与 _sanitize 清洗后的行为一致，也保住旧语义）"""
    from app.main import executable_tasks

    tasks = [{"id": "a", "status": "待办", "depends_on": ["并不存在"]}]
    assert executable_tasks(tasks) == tasks


@pytest.fixture
def counting_memories(monkeypatch):
    """数 memories.json 被读了几次（读一次=整份文件重读，它是全量 JSON）"""
    import app.main as kanban

    calls = {"n": 0}
    real = kanban._load_memories

    def wrapped():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(kanban, "_load_memories", wrapped)
    return calls


def test_export_all_reads_memories_once_not_per_project(app_client, counting_memories, tmp_path):
    """批量导出必须一次读盘后分桶：读次数等于项目数就是白烧 IO

    实测 1000 项目 + 500 条记忆时 /api/export-all 从 139ms 涨到 911ms 全在这。
    """
    import app.main as kanban

    projects = [{"id": f"p{i}", "name": f"项目 {i}", "status": "进行中", "progress": 10,
                 "priority": "中", "tags": [], "tasks": []} for i in range(20)]
    (kanban.data_dir() / "projects.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
    (kanban.data_dir() / "memories.json").write_text(json.dumps(
        [{"pid": f"p{i % 20}", "type": "事实", "text": f"要点 {i}", "time": "2026-01-01 09:00"}
         for i in range(60)]), encoding="utf-8")

    counting_memories["n"] = 0
    status, body = app_client.get("/api/export-all")
    assert status == 200 and "项目 7" in body
    assert counting_memories["n"] <= 1, f"memories.json 被读了 {counting_memories['n']} 次（20 个项目）"


def test_llm_brief_reads_memories_once(app_client, counting_memories):
    """给模型的简报同理：逐项目取记忆时读次数也是项目数"""
    import app.main as kanban

    projects = [{"id": f"p{i}", "name": f"项目 {i}", "status": "进行中", "progress": 10,
                 "priority": "中", "tags": [], "tasks": []} for i in range(15)]
    counting_memories["n"] = 0
    kanban._projects_brief(projects)
    assert counting_memories["n"] == 1, f"读了 {counting_memories['n']} 次"


def test_duplicate_task_ids_are_split_apart(app_client):
    """重复 id 必须收口：否则 first-match 的一整套逻辑都开始说谎

    实测过的破坏：PUT 只改第一条（余份永远陈旧）、DELETE 一次删一条却连返 200、
    topo_order 输入 4 条只输出 3 条（交接包静默丢任务）。
    """
    import app.main as kanban

    dup = {"projects": [{"id": "px", "name": "重复 id 项目", "status": "进行中", "progress": 0,
                         "priority": "中", "tags": [],
                         "tasks": [
                             {"id": "t1", "title": "第一条", "status": "待办", "priority": "中", "depends_on": []},
                             {"id": "t1", "title": "第二条", "status": "待办", "priority": "中", "depends_on": []},
                             {"id": "t1", "title": "第三条", "status": "待办", "priority": "中", "depends_on": []},
                         ]}]}
    (kanban.data_dir() / "projects.json").write_text(json.dumps(dup), encoding="utf-8")

    _, proj = app_client.get("/api/projects/px")
    titles = [t["title"] for t in proj["tasks"]]
    ids = [t["id"] for t in proj["tasks"]]
    assert titles == ["第一条", "第二条", "第三条"], f"内容一条都不能丢：{titles}"
    assert len(set(ids)) == 3, f"重复 id 没收口：{ids}"
    assert ids[0] == "t1" and ids[1] == "t1~2" and ids[2] == "t1~3", \
        f"保留第一条、后来的确定性改名（不能每次读都换身份）：{ids}"
    # 清洗挂在每次读上：两次读必须给出同一批 id，否则界面拿旧 id 点进去就 404
    _, again = app_client.get("/api/projects/px")
    assert [t["id"] for t in again["tasks"]] == ids, f"两次读出来的 id 不一致：{ids} vs {again}"

    # 删一条只剩两条：以前三次 DELETE 会一起删光
    victim = proj["tasks"][1]["id"]
    assert app_client.delete(f"/api/tasks/{victim}")[0] == 200
    _, after = app_client.get("/api/projects/px")
    assert [t["title"] for t in after["tasks"]] == ["第一条", "第三条"]


def test_duplicate_project_ids_cannot_take_two_down_at_once(app_client):
    """同 id 的两个项目：DELETE 只能带走一个（以前一次删俩，另一个项目无声消失）"""
    import app.main as kanban

    two = {"projects": [
        {"id": "same", "name": "甲项目", "status": "进行中", "progress": 0, "priority": "中",
         "tags": [], "tasks": []},
        {"id": "same", "name": "乙项目", "status": "进行中", "progress": 0, "priority": "中",
         "tags": [], "tasks": []},
    ]}
    (kanban.data_dir() / "projects.json").write_text(json.dumps(two), encoding="utf-8")
    _, listing = app_client.get("/api/projects")
    names = sorted(p["name"] for p in listing["projects"])
    assert names == ["乙项目", "甲项目"], f"两个项目都该还在：{names}"
    pid = listing["projects"][0]["id"]
    other = [p["id"] for p in listing["projects"] if p["id"] != pid][0]
    app_client.delete(f"/api/projects/{pid}")
    _, left = app_client.get("/api/projects")
    assert len(left["projects"]) == 1 and left["projects"][0]["id"] == other


def test_today_reports_truncation(app_client):
    """任务多到装不进 limit 时必须说出来：以前只回 total，界面一条都不提"""
    projects = [{"id": "big", "name": "大项目", "status": "进行中", "progress": 0,
                 "priority": "中", "tags": [],
                 "tasks": [{"id": f"t{i}", "title": f"任务 {i}", "status": "待办",
                            "priority": "中", "depends_on": []} for i in range(12)]}]
    import app.main as kanban

    (kanban.data_dir() / "projects.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
    _, body = app_client.get("/api/today?limit=5")
    assert body["total"] == 12 and len(body["tasks"]) == 5
    assert body["truncated"] is True
    _, whole = app_client.get("/api/today?limit=50")
    assert whole["truncated"] is False, "没截断就别报"


def test_sanitize_normalizes_loose_due_dates(app_client):
    """`2026-9-5` 这种写法要归一：否则字符串排序下它排在 2026-08-01 之后，临期/逾期全看错"""
    import app.main as kanban

    p = {"projects": [{"id": "pd", "name": "日期项目", "status": "进行中", "progress": 0,
                       "priority": "中", "tags": [],
                       "tasks": [{"id": "a", "title": "松写法", "status": "待办", "priority": "中",
                                  "due_date": "2026-9-5", "depends_on": []},
                                 {"id": "b", "title": "另一种松写法", "status": "待办", "priority": "中",
                                  "due_date": "2026/09/07", "depends_on": []},
                                 {"id": "c", "title": "废值", "status": "待办", "priority": "中",
                                  "due_date": "下周三", "depends_on": []}]}]}
    (kanban.data_dir() / "projects.json").write_text(json.dumps(p), encoding="utf-8")
    _, proj = app_client.get("/api/projects/pd")
    dues = {t["title"]: t.get("due_date") for t in proj["tasks"]}
    assert dues["松写法"] == "2026-09-05", dues
    assert dues["另一种松写法"] == "2026-09-07", dues
    assert dues["废值"] == "", f"解析不出来的应当清空而不是留着：{dues}"


def test_llm_brief_says_when_it_drops_tasks(app_client):
    """给模型的简报只带前 40 条任务，这个截断必须写在数据里。

    不留痕的后果不是"少看几条"：模型会以为这项目的活就这些，于是监管报告
    漏掉第 41 条之后的阻塞，而用户完全看不出漏过。
    """
    import app.main as kanban

    many = [{"id": f"t{i}", "title": f"任务 {i}", "status": "待办", "priority": "中",
             "depends_on": []} for i in range(55)]
    brief = kanban._projects_brief([
        {"id": "p1", "name": "任务很多", "status": "进行中", "tasks": many},
        {"id": "p2", "name": "任务不多", "status": "进行中", "tasks": many[:10]},
    ])
    by_id = {b["id"]: b for b in brief}
    assert len(by_id["p1"]["tasks"]) == 40
    assert "55" in by_id["p1"]["tasks_note"] and "40" in by_id["p1"]["tasks_note"], by_id["p1"]
    assert "tasks_note" not in by_id["p2"], "没截断就别多塞一个键"
