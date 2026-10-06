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
    # Agent Skills（2026 起的第三个入口格式，`npx skills add` 生态同款）：
    # .claude/skills/<name>/SKILL.md，frontmatter 的 name 必须与目录名一致。
    "skills": ".claude/skills/ai-kanban/SKILL.md",
}

# Skill 目录名与 frontmatter 的 name 是同一件事，写成一份常量防漂
SKILL_NAME = "ai-kanban"

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

def _skill_entry(content: str, p: dict) -> str:
    """Agent Skills 格式：YAML frontmatter（name 与目录名一致、description 供按需加载时
    判断要不要读正文）+ 正文。正文仍是 AGENTS.md 那一份 —— Skill 只是第 9 个投影，"""
    desc = f"操作 {p.get('name', '项目')} 的交接看板：任务/依赖/记忆/交接包的约定与用法"
    return f"---\nname: {SKILL_NAME}\ndescription: {desc}\n---\n\n{content}\n"

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
        elif t == "skills":
            out[rel] = _skill_entry(content, p)
        else:
            out[rel] = content if content.endswith("\n") else content + "\n"
    return out
