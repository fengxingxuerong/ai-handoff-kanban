# -*- coding: utf-8 -*-
"""AGENTS.md 文档生成（AI 版 + 本地规则兜底）。

**从 `app/main.py` 拆出（2026-10-06，依赖注入式，同 `app/handoff_md.py` 的做法）**：
本模块只收现成的数据与回调，不 import main：

- `build_agents_md_local(p, memory_rows, now)`：本地规则兜底版。记忆列表与生成时间
  由 main 注入（记忆取盘、过期过滤走 `_project_memories` 的口径）；
- `generate_agents_md(p, *, project_memories_fn, local_builder_fn)`：AI 版。
  记忆经 `project_memories_fn(pid)` 取（含脱敏与条数上限的口径只有 main 那一份），
  失败时调 `local_builder_fn(p)` 兜底 —— 注入回调而不是反向 import。

main 侧 `_build_agents_md_local` / `_generate_agents_md` 保留原签名
（tests/test_agent_files、tests/test_p1_absorb 与 MCP 的 `kanban_agents_md` 从那里调）。
"""
from __future__ import annotations

import json

from . import llm
from .redact import redact_text
from .taskgraph import topo_order


def build_agents_md_local(p: dict, memory_rows: list[dict], now: str) -> str:
    """本地规则生成 AGENTS.md（LLM 不可用时的兜底）"""
    lines = [f"# AGENTS.md — {p['name']}", ""]
    lines.append(f"> 由 AI 交接看板生成 · {now} · 给 AI 编码代理的项目上下文")
    lines.append("")
    if p.get("background"):
        lines.append("## 项目概述")
        lines.append("")
        lines.append(p["background"])
        lines.append("")
    lines.append("## 构建与测试")
    lines.append("")
    lines.append(f"- 代码路径：`{p.get('repo_path') or '未指定'}`")
    lines.append("- 精确构建/测试命令：请查看仓库 README 或 package.json / pyproject.toml 的 scripts")
    lines.append("")
    if p.get("handoff_note"):
        lines.append("## 交接说明（请务必先读）")
        lines.append("")
        lines.append(p["handoff_note"])
        lines.append("")
    lines.append("## 当前进度与下一步")
    lines.append("")
    lines.append(f"- 状态：{p.get('status', '')}（进度 {p.get('progress', 0)}%）")
    lines.append(f"- 下一步：{p.get('next_action') or '未明确'}")
    lines.append("")
    tasks = topo_order(p.get("tasks") or [])
    by_id = {t.get("id"): t for t in (p.get("tasks") or [])}
    if tasks:
        lines.append("## 任务清单（按依赖排序）")
        lines.append("")
        for t in tasks:
            refs = "、".join(t.get("refs") or [])
            deps = [d for d in (t.get("depends_on") or []) if d in by_id]
            dep_txt = "，前置：" + "、".join(by_id[d].get("title", d) for d in deps) if deps else ""
            lines.append(f"- [{t.get('status', '待办')}] {t.get('title')}（优先级 {t.get('priority', '中')}）"
                         + (f" — 涉及：{refs}" if refs else "") + dep_txt)
        lines.append("")
    if memory_rows:
        lines.append("## 项目记忆（决策 / 坑 / 约定）")
        lines.append("")
        for m in memory_rows:
            lines.append(f"- 【{m.get('type') or '事实'}】{m.get('text') or ''}"
                         f"（{m.get('time') or ''}）")
        lines.append("")
    return "\n".join(lines)


def generate_agents_md(p: dict, *, project_memories_fn, local_builder_fn) -> tuple[str, str, str]:
    """生成 AGENTS.md 正文；返回 (内容, 来源, 端点)；LLM 不可用时退回本地规则版"""
    sys_msg = (
        "你是一位资深软件工程师，负责为项目编写 AGENTS.md —— 这是 Linux Foundation 标准的项目上下文文件，"
        "相当于给 AI 编码代理看的 README。要求：\n"
        "1) 总行数控制在 150 行以内，紧凑高信号；\n"
        "2) 只写 AI 无法从代码直接推断的信息：精确的构建/测试命令、代码约定、关键决策、已知的坑、当前进度；\n"
        "3) 使用 Markdown 小标题组织；具体务实，禁止空话套话（如'代码要规范'）；\n"
        "4) 命令若有不确定，写'请查看仓库 README 或 package.json scripts'，不要编造。\n"
        "直接输出 AGENTS.md 正文，不要任何额外说明。"
    )
    # 送给模型的那一份**必须脱敏**：这条不是"用户点名让润色自己写的文本"那个豁免档，
    # 而是把整份项目状态被动推给远端模型去写文档。以前 `redact` 只作用于返回体，
    # 送出侧的 background/handoff_note/next_action/任务标题全是原文 —— 密钥照样出本机。
    user_msg = "项目信息：\n" + redact_text(json.dumps({
        "name": p["name"], "repo_path": p.get("repo_path", ""),
        "status": p.get("status", ""), "progress": p.get("progress", 0),
        "background": p.get("background", ""), "handoff_note": p.get("handoff_note", ""),
        "next_action": p.get("next_action", ""),
        "tasks": [{"title": t["title"], "status": t.get("status", ""), "priority": t.get("priority", "")}
                  for t in (p.get("tasks") or [])],
        # 记忆层也送进去：系统提示词第 2 条要的就是"关键决策、已知的坑"，
        # 而这三样恰恰是用户特意记在记忆层里的东西（不送就等于白记）
        "memories": [{"type": m.get("type", ""), "text": m.get("text", "")}
                     for m in project_memories_fn(p.get("id", ""))],
    }, ensure_ascii=False, indent=1))
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [{"role": "system", "content": sys_msg},
                                  {"role": "user", "content": user_msg}], temperature=0.2, feature="AGENTS.md")
        if not text or len(text.strip()) < 30:
            raise ValueError("生成内容过短")
        return text.strip(), "ai", ep
    except Exception:
        return local_builder_fn(p), "local", ""
