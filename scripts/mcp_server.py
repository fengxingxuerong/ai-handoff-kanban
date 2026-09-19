# -*- coding: utf-8 -*-
"""
AI 交接看板 - MCP 服务（stdio 传输，零依赖手写 JSON-RPC 2.0）

让 AI 编码代理（Claude Code / Cursor / Cline / Codex / Windsurf …）直接读写看板，
不用再靠人把交接包粘来粘去。

协议兼容性（对照 MCP 规范各版本）：
- 2026-07-28 起协议改为无状态：请求自带 protocolVersion，没有 initialize 握手，
  结果带 resultType。本服务照常处理，并在结果里带 resultType。
- 2025-03-26 ~ 2025-11-25：仍走 initialize / notifications/initialized 握手，本服务同样支持。
- stdio 传输是「一行一个 JSON-RPC 消息」，日志一律走 stderr —— 往 stdout 写别的会污染协议。

注册示例（Claude Code / Cursor / Cline 的 mcpServers 配置里）：
    "ai-kanban": {
      "command": "D:\\\\lo\\\\项目任务看板\\\\.venv\\\\Scripts\\\\python.exe",
      "args": ["D:\\\\lo\\\\项目任务看板\\\\scripts\\\\mcp_server.py"]
    }

手动自测：
    echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | python scripts/mcp_server.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

os.environ.setdefault("KANBAN_NO_SCHEDULER", "1")   # 短命进程不起后台调度线程

# MCP 的 stdio 传输按规范就是 UTF-8，而中文 Windows 上子进程的三条标准流默认跟随控制台
# 码页（cp936）。不钉死的话两头都坏：出方向 `ensure_ascii=False` 的中文工具描述被编成
# GBK，客户端按 UTF-8 解 → 解码异常，工具目录等于没发布；入方向客户端发来的中文任务标题
# 被按 GBK 解 → 静默写进看板一堆乱码。**协议规范优先于宿主环境**，所以在这里自己收口，
# 而不是要求每个客户端都记得设 PYTHONIOENCODING。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001  被替换过的流可能没有 reconfigure
        pass

from app import main as kanban  # noqa: E402

SERVER_NAME = "ai-kanban"
# 与看板本体同一个版本号：手写的 "1.0.0" 在应用升到 1.1.0 之后就成了假信息，
# 客户端拿 serverInfo.version 判断能力/兼容性时会误判。
SERVER_VERSION = kanban.APP_VERSION

# 桌面应用服务的端口，用于给出"活数据"直链。直接取本体的常量而不是重抄一遍
# `os.environ.get("KANBAN_PORT", "18320")` —— 两份兜底值改一处就会漂移，
# 而漂移的后果是交接包里的链接指向一个没在听的端口。
SERVICE_PORT = kanban.SERVICE_PORT


def local_url(path: str) -> str:
    return f"http://127.0.0.1:{SERVICE_PORT}{path}"

# 支持协商的协议版本（新→旧）；客户端报别的版本时回落到最新
SUPPORTED_VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
DEFAULT_VERSION = SUPPORTED_VERSIONS[0]


def log(msg: str) -> None:
    print(f"[ai-kanban-mcp] {msg}", file=sys.stderr, flush=True)


# ---------------- 工具实现 ----------------
# 直接复用 app.main 里的业务函数（含字段白名单与单临界区写入），不另写一套逻辑。

def _projects_brief() -> list[dict]:
    out = []
    data = kanban.get_projects_data()
    for p in data.get("projects", []):
        tasks = p.get("tasks", [])
        counts = {k: 0 for k in kanban.DEFAULT_TASK_STATUS}
        for t in tasks:
            if t.get("status") in counts:
                counts[t["status"]] += 1
        out.append({
            "id": p.get("id"), "name": p.get("name"), "status": p.get("status"),
            "progress": p.get("progress"), "priority": p.get("priority"),
            "tags": p.get("tags", []), "repo_path": p.get("repo_path", ""),
            "next_action": p.get("next_action", ""),
            "task_counts": counts, "total_tasks": len(tasks),
            "readiness": kanban._readiness_score(p),
            "updated_at": p.get("updated_at", ""),
        })
    return out


def _find(project_id: str):
    found = kanban.find_project(project_id)
    return found[1] if found else None


def _match_by_name(query: str) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return []
    hits = []
    for p in _projects_brief():
        if q in (p["name"] or "").lower() or q == (p["id"] or "").lower():
            hits.append(p)
    return hits


def _task_by_id(task_id: str):
    data = kanban.get_projects_data()
    for p in data.get("projects", []):
        for t in p.get("tasks", []):
            if t.get("id") == task_id:
                return p, t
    return None, None


def t_list_projects(_args: dict) -> str:
    projects = _projects_brief()
    if not projects:
        return "看板里还没有项目。可以用 kanban_create_project 建一个。"
    lines = [f"共 {len(projects)} 个项目："]
    for p in projects:
        lines.append(
            f"- [{p['id']}] {p['name']} · {p['status']} · {p['progress']}% · 优先级{p['priority']}"
            f" · 任务 {p['total_tasks']}（进行中 {p['task_counts'].get('进行中', 0)}，阻塞 {p['task_counts'].get('阻塞', 0)}）"
            f" · 就绪度 {p['readiness']}/100"
        )
        if p["next_action"]:
            lines.append(f"    下一步：{p['next_action']}")
    return "\n".join(lines)


def t_get_project(args: dict) -> str:
    pid = args.get("project_id") or ""
    p = _find(pid)
    if not p:
        raise ValueError(f"找不到项目：{pid}（可用 kanban_list_projects 或 kanban_find_project 查 id）")
    lines = [
        f"# {p.get('name')}",
        f"- id: {p.get('id')}",
        f"- 状态/进度：{p.get('status')} / {p.get('progress')}%  优先级：{p.get('priority')}",
        f"- 代码路径：{p.get('repo_path') or '未填写'}",
        f"- 最后更新：{p.get('updated_at', '')}",
        "",
    ]
    if p.get("background"):
        lines += ["## 项目背景", p["background"], ""]
    if p.get("handoff_note"):
        lines += ["## 交接说明（给接手的 AI）", p["handoff_note"], ""]
    if p.get("next_action"):
        lines += ["## 下一步", p["next_action"], ""]
    tasks = p.get("tasks") or []
    if tasks:
        lines.append("## 任务清单")
        for t in tasks:
            row = (f"- [{t.get('id')}] ({t.get('status')}/{t.get('priority')}) {t.get('title')}"
                   + (f" —— {t.get('desc')}" if t.get("desc") else ""))
            if t.get("refs"):
                row += "  ↳ 涉及：" + "、".join(t["refs"])
            lines.append(row)
    return "\n".join(lines)


def t_find_project(args: dict) -> str:
    hits = _match_by_name(args.get("query") or "")
    if not hits:
        return f"没有匹配「{args.get('query')}」的项目。"
    return "\n".join(f"- [{h['id']}] {h['name']} · {h['status']} · {h['progress']}%" for h in hits)


def _ensure_writable() -> None:
    """MCP 只读模式开关：开启后所有写操作被拒绝（个人默认关，分享/演示时打开）"""
    cfg = kanban.llm.load_config()
    if cfg.get("ai", {}).get("mcp_readonly"):
        raise ValueError("看板当前为 MCP 只读模式（AI 设置里可关闭），写操作被拒绝")


def t_create_project(args: dict) -> str:
    _ensure_writable()
    body = kanban.ProjectIn(
        name=args.get("name") or "",
        status=args.get("status") or "规划中",
        progress=int(args.get("progress") or 0),
        priority=args.get("priority") or "中",
        tags=args.get("tags") or [],
        repo_path=args.get("repo_path") or "",
        background=args.get("background") or "",
        handoff_note=args.get("handoff_note") or "",
        next_action=args.get("next_action") or "",
    )
    project = kanban.create_project(body)
    return f"已创建项目 [{project['id']}] {project['name']}（状态 {project['status']}）"


def t_update_project(args: dict) -> str:
    _ensure_writable()
    pid = args.get("project_id") or ""
    p = _find(pid)
    if not p:
        raise ValueError(f"找不到项目：{pid}")

    def pick(key: str, default):
        v = args.get(key)
        return default if v is None else v

    body = kanban.ProjectIn(
        name=pick("name", p.get("name")),
        status=pick("status", p.get("status")),
        progress=int(pick("progress", p.get("progress") or 0)),
        priority=pick("priority", p.get("priority")),
        tags=pick("tags", p.get("tags") or []),
        repo_path=pick("repo_path", p.get("repo_path") or ""),
        background=pick("background", p.get("background") or ""),
        handoff_note=pick("handoff_note", p.get("handoff_note") or ""),
        next_action=pick("next_action", p.get("next_action") or ""),
    )
    updated = kanban.update_project(pid, body)
    changed = [k for k in args if k not in ("project_id",)]
    return f"已更新 [{pid}] {updated['name']}：{('、'.join(changed)) or '无字段变化'}"


def t_add_task(args: dict) -> str:
    _ensure_writable()
    pid = args.get("project_id") or ""
    body = kanban.TaskIn(
        title=args.get("title") or "",
        desc=args.get("desc") or "",
        status=args.get("status") or "待办",
        priority=args.get("priority") or "中",
        agent=args.get("agent") or "",
        refs=args.get("refs") or [],
        depends_on=args.get("depends_on") or [],
        due_date=args.get("due_date") or "",
    )
    task = kanban.create_task(pid, body)
    refs = ("；涉及 " + "、".join(task["refs"])) if task.get("refs") else ""
    return f"已在项目 [{pid}] 新增任务 [{task['id']}] {task['title']}（{task['status']}/{task['priority']}）{refs}"


def t_update_task(args: dict) -> str:
    _ensure_writable()
    tid = args.get("task_id") or ""
    project, task = _task_by_id(tid)
    if not task:
        raise ValueError(f"找不到任务：{tid}")

    def pick(key: str, default):
        v = args.get(key)
        return default if v is None else v

    body = kanban.TaskIn(
        title=pick("title", task.get("title")),
        desc=pick("desc", task.get("desc") or ""),
        status=pick("status", task.get("status")),
        priority=pick("priority", task.get("priority")),
        agent=pick("agent", task.get("agent") or ""),
        refs=pick("refs", task.get("refs") or []),
        depends_on=pick("depends_on", task.get("depends_on") or []),
        due_date=pick("due_date", task.get("due_date") or ""),
    )
    updated = kanban.update_task(tid, body)
    return f"任务 [{tid}] 已更新为 {updated['status']}/{updated['priority']}（所属项目 {project.get('name')}）"


def t_next_task(args: dict) -> str:
    """现在就能动手的任务：接手时先问这个，别一上来挑个被前置挡住的"""
    pid = args.get("project_id") or ""
    p = _find(pid)
    if not p:
        raise ValueError(f"找不到项目：{pid}")
    tasks = p.get("tasks") or []
    ready = kanban.executable_tasks(tasks)
    # 按 id 比：`t not in ready` 是整份字典的相等比较，任务一多就是几万次全字段对比
    ready_ids = {t.get("id") for t in ready}
    blocked = [t for t in tasks if t.get("id") not in ready_ids and t.get("status") != "已完成"]
    if not tasks:
        return f"「{p.get('name')}」还没有任务。"
    lines = [f"# {p.get('name')} · 现在可以做"]
    if ready:
        for t in ready:
            refs = ("（" + "、".join(t.get("refs") or []) + "）") if t.get("refs") else ""
            lines.append(f"- [{t['id']}] {t.get('title')} · {t.get('priority')}{refs}")
    else:
        lines.append("- 没有可以直接开工的任务（都在等前置）")
    if blocked:
        by_id = {x.get("id"): x for x in tasks}
        lines.append("")
        lines.append("被前置挡住：")
        for t in blocked:
            waiting = [by_id[d].get("title", d) for d in (t.get("depends_on") or [])
                       if d in by_id and by_id[d].get("status") != "已完成"]
            if waiting:
                lines.append(f"- {t.get('title')}（等：{'、'.join(waiting)}）")
    return "\n".join(lines)


def t_delete_task(args: dict) -> str:
    _ensure_writable()
    tid = args.get("task_id") or ""
    if not _task_by_id(tid)[1]:
        raise ValueError(f"找不到任务：{tid}")
    kanban.delete_task(tid)
    return f"已删除任务 [{tid}]"


def t_stats(_args: dict) -> str:
    s = kanban.api_stats()
    lines = [
        f"项目总数：{s['project_total']}",
        f"任务总数：{s['total_tasks']}（阻塞 {s['blocked_tasks']}）",
        f"平均进度：{s['avg_progress']}%   平均交接就绪度：{s['avg_readiness']}/100",
        "状态分布：" + "、".join(f"{k}{v}" for k, v in s["status_dist"].items()),
    ]
    low = [r for r in s.get("readiness", []) if r.get("score", 0) < 60]
    if low:
        lines.append("交接就绪度偏低（建议补交接说明/下一步）：")
        lines += [f"  - {r['name']}（{r['score']}/100）" for r in low]
    return "\n".join(lines)


def t_handoff(args: dict) -> str:
    """交接包：让新接手的 AI 一次拿到全部上下文"""
    pid = args.get("project_id") or ""
    p = _find(pid)
    if not p:
        raise ValueError(f"找不到项目：{pid}")
    md = kanban._build_handoff_markdown(p)
    # 智能体是把整包读进上下文窗口的，报个尺寸让它知道要不要改用 kanban_context 分片取
    return md + kanban._budget_note(md)


def t_agents_md(args: dict) -> str:
    pid = args.get("project_id") or ""
    p = _find(pid)
    if not p:
        raise ValueError(f"找不到项目：{pid}")
    text, source, _ep = kanban._generate_agents_md(p)
    body = f"<!-- 来源：{source} -->\n\n{text}"
    return body + kanban._budget_note(body)


def t_context(args: dict) -> str:
    """机器可读接手包：一次拿到背景/交接说明/可执行任务/代码现实/就绪度"""
    pid = args.get("project_id") or ""
    p = _find(pid)
    if not p:
        raise ValueError(f"找不到项目：{pid}")
    ctx = kanban.project_context(pid)
    parts = [
        f"【{ctx['project']['name']}】接手上下文（{ctx['generated_at']}）",
        f"状态：{ctx['project']['status']}（进度 {ctx['project']['progress']}%）| 优先级：{ctx['project']['priority']} | 标签：{'、'.join(ctx['project'].get('tags') or []) or '无'}",
    ]
    if ctx.get("code_reality"):
        cr = ctx["code_reality"]
        parts.append(f"代码现实：分支 {cr.get('branch') or '—'}，最近提交 {cr.get('last_commit_at') or '—'}（{cr.get('days_since_commit')} 天前），未提交文件 {cr.get('uncommitted_files')} 个")
    if ctx["project"].get("background"):
        parts.append("项目背景：" + ctx["project"]["background"])
    if ctx["project"].get("handoff_note"):
        parts.append("交接说明（务必先读）：" + ctx["project"]["handoff_note"])
    if ctx["project"].get("next_action"):
        parts.append("下一步行动：" + ctx["project"]["next_action"])
    mems = [m for m in kanban._load_memories() if m.get("pid") == pid]
    if mems:
        parts.append("项目记忆（决策/坑/约定，接手时自动带上）：")
        parts += [f"  - [{m.get('type')}] {m.get('text')}" for m in mems[-8:]]
    if ctx["start_here"]:
        parts.append("现在就能动手的任务：")
        parts += [f"  - {t['title']}（{t.get('priority','中')}）{' @' + '、'.join(t['refs']) if t.get('refs') else ''}"
                  for t in ctx["start_here"]]
    todo = ctx["tasks"]["todo"]
    doing = ctx["tasks"]["in_progress"]
    blocked = ctx["tasks"]["blocked"]
    if doing:
        parts.append("进行中：" + "；".join(t["title"] for t in doing))
    if todo:
        parts.append("待办：" + "；".join(t["title"] for t in todo))
    if blocked:
        parts.append("阻塞：" + "；".join(t["title"] for t in blocked))
    rd = ctx["readiness"]
    parts.append(f"交接就绪度：{rd['score']}/100" + (f"（缺口：{'、'.join(rd['missing'])}）" if rd["missing"] else "（齐备）"))
    parts.append(f"实时上下文（本机服务，活数据）：{local_url('/api/context/' + pid)}")
    parts.append("—— 来自 AI 交接看板 MCP（可继续用 kanban_* 工具读写任务）")
    return "\n".join(parts)


def t_today(args: dict) -> str:
    """跨项目的「现在就能动手」（与 HTTP 的 /api/today 同一份判定）"""
    r = kanban.today_tasks(limit=int(args.get("limit") or 30))
    tasks = r["tasks"]
    if not tasks:
        return "没有可以立刻动手的任务：要么都已完成，要么还在等前置。"
    lines = [f"现在就能动手：{len(tasks)} 条，来自 {r['project_count']} 个项目（全量 {r['total']} 条）"]
    for t in tasks:
        due = f" 📅{t['due_date']}" if t.get("due_date") else ""
        refs = " 📍" + "、".join(t["refs"]) if t.get("refs") else ""
        mark = "（阻塞中，先解）" if t.get("status") == "阻塞" else ""
        lines.append(f"  [{t['priority']}]{mark} {t['title']} —— {t['project_name']}{due}{refs}")
    return "\n".join(lines)


def t_search_tasks(args: dict) -> str:
    """全局任务搜索（与 Ctrl+K 走同一个接口）"""
    r = kanban.search_tasks(q=args.get("query") or "", limit=int(args.get("limit") or 30))
    hits = r["results"]
    if not hits:
        return f"没有匹配「{args.get('query')}」的任务。"
    lines = [f"匹配 {len(hits)} 条任务："]
    for t in hits:
        # 注意：搜索接口用的是 task_id（today 用的是 id），这里按它自己的字段取
        lines.append(f"  [{t.get('task_id')}] {t['title']} —— {t['project_name']}（{t['status']}/{t['priority']}）")
    return "\n".join(lines)


def t_log_progress(args: dict) -> str:
    """给任务追加一条过程记录（做了什么 / 改了什么决策 / 踩了什么坑）"""
    _ensure_writable()
    tid = args.get("task_id") or ""
    project, task = _task_by_id(tid)
    if not task:
        raise ValueError(f"找不到任务：{tid}")
    entry = kanban.add_task_journal(tid, kanban.JournalIn(text=args.get("text") or ""))
    return f"已记录到任务 [{tid}]（{task.get('title')}）：{entry.get('text')}"


def t_memory_add(args: dict) -> str:
    """往项目记忆层写一条决策/坑/约定 —— 交接包与 AI 监管会自动召回它"""
    _ensure_writable()
    res = kanban.add_memory(kanban.MemoryIn(
        pid=args.get("project_id") or "",
        type=args.get("type") or "事实",
        text=args.get("text") or "",
        source="MCP",
    ))
    mem = res["memory"]
    if res.get("deduped"):
        return f"这条记忆已存在（同项目同文本不重复入），未写入。"
    where = mem.get("pid") or "（未指定项目）"
    return f"已记下 [{mem['type']}] 到 {where}：{mem['text']}"


TOOLS: list[dict] = [
    {
        "name": "kanban_list_projects",
        "description": "列出看板里所有项目的概览（状态、进度、优先级、任务统计、交接就绪度、下一步）。接手工作时先调这个。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "kanban_get_project",
        "description": "读单个项目的完整上下文：背景、交接说明、下一步、全部任务。开始干活前用它代替翻聊天记录。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "项目 id，可从 kanban_list_projects 获取"}},
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_find_project",
        "description": "按名称关键词模糊查找项目 id（只知道项目名、不知道 id 时用）。",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "项目名关键词"}},
            "required": ["query"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_create_project",
        "description": "新建一个项目。建议同时写 background（为什么做）与 next_action（下一步做什么），否则后续交接会缺上下文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "项目名"},
                "status": {"type": "string", "enum": ["规划中", "进行中", "已暂停", "已完成"]},
                "priority": {"type": "string", "enum": ["高", "中", "低"]},
                "progress": {"type": "integer", "minimum": 0, "maximum": 100},
                "repo_path": {"type": "string", "description": "代码路径"},
                "background": {"type": "string", "description": "项目背景"},
                "handoff_note": {"type": "string", "description": "给接手者的交接说明"},
                "next_action": {"type": "string", "description": "下一步行动"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_update_project",
        "description": "更新项目字段（只传要改的）。干完一段活请顺手更新 progress / next_action / handoff_note，让人和 AI 都能接着干。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "name": {"type": "string"},
                "status": {"type": "string", "enum": ["规划中", "进行中", "已暂停", "已完成"]},
                "priority": {"type": "string", "enum": ["高", "中", "低"]},
                "progress": {"type": "integer", "minimum": 0, "maximum": 100},
                "repo_path": {"type": "string"},
                "background": {"type": "string"},
                "handoff_note": {"type": "string"},
                "next_action": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_add_task",
        "description": "给项目加一个任务。refs 填相关代码位置（如 src/app.py:120），接手的人不用再翻一遍找在哪改。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "title": {"type": "string"},
                "desc": {"type": "string", "description": "验收标准 / 补充说明"},
                "status": {"type": "string", "enum": ["待办", "进行中", "已完成", "阻塞"]},
                "priority": {"type": "string", "enum": ["高", "中", "低"]},
                "agent": {"type": "string", "description": "负责的 AI 或人"},
                "refs": {"type": "array", "items": {"type": "string"},
                         "description": "相关代码位置，如 [\"app/main.py:120\", \"tests/test_api.py\"]"},
                "depends_on": {"type": "array", "items": {"type": "string"},
                               "description": "前置任务 id（同一项目内），用于排「先做哪个」"},
                "due_date": {"type": "string",
                             "description": "截止日期 YYYY-MM-DD，留空表示未设置"},
            },
            "required": ["project_id", "title"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_update_task",
        "description": "更新任务（只传要改的）。开始干就把状态改成进行中，干完改成已完成，被卡住改成阻塞。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "desc": {"type": "string"},
                "status": {"type": "string", "enum": ["待办", "进行中", "已完成", "阻塞"]},
                "priority": {"type": "string", "enum": ["高", "中", "低"]},
                "agent": {"type": "string"},
                "refs": {"type": "array", "items": {"type": "string"},
                         "description": "相关代码位置；不改就省略"},
                "depends_on": {"type": "array", "items": {"type": "string"},
                               "description": "前置任务 id；不改就省略"},
                "due_date": {"type": "string",
                             "description": "截止日期 YYYY-MM-DD；不改就省略（传空串等于清掉截止日期）"},
            },
            "required": ["task_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_next_task",
        "description": "问「现在该做哪个」：返回所有前置已完成、可以立刻开工的任务，以及被谁挡住的任务。"
                       "刚接手一个项目时先调这个，别一上来挑个被依赖挡住的。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_delete_task",
        "description": "删除一个任务（不可恢复）。只传 task_id，任务 id 可从项目详情里拿到。",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_stats",
        "description": "看板全局统计：项目/任务数量、阻塞数、平均进度、平均交接就绪度，以及就绪度偏低的项目。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "kanban_handoff",
        "description": "导出项目的完整交接包（Markdown）：背景、交接说明、下一步、任务清单。适合在换会话/换工具时一次性读完上下文。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_agents_md",
        "description": "生成该项目的 AGENTS.md 正文（AI 不可用时自动用本地模板），可写进仓库给编码代理读。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_context",
        "description": "获取项目的机器可读接手包：背景、交接说明、现在就能动手的任务（依赖已满足）、代码现实、交接就绪度。换人/换工具接手时先调这个，一次拿到全部上下文。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_today",
        "description": "今天该干什么：跨项目列出所有「依赖已满足、现在就能动手」的任务，按优先级/截止日期排序。开始一天工作时先调这个，别逐个项目点开。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                          "description": "最多返回多少条，默认 30"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "kanban_search_tasks",
        "description": "跨项目按关键词搜任务（标题/描述/负责人），返回任务 id 与所属项目，拿到 id 就能直接 kanban_update_task。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                          "description": "最多返回多少条，默认 30"},
            },
            "required": ["query"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_log_progress",
        "description": "给任务追加一条过程记录：做了什么、改了什么决策、踩了什么坑。交接包会带上最近几条，接手的人不用重新问一遍。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "text": {"type": "string", "maxLength": 2000,
                         "description": "一条过程记录，具体一点（决定/原因/影响）"},
            },
            "required": ["task_id", "text"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_memory_add",
        "description": "往项目记忆层记一条长期有效的事实：决策/坑/约定。与任务不同，记忆会在交接包、AGENTS.md 和 AI 监管里自动召回。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "留空表示跨项目的通用记忆"},
                "type": {"type": "string", "enum": ["决策", "坑", "约定", "事实"]},
                "text": {"type": "string", "maxLength": 2000},
            },
            "required": ["text"], "additionalProperties": False,
        },
    },
]

HANDLERS = {
    "kanban_list_projects": t_list_projects,
    "kanban_get_project": t_get_project,
    "kanban_find_project": t_find_project,
    "kanban_create_project": t_create_project,
    "kanban_update_project": t_update_project,
    "kanban_add_task": t_add_task,
    "kanban_update_task": t_update_task,
    "kanban_next_task": t_next_task,
    "kanban_delete_task": t_delete_task,
    "kanban_stats": t_stats,
    "kanban_handoff": t_handoff,
    "kanban_agents_md": t_agents_md,
    "kanban_context": t_context,
    "kanban_today": t_today,
    "kanban_search_tasks": t_search_tasks,
    "kanban_log_progress": t_log_progress,
    "kanban_memory_add": t_memory_add,
}


# ---------------- 读写属性与参数校验 ----------------

# 每个工具是读还是写，客户端要靠它决定要不要二次确认；也用来在只读模式下摘掉写工具。
# generate 类只生成文本、不动看板数据，但会花真实模型配额。
TOOL_KIND = {
    "kanban_list_projects": ("read", "项目总览"),
    "kanban_get_project": ("read", "查看项目"),
    "kanban_find_project": ("read", "按名字找项目"),
    "kanban_stats": ("read", "看板统计"),
    "kanban_handoff": ("read", "导出交接包"),
    "kanban_context": ("read", "机器可读接手包"),
    "kanban_today": ("read", "今日待办"),
    "kanban_search_tasks": ("read", "搜索任务"),
    "kanban_next_task": ("read", "现在该做哪个"),
    "kanban_agents_md": ("generate", "生成 AGENTS.md"),
    "kanban_create_project": ("write", "新建项目"),
    "kanban_update_project": ("write", "更新项目"),
    "kanban_add_task": ("write", "新建任务"),
    "kanban_update_task": ("write", "更新任务"),
    "kanban_log_progress": ("write", "追加过程记录"),
    "kanban_memory_add": ("write", "记录项目记忆"),
    "kanban_delete_task": ("destructive", "删除任务"),
}

ANNOTATIONS = {
    "read":        {"readOnlyHint": True,  "destructiveHint": False, "idempotentHint": True},
    "write":       {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    "destructive": {"readOnlyHint": False, "destructiveHint": True,  "idempotentHint": True},
    "generate":    {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
}

TOOL_BY_NAME: dict[str, dict] = {}
for _t in TOOLS:
    # 漏登记的默认按"写"处理（宁可多一次确认，也不能把删除当只读）。
    # tests/test_mcp_server.py 会要求每个工具都在 TOOL_KIND 里登记。
    _kind, _title = TOOL_KIND.get(_t["name"], ("write", _t["name"]))
    _t["title"] = _title
    _t["annotations"] = dict(ANNOTATIONS[_kind])
    TOOL_BY_NAME[_t["name"]] = _t


def _readonly_on() -> bool:
    return bool(kanban.llm.load_config().get("ai", {}).get("mcp_readonly"))


def visible_tools() -> list[dict]:
    """只读模式下把写工具从目录里直接摘掉。

    只在调用时才拒绝是不够的：客户端看得见工具就会去调，然后一次次拿到"只读模式"错误，
    白烧轮次还像是服务坏了。目录里不给看才是正解（对标 Linear 的只读端点）。
    调用侧的 `_ensure_writable()` 保留不动，作为绕过目录时的第二道闸。
    """
    if not _readonly_on():
        return TOOLS
    return [t for t in TOOLS if TOOL_KIND.get(t["name"], ("write",))[0] == "read"]


def _validate_args(schema: dict, args: dict) -> None:
    """按工具自己声明的 inputSchema 校验入参。

    此前 required / enum / additionalProperties 只是写给客户端看的装饰：字段名拼错会被
    静默忽略、工具照跑，结果就是"我明明改了却没改"。声明既然写了就得真的执行。
    """
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        v = args.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise ValueError(f"缺少必填参数：{key}")
    if schema.get("additionalProperties") is False:
        unknown = sorted(k for k in args if k not in props)
        if unknown:
            raise ValueError("不认识的参数：" + "、".join(unknown)
                             + "；可用参数：" + ("、".join(sorted(props)) or "（无）"))
    for key, spec in props.items():
        if key not in args or args[key] is None:
            continue
        val = args[key]
        typ = spec.get("type")
        if typ == "string" and not isinstance(val, str):
            raise ValueError(f"{key} 必须是字符串")
        if typ == "integer":
            if isinstance(val, bool) or not isinstance(val, int):
                raise ValueError(f"{key} 必须是整数")
            if "minimum" in spec and val < spec["minimum"]:
                raise ValueError(f"{key} 不能小于 {spec['minimum']}")
            if "maximum" in spec and val > spec["maximum"]:
                raise ValueError(f"{key} 不能大于 {spec['maximum']}")
        if typ == "array" and not isinstance(val, list):
            raise ValueError(f"{key} 必须是数组")
        enum = spec.get("enum")
        if enum and val not in enum:
            raise ValueError(f"{key} 只能是 {'/'.join(enum)}，收到「{val}」")


# ---------------- JSON-RPC / MCP ----------------

def _ok(mid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": {"resultType": "complete", **result}}


def _err(mid, code: int, message: str, data=None) -> dict:
    e = {"code": code, "message": message}
    if data:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": e}


def _negotiate(client_version: str | None) -> str:
    return client_version if client_version in SUPPORTED_VERSIONS else DEFAULT_VERSION


def handle(msg: dict) -> dict | None:
    """处理一条消息；返回 None 表示是通知、不回包"""
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    is_notification = mid is None

    if method == "initialize":
        client_version = (params.get("protocolVersion") or "")
        return _ok(mid, {
            "protocolVersion": _negotiate(client_version),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": ("这是「AI 交接看板」：管理多个项目的状态与交接上下文。"
                             "开工前先 kanban_list_projects，再用 kanban_get_project 或 kanban_handoff 读上下文；"
                             "干完一段活请用 kanban_update_project 更新 progress 与 next_action。"
                             f"需要最新数据时也可以直接 GET {local_url('/api/context/<项目id>')}"
                             "（本机服务，取到的是活的，不是快照）。"),
        })

    if method in ("notifications/initialized", "initialized"):
        return None                      # 握手通知，无需回包

    if method == "ping":
        return _ok(mid, {})

    if method in ("server/discover", "discover"):
        return _ok(mid, {
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "protocolVersion": DEFAULT_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "tools": visible_tools(),
        })

    if method == "tools/list":
        return _ok(mid, {"tools": visible_tools(), "ttlMs": 60000, "cacheScope": "public"})

    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            return _ok(mid, {
                "content": [{"type": "text", "text": f"未知工具：{name}"}],
                "isError": True,
            })
        if not isinstance(args, dict):
            return _ok(mid, {
                "content": [{"type": "text", "text": "arguments 必须是对象"}],
                "isError": True,
            })
        try:
            _validate_args(TOOL_BY_NAME[name].get("inputSchema") or {}, args)
            text = handler(args)
            return _ok(mid, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:
            log(f"工具 {name} 执行失败：{traceback.format_exc()}")
            detail = getattr(e, "detail", None) or str(e) or type(e).__name__
            return _ok(mid, {"content": [{"type": "text", "text": f"执行失败：{detail}"}], "isError": True})

    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return _ok(mid, {key: []})

    if is_notification:
        log(f"忽略未知通知：{method}")
        return None
    return _err(mid, -32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    log(f"启动（协议版本 {DEFAULT_VERSION}，数据目录 {kanban.data_dir()}）")
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            _write(stdout, _err(None, -32700, "Parse error"))
            continue
        if not isinstance(msg, dict):
            _write(stdout, _err(None, -32600, "Invalid Request"))
            continue
        try:
            resp = handle(msg)
        except Exception:
            log(traceback.format_exc())
            resp = _err(msg.get("id"), -32603, "Internal error")
        if resp is not None:
            _write(stdout, resp)


def _write(stdout, payload: dict) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stdout.flush()


if __name__ == "__main__":
    serve()
