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
