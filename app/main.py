# -*- coding: utf-8 -*-
"""
AI 交接看板 - FastAPI 后端
提供多项目总览 + 单项目详情 + 任务管理 + 交接摘要导出
数据以 JSON 文件持久化，位于用户主目录 .ai-kanban/projects.json（可用环境变量 KANBAN_DATA_DIR 覆盖）
"""
from __future__ import annotations

import heapq
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from . import llm
from .filelock import blocking_lock
from .handoff_page import build_handoff_page
from .readiness import readiness_items, readiness_score
from .version import APP_VERSION, BUILD_TIME

APP_NAME = "ai-kanban"
# 交接包里打印的直链端口。桌面壳用同一个环境变量起服务，两边口径一致；
# 用户没设时两边都落回 18320。服务没开时链接取不到，交接包正文仍可用（它是快照）。
SERVICE_PORT = os.environ.get("KANBAN_PORT", "18320")
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

MAX_REFS = 20
MAX_REF_LEN = 200


def clean_refs(raw) -> list[str]:
    """规范化代码位置引用：去空白、去重、限长限量。

    只当字符串存，**不做任何路径解析或存在性校验** —— 它可能指向别的机器，
    也可能是个还没创建的文件；意义在于"给接头的人一个准确落点"。
    """
    if isinstance(raw, str):
        raw = [p for p in raw.replace("\n", ",").split(",")]
    if not isinstance(raw, (list, tuple)):
        return []
    seen, out = set(), []
    for item in raw:
        if not isinstance(item, str):
            continue
        ref = item.strip().strip("`").strip()
        if not ref or len(ref) > MAX_REF_LEN or ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
        if len(out) >= MAX_REFS:
            break
    return out


# ---- 任务依赖（交接时说清"先做哪个"） ----

MAX_DEPS = 10


def clean_deps(raw, valid_ids) -> list[str]:
    """依赖只能是同一项目里存在的任务 id；去重、限量、去自引用"""
    if isinstance(raw, str):
        raw = [p for p in raw.replace("\n", ",").split(",")]
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for item in raw:
        dep = str(item).strip()
        if not dep or dep in seen or dep not in valid_ids or len(out) >= MAX_DEPS:
            continue
        seen.add(dep)
        out.append(dep)
    return out


def task_graph(tasks: list[dict]) -> dict[str, list[str]]:
    ids = {t.get("id") for t in tasks if isinstance(t, dict)}
    return {t.get("id"): [d for d in (t.get("depends_on") or []) if d in ids]
            for t in tasks if isinstance(t, dict)}


def has_cycle(graph: dict[str, list[str]]) -> bool:
    """依赖图不能有环 —— 否则谁都等谁，永远做不了"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in graph}

    def visit(node: str) -> bool:
        color[node] = GREY
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GREY or (color[dep] == WHITE and visit(dep)):
                return True
        color[node] = BLACK
        return False

    return any(color[n] == WHITE and visit(n) for n in list(graph))


def topo_order(tasks: list[dict]) -> list[dict]:
    """按依赖给出可执行顺序；有环时剩下的退回原始顺序（不该发生，写入时已拦）。

    分层与层内顺序与旧实现逐字一致（层内按原列表顺序），改的只是算法：
    原来每排完一层就重扫整个列表，链长 N 就是 N² 次比较（实测 400 长的链 29ms）；
    这里按 Kahn 记"还差几个前置"，前置满足时给后继 -1，归零进下一层。
    键用 `id(task)` 而不是任务 id：调用方可能传进没经过 `_sanitize` 的列表。
    """
    by_id = {t.get("id"): t for t in tasks}
    rank = {id(t): i for i, t in enumerate(tasks)}      # 层内保持原顺序
    waiting: dict[int, int] = {}
    dependents: dict[object, list] = {}
    for t in tasks:
        deps = [d for d in (t.get("depends_on") or []) if d in by_id]
        # 依赖里指向"不存在的前置"按已满足处理（与旧实现同口径：数据清洗后不该有这种）；
        # 指向自己的那条留在 waiting 里，永远满足不了 → 走有环兜底，同样与旧实现一致。
        waiting[id(t)] = len(deps)
        for d in deps:
            dependents.setdefault(d, []).append(t)
    current = [t for t in tasks if waiting[id(t)] == 0]
    out: list[dict] = []
    while current:
        out.extend(current)
        nxt: list[dict] = []
        for t in current:
            for d in dependents.get(t.get("id"), ()):
                waiting[id(d)] -= 1
                if waiting[id(d)] == 0:
                    nxt.append(d)
        nxt.sort(key=lambda x: rank[id(x)])
        current = nxt
    if len(out) < len(tasks):                     # 有环：剩下的按原序补上
        placed = {id(t) for t in out}
        out.extend(t for t in tasks if id(t) not in placed)
    return out


def _break_dep_cycles(tasks: list[dict]) -> None:
    """就地剪掉依赖里的环：按顺序逐条接受依赖，会把图连成环的那一条直接丢掉。

    写入路径（create_task / update_task）已经在锁内拦环，这里兜的是另外两条入口 ——
    导入外部 JSON、从历史备份恢复，它们绕过端点校验。环一旦进了存储，
    `topo_order` 会退回原序、`executable_tasks` 也可能永远等不齐前置，
    而用户只看到"顺序怎么不对"，查不到原因。

    挂在 `_sanitize()`（每次读都跑）而不是只挂在导入端点上，是为了连手改过的
    projects.json 与旧备份一起自愈。代价实测过：1000 项目 / 一万任务（4.1MB）下
    `scripts/bench.py` 的列表接口从 56ms 涨到 63ms（约 +6ms），离 300ms 的存储层
    改造触发线还很远；常态只有一次 DFS 就返回，不会逐条试。要挪走前先重跑 bench。
    """
    ids = {t.get("id") for t in tasks if isinstance(t, dict)}
    graph: dict[str, list[str]] = {}
    for t in tasks:
        if isinstance(t, dict):
            graph[t.get("id")] = [d for d in (t.get("depends_on") or []) if d in ids]
    if not has_cycle(graph):
        return                      # 常态：一次 DFS 就过，不必逐条试
    # 真有成环（多半来自外部导入/旧备份）：从空图出发按入列顺序逐条接受，
    # 装得进去就留、会把图连成环就丢。必须从空图开始 —— 拿检测用的那张图当底子，
    # 后面任务尚未处理的依赖会假装成"已经在图里"，把本该保留的前一条依赖误剪掉。
    acyclic: dict[str, list[str]] = {tid: [] for tid in graph}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        accepted: list[str] = []
        for dep in (t.get("depends_on") or []):
            if dep not in ids or dep == tid:
                continue
            acyclic[tid].append(dep)
            if has_cycle(acyclic):
                acyclic[tid].pop()
            else:
                accepted.append(dep)
        t["depends_on"] = accepted


def executable_tasks(tasks: list[dict]) -> list[dict]:
    """当前可以动手的任务：自己的状态未完成，且所有前置都已完成"""
    done_ids = {t.get("id") for t in tasks if t.get("status") == "已完成"}
    known_ids = {t.get("id") for t in tasks}      # 只算一次：原来这个集合是在下面按任务逐个重建的
    return [t for t in tasks
            if t.get("status") != "已完成"
            and all(d in done_ids or d not in known_ids
                    for d in (t.get("depends_on") or []))]


def _dedupe_ids(items: list) -> None:
    """就地消除重复 id：保留第一条，后来的换成**确定性**的新 id（`t1~2`、`t1~3`…）。

    全部"按 id 找"的逻辑都是 first-match，所以重复 id 不是显示问题而是静默的数据问题：
    PUT 只改第一条（余份永远读不到新值）、DELETE 会把同 id 的几条**一起删掉**、
    `topo_order` 输入 4 条只输出 3 条（交接包丢任务）；而 `by_id = {t["id"]: t}` 是
    last-wins，于是依赖图和任务列表指向两条不同任务，真环能以 has_cycle=False 入库
    —— 直接违反「不许有环」这条不变式。

    新 id 必须由原 id 推出来、**不能是随机 uuid**：这一步挂在每次读上，
    随机化会让同一条数据在两次读之间换身份 —— 界面拿旧 id 点进去就 404，
    测试里"读一次拿 id、再删它"也会随机失败。内容一条都不能丢，
    id 本来就是 uuid，用户界面靠标题定位，数据里也没有跨项目引用它。

    **没有 id 的条目不补**：导入端点有一条明写并测过的规则 —— 缺 id 或缺名的项目直接丢，
    在这里替它编一个 id 等于把那份残缺数据放回世界。
    """
    seen: set = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        tid = it.get("id")
        if not tid:
            continue
        if tid not in seen:
            seen.add(tid)
            continue
        n = 2
        while f"{tid}~{n}" in seen:
            n += 1
        it["id"] = f"{tid}~{n}"
        seen.add(it["id"])


_DUE_CANONICAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean_due_date(v) -> str:
    """归一截止日期：认 `2026-09-05` / `2026/9/5` / `2026.09.05`，认不出来就清空。

    写入端点自己会校验，这里兜的是导入与手改的 projects.json。
    不归一的话它是**按字符串**比较的：`2026-9-5` 排在 `2026-08-01` 之后（'9' > '0'），
    于是 /api/today 的顺序、临期与逾期判断一起看错，而且错得看不出来。
    清空与 `TaskIn._clean_due` 同口径 —— 留着解析不出的原值只会让下游各自猜。

    长得就已经是规范形式的先看形状放过：这一步挂在每次读上，
    一万条任务逐条 `strptime` 实测给列表接口加了约 20ms（1000 项目数据集）。
    形状对但日期不存在（`2026-13-45`）的会留到 `_due_state` 里解析失败、按"未设截止"处理。
    """
    s = str(v or "").strip()
    if not s:
        return ""
    if _DUE_CANONICAL_RE.match(s):
        return s
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _sanitize(data: dict) -> dict:
    """规范化读入的数据：修正非法状态/优先级，保证前端不拿到越界值"""
    if not isinstance(data, dict):
        return {"projects": []}
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
            t["journal"] = clean_journal(t.get("journal"))   # 过程轨迹也收一道，防导入的脏数据撑大文件
            if t.get("due_date"):
                # 只在本来就有值时归一：不给老数据凭空加键（导出体积与前端判断都按"有没有"走）
                t["due_date"] = _clean_due_date(t["due_date"])
            if not isinstance(t.get("depends_on"), list):
                t["depends_on"] = []
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


# ---------------- 交接就绪度（本地规则，实现收口在 app/readiness.py） ----------------

def _readiness_items(p: dict) -> list[dict]:
    return readiness_items(p)


def _readiness_score(p: dict) -> int:
    return readiness_score(p)


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
    due_date: str = ""            # 截止日期 YYYY-MM-DD，空=未设置
    refs: list[str] = []          # 相关代码位置，如 app/main.py:120；交接时用来说明"改哪里"
    depends_on: list[str] = []    # 前置任务 id（同一项目内），用于排"先做哪个"

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
    而智能体的上下文通常在别的机器上。脱敏范围与 `_projects_brief` 同一条线 ——
    自由文本（背景/交接说明/下一步/任务描述/过程记录/记忆）盖掉，
    id / 标题 / 状态 / 分支名 / refs 原样保留（模型要靠它们对齐项目与任务）。
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
            "id": t.get("id"), "title": t.get("title"), "status": t.get("status"),
            "priority": t.get("priority"),
        }
        if t.get("due_date"):
            d["due_date"] = t["due_date"]
        if t.get("agent"):
            d["agent"] = t["agent"]
        if t.get("refs"):
            d["refs"] = t["refs"]
        if t.get("desc"):
            d["desc"] = mask((t.get("desc") or ""))[:200]
        jr = t.get("journal") or []
        if jr:
            d["recent_journal"] = [{"time": e.get("time", ""), "text": mask((e.get("text") or ""))[:120]}
                                   for e in jr[-2:]]
        waiting = [by_id[x].get("title", x) for x in (t.get("depends_on") or [])
                   if x in by_id and by_id[x].get("status") != "已完成"]
        if waiting:
            d["waiting_for"] = waiting
        return d

    missing = [it["label"] for it in _readiness_items(p) if not it["ok"]]
    return {
        "app": "ai-kanban",
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
                     for m in _load_memories() if m.get("pid") == p.get("id")][-15:],
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
            {"id": t["id"], "title": t.get("title"), "priority": t.get("priority"),
             "refs": t.get("refs") or []} for t in ready
        ],
        "tasks": {
            "todo": [slim(t) for t in tasks if t.get("status") == "待办"],
            "in_progress": [slim(t) for t in tasks if t.get("status") == "进行中"],
            "blocked": [slim(t) for t in tasks if t.get("status") == "阻塞"],
            "done_count": sum(1 for t in tasks if t.get("status") == "已完成"),
        },
        "readiness": {"score": _readiness_score(p), "missing": missing},
    }


# ---- 交接内容脱敏（对标 handoff skill 的 redact） ----

MASK_MARK = "********"      # 脱敏掩码；「建议回传里带掩码就别落盘」那条判断认它

_REDACT_PATTERNS = [
    # 前缀型 Key：OpenAI / Anthropic / NVIDIA / GitHub 全系 / GitLab / AWS（含临时凭据 ASIA）/
    # Slack / npm / PyPI / Docker Hub / HuggingFace / Sonar
    (r"(?i)\b(sk|rc|nvapi|ghp|gho|ghu|ghs|ghr|github_pat|glpat|xoxb|xoxp|xapp"
     r"|npm_|pypi|dckr_pat|hf|sgpat)[-_][A-Za-z0-9_-]{6,}", r"\1-" + MASK_MARK),
    # AWS 访问密钥是前缀直接跟 16 位大写字母数字，**没有分隔符**，套不进上面那条
    (r"\b(AKIA|ASIA)[0-9A-Z]{16}\b", r"\1-" + MASK_MARK),
    # JWT：三段 base64url，段段有点分着，正常中文/代码里不会出现
    (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "JWT." + MASK_MARK),
    (r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}", "Bearer " + MASK_MARK),
    # 私钥整块（PEM）：正文本来就是 base64，逐条规则都命中不了，只能按块盖掉
    (r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----(.*?)(-----END [A-Z ]*PRIVATE KEY-----|$)",
     "-----" + MASK_MARK + "-----"),
    # key: value 形态。**JSON 里键名是带引号的**（`"api_key": "xxx"`），
    # 原来那条 `key\s*[:=]` 卡在引号上匹配不到，于是最常见的 JSON 配置反而没脱敏。
    # 另外替换必须原样保留分隔符 —— 老写法把 `:` 统一换成 `=`，脱敏顺手改了别人的格式。
    (r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token|token"
     r"|secret|password|passwd|pwd|授权码|密钥)([\"']?)(\s*[:=]\s*)([\"']?)[^\s\"']{4,}",
     r"\1\2\3\4" + MASK_MARK),
    # 连接串 postgres://user:pass@host —— 盖掉口令，主机名留着（排查问题要用）
    # 必须排在邮箱规则前面：否则邮箱规则先把 `pass@host.com` 吃掉，口令就漏在协议头里
    (r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s]+(@)", r"\1" + MASK_MARK + r"\2"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "****@****"),
]


def redact_text(text: str) -> str:
    """把交接内容里的敏感信息替换为掩码（API Key / 令牌 / 密码 / 邮箱）"""
    if not text:
        return text
    for pat, rep in _REDACT_PATTERNS:
        text = re.sub(pat, rep, text)
    return text


@app.get("/api/search/tasks")
def search_tasks(q: str = "", limit: int = 30):
    """全局任务搜索：跨项目按标题/描述/负责人匹配"""
    q = (q or "").strip()
    if not q:
        return {"results": []}
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
                    return {"results": out}
    return {"results": out}


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
    # 数据文件损坏并重建过：必须让用户知道，否则会以为项目凭空消失
    recovered, recovered_from = take_recovered_flag()
    return {"projects": out, "data_recovered": recovered, "recovered_from": recovered_from}


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
        # 首次落盘：磁盘上还没有数据文件时，说明看板从未被真正使用过，
        # 内置示例数据只用于引导界面，不该跟着用户的第一条真实数据一起写进去。
        if not data_file().exists():
            data["projects"] = []
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


def _apply_project(p: dict, body: ProjectIn) -> None:
    p.update({
        "name": body.name.strip(), "status": body.status,
        "progress": body.progress, "priority": body.priority,
        "tags": body.tags or [], "repo_path": body.repo_path.strip(),
        "background": body.background, "handoff_note": body.handoff_note,
        "next_action": body.next_action, "updated_at": now_str(),
    })


def _merge_into(model, current: dict, changes: dict):
    """把 `changes` 盖到「当前盘上的这一份」上，再交给 Pydantic 校验。

    只认模型声明过的字段；值为 `None` 视为"没给"（与 MCP 侧一直沿用的 `pick()` 语义一致），
    显式空串则是"要清空"，会照常传下去。
    """
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
def create_task(pid: str, body: TaskIn):
    now = now_str()
    task = {
        "id": uuid.uuid4().hex[:12],
        "title": body.title.strip(), "desc": body.desc,
        "status": body.status, "priority": body.priority,
        "agent": body.agent.strip(), "due_date": body.due_date,
        "created_at": now, "updated_at": now, "journal": [],
        "refs": clean_refs(body.refs),
        "depends_on": [],          # 依赖在锁内算，见 apply
    }

    def apply(p: dict, _data: dict) -> None:
        # 取兄弟任务、清洗依赖、判环全部放进同一个临界区。先前是拿锁外快照算好
        # depends_on 再另起一次锁写入 —— 两个并发请求各自都判得过，
        # 合起来却能把依赖写成一个环（环一进来 topo_order 就静默退化成原序）。
        siblings = [t for t in (p.get("tasks") or []) if isinstance(t, dict)]
        task["depends_on"] = clean_deps(body.depends_on, {t.get("id") for t in siblings})
        if has_cycle(task_graph(siblings + [task])):
            raise HTTPException(400, "依赖会形成循环，已放弃创建")
        p.setdefault("tasks", []).insert(0, task)
        p["updated_at"] = now

    if mutate_project(pid, apply) is None:
        raise HTTPException(404, "项目不存在")
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
        "updated_at": now,
        "refs": clean_refs(body.refs),
        "depends_on": deps,
    })
    # 状态流转自动沉淀过程轨迹
    if body.status != old_status:
        _append_journal(t, f"状态：{old_status} → {body.status}", now)
    p["updated_at"] = now


def patch_task(tid: str, changes: dict, *, with_project: bool = False):
    """部分更新任务：未提到的字段取**当前盘上的值**（理由同 `patch_project`）。

    `with_project=True` 时连带返回所属项目名 —— 智能体侧的回复要报"这个任务在哪个项目"，
    而它不该为此在临界区外再读一整份数据。
    """
    box: dict = {}

    def apply(t: dict, p: dict) -> None:
        _apply_task(t, p, tid, _merge_into(TaskIn, t, changes))
        box["project"] = p.get("name", "")

    t = mutate_task(tid, apply)
    if t is None:
        raise HTTPException(404, "任务不存在")
    return (t, box.get("project", "")) if with_project else t


@app.put("/api/tasks/{tid}")
def update_task(tid: str, body: TaskIn):
    def apply(t: dict, p: dict) -> None:
        _apply_task(t, p, tid, body)

    t = mutate_task(tid, apply)
    if t is None:
        raise HTTPException(404, "任务不存在")
    return t


MAX_JOURNAL = 50           # 每个任务最多留多少条过程记录
MAX_JOURNAL_TEXT = 500     # 单条正文上限（输入框限制 2000，这里再收一道）


def _append_journal(t: dict, text: str, when: str = "") -> None:
    """追加一条过程轨迹，并裁掉超出的部分。

    journal 存在 projects.json 里 —— 那是每次读写都要整份解析的核心文件，
    状态流转还会自动追加，不设上限就是"悄悄变胖"：读写变慢、备份变大、交接包变长。
    """
    j = t.get("journal")
    if not isinstance(j, list):
        j = t["journal"] = []
    j.append({"time": when or now_str(), "text": (text or "")[:MAX_JOURNAL_TEXT]})
    if len(j) > MAX_JOURNAL:
        del j[:-MAX_JOURNAL]


def clean_journal(raw) -> list[dict]:
    """规范化过程轨迹：只留字典条目、限长限量（导入的脏数据也走这里）"""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[-MAX_JOURNAL:]:
        if not isinstance(item, dict):
            continue
        out.append({
            "time": str(item.get("time") or "")[:32],
            "text": str(item.get("text") or "")[:MAX_JOURNAL_TEXT],
        })
    return out


class JournalIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


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


def context_budget(text: str, warn_lines: int = 150, over_lines: int = 300) -> dict:
    """交接物有多长（行 / KB / 档位）。

    为什么值得报这个数字：接手方是模型，上下文窗口是它的稀缺资源 ——
    上游（Claude Code 对 CLAUDE.md 的官方口径）也是按"超过约 200 行遵循度开始掉"
    来建议的。交接包不是越长越负责：过长会被截断、被略读，反而丢关键信息。
    报出来让人和智能体自己决定要不要精简。
    """
    lines = len(text.splitlines())
    kb = round(len(text.encode("utf-8")) / 1024, 1)
    level = "ok" if lines <= warn_lines else ("warn" if lines <= over_lines else "over")
    return {"lines": lines, "kb": kb, "level": level}


def _budget_note(text: str) -> str:
    """给交接物末尾追加一行尺寸提示（只在偏长/过载时说话，正常长度不打扰）"""
    b = context_budget(text)
    if b["level"] == "ok":
        return ""
    hint = {"warn": "偏长，建议精简背景与已完成任务",
            "over": "过长：接手方很可能读不完或被截断，务必裁剪"}[b["level"]]
    return f"\n\n—— 本包约 {b['lines']} 行 / {b['kb']} KB，{hint}。"


def _build_handoff_markdown(p: dict, memory_rows: list[dict] | None = None) -> str:
    """`memory_rows` = 这个项目的记忆列表。批量导出（export-all）必须一次读盘后传进来：
    逐个项目各自 `_load_memories()` 会让 memories.json 的读取次数等于项目数。"""
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
    git_block = _git_line_for_handoff(p)
    if git_block:
        lines.append("## 代码现状（git）")
        lines.append("")
        lines.append(git_block)
        lines.append("")
    tasks = topo_order(p.get("tasks", []) or [])      # 按依赖排序：接手的人照着做就行
    by_id = {t.get("id"): t for t in (p.get("tasks") or [])}
    if tasks:
        lines.append("## 任务清单（按依赖排序）")
        lines.append("")
        lines.append("| 任务 | 状态 | 优先级 | 负责AI | 前置 | 描述 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for t in tasks:
            title = (t.get("title") or "").replace("|", "\\|").replace("\n", " ")
            desc = (t.get("desc") or "").replace("|", "\\|").replace("\n", " ")
            deps = [d for d in (t.get("depends_on") or []) if d in by_id]
            dep_txt = "、".join((by_id[d].get("title") or d) for d in deps) if deps else "—"
            lines.append(f"| {title} | {t['status']} | {t['priority']} | {t.get('agent','') or '—'} "
                         f"| {dep_txt} | {desc} |")
        lines.append("")
        journaled = [(t, t.get("journal") or []) for t in tasks]
        journaled = [(t, j) for t, j in journaled if j]
        if journaled:
            lines.append("### 任务过程记录（每个任务最近 3 条）")
            lines.append("")
            for t, j in journaled:
                lines.append(f"**{t.get('title', '')}**")
                for e in j[-3:]:
                    lines.append(f"- [{e.get('time', '')}] {e.get('text', '')}")
                lines.append("")
        mems = memory_rows if memory_rows is not None else \
            [m for m in _load_memories() if m.get("pid") == p.get("id")]
        if mems:
            lines.append("### 已沉淀记忆（结构化要点）")
            lines.append("")
            for m in mems[-15:]:
                lines.append(f"- 【{m.get('type', '事实')}】[{m.get('time', '')}] {m.get('text', '')}")
            lines.append("")
        with_refs = [t for t in tasks if t.get("refs")]
        if with_refs:
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
    lines.append(f"- 结构化接手包（喂给智能体）：`{_local_url('/api/context/' + pid)}`")
    lines.append(f"- Markdown 交接包：`{_local_url('/api/export/' + pid)}`")
    lines.append(f"- 自包含接手页（人看的单文件网页）：`{_local_url('/api/export/page/' + pid)}`")
    lines.append("")
    lines.append("服务没开时这三个链接取不到，用上面的快照即可。")
    lines.append("")
    lines.append("---")
    lines.append("由 AI 交接看板自动生成")
    return "\n".join(lines)


@app.get("/api/export/{pid}")
def export_handoff(pid: str, redact: int = 0):
    found = find_project(pid)
    if not found:
        raise HTTPException(404, "项目不存在")
    md = _build_handoff_markdown(found[1])
    if redact:
        md = redact_text(md)
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


# =====================================================================
#  统计 / 就绪度 / 完整交接包 / 趋势
# =====================================================================

def _due_state(t: dict) -> str:
    """任务截止状态：overdue=已逾期 / soon=2 天内临期 / ok=正常 / 空=未设截止或已完成"""
    due = (t.get("due_date") or "").strip()
    if not due or t.get("status") == "已完成":
        return ""
    try:
        d = datetime.strptime(due, "%Y-%m-%d").date()
    except ValueError:
        return ""
    today = datetime.now().date()
    if d < today:
        return "overdue"
    if (d - today).days <= 2:
        return "soon"
    return "ok"


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
        if suffix not in (".md", ".txt", ".markdown", ".agentsmd"):
            raise HTTPException(400, "仅支持保存为 .md / .txt / .markdown / .agentsmd 文件")
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


@app.get("/api/export-all")
def export_all(redact: int = 0):
    data = get_projects_data()
    projects = data.get("projects", [])
    mems_by_pid = _memories_by_pid()
    head = f"# AI 交接看板 · 全项目交接包\n\n> 共 {len(projects)} 个项目 · 导出时间 {now_str()}\n"
    parts = [head]
    for p in projects:
        md = _build_handoff_markdown(p, mems_by_pid.get(p.get("id"), []))
        parts.append("\n\n---\n\n" + (redact_text(md) if redact else md))
    return PlainTextResponse("".join(parts), media_type="text/markdown; charset=utf-8")


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
    md = _build_handoff_markdown(p)
    page = build_handoff_page(
        p, md,
        git_block=_git_line_for_handoff(p),
        port=SERVICE_PORT,
        generated_at=now_str(),
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
        text, ep = llm.chat(cfg, [{"role": "system", "content": sys_msg},
                                  {"role": "user", "content": user_msg}], temperature=0.2, feature="AGENTS.md")
        if not text or len(text.strip()) < 30:
            raise ValueError("生成内容过短")
        return text.strip(), "ai", ep
    except Exception:
        return _build_agents_md_local(p), "local", ""


# ---- 多智能体入口文件（对比同类：Claude/Cursor/Copilot/Gemini/Cline/Aider/Windsurf 各要各的入口） ----

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
    folder = backups / f"backup_{ts}"
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
        out.append({"name": b.name, "time": b.name.replace("backup_", "").replace("_", " "), "files": files})
    return {"backups": out}


# ---- 全量数据导出 / 导入（跨机迁移） ----

@app.get("/api/data/export")
def export_all_data():
    data = get_projects_data()
    return {
        "app": "ai-kanban",
        "version": APP_VERSION,
        "exported_at": now_str(),
        "projects": data.get("projects", []),
        # 记忆层单独存一个文件，不带出去就等于换机后"决策/坑/约定"全丢
        "memories": _load_memories(),
    }


class ImportBody(BaseModel):
    content: str = Field(..., min_length=2, max_length=10_000_000)


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
    projects = [p for p in projects if isinstance(p, dict) and p.get("id") and p.get("name")]
    # 记忆层：老导出文件里没有这个键 —— 那就**不动**当前记忆，不能因为导入而清空
    raw_mem = cleaned.get("memories")
    mem_items = None
    if isinstance(raw_mem, list):
        mem_items = [m for m in raw_mem if isinstance(m, dict) and m.get("id") and m.get("text")]

    # 导入前自动留一份快照（零删除安全网，随时可从备份回滚）
    try:
        create_backup()
    except Exception:
        pass

    def worker(data: dict):
        # 读-改-写必须在同一个临界区里（硬性约定 1）：先前这里取了一次读锁拿快照、
        # 再取一次写锁整份覆盖，两次取锁之间别的进程/接口写入的项目会被无声抹掉。
        data["projects"] = projects
        return True, {"ok": True, "imported": len(projects)}

    result = mutate(worker)[1]
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
    ok = [r for r in recs if r.get("ok")]
    totals = {"calls": len(ok), "prompt_tokens": 0, "completion_tokens": 0, "failed": len(recs) - len(ok)}
    by_feature, by_model = {}, {}
    for r in ok:
        pt = r.get("prompt_tokens") or 0
        ct = r.get("completion_tokens") or 0
        totals["prompt_tokens"] += pt
        totals["completion_tokens"] += ct
        bf = by_feature.setdefault(r.get("feature", "?"), {"calls": 0, "tokens": 0})
        bf["calls"] += 1
        bf["tokens"] += pt + ct
        bm = by_model.setdefault(r.get("model", "?"), {"calls": 0, "tokens": 0})
        bm["calls"] += 1
        bm["tokens"] += pt + ct
    recent = sorted(recs, key=lambda r: r.get("time", ""), reverse=True)[:20]
    return {"totals": totals, "by_feature": by_feature, "by_model": by_model, "recent": recent}


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


class MemoryIn(BaseModel):
    pid: str = ""
    type: str = "事实"
    text: str = Field(..., min_length=1, max_length=2000)
    source: str = "手动"


@app.get("/api/memories")
def list_memories(pid: str = "", q: str = "", mtype: str = ""):
    items = _load_memories()
    if pid:
        items = [m for m in items if m.get("pid") == pid]
    if mtype:
        items = [m for m in items if m.get("type") == mtype]
    if q.strip():
        ql = q.strip().lower()
        items = [m for m in items if ql in (m.get("text") or "").lower()]
    items = sorted(items, key=lambda m: m.get("time", ""), reverse=True)
    return {"memories": items}


@app.post("/api/memories", status_code=201)
def add_memory(body: MemoryIn):
    if body.type not in MEMORY_TYPES:
        body.type = "事实"
    entry = {
        "id": uuid.uuid4().hex[:12],
        "pid": body.pid, "type": body.type,
        "text": body.text.strip(),
        "source": body.source, "time": now_str(),
    }

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

def session_sources() -> list[dict]:
    """默认探测常见 AI 工具会话目录；自定义源存 config.ai.session_sources"""
    home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    defaults = [
        {"name": "Claude Code", "path": str(home / ".claude" / "projects"), "type": "claude_jsonl"},
        {"name": "Cline (VS Code)", "path": str(home / "AppData" / "Roaming" / "Code" / "User"
                                                / "globalStorage" / "saoudrizwan.claude-dev" / "tasks"), "type": "cline"},
        {"name": "Cline (独立版)", "path": str(home / ".cline" / "data" / "tasks"), "type": "cline"},
    ]
    cfg = llm.load_config()
    custom = cfg.get("ai", {}).get("session_sources") or []
    return defaults + [c for c in custom if isinstance(c, dict) and c.get("path")]


def _walk_bounded(root: Path, budget: int = 2000, max_depth: int = 3):
    """懒走目录，最多吐 budget 个文件、深 max_depth 层。

    自定义会话源常被随手指到很宽的目录（甚至整块盘）。原来写的是
    `[f for f in root.rglob("*") if f.is_file()][:200]` —— 切片在推导式**之后**，
    等于先把整棵树物化进内存才想起来只要 200 个，一个宽路径就能挂住整个请求。
    """
    base_depth = len(root.parts)
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if len(Path(dirpath).parts) - base_depth >= max_depth:
            dirnames[:] = []            # 不再往下走
        for name in filenames:
            seen += 1
            if seen > budget:
                return
            yield Path(dirpath) / name


def _newest_files(paths, n: int = 8) -> list[Path]:
    """按修改时间取最近 n 个。

    单个文件 stat 失败（扫描过程中被删/被占用）跳过就行，不能像原来那样
    把它留给 `sorted(key=f.stat)` —— 那会让整次 /api/sessions/scan 直接 500。
    """
    scored = []
    for f in paths:
        try:
            scored.append((f.stat().st_mtime, f))
        except OSError:
            continue
    return [f for _, f in heapq.nlargest(n, scored, key=lambda x: x[0])]


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


def _read_tail_text(f: Path, limit_bytes: int = 1 << 20) -> str:
    """只读文件末尾 limit_bytes 字节。

    会话文件动辄几百 MB，而提取逻辑本来就是"尾部优先"：整份 `read_text()`
    等于把内存和一次几秒的 IO 搭进去，只为拿最后 12000 字。
    """
    try:
        size = f.stat().st_size
        with open(f, "rb") as fh:
            if size > limit_bytes:
                fh.seek(-limit_bytes, os.SEEK_END)
            data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", "replace")


def _extract_session_text(path: str, max_chars: int = 12000) -> str:
    """从会话文件提取对话文本（尾部优先）。支持 Claude Code JSONL / cline JSON / 纯文本"""
    f = Path(path)
    if not f.exists():
        raise HTTPException(404, "会话文件不存在")
    texts: list[str] = []
    if f.suffix.lower() == ".jsonl":
        try:
            lines = _read_tail_text(f).splitlines()
        except Exception:
            lines = []
        for line in lines[-200:]:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            msg = obj.get("message") or {}
            role = str(obj.get("type") or msg.get("role") or "")
            content = msg.get("content")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content
                                   if isinstance(c, dict) and c.get("type") == "text")
            if isinstance(content, str) and content.strip():
                tag = "用户" if "user" in role.lower() else "助手"
                texts.append(f"{tag}：{content.strip()[:600]}")
    elif f.suffix.lower() == ".json":
        try:
            data = json.loads(_read_tail_text(f))
        except Exception:
            data = None
        if isinstance(data, list):
            for m in data[-120:]:
                if not isinstance(m, dict):
                    continue
                txt = m.get("text") or ""
                say = str(m.get("say") or m.get("type") or "")
                if txt and "api_req" not in say and "tool" not in say:
                    role = "用户" if "user" in say else "助手"
                    texts.append(f"{role}：{str(txt)[:600]}")
    if not texts:
        texts = [_read_tail_text(f)[-6000:]]
    return "\n\n".join(texts)[-max_chars:]


def _session_roots() -> list[Path]:
    """已配置的会话源根目录（默认源 + config 里用户自定义的）"""
    roots = []
    for src in session_sources():
        p = (src or {}).get("path") if isinstance(src, dict) else None
        if not p:
            continue
        try:
            roots.append(Path(p).resolve())
        except Exception:
            continue
    return roots


def is_allowed_session_file(raw_path: str) -> bool:
    """只允许读取「已配置的会话源」里的文件。

    为什么必须收口：preview/ingest 读到的内容会**送进 LLM** ——
    等于把文件内容搬到外部模型。不限源的话，任意本机路径（config.json 里的 API Key、
    .env、别的项目源码）都能被读走 12000 字符，直接掀翻「数据不出本机」这条边界。
    """
    if not raw_path:
        return False
    try:
        target = Path(raw_path).resolve()
    except Exception:
        return False
    if not target.is_file():
        return False
    for root in _session_roots():
        try:
            if target == root or root in target.parents:
                return True
        except Exception:
            continue
    return False


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
        return {
            "usable": True, "reason": "", "project_count": len(projects),
            "task_count": sum(len(p.get("tasks") or []) for p in projects),
            "names": [p.get("name", "") for p in projects[:3]],
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as e:
        return {"usable": False, "reason": f"无法解析：{str(e)[:100]}",
                "project_count": 0, "task_count": 0, "names": [],
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

    def worker(data: dict):
        try:
            if data_file().exists():
                shutil.copy2(data_file(), data_dir() / "projects.json.before-restore.bak")
        except Exception:
            pass
        data.clear()
        data.update(restored)
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
            payload = [m for m in loaded if isinstance(m, dict) and m.get("id")]

            def wm(items: list[dict]):
                items[:] = payload
                return True, None

            _mutate_memories(wm)
            memory_count = len(payload)

    out = {"ok": True, "source": body.source,
           "project_count": len(restored["projects"]),
           "task_count": sum(len(p.get("tasks") or []) for p in restored["projects"])}
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
        '"new_tasks": [{"title": "任务标题", "desc": "描述", "priority": "高/中/低", "status": "待办/进行中/已完成/阻塞"}], '
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
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ], temperature=0.15, feature="会话提炼")
        parsed = _extract_json(text)
        if not isinstance(parsed, dict):
            raise ValueError("提炼结果格式不正确")
        return {"parsed": parsed, "endpoint": ep}
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
# =====================================================================

GIT_TIMEOUT = 6
_git_cache: dict[str, tuple[float, dict]] = {}
_GIT_CACHE_TTL = 60.0


def _run_git(args: list[str], cwd: Path, timeout: int = GIT_TIMEOUT) -> tuple[int, str]:
    """只以列表形式调用 git，不经过 shell —— 仓库路径里的特殊字符不会被当命令执行"""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
        # 注意用 rstrip 而不是 strip：git 的 porcelain 输出形如 " M 文件"，
        # 前面的状态列空格是有意义的，strip 掉就会把文件名截错。
        return proc.returncode, (proc.stdout or "").rstrip()
    except FileNotFoundError:
        return 127, "未找到 git 命令"
    except subprocess.TimeoutExpired:
        return 124, f"git 命令超时（>{timeout}s）"
    except Exception as e:                       # noqa: BLE001
        return 1, f"git 调用失败：{e}"


def git_snapshot(repo_path: str, limit: int = 10, use_cache: bool = True) -> dict:
    """读取仓库现状。任何异常都转成 ok=False 的描述，绝不向上抛。"""
    raw = (repo_path or "").strip()
    if not raw:
        return {"ok": False, "error": "该项目没有填代码路径"}
    repo = Path(raw)
    if not repo.exists():
        return {"ok": False, "error": f"代码路径不存在：{raw}"}
    if not repo.is_dir():
        return {"ok": False, "error": f"代码路径不是目录：{raw}"}

    cache_key = f"{repo}|{limit}"
    if use_cache:
        hit = _git_cache.get(cache_key)
        if hit and time.time() - hit[0] < _GIT_CACHE_TTL:
            return hit[1]

    code, out = _run_git(["rev-parse", "--is-inside-work-tree"], repo)
    if code != 0 or out != "true":
        return {"ok": False, "error": "该路径不是 git 仓库" if code != 127 else out}

    branch_code, branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    head_code, head_line = _run_git(
        ["log", "-1", "--date=format:%Y-%m-%d %H:%M", "--pretty=%h%x1f%ad%x1f%an%x1f%s"], repo)
    commits_code, log_out = _run_git(
        ["log", f"-{max(1, min(limit, 50))}", "--date=format:%Y-%m-%d %H:%M",
         "--pretty=%h%x1f%ad%x1f%s"], repo)
    dirty_code, porcelain = _run_git(["status", "--porcelain"], repo)

    commits = []
    for line in (log_out or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) >= 3:
            commits.append({"hash": parts[0], "date": parts[1], "subject": parts[2][:200]})

    head = {}
    if head_code == 0 and head_line:
        parts = head_line.split("\x1f")
        if len(parts) >= 4:
            head = {"hash": parts[0], "date": parts[1], "author": parts[2], "subject": parts[3][:200]}

    dirty_files = []
    if dirty_code == 0:
        for line in (porcelain or "").splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:                       # 重命名：取新名字
                path = path.split(" -> ")[-1].strip()
            dirty_files.append(path)
    days_since: int | None = None
    if head.get("date"):
        try:
            days_since = (datetime.now() - datetime.strptime(head["date"], "%Y-%m-%d %H:%M")).days
        except Exception:
            days_since = None

    snapshot = {
        "ok": True, "path": str(repo),
        "branch": branch if branch_code == 0 else "",
        "head": head,
        "commits": commits,
        "dirty_count": len(dirty_files),
        "dirty_files": dirty_files[:20],
        "days_since_commit": days_since,
    }
    if use_cache:
        _git_cache[cache_key] = (time.time(), snapshot)
    return snapshot


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
        "next": [{"id": t["id"], "title": t.get("title"), "priority": t.get("priority"),
                  "refs": t.get("refs") or []} for t in ready],
        "blocked": [{"id": t["id"], "title": t.get("title"),
                     "waiting_for": [by_id[d].get("title", d)
                                     for d in (t.get("depends_on") or [])
                                     if d in by_id and by_id[d].get("status") != "已完成"]}
                    for t in tasks if t.get("id") not in ready_ids and t.get("status") != "已完成"],
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

    自由文本（背景 / 下一步 / 过程记录 / 记忆）过一道脱敏再送出去：这些是要离开本机
    到模型那边的内容，而用户从会话里抄进看板的凭据很常见。
    id / 标题 / 状态 / 分支名原样保留 —— 模型要把它们回传用来对齐项目与任务。
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
            "tasks": [{"title": t["title"], "status": t.get("status", ""), "priority": t.get("priority", ""),
                       **({"due_date": t["due_date"]} if t.get("due_date") else {}),
                       **({"refs": t["refs"]} if t.get("refs") else {}),
                       **({"recent_journal": [(e.get("time", ""),
                                               redact_text((e.get("text") or ""))[:80])
                                              for e in (t.get("journal") or [])[-1:]]}
                          if t.get("journal") else {})}
                      for t in (p.get("tasks") or [])[:40]],
            "memories": [{**m, "text": redact_text(m.get("text") or "")}
                         for m in mems_by_pid.get(p["id"], [])][-10:],
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
                "last_commit_subject": (snap.get("head") or {}).get("subject", ""),
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
                })
            cfg["ai"]["endpoints"] = new_eps
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


@app.post("/api/ai/apply-suggestions")
def ai_apply_suggestions(body: list[SuggestionUpdate]):
    allowed_field = ("next_action", "handoff_note", "background", "priority", "status")

    def worker(data: dict):
        ok, masked = 0, 0
        for u in body:
            if u.field not in allowed_field:
                continue
            if MASK_MARK in (u.value or ""):
                # 送给模型的背景是脱敏过的，它可能把 `********` 当成原文抄回来。
                # 照写就是把用户真内容换成一串星号（而且看不出丢过什么）——
                # 宁可不落地，也要把这个数报出去，让用户去原处看一眼。
                masked += 1
                continue
            for p in data.get("projects", []):
                if p.get("id") == u.project_id:
                    p[u.field] = u.value
                    p["updated_at"] = now_str()
                    ok += 1
                    break
        # 直接写字段可能带进非法状态/优先级，交给统一规范化收口
        _sanitize(data)
        return ok > 0, {"applied": ok, "skipped_masked": masked}

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
        '{"actions": [{"type": "create_task", "project_id": "...", "title": "...", "desc": "...", "priority": "高/中/低", "status": "待办/进行中/已完成/阻塞", "agent": "..."}, ...]}\n'
        "规则：1) 项目用 id 标识，任务用项目下现有任务标题匹配；2) 若指令信息不足无法确定操作，"
        '输出 {"need_clarification": "具体需要用户补充什么"}; 3) 不要编造不存在的项目 id；'
        "4) 删除类操作必须能明确匹配到目标才输出；5) 一次指令可能包含多个操作，按顺序列出。"
    )
    user_msg = "看板数据：\n" + json.dumps(projects, ensure_ascii=False, indent=1) + "\n\n用户指令：" + body.text
    try:
        cfg = llm.load_config()
        text, ep = llm.chat(cfg, [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}], temperature=0.1, feature="自然语言指令")
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
    """幂等启动调度线程（import 即拉起一次）

    KANBAN_NO_SCHEDULER=1 时不启动 —— MCP 服务这类短命进程不该起后台线程。
    """
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED or os.environ.get("KANBAN_NO_SCHEDULER") == "1":
        return
    _SCHEDULER_STARTED = True
    threading.Thread(target=_scheduler_loop, daemon=True, name="kanban-scheduler").start()


start_scheduler()
