# -*- coding: utf-8 -*-
"""LLM 网关单测：多 Key 轮询、端点降级、全失败聚合报错、JSON 容错提取、配置兼容

全部走本机假端点（tests/llm_stub.py），不需要任何真实配额。
"""
from __future__ import annotations

import json

import pytest

from app import llm
from llm_stub import StubLLM

MSGS = [{"role": "user", "content": "hi"}]


@pytest.fixture
def stub():
    s = StubLLM().start()
    yield s
    s.stop()


def cfg_with(*endpoints, active: str | None = None) -> dict:
    return {"ai": {"endpoints": list(endpoints), "active_model": active or ""}}


def test_single_endpoint_success(stub):
    stub.push_text("你好")
    text, name = llm.chat(cfg_with(stub.endpoint("甲")), MSGS)
    assert text == "你好" and name == "甲"
    assert stub.calls and stub.calls[0]["path"].endswith("/chat/completions")


def test_429_on_first_key_rotates_to_second(stub):
    """同一端点内多 Key 轮询：第一个限流就用第二个"""
    stub.status_for_key["sk-bad-0000000000"] = 429
    stub.push_text("第二个 Key 成功")
    text, name = llm.chat(cfg_with(stub.endpoint("甲", keys=["sk-bad-0000000000", "sk-good-000000001"])), MSGS)
    assert text == "第二个 Key 成功" and name == "甲"
    assert len(stub.calls) == 2


def test_endpoint_failover_on_5xx(stub):
    """第一个端点整体挂掉时，自动切到第二个端点"""
    stub.push_status(500)
    stub.push_text("备用端点成功")
    cfg = cfg_with(stub.endpoint("甲"), stub.endpoint("乙"))
    text, name = llm.chat(cfg, MSGS)
    assert text == "备用端点成功" and name == "乙"


def test_all_endpoints_fail_raises_with_aggregated_errors(stub):
    stub.set_default_status(503)
    with pytest.raises(llm.LLMError) as e:
        llm.chat(cfg_with(stub.endpoint("甲"), stub.endpoint("乙")), MSGS)
    msg = str(e.value)
    assert "所有 AI 端点均调用失败" in msg
    assert "甲" in msg and "乙" in msg


def test_endpoint_without_keys_is_skipped(stub):
    """没配 Key 的端点应被跳过，不能发出空鉴权请求"""
    stub.push_text("有 Key 的那个")
    cfg = cfg_with(stub.endpoint("空端点", keys=[]), stub.endpoint("甲"))
    text, name = llm.chat(cfg, MSGS)
    assert text == "有 Key 的那个" and name == "甲"
    assert len(stub.calls) == 1, "空 Key 端点不应该发出请求"


def test_no_enabled_endpoint_raises():
    cfg = {"ai": {"endpoints": [{"name": "停用", "base_url": "http://127.0.0.1:1/v1",
                                 "api_keys": ["sk-x"], "models": ["m"], "enabled": False}],
                  "active_model": ""}}
    with pytest.raises(llm.LLMError, match="没有可用的 AI 端点"):
        llm.chat(cfg, MSGS)


def test_active_model_endpoint_is_preferred(stub):
    """active_model 属于哪个端点，就先打哪个"""
    stub.push_text("来自乙")
    a = stub.endpoint("甲", models=["model-a"])
    b = stub.endpoint("乙", models=["model-b"])
    text, name = llm.chat(cfg_with(a, b, active="model-b"), MSGS)
    assert name == "乙"


def test_model_falls_back_to_first_when_active_missing(stub):
    stub.push_text("ok")
    llm.chat(cfg_with(stub.endpoint("甲", models=["only-model"]), active="不存在的模型"), MSGS)
    assert stub.calls[0]["payload"]["model"] == "only-model"


def test_test_connection_reports_failure(stub):
    stub.set_default_status(500)
    res = llm.test_connection(cfg_with(stub.endpoint("甲")))
    assert res["ok"] is False and "error" in res


def test_test_connection_reports_success(stub):
    stub.push_text("OK")
    res = llm.test_connection(cfg_with(stub.endpoint("甲")))
    assert res["ok"] is True and res["endpoint"] == "甲" and res["reply"] == "OK"


# ---- _extract_json 容错 ----

@pytest.mark.parametrize("raw", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    '好的，结果如下：{"a": 1}\n以上。',
])
def test_extract_json_variants(raw):
    from app.main import _extract_json
    assert _extract_json(raw) == {"a": 1}


def test_extract_json_array():
    from app.main import _extract_json
    assert _extract_json('前缀 [1, 2, 3] 后缀') == [1, 2, 3]


@pytest.mark.parametrize("raw", ["", "完全没有 JSON", "{坏掉的"])
def test_extract_json_gives_up_cleanly(raw):
    from app.main import _extract_json
    with pytest.raises(ValueError):
        _extract_json(raw)


# ---- 掩码与配置兼容 ----

def test_mask_key_short_and_long():
    assert llm.mask_key("") == ""
    assert llm.mask_key("short") == "sho****"
    assert llm.mask_key("sk-1234567890abcdef") == "sk-123****cdef"


def test_mask_config_adds_key_count():
    cfg = {"ai": {"endpoints": [{"name": "甲", "api_keys": ["sk-1234567890abcdef"]}]}}
    out = llm.mask_config(cfg)
    assert out["ai"]["endpoints"][0]["key_count"] == 1
    assert "****" in out["ai"]["endpoints"][0]["api_keys"][0]
    # 原配置不能被就地改坏（deepcopy 语义）
    assert cfg["ai"]["endpoints"][0]["api_keys"][0] == "sk-1234567890abcdef"


def test_load_config_fills_defaults_for_legacy_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_DATA_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"ai": {"active_model": "x"}}), encoding="utf-8")
    cfg = llm.load_config()
    assert cfg["ai"]["active_model"] == "x"
    assert cfg["ai"]["endpoints"], "旧配置缺少 endpoints 时应补默认端点"
    assert cfg["ai"]["schedule"]["frequency"] == "daily"


def test_load_config_survives_broken_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_DATA_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text("{ 这不是 JSON", encoding="utf-8")
    cfg = llm.load_config()
    assert cfg["ai"]["endpoints"] == llm.DEFAULT_ENDPOINTS


def test_save_config_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_DATA_DIR", str(tmp_path))
    llm.save_config({"ai": {"endpoints": [], "active_model": "m", "schedule": {}}})
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["ai"]["active_model"] == "m"
    assert not (tmp_path / "config.tmp").exists(), "临时文件应已被 os.replace 掉"


def test_update_config_serializes_read_modify_write(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_DATA_DIR", str(tmp_path))
    llm.save_config({"ai": {"endpoints": [], "active_model": "m", "schedule": {"enabled": False}}})

    def bump(cfg):
        cfg["ai"]["schedule"]["enabled"] = True
        cfg["ai"]["active_model"] = "m2"

    llm.update_config(bump)
    got = llm.load_config()
    assert got["ai"]["schedule"]["enabled"] is True and got["ai"]["active_model"] == "m2"


def test_load_config_recovers_from_last_good(tmp_path, monkeypatch):
    """config.json 坏了，不能静默把 Key 清空：先留 .bak，再从 last-good 恢复。"""
    monkeypatch.setenv("KANBAN_DATA_DIR", str(tmp_path))
    llm.save_config({"ai": {"endpoints": [], "active_model": "m", "schedule": {"enabled": False}}})
    assert llm.config_last_good_file().exists()
    (tmp_path / "config.json").write_text("{ 不是 JSON", encoding="utf-8")

    got = llm.load_config()
    assert got["ai"]["active_model"] == "m"
    assert (tmp_path / "config.json.bak").exists(), "损坏原文没有留证"
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["ai"]["active_model"] == "m"
