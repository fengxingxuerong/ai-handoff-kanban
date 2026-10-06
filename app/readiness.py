# -*- coding: utf-8 -*-
"""交接就绪度：**唯一实现**。

原来有两份：`app/main.py` 的 `_readiness_items/_readiness_score`（界面与 API 用）和
`app/handoff_page.py` 的 `_readiness_line`（接手页用，为避免循环导入就地重写了一份）。
两份的口径已经不一致了 —— 接手页漏掉"占位词不算下一步"这条规则，
于是同一个项目在主界面显示 75 分、在接手页显示 100/100。

这就是"同一业务规则写两遍"的必然结果：抄的时候看起来一样，改的时候只改一处。
所以把规则收口到这里，两边都 import（本模块不依赖任何东西，不会成环）。
"""
from __future__ import annotations

# 下一步行动的占位词：写这些等于没写
PLACEHOLDER_ACTIONS = ("已完结", "暂停中，暂无下一步", "无")

ITEMS: tuple[tuple[str, str, int], ...] = (
    ("handoff", "交接说明已填写（给智能体）", 35),
    ("next", "下一步行动已明确", 25),
    ("bg", "项目背景已填写", 15),
    ("tasks", "已有任务清单", 15),
    ("active", "任务有推进（进行中/已完成）", 10),
)


def readiness_items(p: dict) -> list[dict]:
    """五项检查，各自带分值；`ok` 为假不计分"""
    next_action = (p.get("next_action") or "").strip()
    tasks = p.get("tasks", []) or []
    checks = {
        "handoff": bool((p.get("handoff_note") or "").strip()),
        "next": bool(next_action) and next_action not in PLACEHOLDER_ACTIONS,
        "bg": bool((p.get("background") or "").strip()),
        "tasks": len(tasks) > 0,
        "active": any(t.get("status") in ("进行中", "待审查", "已完成") for t in tasks),
    }
    return [{"key": k, "label": label, "ok": checks[k], "score": score}
            for k, label, score in ITEMS]


def readiness_score(p: dict) -> int:
    return sum(it["score"] for it in readiness_items(p) if it["ok"])


def readiness_line(p: dict) -> str:
    """给接手页用的一行摘要，如 `75/100（缺：下一步行动已明确）`"""
    items = readiness_items(p)
    missing = [it["label"] for it in items if not it["ok"]]
    tail = "（缺：" + "、".join(missing) + "）" if missing else "（齐备）"
    return f"{readiness_score(p)}/100 {tail}"
