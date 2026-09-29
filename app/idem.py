# -*- coding: utf-8 -*-
"""创建幂等键。

**从 `app/main.py` 整体搬来（2026-09-26 大文件拆分），内容逐字未改。**
`app/main.py` 用 `from app.idem import ...` 保住既有名字 —— 测试里的
`from app.main import X` / `monkeypatch.setattr(main, "X", ...)` 仍然有效。

搬移理由：这些是**纯函数/纯常量**（不碰文件、不碰网络、不读全局状态），
原先埋在 3596 行的 `main.py` 里，只能靠端到端间接验证边界；独立成模块后可以穷举。
约束型注释（含"不要…否则…"的）已随代码一起搬，不要在新家把它们删掉。
"""
from __future__ import annotations

import hashlib

MAX_IDEM = 500           # 幂等键表上限：追加型存储，按硬性约定 11 必须有上限
IDEM_KEY = "_client_refs"

def _idem_seen(data: dict, ref: str) -> str | None:
    """这个 `client_ref` 之前用过吗？用过就返回当初建出来的 id。"""
    hit = (data.get(IDEM_KEY) or {}).get(ref)
    return hit if isinstance(hit, str) else None

def _idem_remember(data: dict, ref: str, ident: str) -> None:
    """记住"这个键造出了这条记录"。

    智能体调用超时后重试是常态：没有幂等键，同一条指令就会造出成对的重复任务，
    重复项还会跟着进交接包与拓扑序，事后要靠"标题一样"手工分辨。
    写入与记账必须在**同一个临界区**里（所以它吃 `mutate` 的那份 `data`）——
    分两次做就会出现"建成了但没记下来"，重试照样翻倍。
    同一个键配不同正文时按"重试"处理：返回第一次的结果，不覆盖。
    """
    table = data.setdefault(IDEM_KEY, {})
    table[ref] = ident
    while len(table) > MAX_IDEM:      # 先进先出；幂等窗口只需覆盖"刚才那次重试"
        table.pop(next(iter(table)))

def _clean_ref(raw) -> str:
    """规范化幂等键。**超长不能简单截断**：两条前 80 字符相同、后面才不同的不同请求
    会被截成同一个键，第二次直接拿回第一次那条记录的 id —— 静默返回错的东西，
    比报个错严重得多（实测过：`A`*80+"-first" 与 `A`*80+"-SECOND" 撞成同一条）。
    超长改成"前缀 + 全串哈希"：表内长度仍有界，不同输入必然不同键。"""
    ref = str(raw or "").strip()
    if len(ref) <= 80:
        return ref
    return ref[:48] + "~" + hashlib.sha1(ref.encode("utf-8")).hexdigest()[:16]
