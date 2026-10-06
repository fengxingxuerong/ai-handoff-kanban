# -*- coding: utf-8 -*-
"""交接包 Markdown 渲染器。

**从 `app/main.py` 拆出（2026-10-06，与此前纯函数搬移略有不同）**：本函数不是纯函数版
原样照搬，而是先做了**依赖注入**再搬 —— 原回退逻辑（`_load_memories()` 兜底取记忆、
`_memory_expired` 判时效）留在 `main` 的包装 `_build_handoff_markdown` 里，本模块只收
**现成的数据**：

- `memory_rows`：已按 pid 过滤、已剔除过期记忆的活跃记忆（main 侧取盘）；
- `expired_memory_count`：被剔除的过期条数（交接包里"省了几个要说话"）；
- `git_line_fn`：取"代码现状"小节的回调（omit 含 git 时不会被调用 —— 连子进程一起省）；
- `port`：三条实时直链的端口。

这样本模块**不 import main、不读盘、不跑子进程**，可以对着任意项目 dict 穷举渲染；
main 侧 `_build_handoff_markdown` 保留原签名（MCP 的 `kanban_handoff` 与既有测试都从那里调）。
"""
from __future__ import annotations

from .handoff_meta import HANDOFF_SECTIONS, _SECTION_CN
from .taskgraph import topo_order


def build_handoff_markdown(p: dict, memory_rows: list[dict], expired_memory_count: int,
                           redact: bool, omit: set[str], *,
                           git_line_fn, port: str) -> str:
    """渲染单项目交接包 Markdown。参数含义见模块 docstring。

    `redact=True` 时正文里那三条实时直链也要带上 `?redact=1`：否则一份"已脱敏"的交接物
    递给对方的同时递了三条取原文的路 —— 掩码盖住了正文，链接却指向未盖的那份。

    `omit` = 要省掉的小节（见 `HANDOFF_SECTIONS`）。省掉什么必须在**开头**留痕，
    否则接手方会把"没写"读成"没有"。
    """
    lines = []
    lines.append(f"# {p['name']} · 项目交接摘要")
    lines.append("")
    lines.append(f"- **状态**：{p['status']}（进度 {p['progress']}%）")
    lines.append(f"- **优先级**：{p['priority']}")
    lines.append(f"- **标签**：{'、'.join(p.get('tags', [])) or '无'}")
    if p.get("repo_path"):
        lines.append(f"- **代码路径**：`{p['repo_path']}`")
    lines.append(f"- **最近更新**：{p.get('updated_at', '')}")
    lines.append("")
    if omit:
        # 放在开头而不是结尾：接手方必须在**读之前**就知道这里少了什么，
        # 否则它会把"按请求省掉了"读成"这个项目没有记忆/没有未完成任务"。
        lines.append("> ⚠️ 本包按调用方请求省略了这些小节："
                     + "、".join(_SECTION_CN[x] for x in HANDOFF_SECTIONS if x in omit)
                     + "。要完整版就别带 omit_sections 参数重新取一次。")
        lines.append("")
    if p.get("background"):
        lines.append("## 项目背景")
        lines.append("")
        lines.append(p["background"])
        lines.append("")
    if p.get("handoff_note"):
        lines.append("## 交接说明（给新智能体）")
        lines.append("")
        lines.append(p["handoff_note"])
        lines.append("")
    if p.get("next_action"):
        lines.append("## 下一步行动")
        lines.append("")
        lines.append(p["next_action"])
        lines.append("")
    # 省掉 git 小节要连子进程一起省：git_line_fn 每次都要跑 git，
    # 裁剪时若还去取，就白付了这份开销。
    git_block = "" if "git" in omit else git_line_fn(p)
    if git_block:
        lines.append("## 代码现状（git）")
        lines.append("")
        lines.append(git_block)
        lines.append("")
    tasks = topo_order(p.get("tasks", []) or [])      # 按依赖排序：接手的人照着做就行
    by_id = {t.get("id"): t for t in (p.get("tasks") or [])}
    shown = [t for t in tasks if not ("done" in omit and t.get("status") == "已完成")]
    if tasks:
        if shown:
            lines.append("## 任务清单（按依赖排序）")
            lines.append("")
            lines.append("| 任务 | 状态 | 优先级 | 负责AI | 前置 | 描述 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
        for t in shown:
            title = (t.get("title") or "").replace("|", "\\|").replace("\n", " ")
            desc = (t.get("desc") or "").replace("|", "\\|").replace("\n", " ")
            deps = [d for d in (t.get("depends_on") or []) if d in by_id]
            dep_txt = "、".join((by_id[d].get("title") or d) for d in deps) if deps else "—"
            lines.append(f"| {title} | {t['status']} | {t['priority']} | {t.get('agent','') or '—'} "
                         f"| {dep_txt} | {desc} |")
        lines.append("")
        journaled = [(t, t.get("journal") or []) for t in shown]
        journaled = [(t, j) for t, j in journaled if j]
        if journaled and "journal" not in omit:
            lines.append("### 任务过程记录（每个任务最近 3 条）")
            lines.append("")
            for t, j in journaled:
                lines.append(f"**{t.get('title', '')}**")
                for e in j[-3:]:
                    lines.append(f"- [{e.get('time', '')}] {e.get('text', '')}")
                lines.append("")
        # 验收标准（对标 Backlog.md 的 DoD）：已完成的不再列（它已经验过了），
        # 其余的带着走 —— 接手方验收"这条到底怎么算完"时照着勾。
        with_acc = [(t, t.get("acceptance") or []) for t in shown
                    if t.get("acceptance") and t.get("status") != "已完成"]
        if with_acc:
            lines.append("### 验收标准（怎么算完成）")
            lines.append("")
            for t, acc in with_acc:
                lines.append(f"**{t.get('title', '')}**")
                lines += [f"- [ ] {a}" for a in acc]
                lines.append("")
        if expired_memory_count and "memories" not in omit:
            # 省了几个要说话：过期记忆是有意不带的，但接手方不能把"没写"读成"没有"
            lines.append(f"> ⚠️ 另有 {expired_memory_count} 条已过时效的记忆未列入"
                         "（expires 早于今天；要翻旧账去看板「项目记忆」面板）。")
            lines.append("")
        if memory_rows and "memories" not in omit:
            lines.append("### 已沉淀记忆（结构化要点）")
            lines.append("")
            for m in memory_rows[-15:]:
                lines.append(f"- 【{m.get('type', '事实')}】[{m.get('time', '')}] {m.get('text', '')}")
            lines.append("")
        with_refs = [t for t in shown if t.get("refs")]
        if with_refs and "refs" not in omit:
            lines.append("### 涉及代码位置")
            lines.append("")
            for t in with_refs:
                marks = "、".join(f"`{r}`" for r in t["refs"])
                lines.append(f"- {t.get('title', '')} → {marks}")
            lines.append("")
    pid = p.get("id") or ""
    suffix = "?redact=1" if redact else ""
    lines.append("## 实时上下文（比上面这份快照更新）")
    lines.append("")
    lines.append("本看板是本机服务，上面内容是导出那一刻的快照。同一台机器上的智能体可以直接取最新的：")
    lines.append("")
    lines.append(f"- 结构化接手包（喂给智能体）：`http://127.0.0.1:{port}/api/context/{pid}{suffix}`")
    lines.append(f"- Markdown 交接包：`http://127.0.0.1:{port}/api/export/{pid}{suffix}`")
    lines.append(f"- 自包含接手页（人看的单文件网页）：`http://127.0.0.1:{port}/api/export/page/{pid}{suffix}`")
    lines.append("")
    lines.append("服务没开时这三个链接取不到，用上面的快照即可。")
    lines.append("")
    lines.append("---")
    lines.append("由 AI 交接看板自动生成")
    return "\n".join(lines)
