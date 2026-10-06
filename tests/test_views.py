# -*- coding: utf-8 -*-
"""持久化自定义视图（Plane Views 轻量版）：保存 → 列表 → 切换条件 → 删除

覆盖：
- 字段进出（add/update/rename/delete、upsert-by-name 的 `updated: true` 语义）
- `_clean_views`：非 dict 丢弃、缺 id/name 丢弃、id 与 name 双重去重（首现者胜）、
  filter 只放行状态枚举、q 截断、总量封顶 `MAX_SAVED_VIEWS`
- 上限：超过 `MAX_SAVED_VIEWS` 粒新视图被 400 拒；同名更新不受上限影响
- 导出/导入/恢复跟随：老导出没有 `views` 键 = 不动现有视图（与 memories 同语义）
"""
from __future__ import annotations

import json

import app.main as kanban


def _save_view(api, name, filter="进行中", q=""):
    return api.post("/api/views", {"name": name, "filter": filter, "q": q})


# ---------------- 字段进出 ----------------

def test_views_roundtrip(app_client):
    """保存一条视图，列表读回一致；PATCH 改条件；DELETE 后消失。"""
    st, view = _save_view(app_client, "进行中交接", filter="进行中", q="交接")
    assert st == 201
    assert view["id"] and view["name"] == "进行中交接"
    assert view["filter"] == "进行中" and view["q"] == "交接"
    # 新视图不带 updated 标记（只有 upsert 命中才带）
    assert "updated" not in view

    _, body = app_client.get("/api/views")
    assert len(body["views"]) == 1 and body["views"][0]["id"] == view["id"]

    st2, upd = app_client.request("PATCH", f"/api/views/{view['id']}",
                                  {"name": "改名了", "filter": "已完成", "q": "上线"})
    assert st2 == 200 and upd["name"] == "改名了" and upd["filter"] == "已完成"
    _, body2 = app_client.get("/api/views")
    assert body2["views"][0]["name"] == "改名了" and body2["views"][0]["q"] == "上线"

    st3, _ = app_client.delete(f"/api/views/{view['id']}")
    assert st3 == 200
    _, body3 = app_client.get("/api/views")
    assert body3["views"] == []


def test_view_upsert_by_same_name(app_client):
    """同名再存 = 更新原视图并返回原 id + updated: true，不产生重复条目。"""
    _, first = _save_view(app_client, "唯一视图", filter="规划中", q="甲")
    st, second = _save_view(app_client, "唯一视图", filter="已暂停", q="乙")
    assert st == 201
    assert second["id"] == first["id"]
    assert second["updated"] is True
    assert second["filter"] == "已暂停" and second["q"] == "乙"

    _, body = app_client.get("/api/views")
    assert len(body["views"]) == 1           # 没有第二条
    assert body["views"][0]["q"] == "乙"     # 字段被更新


def test_view_rename_to_taken_name_409(app_client):
    """PATCH 把名字改成另一个视图已占的名字 = 撞名 409（菜单里两个下拉项不能长得一样）。"""
    _, a = _save_view(app_client, "视图甲")
    _, b = _save_view(app_client, "视图乙")
    st, body = app_client.request("PATCH", f"/api/views/{b['id']}",
                                  {"name": "视图甲", "filter": "全部", "q": ""})
    assert st == 409
    # 改回自己的名字（内容变化）允许
    st2, _ = app_client.request("PATCH", f"/api/views/{b['id']}",
                                {"name": "视图乙", "filter": "进行中", "q": "新词"})
    assert st2 == 200


def test_view_delete_missing_404(app_client):
    st, _ = app_client.delete("/api/views/no-such-id")
    assert st == 404


# ---------------- 上限与清洗 ----------------

def test_view_upper_limit_enforced(app_client):
    """超过 MAX_SAVED_VIEWS 个新视图被 400 拒；同名更新不受上限影响。"""
    limit = kanban.MAX_SAVED_VIEWS
    for i in range(limit):
        assert _save_view(app_client, f"视图{i:02d}")[0] == 201
    st, body = _save_view(app_client, "第31个")
    assert st == 400 and "最多保存" in str(body)
    # 同名更新仍然放行（不改条数）
    st2, upd = _save_view(app_client, "视图00", filter="已完成", q="换条件")
    assert st2 == 201 and upd["updated"] is True
    _, listed = app_client.get("/api/views")
    assert len(listed["views"]) == limit


def test_clean_views_drops_non_dict_and_empty_ids():
    """非 dict、缺 id/name 的整条丢弃；id 与 name 双重去重（首现者胜）；filter 归一；截断。"""
    out = kanban._clean_views([
        "not-a-dict", 42,
        {"id": "", "name": "缺id"},
        {"id": "ok1", "name": ""},                  # 缺 name
        {"id": "ok1", "name": "重复id"},            # 首现，保留
        {"id": "ok1", "name": "合法但id重了"},        # id 撞 → 丢弃
        {"id": "ok2", "name": "同名甲"},
        {"id": "ok3", "name": "同名甲"},             # name 撞 → 丢弃
        {"id": "ok4", "name": "非法状态", "filter": "正在进行", "q": "吧" * 300},
    ])
    assert [v["name"] for v in out] == ["重复id", "同名甲", "非法状态"]
    assert out[2]["filter"] == "全部"               # 非法 filter 归一
    assert len(out[2]["q"]) == kanban.MAX_VIEW_QUERY  # 截断
    assert all(v["id"] and v["name"] for v in out)
    # 合法状态原样放行
    f = kanban._clean_views([{"id": "x1", "name": "进行中", "filter": "进行中", "q": ""}])
    assert f[0]["filter"] == "进行中"


def test_clean_views_caps_at_max():
    """超过 MAX_SAVED_VIEWS 的视图只留前 N 条（同一防线：追加型存储有上限）。"""
    rows = [{"id": f"v{i}", "name": f"视图{i}", "filter": "全部", "q": ""}
            for i in range(kanban.MAX_SAVED_VIEWS + 10)]
    out = kanban._clean_views(rows)
    assert len(out) == kanban.MAX_SAVED_VIEWS


# ---------------- 导出 / 导入 / 恢复 跟随 ----------------

def test_views_travel_with_export_import_and_restore(app_client):
    """视图是全局界面配置：导出带走、导入带回来、恢复回到备份时刻。

    老导出（无 views 键）导入时**不动**现有视图 —— 与 memories 同语义：
    回滚旧数据不能把攒下来的过滤器一起清掉。
    """
    _, view = _save_view(app_client, "交接视图", filter="进行中", q="交接")

    status, exported = app_client.get("/api/data/export")
    assert status == 200 and exported["views"][0]["name"] == "交接视图"
    legacy = {"app": "ai-kanban", "schema_version": 1,
              "projects": exported["projects"], "memories": []}

    # 老导出（无 views 键）导入：现有视图原样保留
    st, _ = app_client.post("/api/data/import", {"content": json.dumps(legacy, ensure_ascii=False)})
    assert st == 200
    _, body = app_client.get("/api/views")
    assert len(body["views"]) == 1 and body["views"][0]["name"] == "交接视图"

    # 备份在视图存在时拍 → 恢复后视图回到备份时刻（备份后新建的视图被丢掉）
    st, _ = app_client.post("/api/backup")
    assert st == 200
    _, bv = _save_view(app_client, "备份后才存", filter="规划中", q="临时")
    assert bv["id"]
    _, backups = app_client.get("/api/backups")
    assert backups["backups"]
    src = backups["backups"][0]["name"]
    st2, _ = app_client.post("/api/restore", {"source": src})
    assert st2 == 200
    _, after = app_client.get("/api/views")
    names = [v["name"] for v in after["views"]]
    assert "交接视图" in names and "备份后才存" not in names