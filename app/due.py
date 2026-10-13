# -*- coding: utf-8 -*-
"""截止日期归一与临期/逾期判定。

**从 `app/main.py` 整体搬来（2026-09-26 大文件拆分），内容逐字未改。**
`app/main.py` 用 `from app.due import ...` 保住既有名字 —— 测试里的
`from app.main import X` / `monkeypatch.setattr(main, "X", ...)` 仍然有效。

搬移理由：这些是**纯函数/纯常量**（不碰文件、不碰网络、不读全局状态），
原先埋在 3596 行的 `main.py` 里，只能靠端到端间接验证边界；独立成模块后可以穷举。
约束型注释（含"不要…否则…"的）已随代码一起搬，不要在新家把它们删掉。
"""
from __future__ import annotations

import re
from datetime import datetime

_DUE_CANONICAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def _clean_due_date(v) -> str:
    """归一截止日期：认 `2026-09-05` / `2026/9/5` / `2026.09.05`，认不出来就清空。

    写入端点自己会校验，这里兜的是导入与手改的 projects.json。
    不归一的话它是**按字符串**比较的：`2026-9-5` 排在 `2026-08-01` 之后（'9' > '0'），
    于是 /api/today 的顺序、临期与逾期判断一起看错，而且错得看不出来。
    清空与 `TaskIn._clean_due` 同口径 —— 留着解析不出的原值只会让下游各自猜。

    长得就已经是规范形式的先看形状放过：这一步挂在每次读上，
    一万条任务逐条 `strptime` 实测给列表接口加了约 20ms（1000 项目数据集）。
    形状对但日期不存在（`2026-13-45`）的会留到 `_due_state` 里解析失败、按"未设截止"处理。
    """
    s = str(v or "").strip()
    if not s:
        return ""
    if _DUE_CANONICAL_RE.match(s):
        return s
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""

def _due_state(t: dict) -> str:
    """任务截止状态：overdue=已逾期 / soon=2 天内临期 / ok=正常 / 空=未设截止或已完成"""
    due = (t.get("due_date") or "").strip()
    if not due or t.get("status") == "已完成":
        return ""
    try:
        d = datetime.strptime(due, "%Y-%m-%d").date()
    except ValueError:
        return ""
    today = datetime.now().date()
    if d < today:
        return "overdue"
    if (d - today).days <= 2:
        return "soon"
    return "ok"


# 记忆召回的类型权重：决策/坑/约定是"换人必须知道"的高价值类，事实只是背景。
# 对齐 claude-mem 的 Awareness —— 高价值观察（decision/bugfix/security_alert/sensitive）
# 永远优先被注入，晚写的普通事实不挤出它们。当截断出口（AGENTS.md 12 条 / 模型简报 10 条）
# 必须取舍时，让背景为高价值让位。非法 type 已被 `_sanitize` 归一成"事实"，兜底同口径。
_MEMORY_RECALL_WEIGHT = {"决策": 0, "坑": 0, "约定": 0, "事实": 1}


def recall_memories(rows: list[dict], limit: int) -> list[dict]:
    """记忆召回排序：高价值类型（决策/坑/约定）优先，同类内时间新的在前，取前 limit 条。

    两步稳定排序：先按 time 倒序（新在前），再按类型权重升序（决策/坑/约定 > 事实）——
    稳定排序保住前一步的时间序，于是得到"类型优先，同类内新在前"。
    2026-10-11 从 claude-mem 的 Awareness push 吸取：此前 `_project_memories` 与
    `projects_brief` 都是纯时间序，晚写的普通「事实」会把更早记下的「坑/决策」挤出窗口——
    交接时把踩过的坑挤掉比挤掉一条背景事实严重得多。
    """
    ranked = sorted(rows, key=lambda m: (m.get("time") or ""), reverse=True)
    ranked.sort(key=lambda m: _MEMORY_RECALL_WEIGHT.get(m.get("type") or "事实", 1))
    return ranked[:limit]


def _memory_expired(m: dict, today: str = "") -> bool:
    """记忆是否已过期：`expires` 设了且早于今天（到期当天仍算有效）。

    2026-10-06 从 `app/main.py` 搬来（内容逐字未改）：这也是**日期类判定**，
    与截止日期同住一个模块。两边都是 YYYY-MM-DD（写入时已归一），字符串比较即日期比较。

    对标 OKM 知识代谢的时效语义：决策/约定这类长期事实没有到期日，而
    "当前用 X 方案""下个迭代前 key 是这把"这类**有保质期**的事实必须能标过期 ——
    否则过期结论会作为"当前事实"混进交接包，被下一个智能体当真。
    """
    exp = (m.get("expires") or "").strip()
    if not exp:
        return False
    today = today or datetime.now().strftime("%Y-%m-%d")
    return exp < today
