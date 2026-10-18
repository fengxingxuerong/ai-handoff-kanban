# -*- coding: utf-8 -*-
"""数据恢复：从 .bak 或手动快照还原（损坏自愈之后还能回去）

场景测试 B14 暴露的问题：数据文件损坏时会自愈成空看板 + .bak，但**没有恢复入口**，
真出事了只能手动改文件名。这里验证恢复链路：来源白名单、可用性预览、恢复前自动另存、
坏来源不破坏现有数据。
"""
from __future__ import annotations

import json

import pytest


def seed_projects(api, *names: str) -> None:
    for n in names:
        api.create_project(n)


def test_recoverable_empty_when_nothing_to_restore(app_client):
    status, body = app_client.get("/api/recoverable")
    assert status == 200
    assert body["recoverable"] == []
    assert body["current"]["usable"] is True or body["current"]["project_count"] == 0


def test_corrupt_file_auto_recovers_from_last_good(app_client):
    """核心链路：文件损坏时，自动从「最近一次正常保存」救回来，而不是清零"""
    api = app_client
    seed_projects(api, "项目一", "项目二")
    target = api.data_dir / "projects.json"
    good = target.read_text(encoding="utf-8")

    target.write_text(good[: len(good) // 2], encoding="utf-8")   # 模拟被同步盘/杀软截断
    status, data = api.get("/api/projects")

    assert status == 200
    assert data["data_recovered"] is True
    assert data["recovered_from"] == "last-good"
    assert sorted(p["name"] for p in data["projects"]) == ["项目一", "项目二"], "数据没被救回来"
    assert (api.data_dir / "projects.json.bak").exists(), "损坏文件应留档取证"

    # 提示只上报一次
    _, again = api.get("/api/projects")
    assert again["data_recovered"] is False


def test_corruption_without_last_good_resets_to_empty(app_client):
    """没有可救的副本时就重置为空，并如实说明"""
    api = app_client
    seed_projects(api, "只此一份")
    api.data_dir.joinpath("projects.json.last-good").unlink()
    good = (api.data_dir / "projects.json").read_text(encoding="utf-8")
    (api.data_dir / "projects.json").write_text(good[: len(good) // 2], encoding="utf-8")

    _, data = api.get("/api/projects")
    assert data["projects"] == [] and data["data_recovered"] is True
    assert data["recovered_from"] == ""


def test_recoverable_lists_last_good_and_broken_bak(app_client):
    """两类来源要分清：能救人的 last-good，和只能取证的 .bak"""
    api = app_client
    seed_projects(api, "项目一")
    good = (api.data_dir / "projects.json").read_text(encoding="utf-8")
    (api.data_dir / "projects.json").write_text(good[: len(good) // 2], encoding="utf-8")
    api.get("/api/projects")                     # 触发一次损坏处理

    _, rec = api.get("/api/recoverable")
    by_source = {r["source"]: r for r in rec["recoverable"]}
    assert by_source["last-good"]["usable"] is True
    assert by_source["last-good"]["project_count"] == 1
    assert by_source["bak"]["usable"] is False, "损坏文件的副本本来就解析不了"

    status, res = api.post("/api/restore", {"source": "last-good"})
    assert status == 200 and res["project_count"] == 1


def test_restore_from_manual_backup(app_client):
    api = app_client
    seed_projects(api, "快照前的项目")
    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]

    # 快照之后再改数据，然后回滚
    api.create_project("快照后的项目")
    assert len(api.get("/api/projects")[1]["projects"]) == 2
    api.delete("/api/projects/nope")   # 无关调用，确保不影响

    status, res = api.post("/api/restore", {"source": folder})
    assert status == 200 and res["project_count"] == 1
    names = [p["name"] for p in api.get("/api/projects")[1]["projects"]]
    assert names == ["快照前的项目"], f"回滚结果不对：{names}"


def test_restore_makes_a_safety_copy_of_current_data(app_client):
    """恢复前必须先把当前数据另存，否则恢复错了就没退路"""
    api = app_client
    seed_projects(api, "当前数据")
    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]
    api.create_project("恢复后会消失的项目")

    assert api.post("/api/restore", {"source": folder})[0] == 200
    safety = api.data_dir / "projects.json.before-restore.bak"
    assert safety.exists(), "没有生成恢复前的安全副本"
    saved = json.loads(safety.read_text(encoding="utf-8"))
    assert any(p["name"] == "恢复后会消失的项目" for p in saved["projects"])


def test_unknown_source_is_rejected(app_client):
    assert app_client.post("/api/restore", {"source": "no-such-backup"})[0] == 404
    assert app_client.post("/api/restore", {"source": "../secret"})[0] == 404
    assert app_client.post("/api/restore", {"source": r"C:\Windows\System32"})[0] == 404


def test_unparsable_source_is_rejected_without_touching_data(app_client):
    """坏来源要报错并保留现有数据，而不是把看板清空"""
    api = app_client
    seed_projects(api, "要保住的项目")
    (api.data_dir / "projects.json.bak").write_text("{ 不是 JSON", encoding="utf-8")

    status, body = api.post("/api/restore", {"source": "bak"})
    assert status == 400
    names = [p["name"] for p in api.get("/api/projects")[1]["projects"]]
    assert names == ["要保住的项目"], "坏来源不该动现有数据"


def test_recoverable_reports_broken_source_as_unusable(app_client):
    api = app_client
    api.data_dir.mkdir(parents=True, exist_ok=True)
    (api.data_dir / "projects.json.bak").write_text("坏掉的备份", encoding="utf-8")
    _, rec = api.get("/api/recoverable")
    entry = next(r for r in rec["recoverable"] if r["source"] == "bak")
    assert entry["usable"] is False and "无法解析" in entry["reason"]


def test_restore_overwrites_instead_of_merging(app_client):
    """恢复语义是"覆盖"，不是"合并"——否则会越恢复越乱"""
    api = app_client
    api.create_project("原有项目")
    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]

    api.create_project("后来加的")
    assert api.post("/api/restore", {"source": folder})[0] == 200
    names = [p["name"] for p in api.get("/api/projects")[1]["projects"]]
    assert names == ["原有项目"], f"应完全覆盖为快照内容，实际：{names}"


def _poison_a_backup(api, folder: str, extra: dict) -> None:
    """把一条"读不回来"的项目塞进快照 —— 手改过的 projects.json、别人导出的包都会这样"""
    src = api.data_dir / "backups" / folder / "projects.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    raw["projects"].append(extra)
    src.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def test_restore_drops_entries_that_would_brick_every_read(app_client):
    """恢复以前只看"projects 是不是列表"，导入却逐条筛 id/名称。

    2026-09-23 实盘复现：从这里放进一条缺 id 的项目，恢复返回 200，
    之后 `/api/projects` 与 `/api/stats` 全部抛 KeyError（线上就是 500），
    而主文件是合法 JSON —— 损坏自愈根本不会介入，等于把"救命的退路"变成"一键变砖"。
    判据因此不是"恢复这个动作成功"，而是**恢复完还读得回来**。
    """
    api = app_client
    seed_projects(api, "干净的项目")
    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]
    _poison_a_backup(api, folder, {"name": "缺 id 的项目", "status": "规划中", "tasks": []})

    st, body = api.post("/api/restore", {"source": folder})
    assert st == 200, body
    assert body["dropped_invalid"] and "缺 id" in body["dropped_note"], body

    st2, lst = api.get("/api/projects")
    assert st2 == 200, f"恢复完读不回来了：{lst}"
    assert [p["name"] for p in lst["projects"]] == ["干净的项目"]
    assert api.get("/api/stats")[0] == 200


def test_restore_rejects_a_source_with_nothing_usable_in_it(app_client):
    """整份都是坏条目时报错并原地不动 —— 恢复一次把看板清空，比不恢复更糟。"""
    api = app_client
    seed_projects(api, "要保住的项目")
    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]
    src = api.data_dir / "backups" / folder / "projects.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    raw["projects"] = [{"name": "缺 id 的"}]
    src.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    st, body = api.post("/api/restore", {"source": folder})
    assert st == 400, body
    names = [p["name"] for p in api.get("/api/projects")[1]["projects"]]
    assert names == ["要保住的项目"], "拒绝恢复就不该动现有数据"


def test_recoverable_preview_counts_unusable_entries_beforehand(app_client):
    """按下去之前就得看见"这份里有几条拿回来会让看板报错"，不是事后才发现少了几项。"""
    api = app_client
    seed_projects(api, "甲", "乙")
    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]
    _poison_a_backup(api, folder, {"status": "规划中", "tasks": []})

    _, rec = api.get("/api/recoverable")
    entry = next(r for r in rec["recoverable"] if r["source"] == folder)
    assert entry["usable"] is True
    assert entry["unusable_count"] == 1, entry
    assert "缺 id" in entry["unusable"][0], entry


def test_pre_restore_copy_is_itself_restorable(app_client):
    """界面向用户承诺"覆盖前会自动把当前数据另存为 before-restore.bak"，
    但这个路径以前只在写入处硬编码过一次，恢复来源的白名单根本不认识它 ——
    恢复错了在界面上无路可走，而且第二次恢复会把这份也覆盖掉。
    """
    api = app_client
    seed_projects(api, "快照里的项目")
    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]
    api.create_project("恢复前这一刻的项目")
    assert api.post("/api/restore", {"source": folder})[0] == 200

    sources = {r["source"] for r in api.get("/api/recoverable")[1]["recoverable"]}
    assert "before-restore" in sources, f"承诺的退路没出现在列表里：{sources}"

    api.create_project("恢复之后又改的一笔")
    api.create_project("恢复之后又改的一笔")
    assert api.post("/api/restore", {"source": "before-restore"})[0] == 200
    names = [p["name"] for p in api.get("/api/projects")[1]["projects"]]
    # 退路要回到"按恢复之前那一刻"：那时板子上是两笔，而第 4 步恢复进来的那笔也在里面
    assert names == ["恢复前这一刻的项目", "快照里的项目"], f"退路内容不对：{names}"
    assert "恢复之后又改的一笔" not in names


def test_restore_does_not_revive_the_idempotency_window(app_client):
    """数据层约定 13：`_client_refs` 是本进程"刚才那次重试"的窗口，导出/导入都不带它。

    恢复是同一类时间机器，而 `backup_*/projects.json` 里原样躺着当时整张键表 ——
    `data.clear(); data.update(restored)` 会把它一起复活，之后拿同一个键建**新**东西
    会被当成重放：返回一个旧 id、一次落盘都不做，用户以为建好了。
    """
    api = app_client
    _, created = api.post("/api/projects?client_ref=win-1", {"name": "带键建的项目"})
    first_id = created["id"]
    # 先确认键在这一次里是真生效的（否则后面那条"重放了"也说明不了什么）
    _, replay = api.post("/api/projects?client_ref=win-1", {"name": "换个正文的重复请求"})
    assert replay["id"] == first_id and replay.get("client_ref_replayed") is True, replay

    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]
    st, r = api.post("/api/restore", {"source": folder})
    assert st == 200, r
    assert r.get("idem_table_reset") == 1, f"恢复时应报清楚作废了几把键：{r}"

    _, again = api.post("/api/projects?client_ref=win-1", {"name": "恢复之后新建的同名请求"})
    assert again["id"] != first_id, "键被复活了：这次创建被判成重放，一个字都没落盘"
    assert not again.get("client_ref_replayed"), again
    names = [p["name"] for p in api.get("/api/projects")[1]["projects"]]
    assert "恢复之后新建的项目" not in names and "恢复之后新建的同名请求" in names, names


def test_import_says_what_it_dropped(app_client):
    """导入可以丢掉读不回来的条目，但不能一声不吭 ——
    "少两个项目"和"一个没少"在界面上以前长得一模一样。"""
    api = app_client
    payload = {"projects": [
        {"id": "keep00000001", "name": "能读回来的", "status": "规划中", "progress": 0,
         "priority": "中", "tasks": []},
        {"name": "读不回来的", "status": "规划中", "tasks": []},
    ]}
    st, body = api.post("/api/data/import", {"content": json.dumps(payload)})
    assert st == 200, body
    assert body["imported"] == 1
    assert body["dropped_invalid"] and "缺 id" in body["dropped_note"], body


def test_backup_carries_repo_watch_state_and_restore_rolls_it_back(app_client):
    """雷达基线（repo_watch.json）此前不在备份里：恢复回滚的是那一刻的项目数据，
    而基线只剩"恢复当时"的现场，两者不是同一时刻的对子。现在同拍同回滚。"""
    api = app_client
    seed_projects(api, "带仓库的项目")
    baseline = {"p1": {"last_seen_signature": "abcd1234", "last_seen_at": "2026-10-18 10:00:00"},
                "bad": "不是字典的条目"}
    (api.data_dir / "repo_watch.json").write_text(json.dumps(baseline), encoding="utf-8")

    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]
    assert (api.data_dir / "backups" / folder / "repo_watch.json").exists(), "备份没有带上雷达基线"

    # 快照之后基线又动了（比如新确认了一次），回滚要把它一起带回去
    (api.data_dir / "repo_watch.json").write_text(
        json.dumps({"p2": {"last_seen_signature": "later"}}), encoding="utf-8")
    st, body = api.post("/api/restore", {"source": folder})
    assert st == 200, body
    assert body.get("watch_state_count") == 1, body
    now = json.loads((api.data_dir / "repo_watch.json").read_text(encoding="utf-8"))
    assert now == {"p1": baseline["p1"]}, f"基线没有随备份一起回滚，或坏形状没被滤掉：{now}"


def test_restore_from_old_backup_without_watch_state_keeps_current(app_client):
    """"来源里没有这份文件 = 不动现状"（与老导出没有 memories 键的语义一致）：
    老备份没有 repo_watch.json 时不许清空现有基线；
    last-good / bak / before-restore 与线上同目录，同样不许"把现值盖回现值"并报一个假回滚。"""
    api = app_client
    seed_projects(api, "甲")
    (api.data_dir / "repo_watch.json").write_text(
        json.dumps({"old": {"last_seen_signature": "x"}}), encoding="utf-8")
    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]
    (api.data_dir / "backups" / folder / "repo_watch.json").unlink()  # 老版本备份没有这份文件

    current = {"keep": {"last_seen_signature": "keepme"}}
    (api.data_dir / "repo_watch.json").write_text(json.dumps(current), encoding="utf-8")

    st, body = api.post("/api/restore", {"source": folder})
    assert st == 200, body
    assert "watch_state_count" not in body, body
    now = json.loads((api.data_dir / "repo_watch.json").read_text(encoding="utf-8"))
    assert now == current, "老备份没有这份文件时不该动现有基线"

    st2, body2 = api.post("/api/restore", {"source": "last-good"})
    assert st2 == 200, body2
    assert "watch_state_count" not in body2, body2
    now2 = json.loads((api.data_dir / "repo_watch.json").read_text(encoding="utf-8"))
    assert now2 == current, "同目录来源不该碰基线"


def test_restore_from_a_corrupt_watch_state_keeps_current(app_client):
    """备份里的基线坏了（手改过/被截断）：不许拿它覆盖好状态，更不许当"空状态"清空。"""
    api = app_client
    seed_projects(api, "甲")
    assert api.post("/api/backup")[0] == 200
    folder = api.get("/api/backups")[1]["backups"][0]["name"]
    (api.data_dir / "backups" / folder / "repo_watch.json").write_text("{ 不是 JSON", encoding="utf-8")

    current = {"keep": {"last_seen_signature": "safe"}}
    (api.data_dir / "repo_watch.json").write_text(json.dumps(current), encoding="utf-8")

    st, body = api.post("/api/restore", {"source": folder})
    assert st == 200, body
    assert "watch_state_count" not in body, body
    now = json.loads((api.data_dir / "repo_watch.json").read_text(encoding="utf-8"))
    assert now == current, "坏基线不该覆盖现有状态"
