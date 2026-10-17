# -*- coding: utf-8 -*-
"""P0（对标 makeplane-plane：用量成本看板 + 项目级趋势统计）的两个后端聚合模块
与前端挂接点的补测。

**为什么单开一个文件**：两个模块（`app/usage_stats.py` / `app/project_stats.py`）
都是纯函数，各自口径在模块 docstring 里写清楚了（成本估算走端点单价、预算按自然月；
项目统计的截止热力窗口是未来 7 天、阻塞判定与 `executable_tasks` 同语义）。
这里把**逻辑真**的分支钉住：聚合桶的分流、钳制边界、阻塞链的反推，以及
"前端真的挂上了渲染函数"（跨语言对账，见 AGENTS.md 测试约定 13）。
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from app import project_stats as ps
from app import usage_stats as us


# ---------------- P0-1：用量聚合（usage_stats.py） ----------------


def test_usage_clamp_price_and_budget_bounds():
    """钳制边界：负数 / NaN / 垃圾 → 0；超上限 → 上限。写侧与读侧共用同一道钳。"""
    assert us.clamp_price(-5) == 0
    assert us.clamp_price(float("nan")) == 0
    assert us.clamp_price("abc") == 0
    assert us.clamp_price(us.MAX_PRICE_PER_1K * 3) == us.MAX_PRICE_PER_1K
    assert us.clamp_price(2.5) == 2.5
    assert us.clamp_budget(us.MAX_BUDGET_LIMIT * 9) == us.MAX_BUDGET_LIMIT
    assert us.clamp_budget(-1) == 0
    assert us.clamp_budget(80) == 80


def test_usage_aggregate_groups_dates_endpoints_and_cost():
    """聚合分流：成功调用才进 calls/tokens/cost，失败单独计数；
    by_date 升序、by_endpoint 按调用数降序；成本按端点单价估算。"""
    price_map = {"A": {"per_1k_input": 0.5, "per_1k_output": 1.0}}
    recs = [
        {"time": "2026-10-01 09:00:00", "feature": "监管", "endpoint": "A", "model": "m1",
         "prompt_tokens": 100, "completion_tokens": 50, "duration_s": 1.0, "ok": True},
        {"time": "2026-10-01 10:00:00", "feature": "监管", "endpoint": "B", "model": "m2",
         "prompt_tokens": 1000, "completion_tokens": 0, "duration_s": 1.0, "ok": False},
        {"time": "2026-10-02 09:00:00", "feature": "指令", "endpoint": "A", "model": "m1",
         "prompt_tokens": 200, "completion_tokens": 100, "duration_s": 1.0, "ok": True},
    ]
    got = us.aggregate(recs, price_map)
    assert got["totals"] == {
        "calls": 2, "failed": 1, "prompt_tokens": 300, "completion_tokens": 150,
        "cost": pytest.approx(0.3)}      # 0.05+0.05 + 0.1+0.1
    assert [d["date"] for d in got["by_date"]] == ["2026-10-01", "2026-10-02"]
    assert got["by_date"][0]["calls"] == 1 and got["by_date"][0]["failed"] == 1
    assert [e["endpoint"] for e in got["by_endpoint"]] == ["A", "B"]
    assert got["by_endpoint"][0]["calls"] == 2
    assert got["by_model"]["m1"]["calls"] == 2 and got["by_model"]["m2"]["failed"] == 1
    assert got["by_feature"]["监管"]["cost"] == pytest.approx(0.1)   # 100/1000*0.5 + 50/1000*1.0


def test_budget_status_handles_unset_and_month_window():
    """预算未设（None / 0）返回 None 不做告警；设了按自然月窗口算 pct，单月不跨月。"""
    base = [
        {"time": "2026-09-30 23:59:00", "endpoint": "A", "ok": True,
         "prompt_tokens": 1000, "completion_tokens": 0},
        {"time": "2026-10-01 00:00:00", "endpoint": "A", "ok": True,
         "prompt_tokens": 1000, "completion_tokens": 0},
    ]
    pm = {"A": {"per_1k_input": 0.5, "per_1k_output": 0.0}}
    assert us.budget_status(base, pm, None) is None
    assert us.budget_status(base, pm, 0) is None
    got = us.budget_status(base, pm, 10, now=datetime(2026, 10, 15))
    assert got is not None
    assert got["month"] == "2026-10"
    assert got["used"] == 0.5 and got["pct"] == 5.0   # 只算 10 月那一条


def test_usage_endpoint_returns_by_date_by_endpoint_and_budget(app_client):
    """/api/usage 保留旧键并新增 by_date / by_endpoint / budget（未设预算 None）。"""
    import app.llm as llm

    recs = [
        {"time": "2026-10-01 09:00:00", "feature": "监管", "endpoint": "A", "model": "m1",
         "prompt_tokens": 100, "completion_tokens": 0, "duration_s": 1.0, "ok": True},
        {"time": "2026-10-01 10:00:00", "feature": "指令", "endpoint": "A", "model": "m1",
         "prompt_tokens": 100, "completion_tokens": 0, "duration_s": 1.0, "ok": False},
    ]
    llm._usage_file().parent.mkdir(parents=True, exist_ok=True)
    llm._usage_file().write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    status, body = app_client.get("/api/usage")
    assert status == 200
    # 旧键保留（test_api.py 也在断）
    assert "totals" in body and "by_feature" in body and "by_model" in body and "recent" in body
    # 新键
    assert body["by_date"] == [{"date": "2026-10-01", "calls": 1, "failed": 1,
                                "tokens": 100, "cost": 0.0}]
    assert body["by_endpoint"][0]["endpoint"] == "A"
    assert body["budget"] is None


def test_ai_config_persists_price_and_budget_with_clamp(app_client):
    """PUT /api/ai/config 把单价与预算写进配置；负值钳到 0、超大钳到上限。"""
    status, body = app_client.put("/api/ai/config", {
        "endpoints": [{
            "name": "端点甲", "base_url": "https://a.example.com/v1",
            "api_keys": ["sk-test1234567890"], "models": ["m1"], "enabled": True,
            "price_per_1k_input": -3, "price_per_1k_output": us.MAX_PRICE_PER_1K * 2,
        }],
        "budget_limit": us.MAX_BUDGET_LIMIT * 4,
    })
    assert status == 200, body
    status, cfg = app_client.get("/api/ai/config")
    assert status == 200
    ep = cfg["ai"]["endpoints"][0]
    assert ep["price_per_1k_input"] == 0.0
    assert ep["price_per_1k_output"] == us.MAX_PRICE_PER_1K
    assert cfg["ai"]["budget_limit"] == us.MAX_BUDGET_LIMIT


# ---------------- P0-2：项目级统计（project_stats.py） ----------------


def _mk(tid: str, status: str = "待办", due: str | None = None, deps: list[str] | None = None) -> dict:
    return {"id": tid, "title": f"任务{tid}", "status": status,
            "due_date": due, "depends_on": deps or []}


def test_project_stats_distribution_and_due_heat():
    """状态分布按白名单四桶；截止热力窗口是未来 7 天（today 可注入）：
    逾期 / 未来 7 天 / 更晚 / 未设；已完成任务不进热力；乱写的 due 算未设。"""
    today = date(2026, 10, 1)
    tasks = [
        _mk("a", "待办", "2026-09-30"),      # 逾期
        _mk("b", "进行中", "2026-10-01"),    # 今天（未来 7 天内）
        _mk("c", "待办", "2026-10-07"),      # 第 7 天（窗口内）
        _mk("d", "待办", "2026-10-08"),      # 更晚
        _mk("e", "待办", "2026-abc"),        # 乱写 → 未设
        _mk("f", "阻塞", None),              # 未设
        _mk("g", "已完成", "2026-09-01"),    # 不进热力
        _mk("h", "已归档"),                  # 未知状态 → 其它桶
    ]
    got = ps.project_stats(tasks, today)
    assert got["by_status"] == {"待办": 4, "进行中": 1, "待审查": 0, "已完成": 1, "阻塞": 1, "其它": 1}
    assert got["due"] == {"overdue": 1, "within_7d": 2, "later": 1, "no_due": 3}   # e/f 没设、h 未知状态也无截止


def test_stalled_detection_window_and_status_filter():
    """停滞检测（第三轮竞品吸取，对标 agent-board）：只有「进行中」且 updated_at
    超 7 天才算停滞；待办/待审查/阻塞/已完成都不算；updated_at 缺失或乱格式不武断判停滞；
    窗口可注入；明细按搁置天数倒序、最多 8 条，count 给总数。"""
    now = datetime(2026, 10, 8, 12, 0)

    def _ts(tid, status, updated=None):
        t = _mk(tid, status)
        t["updated_at"] = updated          # None = 缺失 → 不判停滞
        return t

    tasks = [
        _ts("s1", "进行中", "2026-09-14 10:00"),   # 搁置 24 天，停滞
        _ts("s2", "进行中", "2026-10-02 12:00"),   # 搁置 6 天 → 窗口内，不算
        _ts("todo", "待办", "2026-08-01 08:00"),   # 待办停再久也不算
        _ts("rev", "待审查", "2026-08-01 08:00"),
        _ts("blk", "阻塞", "2026-08-01 08:00"),
        _ts("done", "已完成", "2026-08-01 08:00"),
        _ts("noupdate", "进行中", None),           # 缺失 → 不判
        _ts("bad", "进行中", "乱格式"),             # 解析失败 → 不判
]

    got = ps.project_stats(tasks, date(2026, 10, 8), now=now)
    st = got["stalled"]
    assert st["count"] == 1 and len(st["tasks"]) == 1, st
    assert st["tasks"][0]["id"] == "s1" and st["tasks"][0]["idle_days"] >= 24

    # 窗口注入：收紧到 3 天 → s2（6 天）也进榜；明细按搁置天数倒序
    tight = ps._stalled(tasks, now, window_days=3)
    assert {x["id"] for x in tight["tasks"]} == {"s1", "s2"}
    assert tight["tasks"][0]["id"] == "s1", "搁置更久的排前面"

    # 上限：11 条停滞任务 → count=11 但 tasks 只列 8 条
    many = [{"id": f"m{i}", "title": f"任务{i}", "status": "进行中",
             "updated_at": "2026-09-01 10:00"} for i in range(11)]
    capped = ps._stalled(many, now)
    assert capped["count"] == 11 and len(capped["tasks"]) == 8, (capped["count"], len(capped["tasks"]))


def test_project_stats_blocked_chain():
    """阻塞链：已完成前置不算阻塞、指向不存在的依赖按已满足（与 executable_tasks
    同语义）、多前置只列未完成者；明细只列前 8 条，count 给总数。"""
    tasks = [
        _mk("done", "已完成", deps=[]),
        _mk("free", "待办", deps=["done"]),          # 前置已完成 → 不阻塞
        _mk("ghost", "待办", deps=["no-such-id"]),   # 指向不存在 → 不阻塞
        _mk("wait-a", "待办", deps=["free"]),
        _mk("wait-b", "待办", deps=["wait-a"]),
        _mk("wait-c", "待办", deps=["wait-a", "wait-b"]),
    ]
    got = ps.project_stats(tasks, date(2026, 10, 1))
    blk = got["blocked"]
    assert blk["count"] == 3                       # wait-a / wait-b / wait-c
    names = {t["title"] for t in blk["tasks"]}
    assert names == {"任务wait-a", "任务wait-b", "任务wait-c"}
    by = {t["id"]: t["blocked_by"] for t in blk["tasks"]}
    assert [b["id"] for b in by["wait-a"]] == ["free"]
    assert [b["id"] for b in by["wait-c"]] == ["wait-a", "wait-b"]


def test_project_stats_blocked_list_capped_at_8():
    """明细截断：11 个被阻塞只列前 8 条，count 仍是 11（界面据此显示“共 N 条”）。"""
    tasks = [_mk(f"x{i}", "待办", deps=["blocker"]) for i in range(11)]
    tasks.append(_mk("blocker", "待办"))
    got = ps.project_stats(tasks, date(2026, 10, 1))
    assert got["blocked"]["count"] == 11
    assert len(got["blocked"]["tasks"]) == 8


def test_project_stats_endpoint_and_404(app_client):
    """/api/projects/{pid}/stats 端到端：三块结构齐全；项目不存在 404 且不含 keys。"""
    from datetime import date as _d, timedelta

    _, p = app_client.create_project("统计项目")
    pid = p["id"]
    today = _d.today()
    def add(title, **kw):
        body = {"title": title, "status": "待办", "priority": "中",
                "due_date": None, "depends_on": [], "refs": [], "desc": ""}
        body.update({k: v for k, v in kw.items() if v is not None})
        return app_client.post(f"/api/projects/{pid}/tasks", body)

    add("已办完", status="已完成", due_date=str(today - timedelta(days=3)))
    add("马上到期", due_date=str(today + timedelta(days=2)))
    add("还有的等", due_date=str(today + timedelta(days=30)))
    wait_id = add("干活的人", status="进行中")[1]["id"]
    add("被卡住", depends_on=[wait_id])

    status, body = app_client.get(f"/api/projects/{pid}/stats")
    assert status == 200, body
    assert set(body) == {"by_status", "due", "blocked", "stalled"}
    assert body["by_status"]["已完成"] == 1
    assert body["due"]["within_7d"] == 1 and body["due"]["later"] == 1
    assert body["blocked"]["count"] == 1
    assert body["blocked"]["tasks"][0]["blocked_by"][0]["id"] == wait_id

    status, body = app_client.get("/api/projects/nope/stats")
    assert status == 404
    assert set(body.keys()) == {"detail"}


# ---------------- 前端挂接（跨语言对账） ----------------


def _read_web(name: str) -> str:
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "web" / name
    return p.read_text(encoding="utf-8")


def test_frontend_stats_panel_and_usage_tab_hooks():
    """对账测试（测试约定 13）：前端这三样东西必须真实存在，否则改一边不会变红：
    - 详情页有 #stats-panel 且 renderDetail 真的调 renderStats；
    - 设置弹窗有 #ai-pane-usage（用量 Tab）与 #ai-budget-limit 输入；
    - switchAITab 注册进 ACTIONS（data-act 能分发到）。

    **JS 那几条一律对着"整个前端"判，不写具体文件名**：2026-10-18 把 5 个 js-*.js 重新
    分成 10 个之后，`renderStats();` 随 renderDetail 搬去了 js-detail.js，这条用例当场红 ——
    而它本来就该问"整个前端里有没有这件事"，不是"它在哪个文件里"（`frontend_src.py` 就是
    为这个目的存在的：契约测的是整个前端）。
    """
    from frontend_src import frontend_source

    html = _read_web("index.html")
    assert 'id="stats-panel"' in html
    assert 'id="ai-pane-usage"' in html
    assert 'id="ai-budget-limit"' in html
    front = frontend_source()
    assert "async function renderStats()" in front
    assert "/api/projects/${pid}/stats" in front
    assert "renderStats();" in front, "renderDetail 必须真的调 renderStats"
    assert "switchAITab: " in front, "switchAITab 必须注册进 ACTIONS，否则 data-act 分发不到"
