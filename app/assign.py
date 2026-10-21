# -*- coding: utf-8 -*-
"""任务自动分配（2026-10-01 新增）。

**为什么独立成模块**：自动分配的全部口径（优先级匹配、就绪判定、跳过已分配）
都是**纯函数**（不碰文件、不碰网络、不读全局状态）——从 `app/main.py` 拆出来
是为了能穷举测试各口径，而不是靠端到端间接验证。路由只负责"找项目 + 锁内写入"。

**口径**（与 `app/taskgraph.py` / `app/project_stats.py` 对齐）：
- **就绪判定复用 `executable_tasks`**：未完成 + 所有前置已完成（或依赖指向不存在按已满足）。
  不在这里重写第二遍"依赖就绪"，否则「现在该做哪个」(/next)、「谁被阻塞」(/stats)
  与自动分配会各说各话 —— 这正是本项目反复出问题的形状（两条路给不同答案）。
- 优先级匹配 `{"高","中","低"}`，与 `TaskIn.priority` 的白名单同源。
- 已分配的（agent 非空）任务**默认跳过**——不覆盖手工指派；`overwrite=True` 才强制覆盖。
- **幂等**：`plan_assign` 是纯计算；同一批任务全部分配后再次调用，结果为空、
  接口 `changed=False` 一次落盘都不做。
"""
from __future__ import annotations

from .taskgraph import executable_tasks

# 优先级白名单（与 app.main.TaskIn 同源；这里写死一份常量便于独立测试与传参）
PRIORITY_SET = ("高", "中", "低")
DEFAULT_PRIORITY = "高"


def allocatable(tasks: list[dict], priority: str = DEFAULT_PRIORITY) -> list[dict]:
    """合格候选：`priority` 匹配 + 未完成 + 依赖已就绪（复用 executable_tasks 口径）。

    状态=阻塞的任务不参与分配（2026-10-19）：它"依赖已就绪"是因为阻塞的不是前置，
    而是缺权限/缺环境之类 —— 自动派给谁都是在推一块推不动的石头。
    /next、接手包会把它带标记列出来让人处置，但分配器不该把它算成可开工。

    只做筛选不做分配；写路径在 `apply_assign` 的锁内完成。返回任务 dict 的引用。
    """
    if priority not in PRIORITY_SET:
        raise ValueError(f"未知优先级：{priority!r}，可选 {PRIORITY_SET}")
    return [t for t in executable_tasks(tasks)
            if t.get("priority") == priority and t.get("status") != "阻塞"]


def plan_assign(tasks: list[dict], agent: str, priority: str = DEFAULT_PRIORITY,
                overwrite: bool = False) -> tuple[list[dict], list[dict]]:
    """计算自动分配计划（纯函数，不修改任何输入）。

    返回 `(assign_tasks, skipped)`：
      - `assign_tasks`：将要写入 agent 的任务 dict **引用**列表（按项目内顺序）；
      - `skipped`：本可分配但被跳过的任务摘要 `[{id, title, reason}]`。

    跳过规则：agent 非空的任务，`overwrite=False` 时跳过（防止覆盖手工指派）；
    `overwrite=True` 时无论原来是谁都覆盖。agent 为空串时不做任何分配。
    """
    agent = (agent or "").strip()
    if not agent:
        return [], [{"id": t.get("id"), "title": t.get("title") or "(未命名任务)",
                     "reason": "没有指定要分配给谁"} for t in allocatable(tasks, priority)]

    assign_tasks: list[dict] = []
    skipped: list[dict] = []
    for t in allocatable(tasks, priority):
        cur = (t.get("agent") or "").strip()
        if cur and not overwrite:
            skipped.append({"id": t.get("id"), "title": t.get("title") or "(未命名任务)",
                            "reason": f"已有负责人：{cur}"})
        else:
            assign_tasks.append(t)
    return assign_tasks, skipped