# -*- coding: utf-8 -*-
"""任务依赖图与去重。

**从 `app/main.py` 整体搬来（2026-09-26 大文件拆分），内容逐字未改。**
`app/main.py` 用 `from app.taskgraph import ...` 保住既有名字 —— 测试里的
`from app.main import X` / `monkeypatch.setattr(main, "X", ...)` 仍然有效。

搬移理由：这些是**纯函数/纯常量**（不碰文件、不碰网络、不读全局状态），
原先埋在 3596 行的 `main.py` 里，只能靠端到端间接验证边界；独立成模块后可以穷举。
约束型注释（含"不要…否则…"的）已随代码一起搬，不要在新家把它们删掉。
"""
from __future__ import annotations

import re

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

def deps_blocking(t: dict, done_ids: set, known_ids: set) -> bool:
    """任务是否被前置挡住：存在"存在但未完成"的前置。

    与 `executable_tasks` 的就绪判定同一谓词（前置 id 不存在 = 视为已了结，
    数据自愈会把悬空依赖清掉）。2026-10-07 抽出成函数：`/api/inbox` 的
    "依赖没满足的不算"（AGENTS.md 约定 27）当时只写在文档里、实现漏了过滤 ——
    判定散在 docstring 而不落在共享函数里，就会各写各的。
    """
    return any(d in known_ids and d not in done_ids
               for d in (t.get("depends_on") or []))


def executable_tasks(tasks: list[dict]) -> list[dict]:
    """当前可以动手的任务：自己的状态未完成，且所有前置都已完成"""
    done_ids = {t.get("id") for t in tasks if t.get("status") == "已完成"}
    known_ids = {t.get("id") for t in tasks}      # 只算一次：原来这个集合是在下面按任务逐个重建的
    return [t for t in tasks
            if t.get("status") not in ("已完成", "待审查")
            and not deps_blocking(t, done_ids, known_ids)]

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


# ---------------- 任务关联的代码位置（同批从 app/main.py 搬来，逐字未改） ----------------

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
