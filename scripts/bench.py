# -*- coding: utf-8 -*-
"""
读写性能基准：把 N 个大项目灌进隔离数据目录，量一遍关键接口的真实延迟。

数据层是「整份 JSON 读改写」，所以项目数增长时最该盯的是写接口与列表接口。
先量再优化，别凭直觉加缓存。

用法：
    .venv\\Scripts\\python.exe scripts\\bench.py            # 默认 1000 个项目
    .venv\\Scripts\\python.exe scripts\\bench.py 5000
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 20


def build_dataset(n: int) -> Path:
    """直接写文件造数据，避免用接口灌 1000 次（每次都是全量重写，本身就很慢）"""
    d = Path(tempfile.gettempdir()) / "kanban_bench"
    d.mkdir(parents=True, exist_ok=True)
    projects = []
    for i in range(n):
        projects.append({
            "id": f"p{i:05d}", "name": f"基准项目 {i}", "status": "进行中",
            "progress": i % 101, "priority": "中", "tags": [f"t{i % 7}"],
            "repo_path": f"D:\\work\\demo-{i}", "background": "背景" * 40,
            "handoff_note": "交接说明" * 60, "next_action": "下一步" * 20,
            "tasks": [{"id": f"t{i}-{j}", "title": f"任务 {j}", "desc": "描述" * 20,
                       "status": "待办", "priority": "中", "agent": "",
                       "created_at": "2026-09-14 10:00", "updated_at": "2026-09-14 10:00"}
                      for j in range(10)],
            "created_at": "2026-09-14 10:00", "updated_at": "2026-09-14 10:00",
        })
    (d / "projects.json").write_text(json.dumps({"projects": projects}, ensure_ascii=False), encoding="utf-8")
    return d


def timeit(fn, rounds: int) -> tuple[float, float, float]:
    samples = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return min(samples), statistics.median(samples), max(samples)


def main() -> int:
    data_dir = build_dataset(N)
    size = (data_dir / "projects.json").stat().st_size
    import os

    os.environ["KANBAN_DATA_DIR"] = str(data_dir)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, base_url="http://127.0.0.1") as c:
        read_list = lambda: c.get("/api/projects")
        read_stats = lambda: c.get("/api/stats")
        read_detail = lambda: c.get(f"/api/projects/p{N // 2:05d}")
        read_export = lambda: c.get("/api/export-all")
        write_task = lambda: c.post(f"/api/projects/p0001/tasks",
                                    json={"title": "基准任务", "status": "待办", "priority": "中"})

        print(f"数据集：{N} 个项目 · projects.json {size / 1024 / 1024:.2f} MB · 每项 10 个任务")
        print(f"{'接口':<28}{'min':>9}{'中位':>9}{'max':>9}  （毫秒，{ROUNDS} 次）")
        for name, fn in [("GET /api/projects", read_list), ("GET /api/stats", read_stats),
                         ("GET /api/projects/{pid}", read_detail), ("GET /api/export-all", read_export),
                         ("POST 新建任务（写）", write_task)]:
            lo, mid, hi = timeit(fn, ROUNDS)
            print(f"{name:<28}{lo:>9.1f}{mid:>9.1f}{hi:>9.1f}")

    import shutil

    shutil.rmtree(data_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
