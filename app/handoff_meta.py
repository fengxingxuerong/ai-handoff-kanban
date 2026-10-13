# -*- coding: utf-8 -*-
"""交接包小节与尺寸预算。

**从 `app/main.py` 整体搬来（2026-09-26 大文件拆分），内容逐字未改。**
`app/main.py` 用 `from app.handoff_meta import ...` 保住既有名字 —— 测试里的
`from app.main import X` / `monkeypatch.setattr(main, "X", ...)` 仍然有效。

搬移理由：这些是**纯函数/纯常量**（不碰文件、不碰网络、不读全局状态），
原先埋在 3596 行的 `main.py` 里，只能靠端到端间接验证边界；独立成模块后可以穷举。
约束型注释（含"不要…否则…"的）已随代码一起搬，不要在新家把它们删掉。
"""
from __future__ import annotations

from fastapi import HTTPException

def context_budget(text: str, warn_lines: int = 150, over_lines: int = 300) -> dict:
    """交接物有多长（行 / KB / 档位）。

    为什么值得报这个数字：接手方是模型，上下文窗口是它的稀缺资源 ——
    上游（Claude Code 对 CLAUDE.md 的官方口径）也是按"超过约 200 行遵循度开始掉"
    来建议的。交接包不是越长越负责：过长会被截断、被略读，反而丢关键信息。
    报出来让人和智能体自己决定要不要精简。
    """
    lines = len(text.splitlines())
    kb = round(len(text.encode("utf-8")) / 1024, 1)
    level = "ok" if lines <= warn_lines else ("warn" if lines <= over_lines else "over")
    return {"lines": lines, "kb": kb, "level": level}

def _budget_note(text: str) -> str:
    """给交接物末尾追加一行尺寸提示（只在偏长/过载时说话，正常长度不打扰）"""
    b = context_budget(text)
    if b["level"] == "ok":
        return ""
    hint = {"warn": "偏长，建议精简背景与已完成任务",
            "over": "过长：接手方很可能读不完或被截断，务必裁剪"}[b["level"]]
    return f"\n\n—— 本包约 {b['lines']} 行 / {b['kb']} KB，{hint}。"

HANDOFF_SECTIONS = ("git", "journal", "memories", "refs", "done")
_SECTION_CN = {"git": "代码现状", "journal": "任务过程记录", "memories": "已沉淀记忆",
               "refs": "涉及代码位置", "done": "已完成任务"}

# ---------------- 条目级尺寸估算（2026-10-11 对标 claude-mem 的渐进式披露） ----------------
# claude-mem（99k★）把记忆分两层：索引层给 50-100 token 的紧凑条目，详情层按需再取
# 500-1000 token 的正文，靠这个换来约 10x 的上下文节省。本项目只有一层 ——
# 一条记忆就是完整条目，不存在"再取详情"这回事，所以渐进披露在这里的最小落地是
# **把每条的成本先报出来**：消费方（模型 / 界面）据此决定要不要把这条读进上下文，
# 而不是整屏塞进去之后才发现占掉半个窗口。**不因此新增工具、也不改任何召回口径。**
#
# 口径（**近似值，不是计费口径**）：CJK 字符按 1 token/字、其余字符按 4 字符/token **上取整**。
# 上取整是有意的：宁可略高，也别让消费方按偏小的数以为"这点还装得下"。
# 与真实分词器的偏差大致 ±30%，拿来判断"这条贵不贵"够用；**不要拿它做用量记账** ——
# 那一路是 usage.jsonl 的真实计数（app/usage_stats.py），两者别混。
_CJK_RANGES = ((0x3000, 0x303F), (0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
               (0xAC00, 0xD7AF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF))


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token（上取整）。"""
    cjk = sum(1 for ch in text if _is_cjk(ch))
    return cjk + -(-(len(text) - cjk) // 4)


def text_size(text: str) -> dict:
    """一段文本的尺寸：`chars`（精确字符数）/ `kb`（UTF-8 千字节）/ `est_tokens`（近似）。

    **只用于决策展示，不参与排序也不参与截断** —— 记忆召回窗口仍由
    `due.recall_memories` 的类型加权决定（见硬性约定 26 那一套）。
    """
    text = text or ""
    return {"chars": len(text), "kb": round(len(text.encode("utf-8")) / 1024, 2),
            "est_tokens": estimate_tokens(text)}


def size_note(text: str) -> str:
    """一行的尺寸标注（MCP 记忆列表用），形如 `≈128 字/≈96 tok`。"""
    s = text_size(text)
    return f"≈{s['chars']} 字/≈{s['est_tokens']} tok"


def parse_handoff_sections(raw, *, param: str) -> set[str]:
    """解析"要省略哪些小节"。不认识的名字**必须报错**，不能静默忽略 ——
    静默忽略等于调用方以为自己裁掉了、实际整包原样发出去。"""
    if raw is None or raw == "":
        return set()
    items = raw if isinstance(raw, (list, tuple)) else [x for x in str(raw).replace(" ", ",").split(",")]
    out = set()
    for x in items:
        name = str(x).strip().lower()
        if not name:
            continue
        if name not in HANDOFF_SECTIONS:
            raise HTTPException(400, f"{param} 不认识「{name}」，可选：{'、'.join(HANDOFF_SECTIONS)}")
        out.add(name)
    return out
