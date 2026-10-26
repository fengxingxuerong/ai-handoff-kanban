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

注册示例（Claude Code / Cursor / Cline 的 mcpServers 配置里）。

**推荐用 uvx**（已发布到 PyPI 时；一条命令、不挑平台、不需要 venv）：
    "ai-kanban": {
      "command": "uvx",
      "args": ["--from", "ai-handoff-kanban", "ai-handoff-kanban-mcp"]
    }

源码态（本机开发）仍然可以直指解释器与入口脚本 —— 本文件现在住在 `app/` 下，
`scripts/mcp_server.py` 是它的兼容壳（同一模块对象，见壳里的说明）：
    "ai-kanban": {
      "command": "<repo>\\.venv\\Scripts\\python.exe",
      "args": ["-m", "app.mcp_server"]
    }


手动自测：
    echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | python scripts/mcp_server.py
"""
from __future__ import annotations

import json
import re
import os
import sys
import traceback
from pathlib import Path
from urllib.parse import quote

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
# 而漂移的后果是交接包里的链接指向一个没在听的端口（兜底值本身现在只在 app/service.py 一处）。
SERVICE_PORT = kanban.SERVICE_PORT

# 直链格式：2026-10-24 起与后端共用同一实现（此前这里抄了一份 `http://127.0.0.1:{port}{path}`，
# 正是本项目最忌的"两条路给不同答案"）。名字保住，调用点与测试照旧。
from app.service import local_url as _local_url  # noqa: E402


def local_url(path: str) -> str:
    return _local_url(SERVICE_PORT, path)

# 支持协商的协议版本（新→旧）；客户端报别的版本时回落到最新
SUPPORTED_VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
DEFAULT_VERSION = SUPPORTED_VERSIONS[0]

# 我们声明的服务能力。只有一份：initialize 与 server/discover 必须报同一件事，
# 两处各写一遍的话，将来加能力时必然只改到一边。
# resources / prompts 都只是"把已有的交接内容换个入口摆出来"，不是新能力：
# 客户端把它们渲染成用户可选项，因此不占每轮对话的工具目录。
CAPABILITIES = {"tools": {"listChanged": False},
                "resources": {"listChanged": False},
                "prompts": {"listChanged": False}}


def _add_remove_params(base: str, what: str) -> dict:
    """生成 `<base>_add` / `<base>_remove` 两个增量参数的 schema。

    只有整体覆盖时，代理要加一条就得"读整份 → 拼好 → 写回"，而那次读发生在临界区外 ——
    期间桌面端加进去的条目会被这次整体写回抹掉（硬性约定 1 的丢更新形状）。
    增量在服务端的同一个临界区里展开成整体值，见 `kanban._expand_ops`。
    """
    return {
        f"{base}_add": {"type": "array", "items": {"type": "string"},
                        "description": f"往{what}里追加几条（不必先读整份再整体写回）"},
        f"{base}_remove": {"type": "array", "items": {"type": "string"},
                           "description": f"从{what}里去掉几条"},
    }


# 创建类工具共用的幂等键。智能体超时后重试是常态，没有这个键同一条指令就会
# 造出成对的重复任务（重复项还会跟着进交接包和拓扑序）。
# 命中时返回第一次建出来的那条，并在文案里明说"幂等重放"——只靠"id 和上次一样"
# 判断是不成立的：以前正文文案与新建完全相同，而超过 80 字符的键还会被截断成
# 同一个键（两条不同请求互相撞车）。现在超长键走"前缀+哈希"，不再截断。
CLIENT_REF_SCHEMA = {
    "type": "string",
    "description": "可选幂等键：同一次工作重试时传同一个值（比如你自己的操作 id），"
                   "看板会返回第一次创建的那条而不是再建一条，并在回执里注明是重放。"
                   "建议 80 字符以内；更长的会被压成前缀+哈希，仍然是不同的键。",
}


def _omit_arg(args: dict) -> set:
    """`omit_sections`：要从小节里裁掉什么。不认识的名字要报错，不能静默当"没给"。"""
    try:
        return kanban.parse_handoff_sections(args.get("omit_sections"), param="omit_sections")
    except kanban.HTTPException as e:
        raise ValueError(e.detail) from None


OMIT_SECTIONS_SCHEMA = {
    "type": "array", "items": {"type": "string", "enum": list(kanban.HANDOFF_SECTIONS)},
    "description": "裁掉这些小节以省上下文："
                   + "、".join(f"{k}={v}" for k, v in kanban._SECTION_CN.items())
                   + "。默认全给；裁掉什么会在正文开头留一行说明，别让接手方误读成\"没有\"。",
}

CONTEXT_SECTIONS_SCHEMA = {
    "type": "array", "items": {"type": "string", "enum": list(kanban.CONTEXT_SECTIONS)},
    "description": "只返回这些小节（project 与元数据始终返回）："
                   + "、".join(kanban.CONTEXT_SECTIONS)
                   + "。不传 = 全量；传空数组 = 一个可选小节都不要。",
}


def log(msg: str) -> None:
    print(f"[ai-kanban-mcp] {msg}", file=sys.stderr, flush=True)


# ---------------- 工具实现 ----------------
# 直接复用 app.main 里的业务函数（含字段白名单与单临界区写入），不另写一套逻辑。

def _board_rows() -> list[dict]:
    """`kanban_list_projects` / `resources/list` 用的"整块板子概览"。

    以前它叫 `_projects_brief`，与 `app.main._projects_brief(projects)`（送给模型的简报）
    **同名不同物**：一个是给人/代理看的板子摘要，一个是给 LLM 的载荷，字段集与脱敏口径都不同。
    同名两份实现是本项目的老毛病（`app/main.py` 里那句注释、以及"两份 `_projects_brief`
    付过一次代价"的测试注释都在说这件事），所以先把名字改成分得懂的。

    `next_action` 要过脱敏：它是自由文本，而这条是**代理侧**的读取 ——
    模型简报那份一直盖着，板子概览这份以前原样发出去，同一个字段两条路两个答案。
    """
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
            "next_action": kanban.redact_text(p.get("next_action", "") or ""),
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
    for p in _board_rows():
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
    projects = _board_rows()
    if not projects:
        return "看板里还没有项目。可以用 kanban_create_project 建一个。"
    lines = [f"共 {len(projects)} 个项目："]
    recovered, recovered_from = kanban.take_recovered_flag()
    if recovered:
        # 界面有恢复提示，代理这边以前什么都收不到：它以为看到的是正常的板，
        # 实际上这份可能刚从 last-good 救回来、缺着最近的项目。
        lines.insert(0, "⚠ 数据文件此前损坏，本次读到的内容刚从"
                        + (f" {recovered_from} " if recovered_from else "备份/空结构 ")
                        + "自愈恢复，可能不是最新状态；写入前请先与用户确认。")
    for p in projects:
        lines.append(
            f"- [{p['id']}] {p['name']} · {p['status']} · {p['progress']}% · 优先级{p['priority']}"
            f" · 任务 {p['total_tasks']}（进行中 {p['task_counts'].get('进行中', 0)}，阻塞 {p['task_counts'].get('阻塞', 0)}）"
            f" · 就绪度 {p['readiness']}/100"
        )
        if p["next_action"]:
            lines.append(f"    下一步：{p['next_action']}")
    return "\n".join(lines)


def _agent_text(body: str, args: dict) -> str:
    """代理侧读路径的统一出口脱敏（默认开）。

    AGENTS 约定 9 写的是"代理侧默认就是脱敏的"，但 `kanban_get_project` / `kanban_today` /
    `kanban_search_tasks` / `kanban_next_task` 四条以前**连这个开关都没有** ——
    而 `kanban_get_project` 恰恰是 `initialize` 指引里让代理第一个调的工具。
    掩码只替换凭据那一段：id / 状态 / 路径 / 项目名都不受影响，代理照样能对齐任务。
    要原文时显式传 `redact: false`。
    """
    return kanban.redact_text(body) if _redact_arg(args) else body


def _tool_content(text, args: dict, is_error: bool = False) -> dict:
    """代理侧**唯一**的出口：所有 tools/call 的正文（连报错正文一起）都从这儿走。

    为什么收在分发点、而不是让 23 个 handler 各自记得盖：AGENTS 约定 9 说的是
    "代理侧默认脱敏"，而"各自自觉"= 新增一条就漏一条。2026-09-23 实测漏掉的正好是
    回执类 —— `kanban_update_task` 的「已解锁 <别的任务标题>」、`kanban_log_progress`
    的 <任务标题>、`kanban_stats` 的项目名清单（它连 redact 参数都没有）、
    `kanban_delete_project` 的拒绝正文。标题里写"轮换 sk-xxx 这把"是最常见写法。

    各 handler 里原有的 `_agent_text` 保留：掩码只替换凭据那一段，对同一串重复施加
    是幂等的，不会把已经盖好的再盖一层。
    """
    return {"content": [{"type": "text", "text": _agent_text(str(text), args)}],
            "isError": is_error}


REDACT_SCHEMA = {"type": "boolean", "default": True,
                 "description": "先把密钥/令牌/邮箱打掩码再返回（默认 true）；确实需要原文时显式传 false"}


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
            # 验收标准跟在任务行下面：代理改完状态前要能对着它自检「怎么算完成」
            for a in (t.get("acceptance") or []):
                mark = "x" if t.get("status") == "已完成" else " "
                lines.append(f"    - [{mark}] {a}")
    return _agent_text("\n".join(lines), args)


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
    project = kanban.create_project(body, client_ref=args.get("client_ref") or "")
    replay = "（幂等重放：这个项目之前就存在，没有重复创建）" if project.get("client_ref_replayed") else ""
    return _agent_text(f"已创建项目 [{project['id']}] {project['name']}（状态 {project['status']}）"
                       f"{_repo_path_note(project.get('repo_path'))}{replay}", args)


def _repo_path_note(repo_path: str) -> str:
    """设了代码路径就要说清后果 —— 这是 MCP 与 AI 指令之间**唯一**那条能力不对称。

    AI 指令的白名单永远不含 `repo_path`（前端 `AI_PROJECT_FIELDS` / 后端
    `AI_EDITABLE_PROJECT_FIELDS` 都有钉子），因为那等于让模型自己决定"去探测本机哪个目录"；
    代理这条路径是用户显式指挥的，保留能力。但保留能力不代表可以不吭声：
    填上路径之后，看板会去那里跑 git，把分支/最后提交/脏文件数拼进上下文与模型请求。
    """
    if not repo_path:
        return ""
    return (f"；代码路径已设为 `{repo_path}` —— 之后看板会在那里跑 git，"
            "分支/最近提交/未提交改动会进入接手包与 AI 上下文（不想被探测就把它清空）")


def t_update_project(args: dict) -> str:
    _ensure_writable()
    pid = args.get("project_id") or ""
    changes = {k: v for k, v in args.items() if k != "project_id"}
    # 「没给的字段保持原值」这件事必须在 mutate 的临界区里做（见 kanban.patch_project）：
    # 以前这里先 _find() 读一整份、再把字段逐个回填提交，桌面端在那之间改过的值
    # 会被我们带回去的旧值反向抹掉 —— 典型的丢更新，而且只有并发时才看得见。
    try:
        updated = kanban.patch_project(pid, changes)
    except kanban.HTTPException as e:
        # main 侧的 404 不带 id（HTTP 的 id 在 URL 里），代理这边得补上
        raise ValueError(f"{e.detail}（project_id={pid}；先用 kanban_list_projects 或 "
                         f"kanban_find_project 确认 id）") from None
    return _agent_text(f"已更新 [{pid}] {updated['name']}：{('、'.join(changes)) or '无字段变化'}"
                       + _repo_path_note(updated.get("repo_path") if "repo_path" in changes else ""), args)


def _clean_notes(args: dict, stored: dict) -> list[str]:
    """把"传了但没落上"的字段说出来。

    看板对入参做规范化（截止日期只认 `YYYY-MM-DD`；refs / depends_on 去重、限量、
    去不存在项），规则本身是对的，以前的问题是**丢得毫无声音**：智能体传
    `due_date="下周三"` 会收到一句"已新增任务"，而盘上日期是空的，它以为设上了。
    这里不改动清洗规则，只把丢掉的东西讲出来 —— 代理需要的是可行动的反馈。
    """
    notes = []
    raw_due = args.get("due_date")
    if isinstance(raw_due, str) and raw_due.strip() and not (stored.get("due_date") or ""):
        notes.append(f"截止日期「{raw_due.strip()[:40]}」没认出来，已留空（只接受 YYYY-MM-DD）")
    raw_blocked = args.get("blocked_reason")
    if isinstance(raw_blocked, str) and raw_blocked.strip():
        norm = raw_blocked.strip()[:kanban.MAX_BLOCKED_REASON]
        # 两种"没落上"：状态不是阻塞（字段随状态存在，离开就清）；或超长被截。
        if ((stored.get("blocked_reason") or "") != norm
                or len(raw_blocked.strip()) > kanban.MAX_BLOCKED_REASON):
            notes.append(f"阻塞原因没落上（只在任务状态为「阻塞」时保留，超 "
                         f"{kanban.MAX_BLOCKED_REASON} 字会被截断）")
    for key, label, cap, norm in (
            ("refs", "涉及代码位置", kanban.MAX_REFS,
             lambda s: s.strip().strip("`").strip()),
            ("depends_on", "前置依赖", kanban.MAX_DEPS, lambda s: s.strip()),
            ("acceptance", "验收标准", kanban.MAX_ACCEPTANCE_ITEMS, lambda s: s.strip())):
        raw = args.get(key)
        if not isinstance(raw, (list, tuple)) or not raw:
            continue
        got = set(stored.get(key) or [])
        lost = [n for n in (norm(x) for x in raw if isinstance(x, str)) if n and n not in got]
        if lost:
            notes.append(f"{label}有 {len(lost)} 条没落上（不存在/重复/自引用/超 {cap} 条或过长）："
                         + "、".join(lost[:5]))
    # 增量动词的「没落上」同样要说话：*_add 撞条数上限/单条超长被截时静默，
    # 回执照样是"任务已更新" —— 整批覆盖路径有说明而增量没有，正是这个功能自己
    # 要消灭的形状（2026-10-06 审查发现：acceptance_add 撞 10 条上限后无人吭声）。
    # 对比基准是**落盘后**的 stored，且 norm 必须镜像后端清洗（截断到同一个长度），
    # 否则"截断后落上了"会被误报成丢失。*_remove 不在这里说：只有 post 状态可查，
    # "删掉了"与"本来就不存在"无法区分，而删除本来就是幂等语义。
    add_norm = {
        "tags": (lambda s: s.strip()[:24], "标签"),
        "refs": (lambda s: s.strip().strip("`").strip()[:kanban.MAX_REF_LEN], "涉及代码位置"),
        "depends_on": (lambda s: s.strip(), "前置依赖"),
        "acceptance": (lambda s: s.strip()[:kanban.MAX_ACCEPTANCE_TEXT], "验收标准"),
    }
    for base, (norm_fn, label) in add_norm.items():
        raw = args.get(base + "_add")
        if not isinstance(raw, (list, tuple)) or not raw:
            continue
        got = set(stored.get(base) or [])
        lost = [n for n in (norm_fn(x) for x in raw if isinstance(x, str))
                if n and n not in got]
        if lost:
            notes.append(f"{label}要新增的 {len(lost)} 条没落上（重复/已达条数上限或单条超长）："
                         + "、".join(lost[:5]))
    return notes


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
        acceptance=args.get("acceptance") or [],
        due_date=args.get("due_date") or "",
        blocked_reason=args.get("blocked_reason") or "",
    )
    task = kanban.create_task(pid, body, client_ref=args.get("client_ref") or "")
    refs = ("；涉及 " + "、".join(task["refs"])) if task.get("refs") else ""
    acc = task.get("acceptance") or []
    acc_txt = (f"；验收标准 {len(acc)} 条已记下" if acc else "")
    why = ("；阻塞原因：" + task["blocked_reason"]) if task.get("blocked_reason") else ""
    replay = "（幂等重放：这条之前就存在，没有重复创建）" if task.get("client_ref_replayed") else ""
    lost = _clean_notes(args, task)
    tail = ("；" + "；".join(lost)) if lost else ""
    return (f"已在项目 [{pid}] 新增任务 [{task['id']}] {task['title']}"
            f"（{task['status']}/{task['priority']}）{refs}{acc_txt}{why}{replay}{tail}")


def t_update_task(args: dict) -> str:
    _ensure_writable()
    tid = args.get("task_id") or ""
    changes = {k: v for k, v in args.items() if k != "task_id"}
    # 同 t_update_project：补全在临界区内做，不在锁外读快照
    try:
        updated, info = kanban.patch_task(tid, changes, with_project=True)
    except kanban.HTTPException as e:
        raise ValueError(f"{e.detail}（task_id={tid}）") from None
    lost = _clean_notes(args, updated)
    tail = ("；" + "；".join(lost)) if lost else ""
    # 停到阻塞却不给原因 = 下一个接手的人要多问一轮（第六轮吸取的"阻塞必须带原因"）。
    # 不拒绝（渐进式：轻量场景不该被强制），只提醒一次。
    if updated.get("status") == "阻塞" and not (updated.get("blocked_reason") or ""):
        tail += "；未记阻塞原因（blocked_reason）—— 建议补一句「卡在哪、为什么」，接手方会少问一轮"
    un = info.get("unblocked") or []
    # 报"你刚放行了哪几条"：不然代理得再问一遍 kanban_next_task 才知道下一步做什么
    unblock = ("；已解锁 " + "、".join(f"{x['title']} [{x['id']}]" for x in un[:5])
               + ("…" if len(un) > 5 else "")) if un else ""
    # 整条回执走 `_agent_text`：`unblock` 里是**别的任务的标题**、`info['project']` 是
    # 项目名，都是这次调用方没有提供、也不该原样拿到的存量内容 ——
    # 而"轮换 sk-xxx 这把"写在任务标题里是最常见的写法。
    # 分发点那层也会再盖一次（幂等），这里盖是因为**源头就不该出明文**。
    why = ("；阻塞原因：" + updated["blocked_reason"]) if updated.get("blocked_reason") else ""
    return _agent_text(
        f"任务 [{tid}] 已更新为 {updated['status']}/{updated['priority']}"
        f"（所属项目 {info.get('project', '')}）{why}{tail}{unblock}", args)


def _blocked_mark(t: dict) -> str:
    """「现在可以做」条目上的阻塞标记。

    状态=阻塞的任务也会出现在可做列表里（先解阻塞也算推进，与 /today 同口径），
    但必须带出来 —— 2026-10-19 实测：不带标记时代理把它当普通待办直接开工，
    而同一条任务在 /today、交接行里都带着阻塞标记。
    """
    if t.get("status") != "阻塞":
        return ""
    reason = t.get("blocked_reason") or ""
    return f" · ⛔ 阻塞中{'：' + reason if reason else ''}（先解阻塞，别直接开工）"


def t_next_task(args: dict) -> str:
    """现在就能动手的任务：接手时先问这个，别一上来挑个被前置挡住的"""
    pid = args.get("project_id") or ""
    # 判定全部复用 HTTP 的 /api/projects/{pid}/next。以前这里自己 _find() 读一份、
    # 再手算一遍 blocked：一次调用两次整份读盘，而且是**第二套实现** ——
    # 改了一边，代理看到的"可执行"就会和界面给的不一样（本项目反复栽的形状）。
    try:
        data = kanban.next_tasks(pid)
    except kanban.HTTPException as e:
        raise ValueError(f"{e.detail}（project_id={pid}）") from None
    name, ready, blocked = data["project"], data["next"], data["blocked"]
    if not data["total"]:
        return f"「{name}」还没有任务。可以用 kanban_add_task 建第一条。"
    lines = [f"# {name} · 现在可以做"]
    review = data.get("review") or []
    if ready:
        for t in ready:
            refs = ("（" + "、".join(t.get("refs") or []) + "）") if t.get("refs") else ""
            lines.append(f"- [{t['id']}] {t['title']} · {t.get('priority')}{refs}"
                         + _blocked_mark(t))
    elif data["done"] == data["total"]:
        lines.append(f"- 全部 {data['total']} 条任务都已完成，没有待办。")
    elif review and data["done"] + len(review) == data["total"]:
        reviewed = "、".join(f"{t['title']} [{t['id']}]" for t in review[:5])
        tail = " …" if len(review) > 5 else ""
        lines.append(f"- 全部干完了，{len(review)} 条在等人验收：{reviewed}{tail}"
                     "（验收通过后 kanban_update_task 改成已完成）")
    else:
        lines.append("- 没有可以直接开工的任务（都在等前置）")
    if blocked:
        lines.append("")
        lines.append("被前置挡住 / 阻塞中：")
        for t in blocked:
            bits = []
            waiting = t.get("waiting_for") or []
            if waiting:
                bits.append("等：" + "、".join(waiting))
            if t.get("status") == "阻塞" and t.get("blocked_reason"):
                bits.append("阻塞原因：" + t["blocked_reason"])
            tail = f"（{'；'.join(bits)}）" if bits else ""
            lines.append(f"- {t['title']}{tail}")
    return _agent_text("\n".join(lines), args)


def t_delete_task(args: dict) -> str:
    _ensure_writable()
    tid = args.get("task_id") or ""
    kanban.delete_task(tid)      # 找不到会抛 404，不必先在锁外读一整份确认存在
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


def _redact_arg(args: dict, default: bool = True) -> bool:
    """`redact` 参数：智能体拿到的东西默认**脱敏**。

    默认值取 True 是因为这些文本会进别的机器上的上下文窗口（看板自己的约定 9：
    被动外出的读取默认收口，用户主动要的润色才给原文）。
    想拿原文：显式传 `false`。类型由 inputSchema 声明 boolean、`_validate_args` 强制，
    所以这里只会收到真正的布尔值。
    """
    v = args.get("redact")
    return default if v is None else bool(v)


def t_handoff(args: dict) -> str:
    """交接包：让新接手的 AI 一次拿到全部上下文"""
    pid = args.get("project_id") or ""
    p = _find(pid)
    if not p:
        raise ValueError(f"找不到项目：{pid}")
    show = _redact_arg(args)
    md = kanban._build_handoff_markdown(p, redact=show, omit=_omit_arg(args))
    if show:
        md = kanban.redact_text(md)
    # 智能体是把整包读进上下文窗口的，报个尺寸让它知道要不要改用 kanban_context 分片取
    return md + kanban._budget_note(md)


def t_agents_md(args: dict) -> str:
    pid = args.get("project_id") or ""
    p = _find(pid)
    if not p:
        raise ValueError(f"找不到项目：{pid}")
    text, source, _ep = kanban._generate_agents_md(p)
    body = f"<!-- 来源：{source} -->\n\n{text}"
    if _redact_arg(args):
        body = kanban.redact_text(body)
    return body + kanban._budget_note(body)


def t_context(args: dict) -> str:
    """机器可读接手包：一次拿到背景/交接说明/可执行任务/代码现实/就绪度

    `sections` / `since` 原样透传给 `/api/context/{pid}` —— 切片与增量的判定只有
    `app.context_slice` 一份实现，MCP 不重算（重算就会出现"工具说变了、界面说没变"）。
    """
    pid = args.get("project_id") or ""
    sections = args.get("sections")
    since = args.get("since") or None
    # 不再自己 _find() 判存在：project_context 找不到会 404，而 _find 是**又一整份读盘**
    ctx = kanban.project_context(pid, redact=_redact_arg(args),
                                 sections=sections, since=since)
    meta = ctx.get("slice") or {}
    parts = [
        f"【{ctx['project']['name']}】接手上下文（{ctx['generated_at']}）",
        f"状态：{ctx['project']['status']}（进度 {ctx['project']['progress']}%）| 优先级：{ctx['project']['priority']} | 标签：{'、'.join(ctx['project'].get('tags') or []) or '无'}",
    ]
    if meta.get("delta"):
        parts.append(
            f"增量模式：自 {meta.get('since')} 以来只列变化条目"
            f"（任务 {meta.get('changed_tasks', 0)} 条 / 记忆 {meta.get('changed_memories', 0)} 条）；"
            "start_here、代码现实与就绪度不随 since 缩水")
    if meta.get("omitted"):
        parts.append("本次省略小节：" + "、".join(meta["omitted"]))
    cr = ctx.get("code_reality")
    if cr is not None:
        if cr.get("available"):
            parts.append(f"代码现实：分支 {cr.get('branch') or '—'}，最近提交 {cr.get('last_commit_at') or '—'}（{cr.get('days_since_commit')} 天前），未提交文件 {cr.get('uncommitted_files')} 个")
        else:
            # 读不到要说出来：以前整行不出现，"没填路径"和"git 坏了"看起来一模一样
            parts.append(f"代码现实：不可用（{cr.get('reason') or '未知原因'}）")
    if ctx["project"].get("background"):
        parts.append("项目背景：" + ctx["project"]["background"])
    if ctx["project"].get("handoff_note"):
        parts.append("交接说明（务必先读）：" + ctx["project"]["handoff_note"])
    if ctx["project"].get("next_action"):
        parts.append("下一步行动：" + ctx["project"]["next_action"])
    if "memories" in ctx:
        mems = ctx.get("memories") or []
        if mems:
            parts.append("项目记忆（决策/坑/约定，接手时自动带上）：")
            parts += [f"  - [{m.get('type')}] {m.get('text')}" for m in mems]
    def _task_line(t: dict) -> str:
        """MCP 接手包也要带验收标准；只报标题会让新智能体知道做什么、不知道做到哪算完。
        阻塞中的任务带 ⛔ 标记与原因（同 kanban_next_task 口径，别直接开工）。"""
        bits = [t["title"], f"（{t.get('priority', '中')}）"]
        refs = t.get("refs") or []
        if refs:
            bits.append(" @" + "、".join(refs))
        acc = [a for a in (t.get("acceptance") or []) if a]
        if acc:
            bits.append(" — 验收：" + "；".join(acc))
        bits.append(_blocked_mark(t))
        return "".join(bits)

    if "start_here" in ctx:
        if ctx["start_here"]:
            parts.append("现在就能动手的任务：")
            parts += [f"  - {_task_line(t)}" for t in ctx["start_here"]]
        else:
            parts.append("现在就能动手的任务：无（都完成了或在等前置）")
    tasks = ctx.get("tasks")
    if tasks is not None:
        todo = tasks["todo"]
        doing = tasks["in_progress"]
        review = tasks.get("review") or []
        blocked = tasks["blocked"]
        if doing:
            parts.append("进行中：" + "；".join(_task_line(t) for t in doing))
        if review:
            parts.append("待审查：" + "；".join(_task_line(t) for t in review))
        if todo:
            parts.append("待办：" + "；".join(_task_line(t) for t in todo))
        if blocked:
            parts.append("阻塞：" + "；".join(_task_line(t) for t in blocked))
    rd = ctx.get("readiness")
    if rd is not None:
        parts.append(f"交接就绪度：{rd['score']}/100" + (f"（缺口：{'、'.join(rd['missing'])}）" if rd["missing"] else "（齐备）"))
    # 直链也要带上这次同样的脱敏/切片口径：不然是"工具给了切片版、链接指向整包"。
    query = []
    if _redact_arg(args):
        query.append("redact=1")
    if sections is not None:
        query.append("sections=" + quote(",".join(sections), safe=","))
    if since:
        query.append("since=" + quote(str(since), safe=""))
    link_suffix = ("?" + "&".join(query)) if query else ""
    parts.append(f"实时上下文（本机服务，活数据）：{local_url('/api/context/' + pid)}{link_suffix}")
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
        mark = (f"（⛔ 阻塞中：{t['blocked_reason']}，先解）"
                if t.get("status") == "阻塞" and t.get("blocked_reason")
                else ("（阻塞中，先解）" if t.get("status") == "阻塞" else ""))
        lines.append(f"  [{t['priority']}]{mark} {t['title']} —— {t['project_name']}{due}{refs}")
    return _agent_text("\n".join(lines), args)


def t_search_tasks(args: dict) -> str:
    """全局任务搜索（与 Ctrl+K 走同一个接口）"""
    lim = int(args.get("limit") or 30)
    r = kanban.search_tasks(q=args.get("query") or "", limit=lim)
    hits, truncated = r["results"], r.get("truncated", False)
    if not hits:
        return f"没有匹配「{args.get('query')}」的任务。"
    # 以前这里按"50"这个常量决定要不要留痕，而默认 limit 是 30 ——
    # 于是命中 30 条时说"就这么些"，命中 100 条时说"上限 50"，两个数都是猜的。
    # 现在照接口自己说的算。
    head = f"匹配 {len(hits)} 条任务"
    if truncated:
        head += f"（已到本次上限 {r.get('limit', lim)}，后面还有 —— 换个更具体的关键词试试）"
    # 注意：搜索接口用的是 task_id（today 用的是 id），这里按它自己的字段取
    lines = [head + "："] + [
        f"  [{t.get('task_id')}] {t['title']} —— {t['project_name']}（{t['status']}/{t['priority']}）"
        for t in hits]
    return _agent_text("\n".join(lines), args)


def t_log_progress(args: dict) -> str:
    """给任务追加一条过程记录（做了什么 / 改了什么决策 / 踩了什么坑）"""
    _ensure_writable()
    tid = args.get("task_id") or ""
    # 复用 JournalIn 那一份校验（非空 + 长度上限），别绕过去自己信客户端
    text = kanban.JournalIn(text=args.get("text") or "").text
    # 一次读改写就拿到标题与新记录；原来为了报标题，会先在锁外整份读一遍
    try:
        task = kanban.append_task_journal(tid, text)
    except kanban.HTTPException as e:
        raise ValueError(f"{e.detail}（task_id={tid}）") from None
    entry = task["journal"][-1]
    # 同上：括号里是**存量任务标题**，不是这次调用方给的内容（正文才是），所以必须过出口
    return _agent_text(f"已记录到任务 [{tid}]（{task.get('title')}）：{entry.get('text')}", args)


def t_memory_add(args: dict) -> str:
    """往项目记忆层写一条决策/坑/约定 —— 交接包与 AI 监管会自动召回它"""
    _ensure_writable()
    res = kanban.add_memory(kanban.MemoryIn(
        pid=args.get("project_id") or "",
        type=args.get("type") or "事实",
        text=args.get("text") or "",
        source="MCP",
        expires=args.get("expires") or "",
    ))
    mem = res["memory"]
    # deduped 时 mem 就是**盘上已有那条**（2026-10-19 起 —— 以前返回的是刚构造的新
    # entry，id 是盘上不存在的，拿去 kanban_delete_memory 会 404）。回执说清 id，
    # 调用方要撤销/引用这一条都得靠它。
    if res.get("deduped"):
        return f"这条记忆已存在（同项目同类型同文本不重复入），未写入；已有 id {mem.get('id')}。"
    where = mem.get("pid") or "（未指定项目）"
    # 清洗丢了东西要说话：expires 格式不对会被留空 = 变成"长期有效"，
    # 调用方以为设了保质期，实际这条会永远被召回（与 due_date="下周三" 同一类说谎的回执）
    exp = mem.get("expires") or ""
    raw_exp = (args.get("expires") or "").strip()
    exp_note = ""
    if raw_exp and not exp:
        exp_note = f"（expires「{raw_exp[:40]}」没认出来，已按长期有效处理：只接受 YYYY-MM-DD）"
    elif exp:
        exp_note = f"（有效期到 {exp}，过期后不再召回/进交接包）"
    return f"已记下 [{mem['type']}] [{mem['id']}] 到 {where}：{mem['text']}{exp_note}"


MEMORY_LIST_LIMIT = 50


def t_list_memories(args: dict) -> str:
    """查记忆层：可按项目 / 类型 / 关键词过滤（以前智能体只能盲写，写完看不见）"""
    pid = args.get("project_id") or ""
    rows = kanban.list_memories(pid=pid, q=args.get("query") or "",
                                mtype=args.get("type") or "")["memories"]
    if not rows:
        scope = f"项目 [{pid}]" if pid else "看板"
        return f"{scope}的记忆层是空的（或没有匹配的）。可以用 kanban_memory_add 记决策/坑/约定。"
    show = args.get("redact") is not False
    lines = [f"记忆 {len(rows)} 条（新的在前）："]
    for m in rows[:MEMORY_LIST_LIMIT]:
        text = kanban.redact_text(m.get("text") or "") if show else (m.get("text") or "")
        exp = f" · ⚠️已于 {m['expires']} 过期" if m.get("expired") else (
            f" · 有效期至 {m['expires']}" if m.get("expires") else "")
        # 尺寸按**实际给出去的那份文本**算（脱敏会改长度）：报了 120 字却给出 90 字，
        # 消费方的预算就白做了。参见硬性约定「条数截断要报数」的同款纪律。
        lines.append(f"- [{m.get('id')}]【{m.get('type')}】{m.get('time')} "
                     f"· {m.get('pid') or '通用'} · {kanban.size_note(text)}{exp} · {text}")
    if len(rows) > MEMORY_LIST_LIMIT:
        # 未列出的那批也报成本：消费方据此决定"要不要换个更窄的过滤条件再拉一次"，
        # 而不是盲目 project_id="" 拉全量
        rest = [kanban.text_size(kanban.redact_text(m.get("text") or "") if show
                                 else (m.get("text") or ""))
                for m in rows[MEMORY_LIST_LIMIT:]]
        lines.append(f"…… 另有 {len(rest)} 条未列出（合计 ≈{sum(r['chars'] for r in rest)} 字/"
                     f"≈{sum(r['est_tokens'] for r in rest)} tok）："
                     "用 project_id / type / query 收窄，别一次全塞进上下文。")
    return "\n".join(lines)


def t_delete_memory(args: dict) -> str:
    _ensure_writable()
    mid = args.get("memory_id") or ""
    try:
        kanban.delete_memory(mid)
    except kanban.HTTPException as e:
        raise ValueError(f"{e.detail}（memory_id={mid}；先用 kanban_list_memories 看现有条目）") from None
    return f"已删除记忆 [{mid}]"


def t_delete_project(args: dict) -> str:
    """删项目 —— 整个看板里破坏性最大的一次调用，所以要求显式 confirm"""
    _ensure_writable()
    pid = args.get("project_id") or ""
    found = kanban.find_project(pid)
    if not found:
        raise ValueError(f"找不到项目：{pid}（kanban_list_projects 可以看现有项目）")
    project = found[1]
    tasks = project.get("tasks") or []
    unfinished = [t for t in tasks if t.get("status") != "已完成"]
    mems = [m for m in kanban._load_memories() if m.get("pid") == pid]

    if args.get("confirm") is not True:
        # 没有软删除、没有回收站：项目连同任务一起消失，孤儿记忆也会被顺手清掉。
        # 想回来只能事后用备份**整份**恢复 —— 那会把别的项目也一起回滚，所以这里先拒绝一次。
        raise ValueError(
            f"删除项目「{project['name']}」会一起失去 {len(tasks)} 条任务"
            f"（{len(unfinished)} 条还没做完）和 {len(mems)} 条项目记忆，"
            f"且无法单独撤销：只能从备份整份恢复（会连其它项目一起回滚）。"
            f"确认就带 confirm=true 再调一次。")
    kanban.delete_project(pid)
    return (f"已删除项目 [{pid}]「{project['name']}」"
            f"（连带 {len(tasks)} 条任务、{len(mems)} 条记忆）")


def t_backup(args: dict) -> str:
    """给整块板拍一份快照（批量改动前的保险）"""
    # 故意不挂 _ensure_writable：它只把数据拷出去，一行看板内容都不改，
    # 而"动手前先留一份退路"恰恰是只读模式下最该保留的动作。
    res = kanban.create_backup()
    return (f"已创建快照 {Path(res['folder']).name}（含 {'、'.join(res['files']) or '无文件'}；"
            f"当前共 {res['total']} 份，只保留最近 10 份）。"
            "恢复要人在看板「数据」面板里点 —— 代理自动回滚整块板会吞掉你可能想救的内容。")


def t_list_backups(args: dict) -> str:
    rows = kanban.list_backups()["backups"]
    if not rows:
        return "还没有任何快照。批量改动前可以先调 kanban_backup。"
    lines = [f"快照 {len(rows)} 份（新的在前，只保留最近 10 份）："]
    for b in rows:
        lines.append(f"- {b['time']} · {b['name']} · {'、'.join(b.get('files') or []) or '（空）'}")
    lines.append("恢复只由人在界面上操作，没有对应的 MCP 工具（这是有意的）。")
    return "\n".join(lines)


def t_export_all(args: dict) -> str:
    """全项目合并交接包（与 GET /api/export-all 同一份渲染）"""
    md = kanban.build_export_all(_redact_arg(args), _omit_arg(args))
    return md + kanban._budget_note(md)


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
            "properties": {"project_id": {"type": "string", "description": "项目 id，可从 kanban_list_projects 获取"},
                           "redact": REDACT_SCHEMA},
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
                "client_ref": CLIENT_REF_SCHEMA,
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
                **_add_remove_params("tags", "标签"),
            },
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_add_task",
        "description": "给项目加一个任务。refs 填相关代码位置（如 src/app.py:120），接手的人不用再翻一遍找在哪改；"
                       "acceptance 填验收标准（每条一个『怎么算完成』），交接包会带着走。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "title": {"type": "string"},
                "desc": {"type": "string", "description": "补充说明（与验收标准分开，验收标准用 acceptance）"},
                "status": {"type": "string", "enum": ["待办", "进行中", "待审查", "已完成", "阻塞"]},
                "priority": {"type": "string", "enum": ["高", "中", "低"]},
                "agent": {"type": "string", "description": "负责的 AI 或人"},
                "refs": {"type": "array", "items": {"type": "string"},
                         "description": "相关代码位置，如 [\"app/main.py:120\", \"tests/test_api.py\"]"},
                "depends_on": {"type": "array", "items": {"type": "string"},
                               "description": "前置任务 id（同一项目内），用于排「先做哪个」"},
                "acceptance": {"type": "array", "items": {"type": "string"},
                               "description": "验收标准（怎么算完成），如 [\"全部用例绿\", \"README 补用法\"]；"
                                              "最多 10 条、单条 120 字，超出部分会被丢弃并在回执说明"},
                "due_date": {"type": "string",
                             "description": "截止日期 YYYY-MM-DD，留空表示未设置"},
                "blocked_reason": {"type": "string", "maxLength": kanban.MAX_BLOCKED_REASON,
                                   "description": "为什么被卡住（只在状态为「阻塞」时保留；离开阻塞自动清空）"},
                "client_ref": CLIENT_REF_SCHEMA,
            },
            "required": ["project_id", "title"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_update_task",
        "description": "更新任务（只传要改的）。开始干就把状态改成进行中，干完改成待审查等人验收，验收通过改成已完成，被卡住改成阻塞。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "desc": {"type": "string"},
                "status": {"type": "string", "enum": ["待办", "进行中", "待审查", "已完成", "阻塞"]},
                "priority": {"type": "string", "enum": ["高", "中", "低"]},
                "agent": {"type": "string"},
                "refs": {"type": "array", "items": {"type": "string"},
                         "description": "相关代码位置；不改就省略"},
                "depends_on": {"type": "array", "items": {"type": "string"},
                               "description": "前置任务 id；不改就省略"},
                **_add_remove_params("refs", "相关代码位置"),
                **_add_remove_params("depends_on", "前置依赖"),
                "acceptance": {"type": "array", "items": {"type": "string"},
                               "description": "验收标准（整批覆盖）；不改就省略，追加用 acceptance_add"},
                **_add_remove_params("acceptance", "验收标准"),
                "due_date": {"type": "string",
                             "description": "截止日期 YYYY-MM-DD；不改就省略（传空串等于清掉截止日期）"},
                "blocked_reason": {"type": "string", "maxLength": kanban.MAX_BLOCKED_REASON,
                                   "description": "为什么被卡住（改成阻塞时带上；恢复其他状态会自动清空，历史留在过程日志）"},
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
            "properties": {"project_id": {"type": "string"},
                           "redact": REDACT_SCHEMA},
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
        "description": "导出项目的完整交接包（Markdown）：背景、交接说明、下一步、任务清单。适合在换会话/换工具时一次性读完上下文。默认脱敏。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"},
                           "redact": REDACT_SCHEMA,
                           "omit_sections": OMIT_SECTIONS_SCHEMA},
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_agents_md",
        "description": "生成该项目的 AGENTS.md 正文（AI 不可用时自动用本地模板），可写进仓库给编码代理读。默认脱敏。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"},
                           "redact": REDACT_SCHEMA},
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_context",
        "description": "获取项目的机器可读接手包：背景、交接说明、现在就能动手的任务（依赖已满足）、代码现实、交接就绪度。换人/换工具接手时先调这个，一次拿到全部上下文。默认脱敏；可用 sections 只取需要的小节、since 只取该时刻之后变化的条目（上下文省着用）。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"},
                           "redact": REDACT_SCHEMA,
                           "sections": CONTEXT_SECTIONS_SCHEMA,
                           "since": {"type": "string",
                                     "description": "只列该时刻之后变化的 tasks / memories（YYYY-MM-DD 或 YYYY-MM-DD HH:MM）；start_here / 就绪度 / 代码现实仍全量"}},
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
                "redact": REDACT_SCHEMA,
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
                # 上限与 search_tasks 内部那道 min(50) 一致：以前这里写 200，
                # 客户端要 150 条拿回 50 条、还以为是"就这么多"。
                "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                          "description": "最多返回多少条，默认 30，上限 50"},
                "redact": REDACT_SCHEMA,
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
                "text": {"type": "string", "maxLength": kanban.MAX_JOURNAL_TEXT,
                         "description": f"一条过程记录，具体一点（决定/原因/影响）。"
                                        f"上限 {kanban.MAX_JOURNAL_TEXT} 字 —— "
                                        "与落盘长度同一个数，超限会被拒而不是悄悄截断；"
                                        "长内容请拆成几条记"},
            },
            "required": ["task_id", "text"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_memory_add",
        "description": "往项目记忆层记一条事实：决策/坑/约定。与任务不同，记忆会在交接包、AGENTS.md 和 AI 监管里自动召回。"
                       "有保质期的事实（『当前用 X 方案』『这把 key 用到月底』）给 expires，过期后不再召回、不再进交接包。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "留空表示跨项目的通用记忆"},
                "type": {"type": "string", "enum": ["决策", "坑", "约定", "事实"]},
                "text": {"type": "string", "maxLength": 2000},
                "expires": {"type": "string",
                            "description": "有效期到 YYYY-MM-DD（当天仍有效）；留空表示长期有效。"
                                           "格式不对会被留空（变成长期有效）并在回执说明"},
            },
            "required": ["text"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_list_memories",
        "description": "查项目记忆层（决策/坑/约定），可按项目、类型、关键词过滤。"
                       "写完记忆后调它确认进去了；也可用来回顾一个项目的历史约束。默认脱敏。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "留空表示看全部（含跨项目的通用记忆）"},
                "type": {"type": "string", "enum": ["决策", "坑", "约定", "事实"]},
                "query": {"type": "string", "description": "按正文关键词过滤"},
                "redact": REDACT_SCHEMA,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "kanban_delete_memory",
        "description": "删掉一条已过期的项目记忆（先用 kanban_list_memories 拿到它的 id）。"
                       "记忆是长期复用的，删错了不会体现在任何项目页面上。",
        "inputSchema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_delete_project",
        "description": "删除整个项目：连带它的任务、过程轨迹与该项目记忆一起消失，且不能单独撤销。"
                       "第一次调用（不带 confirm）只会告诉你将失去什么，确认后再带 confirm=true。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "confirm": {"type": "boolean",
                            "description": "必须显式传 true 才会真删；不传只返回影响清单"},
            },
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "kanban_export_all",
        "description": "导出**所有项目**合并成的交接包（Markdown）：换机器、写给同事接手、"
                       "或一次读完整个看板时用。默认脱敏；单个项目请用 kanban_handoff。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "redact": REDACT_SCHEMA,
                "omit_sections": OMIT_SECTIONS_SCHEMA,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "kanban_backup",
        "description": "给整个看板数据拍一份快照（projects/config/memories/监管报告），"
                       "批量改动或删除项目前先调它。不改任何看板内容，只留一份退路；"
                       "恢复由人在界面上点，没有对应的工具。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "kanban_list_backups",
        "description": "列出已有快照的时间与内容（只保留最近 10 份）。需要回滚时把这些报给用户，"
                       "由用户在「数据」面板里选一份恢复。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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
    "kanban_list_memories": t_list_memories,
    "kanban_delete_memory": t_delete_memory,
    "kanban_delete_project": t_delete_project,
    "kanban_export_all": t_export_all,
    "kanban_backup": t_backup,
    "kanban_list_backups": t_list_backups,
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
    "kanban_list_memories": ("read", "查项目记忆"),
    "kanban_delete_memory": ("destructive", "删除项目记忆"),
    "kanban_delete_task": ("destructive", "删除任务"),
    "kanban_delete_project": ("destructive", "删除整个项目"),
    "kanban_export_all": ("read", "导出全项目交接包"),
    # snapshot：不改看板内容，但也不是"读"—— 每次调用都会新建一份文件，所以
    # 不能挂着 idempotentHint=True 骗客户端；同时它在只读模式下应当保留（见 READONLY_KINDS）。
    "kanban_backup": ("snapshot", "创建数据快照"),
    "kanban_list_backups": ("read", "查看数据快照"),
}

ANNOTATIONS = {
    "read":        {"readOnlyHint": True,  "destructiveHint": False, "idempotentHint": True},
    "write":       {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    "destructive": {"readOnlyHint": False, "destructiveHint": True,  "idempotentHint": True},
    "generate":    {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    "snapshot":    {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
}

# 只读模式下仍然可见的 kinds：read 本来就是读；snapshot 只拷出不改动。
READONLY_KINDS = {"read", "snapshot"}

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
    return [t for t in TOOLS
            if TOOL_KIND.get(t["name"], ("write",))[0] in READONLY_KINDS]


# ---------------- resources：把交接内容摆成"客户端可挑的资源" ----------------
#
# 为什么不干脆再加三个工具：工具目录每一轮都占模型的上下文，而"交接包"这类内容
# 通常只在一开始读一次。resources 在客户端（Claude Desktop 的 @ 菜单、Cursor、
# Continue 的 @mcp）是**人选**的入口，不进目录 —— 同一份内容换个摆法，不扩工具面。
# 读出来的文本一律走同一批 handler，不复刻第二套渲染逻辑。

RESOURCE_INDEXES = [
    ("kanban://projects", "看板项目总览", "text/plain",
     "所有项目的状态、进度、任务数与就绪度一览"),
    ("kanban://board/today", "现在就能动手的任务", "text/plain",
     "跨项目的可执行任务，按优先级与截止日期排序"),
]
RESOURCE_TEMPLATES = [
    ("kanban://project/{pid}/handoff", "项目交接包（Markdown）", "text/markdown"),
    ("kanban://project/{pid}/context", "项目机器可读接手包（JSON）", "application/json"),
]
RESOURCE_LIMIT = 200          # 项目多到一定程度，列出来的资源就没法挑了；与工具目录同一考量


def _uri_kind(uri: str) -> tuple[str | None, str]:
    """uri → (种类, 项目 id)；认不出来返回 (None, "")。"""
    if uri == "kanban://projects":
        return "projects", ""
    if uri == "kanban://board/today":
        return "today", ""
    m = re.match(r"^kanban://project/([^/]+)/(handoff|context)$", uri or "")
    return (m.group(2), m.group(1)) if m else (None, "")


def list_resources() -> list[dict]:
    out = [{"uri": u, "name": n, "mimeType": mt, "description": d}
           for u, n, mt, d in RESOURCE_INDEXES]
    rows = _board_rows()
    for p in rows[:RESOURCE_LIMIT]:
        pid = p["id"]
        out.append({"uri": f"kanban://project/{pid}/handoff",
                    "name": f"{p['name']} · 交接包", "mimeType": "text/markdown",
                    "description": "完整交接包（已脱敏）：背景、交接说明、下一步、按依赖排序的任务清单"})
        out.append({"uri": f"kanban://project/{pid}/context",
                    "name": f"{p['name']} · 接手上下文", "mimeType": "application/json",
                    "description": "机器可读接手包（已脱敏）：可执行任务、代码现实、就绪度缺口、记忆"})
    if len(rows) > RESOURCE_LIMIT:
        # 静默截断 = 代理以为看板只有前 200 个项目。结构化列表没地方写脚注，
        # 所以占一条说明性条目：它不可读（`_uri_kind` 不认这个 uri），
        # 但任何遍历 name 的客户端都会看见这句。
        out.append({"uri": "kanban://truncated", "name":
                    f"⚠ 另有 {len(rows) - RESOURCE_LIMIT} 个项目未列出（上限 {RESOURCE_LIMIT}）"
                    f"：全量请用 kanban_list_projects，知道 id 就直接读 kanban://project/<id>/handoff",
                    "mimeType": "text/plain", "description": "截断提示条目，不可读"})
    return out


def read_resource(uri: str) -> dict:
    kind, pid = _uri_kind(uri)
    if kind == "projects":
        text, mime = t_list_projects({}), "text/plain"
    elif kind == "today":
        text, mime = t_today({}), "text/plain"
    elif kind == "handoff":
        text, mime = t_handoff({"project_id": pid}), "text/markdown"
    elif kind == "context":
        text = json.dumps(kanban.project_context(pid, redact=1), ensure_ascii=False, indent=1)
        mime = "application/json"
    else:
        raise ValueError(f"没有这个资源：{uri}（先 resources/list 看看有哪些）")
    return {"contents": [{"uri": uri, "mimeType": mime, "text": text}],
            "ttlMs": 60000, "cacheScope": "public"}


# ---------------- prompts：把"怎么接手 / 怎么收尾"摆成用户可点的指令 ----------------
#
# 这三段以前只写在 initialize 的 instructions 里，客户端不会把它显示成入口。
# 摆成 prompts 之后用户能一键插入，正文仍然只引用现有工具与资源，不含任何新能力。

PROMPTS = {
    "takeover": {
        "title": "接手一个项目",
        "description": "读某个项目的交接上下文，先复述约束再动手",
        "arguments": [{"name": "project", "description": "项目名或 id", "required": True}],
    },
    "daily": {
        "title": "今天做什么",
        "description": "跨项目列出可以立刻动手的任务并排好顺序",
        "arguments": [],
    },
    "wrapup": {
        "title": "干完一段活写回看板",
        "description": "更新任务状态/进度/下一步，并把决策与坑记进项目记忆",
        "arguments": [{"name": "project", "description": "项目名或 id", "required": True}],
    },
}


def _resolve_project(ref: str) -> dict:
    hits = _match_by_name(ref)
    if not hits:
        raise ValueError(f"找不到项目「{ref}」；kanban://projects 或 kanban_list_projects 可以看现有项目")
    if len(hits) > 1:
        raise ValueError("项目名有歧义，请给 id：" + "、".join(f"{h['name']}[{h['id']}]" for h in hits[:6]))
    return hits[0]


def list_prompts() -> list[dict]:
    return [{"name": k, "title": v["title"], "description": v["description"],
             "arguments": v["arguments"]} for k, v in PROMPTS.items()]


def get_prompt(name: str, args: dict) -> dict:
    spec = PROMPTS.get(name or "")
    if not spec:
        raise ValueError(f"没有这个提示词：{name}；可用：{'、'.join(PROMPTS)}")
    if name == "daily":
        text = ("下面是看板上「现在就能动手」的任务（依赖已满足、自己未完成），"
                "按优先级和截止日期排过序。挑一条开始做，做完用 kanban_update_task 改状态，"
                "别一次领多条。\n\n" + t_today({}))
    else:
        p = _resolve_project(str(args.get("project") or ""))
        pid = p["id"]
        if name == "takeover":
            text = (f"接手项目「{p['name']}」（id {pid}）。先读它的交接上下文："
                    "kanban_context 或资源 kanban://project/" + pid + "/context，"
                    "然后向用户复述三件事——背景与约束、交接说明里点过的坑、你现在要从哪条任务开始"
                    "（用 start_here / kanban_next_task，别挑被前置挡住的）。"
                    "复述完得到确认再动手写代码。")
        else:
            text = (f"项目「{p['name']}」（id {pid}）这一段活干完了，按看板约定写回："
                    "1) 用 kanban_update_task 把做完的任务改成「已完成」（状态变化会自动进过程轨迹）；"
                    "2) 用 kanban_log_progress 给还在做的任务留一条「改了什么决策/踩了什么坑」；"
                    "3) 有长期价值的结论用 kanban_memory_add 记成 决策/坑/约定；"
                    "4) 用 kanban_update_project 更新 progress 与 next_action，"
                    "next_action 要写成下一条能直接开工的具体动作。")
    return {"description": spec["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


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
        if typ == "string" and isinstance(val, str):
            # 整数有 minimum/maximum，字符串的 maxLength 以前**只写在 schema 里没人执行** ——
            # 于是"声明收 500、实际收 5000 再被落盘截掉"这种事两侧都不报错。
            # 声明了什么就执行什么，这条对 integer 和 string 应当同等成立。
            if "maxLength" in spec and len(val) > spec["maxLength"]:
                raise ValueError(f"{key} 最长 {spec['maxLength']} 字，收到 {len(val)} 字"
                                 f"（长内容请拆成多次调用，不要指望被截断后还完整）")
            if "minLength" in spec and len(val.strip()) < spec["minLength"]:
                raise ValueError(f"{key} 至少 {spec['minLength']} 字")
        if typ == "integer":
            if isinstance(val, bool) or not isinstance(val, int):
                raise ValueError(f"{key} 必须是整数")
            if "minimum" in spec and val < spec["minimum"]:
                raise ValueError(f"{key} 不能小于 {spec['minimum']}")
            if "maximum" in spec and val > spec["maximum"]:
                raise ValueError(f"{key} 不能大于 {spec['maximum']}")
        if typ == "array" and not isinstance(val, list):
            raise ValueError(f"{key} 必须是数组")
        if typ == "array" and isinstance(val, list):
            # items 里的 enum 以前**只写在 schema 里没人执行** —— 客户端传
            # ["memories","背景"] 会被原样收下，工具再默默当成"没传"或漏掉一项。
            # 声明了什么就执行什么（同 string 的 maxLength，2026-10-19 收口）。
            if "minItems" in spec and len(val) < spec["minItems"]:
                raise ValueError(f"{key} 至少 {spec['minItems']} 项")
            if "maxItems" in spec and len(val) > spec["maxItems"]:
                raise ValueError(f"{key} 最多 {spec['maxItems']} 项")
            item_spec = spec.get("items") or {}
            item_type = item_spec.get("type")
            item_enum = item_spec.get("enum")
            for idx, item in enumerate(val):
                if item_type == "string" and not isinstance(item, str):
                    raise ValueError(f"{key}[{idx}] 必须是字符串")
                if item_type == "integer" and (isinstance(item, bool) or not isinstance(item, int)):
                    raise ValueError(f"{key}[{idx}] 必须是整数")
                if item_enum and item not in item_enum:
                    raise ValueError(f"{key}[{idx}] 只能是 {'/'.join(item_enum)}，收到「{item}」")
        if typ == "boolean" and not isinstance(val, bool):
            raise ValueError(f"{key} 必须是布尔值（收到「{val}」）")
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
            "capabilities": CAPABILITIES,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": ("这是「AI 交接看板」：管理多个项目的状态与交接上下文。"
                             "开工前先 kanban_list_projects，再用 kanban_get_project 或 kanban_handoff 读上下文；"
                             "干完一段活请用 kanban_update_project 更新 progress 与 next_action。"
                             f"需要最新数据时也可以直接 GET {local_url('/api/context/<项目id>?redact=1')}"
                             "（本机服务，取到的是活的，不是快照；带 redact=1 是脱敏版）。"),
        })

    if method in ("notifications/initialized", "initialized"):
        return None                      # 握手通知，无需回包

    if method == "ping":
        return _ok(mid, {})

    if method in ("server/discover", "discover"):
        # 无状态模式下客户端靠 discover 选版本，规范（2026-07-28）要求回 `supportedVersions`
        # —— 只报单个 protocolVersion 等于把协商的余地全给了对面，现代客户端可能直接选不到版本。
        # 同时 `resultType:"complete"` 的结果必须带缓存提示；serverInfo 走 `_meta`，
        # 顶层那份仍留着给停在 2025-xx 的客户端。
        return _ok(mid, {
            "protocolVersion": DEFAULT_VERSION,
            "supportedVersions": SUPPORTED_VERSIONS,
            "capabilities": CAPABILITIES,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "_meta": {"io.modelcontextprotocol/serverInfo":
                      {"name": SERVER_NAME, "version": SERVER_VERSION}},
            "tools": visible_tools(),
            "ttlMs": 60000, "cacheScope": "public",
        })

    if method == "tools/list":
        return _ok(mid, {"tools": visible_tools(), "ttlMs": 60000, "cacheScope": "public"})

    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            # 「工具不存在」是**调用方的形状错误**，规范给的是 JSON-RPC -32602；
            # 工具跑了但失败（含入参校验不过）才是 isError —— 那条要让模型自己纠。
            # 原来两者都走 isError，于是客户端把"名字写错"当成"服务里这个工具坏了"。
            return _err(mid, -32602, f"未知工具：{name}", {"可用工具": sorted(TOOL_BY_NAME)})
        if not isinstance(args, dict):
            return _err(mid, -32602, "params.arguments 必须是对象")
        try:
            _validate_args(TOOL_BY_NAME[name].get("inputSchema") or {}, args)
            return _ok(mid, _tool_content(handler(args), args))
        except Exception as e:
            log(f"工具 {name} 执行失败：{traceback.format_exc()}")
            detail = getattr(e, "detail", None) or str(e) or type(e).__name__
            return _ok(mid, _tool_content(f"执行失败：{detail}", args, is_error=True))

    if method == "resources/list":
        return _ok(mid, {"resources": list_resources(), "ttlMs": 60000, "cacheScope": "public"})

    if method == "resources/templates/list":
        return _ok(mid, {"resourceTemplates": [
            {"uriTemplate": u, "name": n, "mimeType": mt,
             "description": "按项目 id 取；pid 从 kanban_list_projects 或 kanban://projects 拿"}
            for u, n, mt in RESOURCE_TEMPLATES],
            "ttlMs": 60000, "cacheScope": "public"})

    if method == "resources/read":
        uri = params.get("uri") or ""
        try:
            return _ok(mid, read_resource(uri))
        except ValueError as e:
            # 规范：没有这个资源回 -32602（旧客户端可能仍按 -32002 找，但我们只发一种）
            return _err(mid, -32602, str(e))

    if method == "prompts/list":
        return _ok(mid, {"prompts": list_prompts(), "ttlMs": 60000, "cacheScope": "public"})

    if method == "prompts/get":
        try:
            return _ok(mid, get_prompt(params.get("name") or "", params.get("arguments") or {}))
        except ValueError as e:
            return _err(mid, -32602, str(e))

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
