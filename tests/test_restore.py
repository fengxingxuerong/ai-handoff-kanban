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
