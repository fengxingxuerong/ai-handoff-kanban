# -*- coding: utf-8 -*-
"""
AI 交接看板 - FastAPI 后端
提供多项目总览 + 单项目详情 + 任务管理 + 交接摘要导出
数据以 JSON 文件持久化，位于用户主目录 .ai-kanban/projects.json（可用环境变量 KANBAN_DATA_DIR 覆盖）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from . import llm

APP_NAME = "ai-kanban"
DEFAULT_STATUS = ["规划中", "进行中", "已暂停", "已完成"]
DEFAULT_TASK_STATUS = ["待办", "进行中", "已完成", "阻塞"]
DEFAULT_PRIORITY = ["高", "中", "低"]

# 合法值白名单：状态/优先级会被前端拼进 class 与文本，必须收口
_ABBR = {"规划中": "gray", "进行中": "blue", "已暂停": "orange", "已完成": "green"}


def _resource_path(rel: str) -> Path:
    """兼容 PyInstaller -F 单文件模式的资源定位"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / rel
    # 开发模式：项目根目录
    return Path(__file__).resolve().parent.parent / rel

# ---------------- 数据层 ----------------

def data_dir() -> Path:
    env = os.environ.get("KANBAN_DATA_DIR")
    if env:
        d = Path(env)
    else:
        # Windows 下优先 USERPROFILE，避免受 HOME 环境变量干扰
        home = os.environ.get("USERPROFILE") or str(Path.home())
        d = Path(home) / ".ai-kanban"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_file() -> Path:
    return data_dir() / "projects.json"


_LOCK = threading.Lock()

_SAMPLE_DATA = {
    "projects": [
        {
            "id": "sample-a",
            "name": "示例项目 A · 后台系统",
            "status": "进行中",
            "progress": 65,
            "priority": "高",
            "tags": ["全栈", "monorepo"],
            "repo_path": "D:\\work\\demo-app",
            "background": "全栈后台管理系统，monorepo 架构，已覆盖核心业务域决策。",
            "handoff_note": "接手前先看 docs 目录的架构说明；已有决策改动前先读对应文档；前端有部分页面是假实现，注意核对。",
            "next_action": "补完剩余接口联调，跑一遍全量测试基线",
            "tasks": [
                {"id": "t1", "title": "接口联调", "desc": "前后端联调剩余接口，确认数据流", "status": "进行中", "priority": "高", "agent": "", "created_at": "2026-09-01 10:00", "updated_at": "2026-09-14 09:30"},
                {"id": "t2", "title": "全量测试基线回归", "desc": "跑通全部测试并修复失败用例", "status": "待办", "priority": "高", "agent": "", "created_at": "2026-09-05 14:00", "updated_at": "2026-09-14 09:30"},
                {"id": "t3", "title": "前端假实现核对", "desc": "排查哪些页面是 setTimeout 假实现，补齐真实接口", "status": "待办", "priority": "中", "agent": "", "created_at": "2026-09-10 16:00", "updated_at": "2026-09-14 09:30"},
            ],
            "created_at": "2026-09-01 10:00",
            "updated_at": "2026-09-14 09:30",
        },
        {
            "id": "sample-b",
            "name": "示例项目 B · 内容流水线",
            "status": "进行中",
            "progress": 80,
            "priority": "中",
            "tags": ["AI 写作", "多模型流水线"],
            "repo_path": "",
            "background": "内容生产项目，多模型流水线持续演进。",
            "handoff_note": "长跑任务需与会话解耦启动；进程排查时注意别误杀流水线后台进程；重启长跑前先做编译+符号完整性冒烟。",
            "next_action": "继续验证流水线健壮性，处理下一轮生成结果质检",
            "tasks": [
                {"id": "t4", "title": "流水线健壮性验证", "desc": "断点续传、守护进程、健康池冷却等机制验证", "status": "进行中", "priority": "中", "agent": "", "created_at": "2026-09-08 11:00", "updated_at": "2026-09-14 09:30"},
                {"id": "t5", "title": "生成结果质检", "desc": "对最新一轮生成内容做通读级终审", "status": "待办", "priority": "中", "agent": "", "created_at": "2026-09-12 15:00", "updated_at": "2026-09-14 09:30"},
            ],
            "created_at": "2026-09-02 10:00",
            "updated_at": "2026-09-14 09:30",
        },
        {
            "id": "sample-c",
            "name": "示例项目 C · 文本润色工具",
            "status": "已暂停",
            "progress": 50,
            "priority": "中",
            "tags": ["文本处理"],
            "repo_path": "",
            "background": "中文文本润色工具，当前已暂停。",
            "handoff_note": "暂停中。恢复时先看项目文档确认当前实现进度与下一步计划。",
            "next_action": "暂停中，暂无下一步",
            "tasks": [],
            "created_at": "2026-08-20 10:00",
            "updated_at": "2026-09-10 09:30",
        },
        {
            "id": "sample-d",
            "name": "示例项目 D · 扫描器",
            "status": "已完成",
            "progress": 100,
            "priority": "低",
            "tags": ["安全", "对标评测"],
            "repo_path": "D:\\work\\demo-scanner",
            "background": "命令行扫描器，已完成实战评测。",
            "handoff_note": "已完结。如需继续迭代可参考实战评测结果。",
            "next_action": "已完结",
            "tasks": [],
            "created_at": "2026-07-01 10:00",
            "updated_at": "2026-08-30 09:30",
        },
    ]
}


def _sanitize(data: dict) -> dict:
    """规范化读入的数据：修正非法状态/优先级，保证前端不拿到越界值"""
    if not isinstance(data, dict):
        return {"projects": []}
    projects = data.get("projects")
    if not isinstance(projects, list):
        data["projects"] = []
        return data
    for p in projects:
        if not isinstance(p, dict):
            continue
        if p.get("status") not in DEFAULT_STATUS:
            p["status"] = "规划中"
        if p.get("priority") not in DEFAULT_PRIORITY:
            p["priority"] = "中"
        try:
            p["progress"] = max(0, min(100, int(p.get("progress", 0))))
        except (TypeError, ValueError):
            p["progress"] = 0
        if not isinstance(p.get("tags"), list):
            p["tags"] = []
        tasks = p.get("tasks")
        if not isinstance(tasks, list):
            p["tasks"] = tasks = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            if t.get("status") not in DEFAULT_TASK_STATUS:
                t["status"] = "待办"
            if t.get("priority") not in DEFAULT_PRIORITY:
                t["priority"] = "中"
    return data


def _load_data() -> dict:
    f = data_file()
    if not f.exists():
        return json.loads(json.dumps(_SAMPLE_DATA))
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return _sanitize(json.load(fp))
    except Exception:
        # 数据损坏时保留原文件备份，重建
        try:
            shutil.copy2(f, f.with_suffix(".json.bak"))
        except Exception:
            pass
        return {"projects": []}


def _save_data(data: dict) -> None:
    f = data_file()
    tmp = f.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, f)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def get_projects_data() -> dict:
    """只读快照（统计/列表/导出用）"""
    with _LOCK:
        return _load_data()


def write_projects_data(data: dict) -> None:
    """整份覆盖写入。仅用于已自行持锁的内部流程，业务接口请改用 mutate()"""
    with _LOCK:
        _save_data(data)


def mutate(fn) -> tuple[bool, object]:
    """在同一个临界区内完成「读 → 改 → 写」。

    fn(data) 需要返回 (changed, result)：
      changed=False 时不落盘（避免无意义的写放大），result 原样透出。
    所有写接口必须走这里 —— 拆成 get_projects_data() + write_projects_data()
    两次取锁会让并发请求互相覆盖，实测 20 并发只落盘 3 条。
    """
    with _LOCK:
        data = _load_data()
        changed, result = fn(data)
        if changed:
            _save_data(data)
        return bool(changed), result


def mutate_project(pid: str, fn):
    """在锁内定位项目并执行 fn(project, data) -> None，返回修改后的项目；找不到返回 None"""
    box: dict = {"p": None}

    def worker(data: dict) -> tuple[bool, object]:
        for p in data.get("projects", []):
            if p.get("id") == pid:
                fn(p, data)
                box["p"] = p
                return True, p
        return False, None

    mutate(worker)
    return box["p"]


def mutate_task(tid: str, fn):
    """在锁内定位任务并执行 fn(task, project) -> None，返回修改后的任务；找不到返回 None"""
    box: dict = {"t": None}

    def worker(data: dict) -> tuple[bool, object]:
        for p in data.get("projects", []):
            for t in p.get("tasks", []):
                if t.get("id") == tid:
                    fn(t, p)
                    box["t"] = t
                    return True, t
        return False, None

    mutate(worker)
    return box["t"]


def find_project(pid: str) -> tuple[dict, dict] | None:
    """只读查找（不含写入语义）"""
    data = get_projects_data()
    for p in data.get("projects", []):
        if p.get("id") == pid:
            return data, p
    return None


# ---------------- 交接就绪度（本地规则） ----------------

def _readiness_items(p: dict) -> list[dict]:



    next_action = (p.get("next_action") or "").strip()
    done_placeholder = next_action in ("已完结", "暂停中，暂无下一步", "无")
    tasks = p.get("tasks", [])
    return [
        {"key": "handoff", "label": "交接说明已填写（给智能体）", "ok": bool((p.get("handoff_note") or "").strip()), "score": 35},
        {"key": "next", "label": "下一步行动已明确", "ok": bool(next_action) and not done_placeholder, "score": 25},
        {"key": "bg", "label": "项目背景已填写", "ok": bool((p.get("background") or "").strip()), "score": 15},
        {"key": "tasks", "label": "已有任务清单", "ok": len(tasks) > 0, "score": 15},
        {"key": "active", "label": "任务有推进（进行中/已完成）", "ok": any(t.get("status") in ("进行中", "已完成") for t in tasks), "score": 10},
    ]


def _readiness_score(p: dict) -> int:
    return sum(it["score"] for it in _readiness_items(p) if it["ok"])


# ---------------- 请求模型 ----------------

class ProjectIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    status: Literal["规划中", "进行中", "已暂停", "已完成"] = "规划中"
    progress: int = Field(0, ge=0, le=100)
    priority: Literal["高", "中", "低"] = "中"
    tags: list[str] = []
    repo_path: str = ""
    background: str = ""
    handoff_note: str = ""
    next_action: str = ""

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        out = []
        for t in v or []:
            t = (t or "").strip()[:24]
            if t and t not in out:
                out.append(t)
        return out[:12]


class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    desc: str = ""
    status: Literal["待办", "进行中", "已完成", "阻塞"] = "待办"
    priority: Literal["高", "中", "低"] = "中"
    agent: str = ""


# ---------------- 应用 ----------------

app = FastAPI(title="AI 交接看板", docs_url=None, redoc_url=None)

WEB_DIR = _resource_path("web")
DATA_FILE = data_file()

# ---------------- 本地访问防护 ----------------
# 威胁模型：单机单用户，不做用户鉴权（任何本机进程与用户同权限）。
# 这里只挡两类真实可被触发的攻击：恶意网页跨站打洞、DNS rebinding。
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


@app.middleware("http")
async def guard_local_request(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0].strip().strip("[]").lower()
    if host and host not in _ALLOWED_HOSTS:
        return JSONResponse({"detail": "拒绝非本机 Host 访问"}, status_code=403)
    origin = request.headers.get("origin")
    if origin:
        if (urlparse(origin).hostname or "").lower() not in _ALLOWED_HOSTS:
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
    token = os.environ.get("KANBAN_TOKEN")
    if token and request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if request.headers.get("x-kanban-token") != token:
            return JSONResponse({"detail": "缺少或错误的 X-Kanban-Token"}, status_code=401)
    return await call_next(request)


@app.get("/")
def index():
    f = Path(WEB_DIR) / "index.html"
    if f.exists():
        return FileResponse(f)
    return PlainTextResponse("前端资源缺失，请检查安装包完整性", status_code=500)


@app.get("/api/health")
def health():
    return {"ok": True, "data_file": str(DATA_FILE)}


# ---- 项目 CRUD ----

@app.get("/api/projects")
def list_projects():
    """总览：不含任务明细，带任务统计"""
    data = get_projects_data()
    out = []
    for p in data.get("projects", []):
        tasks = p.get("tasks", [])
        counts = {"待办": 0, "进行中": 0, "已完成": 0, "阻塞": 0}
        for t in tasks:
            counts[t.get("status", "待办")] = counts.get(t.get("status", "待办"), 0) + 1
        out.append({
            "id": p["id"], "name": p["name"], "status": p["status"],
            "progress": p["progress"], "priority": p["priority"],
            "tags": p.get("tags", []), "repo_path": p.get("repo_path", ""),
            "next_action": p.get("next_action", ""),
            "created_at": p.get("created_at", ""), "updated_at": p.get("updated_at", ""),
            "task_counts": counts, "total_tasks": len(tasks),
        })
    return {"projects": out}


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectIn):
    now = now_str()
    project = {
        "id": uuid.uuid4().hex[:12], "name": body.name.strip(), "status": body.status,
        "progress": body.progress, "priority": body.priority,
        "tags": body.tags or [], "repo_path": body.repo_path.strip(),
        "background": body.background, "handoff_note": body.handoff_note,
        "next_action": body.next_action, "tasks": [],
        "created_at": now, "updated_at": now,
    }

    def worker(data: dict):
        data.setdefault("projects", []).insert(0, project)
        return True, project

    mutate(worker)
    return project


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    return found[1]


@app.put("/api/projects/{pid}")
def update_project(pid: str, body: ProjectIn):
    def apply(p: dict, _data: dict) -> None:
        p.update({
            "name": body.name.strip(), "status": body.status,
            "progress": body.progress, "priority": body.priority,
            "tags": body.tags or [], "repo_path": body.repo_path.strip(),
            "background": body.background, "handoff_note": body.handoff_note,
            "next_action": body.next_action, "updated_at": now_str(),
        })

    p = mutate_project(pid, apply)
    if p is None:
        raise HTTPException(404, "项目不存在")
    return p


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    def worker(data: dict):
        projects = data.get("projects", [])
        keep = [p for p in projects if p.get("id") != pid]
        if len(keep) == len(projects):
            return False, False
        data["projects"] = keep
        return True, True

    changed, _ = mutate(worker)
    if not changed:
        raise HTTPException(404, "项目不存在")
    return {"ok": True}


# ---- 任务 CRUD ----

@app.post("/api/projects/{pid}/tasks", status_code=201)
def create_task(pid: str, body: TaskIn):
    now = now_str()
    task = {
        "id": uuid.uuid4().hex[:12],
        "title": body.title.strip(), "desc": body.desc,
        "status": body.status, "priority": body.priority,
        "agent": body.agent.strip(), "created_at": now, "updated_at": now,
    }

    def apply(p: dict, _data: dict) -> None:
        p.setdefault("tasks", []).insert(0, task)
        p["updated_at"] = now

    if mutate_project(pid, apply) is None:
        raise HTTPException(404, "项目不存在")
    return task


@app.put("/api/tasks/{tid}")
def update_task(tid: str, body: TaskIn):
    def apply(t: dict, p: dict) -> None:
        now = now_str()
        t.update({
            "title": body.title.strip(), "desc": body.desc,
            "status": body.status, "priority": body.priority,
            "agent": body.agent.strip(), "updated_at": now,
        })
        p["updated_at"] = now

    t = mutate_task(tid, apply)
    if t is None:
        raise HTTPException(404, "任务不存在")
    return t


@app.delete("/api/tasks/{tid}")
def delete_task(tid: str):
    box = {"ok": False}

    def worker(data: dict):
        for p in data.get("projects", []):
            for t in list(p.get("tasks", [])):
                if t.get("id") == tid:
                    p["tasks"].remove(t)
                    p["updated_at"] = now_str()
                    box["ok"] = True
                    return True, True
        return False, False

    changed, _ = mutate(worker)
    if not changed:
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


# ---- 交接摘要导出 ----

def _build_handoff_markdown(p: dict) -> str:
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
    tasks = p.get("tasks", [])
    if tasks:
        lines.append("## 任务清单")
        lines.append("")
        lines.append("| 任务 | 状态 | 优先级 | 负责AI | 描述 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for t in tasks:
            title = (t.get("title") or "").replace("|", "\\|").replace("\n", " ")
            desc = (t.get("desc") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {title} | {t['status']} | {t['priority']} | {t.get('agent','') or '—'} | {desc} |")
        lines.append("")
    lines.append("---")
    lines.append("由 AI 交接看板自动生成")
    return "\n".join(lines)


@app.get("/api/export/{pid}")
def export_handoff(pid: str):
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    md = _build_handoff_markdown(found[1])
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


# =====================================================================
#  统计 / 就绪度 / 完整交接包 / 趋势
# =====================================================================

@app.get("/api/stats")
def api_stats():
    data = get_projects_data()
    projects = data.get("projects", [])
    status_dist = {s: 0 for s in DEFAULT_STATUS}
    task_dist = {s: 0 for s in DEFAULT_TASK_STATUS}
    total_tasks = 0
    blocked = 0
    progresses = []
    readiness = []
    for p in projects:
        st = p.get("status", "规划中")
        status_dist[st] = status_dist.get(st, 0) + 1
        progresses.append(p.get("progress", 0))
        for t in p.get("tasks", []):
            ts = t.get("status", "待办")
            task_dist[ts] = task_dist.get(ts, 0) + 1
            total_tasks += 1
            if ts == "阻塞":
                blocked += 1
        readiness.append({
            "id": p["id"], "name": p["name"],
            "score": _readiness_score(p), "items": _readiness_items(p),
        })
    avg_progress = round(sum(progresses) / len(progresses)) if progresses else 0
    avg_readiness = round(sum(r["score"] for r in readiness) / len(readiness)) if readiness else 0
    return {
        "project_total": len(projects),
        "status_dist": status_dist,
        "task_dist": task_dist,
        "total_tasks": total_tasks,
        "blocked_tasks": blocked,
        "avg_progress": avg_progress,
        "avg_readiness": avg_readiness,
        "readiness": readiness,
    }


class SaveExportBody(BaseModel):
    filename: str = "交接包.md"
    content: str = ""
    path: str | None = None  # 可选：显式保存路径（用户输入）


# 允许"用户填路径保存"，但不允许落到系统目录 —— 防止被诱导覆盖系统/程序文件
def _forbidden_roots() -> list[Path]:
    roots = []
    for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        v = os.environ.get(key)
        if v:
            roots.append(Path(v))
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if home:
        roots.append(Path(home) / "AppData")
    roots += [Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"), Path("/System")]
    return roots


def _is_forbidden_target(target: Path) -> bool:
    try:
        resolved = target.resolve()
    except Exception:
        return True
    # 临时目录放行（AppData 下唯一允许的位置）
    for env in ("TEMP", "TMP"):
        tmp = os.environ.get(env)
        if tmp:
            try:
                t = Path(tmp).resolve()
            except Exception:
                continue
            if resolved == t or t in resolved.parents:
                return False
    for root in _forbidden_roots():
        try:
            r = root.resolve()
        except Exception:
            continue
        if resolved == r or r in resolved.parents:
            return True
    return False


@app.post("/api/export/save")
def save_export(body: SaveExportBody):
    """保存交接包：优先按用户指定路径，否则桌面，最后回退数据目录 exports/"""
    name = (body.filename or "交接包.md").strip().replace("/", "_").replace("\\", "_")
    content = body.content or ""
    if body.path and body.path.strip():
        target = Path(body.path.strip())
        suffix = target.suffix.lower()
        if suffix not in (".md", ".txt", ".markdown", ".agentsmd"):
            raise HTTPException(400, "仅支持保存为 .md / .txt 文件")
        if _is_forbidden_target(target):
            raise HTTPException(400, "不允许写入系统目录，请换一个路径")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"ok": True, "path": str(target)}
        except Exception as e:
            raise HTTPException(500, f"写入失败：{e}")
    candidates = []
    desktop = Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Desktop"
    candidates.append(desktop / name)
    candidates.append(data_dir() / "exports" / name)
    for c in candidates:
        try:
            c.parent.mkdir(parents=True, exist_ok=True)
            c.write_text(content, encoding="utf-8")
            return {"ok": True, "path": str(c)}
        except Exception:
            continue
    raise HTTPException(500, "保存失败，无法写入任何位置")


@app.get("/api/export-all")
def export_all():
    data = get_projects_data()
    projects = data.get("projects", [])
    head = f"# AI 交接看板 · 全项目交接包\n\n> 共 {len(projects)} 个项目 · 导出时间 {now_str()}\n"
    parts = [head]
    for p in projects:
        parts.append("\n\n---\n\n" + _build_handoff_markdown(p))
    return PlainTextResponse("".join(parts), media_type="text/markdown; charset=utf-8")


@app.get("/api/ai/trends")
def ai_trends():
    """从历史监管报告中提取各项目健康分序列"""
    series_map = {}
    for f in sorted(reports_dir().glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        t = r.get("created_at", "")
        for it in r.get("insights", []):
            pid = it.get("id")
            if not pid:
                continue
            series_map.setdefault(pid, {"id": pid, "name": it.get("name", ""), "points": []})
            series_map[pid]["points"].append({"time": t, "health": it.get("health")})
    return {"series": list(series_map.values())}


# ---- AGENTS.md 生成（对接行业标准） ----

def _build_agents_md_local(p: dict) -> str:
    """本地规则生成 AGENTS.md（LLM 不可用时的兜底）"""
    lines = [f"# AGENTS.md — {p['name']}", ""]
    lines.append(f"> 由 AI 交接看板生成 · {now_str()} · 给 AI 编码代理的项目上下文")
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
    tasks = p.get("tasks", [])
    if tasks:
        lines.append("## 任务清单")
        lines.append("")
        for t in tasks:
            lines.append(f"- [{t.get('status', '待办')}] {t['title']}（优先级 {t.get('priority', '中')}）")
        lines.append("")
    return "\n".join(lines)


@app.post("/api/export/agentsmd/{pid}")
def export_agentsmd(pid: str):
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    p = found[1]
    sys_msg = (
        "你是一位资深软件工程师，负责为项目编写 AGENTS.md —— 这是 Linux Foundation 标准的项目上下文文件，"
        "相当于给 AI 编码代理看的 README。要求：\n"
        "1) 总行数控制在 150 行以内，紧凑高信号；\n"
        "2) 只写 AI 无法从代码直接推断的信息：精确的构建/测试命令、代码约定、关键决策、已知的坑、当前进度；\n"
        "3) 使用 Markdown 小标题组织；具体务实，禁止空话套话（如'代码要规范'）；\n"
        "4) 命令若有不确定，写'请查看仓库 README 或 package.json scripts'，不要编造。\n"
        "直接输出 AGENTS.md 正文，不要任何额外说明。"
    )
    user_msg = "项目信息：\n" + json.dumps({
        "name": p["name"], "repo_path": p.get("repo_path", ""),
        "status": p.get("status", ""), "progress": p.get("progress", 0),
        "background": p.get("background", ""), "handoff_note": p.get("handoff_note", ""),
        "next_action": p.get("next_action", ""),
        "tasks": [{"title": t["title"], "status": t.get("status", ""), "priority": t.get("priority", "")}
                  for t in (p.get("tasks") or [])],
    }, ensure_ascii=False, indent=1)
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}], temperature=0.2)
        if not text or len(text.strip()) < 30:
            raise ValueError("生成内容过短")
        return {"text": text.strip(), "endpoint": ep, "source": "ai"}
    except Exception:
        return {"text": _build_agents_md_local(p), "endpoint": "", "source": "local"}


# ---- 数据一键备份 ----

@app.post("/api/backup")
def create_backup():
    backups = data_dir() / "backups"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = backups / f"backup_{ts}"
    folder.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("projects.json", "config.json"):
        src = data_dir() / name
        if src.exists():
            shutil.copy2(src, folder / name)
            copied.append(name)
    # 只保留最近 10 份
    for old in sorted(backups.glob("backup_*"), reverse=True)[10:]:
        shutil.rmtree(old, ignore_errors=True)
    return {"ok": True, "folder": str(folder), "files": copied, "total": len(list(backups.glob("backup_*")))}


@app.get("/api/backups")
def list_backups():
    backups = data_dir() / "backups"
    out = []
    for b in sorted(backups.glob("backup_*"), reverse=True):
        try:
            files = [f.name for f in b.iterdir()] if b.is_dir() else []
        except Exception:
            files = []
        out.append({"name": b.name, "time": b.name.replace("backup_", "").replace("_", " "), "files": files})
    return {"backups": out}


# ---- 会话粘贴 AI 提炼 ----

class SummarizeBody(BaseModel):
    pid: str = ""
    text: str = Field(..., min_length=10, max_length=20000)


@app.post("/api/ai/summarize")
def ai_summarize(body: SummarizeBody):
    found = find_project(body.pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    p = found[1]
    existing = [t["title"] for t in p.get("tasks", [])]
    sys_msg = (
        "你是任务看板的会话提炼助手。用户会把一段与某个项目相关的 AI 对话/聊天记录粘给你，"
        "请提炼有价值的信息，输出 JSON（不要输出其他内容）：\n"
        '{"summary": "这段会话的一句话总结", '
        '"new_tasks": [{"title": "任务标题", "desc": "描述", "priority": "高/中/低", "status": "待办/进行中/已完成/阻塞"}], '
        '"next_action": "建议的下一步行动（若没有则为空字符串）", '
        '"handoff_note_updates": "建议补充进交接说明的要点（纯文本一段，若没有则为空字符串）"}\n'
        "规则：1) new_tasks 只收录明确提到要做、且不在现有任务清单里的事项，不要编造；"
        "2) summary 一句话；3) 中文输出。"
    )
    user_msg = (f"项目：{p['name']}\n"
                f"现有任务：{json.dumps(existing, ensure_ascii=False)}\n\n"
                f"会话记录：\n{body.text}")
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ], temperature=0.15)
        parsed = _extract_json(text)
        if not isinstance(parsed, dict):
            raise ValueError("提炼结果格式不正确")
        return {"parsed": parsed, "endpoint": ep}
    except llm.LLMError as e:
        raise HTTPException(502, f"AI 调用失败：{e}")
    except Exception as e:
        raise HTTPException(500, f"提炼失败：{e}")


# =====================================================================
#  AI 自我监管
# =====================================================================

def reports_dir() -> Path:
    d = data_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _extract_json(text: str) -> dict | list:
    """从 LLM 输出中容错提取 JSON（去除 markdown 代码块、前后缀文本）"""
    if not text:
        raise ValueError("LLM 返回为空")
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # 前后缀有噪声时，取「出现位置更靠前」的那个候选：
    # 否则像「前缀 [{"a":1}] 后缀」这种会先命中内层对象，把数组吞掉。
    candidates = []
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = s.find(open_ch)
        end = s.rfind(close_ch)
        if start != -1 and end > start:
            candidates.append((start, s[start:end + 1]))
    for _, chunk in sorted(candidates, key=lambda x: x[0]):
        try:
            return json.loads(chunk)
        except Exception:
            continue
    raise ValueError("无法从 LLM 输出中解析 JSON")


def _projects_brief(projects: list[dict]) -> list[dict]:
    """给 LLM 的精简项目数据"""
    brief = []
    for p in projects:
        brief.append({
            "id": p["id"],
            "name": p["name"],
            "status": p.get("status", ""),
            "progress": p.get("progress", 0),
            "priority": p.get("priority", ""),
            "tags": p.get("tags", []),
            "next_action": (p.get("next_action") or "")[:300],
            "background": (p.get("background") or "")[:300],
            "tasks": [{"title": t["title"], "status": t.get("status", ""), "priority": t.get("priority", "")}
                      for t in (p.get("tasks") or [])[:40]],
        })
    return brief


class AIConfigIn(BaseModel):
    endpoints: list[dict] | None = None
    active_model: str | None = None
    schedule: dict | None = None


def _merge_keys(old_keys: list[str], new_keys: list[str], fallback_pool: list[str] | None = None) -> list[str]:
    """合并 Key：掩码形式(含 ****)的提交项从旧配置找回真实 Key。

    查找顺序：本端点的旧 Key → fallback_pool（全部端点的旧 Key）。
    后者用于端点改名/换位置的场景 —— 否则掩码找不到匹配，会被当成真实 Key 写回配置。
    仍然找不到就丢弃：宁可少一个 Key，也不能把 sk-abc****xyz 这种掩码写进配置。
    """
    pool = list(old_keys or []) + list(fallback_pool or [])
    out = []
    for k in new_keys or []:
        k = (k or "").strip()
        if not k:
            continue
        if "****" in k:
            found = None
            for ok in pool:
                if len(ok) >= 10 and len(k) >= 10 and ok[:6] == k[:6] and ok[-4:] == k[-4:]:
                    found = ok
                    break
            if found:
                out.append(found)
            continue
        out.append(k)
    return out


@app.get("/api/ai/config")
def get_ai_config():
    return llm.mask_config(llm.load_config())


@app.put("/api/ai/config")
def put_ai_config(body: AIConfigIn):
    def apply(cfg: dict) -> None:
        if body.endpoints is not None:
            old_eps = cfg.get("ai", {}).get("endpoints", [])
            old = {ep.get("name"): ep.get("api_keys", []) for ep in old_eps}
            # 全局 Key 池：端点改名后掩码仍能找回真实 Key
            pool = [k for ep in old_eps for k in (ep.get("api_keys") or []) if k]
            new_eps = []
            for ep in body.endpoints:
                name = (ep.get("name") or "").strip() or "未命名端点"
                new_eps.append({
                    "name": name,
                    "base_url": (ep.get("base_url") or "").strip().rstrip("/"),
                    "api_keys": _merge_keys(old.get(name, []), ep.get("api_keys", []), pool),
                    "models": [m for m in (ep.get("models") or []) if m.strip()],
                    "enabled": bool(ep.get("enabled", True)),
                })
            cfg["ai"]["endpoints"] = new_eps
        if body.active_model is not None:
            cfg["ai"]["active_model"] = body.active_model.strip()
        if body.schedule is not None:
            s = cfg["ai"].setdefault("schedule", {})
            s.update({k: v for k, v in body.schedule.items() if k in ("enabled", "frequency", "time", "last_run")})
            s["enabled"] = bool(s.get("enabled"))

    return llm.mask_config(llm.update_config(apply))


@app.post("/api/ai/test")
def ai_test_connection():
    cfg = llm.load_config()
    return llm.test_connection(cfg)


# ---- 监管分析 ----

def _build_analyze_prompt(projects: list[dict]) -> list[dict]:
    sys_msg = (
        "你是一位资深的项目管理监管助手，负责审查一个多项目任务看板。"
        "请分析下面给出的项目数据，输出 JSON（不要输出其他内容），格式：\n"
        '{"summary": "整体监管概览，2-3 句话", '
        '"insights": [{"id": "项目id", "name": "项目名", "health": 0到100的整数健康分, '
        '"reason": "评分理由一句话", "risks": ["风险1", "风险2"], '
        '"suggestions": [{"text": "具体可执行的建议，如下一步行动", "apply_to": "next_action"}]}], '
        '"global_suggestions": ["全局性建议1", "全局性建议2"]}\n'
        "要求：health 分数基于进度合理性、任务堆积情况、阻塞与风险；suggestions 要具体可执行；"
        "apply_to 目前只支持 next_action（写入下一步行动）。"
    )
    user_msg = "当前看板项目数据（JSON）：\n" + json.dumps(projects, ensure_ascii=False, indent=1)
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]


def _save_report(report: dict) -> dict:
    rid = report["id"]
    (reports_dir() / f"{rid}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _run_analyze(trigger: str = "manual") -> dict:
    data = get_projects_data()
    projects = data.get("projects", [])
    cfg = llm.load_config()
    messages = _build_analyze_prompt(_projects_brief(projects))
    text, ep = llm.chat(cfg, messages, temperature=0.2)
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        raise ValueError("监管分析结果格式不正确")
    report = {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "created_at": now_str(),
        "trigger": trigger,
        "endpoint": ep,
        "summary": parsed.get("summary", ""),
        "insights": parsed.get("insights", []) or [],
        "global_suggestions": parsed.get("global_suggestions", []) or [],
    }
    return _save_report(report)


@app.post("/api/ai/analyze")
def ai_analyze():
    try:
        report = _run_analyze("manual")
        return report
    except llm.LLMError as e:
        raise HTTPException(502, f"AI 调用失败：{e}")
    except Exception as e:
        raise HTTPException(500, f"分析失败：{e}")


@app.get("/api/ai/reports")
def ai_reports():
    out = []
    for f in sorted(reports_dir().glob("*.json"), reverse=True):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "id": r.get("id"), "created_at": r.get("created_at"),
                "trigger": r.get("trigger"), "endpoint": r.get("endpoint"),
                "summary": (r.get("summary") or "")[:120],
                "insight_count": len(r.get("insights", [])),
            })
        except Exception:
            continue
    return {"reports": out}


@app.get("/api/ai/reports/{rid}")
def ai_report_detail(rid: str):
    f = reports_dir() / f"{rid}.json"
    if not f.exists():
        raise HTTPException(404, "报告不存在")
    return json.loads(f.read_text(encoding="utf-8"))


# ---- 手动应用监管建议 ----

class SuggestionUpdate(BaseModel):
    project_id: str
    field: str = "next_action"
    value: str = ""


@app.post("/api/ai/apply-suggestions")
def ai_apply_suggestions(body: list[SuggestionUpdate]):
    allowed_field = ("next_action", "handoff_note", "background", "priority", "status")

    def worker(data: dict):
        ok = 0
        for u in body:
            if u.field not in allowed_field:
                continue
            for p in data.get("projects", []):
                if p.get("id") == u.project_id:
                    p[u.field] = u.value
                    p["updated_at"] = now_str()
                    ok += 1
                    break
        # 直接写字段可能带进非法状态/优先级，交给统一规范化收口
        _sanitize(data)
        return ok > 0, ok

    _, ok = mutate(worker)
    return {"applied": ok}


# ---- 交接文档智能生成 ----

class HandoffBody(BaseModel):
    mode: str = "generate"  # generate=新写 / polish=润色现有


@app.post("/api/ai/handoff/{pid}")
def ai_generate_handoff(pid: str, body: HandoffBody):
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    p = found[1]
    sys_msg = (
        "你是一位资深的技术项目经理，擅长撰写给 AI 智能体的项目交接文档。"
        "根据提供的项目信息，输出一份交接说明，要求："
        "1) 结构清晰，用 Markdown 小标题分段；2) 包含：项目概况、技术栈与目录、关键决策/约定、"
        "已知的坑与注意事项、如何运行与测试（如有）、当前进度、下一步行动；"
        "3) 语言具体务实，不要空话套话；4) 篇幅 300-500 字。直接输出交接说明正文，不要其他内容。"
    )
    user_msg = "项目信息：\n" + json.dumps(_projects_brief([p])[0], ensure_ascii=False, indent=1)
    if body.mode == "polish" and p.get("handoff_note"):
        user_msg += "\n\n现有交接说明（请在其基础上润色增强，保留有效信息）：\n" + p["handoff_note"]
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}], temperature=0.3)
        return {"text": text.strip(), "endpoint": ep}
    except llm.LLMError as e:
        raise HTTPException(502, f"AI 调用失败：{e}")
    except Exception as e:
        raise HTTPException(500, f"生成失败：{e}")


# ---- 自然语言维护看板 ----

class CommandBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


_CMD_TYPES = (
    "create_project(新建项目), update_project(修改项目字段 field ∈ status/progress/priority/next_action/handoff_note/background), "
    "delete_project(删除项目), create_task(在 project_id 下新建任务), update_task(修改任务 field ∈ status/priority/agent/title/desc), "
    "delete_task(删除任务)"
)


@app.post("/api/ai/command")
def ai_command(body: CommandBody):
    data = get_projects_data()
    projects = _projects_brief(data.get("projects", []))
    sys_msg = (
        "你是任务看板的自然语言操作助手。用户会用中文下达操作指令，请把它解析成结构化操作。"
        f"支持的操作类型：{_CMD_TYPES}\n"
        "输出 JSON（不要输出其他内容）：\n"
        '{"actions": [{"type": "create_task", "project_id": "...", "title": "...", "desc": "...", "priority": "高/中/低", "status": "待办/进行中/已完成/阻塞", "agent": "..."}, ...]}\n'
        "规则：1) 项目用 id 标识，任务用项目下现有任务标题匹配；2) 若指令信息不足无法确定操作，"
        '输出 {"need_clarification": "具体需要用户补充什么"}; 3) 不要编造不存在的项目 id；'
        "4) 删除类操作必须能明确匹配到目标才输出；5) 一次指令可能包含多个操作，按顺序列出。"
    )
    user_msg = "看板数据：\n" + json.dumps(projects, ensure_ascii=False, indent=1) + "\n\n用户指令：" + body.text
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}], temperature=0.1)
        parsed = _extract_json(text)
        return {"parsed": parsed, "endpoint": ep}
    except llm.LLMError as e:
        raise HTTPException(502, f"AI 调用失败：{e}")
    except Exception as e:
        raise HTTPException(500, f"解析失败：{e}")


# ---- 定时自动监管 ----

def _period_key(now: datetime, freq: str) -> str:
    """当前调度周期标识：daily 按天、weekly 按 ISO 周"""
    if freq == "weekly":
        iso = now.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return now.strftime("%Y-%m-%d")


def _scheduler_loop() -> None:
    while True:
        try:
            cfg = llm.load_config()
            sched = cfg.get("ai", {}).get("schedule", {}) or {}
            freq = sched.get("frequency")
            if sched.get("enabled") and freq in ("daily", "weekly"):
                now = datetime.now()
                period = _period_key(now, freq)
                # last_period 落盘，重启后同一周期不会重复触发
                if now.strftime("%H:%M") == sched.get("time") and sched.get("last_period") != period:
                    try:
                        report = _run_analyze("schedule")

                        def touch(c: dict) -> None:
                            c.setdefault("ai", {}).setdefault("schedule", {}).update(
                                {"last_run": report["created_at"], "last_period": period})

                        llm.update_config(touch)
                    except Exception:
                        pass
                    time.sleep(61)
        except Exception:
            pass
        time.sleep(30)


_SCHEDULER_STARTED = False


def start_scheduler() -> None:
    """幂等启动调度线程（import 即拉起一次）"""
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED:
        return
    _SCHEDULER_STARTED = True
    threading.Thread(target=_scheduler_loop, daemon=True, name="kanban-scheduler").start()


start_scheduler()
