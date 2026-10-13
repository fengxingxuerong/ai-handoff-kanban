# -*- coding: utf-8 -*-
"""AI 编排的「只读计算」层：给模型的看板简报与会话提炼。

**从 `app/main.py` 拆出（2026-10-06，依赖注入式，同 handoff_md/agentsmd 的做法）**。
这里只做「算出要发给模型的内容 + 调用 + 解析回执」，**不落库** —— 写动作
（apply-suggestions、前端 execSingleAction）仍走 main 的 `mutate()` 临界区。
依赖经参数注入，不 import main：

- `projects_brief(projects, *, memories_by_pid_fn, git_snapshot_fn)`：
  记忆分桶与 git 探测经回调注入（main 在调用时解析自己的全局名，
  既有的 `monkeypatch.setattr(main, "git_snapshot", ...)` 照常有效）；
- `summarize_inner(pid, text, *, find_project_fn)`：项目查找经回调注入。

`_memory_expired` 从 `app/due.py` 取（同为日期类判定），`git_snapshot` 从
`app.gitinfo`、`redact_text` 从 `app.redact`、`_extract_json` 从 `app.llm`。
"""
from __future__ import annotations

import json
import uuid

from fastapi import HTTPException

from . import llm
from .due import _memory_expired, recall_memories
from .gitinfo import git_snapshot
from .llm import _extract_json
from .redact import redact_text


# 停滞计算回调（main 注入 project_stats._stalled 的包装）；本模块不 import main，
# 未注入时给中性零值（简报形状稳定，调用方总能在 item["stalled"] 上取到结构）。
project_stats_stalled = lambda tasks: {"count": 0, "tasks": []}


def projects_brief(projects: list[dict], *, memories_by_pid_fn, git_snapshot_fn,
                   stalled_fn=None) -> list[dict]:
    """给 LLM 的精简项目数据（含代码现状，便于发现"看板说有进展、代码其实没动"）

    自由文本（背景 / 下一步 / 任务标题 / 过程记录 / 记忆）过一道脱敏再送出去：这些是要离开本机
    到模型那边的内容，而用户从会话里抄进看板的凭据很常见。
    id / 状态 / 分支名原样保留 —— 模型要把它们回传用来对齐项目与任务。
    标题现在能盖，是因为**每条任务都带 id**：以前简报里没有任务 id，提示词只能写
    "任务用现有标题匹配"，盖了标题就把建议对不回任务了（见 `ai_command` 的规则 1）。
    """
    mems_by_pid = memories_by_pid_fn()
    brief = []
    for p in projects:
        item = {
            "id": p["id"],
            "name": p["name"],
            "status": p.get("status", ""),
            "progress": p.get("progress", 0),
            "priority": p.get("priority", ""),
            "tags": p.get("tags", []),
            "next_action": redact_text((p.get("next_action") or ""))[:300],
            "background": redact_text((p.get("background") or ""))[:300],
            "tasks": [{"id": t.get("id"),
                       "title": redact_text(t.get("title") or ""),
                       "status": t.get("status", ""), "priority": t.get("priority", ""),
                       **({"due_date": t["due_date"]} if t.get("due_date") else {}),
                       **({"refs": t["refs"]} if t.get("refs") else {}),
                       **({"recent_journal": [(e.get("time", ""),
                                               redact_text((e.get("text") or ""))[:80])
                                              for e in (t.get("journal") or [])[-1:]]}
                          if t.get("journal") else {})}
                      for t in (p.get("tasks") or [])[:40]],
"memories": [{**m, "text": redact_text(m.get("text") or "")}
                         for m in recall_memories(
                             [m for m in mems_by_pid.get(p["id"], [])
                              if not _memory_expired(m)], 10)],
        }
        # 截断必须留痕：以前静默给前 40 条，模型以为"这项目的活就这些"，
        # 于是监管报告漏掉第 41 条之后的阻塞。看板自己的场景测试就造过 45 条的项目。
        total_tasks = len(p.get("tasks") or [])
        if total_tasks > 40:
            item["tasks_note"] = (f"该项目共 {total_tasks} 条任务，本简报只带前 40 条，"
                                  "其余未在本次分析范围内")
        # 停滞信号（第三轮竞品吸取）：与 git.days_since_commit 对称的"看板侧停滞"。
        # 口径唯一在 project_stats._stalled —— 两条路给不同答案是本项目反复出问题的形状。
        item["stalled"] = (stalled_fn or project_stats_stalled)(p.get("tasks") or [])
        snap = git_snapshot_fn(p.get("repo_path", ""), limit=1)
        if snap.get("ok"):
            item["git"] = {
                "branch": snap.get("branch", ""),
                "last_commit": (snap.get("head") or {}).get("hash", ""),
                "last_commit_at": (snap.get("head") or {}).get("date", ""),
                "last_commit_subject": redact_text((snap.get("head") or {}).get("subject", "")),
                "days_since_commit": snap.get("days_since_commit"),
                "uncommitted_files": snap.get("dirty_count", 0),
            }
        brief.append(item)
    return brief


def summarize_inner(pid: str, text: str, *, find_project_fn) -> dict:
    """会话提炼核心：给粘贴提炼与会话捕获共用"""
    found = find_project_fn(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    # 送出去之前先脱敏：会话日志里最常出现的就是别人贴进来的 Key / 连接串，
    # 而"提炼"这件事必须把整段对话发到模型那边去 —— 一次调用就等于把凭据发出去了。
    text = redact_text(text or "")
    p = found[1]
    existing = [t["title"] for t in p.get("tasks", [])]
    sys_msg = (
        "你是任务看板的会话提炼助手。用户会把一段与某个项目相关的 AI 对话/聊天记录粘给你，"
        "请提炼有价值的信息，输出 JSON（不要输出其他内容）：\n"
        '{"summary": "这段会话的一句话总结", '
        '"new_tasks": [{"title": "任务标题", "desc": "描述", "priority": "高/中/低", "status": "待办/进行中/待审查/已完成/阻塞"}], '
        '"next_action": "建议的下一步行动（若没有则为空字符串）", '
        '"memories": [{"type": "决策/坑/约定/事实", "text": "值得长期记住的要点"}], '
        '"handoff_note_updates": "建议补充进交接说明的要点（纯文本一段，若没有则为空字符串）"}\n'
        "规则：1) new_tasks 只收录明确提到要做、且不在现有任务清单里的事项，不要编造；"
        "2) memories 收录决策、踩坑、约定类要点，没有则为空数组；"
        "3) summary 一句话；4) 中文输出。"
    )
    user_msg = (f"项目：{p['name']}\n"
                f"现有任务：{json.dumps(existing, ensure_ascii=False)}\n\n"
                f"会话记录：\n{text}")
    batch_id = uuid.uuid4().hex      # 这一次提炼的身份证号，见下面返回处的注释
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ], temperature=0.15, feature="会话提炼")
        parsed = _extract_json(text)
        if not isinstance(parsed, dict):
            raise ValueError("提炼结果格式不正确")
        # batch_ref：前端"应用到项目"拿它当幂等键前缀。同一个弹窗里手抖点两次
        # 「✅ 应用到项目」，以前会把那批任务原样建两遍（2026-09-23 实测：弹窗关错门、
        # 按钮还在，再点一次即重复），带上键之后第二次只是重放第一次的结果。
        return {"parsed": parsed, "endpoint": ep, "batch_ref": batch_id}
    except llm.LLMError as e:
        raise HTTPException(502, f"AI 调用失败：{e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"提炼失败：{e}")
