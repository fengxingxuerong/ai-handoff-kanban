# -*- coding: utf-8 -*-
"""AI 配置读写：掩码往返不能丢真实 Key（不需要真实模型配额）

背景：界面拿到的是掩码后的 Key（sk-aaab****ffff），用户点保存时提交回来的还是掩码值。
后端必须能按前后缀把真实 Key 找回来，否则一保存就把 Key 写坏了。
这条路径在 2026-09-14 的重构里改成了 update_config()，需要回归保护。
"""
from __future__ import annotations

import json

from app.main import _period_key  # noqa: E402
from datetime import datetime


def read_cfg(api):
    return json.loads((api.data_dir / "config.json").read_text(encoding="utf-8"))


def test_keys_are_masked_on_read(seeded_server):
    status, cfg = seeded_server.get("/api/ai/config")
    assert status == 200
    eps = {ep["name"]: ep for ep in cfg["ai"]["endpoints"]}
    assert set(eps) == {"端点甲", "端点乙"}
    for ep in eps.values():
        for k in ep["api_keys"]:
            assert "****" in k, f"Key 未掩码：{k}"
        assert ep["key_count"] == len(ep["api_keys"])


def test_masked_roundtrip_keeps_real_keys(seeded_server):
    """把 GET 到的掩码配置原样 PUT 回去，真实 Key 必须原封不动"""
    _, before = seeded_server.get("/api/ai/config")
    status, after = seeded_server.put("/api/ai/config", {"endpoints": before["ai"]["endpoints"]})
    assert status == 200

    on_disk = read_cfg(seeded_server)
    eps = {ep["name"]: ep for ep in on_disk["ai"]["endpoints"]}
    assert eps["端点甲"]["api_keys"] == ["sk-aaaaaa1111111111", "sk-bbbbbb2222222222"]
    assert eps["端点乙"]["api_keys"] == ["sk-cccccc3333333333"]
    # 返回给前端的仍然是掩码
    for ep in after["ai"]["endpoints"]:
        assert all("****" in k for k in ep["api_keys"])


def test_masked_key_resolves_across_endpoints(seeded_server):
    """掩码值出现在别的端点时，应通过全局 Key 池找回真实 Key，而不是把掩码写进配置"""
    _, before = seeded_server.get("/api/ai/config")
    eps = before["ai"]["endpoints"]
    moved = json.loads(json.dumps(eps))
    moved[1]["api_keys"] = [eps[0]["api_keys"][0]]      # 把甲的掩码塞给乙
    assert seeded_server.put("/api/ai/config", {"endpoints": moved})[0] == 200

    on_disk = {ep["name"]: ep for ep in read_cfg(seeded_server)["ai"]["endpoints"]}
    assert on_disk["端点乙"]["api_keys"] == ["sk-aaaaaa1111111111"]


def test_renaming_endpoint_keeps_its_keys(seeded_server):
    """改端点名（前端仍然提交掩码）不能把 Key 弄丢，也不能写进掩码占位"""
    _, before = seeded_server.get("/api/ai/config")
    eps = json.loads(json.dumps(before["ai"]["endpoints"]))
    eps[0]["name"] = "端点甲改"
    assert seeded_server.put("/api/ai/config", {"endpoints": eps})[0] == 200

    on_disk = {ep["name"]: ep for ep in read_cfg(seeded_server)["ai"]["endpoints"]}
    assert on_disk["端点甲改"]["api_keys"] == ["sk-aaaaaa1111111111", "sk-bbbbbb2222222222"]


def test_unresolvable_mask_is_dropped(seeded_server):
    """完全对不上的掩码应被丢弃，绝不能当真实 Key 落到配置里"""
    _, before = seeded_server.get("/api/ai/config")
    eps = json.loads(json.dumps(before["ai"]["endpoints"]))
    eps[0]["api_keys"] = ["sk-zzzzzz****9999", "sk-aaaaaa1111111111"]
    assert seeded_server.put("/api/ai/config", {"endpoints": eps})[0] == 200

    keys = read_cfg(seeded_server)["ai"]["endpoints"][0]["api_keys"]
    assert keys == ["sk-aaaaaa1111111111"], f"掩码占位没被清掉：{keys}"


def test_new_plain_key_is_written(seeded_server):
    _, before = seeded_server.get("/api/ai/config")
    eps = json.loads(json.dumps(before["ai"]["endpoints"]))
    eps[0]["api_keys"].append("sk-brandnew99999999")
    assert seeded_server.put("/api/ai/config", {"endpoints": eps})[0] == 200
    on_disk = {ep["name"]: ep for ep in read_cfg(seeded_server)["ai"]["endpoints"]}
    assert "sk-brandnew99999999" in on_disk["端点甲"]["api_keys"]


def test_endpoint_fields_are_normalized(seeded_server):
    """base_url 去尾斜杠、models 去空值、enabled 转 bool"""
    status, _ = seeded_server.put("/api/ai/config", {"endpoints": [
        {"name": "  新端点  ", "base_url": "https://c.example.com/v1/", "api_keys": ["sk-x1234567890abcd"],
         "models": ["m1", "  ", "m2"], "enabled": 1},
    ]})
    assert status == 200
    ep = read_cfg(seeded_server)["ai"]["endpoints"][0]
    assert ep["name"] == "新端点"
    assert ep["base_url"] == "https://c.example.com/v1"
    assert ep["models"] == ["m1", "m2"]
    assert ep["enabled"] is True


def test_active_model_and_schedule_update(seeded_server):
    status, cfg = seeded_server.put("/api/ai/config", {
        "active_model": "model-b",
        "schedule": {"enabled": True, "frequency": "weekly", "time": "08:30", "last_run": "被忽略", "恶意字段": 1},
    })
    assert status == 200
    assert cfg["ai"]["active_model"] == "model-b"
    sched = cfg["ai"]["schedule"]
    assert sched["enabled"] is True and sched["frequency"] == "weekly" and sched["time"] == "08:30"
    assert "恶意字段" not in sched, "未知调度字段不应写进配置"


def test_period_key_daily_and_weekly():
    d = datetime(2026, 9, 14, 9, 0)          # 周一
    assert _period_key(d, "daily") == "2026-09-14"
    assert _period_key(d, "weekly") == "2026-W38"
    # 同一周内不同天应得到同一个周期键（weekly 真的按周去重）
    assert _period_key(datetime(2026, 9, 20, 9, 0), "weekly") == "2026-W38"
    assert _period_key(datetime(2026, 9, 21, 9, 0), "weekly") == "2026-W39"
