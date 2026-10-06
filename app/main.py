# -*- coding: utf-8 -*-
"""
AI 交接看板 - FastAPI 后端
提供多项目总览 + 单项目详情 + 任务管理 + 交接摘要导出
数据以 JSON 文件持久化，位于用户主目录 .ai-kanban/projects.json（可用环境变量 KANBAN_DATA_DIR 覆盖）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from . import llm
from .filelock import blocking_lock
from .handoff_page import build_handoff_page, dep_graph_svg
from .handoff_page import MAX_NODES as GRAPH_MAX_NODES
from .handoff_page import MAX_COLS as GRAPH_MAX_COLS
from .handoff_page import _layers as _graph_layers
# 任务依赖图与重复 id 处理：2026-09-26 从本文件整体搬出（实现逐字未改）
from .taskgraph import (  # noqa: F401  —— 这些名字仍可从 app.main 取，既有 import 与 monkeypatch 路径不变
    MAX_DEPS,
    clean_deps,
    task_graph,
    has_cycle,
    topo_order,
    _break_dep_cycles,
    executable_tasks,
    _dedupe_ids,
)
# 截止日期归一与临期/逾期判定：2026-09-26 从本文件整体搬出（实现逐字未改）
from .due import (  # noqa: F401  —— 这些名字仍可从 app.main 取，既有 import 与 monkeypatch 路径不变
    _DUE_CANONICAL_RE,
    _clean_due_date,
    _due_state,
)
# LLM 用量聚合：2026-10-01 P0-1 从本文件的 usage_stats() 抽出（纯函数，穷举测试）
from . import usage_stats as usage_metrics
from . import project_stats as project_metrics
# 任务自动分配：2026-10-01 新增（纯函数，就绪口径复用 taskgraph.executable_tasks）
from . import assign as assign_module
# 交接内容脱敏：2026-09-26 从本文件整体搬出（实现逐字未改）
from .redact import (  # noqa: F401  —— 这些名字仍可从 app.main 取，既有 import 与 monkeypatch 路径不变
    MASK_MARK,
    _REDACT_PATTERNS,
    redact_text,
)
# 创建幂等键：2026-09-26 从本文件整体搬出（实现逐字未改）
from .idem import (  # noqa: F401  —— 这些名字仍可从 app.main 取，既有 import 与 monkeypatch 路径不变
    MAX_IDEM,
    IDEM_KEY,
    _idem_seen,
    _idem_remember,
    _clean_ref,
)
# 交接包小节与尺寸预算：2026-09-26 从本文件整体搬出（实现逐字未改）
from .handoff_meta import (  # noqa: F401  —— 这些名字仍可从 app.main 取，既有 import 与 monkeypatch 路径不变
    HANDOFF_SECTIONS,
    _SECTION_CN,
    parse_handoff_sections,
    context_budget,
    _budget_note,
)
# 任务关联的代码位置：2026-09-26 从本文件整体搬出（实现逐字未改）
from .taskgraph import (  # noqa: F401
    MAX_REFS,
    MAX_REF_LEN,
    clean_refs,
)
# 会话扫描与文本提取：2026-09-26 从本文件整体搬出（实现逐字未改）
from .sessions import (  # noqa: F401
    session_sources,
    _walk_bounded,
    _newest_files,
    _read_tail_text,
    _extract_session_text,
    _session_roots,
    is_allowed_session_file,
)
# git 集成（只读、列表传参、绝不拼 shell）：2026-10-06 从本文件整体搬出（实现逐字未改）。
# ⚠️ _run_git / _git_cache / GIT_TIMEOUT 的 monkeypatch 点随迁到 app.gitinfo，
# main 里只是转发别名（纯函数调用的 patch 如 main.git_snapshot 仍打在 main 上）
from .gitinfo import (  # noqa: F401
    GIT_TIMEOUT,
    _git_cache,
    _git_sliced,
    _run_git,
    git_snapshot,
)
# 多智能体入口文件生成：2026-09-26 从本文件整体搬出（实现逐字未改）
from .agentfiles import (  # noqa: F401  —— 这些名字仍可从 app.main 取，既有 import 与 monkeypatch 路径不变
    AGENT_ENTRY_TARGETS,
    CLAUDE_BRIDGE,
    SKILL_NAME,
    _cursor_rule,
    _skill_entry,
    build_agent_entries,
)
from .readiness import readiness_items, readiness_score
from .version import APP_VERSION, BUILD_TIME

APP_NAME = "ai-kanban"
# 交接包里打印的直链端口。桌面壳用同一个环境变量起服务，两边口径一致；
# 用户没设时两边都落回 18320。服务没开时链接取不到，交接包正文仍可用（它是快照）。
SERVICE_PORT = os.environ.get("KANBAN_PORT", "18320")
DEFAULT_STATUS = ["规划中", "进行中", "已暂停", "已完成"]
DEFAULT_TASK_STATUS = ["待办", "进行中", "待审查", "已完成", "阻塞"]
DEFAULT_PRIORITY = ["高", "中", "低"]

# 数据文档与机器可读交接包的**结构版本**，与 APP_VERSION（发布号）分开：
# 换机导入时"这份 JSON 的字段形状是哪一版"和"看板是第几版"是两件事。
# 加字段/改字段语义时 +1，并在 tests/test_api.py 的接手包字段集合快照里同步。
# v2（2026-10-06）：任务 slim 带 `acceptance`（验收标准）、顶层带 `expired_memory_count`。
SCHEMA_VERSION = 2

# 导出落盘允许的扩展名。前端 `saveAgentsMD` 里那条正则必须与这份一字不差
# （以前前端少一条 `.markdown`，后端明明允许却被前端挡在门外），
# 对账由 tests/test_frontend.py 钉住。
EXPORT_SUFFIXES = (".md", ".txt", ".markdown", ".agentsmd")

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
_LOCK_DEPTH = threading.local()   # _cross_process_lock 的同线程重入计数

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


# ---- 任务关联的代码位置（交接时说清"改哪里"） ----

# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入。


# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入。


# ---- 任务依赖（交接时说清"先做哪个"） ----

# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入，见文件顶部 import。


# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入，见文件顶部 import。


# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入，见文件顶部 import。


# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入，见文件顶部 import。


# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入，见文件顶部 import。


# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入，见文件顶部 import。


# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入，见文件顶部 import。


# [taskgraph.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.taskgraph 导入，见文件顶部 import。


# [due.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.due 导入，见文件顶部 import。


# [due.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.due 导入，见文件顶部 import。


def _sanitize(data: dict) -> dict:
    """规范化读入的数据：修正非法状态/优先级，保证前端不拿到越界值"""
    if not isinstance(data, dict):
        return {"projects": []}
    # 自定义视图是全局配置（"保存当前筛选 → 命名 → 切换"），同样收一道：
    # 导入/恢复来的脏视图在每次读上清掉，保证前端永远拿到形状稳定的列表。
    if isinstance(data.get("views"), list):
        data["views"] = _clean_views(data["views"])
    projects = data.get("projects")
    if not isinstance(projects, list):
        data["projects"] = []
        return data
    _dedupe_ids(projects)
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
        if isinstance(p.get("milestones"), list):
            # 里程碑是「追加型」字段，导入/恢复时同样收一道：
            # 同名、超长、超过上限的条目在这里清掉，保证前端拿到稳定形状。
            p["milestones"] = _clean_milestones(p["milestones"])
        tasks = p.get("tasks")
        if not isinstance(tasks, list):
            p["tasks"] = tasks = []
        _dedupe_ids(tasks)     # 必须排在依赖清洗前面：见函数说明
        for t in tasks:
            if not isinstance(t, dict):
                continue
            if t.get("status") not in DEFAULT_TASK_STATUS:
                t["status"] = "待办"
            if t.get("priority") not in DEFAULT_PRIORITY:
                t["priority"] = "中"
            t["refs"] = clean_refs(t.get("refs"))      # 老数据没有这个字段，顺带补齐
            t["acceptance"] = clean_acceptance(t.get("acceptance"))   # 同上：老数据补空列表
            t["journal"] = clean_journal(t.get("journal"))   # 过程轨迹也收一道，防导入的脏数据撑大文件
            if t.get("due_date"):
                # 只在本来就有值时归一：不给老数据凭空加键（导出体积与前端判断都按"有没有"走）
                t["due_date"] = _clean_due_date(t["due_date"])
            if not isinstance(t.get("depends_on"), list):
                t["depends_on"] = []
            if t.get("milestone") is not None:
                # 只在本来就有值时归一（与 due_date 同一纪律）：不给老数据凭空加键；
                # 只留字符串、去首尾空白、截断 40，别的类型一律置 ""（归不进任何里程碑）。
                m = t["milestone"]
                t["milestone"] = str(m).strip()[:MAX_MILESTONE_NAME] if isinstance(m, str) else ""
        # 顺手清掉指向已删除任务的依赖（否则任务永远"等一个不存在的东西"）
        ids = {t.get("id") for t in tasks if isinstance(t, dict)}
        for t in tasks:
            if isinstance(t, dict):
                t["depends_on"] = [d for d in t["depends_on"] if d in ids]
        _break_dep_cycles(tasks)   # 环只能从导入/恢复进来，但仍放在每次读上：见函数说明
    return data


_DATA_RECOVERED = False          # 数据文件损坏并重建过；上报一次后清掉
_DATA_RECOVERED_FROM = ""        # 空的表示重置为空看板，否则是恢复来源
LAST_GOOD_SUFFIX = ".json.last-good"


def last_good_file() -> Path:
    """最近一次正常保存的数据副本。

    损坏时把当前文件复制成 .bak 是没用的 —— 那时的文件**本身就是坏的**。
    真正能救命的是一份"上次写成功时的样子"。
    """
    return data_file().with_suffix(LAST_GOOD_SUFFIX)


def before_restore_file() -> Path:
    """每次恢复**之前**把当时的数据另存到这里 —— 恢复错了的那条退路。

    单独一个函数是因为这个路径以前只在写入处硬编码过一次，于是"可恢复来源"的白名单
    根本不认识它：界面向用户承诺"覆盖前会自动另存"，实际没有任何入口能把它拿回来。
    只有一份（下一次恢复会覆盖），所以对外要说清是"最近一次恢复前"。
    """
    return data_dir() / "projects.json.before-restore.bak"


def _load_data() -> dict:
    global _DATA_RECOVERED, _DATA_RECOVERED_FROM
    f = data_file()
    if not f.exists():
        return json.loads(json.dumps(_SAMPLE_DATA))
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return _sanitize(json.load(fp))
    except Exception:
        # 1) 先把损坏文件留一份，便于事后取证
        try:
            shutil.copy2(f, f.with_suffix(".json.bak"))
        except Exception:
            pass
        # 2) 尝试从"最近一次正常保存"救回来
        healed = None
        lf = last_good_file()
        if lf.exists():
            try:
                candidate = _sanitize(json.loads(lf.read_text(encoding="utf-8")))
                if isinstance(candidate.get("projects"), list):
                    healed = candidate
            except Exception:
                healed = None
        # 3) 落盘（救回来了就写回数据，否则写空结构）。
        #    注意不能更新 last-good：这次写的可能是空看板，别把救命副本覆盖掉。
        #    这条路径可能只拿着 _LOCK 就被调进来（get_projects_data），写盘必须和
        #    mutate() 抢同一把跨进程锁，否则会与另一个进程的写入互相覆盖。
        healed_data = healed if healed is not None else {"projects": []}
        with _cross_process_lock():
            _save_data(healed_data, update_last_good=False)
        _DATA_RECOVERED = True
        _DATA_RECOVERED_FROM = "last-good" if healed is not None else ""
        return healed_data


def _save_data(data: dict, update_last_good: bool = True) -> None:
    f = data_file()
    tmp = f.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, f)
    if update_last_good:
        # 每次成功保存都刷一份"上次正常状态"，供损坏时自动恢复
        try:
            shutil.copy2(f, last_good_file())
        except Exception:
            pass


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------- 跨进程写锁 ----------------
# 线程锁只挡得住同一进程内的并发。桌面应用与 MCP 服务（scripts/mcp_server.py）
# 会同时写同一份 projects.json，必须再加一层跨进程文件锁，否则又是丢更新。

@contextmanager
def _cross_process_lock(timeout: float = 15.0):
    """跨进程写锁。同线程可重入 —— Windows 的字节锁按**句柄**生效，
    `mutate()` 持锁期间 `_load_data()` 的自愈写盘若再开一个句柄抢同一把锁，
    同进程也会互相冲突，然后一路等到 15 秒超时报错。

    锁本身在 `app/filelock.py` —— llm 也要一把同样的锁，而它不能 import main（会成环）。
    """
    depth = getattr(_LOCK_DEPTH, "n", 0)
    if depth:
        _LOCK_DEPTH.n = depth + 1
        try:
            yield
        finally:
            _LOCK_DEPTH.n = depth
        return

    _LOCK_DEPTH.n = 1
    try:
        with blocking_lock(data_dir() / "projects.lock", timeout=timeout, what="数据文件"):
            yield
    finally:
        _LOCK_DEPTH.n = 0


def get_projects_data() -> dict:
    """只读快照（统计/列表/导出用）"""
    with _LOCK:
        return _load_data()


def mutate(fn) -> tuple[bool, object]:
    """在同一个临界区内完成「读 → 改 → 写」。

    fn(data) 需要返回 (changed, result)：
      changed=False 时不落盘（避免无意义的写放大），result 原样透出。
    所有写接口必须走这里 —— 自己取一份快照、改完再另起一次锁写回，两次取锁之间会被
    并发请求互相覆盖，实测 20 并发创建全部返回 201 却只落盘 3 条。
    """
    with _LOCK, _cross_process_lock():
        data = _load_data()
        changed, result = fn(data)
        if changed:
            _save_data(data)
        return bool(changed), result


def mutate_project(pid: str, fn):
    """在锁内定位项目并执行 fn(project, data)，返回修改后的项目；找不到返回 None

    `fn` 显式返回 `False` 表示"这次一个字都没改"，于是不落盘 —— 幂等键的回放走这条。
    返回 None（所有老调用方的写法）照旧算"改过了"。
    """
    box: dict = {"p": None}

    def worker(data: dict) -> tuple[bool, object]:
        for p in data.get("projects", []):
            if p.get("id") == pid:
                changed = fn(p, data)
                box["p"] = p
                return changed is not False, p
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


# ---------------- 交接就绪度（本地规则，实现收口在 app/readiness.py） ----------------

def _readiness_items(p: dict) -> list[dict]:
    return readiness_items(p)


def _readiness_score(p: dict) -> int:
    return readiness_score(p)


# ---------------- 请求模型 ----------------

# 自定义视图（Plane Views 轻量版）：总览筛选条件的「命名 → 保存 → 切换」薄壳。
# 它是追加型存储（存 projects.json 顶层 `views`），按硬性约定 11 必须有上限。
MAX_SAVED_VIEWS = 30
MAX_VIEW_NAME = 40
MAX_VIEW_QUERY = 200   # 视图里保存的搜索词上限


def _clean_views(v) -> list[dict]:
    """清洗顶层 `views`：`[{id, name, filter, q, created_at, updated_at}]`。

    纪律与 `_clean_milestones` 一致：只认带非空 id/name 的 dict，类型不对的整条丢弃；
    name 去首尾空白并截断、**id 与 name 双重去重**（首现者胜）；
    `filter` 只放行「全部」或项目状态枚举；`q` 截断；总量封顶 `MAX_SAVED_VIEWS`。
    id 缺失不补（与项目/任务同纪律：视图 id 由服务端生成，缺 id = 脏数据）。
    挂在每次读的 `_sanitize` 上，导入/恢复过来的脏视图同样过这道。
    """
    out: list[dict] = []
    if not isinstance(v, (list, tuple)):
        return []
    seen_ids: set = set()
    seen_names: set = set()
    for item in v:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("id") or "").strip()[:24]
        name = str(item.get("name") or "").strip()[:MAX_VIEW_NAME]
        if not vid or not name or vid in seen_ids or name in seen_names:
            continue
        f = str(item.get("filter") or "全部").strip()
        if f not in ("全部", *DEFAULT_STATUS):
            f = "全部"
        q = str(item.get("q") or "").strip()[:MAX_VIEW_QUERY]
        seen_ids.add(vid)
        seen_names.add(name)
        out.append({
            "id": vid,
            "name": name,
            "filter": f,
            "q": q,
            "created_at": str(item.get("created_at") or "")[:30],
            "updated_at": str(item.get("updated_at") or "")[:30],
        })
        if len(out) >= MAX_SAVED_VIEWS:
            break
    return out


# 里程碑（P2 轻量版）上限：方案明确「追加型字段设上限，里程碑≦20 个」。
MAX_MILESTONES = 20
MAX_MILESTONE_NAME = 40   # 也用于任务的 milestone 归属字段（与名称同一截断口径）


def _clean_milestones(v) -> list[dict]:
    """清洗项目的里程碑数组：`[{name, done?}]`。

    规则：只认带非空 name 的 dict（类型不对的整条丢弃）；name 去首尾空白并截断；
    同名去重（按首次出现的顺序保留）；`done` 只当布尔用，其余值一律按 False；
    总量封顶 `MAX_MILESTONES`。清出来的结构保证前端与统计模块拿到稳定形状。
    """
    out: list[dict] = []
    if isinstance(v, str):
        v = [{"name": x} for x in v.replace("\n", ",").split(",")]
    if not isinstance(v, (list, tuple)):
        return []
    for item in v:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:MAX_MILESTONE_NAME]
        if not name or name in [m["name"] for m in out]:
            continue
        out.append({"name": name, "done": bool(item.get("done"))})
        if len(out) >= MAX_MILESTONES:
            break
    return out


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
    # 类型放宽为 list：清洗全部交给 _clean_milestones（非 dict 元素按规则丢弃、
    # 不 422 —— 方案口径是「类型不对的整条丢弃」）。若钉死 list[dict]，
    # pydantic 会在 validator 之前就把非 dict 元素拒掉，容错永远走不到。
    milestones: list = []

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        out = []
        for t in v or []:
            t = (t or "").strip()[:24]
            if t and t not in out:
                out.append(t)
        return out[:12]

    @field_validator("milestones")
    @classmethod
    def _clean_milestones_val(cls, v):
        return _clean_milestones(v)


class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    desc: str = ""
    status: Literal["待办", "进行中", "待审查", "已完成", "阻塞"] = "待办"
    priority: Literal["高", "中", "低"] = "中"
    agent: str = ""
    due_date: str = ""            # 截止日期 YYYY-MM-DD，空=未设置
    refs: list[str] = []          # 相关代码位置，如 app/main.py:120；交接时用来说明"改哪里"
    depends_on: list[str] = []    # 前置任务 id（同一项目内），用于排"先做哪个"
    milestone: str = ""           # 归入的项目里程碑名（空=未归入）；普通字符串校验，非 Literal
    acceptance: list[str] = []    # 验收标准（对标 Backlog.md 的 DoD）：逐条"怎么算完成"，
                                  # 交接包与接手页带着走，待审查→已完成时照着验
    @field_validator("due_date", mode="before")
    @classmethod
    def _clean_due(cls, v):
        v = (v or "").strip()
        if not v:
            return ""
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return ""
        return v

    @field_validator("milestone", mode="before")
    @classmethod
    def _clean_milestone(cls, v):
        v = (v or "").strip()
        return v[:MAX_MILESTONE_NAME] if v else ""

    @field_validator("refs", mode="before")
    @classmethod
    def _clean_refs(cls, v):
        return clean_refs(v)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _clean_deps(cls, v):
        if isinstance(v, str):
            v = [p for p in v.replace("\n", ",").split(",")]
        if not isinstance(v, (list, tuple)):
            return []
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("acceptance", mode="before")
    @classmethod
    def _clean_acceptance(cls, v):
        return clean_acceptance(v)


class AssignIn(BaseModel):
    """任务自动分配请求：把项目内『指定优先级、未完成、依赖已就绪』的任务批量指派给某智能体。

    `overwrite=True` 时连已有负责人的任务一起覆盖（默认跳过，保护手工指派）。
    """
    agent: str = Field(..., min_length=1, max_length=60, description="要指派给的智能体名，如 cline / 豆包")
    priority: Literal["高", "中", "低"] = "高"
    overwrite: bool = False


class ViewIn(BaseModel):
    """持久化自定义视图（Plane Views 轻量版）：`filter` 放行项目状态白名单，`q` 是搜索词。

    保存语义是「upsert-by-name」：同名再存 = 更新该视图并返回原 id（带 `updated: true`），
    不产生重复条目 —— 菜单里"把当前筛选存成同名视图"是常见动作，报 409 只会让人困惑。
    """
    name: str = Field(..., min_length=1, max_length=MAX_VIEW_NAME, description="视图名，同名即覆盖更新")
    filter: str = Field("全部", description="项目状态筛选：全部/规划中/进行中/已暂停/已完成")
    q: str = Field("", max_length=MAX_VIEW_QUERY, description="保存的搜索词，应用视图时回填搜索框")

    @field_validator("filter")
    @classmethod
    def _clean_view_filter(cls, v):
        v = (v or "").strip()
        return v if v in ("全部", *DEFAULT_STATUS) else "全部"


# ---------------- 应用 ----------------

app = FastAPI(title="AI 交接看板", docs_url=None, redoc_url=None)

WEB_DIR = _resource_path("web")
DATA_FILE = data_file()

# ---------------- 本地访问防护 ----------------
# 威胁模型：单机单用户，不做用户鉴权（任何本机进程与用户同权限）。
# 这里只挡两类真实可被触发的攻击：恶意网页跨站打洞、DNS rebinding。
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _host_of(raw: str) -> str:
    """Host 头里只取主机名，端口丢掉。

    原来写的是 `raw.split(":")[0]` —— 碰到 IPv6 的 `[::1]:18320` 只切出一个 `[`，
    再 strip("[]") 就成了空串，于是 `if host` 短路，任何带方括号的 Host 都放行。
    """
    raw = (raw or "").strip()
    try:
        host = urlparse("//" + raw).hostname
    except ValueError:
        # 方括号里塞的不是 IPv6（`[evil.example.com]`）—— hostname 会当场抛，
        # 但这恰恰是最该挡的形式，不能因为解析失败就当成"没有 Host"放过。
        host = raw[1:].split("]")[0] if raw.startswith("[") else raw.split(":")[0]
    return (host or "").strip().lower()


@app.middleware("http")
async def guard_local_request(request: Request, call_next):
    host = _host_of(request.headers.get("host") or "")
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


# 前端静态资源：2026-09-28 之前的 index.html 是"CSS+JS 全内联"的单文件，
# 拆成 `style.css` + `js-*.js` 之后，浏览器会单独请求这些文件 ——
# 不挂这一条，界面就是一片白（而 `/` 那条接口照样 200，看不出问题）。
# 挂 `/web` 而不是根：根下已经有 `/api/*`，放开了容易和以后的路由撞。
if Path(WEB_DIR).is_dir():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), check_dir=False), name="web")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "data_file": str(DATA_FILE),
        "version": APP_VERSION,
        "build_time": BUILD_TIME,
        "frozen": bool(getattr(sys, "_MEIPASS", None)),   # True=跑在打包后的 exe 里
    }


# ---- 项目 CRUD ----

@app.get("/api/context/{pid}")
def project_context(pid: str, redact: int = 0):
    """机器可读项目上下文：给 AI 智能体直接拉取的「接手包」。

    对标 bd prime / Xanther——其他工具无需人工粘贴，
    通过本接口（或 MCP 工具）一次拿到接手所需的全部结构化上下文。

    `?redact=1` 只打掩码，不改结构：这个包是**推荐给智能体直接取**的入口，
    而智能体的上下文通常在别的机器上。自由文本一律盖：
    背景 / 交接说明 / 下一步 / 任务标题 / 任务描述 / 过程记录 / 记忆；
    id / 状态 / 分支名 / refs 原样保留（模型要靠它们对齐项目与任务）。
    （`_projects_brief` 是这条线的一个例外：那里的标题留着，因为 ai_command 的提示词
    明写"任务用现有任务标题匹配"，盖了标题就没法把建议对回任务。）

    标题为什么也盖：掩码只替换凭据那一段（"轮换 sk-abcd… 这把" → "轮换 sk-******** 这把"），
    标题照样认得出来，对齐不受影响；反过来，"把密钥写在任务标题里"恰恰是最常见的写法，
    放行就等于让唯一一条 redact 开关在最常见的形状上失效。
    """
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    p = found[1]
    mask = redact_text if redact else (lambda s: s)
    tasks = p.get("tasks") or []
    ready = executable_tasks(tasks)
    by_id = {t.get("id"): t for t in tasks}
    snap = git_snapshot(p.get("repo_path", ""), limit=3) if p.get("repo_path") else {"ok": False}

    def slim(t: dict) -> dict:
        d = {
            "id": t.get("id"), "title": mask(t.get("title") or ""), "status": t.get("status"),
            "priority": t.get("priority"),
        }
        if t.get("due_date"):
            d["due_date"] = t["due_date"]
        if t.get("agent"):
            d["agent"] = t["agent"]
        if t.get("refs"):
            d["refs"] = t["refs"]
        if t.get("acceptance"):
            # 验收标准原样保留：接手方要照着验"怎么算完成"，盖了就没法当清单用
            d["acceptance"] = t["acceptance"]
        if t.get("desc"):
            d["desc"] = mask((t.get("desc") or ""))[:200]
        jr = t.get("journal") or []
        if jr:
            d["recent_journal"] = [{"time": e.get("time", ""), "text": mask((e.get("text") or ""))[:120]}
                                   for e in jr[-2:]]
        waiting = [mask(by_id[x].get("title", x)) for x in (t.get("depends_on") or [])
                   if x in by_id and by_id[x].get("status") != "已完成"]
        if waiting:
            d["waiting_for"] = waiting
        return d

    missing = [it["label"] for it in _readiness_items(p) if not it["ok"]]
    # 过期记忆不进接手包（"轮换 sk-xxx 这把"这类有保质期的事实过期后就是误导），
    # 但**省了几个要说话**：接手方得知道这里少了几条、为什么少。
    pid_memories = [m for m in _load_memories() if m.get("pid") == p.get("id")]
    expired_cnt = sum(1 for m in pid_memories if _memory_expired(m))
    return {
        "app": "ai-kanban",
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_str(),
        "usage_hint": "把本 JSON 作为项目上下文直接喂给 AI 智能体即可接手；配合看板 MCP 工具可读写任务",
        "project": {
            "id": p["id"], "name": p["name"], "status": p.get("status", ""),
            "progress": p.get("progress", 0), "priority": p.get("priority", ""),
            "tags": p.get("tags", []), "repo_path": p.get("repo_path", ""),
            "background": mask(p.get("background", "")),
            "handoff_note": mask(p.get("handoff_note", "")),
            "next_action": mask(p.get("next_action", "")),
        },
        "memories": [{**m, "text": mask(m.get("text") or "")}
                     for m in pid_memories if not _memory_expired(m)][-15:],
        "expired_memory_count": expired_cnt,
        "code_reality": ({
            "available": True,
            "branch": snap.get("branch", ""),
            "last_commit_at": (snap.get("head") or {}).get("date", ""),
            "last_commit_subject": mask((snap.get("head") or {}).get("subject", "")),
            "days_since_commit": snap.get("days_since_commit"),
            "uncommitted_files": snap.get("dirty_count", 0),
        } if snap.get("ok") else {
            # 读不到要说话。以前整节省掉（=null），于是"这个项目没填代码路径"和
            # "填了但不是 git 仓库/路径没了"在接手方看来一模一样，排查成本全转给用户。
            "available": False,
            "reason": "未填代码路径" if not p.get("repo_path") else (snap.get("error") or "git 读取失败"),
        }),
        "start_here": [  # 现在就能动手的（依赖已满足）
            {"id": t["id"], "title": mask(t.get("title") or ""), "priority": t.get("priority"),
             "refs": t.get("refs") or []} for t in ready
        ],
        "tasks": {
            "todo": [slim(t) for t in tasks if t.get("status") == "待办"],
            "in_progress": [slim(t) for t in tasks if t.get("status") == "进行中"],
            "review": [slim(t) for t in tasks if t.get("status") == "待审查"],
            "blocked": [slim(t) for t in tasks if t.get("status") == "阻塞"],
            "done_count": sum(1 for t in tasks if t.get("status") == "已完成"),
        },
        "readiness": {"score": _readiness_score(p), "missing": missing},
    }


# ---- 交接内容脱敏（对标 handoff skill 的 redact） ----

# [redact.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.redact 导入，见文件顶部 import。

# [redact.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.redact 导入，见文件顶部 import。


# [redact.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.redact 导入，见文件顶部 import。


@app.get("/api/search/tasks")
def search_tasks(q: str = "", limit: int = 30):
    """全局任务搜索：跨项目按标题/描述/负责人匹配

    带 `truncated`：命中到上限和"总共就这么些"是两件必须分开说的事。
    以前响应里只有 `results`，调用方（智能体尤其）会把"给我 30 条"当成"只有 30 条"，
    于是第 31 条在它的认知里不存在 —— 而 MCP 侧那句"已到上限 50"是按另一个数字写的，
    两边都在猜。
    """
    q = (q or "").strip()
    if not q:
        return {"results": [], "truncated": False, "limit": 30}
    limit = max(1, min(50, int(limit)))
    data = get_projects_data()
    ql = q.lower()
    out = []
    for p in data.get("projects", []):
        for t in (p.get("tasks") or []):
            if not isinstance(t, dict):
                continue
            hay = " ".join([
                t.get("title", ""), t.get("desc", ""), t.get("agent", ""),
                t.get("refs", "") if isinstance(t.get("refs"), str) else " ".join(t.get("refs", []) or []),
            ]).lower()
            if ql in hay:
                out.append({
                    "task_id": t.get("id"),
                    "title": t.get("title", ""),
                    "status": t.get("status", ""),
                    "priority": t.get("priority", ""),
                    "due_date": t.get("due_date", ""),
                    "agent": t.get("agent", ""),
                    "project_id": p.get("id"),
                    "project_name": p.get("name", ""),
                })
                if len(out) >= limit:
                    # 命中到上限就早退：这不代表"只有这么多"，所以把这件事明确说出来
                    return {"results": out, "truncated": True, "limit": limit}
    return {"results": out, "truncated": False, "limit": limit}


def take_recovered_flag() -> tuple[bool, str]:
    """取走「这次读盘刚从 last-good/空结构自愈过」的标记（一次性，读完即清）。

    包成函数而不是让调用方直接摸全局，是因为两个写入进程都要上报它：界面靠它弹恢复提示，
    MCP 的 `kanban_list_projects` 要在智能体动手改看板**之前**先说清楚
    "这份数据刚被救回来过" —— 否则智能体会拿一份残缺的旧状态当事实继续往下写。
    """
    global _DATA_RECOVERED, _DATA_RECOVERED_FROM
    recovered, _DATA_RECOVERED = _DATA_RECOVERED, False
    recovered_from, _DATA_RECOVERED_FROM = _DATA_RECOVERED_FROM, ""
    return recovered, recovered_from


@app.get("/api/projects")
def list_projects():
    """总览：不含任务明细，带任务统计"""
    data = get_projects_data()
    out = []
    for p in data.get("projects", []):
        tasks = p.get("tasks", [])
        counts = {"待办": 0, "进行中": 0, "待审查": 0, "已完成": 0, "阻塞": 0}
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
    # 数据文件损坏并重建过：必须让用户知道，否则会以为项目凭空消失
    recovered, recovered_from = take_recovered_flag()
    return {"projects": out, "data_recovered": recovered, "recovered_from": recovered_from}


# [idem.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.idem 导入，见文件顶部 import。


# [idem.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.idem 导入，见文件顶部 import。


# [idem.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.idem 导入，见文件顶部 import。


# [idem.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.idem 导入，见文件顶部 import。


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectIn, client_ref: str = ""):
    now = now_str()
    project = {
        "id": uuid.uuid4().hex[:12], "name": body.name.strip(), "status": body.status,
        "progress": body.progress, "priority": body.priority,
        "tags": body.tags or [], "repo_path": body.repo_path.strip(),
        "background": body.background, "handoff_note": body.handoff_note,
        "next_action": body.next_action, "milestones": body.milestones or [],
        "tasks": [],
        "created_at": now, "updated_at": now,
    }
    ref = _clean_ref(client_ref)

    def worker(data: dict):
        if ref:
            hit = _idem_seen(data, ref)
            if hit:
                for p in data.get("projects", []):
                    if p.get("id") == hit:
                        return False, p      # 重试：原样返回，一个字节都不写
                data.get(IDEM_KEY, {}).pop(ref, None)   # 键指向的记录已被删，别再认它
# 首次落盘：磁盘上还没有数据文件时，说明看板从未被真正使用过，
        # 内置示例数据只用于引导界面，不该跟着用户的第一条真实数据一起写进去。
        if not data_file().exists():
            data["projects"] = []
        data.setdefault("projects", []).insert(0, project)
        if ref:
            _idem_remember(data, ref, project["id"])
        return True, project

    changed, result = mutate(worker)
    if not changed:
        return {**result, "client_ref_replayed": True}   # 同 create_task：复制后再打标记
    return result


# ---- 持久化自定义视图（Plane Views 轻量版） ----
# 视图 = 一条「当前筛选」快照：项目状态 filter + 搜索词 q，命名保存、一键切换。
# 存 projects.json 顶层 `views`（与项目同文件，走 mutate 单临界区），上限 MAX_SAVED_VIEWS。
# 保存语义是 upsert-by-name：同名再存 = 更新该视图并返回原 id（updated: true），不产生重复条目。
# 这部分是纯界面配置，不走 MCP（代理不需要替用户改界面偏好）。

@app.get("/api/views")
def list_views():
    """视图列表（含 0 条）。按创建顺序返回，前端下拉即用。"""
    return {"views": get_projects_data().get("views") or []}


@app.post("/api/views", status_code=201)
def save_view(body: ViewIn):
    """保存视图：name 已存在则更新其 filter/q（upsert），否则新建。"""
    now = now_str()
    name = body.name.strip()

    def worker(data: dict):
        views = data.setdefault("views", [])
        for v in views:
            if v.get("name") == name:
                v["filter"], v["q"] = body.filter, body.q.strip()
                v["updated_at"] = now
                return True, {**v, "updated": True}   # 复制后打标记，不回传 storage 引用
        if len(views) >= MAX_SAVED_VIEWS:
            raise HTTPException(400, f"最多保存 {MAX_SAVED_VIEWS} 个视图，先删掉不用的再存")
        view = {"id": uuid.uuid4().hex[:12], "name": name,
                "filter": body.filter, "q": body.q.strip(),
                "created_at": now, "updated_at": now}
        views.append(view)
        return True, view

    changed, result = mutate(worker)
    return result


@app.patch("/api/views/{vid}")
def rename_view(vid: str, body: ViewIn):
    """更新视图（改名/改条件）。改名撞同名视为重复保存（upsert 语义：直接改目标视图）。"""
    now = now_str()

    def worker(data: dict):
        views = data.get("views") or []
        for i, v in enumerate(views):
            if v.get("id") != vid:
                continue
            target = i
            new_name = body.name.strip()
            if new_name != v.get("name"):
                # 名字换给别的视图 = 撞名冲突，报 409（避免前端两个下拉项长得一样）。
                if any(x.get("name") == new_name for x in views if x.get("id") != vid):
                    raise HTTPException(409, "已有同名视图")
            views[target] = {"id": vid, "name": new_name, "filter": body.filter,
                             "q": body.q.strip(), "created_at": v.get("created_at", ""),
                             "updated_at": now}
            return True, views[target]
        raise HTTPException(404, "视图不存在")

    changed, result = mutate(worker)
    return result


@app.delete("/api/views/{vid}")
def delete_view(vid: str):
    """删除视图。视图是界面配置，删除后只影响菜单，不动任何项目数据。"""
    def worker(data: dict):
        views = data.get("views") or []
        nxt = [v for v in views if v.get("id") != vid]
        if len(nxt) == len(views):
            raise HTTPException(404, "视图不存在")
        data["views"] = nxt
        return True, {"ok": True}

    changed, result = mutate(worker)
    return result


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    """项目详情（P2 起附带 `milestones_progress`）。

    `milestones_progress` 由 `app/project_stats.py::milestone_progress` 计算：
    每个里程碑下已归组任务的完成进度（任务按 `milestone` 字段匹配里程碑 name）。
    进度口径只有这一处实现 —— 前端不重算，避免「两条路给不同答案」
    （本项目纪律，见 AGENTS.md 约定 20）。浅拷贝后再附加，绝不写进存储引用。
    """
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    p = dict(found[1])
    p["milestones_progress"] = project_metrics.milestone_progress(
        p.get("milestones") or [], p.get("tasks") or [])
    return p


@app.get("/api/projects/{pid}/stats")
def get_project_stats(pid: str):
    """项目级统计（P0-2）：任务状态分布 / 截止日期热力 / 依赖阻塞链。

    纯函数聚合在 `app/project_stats.py`（口径与 due._due_state、taskgraph.executable_tasks
对齐）；这里只负责找项目。读路径不写任何数据。
    """
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    return project_metrics.project_stats(found[1].get("tasks") or [])


@app.post("/api/projects/{pid}/assign")
def auto_assign_tasks(pid: str, body: AssignIn):
    """自动分配（P1）：把项目内『指定优先级 + 未完成 + 依赖已就绪』的任务批量指派智能体。

    写路径走 `mutate_project` 单临界区；分配计划由 `app/assign.py::plan_assign` 纯函数
    计算（就绪口径复用 `executable_tasks`，与 /next、/stats 同源，不在接口里重写一遍）。
    返回 `{assigned: [...], skipped: [...], count, skipped_count}` —— 跳过的任务带原因，
    调用方据此知道『没被分到的是没人做，还是已经有主』，不猜。
    """
    agent = (body.agent or "").strip()
    box: dict = {"result": None}

    def apply(p: dict, _data: dict):
        tasks = [t for t in (p.get("tasks") or []) if isinstance(t, dict)]
        to_assign, skipped = assign_module.plan_assign(
            tasks, agent, priority=body.priority, overwrite=body.overwrite)
        if not to_assign:
            box["result"] = {"assigned": [], "skipped": skipped,
                             "count": 0, "skipped_count": len(skipped),
                             "priority": body.priority, "agent": agent}
            return False                 # 一个字节都不写：没人需要分配
        now = now_str()
        for t in to_assign:
            t["agent"] = agent
            t["updated_at"] = now
        p["updated_at"] = now
        box["result"] = {
            "assigned": [{"id": t.get("id"), "title": t.get("title") or "(未命名任务)"}
                         for t in to_assign],
            "skipped": skipped,
            "count": len(to_assign),
            "skipped_count": len(skipped),
            "priority": body.priority,
            "agent": agent,
        }

    p = mutate_project(pid, apply)
    if p is None:
        raise HTTPException(404, "项目不存在")
    return box["result"]


def _apply_project(p: dict, body: ProjectIn) -> None:
    p.update({
        "name": body.name.strip(), "status": body.status,
        "progress": body.progress, "priority": body.priority,
        "tags": body.tags or [], "repo_path": body.repo_path.strip(),
        "background": body.background, "handoff_note": body.handoff_note,
        "next_action": body.next_action, "milestones": body.milestones or [],
        "updated_at": now_str(),
    })


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, str)]
    raise HTTPException(400, "增量参数要字符串或字符串数组")


# 集合字段的**增量**入口。整体覆盖照旧保留，另给 add / remove 两个动词：
# 只有整体覆盖时，智能体想加一条 refs 就得"先读整份 → 拼好 → 写回"，
# 而那一次读发生在临界区**外面** —— 期间桌面端加进去的 refs 会被这次写回整体抹掉。
# 这正是硬性约定 1 的丢更新形状，Backlog.md 的 `task_edit`（planSet/planAppend/planClear）
# 与 GitHub labels 的 PUT/POST/DELETE 三件套都是同一个解法。
COLLECTION_OPS = {"tags": "tags_add", "refs": "refs_add", "depends_on": "depends_on_add",
                  "acceptance": "acceptance_add"}


def _expand_ops(model, current: dict, changes: dict) -> dict:
    """把 `tags_add` / `tags_remove` 这类增量动词就地展开成整体值。

    必须在 `mutate` 的临界区里做（和 `_merge_into` 同一处）：基准值是**现在盘上的**那份，
    不是调用方早前取的那份快照。
    """
    fields = set(model.model_fields)
    out = dict(changes)
    # 拼错的动词必须先报错，不能让它走到下面被"只认模型字段"那道过滤顺手丢掉：
    # `tag_add`（少个 s）以前是 200 + 一句"已更新：tag_add"，两边谁都没说这个字段
    # 一个字都没变 —— 与 `due_date="下周三"` 静默变空串是同一类说谎的回执。
    for k in sorted(out):
        base = k[:-4] if k.endswith("_add") else k[:-7]
        if (k.endswith("_add") or k.endswith("_remove")) and base not in COLLECTION_OPS:
            raise HTTPException(
                400, f"不认识的操作「{k}」：支持增删动词的集合字段只有 "
                     f"{'、'.join(sorted(f'{b}_add / {b}_remove' for b in COLLECTION_OPS))}")
    for base, add_key in COLLECTION_OPS.items():
        rm_key = add_key.replace("_add", "_remove")
        if add_key not in out and rm_key not in out:
            continue
        if base not in fields:
            raise HTTPException(400, f"这个对象没有 {base} 字段，{add_key}/{rm_key} 无处可加")
        if base in out:
            raise HTTPException(400, f"{base} 不能同时给整体值和 {add_key}/{rm_key}")
        cur = [x for x in (current.get(base) or []) if isinstance(x, str)]
        if add_key in out:
            cur = cur + _as_list(out.pop(add_key))
        if rm_key in out:
            gone = set(_as_list(out.pop(rm_key)))
            cur = [x for x in cur if x not in gone]
        out[base] = cur
    return out


def _merge_into(model, current: dict, changes: dict):
    """把 `changes` 盖到「当前盘上的这一份」上，再交给 Pydantic 校验。

    只认模型声明过的字段；值为 `None` 视为"没给"（与 MCP 侧一直沿用的 `pick()` 语义一致），
    显式空串则是"要清空"，会照常传下去。
    """
    changes = _expand_ops(model, current, changes)
    fields = set(model.model_fields)
    merged = {k: v for k, v in current.items() if k in fields and v is not None}
    merged.update({k: v for k, v in changes.items() if k in fields and v is not None})
    return model(**merged)


def patch_project(pid: str, changes: dict) -> dict:
    """部分更新：未提到的字段保持**当前值**。

    与 `PUT /api/projects/{pid}`（整体覆盖）的差别不只是"少传几个字段"：
    调用方自己先读一份、改完再提交，那份快照是在临界区**外面**读的 ——
    桌面端在这两次读之间改过的字段，会被这次提交带回来的旧值反向覆盖（丢更新）。
    所以补全必须发生在 `mutate` 里面。MCP 那几个 `*_update_*` 工具原来正是这样在外面读的。
    """
    def apply(p: dict, _data: dict) -> None:
        _apply_project(p, _merge_into(ProjectIn, p, changes))

    p = mutate_project(pid, apply)
    if p is None:
        raise HTTPException(404, "项目不存在")
    return p


@app.patch("/api/projects/{pid}")
def patch_project_http(pid: str, body: dict):
    """部分更新项目：只改你点名的字段，其余保持**盘上此刻的值**。

    这条路由存在的理由不是"少写几个字段方便"：`PUT` 要求调用方先读一份、改完再整份写回，
    而那次读发生在临界区**外面** —— 桌面端与智能体同时动同一个项目时，后提交的一方
    会拿手里的旧值把对方刚写的字段抹掉。把补全挪进 `mutate` 里面才是解法，
    所以这里直接复用 `patch_project`：与 MCP 的 `kanban_update_project` 同一套实现，
    两侧不会漂，集合字段的 `tags_add` / `tags_remove` 也一并能用。
    """
    if not body:
        raise HTTPException(400, "PATCH 至少要点名一个字段（整体覆盖请用 PUT）")
    return patch_project(pid, body)


@app.put("/api/projects/{pid}")
def update_project(pid: str, body: ProjectIn):
    def apply(p: dict, _data: dict) -> None:
        _apply_project(p, body)

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

    # 记忆单独存在 memories.json，删项目不会顺带带走它：孤儿不会显示出来，
    # 却永久占着条数上限（约定 11），导出换机时也是一堆挂在已删项目上的记录。
    def drop_orphans(items: list[dict]):
        keep = [m for m in items if m.get("pid") != pid]
        if len(keep) == len(items):
            return False, None
        items[:] = keep
        return True, len(keep)

    _mutate_memories(drop_orphans)
    return {"ok": True}


# ---- 任务 CRUD ----

@app.post("/api/projects/{pid}/tasks", status_code=201)
def create_task(pid: str, body: TaskIn, client_ref: str = ""):
    now = now_str()
    ref = _clean_ref(client_ref)
    task = {
        "id": uuid.uuid4().hex[:12],
        "title": body.title.strip(), "desc": body.desc,
        "status": body.status, "priority": body.priority,
        "agent": body.agent.strip(), "due_date": body.due_date,
        "milestone": body.milestone, "created_at": now, "updated_at": now,
        "journal": [],
        "refs": clean_refs(body.refs),
        "acceptance": clean_acceptance(body.acceptance),
        "depends_on": [],          # 依赖在锁内算，见 apply
    }
    replay: dict = {}

    def apply(p: dict, data: dict):
        # 取兄弟任务、清洗依赖、判环全部放进同一个临界区。先前是拿锁外快照算好
        # depends_on 再另起一次锁写入 —— 两个并发请求各自都判得过，
        # 合起来却能把依赖写成一个环（环一进来 topo_order 就静默退化成原序）。
        if ref:
            hit = _idem_seen(data, ref)
            if hit:
                for t in (p.get("tasks") or []):
                    if t.get("id") == hit:
                        replay["task"] = t
                        return False      # 重试：原样返回那条，一个字节都不写
                data.get(IDEM_KEY, {}).pop(ref, None)   # 记录已删，别再认这个键
        siblings = [t for t in (p.get("tasks") or []) if isinstance(t, dict)]
        task["depends_on"] = clean_deps(body.depends_on, {t.get("id") for t in siblings})
        if has_cycle(task_graph(siblings + [task])):
            raise HTTPException(400, "依赖会形成循环，已放弃创建")
        p.setdefault("tasks", []).insert(0, task)
        p["updated_at"] = now
        if ref:
            _idem_remember(data, ref, task["id"])

    p = mutate_project(pid, apply)
    if p is None:
        raise HTTPException(404, "项目不存在")
    if replay:
        # 只在"命中幂等键"时多带一个标记：调用方（尤其智能体）否则分不清
        # 这是刚建出来的还是重试拿回来的那条。复制一份再打标记 ——
        # 直接改 `replay["task"]` 会把标记写进盘上的任务。
        return {**replay["task"], "client_ref_replayed": True}
    return task


def _apply_task(t: dict, p: dict, tid: str, body: TaskIn) -> None:
    # 同 create_task：兄弟任务、自依赖、环判定都必须在锁内做，
    # 否则两个并发请求能合力把一个环写进依赖图。
    now = now_str()
    if tid in (body.depends_on or []):
        raise HTTPException(400, "任务不能依赖自己")
    siblings = [x for x in (p.get("tasks") or [])
                if isinstance(x, dict) and x.get("id") != tid]
    deps = clean_deps(body.depends_on, {x.get("id") for x in siblings})
    if has_cycle(task_graph(siblings + [{"id": tid, "depends_on": deps}])):
        raise HTTPException(400, "依赖会形成循环，已放弃更新")

    old_status = t.get("status", "")
    t.update({
        "title": body.title.strip(), "desc": body.desc,
        "status": body.status, "priority": body.priority,
        "agent": body.agent.strip(), "due_date": body.due_date,
        "milestone": body.milestone, "updated_at": now,
        "refs": clean_refs(body.refs),
        "acceptance": clean_acceptance(body.acceptance),
        "depends_on": deps,
    })
    # 状态流转自动沉淀过程轨迹
    if body.status != old_status:
        _append_journal(t, f"状态：{old_status} → {body.status}", now)
    p["updated_at"] = now


def patch_task(tid: str, changes: dict, *, with_project: bool = False):
    """部分更新任务：未提到的字段取**当前盘上的值**（理由同 `patch_project`）。

    `with_project=True` 时返回 `(任务, {"project": 项目名, "unblocked": [...]})`：
    智能体侧的回复要报"这个任务在哪个项目"，而它不该为此在临界区外再读一整份数据。
    `unblocked` 是这次改动**新解锁**的任务 —— 把前置做成"已完成"之后，代理若不额外
    问一遍 `kanban_next_task` 就不知道自己刚放行了哪条活（agendum 的 `pm_done` 就报这个）。
    """
    box: dict = {}

    def apply(t: dict, p: dict) -> None:
        # 基线只在"可能解锁别人"的那次改动前取：executable_tasks 是遍历依赖图的活
        before = ({x.get("id") for x in executable_tasks(p.get("tasks") or [])}
                  if changes.get("status") == "已完成" else None)
        _apply_task(t, p, tid, _merge_into(TaskIn, t, changes))
        box["project"] = p.get("name", "")
        if before is not None:
            box["unblocked"] = [{"id": x.get("id"), "title": x.get("title")}
                                for x in executable_tasks(p.get("tasks") or [])
                                if x.get("id") not in before]

    t = mutate_task(tid, apply)
    if t is None:
        raise HTTPException(404, "任务不存在")
    if not with_project:
        return t
    return t, {"project": box.get("project", ""), "unblocked": box.get("unblocked", [])}


@app.get("/api/tasks/{tid}")
def get_task(tid: str):
    """按任务 id 反查任务与所属项目。

    以前只能"先知道项目、再在项目里找任务"，而 AI 指令回传的操作常常只带 `task_id`
    （列表接口不含任务，前端无法从 id 反查项目），于是明明有 id 却报"找不到对应任务"。
    id 是智能体唯一确定持有的东西，这条读路径本来就该有。
    """
    data = get_projects_data()
    for p in data.get("projects", []):
        for t in p.get("tasks", []):
            if t.get("id") == tid:
                return {"task": t, "project_id": p.get("id"), "project_name": p.get("name")}
    raise HTTPException(404, "任务不存在")


@app.patch("/api/tasks/{tid}")
def patch_task_http(tid: str, body: dict):
    """部分更新任务。理由与 `PATCH /api/projects/{pid}` 同：拖一张卡片只意味着改一个
    `status`，不该把智能体刚补进去的 `refs` / `depends_on` / `desc` 按旧快照写回去。

    返回体与 `PUT` 一致（就是那条任务），不在同一个资源上给两种形状。
    """
    if not body:
        raise HTTPException(400, "PATCH 至少要点名一个字段（整体覆盖请用 PUT）")
    return patch_task(tid, body)


@app.put("/api/tasks/{tid}")
def update_task(tid: str, body: TaskIn):
    def apply(t: dict, p: dict) -> None:
        _apply_task(t, p, tid, body)

    t = mutate_task(tid, apply)
    if t is None:
        raise HTTPException(404, "任务不存在")
    return t


MAX_JOURNAL = 50           # 每个任务最多留多少条过程记录
MAX_JOURNAL_TEXT = 500     # 单条正文上限：这一个数同时是 HTTP/MCP 的入参上限与落盘长度
JOURNAL_SNIPPET = 60       # 折叠摘要里每条旧记录保留的字符数（摘要整体仍受 MAX_JOURNAL_TEXT 约束）
# 验收标准（对标 Backlog.md 的可复用 DoD 清单）：任务"怎么算完成"要明说，
# 否则"待审查→已完成"全凭当时的人/智能体心情，接手方也没有验收清单可照。
MAX_ACCEPTANCE_ITEMS = 10  # 每个任务最多多少条验收标准
MAX_ACCEPTANCE_TEXT = 120  # 单条验收标准长度上限
                           # （以前入参收 2000、落盘悄悄截到 500，两侧的"1200 字记录"
                           # 会少掉 700 字而回执不说 —— 现在超限在边界就被挡）


def _append_journal(t: dict, text: str, when: str = "") -> None:
    """追加一条过程轨迹，超出上限的旧条目**折叠成摘要**而不是直接丢掉。

    journal 存在 projects.json 里 —— 那是每次读写都要整份解析的核心文件，
    状态流转还会自动追加，不设上限就是"悄悄变胖"：读写变慢、备份变大、交接包变长。
    但"只留最近 N 条"意味着早期记录无声消失（为什么改方向、哪天开始卡住的）——
    对标 Beads 的旧记录衰减摘要：溢出部分折成一条摘要留在最前面，体量有上限、脉络不丢。
    """
    j = t.get("journal")
    if not isinstance(j, list):
        j = t["journal"] = []
    j.append({"time": when or now_str(), "text": (text or "")[:MAX_JOURNAL_TEXT]})
    if len(j) > MAX_JOURNAL:
        t["journal"] = compress_journal(j)


def compress_journal(entries: list[dict]) -> list[dict]:
    """把超出 `MAX_JOURNAL` 的最旧条目折成一条摘要，总条数压回上限内。

    纯函数：不修改入参。摘要条目带 `folded` 计数（累计折进去多少条原始记录，
    再次折叠时继承），`time` 是被折区间的首尾日期，正文仍是受限的普通文本 ——
    前端/交接包不需要任何特殊处理就能显示，只是这条以【早期…已折叠】开头。
    """
    entries = [e for e in entries if isinstance(e, dict)]
    if len(entries) <= MAX_JOURNAL:
        return entries
    cut = len(entries) - MAX_JOURNAL          # 溢出条数；再多折 1 条进来给摘要腾位置
    overflow, keep = entries[:cut + 1], entries[cut + 1:]
    folded = sum(int(e.get("folded") or 0) or 1 for e in overflow)
    digest = "；".join((e.get("text") or "")[:JOURNAL_SNIPPET] for e in overflow)
    summary = {
        "time": f"{(overflow[0].get('time') or '')[:10]}~{(overflow[-1].get('time') or '')[:10]}",
        "text": (f"【早期 {folded} 条已折叠】{digest}")[:MAX_JOURNAL_TEXT],
        "folded": folded,
    }
    return [summary] + keep


def clean_journal(raw) -> list[dict]:
    """规范化过程轨迹：只留字典条目、限长，超出 `MAX_JOURNAL` 的折叠成摘要
    （导入的脏数据与手改的 projects.json 也走这里，读路径上自愈）"""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        e = {
            "time": str(item.get("time") or "")[:32],
            "text": str(item.get("text") or "")[:MAX_JOURNAL_TEXT],
        }
        # folded 计数跟着条目走：折叠摘要再次落入溢出区时要按它折进来的原始条数累计
        if isinstance(item.get("folded"), int) and item["folded"] > 0:
            e["folded"] = item["folded"]
        out.append(e)
    return compress_journal(out)


def clean_acceptance(raw) -> list[str]:
    """规范化验收标准（对标 Backlog.md 的 DoD 清单）：只留字符串、去空去重、限量限长。

    接受字符串或列表（列表元素逐条清洗）；非字符串条目转成字符串保留 ——
    智能体经 MCP 传数字/布尔时，转写比整条丢弃更接近调用方本意。
    """
    if isinstance(raw, str):
        raw = raw.replace("\r", "").split("\n")
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        s = (item if isinstance(item, str) else str(item)).strip()[:MAX_ACCEPTANCE_TEXT]
        if s and s not in out:
            out.append(s)
        if len(out) >= MAX_ACCEPTANCE_ITEMS:
            break
    return out


class JournalIn(BaseModel):
    # 上限与 `MAX_JOURNAL_TEXT` 同一个数（以前声明收 2000、落盘偷偷截到 500 ——
    # 智能体写 1200 字的记录会无声掉一半，而回执还写着"已记录"）。
    # 对账钉子：test_journal_limit_is_one_number_everywhere
    text: str = Field(..., min_length=1, max_length=MAX_JOURNAL_TEXT)


def append_task_journal(tid: str, text: str) -> dict:
    """追加一条过程记录，返回**追加后的任务本身**。

    返回整个任务而不是那条记录，是为了让 MCP 那类调用方一次读改写就能同时拿到
    任务标题和新记录 —— 它原来为了在回复里报出标题，会在临界区外再多读一整份数据。
    """
    def apply(t: dict, p: dict) -> None:
        now = now_str()
        _append_journal(t, text.strip(), now)
        p["updated_at"] = now

    t = mutate_task(tid, apply)
    if t is None:
        raise HTTPException(404, "任务不存在")
    return t


@app.post("/api/tasks/{tid}/journal", status_code=201)
def add_task_journal(tid: str, body: JournalIn):
    """手动追加一条过程记录（做了什么、改了什么决策、踩了什么坑）"""
    return append_task_journal(tid, body.text)["journal"][-1]


@app.delete("/api/tasks/{tid}")
def delete_task(tid: str):
    box = {"ok": False}

    def worker(data: dict):
        for p in data.get("projects", []):
            for t in list(p.get("tasks", [])):
                if t.get("id") == tid:
                    p["tasks"].remove(t)
                    # 把指向它的依赖一并摘掉，否则剩下的任务永远"等一个不存在的东西"
                    for other in p.get("tasks", []):
                        if tid in (other.get("depends_on") or []):
                            other["depends_on"] = [d for d in other["depends_on"] if d != tid]
                    p["updated_at"] = now_str()
                    box["ok"] = True
                    return True, True
        return False, False

    changed, _ = mutate(worker)
    if not changed:
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


# ---- 交接摘要导出 ----

def _local_url(path: str) -> str:
    """本机服务的直链（只给 127.0.0.1，不给局域网地址——不出本机是硬边界）"""
    return f"http://127.0.0.1:{SERVICE_PORT}{path}"


# [handoff_meta.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.handoff_meta 导入，见文件顶部 import。


# [handoff_meta.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.handoff_meta 导入，见文件顶部 import。


# [handoff_meta.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.handoff_meta 导入，见文件顶部 import。


# [handoff_meta.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.handoff_meta 导入，见文件顶部 import。


def _build_handoff_markdown(p: dict, memory_rows: list[dict] | None = None,
                            redact: bool = False, omit: set[str] | None = None) -> str:
    """`memory_rows` = 这个项目的记忆列表。批量导出（export-all）必须一次读盘后传进来：
    逐个项目各自 `_load_memories()` 会让 memories.json 的读取次数等于项目数。

    `redact=True` 时正文里那三条实时直链也要带上 `?redact=1`：否则一份"已脱敏"的交接物
    递给对方的同时递了三条取原文的路 —— 掩码盖住了正文，链接却指向未盖的那份。

    `omit` = 要省掉的小节（见 `HANDOFF_SECTIONS`）。`_budget_note` 会告诉智能体"本包偏长、
    建议裁剪"，以前却没有任何一条路能真的裁 —— 光有警告没有解法。省掉什么必须在文末留痕，
    否则接手方会把"没写"读成"没有"。
    """
    omit = omit or set()
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
    # 省掉 git 小节要连子进程一起省：`_git_line_for_handoff` 每次都要跑 git，
    # 裁剪时若还去取，就白付了这份开销。
    git_block = "" if "git" in omit else _git_line_for_handoff(p)
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
        mems = memory_rows if memory_rows is not None else \
            [m for m in _load_memories() if m.get("pid") == p.get("id")]
        expired_mems = [m for m in mems if _memory_expired(m)]
        mems = [m for m in mems if not _memory_expired(m)]
        if expired_mems and "memories" not in omit:
            # 省了几个要说话：过期记忆是有意不带的，但接手方不能把"没写"读成"没有"
            lines.append(f"> ⚠️ 另有 {len(expired_mems)} 条已过时效的记忆未列入"
                         "（expires 早于今天；要翻旧账去看板「项目记忆」面板）。")
            lines.append("")
        if mems and "memories" not in omit:
            lines.append("### 已沉淀记忆（结构化要点）")
            lines.append("")
            for m in mems[-15:]:
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
    lines.append("## 实时上下文（比上面这份快照更新）")
    lines.append("")
    lines.append("本看板是本机服务，上面内容是导出那一刻的快照。同一台机器上的智能体可以直接取最新的：")
    lines.append("")
    pid = p.get("id") or ""
    suffix = "?redact=1" if redact else ""
    lines.append(f"- 结构化接手包（喂给智能体）：`{_local_url('/api/context/' + pid)}{suffix}`")
    lines.append(f"- Markdown 交接包：`{_local_url('/api/export/' + pid)}{suffix}`")
    lines.append(f"- 自包含接手页（人看的单文件网页）：`{_local_url('/api/export/page/' + pid)}{suffix}`")
    lines.append("")
    lines.append("服务没开时这三个链接取不到，用上面的快照即可。")
    lines.append("")
    lines.append("---")
    lines.append("由 AI 交接看板自动生成")
    return "\n".join(lines)


@app.get("/api/export/{pid}")
def export_handoff(pid: str, redact: int = 0, omit_sections: str = ""):
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    omit = parse_handoff_sections(omit_sections, param="omit_sections")
    md = _build_handoff_markdown(found[1], redact=bool(redact), omit=omit)
    if redact:
        md = redact_text(md)
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


# =====================================================================
#  统计 / 就绪度 / 完整交接包 / 趋势
# =====================================================================

# [due.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.due 导入，见文件顶部 import。


@app.get("/api/stats")
def api_stats():
    data = get_projects_data()
    projects = data.get("projects", [])
    status_dist = {s: 0 for s in DEFAULT_STATUS}
    task_dist = {s: 0 for s in DEFAULT_TASK_STATUS}
    total_tasks = 0
    blocked = 0
    overdue = 0
    duesoon = 0
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
            ds = _due_state(t)
            if ds == "overdue":
                overdue += 1
            elif ds == "soon":
                duesoon += 1
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
        "overdue_tasks": overdue,
        "duesoon_tasks": duesoon,
        "avg_progress": avg_progress,
        "avg_readiness": avg_readiness,
        "readiness": readiness,
    }


class SaveExportBody(BaseModel):
    filename: str = "交接包.md"
    content: str = ""
    path: str | None = None  # 可选：显式保存路径（用户输入）
    overwrite: bool = False  # 显式路径时是否允许覆盖已有文件（默认不覆盖）


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
        # 必须给绝对路径：相对路径是按**服务进程的当前目录**解析的（源码态是仓库根、
        # 打包态是 exe 所在目录），于是 "AGENTS.md" 这种输入会写到应用自己身上，
        # 而 _forbidden_roots 那套黑名单也完全拦不到。
        if not target.is_absolute():
            raise HTTPException(400, "请填写绝对路径（含盘符），例如 D:\\work\\demo\\AGENTS.md")
        suffix = target.suffix.lower()
        if suffix not in EXPORT_SUFFIXES:
            raise HTTPException(400, "仅支持保存为 " + " / ".join(EXPORT_SUFFIXES) + " 文件")
        if _is_forbidden_target(target):
            raise HTTPException(400, "不允许写入系统目录，请换一个路径")
        if target.exists() and not body.overwrite:
            # 与 /api/export/agent-files 同一口径：这个路径很可能指向用户手写的文件
            # （默认填的就是 <仓库>\AGENTS.md），静默覆盖等于内容丢失。
            raise HTTPException(409, f"文件已存在：{target}（要覆盖请勾选「覆盖已有文件」）")
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


def build_export_all(redact: bool = False, omit: set[str] | None = None) -> str:
    """全项目合并交接包的正文。

    单独一个函数是为了让 MCP 的 `kanban_export_all` 复用同一份渲染，
    而不是抄第二遍（本项目已经有过两份 `_projects_brief` 分叉的前例）。
    """
    data = get_projects_data()
    projects = data.get("projects", [])
    mems_by_pid = _memories_by_pid()
    head = f"# AI 交接看板 · 全项目交接包\n\n> 共 {len(projects)} 个项目 · 导出时间 {now_str()}\n"
    parts = [head]
    for p in projects:
        md = _build_handoff_markdown(p, mems_by_pid.get(p.get("id"), []),
                                     redact=redact, omit=omit)
        parts.append("\n\n---\n\n" + (redact_text(md) if redact else md))
    return "".join(parts)


@app.get("/api/export-all")
def export_all(redact: int = 0, omit_sections: str = ""):
    omit = parse_handoff_sections(omit_sections, param="omit_sections")
    return PlainTextResponse(build_export_all(bool(redact), omit),
                             media_type="text/markdown; charset=utf-8")


@app.get("/api/today")
def today_tasks(limit: int = 30):
    """跨项目的「现在就能动手」：依赖已满足、自己未完成的任务，按 优先级 → 截止 → 最近更新 排。

    `executable_tasks()` 此前只有 MCP 侧在用 —— 人想回答"今天该干什么"只能逐个项目点开。
    已完成的项目整跳过；阻塞中的任务会保留并标出（它也是现在就该处理的）。
    """
    data = get_projects_data()
    pri_rank = {"高": 0, "中": 1, "低": 2}
    out: list[dict] = []
    projects_seen = 0
    for p in data.get("projects", []):
        if p.get("status") == "已完成":
            continue
        ready = executable_tasks(p.get("tasks") or [])
        if not ready:
            continue
        projects_seen += 1
        for t in ready:
            out.append({
                "project_id": p["id"], "project_name": p["name"],
                "project_status": p.get("status", ""),
                "id": t.get("id"), "title": t.get("title"),
                "status": t.get("status", "待办"), "priority": t.get("priority", "中"),
                "due_date": t.get("due_date") or "",
                "refs": t.get("refs") or [],
                "updated_at": t.get("updated_at") or "",
            })
    # 稳定排序：先按更新时间倒序，再按（优先级、截止）升序
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    out.sort(key=lambda x: (pri_rank.get(x.get("priority"), 1), x.get("due_date") or "9999-12-31"))
    shown = max(1, min(limit, 200))
    return {
        "tasks": out[:shown],
        "total": len(out),
        # 截断要说出来：以前只回 total，前端一个数都不显示，
        # 第 51 条开始的任务对用户就是"不存在"。
        "truncated": len(out) > shown,
        "project_count": projects_seen,
    }


@app.get("/api/inbox")
def inbox():
    """需要人拍板的事，跨项目汇总：逾期（改期还是推进？）、待审查（验不验收？）、
    阻塞（障碍解不解？）。

    对标 Multica 的 Inbox 哲学——"agent 需要人拍板才 ping 你，不是每步都 ping"：
    这里**只列等人的**。依赖没满足的任务会随前置完成自然流转（/api/today 管"现在能
    做什么"），临期（2 天内）只是提醒不是决定（顶部横幅已经管了），都不进这份清单。
    每条任务只进一类，优先级：待审查（最接近完成）> 阻塞 > 逾期。
    """
    data = get_projects_data()
    today = datetime.now().strftime("%Y-%m-%d")
    items = []
    for p in data.get("projects", []):
        for t in (p.get("tasks") or []):
            if not isinstance(t, dict) or t.get("status") == "已完成":
                continue
            kind = ""
            if t.get("status") == "待审查":
                kind = "review"
            elif t.get("status") == "阻塞":
                kind = "blocked"
            else:
                due = (t.get("due_date") or "").strip()
                if due and due < today:
                    kind = "overdue"
            if kind:
                items.append({
                    "kind": kind,
                    "project_id": p.get("id"), "project_name": p.get("name", ""),
                    "task_id": t.get("id"), "title": t.get("title", ""),
                    "priority": t.get("priority", "中"),
                    "due_date": t.get("due_date") or "",
                })
    order = {"review": 0, "blocked": 1, "overdue": 2}
    items.sort(key=lambda x: (order[x["kind"]], x["priority"] == "低",
                              x["due_date"] or "9999-12-31"))
    counts = {k: sum(1 for i in items if i["kind"] == k) for k in ("review", "blocked", "overdue")}
    return {"items": items, "counts": counts, "total": len(items),
            "generated_at": now_str()}


@app.get("/api/export/page/{pid}")
def export_handoff_page(pid: str, redact: int = 0):
    """自包含接手页：单文件 HTML，离线可用，人和智能体都能读。

    为什么是文件不是链接：链接要出本机（数据离开 %USERPROFILE%\\.ai-kanban 就突破安全边界），
    还会过期。文件 100% 可达，且同样能被智能体读。
    """
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    p = found[1]
    md = _build_handoff_markdown(p, redact=bool(redact))
    page = build_handoff_page(
        p, md,
        git_block=_git_line_for_handoff(p),
        port=SERVICE_PORT,
        generated_at=now_str(),
        link_suffix="?redact=1" if redact else "",
    )
    if redact:
        page = redact_text(page)
    return HTMLResponse(page, headers={"Content-Disposition":
                                       f'attachment; filename="handoff-{pid}.html"'})


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
            # 报告正文是模型写的：`health` 可以是 null、"82 分"，也可以是一段带尖括号的字符串。
            # 这里就归一成 0-100 的整数、坏点丢掉 —— 它一路走到前端 SVG 的 <title> 插值里，
            # 而那里曾是同一个函数中唯一没过 esc 的一处（`_attach_trends` 是 int 过的）。
            try:
                score = max(0, min(100, int(it.get("health"))))
            except (TypeError, ValueError):
                continue
            series_map.setdefault(pid, {"id": pid, "name": it.get("name", ""), "points": []})
            series_map[pid]["points"].append({"time": t, "health": score})
    return {"series": list(series_map.values())}


# ---- AGENTS.md 生成（对接行业标准） ----

AGENTS_MEMORY_LINES = 12     # 进 AGENTS.md 的记忆条数上限（新的优先）：这文件是给代理读的


def _project_memories(pid: str) -> list[dict]:
    """这个项目该被 AGENTS.md 召回的记忆（新的在前，取前 AGENTS_MEMORY_LINES 条）。

    `kanban_memory_add` 对模型承诺过"记忆会在交接包、AGENTS.md 和 AI 监管里自动召回"，
    而以前只有前两处真读了记忆 —— 声明与实现不一致时，用户按说明记了坑，
    换工具接手还是踩一遍。这里把最后一处接上，并限死条数（整份文件是给代理看的，
    无限追加会把真正重要的构建约定挤出去）。
    已过期的不召回（`expires` 就是"过了这天别再当真"的意思）。
    """
    rows = [m for m in list_memories(pid=pid)["memories"] if not m.get("expired")]
    return rows[:AGENTS_MEMORY_LINES]


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
    mems = _project_memories(p.get("id", ""))
    if mems:
        lines.append("## 项目记忆（决策 / 坑 / 约定）")
        lines.append("")
        for m in mems:
            lines.append(f"- 【{m.get('type') or '事实'}】{m.get('text') or ''}"
                         f"（{m.get('time') or ''}）")
        lines.append("")
    return "\n".join(lines)


@app.post("/api/export/agentsmd/{pid}")
def export_agentsmd(pid: str, redact: int = 0):
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    p = found[1]
    text, src, ep = _generate_agents_md(p)
    if redact:
        text = redact_text(text)
    return {"text": text, "endpoint": ep, "source": src}


def _generate_agents_md(p: dict) -> tuple[str, str, str]:
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
                     for m in _project_memories(p.get("id", ""))],
    }, ensure_ascii=False, indent=1))
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [{"role": "system", "content": sys_msg},
                                  {"role": "user", "content": user_msg}], temperature=0.2, feature="AGENTS.md")
        if not text or len(text.strip()) < 30:
            raise ValueError("生成内容过短")
        return text.strip(), "ai", ep
    except Exception:
        return _build_agents_md_local(p), "local", ""


# ---- 多智能体入口文件（对比同类：Claude/Cursor/Copilot/Gemini/Cline/Aider/Windsurf 各要各的入口） ----

# [agentfiles.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.agentfiles 导入。

# [agentfiles.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.agentfiles 导入。


# [agentfiles.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.agentfiles 导入。


# [agentfiles.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.agentfiles 导入。


class AgentFilesBody(BaseModel):
    dir: str = ""                       # 目标仓库根目录；留空表示只预览不写盘
    targets: list[str] | None = None
    content: str | None = None          # 前端已预览过的内容，传回来可省一次模型调用
    overwrite: bool = False             # 默认不覆盖仓库里已有的入口文件（可能是用户手写的）


@app.post("/api/export/agent-files/{pid}")
def export_agent_files(pid: str, body: AgentFilesBody):
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    p = found[1]

    if body.content and body.content.strip():
        content, source, endpoint = body.content.strip(), "passed-in", ""
    else:
        content, source, endpoint = _generate_agents_md(p)

    entries = build_agent_entries(p, content, body.targets)
    result = {
        "source": source, "endpoint": endpoint,
        "files": [{"path": rel, "content": text} for rel, text in entries.items()],
        "written": [], "skipped_existing": [], "failed": [], "preview": True,
    }

    target_dir = (body.dir or "").strip()
    if not target_dir:
        return result

    base = Path(target_dir)
    if _is_forbidden_target(base):
        raise HTTPException(400, "不允许写入系统目录，请换一个项目目录")
    for rel, text in entries.items():
        target = base / rel
        if _is_forbidden_target(target):
            result["failed"].append(rel)
            continue
        if target.exists() and not body.overwrite:
            # 仓库里已经有这个文件（可能是用户手写的）——默认不覆盖，交由用户决定
            result["skipped_existing"].append(rel)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            result["written"].append(rel)
        except Exception:
            result["failed"].append(rel)

    result["dir"] = str(base)
    result["preview"] = False
    if not result["written"] and result["failed"]:
        raise HTTPException(500, "没有任何文件写入成功，请检查目录是否存在且可写")
    return result


# ---- 数据一键备份 ----

@app.post("/api/backup")
def create_backup():
    backups = data_dir() / "backups"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 名字只精确到秒，而"备份"恰恰是最容易被连着调两次的操作（代理批量改动前拍一份、
    # 又拍一份；或导入流程自己先备份一次）。同名会直接落进同一个目录，
    # 第二次把第一次的内容覆盖掉 —— 用户以为留了两份退路，其实只有一份。
    folder = backups / f"backup_{ts}"
    n = 2
    while folder.exists():
        folder = backups / f"backup_{ts}-{n}"
        n += 1
    folder.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("projects.json", "config.json", "memories.json"):
        src = data_dir() / name
        if src.exists():
            shutil.copy2(src, folder / name)
            copied.append(name)
    # 监管报告一并备份（恢复后健康趋势才不会空）
    reports = reports_dir()
    if reports.exists() and any(reports.iterdir()):
        report_dst = folder / "reports"
        shutil.copytree(reports, report_dst, dirs_exist_ok=True)
        copied.append("reports/")
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
        out.append({"name": b.name, "time": _backup_label(b.name), "files": files})
    return {"backups": out}


def _backup_label(name: str) -> str:
    """快照目录名 → 给人看的时刻。

    同秒重入时名字带 `-2` 后缀（见 create_backup），所以这里只认前两段，
    并把后缀原样接在时间后面 —— 用字符串 replace 硬凑会把 "…-2" 当成时刻显示。
    """
    stem = name[len("backup_"):] if name.startswith("backup_") else name
    stamp, _, suffix = stem.partition("-")
    parts = stamp.split("_")
    if len(parts) == 2 and len(stamp) == 15:
        try:
            shown = datetime.strptime(stamp, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            shown = stamp
    else:
        shown = stamp
    return f"{shown}（第 {suffix} 次）" if suffix else shown


# ---- 全量数据导出 / 导入（跨机迁移） ----

@app.get("/api/data/export")
def export_all_data():
    data = get_projects_data()
    return {
        "app": "ai-kanban",
        "schema_version": SCHEMA_VERSION,
        "version": APP_VERSION,
        "exported_at": now_str(),
        "projects": data.get("projects", []),
        # 记忆层单独存一个文件，不带出去就等于换机后"决策/坑/约定"全丢
        "memories": _load_memories(),
        # 自定义视图是全局界面配置，换机不带着走 = 用户攒的筛选快照白存
        "views": data.get("views", []),
    }


class ImportBody(BaseModel):
    content: str = Field(..., min_length=2, max_length=10_000_000)


def _writable_projects(projects: list) -> tuple[list, list[str]]:
    """挑出"以后读得回来"的项目，并把丢掉的条目说清楚：返回 (可用列表, 丢弃说明)。

    读路径是按 `p["id"]` / `p["name"]` 硬下标取值的，而 `_sanitize` 明写"没有 id 的条目
    不补"（理由见 `_dedupe_ids`）—— 所以一条缺 id 的项目一旦落盘，**每一个读接口都会 500**，
    而主文件仍是合法 JSON、自愈不会介入。凡是"整份覆盖"的入口（导入、恢复）都必须先过这道。

    丢字必须报出来而不是悄悄少一个：用户以为恢复回来的是完整的一份，
    而"少了两个项目"和"一个没少"在现在的界面上长得一模一样。
    """
    kept: list = []
    dropped: list[str] = []
    for i, p in enumerate(projects):
        name = p.get("name") if isinstance(p, dict) else None
        if isinstance(p, dict) and p.get("id") and name:
            kept.append(p)
            continue
        why = ("不是对象" if not isinstance(p, dict)
               else ("缺 id" if not p.get("id") else "缺名称"))
        dropped.append(f"「{str(name)[:40]}」{why}" if name else f"第 {i + 1} 条：{why}")
    return kept, dropped


def _writable_memories(rows: list) -> list[dict]:
    """记忆层的同一道筛选。导入以前要求 id+text、恢复只要求 id —— 同一个"整份覆盖"
    两条路两套规则，正是这个项目反复出事的那个形状；一条空文本的记忆在两处都应该丢。
    """
    return [m for m in rows if isinstance(m, dict) and m.get("id") and m.get("text")]


@app.post("/api/data/import")
def import_all_data(body: ImportBody):
    try:
        parsed = json.loads(body.content)
    except Exception:
        raise HTTPException(400, "不是合法的 JSON 文件")
    if not isinstance(parsed, dict):
        raise HTTPException(400, "文件结构不对：顶层应为对象")
    if not isinstance(parsed.get("projects"), list):
        raise HTTPException(400, "文件里没有 projects 数据")
    try:
        cleaned = _sanitize(parsed)
    except Exception:
        cleaned = parsed
    projects = cleaned.get("projects")
    if not isinstance(projects, list):
        raise HTTPException(400, "文件里没有 projects 数据")
    projects, dropped = _writable_projects(projects)
# 记忆层：老导出文件里没有这个键 —— 那就**不动**当前记忆，不能因为导入而清空
    raw_mem = cleaned.get("memories")
    mem_items = None
    if isinstance(raw_mem, list):
        mem_items = _writable_memories(raw_mem)
    # 自定义视图：同一语义 —— 老导出没有 `views` 键就是不动现有视图；
    # 有了就整份替换（它是全局配置，不属于任何项目，按目录快照整体迁移才对）。
    raw_views = cleaned.get("views")
    view_items = None
    if isinstance(raw_views, list):
        view_items = _clean_views(raw_views)   # _sanitize 已清一道，这里再兜一道

    # 导入前自动留一份快照（零删除安全网，随时可从备份回滚）
    try:
        create_backup()
    except Exception:
        pass

    def worker(data: dict):
        # 读-改-写必须在同一个临界区里（硬性约定 1）：先前这里取了一次读锁拿快照、
        # 再取一次写锁整份覆盖，两次取锁之间别的进程/接口写入的项目会被无声抹掉。
        data["projects"] = projects
        if view_items is not None:
            data["views"] = view_items
        return True, {"ok": True, "imported": len(projects)}

    result = mutate(worker)[1]
    if dropped:
        result["dropped_invalid"] = dropped
        result["dropped_note"] = (f"{len(dropped)} 条项目缺 id 或名称 —— 留下会让整个看板读不回来，"
                                  "已跳过：" + "；".join(dropped[:5]))
    declared = parsed.get("schema_version")
    if isinstance(declared, int) and declared > SCHEMA_VERSION:
        # 只说话、不拒绝：这份导出多半来自更新的看板，字段是超集，硬挡等于把用户关在门外。
        # 但"导入的东西比本程序新一版"必须让人看见，而不是事后发现某些字段被规范化掉了。
        result["schema_note"] = (f"导入文件声明结构 v{declared}，本看板认得 v{SCHEMA_VERSION}，"
                                 "较新的字段可能被规范化掉")
    result["schema_version"] = SCHEMA_VERSION
    if mem_items is not None:
        # 与 projects 分属两个文件，只能各自进临界区替换（中间断电最坏是"项目已换、
        # 记忆还是旧的"，不会互相写坏）；快照已在上面留好，可回滚。
        def w(items: list[dict]):
            items[:] = mem_items
            return True, None

        _mutate_memories(w)
        result["memories"] = len(mem_items)
    return result


# ---- LLM 用量统计 ----

@app.get("/api/usage")
def usage_stats():
    f = llm._usage_file()
    recs = []
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    # 价格与预算来自 AI 配置（端点级单价 / ai 级月度预算），
    # 物化为纯函数输入后聚合 —— 路由只负责"读文件 + 读配置 + 透传"。
    cfg = llm.load_config()
    price_map = usage_metrics.price_map_from_endpoints((cfg.get("ai") or {}).get("endpoints"))
    budget_limit = (cfg.get("ai") or {}).get("budget_limit")
    agg = usage_metrics.aggregate(recs, price_map)
    budget = usage_metrics.budget_status(recs, price_map, budget_limit)
    recent = sorted(recs, key=lambda r: r.get("time", ""), reverse=True)[:20]
    return {
        "totals": agg["totals"],
        "by_feature": agg["by_feature"],
        "by_model": agg["by_model"],
        "by_date": agg["by_date"],
        "by_endpoint": agg["by_endpoint"],
        "recent": recent,
        "budget": budget,
    }


# ---- 项目记忆层（结构化、可检索，对标 Mem0 的本地轻量版） ----

MEMORY_TYPES = ["决策", "坑", "约定", "事实"]


def memories_file() -> Path:
    return data_dir() / "memories.json"


def _load_memories() -> list[dict]:
    f = memories_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _memories_by_pid() -> dict[str, list[dict]]:
    """memories.json **读一次**，按项目分好桶。

    交接摘要与 AI 简报都要按项目取记忆；以前是在项目循环里逐个
    `_load_memories()`，于是这份文件的读取次数等于项目数 —— 实测 1000 项目 +
    500 条记忆时 `/api/export-all` 从 139ms 涨到 911ms。这不是缓存（没有失效问题），
    只是把"每次读一遍"改成"每次读一遍**整份**"。
    """
    bucket: dict[str, list[dict]] = {}
    for m in _load_memories():
        if isinstance(m, dict):
            bucket.setdefault(m.get("pid"), []).append(m)
    return bucket


def _save_memories(items: list[dict]) -> None:
    f = memories_file()
    tmp = f.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(items, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, f)


MAX_MEMORIES = 500             # 记忆是"追加型"存储，按硬性约定 11 必须有上限
KEEP_MEMORIES_ON_TRIM = 400    # 超限后裁到最近这些条（留出余量，不用每次写都裁）


def _mutate_memories(fn):
    """记忆的「读 → 改 → 写」临界区，与 `mutate()` 用同一套锁。

    原先 add/delete 两个端点各自裸调 `_load_memories()` + `_save_memories()`：
    两次读-改-写一交叠，后写的那份就把先写的那条整条覆盖掉 —— 记忆是无声色丢失的，
    而且它存在独立文件里，`mutate()` 对 projects.json 上的锁一点也保护不到它。
    """
    with _LOCK, _cross_process_lock():
        items = _load_memories()
        changed, result = fn(items)
        if changed:
            _save_memories(items)
        return changed, result


def _trim_memories(items: list[dict]) -> None:
    """就地丢掉超出上限的旧记忆。

    按**入列顺序**保留末尾那些条：记忆是 append 进去的，顺序本身就等于时间序；
    不去比 `time` 字符串，是因为同一秒内写进多条时排序不稳，裁剪会变成看运气。
    """
    if len(items) > MAX_MEMORIES:
        del items[:-KEEP_MEMORIES_ON_TRIM]


def _memory_expired(m: dict, today: str = "") -> bool:
    """记忆是否已过期：`expires` 设了且早于今天（到期当天仍算有效）。

    对标 OKM 知识代谢的时效语义：决策/约定这类长期事实没有到期日，而
    "当前用 X 方案""下个迭代前 key 是这把"这类**有保质期**的事实必须能标过期 ——
    否则过期结论会作为"当前事实"混进交接包，被下一个智能体当真。
    两边都是 YYYY-MM-DD（写入时已归一），字符串比较即日期比较。
    """
    exp = (m.get("expires") or "").strip()
    if not exp:
        return False
    today = today or datetime.now().strftime("%Y-%m-%d")
    return exp < today


class MemoryIn(BaseModel):
    pid: str = ""
    type: str = "事实"
    text: str = Field(..., min_length=1, max_length=2000)
    source: str = "手动"
    expires: str = ""   # 时效（YYYY-MM-DD，空=长期有效）；过期后不进交接包/AGENTS.md

    @field_validator("expires", mode="before")
    @classmethod
    def _clean_expires(cls, v):
        v = (v or "").strip()
        if not v:
            return ""
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return ""
        return v


@app.get("/api/memories")
def list_memories(pid: str = "", q: str = "", mtype: str = ""):
    """`expired` 是每次读**算出来的**（不是存出来的）：跨过到期日后，盘上的数据一个
    字节没变，但任何出口都不该再把这条当"当前事实"。要不要真删由人决定 ——
    界面和 MCP 都会把过期条目标出来，照常可以删。"""
    items = _load_memories()
    if pid:
        items = [m for m in items if m.get("pid") == pid]
    if mtype:
        items = [m for m in items if m.get("type") == mtype]
    if q.strip():
        ql = q.strip().lower()
        items = [m for m in items if ql in (m.get("text") or "").lower()]
    items = sorted(items, key=lambda m: m.get("time", ""), reverse=True)
    items = [{**m, "expired": _memory_expired(m)} for m in items]
    return {"memories": items,
            "expired_count": sum(1 for m in items if m["expired"])}


@app.post("/api/memories", status_code=201)
def add_memory(body: MemoryIn):
    if body.type not in MEMORY_TYPES:
        body.type = "事实"
    if body.pid and find_project(body.pid) is None:
        # 以前不校验：挂在不存在项目上的记忆永远进不了交接包/接手包（那两处按 pid 过滤），
        # 而回执照样说"已记下 [决策] 到 <pid>" —— 用户以为记下了，其实那条谁也读不到。
        # 留空 pid 是**有意**的另一回事：那是跨项目的通用记忆。
        raise HTTPException(400, f"项目不存在：{body.pid}（先用 /api/projects 或 "
                                f"kanban_list_projects 确认 id；跨项目的通用记忆请把 pid 留空）")
    entry = {
        "id": uuid.uuid4().hex[:12],
        "pid": body.pid, "type": body.type,
        "text": body.text.strip(),
        "source": body.source, "time": now_str(),
    }
    if body.expires:
        entry["expires"] = body.expires

    def worker(items: list[dict]):
        # 去重：同项目同文本不重复入
        if any(m.get("pid") == entry["pid"] and m.get("text") == entry["text"] for m in items):
            return False, {"ok": True, "deduped": True, "memory": entry}
        items.append(entry)
        _trim_memories(items)
        return True, {"ok": True, "memory": entry}

    _, result = _mutate_memories(worker)
    return result


@app.delete("/api/memories/{mid}")
def delete_memory(mid: str):
    def worker(items: list[dict]):
        keep = [m for m in items if m.get("id") != mid]
        if len(keep) == len(items):
            return False, None
        items[:] = keep
        return True, {"ok": True}

    changed, result = _mutate_memories(worker)
    if not changed:
        raise HTTPException(404, "记忆不存在")
    return result


# ---- 会话捕获：自动发现 AI 工具的本地会话文件 ----

# [sessions.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.sessions 导入。


# [sessions.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.sessions 导入。


# [sessions.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.sessions 导入。


@app.get("/api/sessions/scan")
def scan_sessions():
    """扫描各 AI 工具会话目录，返回最近活跃的会话文件（按修改时间倒序）"""
    out = []
    for src in session_sources():
        root = Path(src["path"])
        if not root.exists():
            continue
        stype = src.get("type", "text")
        try:
            if stype == "claude_jsonl":
                files = _newest_files(root.glob("*/*.jsonl"))
            elif stype == "cline":
                files = _newest_files(d / "ui_messages.json" for d in root.glob("*") if d.is_dir())
            else:
                files = _newest_files(_walk_bounded(root))
        except Exception:
            continue
        for f in files:
            try:
                stat = f.stat()
            except OSError:
                continue
            out.append({
                "source": src["name"], "type": stype, "path": str(f),
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "mtime_ts": stat.st_mtime,
                "size_kb": round(stat.st_size / 1024, 1),
            })
    out.sort(key=lambda x: x["mtime_ts"], reverse=True)
    for x in out:
        x.pop("mtime_ts", None)
    return {"sessions": out[:20]}


# [sessions.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.sessions 导入。


# [sessions.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.sessions 导入。


# [sessions.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.sessions 导入。


# [sessions.py] 本节已于 2026-09-26 整体搬出（实现逐字未改），名字改为从 app.sessions 导入。


@app.get("/api/sessions/preview")
def preview_session(path: str):
    if not is_allowed_session_file(path):
        raise HTTPException(400, "只能预览已配置的会话源里的文件（AI 设置可添加自定义源）")
    text = _extract_session_text(path)
    return {"text": text, "chars": len(text)}


@app.post("/api/sessions/ingest")
def ingest_session(body: dict):
    """读取捕获的会话 → 走提炼 → 返回解析结果（前端确认后应用）"""
    pid = (body or {}).get("pid") or ""
    path = (body or {}).get("path") or ""
    if not path:
        raise HTTPException(400, "缺少会话文件路径")
    if not pid:
        raise HTTPException(400, "请先选择要入库的项目")
    if not is_allowed_session_file(path):
        raise HTTPException(400, "只能导入已配置的会话源里的文件（AI 设置可添加自定义源）")
    text = _extract_session_text(path)
    result = _summarize_inner(pid, text)
    result["path"] = path
    return result


# ---- 数据恢复：从 .bak 或历史快照还原（损坏自愈之后还能回去） ----

class RestoreBody(BaseModel):
    source: str = "bak"


def _peek_projects(path: Path) -> dict:
    """安全地评估一个候选来源：坏了也要给出原因，而不是抛异常"""
    try:
        stat = path.stat()
    except Exception:
        return {"usable": False, "reason": "文件不存在", "project_count": 0, "task_count": 0, "names": []}
    try:
        data = _sanitize(json.loads(path.read_text(encoding="utf-8")))
        projects = data.get("projects")
        if not isinstance(projects, list):
            raise ValueError("不是看板数据结构")
        _, unusable = _writable_projects(projects)
        return {
            "usable": True, "reason": "", "project_count": len(projects),
            # 恢复前先让人看见"这份里有多少条是拿不回来的"，而不是恢复完才发现少了几项
            "unusable_count": len(unusable), "unusable": unusable[:5],
            "task_count": sum(len(p.get("tasks") or []) for p in projects),
            "names": [p.get("name", "") for p in projects[:3]],
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as e:
        return {"usable": False, "reason": f"无法解析：{str(e)[:100]}",
                "project_count": 0, "task_count": 0, "names": [],
                "unusable_count": 0, "unusable": [],
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")}


def _resolve_recoverable(source: str) -> Path | None:
    """只允许白名单来源，避免变成任意路径读取"""
    if source in ("", "bak"):
        p = data_file().with_suffix(".json.bak")
        return p if p.exists() else None
    if source == "last-good":
        p = last_good_file()
        return p if p.exists() else None
    if source == "before-restore":
        p = before_restore_file()
        return p if p.exists() else None
    if source.startswith("backup_") and "/" not in source and "\\" not in source:
        p = data_dir() / "backups" / source / "projects.json"
        return p if p.exists() else None
    return None


@app.get("/api/recoverable")
def list_recoverable():
    """列出可恢复的数据来源，并给出各自的规模预览"""
    out = []
    lf = last_good_file()
    if lf.exists():
        out.append({"source": "last-good", "label": "最近一次正常保存（损坏时自动用它恢复）",
                    **_peek_projects(lf)})
    br = before_restore_file()
    if br.exists():
        out.append({"source": "before-restore",
                    "label": "最近一次恢复之前的现场（再做一次恢复会覆盖这份）",
                    **_peek_projects(br)})
    bak = data_file().with_suffix(".json.bak")
    if bak.exists():
        out.append({"source": "bak", "label": "损坏文件留存（仅供取证，通常无法解析）",
                    **_peek_projects(bak)})
    for b in sorted((data_dir() / "backups").glob("backup_*"), reverse=True):
        f = b / "projects.json"
        if f.exists():
            out.append({"source": b.name, "label": "手动快照 " + b.name.replace("backup_", ""),
                        **_peek_projects(f)})
    current = _peek_projects(data_file())
    return {"recoverable": out, "current": current}


@app.post("/api/restore")
def restore_data(body: RestoreBody):
    """用某个来源覆盖当前数据；覆盖前先把当前的另存一份，恢复错了还能回去"""
    src = _resolve_recoverable(body.source)
    if src is None:
        raise HTTPException(404, "找不到该恢复来源")
    try:
        restored = _sanitize(json.loads(src.read_text(encoding="utf-8")))
    except Exception:
        raise HTTPException(400, "该来源无法解析为数据文件，已放弃恢复")
    if not isinstance(restored.get("projects"), list):
        raise HTTPException(400, "该来源内容不是看板数据，已放弃恢复")
    # 恢复与导入必须过同一道筛选：以前 import 逐条筛 id/名称、restore 只看"是不是列表"，
    # 于是一条缺 id 的项目就能从这里进来 —— 之后**每一个读接口都 500**，
    # 而主文件是合法 JSON，自愈不介入，等于把"救命的退路"变成"一键让看板变砖"。
    kept, dropped = _writable_projects(restored["projects"])
    if not kept:
        raise HTTPException(400, f"该来源里没有任何可恢复的项目（{len(dropped)} 条都缺 id 或名称），"
"已放弃恢复；当前数据保持原样")
    restored["projects"] = kept
    # 约定 13：幂等键表是"本进程刚才那次重试"的窗口，导出/导入都不带它。
    # 恢复同样是时间机器（`backup_*/projects.json` 里原样躺着当时整张表），
    # 复活之后拿同一个键建**新**东西会被判成"重试"而一次落盘都不做。
    revived_refs = len(restored.get(IDEM_KEY) or {})
    restored.pop(IDEM_KEY, None)

    def worker(data: dict):
        try:
            if data_file().exists():
                shutil.copy2(data_file(), before_restore_file())
        except Exception:
            pass
        # 自定义视图与记忆层同语义：来源里没有 `views` 键 = 保持现状。
        # 老备份是"视图功能上线前"拍的，不能因为回滚旧数据把攒下来的界面配置一起清掉。
        kept_views = data.get("views") if "views" not in restored else None
        data.clear()
        data.update(restored)
        if kept_views is not None:
            data["views"] = kept_views
        return True, restored

    mutate(worker)

    # 记忆层与 projects.json 分开存，恢复项目而不恢复记忆会让两边对不上
    # （旧记忆挂在已经不存在的项目上）。备份里没带 memories.json 时保持原样。
    memory_count = -1
    mem_src = src.parent / "memories.json"
    try:
        same_as_live = mem_src.resolve() == memories_file().resolve()
    except Exception:
        same_as_live = False
    if not same_as_live and mem_src.exists():
        try:
            loaded = json.loads(mem_src.read_text(encoding="utf-8"))
        except Exception:
            loaded = None
        if isinstance(loaded, list):
            payload = _writable_memories(loaded)

            def wm(items: list[dict]):
                items[:] = payload
                return True, None

            _mutate_memories(wm)
            memory_count = len(payload)

    out = {"ok": True, "source": body.source,
           "project_count": len(restored["projects"]),
           "task_count": sum(len(p.get("tasks") or []) for p in restored["projects"])}
    if dropped:
        out["dropped_invalid"] = dropped
        out["dropped_note"] = (f"{len(dropped)} 条项目缺 id 或名称，恢复回来会让整个看板读不回来，"
                               "已跳过：" + "；".join(dropped[:5]))
    if revived_refs:
        out["idem_table_reset"] = revived_refs
    if memory_count >= 0:
        out["memory_count"] = memory_count
    return out


# ---- 会话粘贴 AI 提炼 ----

class SummarizeBody(BaseModel):
    pid: str = ""
    text: str = Field(..., min_length=10, max_length=20000)


def _summarize_inner(pid: str, text: str) -> dict:
    """会话提炼核心：给粘贴提炼与会话捕获共用"""
    found = find_project(pid)
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


@app.post("/api/ai/summarize")
def ai_summarize(body: SummarizeBody):
    return _summarize_inner(body.pid, body.text)


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


# =====================================================================
#  git 集成：让看板和代码现实对上（只读、只跑固定子命令、绝不拼 shell）
#  [gitinfo.py] 本节已于 2026-10-06 整体搬出（实现逐字未改），名字改为从 app.gitinfo
#  导入（见文件顶部 import）。⚠️ 有状态名字（_run_git / _git_cache / GIT_TIMEOUT）的
#  monkeypatch 点随迁到 app.gitinfo —— main 里是转发别名，见 gitinfo 模块 docstring。
# =====================================================================

# [gitinfo.py] 本节已于 2026-10-06 整体搬出（实现逐字未改），名字改为从 app.gitinfo 导入，见文件顶部 import。


@app.get("/api/projects/{pid}/next")
def next_tasks(pid: str):
    """现在就能动手的任务：自己没完成，且所有前置都已完成。换人接手时看这个就知道先做啥"""
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    p = found[1]
    tasks = p.get("tasks") or []
    by_id = {t.get("id"): t for t in tasks}
    ready = executable_tasks(tasks)
    # 按 id 判，不要写 `t not in ready`：那是对整份字典做相等比较，
    # 每个任务比一遍全列表，400 个任务就是几万次字典对比
    ready_ids = {t.get("id") for t in ready}
    return {
        "project": p.get("name", ""),
        # 三个计数是给"为什么没有可做的"准备的：next 为空时，
        # 没任务、都已完成、都在等前置 是三件完全不同的事（MCP 侧要靠它分辨并说清楚）。
        "total": len(tasks),
        "done": sum(1 for t in tasks if t.get("status") == "已完成"),
        "next": [{"id": t["id"], "title": t.get("title"), "priority": t.get("priority"),
                  "refs": t.get("refs") or []} for t in ready],
        "review": [{"id": t["id"], "title": t.get("title")}
                   for t in tasks if t.get("status") == "待审查"],
        "blocked": [{"id": t["id"], "title": t.get("title"),
                     "waiting_for": [by_id[d].get("title", d)
                                     for d in (t.get("depends_on") or [])
                                     if d in by_id and by_id[d].get("status") != "已完成"]}
                    for t in tasks if t.get("id") not in ready_ids and t.get("status") not in ("已完成", "待审查")],
    }


@app.get("/api/projects/{pid}/git")
def project_git(pid: str, limit: int = 10):
    """只读：分支 / HEAD / 最近提交 / 工作区是否干净 / 距上次提交多少天

    这个接口是给人点的，不能吃缓存 —— 用户刚提交完点开却看到旧状态最容易被坑。
    缓存只服务于批量场景（AI 监管里逐个项目取摘要）。
    """
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    snap = git_snapshot(found[1].get("repo_path", ""), limit=limit, use_cache=False)
    if not snap.get("ok"):
        raise HTTPException(400, snap.get("error", "无法读取仓库信息"))
    return snap


@app.get("/api/projects/{pid}/graph")
def project_graph(pid: str, theme: str = "dark"):
    """任务依赖图（内联 SVG），给应用内的详情页画"这堆活的形状"。

    **复用接手页那份 `dep_graph_svg`，不在前端再画第二遍** —— 本项目反复出问题的形状
    就是"同一件事两套实现"（两份 `_projects_brief`、MCP 重算一遍 `/next`）。
    没有依赖时它返回空串，这里如实报 `empty` + 原因，别让调用方拿空串去猜是"坏了"还是"没依赖"。
    """
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    tasks = found[1].get("tasks") or []
    light = theme.strip().lower() == "light"
    svg = dep_graph_svg(tasks,
                        node_fill="#FFFFFF" if light else "#1E1E22",
                        text_fill="#1F2430" if light else "#E8E6E1")
    if not svg:
        return {"svg": "", "empty": True, "nodes": len(tasks),
                "reason": "还没有任务" if not tasks else "任务之间还没设前置依赖"}
    # 层级超过 MAX_COLS 时更深的节点被并进末列 —— 那两条在图上看着像并行，
    # 其实有前后关系。不说的话图就在撒谎。
    depth = _graph_layers(tasks[:GRAPH_MAX_NODES]) if tasks else {}
    collapsed = bool(depth) and max(depth.values()) >= GRAPH_MAX_COLS
    return {"svg": svg, "empty": False, "nodes": len(tasks),
            "shown": min(len(tasks), GRAPH_MAX_NODES),
            "truncated": len(tasks) > GRAPH_MAX_NODES,
            "collapsed": collapsed}


def _git_line_for_handoff(p: dict) -> str:
    """交接包里的一行代码现状；读不到就返回空串，不影响导出"""
    snap = git_snapshot(p.get("repo_path", ""), limit=5)
    if not snap.get("ok"):
        return ""
    head = snap.get("head") or {}
    bits = [f"- 分支 `{snap['branch'] or '(detached)'}`"]
    if head:
        bits.append(f"- 最新提交：`{head['hash']}` {head['date']} {head['subject']}")
    if snap.get("days_since_commit") is not None:
        bits.append(f"- 距上次提交 {snap['days_since_commit']} 天")
    dirty = snap.get("dirty_count", 0)
    bits.append("- 工作区" + ("干净" if dirty == 0 else f"有 {dirty} 个未提交改动"))
    if snap.get("commits"):
        bits.append("- 最近提交：")
        bits.extend(f"  - `{c['hash']}` {c['date']} {c['subject']}" for c in snap["commits"])
    return "\n".join(bits)


def _projects_brief(projects: list[dict]) -> list[dict]:
    """给 LLM 的精简项目数据（含代码现状，便于发现"看板说有进展、代码其实没动"）

    自由文本（背景 / 下一步 / 任务标题 / 过程记录 / 记忆）过一道脱敏再送出去：这些是要离开本机
    到模型那边的内容，而用户从会话里抄进看板的凭据很常见。
    id / 状态 / 分支名原样保留 —— 模型要把它们回传用来对齐项目与任务。
    标题现在能盖，是因为**每条任务都带 id**：以前简报里没有任务 id，提示词只能写
    "任务用现有标题匹配"，盖了标题就把建议对不回任务了（见 `ai_command` 的规则 1）。
    """
    mems_by_pid = _memories_by_pid()
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
                         for m in mems_by_pid.get(p["id"], []) if not _memory_expired(m)][-10:],
        }
        # 截断必须留痕：以前静默给前 40 条，模型以为"这项目的活就这些"，
        # 于是监管报告漏掉第 41 条之后的阻塞。看板自己的场景测试就造过 45 条的项目。
        total_tasks = len(p.get("tasks") or [])
        if total_tasks > 40:
            item["tasks_note"] = (f"该项目共 {total_tasks} 条任务，本简报只带前 40 条，"
                                  "其余未在本次分析范围内")
        snap = git_snapshot(p.get("repo_path", ""), limit=1)
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


class AIConfigIn(BaseModel):
    endpoints: list[dict] | None = None
    active_model: str | None = None
    schedule: dict | None = None
    mcp_readonly: bool | None = None
    session_sources: list[dict] | None = None
    budget_limit: float | None = None


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
                if llm.mask_matches(ok, k):
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
                    # 每千 token 单价（美元）：成本估算的数据源，走白名单+上限钳制。
                    # 端点没配 = 0（成本不估算），由 /api/usage 侧按"定价未配置"提示区分。
                    "price_per_1k_input": usage_metrics.clamp_price(ep.get("price_per_1k_input")),
                    "price_per_1k_output": usage_metrics.clamp_price(ep.get("price_per_1k_output")),
                })
            cfg["ai"]["endpoints"] = new_eps
        if body.budget_limit is not None:
            cfg["ai"]["budget_limit"] = usage_metrics.clamp_budget(body.budget_limit)
        if body.active_model is not None:
            cfg["ai"]["active_model"] = body.active_model.strip()
        if body.schedule is not None:
            s = cfg["ai"].setdefault("schedule", {})
            s.update({k: v for k, v in body.schedule.items() if k in ("enabled", "frequency", "time", "last_run")})
            s["enabled"] = bool(s.get("enabled"))
        if body.mcp_readonly is not None:
            cfg["ai"]["mcp_readonly"] = bool(body.mcp_readonly)
        if body.session_sources is not None:
            cfg["ai"]["session_sources"] = [
                {"name": (s.get("name") or "未命名源").strip(),
                 "path": (s.get("path") or "").strip(),
                 "type": (s.get("type") or "text").strip()}
                for s in body.session_sources if isinstance(s, dict) and (s.get("path") or "").strip()
            ]

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


MAX_REPORTS = 100   # 监管报告留多少份（报告 id 就是时间戳，可直接按文件名排序）


def _trim_reports() -> None:
    """裁掉超出上限的旧报告。

    开了定时监管后每天/每周都会落一份 JSON，reports/ 只写不删就是无界增长 ——
    而列报告的接口是逐文件读的，越往后越慢。
    """
    try:
        files = sorted(reports_dir().glob("*.json"), reverse=True)
    except Exception:
        return
    for old in files[MAX_REPORTS:]:
        try:
            old.unlink()
        except Exception:
            pass


def _save_report(report: dict) -> dict:
    rid = report["id"]
    (reports_dir() / f"{rid}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _trim_reports()
    return report


def _latest_report_before(rid: str) -> dict | None:
    """取时间上早于指定报告 id 的最近一份报告（报告 id 即时间戳，可直接比较）"""
    best = None
    for f in reports_dir().glob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if r.get("id") and r["id"] < rid and (best is None or r["id"] > best["id"]):
            best = r
    return best


def _attach_trends(report: dict) -> dict:
    """对比上一份报告，给每个项目洞察标注健康分趋势（down=恶化≥10 / up=好转≥10 / flat）"""
    prev = _latest_report_before(report["id"])
    if not prev:
        for it in report.get("insights", []):
            it["trend"] = {"delta": None, "level": "new"}
        return report
    prev_map = {it.get("id"): it for it in prev.get("insights", []) if isinstance(it, dict)}
    for it in report.get("insights", []):
        old = prev_map.get(it.get("id"))
        if not old or old.get("health") is None or it.get("health") is None:
            it["trend"] = {"delta": None, "level": "new"}
            continue
        delta = int(it["health"]) - int(old["health"])
        level = "down" if delta <= -10 else "up" if delta >= 10 else "flat"
        it["trend"] = {"prev": int(old["health"]), "delta": delta, "level": level}
    return report


def _run_analyze(trigger: str = "manual") -> dict:
    data = get_projects_data()
    projects = data.get("projects", [])
    cfg = llm.load_config()
    messages = _build_analyze_prompt(_projects_brief(projects))
    text, ep = llm.chat(cfg, messages, temperature=0.2, feature="监管分析")
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
    report = _attach_trends(report)
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
        # 文件名不合法规的就直接跳过：详情端点是按 `_REPORT_ID_RE` + 文件名取文件的，
        # 列出来等于发一个点进去必然 404 的条目（手工塞进 reports/ 的文件、
        # 或上一版留下的旧命名都会踩到）
        if not _REPORT_ID_RE.match(f.stem):
            continue
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                # 用**文件名**当 id，不用正文里的 `id` 字段：详情端点是按文件名取文件的
                # （`reports/{rid}.json` + `_REPORT_ID_RE`），两边口径不一致时，
                # 列表就会发一个点进去必然 404 的 id（2026-09-20 造夹具时真撞到一次）。
                "id": f.stem, "created_at": r.get("created_at"),
                "trigger": r.get("trigger"), "endpoint": r.get("endpoint"),
                "summary": (r.get("summary") or "")[:120],
                "insight_count": len(r.get("insights", [])),
            })
        except Exception:
            continue
    return {"reports": out}


_REPORT_ID_RE = re.compile(r"^\d{8}_\d{6}$")  # 报告 id 由 datetime.strftime("%Y%m%d_%H%M%S") 生成


@app.get("/api/ai/reports/{rid}")
def ai_report_detail(rid: str):
    """按 id 取一份报告。

    rid 必须整段匹配时间戳格式：`Path(dir) / f"{rid}.json"` 不做任何清洗，
    `%2E%2E%5Cconfig` 这类编码后穿越会解析到 `..\\config.json`，
    而那份文件里是所有端点的明文 Key。路由只挡 `%2F`，挡不住反斜杠。
    """
    if not _REPORT_ID_RE.match(rid):
        raise HTTPException(404, "报告不存在")
    f = reports_dir() / f"{rid}.json"
    if not f.exists():
        raise HTTPException(404, "报告不存在")
    return json.loads(f.read_text(encoding="utf-8"))


# ---- 手动应用监管建议 ----

class SuggestionUpdate(BaseModel):
    project_id: str
    field: str = "next_action"
    value: str = ""


MAX_SUGGESTION_VALUE = 2000   # 单条建议值的上限。整份 JSON 每次请求都整读整写，
                              # 模型抽风吐 40 万字符就会永久长在数据里（实测：改前能落 40 万字）

# 监管报告"建议回传"允许改的字段。比 `ai_command` 那份清单（AI_EDITABLE_PROJECT_FIELDS）
# 窄是**有意**的：这条是"模型建议 + 人点一下应用"的通道，只放自由文本与状态/优先级；
# 改 name / tags / progress 得走用户自己打出来的显式指令。
SUGGESTION_FIELDS = ("next_action", "handoff_note", "background", "priority", "status")


@app.post("/api/ai/apply-suggestions")
def ai_apply_suggestions(body: list[SuggestionUpdate], dry_run: int = 0):
    """把模型的回传建议落地。`?dry_run=1` 只报"每条会怎样"，一个字节都不写。

    逐条回执而不是只给两个计数：一批 8 条里 2 条没落地时，光看 `applied=6` 的人
    不知道**是哪两条**、也不知道为什么（字段不在清单 / 项目不存在 / 值过长 / 带掩码）。
    `applied` 与 `skipped_masked` 两个老键保留，前端照旧能读。
    """

    def worker(data: dict):
        results = []
        for i, u in enumerate(body):
            val = u.value or ""
            base = {"index": i, "project_id": u.project_id, "field": u.field}
            if u.field not in SUGGESTION_FIELDS:
                results.append(dict(base, ok=False, reason="字段不在建议回传清单内"))
                continue
            if MASK_MARK in val:
                # 送给模型的背景是脱敏过的，它可能把 `********` 当成原文抄回来。
                # 照写就是把用户真内容换成一串星号（而且看不出丢过什么）——
                # 宁可不落地，也要报出来，让用户去原处看一眼。
                results.append(dict(base, ok=False, reason="值里带掩码，可能是把星号当原文抄回来了"))
                continue
            if len(val) > MAX_SUGGESTION_VALUE:
                results.append(dict(base, ok=False,
                                    reason=f"值过长（{len(val)} 字，上限 {MAX_SUGGESTION_VALUE}）"))
                continue
            target = next((p for p in data.get("projects", []) if p.get("id") == u.project_id), None)
            if target is None:
                results.append(dict(base, ok=False, reason="项目不存在"))
                continue
            results.append(dict(base, ok=True, would_write=not dry_run))
            if not dry_run:
                target[u.field] = val
                target["updated_at"] = now_str()
        applied = sum(1 for r in results if r["ok"])
        if applied and not dry_run:
            # 直接写字段可能带进非法状态/优先级，交给统一规范化收口
            _sanitize(data)
        return (applied > 0 and not dry_run), {
            "applied": applied,
            "skipped_masked": sum(1 for r in results if "掩码" in r.get("reason", "")),
            "skipped_other": sum(1 for r in results if not r["ok"]
                                 and "掩码" not in r.get("reason", "")),
            "dry_run": bool(dry_run),
            "results": results,
        }

    _, result = mutate(worker)
    return result


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
        text, ep = llm.chat(cfg, [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}], temperature=0.3, feature="会话提炼")
        return {"text": text.strip(), "endpoint": ep}
    except llm.LLMError as e:
        raise HTTPException(502, f"AI 调用失败：{e}")
    except Exception as e:
        raise HTTPException(500, f"生成失败：{e}")


# ---- 自然语言维护看板 ----

class CommandBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


# 这两份名单就是 /api/ai/command 告诉模型的"能改什么"，也是前端 execSingleAction
# 真正放行的白名单 —— 前端那份在 web/index.html 里（AI_PROJECT_FIELDS / AI_TASK_FIELDS），
# `test_ai_command_prompt_fields_match_frontend_whitelist` 逐字比对两边。
# **repo_path 永远不进这个名单**：它指向本机目录，改了它就等于让外部内容远程探测本机
# 并把结果拼进 prompt（见 git_snapshot / _projects_brief）。
AI_EDITABLE_PROJECT_FIELDS = ["name", "status", "progress", "priority", "tags",
                              "background", "handoff_note", "next_action"]
AI_EDITABLE_TASK_FIELDS = ["title", "desc", "status", "priority", "agent", "due_date",
                           "refs", "depends_on"]

_CMD_TYPES = (
    "create_project(新建项目), update_project(修改项目字段 field ∈ "
    + ", ".join(AI_EDITABLE_PROJECT_FIELDS) + "), "
    "delete_project(删除项目), create_task(在 project_id 下新建任务), "
    "update_task(修改任务 field ∈ " + ", ".join(AI_EDITABLE_TASK_FIELDS) + "), "
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
        '{"actions": [{"type": "create_task", "project_id": "...", "title": "...", "desc": "...", "priority": "高/中/低", "status": "待办/进行中/待审查/已完成/阻塞", "agent": "..."}, '
        '{"type": "update_task", "project_id": "...", "task_id": "...", "field": "...", "value": "..."}, ...]}\n'
        "规则：1) **项目与任务一律用 id 标识** —— 看板数据里每条任务都带 `id`，"
        "改/删任务时同时回传 `project_id` 与 `task_id`；标题可能带脱敏掩码，"
        "**不要拿标题当匹配键**；"
        "2) 若指令信息不足无法确定操作，"
        '输出 {"need_clarification": "具体需要用户补充什么"}; 3) 不要编造不存在的项目 id 或任务 id；'
        "4) 删除类操作必须能明确匹配到目标才输出；5) 一次指令可能包含多个操作，按顺序列出。"
    )
    user_msg = "看板数据：\n" + json.dumps(projects, ensure_ascii=False, indent=1) + "\n\n用户指令：" + body.text
    cmd_batch = uuid.uuid4().hex      # 前端执行这批动作时当幂等键前缀用，理由同会话提炼
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}], temperature=0.1, feature="自然语言指令")
        parsed = _extract_json(text)
        return {"parsed": parsed, "endpoint": ep, "batch_ref": cmd_batch}
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
    """幂等启动调度线程（import 即拉起一次）

    KANBAN_NO_SCHEDULER=1 时不启动 —— MCP 服务这类短命进程不该起后台线程。
    """
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED or os.environ.get("KANBAN_NO_SCHEDULER") == "1":
        return
    _SCHEDULER_STARTED = True
    threading.Thread(target=_scheduler_loop, daemon=True, name="kanban-scheduler").start()


start_scheduler()
