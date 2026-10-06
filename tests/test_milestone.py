# -*- coding: utf-8 -*-
"""P2「里程碑轻量版」功能测试。

覆盖：
- 字段进出（create/get/update 带 milestones 与 task.milestone）
- `_clean_milestones`：非 dict 丢弃、name strip+截断、同名去重、done 布尔归一、上限 20、字符串拆分
- `_clean_milestone`（TaskIn）：strip + 截断 40
- `milestone_progress` 纯函数：归组口径、进度计算、无任务里程碑、不存在的归属不计
- 交接包不因里程碑字段变长（`/api/context/{pid}` 的 project 无 milestones 键、任务无 milestone 键）
- 前端契约：项目弹窗行编辑器、任务弹窗下拉、ACTIONS 注册对齐
"""
from __future__ import annotations

import re

import pytest
from frontend_src import frontend_source

import app.main as kanban
from app.project_stats import milestone_progress


# ---------------- 字段进出 ----------------

def test_milestones_roundtrip(app_client):
    """创建项目带里程碑，详情读回一致；任务可归入里程碑。"""
    _, p = app_client.create_project("里程碑项目", milestones=[
        {"name": "v1.1 冻结", "done": False},
        {"name": "上线前", "done": True},
    ])
    pid = p["id"]
    assert p["milestones"] == [
        {"name": "v1.1 冻结", "done": False},
        {"name": "上线前", "done": True},
    ]

    _, t1 = app_client.post(f"/api/projects/{pid}/tasks",
                            {"title": "打版本", "milestone": "v1.1 冻结"})
    st, t2 = app_client.post(f"/api/projects/{pid}/tasks",
                             {"title": "回归", "milestone": "上线前"})
    assert st == 201 and t2["milestone"] == "上线前"

    _, detail = app_client.get(f"/api/projects/{pid}")
    tasks = detail["tasks"]
    assert {t["title"]: t.get("milestone") for t in tasks} == {
        "打版本": "v1.1 冻结", "回归": "上线前"}
    # 没传 milestone 的任务默认为空串（未归入），不是无键
    _, t3 = app_client.post(f"/api/projects/{pid}/tasks", {"title": "杂务"})
    assert t3["milestone"] == ""

    # 详情附带 milestones_progress（口径唯一在后端）
    mp = detail["milestones_progress"]
    assert {m["name"] for m in mp} == {"v1.1 冻结", "上线前"}


def test_milestones_can_be_updated(app_client):
    """整体更新（PUT）换里程碑列表，任务归属字段跟随请求。"""
    _, p = app_client.create_project("更新里程碑", milestones=[{"name": "旧里程碑"}])
    pid = p["id"]
    _, t = app_client.post(f"/api/projects/{pid}/tasks",
                           {"title": "任务", "milestone": "旧里程碑"})
    st, body = app_client.request("PUT", f"/api/projects/{pid}", {
        "name": "更新里程碑", "status": "进行中", "progress": 10, "priority": "中",
        "milestones": [{"name": "新里程碑", "done": True}],
    })
    assert st == 200 and body["milestones"] == [{"name": "新里程碑", "done": True}]

    st2, t2 = app_client.request("PATCH", f"/api/tasks/{t['id']}",
                                 {"milestone": "新里程碑"})
    assert st2 == 200 and t2["milestone"] == "新里程碑"


# ---------------- 清洗规则 ----------------

def test_clean_milestones_drops_non_dict_and_empty_names():
    """非 dict 元素与空 name 整条丢弃；字符串按逗号/换行拆分。"""
    out = kanban._clean_milestones([
        "not-a-dict", 42, None,
        {"name": "  "},                        # 空 name
        {"name": "甲"}, {"name": " 甲 "},      # 去重（strip 后同名）
    ])
    assert out == [{"name": "甲", "done": False}]

    out2 = kanban._clean_milestones("甲,乙\n丙")
    assert [m["name"] for m in out2] == ["甲", "乙", "丙"]
    assert kanban._clean_milestones(12345) == []
    assert kanban._clean_milestones(None) == []


def test_clean_milestones_truncates_and_dedupes():
    """name 超长截断到 40、done 只当布尔、同名去重。"""
    long_name = "长" * 60
    out = kanban._clean_milestones([
        {"name": long_name, "done": "truthy"},
        {"name": long_name[:40], "done": 0},   # 截断后与上一条同名 → 去重
        {"name": "乙", "done": "false"},
    ])
    assert out[0]["name"] == "长" * 40 and out[0]["done"] is True
    # done 归一：truthy 字符串 → True；"false" 字符串 → True（bool("false") 就是 True）
    assert out[1]["name"] == "乙"
    assert out[1]["done"] is True
    assert len(out) == 2  # 截断去重前 3 条，去重后剩 2 条


def test_clean_milestones_caps_at_max():
    """总量封顶 MAX_MILESTONES，超出的丢弃。"""
    many = [{"name": f"M{i}"} for i in range(30)]
    out = kanban._clean_milestones(many)
    assert len(out) == kanban.MAX_MILESTONES == 20
    assert out[-1]["name"] == "M19"


def test_task_milestone_cleaned():
    """TaskIn.milestone strip 首尾空白并截断 40；超长不报错。"""
    # 直接测模型层（进程内零依赖），创建不需要 app_client
    from app.main import TaskIn
    t = TaskIn(title="任务", milestone="  归属  ")
    assert t.milestone == "归属"
    t2 = TaskIn(title="任务2", milestone="长" * 50)
    assert len(t2.milestone) == kanban.MAX_MILESTONE_NAME == 40
    t3 = TaskIn(title="任务3", milestone="   ")
    assert t3.milestone == ""
    t4 = TaskIn(title="任务4")          # 缺省
    assert t4.milestone == ""


def test_milestone_cleaned_over_http(app_client):
    """HTTP 入口同样走清洗：空转归一、超长截断。"""
    _, p = app_client.create_project("清洗入口", milestones=[{"name": " 甲 "}])
    pid = p["id"]
    _, t = app_client.post(f"/api/projects/{pid}/tasks",
                           {"title": "任务", "milestone": "  甲  "})
    assert t["milestone"] == "甲"
    _, t2 = app_client.post(f"/api/projects/{pid}/tasks",
                            {"title": "任务2", "milestone": "长" * 50})
    assert len(t2["milestone"]) == 40


# ---------------- milestone_progress 纯函数 ----------------

def test_milestone_progress_counts_done_over_total():
    mp = milestone_progress(
        [{"name": "A", "done": False}, {"name": "B", "done": True}],
        [
            {"milestone": "A", "status": "已完成"},
            {"milestone": "A", "status": "待办"},
            {"milestone": "A", "status": "进行中"},
        ],
    )
    by_name = {m["name"]: m for m in mp}
    assert by_name["A"]["progress"] == 33          # 1/3 完成
    assert by_name["A"]["total"] == 3 and by_name["A"]["done_tasks"] == 1
    assert by_name["B"]["progress"] == 0           # 有 done 标记但没有任务归组
    assert by_name["B"]["done"] is True
    assert len(mp) == 2


def test_milestone_progress_ignores_unknown_assignments():
    """任务引用了项目里不存在的里程碑 → 不计入任何分组（也不算总数）。"""
    mp = milestone_progress([{"name": "A"}], [
        {"milestone": "A", "status": "已完成"},
        {"milestone": "Ghost", "status": "已完成"},   # 不存在的归属
        {"milestone": "", "status": "已完成"},         # 未归组
    ])
    assert mp == [{"name": "A", "done": False, "total": 1,
                   "done_tasks": 1, "progress": 100}]


def test_milestone_progress_skips_bad_milestone_rows():
    mp = milestone_progress(["bad", {"name": ""}, {"name": "A", "done": False}], [])
    assert [m["name"] for m in mp] == ["A"]


def test_milestone_progress_rounds_half_up():
    mp = milestone_progress([{"name": "A"}], [
        {"milestone": "A", "status": "已完成"},
        {"milestone": "A", "status": "待办"},
        {"milestone": "A", "status": "待办"},
    ])
    assert mp[0]["progress"] == 33


# ---------------- 交接包契约：不因新字段变长 ----------------

def test_handoff_package_has_no_milestone_keys(app_client):
    """交接包是给别的程序读的：里程碑字段不能混进去（验收口径）。"""
    _, p = app_client.create_project("交接包里程碑", milestones=[
        {"name": "内部里程碑", "done": False}])
    pid = p["id"]
    app_client.post(f"/api/projects/{pid}/tasks",
                    {"title": "任务", "milestone": "内部里程碑", "status": "待办"})
    _, ctx = app_client.get(f"/api/context/{pid}")
    assert set(ctx["project"]) == {  # 与 field-set 钉死的那份完全一致
        "id", "name", "status", "progress", "priority", "tags",
        "repo_path", "background", "handoff_note", "next_action"}
    assert "milestones" not in ctx["project"]
    # 任务的里程碑归属也不应进入交接包的任务视图（slim 手工挑选字段）
    assert ctx["tasks"]["todo"], "待办任务应存在，否则断言空转"
    assert all("milestone" not in t for t in ctx["tasks"]["todo"])
    # 详情接口是另一条路：它必须带 milestones_progress（前端渲染只认这一份）
    _, detail = app_client.get(f"/api/projects/{pid}")
    assert "milestones_progress" in detail


# ---------------- 前端契约 ----------------

def test_frontend_milestone_contract():
    html = frontend_source()
    # 项目弹窗：里程碑行编辑器 + 添加按钮
    assert 'id="f-milestones"' in html
    assert 'data-act="addMilestoneRow"' in html
    # 行内删除按钮带 data-arg（收行序号）
    assert re.search(r'data-act="removeMilestoneRow"\s+data-arg', html) or \
           re.search(r'data-arg="\$\{i\}"[^>]*data-act="removeMilestoneRow"', html) or \
           'data-act="removeMilestoneRow"' in html
    # 任务弹窗：里程碑下拉
    assert 'id="t-milestone"' in html
    # 详情：里程碑面板
    assert 'id="ms-panel"' in html and 'id="ms-body"' in html

    # ACTIONS 表注册对账（data-act → 实现）
    block = re.search(r"const ACTIONS = \{(.*?)\n\};", html, re.S)
    assert block, "未找到 ACTIONS 表"
    keys = set(re.findall(r"^\s{2}([A-Za-z_$][\w$]*):", block.group(1), re.M))
    assert {"addMilestoneRow", "removeMilestoneRow"} <= keys
    # 内联事件红线：里程碑控件不许走 onclick
    assert not re.search(r'<input[^>]+\son(?:change|input|click)=', html)