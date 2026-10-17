# -*- coding: utf-8 -*-
"""git 集成：让看板和代码现实对上（只读、只跑固定子命令、绝不拼 shell）。

**从 `app/main.py` 整体搬来（2026-10-06 大文件继续拆分），内容逐字未改。**
`app/main.py` 用 `from .gitinfo import ...` 保住既有名字 —— 测试里的
`from app.main import git_snapshot` 仍然有效，`main` 内部的调用点也照常走这里。

⚠️ 有状态名字的 monkeypatch 点**随迁到这里**：`_run_git` / `_git_cache` / `GIT_TIMEOUT`
请打在 `app.gitinfo` 上（`app.main` 里那几个只是转发别名，打在 main 上管不到本模块
内部的调用）。纯函数调用的 patch（如 `monkeypatch.setattr(main, "git_snapshot", ...)`）
仍然打在 main 上有效 —— 那改的是 main 调用点的名字解析。
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

GIT_TIMEOUT = 6
_git_cache: dict[str, tuple[float, dict]] = {}
_GIT_CACHE_TTL = 60.0
_GIT_LOG_WINDOW = 50          # 缓存里存这么宽的一份（与 `/api/.../git` 自己声明的上限同数）

# 交接包全量导出要对几十上百个仓库各跑 5 个 git 子进程；串行排队实测冷态中位 5.8s
# （1000 项目基准，2026-10-08）。预取线程数有上限：git 是本地子进程，开太多反而抢 CPU，
# 32 并发已足够把"每个仓库 5 个子进程"的墙钟压到接近单个仓库的耗时。
PREFETCH_WORKERS = 32


def prefetch_snapshots(repo_paths: list[str], use_cache: bool = True) -> int:
    """并发预热 git 快照缓存；返回实际发起预取的仓库数（去重、只算存在的路径）。

    只写缓存、不改任何单仓语义：之后 `git_snapshot` 照常按 TTL 命中，`use_cache=False`
    的调用方（/api/.../git）依然绕过缓存读最新 —— 预取只是把"冷启动的第一次
    build_export_all"从串行 100 次子进程变成一批并发子进程。重复路径去重后才计数。
    """
    seen: dict[str, None] = {}
    for raw in repo_paths:
        p = (raw or "").strip()
        if p and p not in seen and Path(p).exists():
            seen[p] = None
    if not seen:
        return 0
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(PREFETCH_WORKERS, len(seen))) as ex:
        list(ex.map(lambda p: git_snapshot(p, use_cache=use_cache), seen))
    return len(seen)


def _git_sliced(snap: dict, limit: int) -> dict:
    """按调用方要的条数切一份返回。切出来的是新列表，缓存里那份动不到。"""
    return {**snap, "commits": snap["commits"][:max(1, min(limit, _GIT_LOG_WINDOW))]}


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

    # 缓存键里**不带 limit**：以前是 `f"{repo}|{limit}"`，而同一次刷新里三个出口分别要
    # limit=1 / 3 / 5（`_projects_brief`、`project_context`、交接页），于是同一个仓库
    # 在 60 秒 TTL 内被完整扫 3 遍 × 每次 5 个 git 子进程。改成"每次取满一窗、按人切片"：
    # 窗口取 `/api/.../git` 自己声明的上限 50，所以任何调用方都不会拿到比它要的更少。
    # 返回的是浅拷贝 + 切过的新列表 —— 不让调用方摸到缓存里那份。
    cache_key = str(repo)
    if use_cache:
        hit = _git_cache.get(cache_key)
        if hit and time.time() - hit[0] < _GIT_CACHE_TTL:
            return _git_sliced(hit[1], limit)

    code, out = _run_git(["rev-parse", "--is-inside-work-tree"], repo)
    if code != 0 or out != "true":
        return {"ok": False, "error": "该路径不是 git 仓库" if code != 127 else out}

    branch_code, branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    head_code, head_line = _run_git(
        ["log", "-1", "--date=format:%Y-%m-%d %H:%M", "--pretty=%h%x1f%ad%x1f%an%x1f%s"], repo)
    commits_code, log_out = _run_git(
        ["log", f"-{_GIT_LOG_WINDOW}", "--date=format:%Y-%m-%d %H:%M",
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
    return _git_sliced(snapshot, limit)


# 失效引用最多报这么多条：真实的交接场景里"三五处对不上"就足够说明问题，
# 报满一屏反而没人看；输出的口径是"有几处、都是谁"，不是一个不漏的清单。
MAX_REF_MISSING = 50


def check_refs_exist(repo_path: str, tasks: list[dict]) -> dict:
    """检查任务里的「相关代码位置」（refs）在仓库里还在不在。

    为什么值得单独做（第五轮竞品对标里 drift auditing 在**任务层**的同族形态）：
    refs 是写给人/AI 的"去这里改"路标；文件被改名或挪走之后，它就悄悄变成一条死路 ——
    看板上看不出任何异常，接手的智能体照着找一圈才发现，而这正是交接最贵的那种浪费。

    ref 的形态：`app/main.py:120` / `app/main.py` / `tests/`（目录也算）。
    **冒号后的行号不校验**：行号漂移太常见，报出来只会变成噪音、把真问题淹掉。
    """
    base = Path(repo_path or "")
    if not repo_path or not base.is_dir():
        return {"ok": False, "total": 0, "missing": [], "checked_tasks": 0}
    total = 0
    missing: list[dict] = []
    for t in tasks:
        for ref in (t.get("refs") or []):
            raw = str(ref).split(":")[0].strip().replace("\\", "/")
            if not raw:
                continue
            total += 1
            try:
                exists = (base / raw).exists()
            except OSError:
                exists = False
            if not exists and len(missing) < MAX_REF_MISSING:
                missing.append({"task_id": t.get("id"), "task": t.get("title"), "ref": str(ref)})
    return {"ok": True, "total": total, "missing": missing, "checked_tasks": len(tasks)}
