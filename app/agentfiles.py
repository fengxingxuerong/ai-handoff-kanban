# -*- coding: utf-8 -*-
"""多智能体入口文件生成。

**从 `app/main.py` 整体搬来（2026-09-26 大文件拆分），内容逐字未改。**
`app/main.py` 用 `from .agentfiles import ...` 保住既有名字 —— 测试里的
`from app.main import X` 仍然有效。
"""
from __future__ import annotations


# 各工具的入口文件路径：AGENTS.md 是唯一事实来源，其余都是它的投影
AGENT_ENTRY_TARGETS: dict[str, str] = {
    "agents": "AGENTS.md",
    "claude": "CLAUDE.md",
    "cursor": ".cursor/rules/project.mdc",
    "copilot": ".github/copilot-instructions.md",
    "gemini": "GEMINI.md",
    "cline": ".clinerules/project.md",
    "aider": "CONVENTIONS.md",
    "windsurf": ".windsurfrules",
}

# Claude Code 不直接读 AGENTS.md，官方给的方式是让 CLAUDE.md 导入它
CLAUDE_BRIDGE = (
    "# CLAUDE.md\n"
    "\n"
    "<!-- 项目约定统一维护在 AGENTS.md，本文件只做桥接，避免两份内容各写各的、慢慢漂移 -->\n"
    "\n"
    "@AGENTS.md\n"
)

def _cursor_rule(content: str, p: dict) -> str:
    """Cursor Project Rules 需要 YAML frontmatter（描述 / 作用范围 / 是否常驻）"""
    desc = f"{p.get('name', '项目')} 的工程约定（由 AI 交接看板生成）"
    return f"---\ndescription: {desc}\nglobs:\nalwaysApply: true\n---\n\n{content}\n"

def build_agent_entries(p: dict, content: str, targets: list[str] | None = None) -> dict[str, str]:
    """把同一份内容投影成各工具要求的入口文件，返回 {相对路径: 内容}

    相对路径来自白名单，不接受调用方传入，因此不存在路径穿越。
    """
    picked = targets if targets else list(AGENT_ENTRY_TARGETS)
    out: dict[str, str] = {}
    for t in picked:
        rel = AGENT_ENTRY_TARGETS.get(t)
        if not rel or rel in out:
            continue
        if t == "claude":
            out[rel] = CLAUDE_BRIDGE
        elif t == "cursor":
            out[rel] = _cursor_rule(content, p)
        else:
            out[rel] = content if content.endswith("\n") else content + "\n"
    return out
