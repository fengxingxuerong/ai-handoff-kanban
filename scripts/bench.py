# -*- coding: utf-8 -*-
"""读写性能基准：把 N 个大项目灌进隔离数据目录，量一遍关键接口的真实延迟。

数据层是「整份 JSON 读改写」，所以项目数增长时最该盯的是写接口与列表接口。
先量再优化，别凭直觉加缓存。

**为什么现在要 `--repos`**：以前造数据用的是 `repo_path="D:\\work\\demo-{i}"` —— 那种路径
根本不存在，`git_snapshot` 在 `repo.exists()` 就返回了，于是**历次引用的"1000 项目中位 62ms"
里 git 探测成本恒等于 0**，而"列表接口 >300ms 才考虑换存储层"这条判断线正是建立在那个数上的。
现在默认就建真仓库并量两条路（带 git / 不带 git），差值单独印出来；git 不可用时明说，
不悄悄退回旧测法。

用法：
    .venv\\Scripts\\python.exe scripts\\bench.py                # 默认 1000 项目 + 20 个真仓库
    .venv\\Scripts\\python.exe scripts\\bench.py 5000
    .venv\\Scripts\\python.exe scripts\\bench.py 1000 20 --repos 50
    .venv\\Scripts\\python.exe scripts\\bench.py 1000 20 --repos 0   # 只要不含 git 的那份数
"""
from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 输出里有 ✓ 和中文：重定向到文件时 Python 用系统 ANSI 码页（本机 cp936），
# 打第一个 ✓ 就 UnicodeEncodeError 崩掉 —— 门禁脚本必须能被重定向与管道。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001  被替换掉的 stdout 可能没有 reconfigure
    pass

sys.path.insert(0, str(ROOT))

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
REPOS = 20
if "--repos" in sys.argv:
    REPOS = int(sys.argv[sys.argv.index("--repos") + 1])
N = int(ARGS[0]) if ARGS else 1000
ROUNDS = int(ARGS[1]) if len(ARGS) > 1 else 20


def make_git_repos(count: int, where: Path) -> list[str]:
    """造 count 个真仓库（各 3 条提交、留一点脏文件）。git 不可用就返回空并说明。

    为什么只建几十个而不是每个项目一个：真实使用中"每个项目都是不同仓库"才是常态，
    而 `git_snapshot` 有 60 秒 TTL —— 仓库数远小于项目数时，缓存命中率会明显好于真实情况，
    量出来的"带 git"那一列就会偏乐观。所以这里刻意让仓库数与项目数同量级可调（`--repos`），
    并同时报**冷**（每轮清缓存，等于 TTL 全过期）与**热**两份数：冷那份才是上界。
    """
    if count <= 0:
        return []
    where.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for i in range(count):
        d = where / f"repo{i}"
        d.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            for j in range(3):
                (d / f"f{j}.txt").write_text(f"{j}\n", encoding="utf-8")
                subprocess.run(["git", "add", "-A"], cwd=d, check=True)
                subprocess.run(["git", "-c", "user.email=b@bench", "-c", "user.name=bench",
                                "commit", "-q", "-m", f"c{j}"], cwd=d, check=True)
            (d / "dirty.txt").write_text("未提交\n", encoding="utf-8")
            out.append(str(d))
        except Exception as e:                                 # noqa: BLE001
            print(f"（造不出测试仓库，git 这一列将全部缺测：{type(e).__name__}: {str(e)[:80]}）")
            return []
    return out


def build_dataset(n: int, repo_paths: list[str]) -> Path:
    """直接写文件造数据，避免用接口灌 1000 次（每次都是全量重写，本身就很慢）"""
    d = Path(tempfile.gettempdir()) / "kanban_bench"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    projects = []
    for i in range(n):
        projects.append({
            "id": f"p{i:05d}", "name": f"基准项目 {i}", "status": "进行中",
            "progress": i % 101, "priority": "中", "tags": [f"t{i % 7}"],
            "repo_path": repo_paths[i % len(repo_paths)] if repo_paths else "",
            "background": "背景" * 40,
            "handoff_note": "交接说明" * 60, "next_action": "下一步" * 20,
            "tasks": [{"id": f"t{i}-{j}", "title": f"任务 {j}", "desc": "描述" * 20,
                       "status": "待办", "priority": "中", "agent": "",
                       "created_at": "2026-09-14 10:00", "updated_at": "2026-09-14 10:00"}
                      for j in range(10)],
            "created_at": "2026-09-14 10:00", "updated_at": "2026-09-14 10:00",
        })
    (d / "projects.json").write_text(json.dumps({"projects": projects}, ensure_ascii=False), encoding="utf-8")
    return d


def timeit(fn, rounds: int, between=None) -> tuple[float, float, float]:
    samples = []
    for _ in range(rounds):
        if between:
            between()
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return min(samples), statistics.median(samples), max(samples)


def main() -> int:
    repos = make_git_repos(REPOS, Path(tempfile.gettempdir()) / "kanban_bench_repos")
    data_dir = build_dataset(N, repos)
    size = (data_dir / "projects.json").stat().st_size
    import os

    os.environ["KANBAN_DATA_DIR"] = str(data_dir)

    from fastapi.testclient import TestClient

    from app import main as kanban
    from app.main import app

    with TestClient(app, base_url="http://127.0.0.1") as c:
        read_list = lambda: c.get("/api/projects")
        read_stats = lambda: c.get("/api/stats")
        read_detail = lambda: c.get(f"/api/projects/p{N // 2:05d}")
        read_export = lambda: c.get("/api/export-all")
        write_task = lambda: c.post("/api/projects/p0001/tasks",
                                    json={"title": "基准任务", "status": "待办", "priority": "中"})

        print(f"数据集：{N} 个项目 · projects.json {size / 1024 / 1024:.2f} MB · 每项 10 个任务")
        if repos:
            print(f"代码路径：{N} 个项目轮转指向 {len(repos)} 个**真实 git 仓库**"
                  f"（各 3 条提交 + 1 个脏文件）—— git 探测计入下面的数字")
            print("「冷」= 每轮清空 git 缓存（等价 TTL 全过期，生产上的上界）；"
                  "「热」= 60 秒 TTL 内重复请求")
        else:
            print("⚠ 这一份数字**不含任何 git 成本**（没造出真仓库或 --repos 0）—— "
                  "别拿它当「列表接口多快」的结论，那正是以前 62ms 那个数的口径缺陷")
        print(f"{'接口':<28}{'min':>9}{'中位':>9}{'max':>9}  （毫秒，{ROUNDS} 次）")
        cold = lambda: kanban._git_cache.clear()
        for name, fn in [("GET /api/projects", read_list), ("GET /api/stats", read_stats),
                         ("GET /api/projects/{pid}", read_detail), ("GET /api/export-all", read_export),
                         ("POST 新建任务（写）", write_task)]:
            lo, mid, hi = timeit(fn, ROUNDS)
            print(f"{name:<28}{lo:>9.1f}{mid:>9.1f}{hi:>9.1f}")
            if repos:
                clo, cmid, chi = timeit(fn, ROUNDS, between=cold)
                print(f"{'  └ 冷（每次重探 git）':<27}{clo:>9.1f}{cmid:>9.1f}{chi:>9.1f}")

    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(Path(tempfile.gettempdir()) / "kanban_bench_repos", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
