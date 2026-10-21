# -*- coding: utf-8 -*-
"""接手包切片与增量（2026-10-21 第七轮竞品吸取）。

背景：`/api/context/{pid}` 与 MCP `kanban_context` 一次返回整包（Project / 记忆 /
代码现实 / start_here / 四列任务 / 就绪度）。项目一多、任务一长，代理每轮只想知道
"自上次接手以来变了什么"，却被迫重读整包 —— 上下文窗口被重复内容吃掉。

本模块只做三件纯事（不 import main、不读盘、不跑子进程）：

1. `parse_context_sections`：`sections` 是**可选小节白名单**（`project` 始终返回）。
   不认识的 name 直接 400 —— 静默忽略会让调用方以为"要到了"，实际拿到另一份包。
2. `parse_since`：把 `YYYY-MM-DD` / `YYYY-MM-DD HH:MM` / ISO `T` 形式归一成
   `YYYY-MM-DD HH:MM`。只到分钟：同一分钟内改动会在下一次增量里**重复出现**，
   不会漏（重复可以忍，漏一条会让代理以为没发生）。
3. `changed_since`：缺时间戳的条目 fail-open 算"变了" —— 宁可多给一条，
   不能让老数据/手工导入的条目在增量里永久消失。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

# 可选小节（`project` 不在里面：接手方永远需要知道自己在接哪个项目）。
CONTEXT_SECTIONS = ("memories", "code_reality", "start_here", "tasks", "readiness")

_SECTION_HINT = "、".join(CONTEXT_SECTIONS)

# 只认这三种；顺序有意义：带秒的必须在 `%H:%M` 之前试，否则会被判失败。
_SINCE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_context_sections(raw, *, param: str = "sections") -> set[str] | None:
    """把 `sections` 参数归一成集合；`None` = 不裁剪（调用方没给这个参数）。

    空串 / 空数组 = 一个可选小节都不要（仍保留 `project` 与元数据）。
    支持逗号分隔（HTTP 查询参数）与列表（MCP / 内部调用）。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        items = raw.replace("，", ",").split(",")
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        raise HTTPException(400, f"{param} 要逗号分隔的字符串或字符串数组")

    out: set[str] = set()
    for item in items:
        name = str(item).strip().lower()
        if not name:
            continue
        if name not in CONTEXT_SECTIONS:
            raise HTTPException(
                400, f"{param} 不认识「{name}」，可选：{_SECTION_HINT}（project 始终返回）")
        out.add(name)
    return out


def _parse_timestamp(ts) -> datetime | None:
    text = str(ts or "").strip().replace("T", " ").rstrip("Z").strip()
    if not text:
        return None
    for fmt in _SINCE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_since(raw) -> str:
    """把 `since` 归一成 `YYYY-MM-DD HH:MM`；空值返回空串（= 全量）。"""
    if raw is None:
        return ""
    value = str(raw).strip()
    if not value:
        return ""
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise HTTPException(
            400, f"since 要 YYYY-MM-DD 或 YYYY-MM-DD HH:MM（收到「{value}」）")
    return parsed.strftime("%Y-%m-%d %H:%M")


def changed_since(ts, since: str) -> bool:
    """`ts` 是否不早于 `since`（含边界）。

    - `since` 为空 → 全量，直接 True；
    - `ts` 缺失/认不出 → True（fail-open，见模块头）；
    - 日期只有 `YYYY-MM-DD` → 按当天比较（整天的改动都算）；
    - 精确到分钟时用 `>=`：同分钟重复，不漏。
    """
    if not since:
        return True
    parsed = _parse_timestamp(ts)
    if parsed is None:
        return True
    text = str(ts).strip().replace("T", " ").rstrip("Z").strip()
    if len(text) == 10:
        return text >= since[:10]
    return parsed.strftime("%Y-%m-%d %H:%M") >= since[:16]


def has_timestamp(ts) -> bool:
    """只用于增量回执的"这几条没有时间戳"计数，不参与过滤判定。"""
    return _parse_timestamp(ts) is not None
