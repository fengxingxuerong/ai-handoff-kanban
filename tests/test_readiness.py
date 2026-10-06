# -*- coding: utf-8 -*-
"""交接就绪度：只能有一份口径

背景：这个规则原先写了两遍 —— `app/main.py`（界面/API 用）和 `app/handoff_page.py`
（接手页用，为避免循环导入就地重写）。两份已经漂了：接手页漏掉"占位词不算下一步"，
于是同一个项目在主界面显示 75 分、在接手页显示 100/100。
现在收口到 `app/readiness.py`，这里守住"两边算出来必须一样"。
"""
from __future__ import annotations

import pytest

from app import main as kanban
from app.handoff_page import build_handoff_page
from app.readiness import PLACEHOLDER_ACTIONS, readiness_items, readiness_line, readiness_score

FULL = {
    "id": "p1", "name": "样板项目", "handoff_note": "有交接说明", "next_action": "明天做 A",
    "background": "背景", "tasks": [{"id": "t1", "title": "A", "status": "进行中"}],
}


def test_weights_sum_to_100():
    assert sum(it["score"] for it in readiness_items({})) == sum(
        it["score"] for it in readiness_items(FULL)) == 100
    assert {it["key"] for it in readiness_items({})} == {"handoff", "next", "bg", "tasks", "active"}


def test_full_project_scores_100():
    assert readiness_score(FULL) == 100
    assert readiness_line(FULL).endswith("（齐备）")


def test_completed_tasks_still_count_as_active():
    """active 项认『进行中/已完成』两种状态（2026-09-29 补）。

    变异 readiness_active_narrowed（把已完成从推进判定里摘掉）曾存活：
    FULL 样板只用『进行中』。一个全部做完的项目理应拿满 100 分 ——
    做完比没做更"推进过"， narrowed 之后反而掉 10 分。
    """
    done_only = {**FULL, "tasks": [{"id": "t1", "title": "A", "status": "已完成"}]}
    assert readiness_score(done_only) == 100


def test_review_tasks_count_as_active():
    """active 项也认『待审查』（2026-10-01 补）：Agent 干完活等人验收算推进。

    变异 readiness_active_narrowed 现在注入的是只认『进行中』的元组，
    待审查任务若从推进判定里摘掉，一个"活全干完、在等人验收"的项目会掉 10 分。
    """
    review_only = {**FULL, "tasks": [{"id": "t1", "title": "A", "status": "待审查"}]}
    assert readiness_score(review_only) == 100


def test_empty_project_scores_zero_and_lists_all_missing():
    assert readiness_score({}) == 0
    line = readiness_line({})
    assert line.startswith("0/100") and "缺：" in line


@pytest.mark.parametrize("placeholder", PLACEHOLDER_ACTIONS)
def test_placeholder_next_action_does_not_count(placeholder):
    """写「已完结」「无」这类占位词等于没写下一步（曾经只有主界面认这条规则）"""
    p = dict(FULL, next_action=placeholder)
    assert readiness_score(p) == 75
    assert "下一步" in readiness_line(p)


def test_handoff_page_and_api_agree():
    """**这条是这次修 bug 的核心**：接手页与主界面必须给出同一个分数"""
    for p in (FULL, {"next_action": "已完结"}, {}, {"tasks": [{"status": "阻塞"}]}):
        page = build_handoff_page(p, md="", generated_at="2026-09-16 00:00")
        assert f"{readiness_score(p)}/100" in page, f"接手页分数与 API 不一致：{p}"
        assert f"{kanban._readiness_score(p)}/100" in page


def test_handoff_page_marks_unfilled_fields():
    """没填的项要在页面上直接点出来，别让人以为齐了"""
    page = build_handoff_page({"name": "空项目"}, md="", generated_at="2026-09-16 00:00")
    assert "0/100" in page and "缺：" in page
    assert "（未填写 —— 这是接手时最该补的一项）" in page
