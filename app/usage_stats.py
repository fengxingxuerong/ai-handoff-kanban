# -*- coding: utf-8 -*-
"""LLM 用量聚合（2026-10-01 P0-1 从 `app/main.py` 抽出）。

**为什么独立成模块**：usage.jsonl 的解析在 `app/main.py`，这里的聚合是**纯函数**
（不碰文件、不碰网络、不读全局状态）——按 日 / 端点 / 模型 / 功能 的调用次数、
token、失败数与估算成本，以及自然月预算使用情况。单独成模块是为了能穷举测试
各聚合口径，而不是靠端到端间接验证。

**字段口径**（与 `app/llm.py:_record_usage` 每行一致）：
`time, feature, endpoint, model, prompt_tokens, completion_tokens, duration_s, ok`。

**成本估算口径**：
`cost = prompt_tokens/1000 * per_1k_input + completion_tokens/1000 * per_1k_output`
按端点单价；端点没配单价（或配的是垃圾）则记 0——没配的不猜价，空配置下的
"成本为零"要能在界面上从"没配价格"的提示区分出来，不能伪装成"真没花钱"。
失败调用没有 token 数据，成本一律不算（计 0）。
"""
from __future__ import annotations

from datetime import datetime


def _f(v, default=0.0) -> float:
    """把任意值安全转成非负有限浮点数：解析不出 / 负值 / NaN / Inf → default。"""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    if n != n:          # NaN
        return default
    if n < 0:
        return default
    return n


def _cost(rec: dict, prices: dict | None) -> float:
    """单条成功调用的估算成本（美元，千 token 单价）。"""
    p = prices or {}
    in_p = _f(p.get("per_1k_input"))
    out_p = _f(p.get("per_1k_output"))
    return (_f(rec.get("prompt_tokens")) / 1000.0 * in_p
            + _f(rec.get("completion_tokens")) / 1000.0 * out_p)


def _date_of(rec: dict) -> str:
    """usage 记录的 time 形如 `2026-10-01 13:00:00`，按日聚合取前 10 位。"""
    t = rec.get("time") or ""
    return t[:10] if isinstance(t, str) and len(t) >= 10 else ""


# 配置写入的硬上限：价格是"每千 token 美元"（千 token 过 1000 美元是配置错误），
# 月度预算是美元（过 100 万美元没有现实意义，截到这一档防止手滑）。
MAX_PRICE_PER_1K = 1000.0
MAX_BUDGET_LIMIT = 1_000_000.0


def clamp_price(v, hi: float = MAX_PRICE_PER_1K) -> float:
    """端点单价钳到 [0, hi]：负数 / NaN / 解析不出 → 0，超上限 → 上限。

    配置写入（`PUT /api/ai/config`）与成本估算共用同一道钳 —— 写侧与读侧各写一遍
    迟早漂成两种口径（输入 1e9 写入成功，展示时却按"没配价格"记 0 成本）。
    """
    n = _f(v, default=0.0)
    return min(n, hi)


def clamp_budget(v, hi: float = MAX_BUDGET_LIMIT) -> float:
    """月度预算上限钳到 [0, hi]（语义同 `clamp_price`）。"""
    n = _f(v, default=0.0)
    return min(n, hi)


def price_map_from_endpoints(endpoints: list[dict] | None) -> dict:
    """从 AI 配置的端点列表抽价格表：`{端点名: {"per_1k_input", "per_1k_output"}}`。

    端点没配价格字段 = 按 0 处理（成本不估算），由前端从"定价未配置"提示区分。
    """
    out: dict = {}
    for ep in endpoints or []:
        if not isinstance(ep, dict):
            continue
        name = str(ep.get("name") or "").strip()
        if not name:
            continue
        out[name] = {
            "per_1k_input": clamp_price(ep.get("price_per_1k_input")),
            "per_1k_output": clamp_price(ep.get("price_per_1k_output")),
        }
    return out


def aggregate(recs: list[dict], price_map: dict | None = None) -> dict:
    """把 usage.jsonl 的记录聚合成展示结构。

    `price_map`: `{endpoint名: {"per_1k_input": x, "per_1k_output": y}}`
    （美元 / 千 token）。返回结构：
    - `totals`: 总调用 / 失败 / prompt / completion / 估算成本
    - `by_feature` / `by_model`: `{name: {calls, failed, tokens, cost}}`（含失败计数）
    - `by_date`: `[{date, calls, failed, tokens, cost}]` 升序（折线图直接画）
    - `by_endpoint`: `[{endpoint, calls, failed, tokens, cost}]` 按调用数降序
    """
    price_map = price_map or {}
    totals = {"calls": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    by_feature: dict = {}
    by_model: dict = {}
    by_date: dict = {}
    by_endpoint: dict = {}

    def bump(tbl: dict, key: str, rec: dict, ok: bool) -> dict:
        cell = tbl.setdefault(key, {"calls": 0, "failed": 0, "tokens": 0, "cost": 0.0})
        if ok:
            cell["calls"] += 1
        else:
            cell["failed"] += 1
        return cell

    for r in recs:
        ok = bool(r.get("ok"))
        pt = _f(r.get("prompt_tokens"))
        ct = _f(r.get("completion_tokens"))
        cost = _cost(r, price_map.get(r.get("endpoint") or "")) if ok else 0.0
        if ok:
            totals["calls"] += 1
            totals["prompt_tokens"] += pt
            totals["completion_tokens"] += ct
            totals["cost"] += cost
        else:
            totals["failed"] += 1

        fcell = bump(by_feature, r.get("feature") or "?", r, ok)
        mcell = bump(by_model, r.get("model") or "?", r, ok)
        ecell = bump(by_endpoint, r.get("endpoint") or "?", r, ok)
        if ok:
            for cell in (fcell, mcell, ecell):
                cell["tokens"] += pt + ct
                cell["cost"] += cost

        d = _date_of(r)
        if d:
            dcell = by_date.setdefault(d, {"date": d, "calls": 0, "failed": 0, "tokens": 0, "cost": 0.0})
            if ok:
                dcell["calls"] += 1
                dcell["tokens"] += pt + ct
                dcell["cost"] += cost
            else:
                dcell["failed"] += 1

    return {
        "totals": totals,
        "by_feature": by_feature,
        "by_model": by_model,
        "by_date": sorted(by_date.values(), key=lambda x: x["date"]),
        "by_endpoint": [{"endpoint": k, **v} for k, v in
                        sorted(by_endpoint.items(), key=lambda kv: (-kv[1]["calls"], kv[0]))],
    }


def budget_status(recs: list[dict], price_map: dict | None = None,
                  budget_limit: float | None = None, now: datetime | None = None) -> dict | None:
    """自然月预算使用情况；预算未设（None）或 <= 0 返回 None（前端不做阈值告警）。

    返回 `{"limit", "month", "used", "pct"}`：`pct` 是已用占预算的百分比（1 位小数），
    前端按 80% 黄 / 100% 红给告警。`now` 参数用于测试注入固定时刻。
    """
    if budget_limit is None or _f(budget_limit) <= 0:
        return None
    now = now or datetime.now()
    month = now.strftime("%Y-%m")
    used = 0.0
    for r in recs:
        if r.get("ok") and _date_of(r).startswith(month):
            used += _cost(r, price_map.get(r.get("endpoint") or "")) if price_map else 0.0
    limit = _f(budget_limit)
    return {
        "limit": limit,
        "month": month,
        "used": round(used, 4),
        "pct": round(used / limit * 100, 1) if limit > 0 else 0.0,
    }