# -*- coding: utf-8 -*-
"""监管报告的落盘、裁剪、历史与趋势。

**从 `app/main.py` 拆出（2026-10-06，同 handoff_md/agentsmd 的依赖注入法）**：
本模块只管「报告文件」这一层 —— 落盘、裁剪、按 id 找历史、对比趋势。
编排（`_run_analyze`：取看板数据 → 构造简报 → llm.chat → 落盘）留在 main，
因为它的两段读盘（`get_projects_data` / `_projects_brief`）是 main 的口径；
`_build_analyze_prompt` 是纯函数，随报告一起搬到这里。

⚠️ 有状态名字的 monkeypatch 点**随迁到这里**：`MAX_REPORTS` 请打在 `app.aireports` 上
（main 里是转发别名）。`reports_dir` / `_save_report` / `_attach_trends` 这些**纯转发名**
打在 main 上仍有效（main 的调用点解析的是导入后的名字）。

报告上限（MAX_REPORTS）挂在「约定 11：追加型存储必须有上限」这一条 ——
开了定时监管后每天一份，reports/ 只写不删就是无界增长。
"""
from __future__ import annotations

import json
from pathlib import Path

from .paths import data_dir


def reports_dir() -> Path:
    d = data_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


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


def build_analyze_prompt(projects: list[dict]) -> list[dict]:
    """监管分析的两段式提示词（纯函数：projects 由调用方先用 `_projects_brief` 加工好）"""
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
