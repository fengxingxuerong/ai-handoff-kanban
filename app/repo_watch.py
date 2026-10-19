# -*- coding: utf-8 -*-
"""仓库变化雷达：只读 git，发现变化后给建议，不静默改进度。

设计边界：
- git 只能证明“代码变了”，不能证明“完成了多少”；
- progress 是人工承诺值，必须由用户/代理显式确认后才写入；
- 基线持久化在 data_dir/repo_watch.json（项目删除时用 forget() 清对应条目），
  确认事件追加到 repo_audit.jsonl —— 追加型存储，行数有上限
  （超 MAX_AUDIT_LINES 裁到 KEEP_AUDIT_ON_TRIM，硬性约定 11）。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from .filelock import blocking_lock
from .gitinfo import git_snapshot
from .paths import data_dir

_LOCK = threading.RLock()

# 确认审计（repo_audit.jsonl）是追加型存储：用户每点一次「确认并同步/标记已读」落一行，
# 不设上限就会一直涨，而 `recent_audit` 在每次巡检时都**整读**它（硬性约定 11）。
# 一行约几百字节，2000 行 ≈ 1MB 以内；触及上限前需要用户确认 2000 次。
# 语义说明：裁剪丢的是最老的确认记录 —— 这是"最近发生了什么"的窗口，不是不可删的账本。
MAX_AUDIT_LINES = 2000
KEEP_AUDIT_ON_TRIM = 1000


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _state_file() -> Path:
    return data_dir() / "repo_watch.json"


def _audit_file() -> Path:
    return data_dir() / "repo_audit.jsonl"


def _load_state() -> dict:
    f = _state_file()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    f = _state_file()
    tmp = f.with_suffix(".json.tmp")
    with _LOCK:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(state, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, f)


def _trim_audit(f: Path) -> None:
    """滚动裁剪 repo_audit.jsonl，只保留最近 KEEP_AUDIT_ON_TRIM 条。

    只在**超过**上限时才改写文件：常态是纯追加，白读一遍 + replace 是写放大。
    """
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    if len(lines) <= MAX_AUDIT_LINES:
        return
    tmp = f.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines[-KEEP_AUDIT_ON_TRIM:]) + "\n", encoding="utf-8")
    os.replace(tmp, f)


def _signature(snap: dict) -> str:
    dirty = "|".join(sorted(str(x) for x in (snap.get("dirty_files") or [])))
    raw = f"{snap.get('branch', '')}|{(snap.get('head') or {}).get('hash', '')}|{snap.get('dirty_count', 0)}|{dirty}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def task_completion(project: dict) -> int | None:
    tasks = [t for t in (project.get("tasks") or []) if isinstance(t, dict)]
    if not tasks:
        return None
    done = sum(1 for t in tasks if t.get("status") == "已完成")
    return round(done * 100 / len(tasks))


STALE_DONE_LIMIT = 10


def _as_dt(s: str):
    """按分钟精度解析时间戳（now_str 与 git log 的日期格式都认）。"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def stale_done_tasks(tasks: list[dict], head: dict, limit: int = STALE_DONE_LIMIT) -> dict:
    """已完成的任务，完成之后仓库又有新提交 → 建议复核（第六轮吸取：riqor 的"证据撤销"最小切片）。

    为什么值得做：任务标完成时是有人**当场验过的**（acceptance 清单就是干这个的），
    之后仓库每多一次提交，这个结论就旧一分，而界面上完全看不出来 ——
    "什么时候能信任一次完成"正是 riqor 整条产品线在卖的判定。

    三条纪律（宁可不报，不猜）：
    - 只认提交时间，不认未提交改动 —— 工作区里的脏文件没有时间戳，无法证明"晚于验收"；
    - 没有 `done_at` 的老任务直接跳过（本字段 2026-10-19 才落，历史任务无从判定）；
    - **只提示，绝不自动改任何状态** —— 复核结论只能由人/代理显式写回。
    返回 `{count, items}`：count 是全部条数，items 截断到 limit（截断必须留痕，硬性约定 24）。
    """
    head_date = str((head or {}).get("date") or "")
    hd = _as_dt(head_date)
    if hd is None:
        return {"count": 0, "items": []}
    stale = []
    for t in tasks or []:
        if not isinstance(t, dict) or t.get("status") != "已完成":
            continue
        dd = _as_dt(str(t.get("done_at") or ""))
        if dd is None or dd >= hd:
            continue
        stale.append({"id": t.get("id"), "title": t.get("title", ""),
                      "done_at": t.get("done_at"), "commit_at": head_date})
    stale.sort(key=lambda x: (x["done_at"] or "", str(x["id"] or "")))
    return {"count": len(stale), "items": stale[:limit]}


def scan_project(project: dict, state: dict | None = None, use_cache: bool = False) -> dict:
    """扫描一个项目；返回变化事件。不写进度。"""
    pid = str(project.get("id") or "")
    name = str(project.get("name") or "")
    snap = git_snapshot(str(project.get("repo_path") or ""), limit=1, use_cache=use_cache)
    if not snap.get("ok"):
        return {"project_id": pid, "project_name": name, "ok": False,
                "changed": False, "error": snap.get("error") or "无法读取仓库"}

    sig = _signature(snap)
    prev = (state or {}).get(pid) or {}
    first = not prev.get("last_seen_signature")
    # 第一次见到仓库时，若已有未提交文件，也提醒一次；提交历史只建立基线。
    changed = bool(prev.get("last_seen_signature") and prev["last_seen_signature"] != sig) or (first and snap.get("dirty_count", 0) > 0)
    detected_at = (prev.get("detected_at") if prev.get("last_seen_signature") and changed else None) or (_now() if changed else None)
    completion = task_completion(project)
    current = int(project.get("progress") or 0)
    suggested = completion if (completion is not None and completion > current) else None
    reason = ""
    if changed:
        if suggested is not None:
            reason = f"代码有变化；任务完成度 {completion}%，当前进度 {current}%"
        else:
            reason = "代码有变化；请确认下一步或进度"
    return {
        "project_id": pid, "project_name": name, "ok": True,
        "changed": changed, "signature": sig,
        "branch": snap.get("branch", ""), "head": snap.get("head") or {},
        "dirty_count": snap.get("dirty_count", 0),
        "dirty_files": (snap.get("dirty_files") or [])[:10],
        "task_completion": completion, "current_progress": current,
        "suggested_progress": suggested,
        "reason": reason,
        "detected_at": detected_at,
        "last_seen_at": prev.get("last_seen_at"),
        # 「验收后代码又变了」的复核提示：与 changed 正交（提交后又被已读也照报，
        # 因为它答的是另一个问题：那几条已完成任务的验收结论还新不新）
        "stale_done": stale_done_tasks([t for t in (project.get("tasks") or [])
                                        if isinstance(t, dict)],
                                       snap.get("head") or {}),
    }


def scan_projects(projects: list[dict], use_cache: bool = False) -> list[dict]:
    """扫描项目并更新基线状态；返回事件列表。"""
    with _LOCK:
        state = _load_state()
        out = []
        now = _now()
        for p in projects:
            if not isinstance(p, dict) or not p.get("repo_path"):
                continue
            entry = scan_project(p, state, use_cache=use_cache)
            pid = entry["project_id"]
            st = state.setdefault(pid, {})
            if not st.get("last_seen_signature"):
                st["last_seen_signature"] = entry.get("signature")
                st["last_seen_at"] = now
            st["current_signature"] = entry.get("signature")
            st["current_at"] = now
            if entry.get("changed"):
                st.setdefault("detected_at", now)
            else:
                st.pop("detected_at", None)
            out.append(entry)
        _save_state(state)
    return out


def ack(pid: str, evidence: dict, before: int | None, after: int | None, note: str = "", actor: str = "human") -> dict:
    """确认当前仓库状态为已读；若 after 提供，写一条进度审计。"""
    with _LOCK:
        state = _load_state()
        now = _now()
        st = state.setdefault(str(pid), {})
        st["last_seen_signature"] = evidence.get("signature") or st.get("current_signature")
        st["last_seen_at"] = now
        st["current_signature"] = evidence.get("signature") or st.get("current_signature")
        st["current_at"] = now
        st.pop("detected_at", None)
        _save_state(state)
        audit = {
            "ts": now, "project_id": str(pid), "actor": actor,
            "before_progress": before, "after_progress": after,
            "signature": evidence.get("signature"),
            "head": (evidence.get("head") or {}).get("hash"),
            "dirty_count": evidence.get("dirty_count"),
            "dirty_files": evidence.get("dirty_files") or [],
            "task_completion": evidence.get("task_completion"),
            "note": note,
        }
        # 与 usage.jsonl 同一套：线程锁挡同进程，侧车文件锁挡第二条进程写同一份账。
        # 追加与裁剪必须在同一把锁里 —— 不然裁剪的「整读→重写」会吞掉并发追加的行。
        f = _audit_file()
        with blocking_lock(f.with_suffix(".lock"), timeout=10.0, what="仓库审计"):
            with open(f, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(audit, ensure_ascii=False) + "\n")
            _trim_audit(f)
        return audit


def forget(pids: str | list[str]) -> int:
    """清掉这些项目的基线条目（删除项目时调用），返回清掉的条数。

    与记忆层同一条纪律：孤儿条目既不显示，又会永久留在 repo_watch.json 里。
    **审计历史保留** —— repo_audit.jsonl 记的是"当时确认过什么"，属于历史事实，
    不随项目消失（它自带行数上限，不会无界增长）。
    """
    ids = {pids} if isinstance(pids, str) else {str(p) for p in pids}
    with _LOCK:
        state = _load_state()
        removed = sum(1 for p in ids if state.pop(p, None) is not None)
        if removed:
            _save_state(state)
        return removed


def replace_state(state: dict) -> int:
    """整份替换基线文件（恢复备份时调用），返回写入的条目数。

    备份是"项目数据 + 雷达基线"同一时刻的对子，恢复要一起回滚才对得上；
    非字典条目直接丢弃（读路径按 `prev.get(...)` 取值，坏形状留着没有意义）。
    调用方保证**只在来源里真有这份文件时**才调 —— 老备份没有这份文件时保持现状，
    与记忆层"来源里没有 memories.json = 不动现有记忆"同一语义。
    """
    clean = {str(k): v for k, v in state.items() if isinstance(v, dict)}
    with _LOCK:
        _save_state(clean)
    return len(clean)


def recent_audit(limit: int = 20) -> list[dict]:
    f = _audit_file()
    if not f.exists():
        return []
    rows = []
    try:
        for line in f.read_text(encoding="utf-8").splitlines()[-limit:]:
            if line.strip():
                rows.append(json.loads(line))
    except Exception:
        return []
    return rows
