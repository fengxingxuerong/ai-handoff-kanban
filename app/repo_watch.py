# -*- coding: utf-8 -*-
"""仓库变化雷达：只读 git，发现变化后给建议，不静默改进度。

设计边界：
- git 只能证明“代码变了”，不能证明“完成了多少”；
- progress 是人工承诺值，必须由用户/代理显式确认后才写入；
- 基线持久化在 data_dir/repo_watch.json，确认事件追加到 repo_audit.jsonl。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from .gitinfo import git_snapshot
from .paths import data_dir

_LOCK = threading.RLock()


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
        with open(_audit_file(), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(audit, ensure_ascii=False) + "\n")
        return audit


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
