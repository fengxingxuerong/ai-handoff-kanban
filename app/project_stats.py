# -*- coding: utf-8 -*-
"""项目统计聚合（2026-10-01 P0-2 新增）。

**为什么独立成模块**：任务状态分布、截止日期热力、依赖阻塞链这三项都是**纯函数**
（不碰文件、不碰网络、不读全局状态）——从 `app/main.py` 拆出来是为了能穷举测试
各口径，而不是靠端到端间接验证。路由只负责"找项目 + 透传"。

**口径**（与 `app/due.py` / `app/taskgraph.py` 对齐）：
- 截止日期用 `YYYY-MM-DD` 字符串与 `date` 比较（与 `_due_state` 同源，
  但那边的"临期"是 2 天，这边的热力窗口是**未来 7 天**，两处不混用）；
- 已完成任务不进热力统计（已完成 = 无需再关心截止）；
- 阻塞判定复用 `executable_tasks` 的同一语义：未完成任务的所有前置都已完成
  （或依赖指向的任务不存在）才算可执行，否则为"被阻塞"；指向不存在的依赖
  按已满足处理，与 `executable_tasks` 的 `d not in known_ids` 分支一致。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# 状态白名单（与前端 STATUS_SET 同源，见 ACENTS.md 硬性约定 6）
STATUS_SET = ("待办", "进行中", "待审查", "已完成", "阻塞")


def _due_date_of(t: dict) -> str:
    """取任务规范化的截止日期；没设/解析不出都是空串（与 `due._clean_due_date` 同口径）。"""
    v = t.get("due_date") or ""
    return str(v).strip() if isinstance(v, str) else ""


def _status_dist(tasks: list[dict]) -> dict:
    """任务状态分布：只统计 STATUS_SET 内状态，未知状态并入「待办」以外的兜底项？不 ——
    未知状态是数据异常（写入时白名单已拦），单独给个 `其它` 桶，别让界面上"少了一个状态"。"""
    out = {s: 0 for s in STATUS_SET}
    other = 0
    for t in tasks:
        s = (t.get("status") or "").strip()
        if s in out:
            out[s] += 1
        else:
            other += 1
    if other:
        out["其它"] = other
    return out


def _due_heat(tasks: list[dict], today: date) -> dict:
    """截止日期热力：overdue=逾期 / within_7d=未来 7 天内（含今天）/ no_due=未设截止 /
    later=更晚。已完成任务不进热力。today 可注入固定值用于测试。"""

    def parse(d: str) -> date | None:
        try:
            return datetime.strptime(d, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    out = {"overdue": 0, "within_7d": 0, "no_due": 0, "later": 0}
    for t in tasks:
        if t.get("status") == "已完成":
            continue
        d = parse(_due_date_of(t))
        if d is None:
            out["no_due"] += 1
        elif d < today:
            out["overdue"] += 1
        elif d <= today + timedelta(days=6):   # 未来 7 天窗口：今天 + 后 6 天
            out["within_7d"] += 1
        else:
            out["later"] += 1
    return out


def _blocked_chain(tasks: list[dict]) -> dict:
    """依赖阻塞链：未完成任务的任一前置未完成 → 该任务被阻塞。
    返回被阻塞任务数（count）与明细（tasks，按输入顺序，最多 BLOCKED_SHOW 条）：
    每条含 id / title / status / blocked_by（阻塞它的前置任务摘要列表，含 id 与 title）。
    只读聚合，不修改任务；已完成任务与"阻塞"状态都按任务自身状态判，不追加阻塞。"""
    BLOCKED_SHOW = 8
    by_id = {t.get("id"): t for t in tasks if isinstance(t, dict) and t.get("id")}
    blocked: list[dict] = []
    for t in tasks:
        if not isinstance(t, dict) or t.get("status") == "已完成":
            continue
        blockers = []
        for d in (t.get("depends_on") or []):
            dep = by_id.get(d)
            if dep is None:
                continue            # 指向不存在的依赖按已满足处理（与 executable_tasks 一致）
            if dep.get("status") != "已完成":
                blockers.append({"id": dep.get("id"), "title": dep.get("title") or "(未命名任务)"})
        if blockers:
            blocked.append({
                "id": t.get("id"),
                "title": t.get("title") or "(未命名任务)",
                "status": t.get("status") or "",
                "blocked_by": blockers,
            })
    return {"count": len(blocked), "tasks": blocked[:BLOCKED_SHOW]}


STALLED_DAYS = 7          # 进行中任务多久没动算"停滞"（对标 agent-board stuck detection）


def _stalled(tasks: list[dict], now: datetime, window_days: int = STALLED_DAYS) -> dict:
    """停滞检测：`进行中` 且 updated_at 距 now 超过 window_days 天的任务。

    只盯「进行中」：待办没人承诺在做、待审查/阻塞的停滞语义各不相同（一个等人验收、
    一个等人解障，都不该按"没动"报警）；已完成天然排除。updated_at 缺失或解析失败
    的任务不判停滞——数据不完整时宁可漏报也不武断。窗口可注入便于测试与将来做成设置。
    """
    out: list[dict] = []
    for t in tasks:
        if not isinstance(t, dict) or t.get("status") != "进行中":
            continue
        raw = t.get("updated_at") or ""
        try:
            updated = datetime.strptime(str(raw), "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue
        idle_days = (now - updated).days
        if idle_days > window_days:
            out.append({"id": t.get("id"), "title": t.get("title") or "(未命名任务)",
                        "idle_days": idle_days})
    out.sort(key=lambda x: -x["idle_days"])
    return {"count": len(out), "tasks": out[:8]}


def milestone_progress(milestones: list[dict], tasks: list[dict]) -> list[dict]:
    """里程碑进度（P2 轻量版）：每个里程碑下已归组任务的完成情况。

    口径：任务归属按任务 `milestone` 字段等于里程碑 name 判断；进度 =
    该里程碑下「已完成」任务数 / 总归组任务数（无任务的里程碑按 0 算，
    靠 `done` 手工标记表示这个节点本身已经过去，界面上两者并列展示）。
    只统计 STATUS_SET 内的状态；milestones 非 dict / name 为空的一律跳过。
    """
    names = set()
    out: list[dict] = []
    m_by_name: dict[str, dict] = {}
    for m in milestones:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        if not name or name in names:
            continue
        names.add(name)
        m_by_name[name] = {"name": name, "done": bool(m.get("done")),
                           "total": 0, "done_tasks": 0}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        name = str(t.get("milestone") or "").strip()
        if name not in m_by_name:
            continue          # 引用了不存在的里程碑 = 归不进任何分组，不算数
        m_by_name[name]["total"] += 1
        if t.get("status") == "已完成":
            m_by_name[name]["done_tasks"] += 1
    for name in names:
        m = m_by_name[name]
        total = m["total"]
        progress = round(m["done_tasks"] / total * 100) if total else 0
        out.append({"name": m["name"], "done": m["done"],
                    "total": total, "done_tasks": m["done_tasks"], "progress": progress})
    return out


def project_stats(tasks: list[dict], today: date | None = None,
                  now: datetime | None = None) -> dict:
    """聚合项目级统计（纯函数）。`today` / `now` 可注入固定时钟，默认取当天/当前时刻。

    返回：`{by_status, due, blocked, stalled}`。全部只读。
    """
    now = now or datetime.now()
    today = today or now.date()
    return {
        "by_status": _status_dist(tasks),
        "due": _due_heat(tasks, today),
        "blocked": _blocked_chain(tasks),
        # 停滞检测（第三轮竞品吸取，对标 agent-board）：进行中但 updated_at 超 7 天
        "stalled": _stalled(tasks, now),
    }