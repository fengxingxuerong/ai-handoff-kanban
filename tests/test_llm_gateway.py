# -*- coding: utf-8 -*-
"""LLM 网关单测：多 Key 轮询、端点降级、全失败聚合报错、JSON 容错提取、配置兼容

全部走本机假端点（tests/llm_stub.py），不需要任何真实配额。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


@pytest.fixture
def no_sleep(monkeypatch):
    """空回复要退避 0.4s 再试下一个端点；用例里把 sleep 换掉别拖慢套件"""
    import time as real_time

    class _NoSleep:
        def __getattr__(self, name):
            return getattr(real_time, name)

        def sleep(self, _sec: float) -> None:
            pass

    monkeypatch.setattr(llm, "time", _NoSleep())


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


def test_empty_content_retries_same_key_with_lifted_budget_then_falls_back(stub, no_sleep):
    """推理型模型把 max_tokens 烧在 reasoning_content 上 → HTTP 200 但 content 为空。

    2026-10-19 实盘：网关把空回复当成功直接返回，降级链根本不触发。
    2026-10-24 升级：空回复不再直接跳下一个端点，而是**同 Key 先用 4 倍预算重试**
    （deepseek-v4-flash 实测 4 连败的根因就是 3000 预算被推理烧光，而下一个端点
    可能更糟——当晚 NVIDIA 持续超时）。重试仍空才算失败、才走降级链。
    """
    stub.push_text("")              # 甲第一次：200 但正文为空（推理烧光）
    stub.push_text("")              # 甲第二次（4 倍预算）：仍为空
    stub.push_text("备用端点成功")    # 乙
    text, name = llm.chat(cfg_with(stub.endpoint("甲"), stub.endpoint("乙")), MSGS)
    assert text == "备用端点成功" and name == "乙"
    assert stub.calls[1]["payload"]["max_tokens"] == llm.MAX_TOKENS * 4, (
        "同 Key 的第二次尝试必须带着提升后的预算，否则等于原样重放")
    assert stub.calls[2]["payload"]["max_tokens"] == llm.MAX_TOKENS, (
        "换到乙端点要回到正常预算，别把提升值带过去")


def test_blank_only_content_is_also_a_failure(stub, no_sleep):
    """纯空白也是空：`.strip()` 之后判断，别让一个换行当正文混过去"""
    stub.push_text("   \n  ")
    stub.push_text("   \n  ")
    stub.push_text("乙来兜底")
    text, name = llm.chat(cfg_with(stub.endpoint("甲"), stub.endpoint("乙")), MSGS)
    assert text == "乙来兜底" and name == "乙"


def test_reasoning_burn_succeeds_on_same_key_with_lifted_budget(stub, no_sleep):
    """推理烧光但 4 倍预算下模型能吐出正文 → 同 Key 直接成功，不劳降级链"""
    stub.push_text("")                        # 第一次：推理烧光
    stub.push_text("提升预算后有正文了")         # 第二次：同 Key 成功
    text, name = llm.chat(cfg_with(stub.endpoint("甲")), MSGS)
    assert text == "提升预算后有正文了" and name == "甲"
    assert len(stub.calls) == 2, "成功就不该再碰任何端点"
    assert stub.calls[1]["payload"]["max_tokens"] == llm.MAX_TOKENS * 4


def test_truncated_length_finish_reason_also_lifts_budget(stub, no_sleep):
    """finish_reason=length（内容非空但被截断）同样触发预算提升 ——
    JSON 写到一半必解析失败，这种形状此前被当成功返回给上层；监管分析报
    「无法从 LLM 输出中解析 JSON」的另一半来源就是它"""
    stub.push_text_finish('{"summary": "写到一半被截', "length")
    stub.push_text_finish('{"summary": "这次完整了"}', "stop")
    text, name = llm.chat(cfg_with(stub.endpoint("甲")), MSGS)
    assert text == '{"summary": "这次完整了"}' and name == "甲"
    assert stub.calls[1]["payload"]["max_tokens"] == llm.MAX_TOKENS * 4


def test_all_empty_replies_raise_with_reason(stub, no_sleep):
    """全部端点都空 → 聚合成一个错误，理由里说清是"返回为空"而不是笼统的失败"""
    stub.push_text("")
    stub.push_text("")
    stub.push_text("")
    stub.push_text("")
    with pytest.raises(llm.LLMError) as e:
        llm.chat(cfg_with(stub.endpoint("甲"), stub.endpoint("乙")), MSGS)
    msg = str(e.value)
    assert "返回为空" in msg and "max_tokens" in msg, msg
    assert "甲" in msg and "乙" in msg


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


# ---------------- 2026-11-03：流式网关（stream_chat）与错误分类 ----------------

class _RawHandler(BaseHTTPRequestHandler):
    """按字节回放预编排响应的最小服务器：SSE 解析逻辑需要一个真正的 text/event-stream 对端。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):        # 静音
        pass

    def do_POST(self):  # noqa: N802
        srv = self.server
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            srv.requests.append(json.loads(self.rfile.read(length).decode("utf-8")))
        body = srv.body
        self.send_response(srv.status)
        self.send_header("Content-Type", srv.ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _RawSSE:
    def __init__(self) -> None:
        self.srv = None
        self.requests: list[dict] = []

    def __call__(self, ctype: str, body: bytes, status: int = 200) -> str:
        self.stop()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _RawHandler)
        self.srv.ctype, self.srv.body, self.srv.status = ctype, body, status
        self.srv.requests = self.requests = []
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def stop(self) -> None:
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None


@pytest.fixture
def raw_sse():
    box = _RawSSE()
    yield box
    box.stop()


def _ep(name: str, base_url: str, models=("m",)) -> dict:
    return {"name": name, "base_url": base_url, "api_keys": ["sk-stub-000000000001"],
            "models": list(models), "enabled": True}


SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":1,"completion_tokens":2}}\n\n'
    'data: [DONE]\n\n'
).encode("utf-8")


def test_stream_chat_parses_real_sse(raw_sse, no_sleep):
    """真 text/event-stream 对端：逐帧拆 delta，done 帧带 finish 与端点名；请求必须带 stream=True。"""
    url = raw_sse("text/event-stream", SSE_BODY)
    events = list(llm.stream_chat(cfg_with(_ep("甲", url)), MSGS))
    assert "".join(p for k, p in events if k == "delta") == "你好"
    dones = [p for k, p in events if k == "done"]
    assert dones and dones[0]["endpoint"] == "甲" and dones[0]["finish"] == "stop", dones
    assert raw_sse.requests and raw_sse.requests[0].get("stream") is True, raw_sse.requests


def test_stream_chat_tolerates_weird_sse_framing(raw_sse, no_sleep):
    """省略 data: 前缀的行、注释行、坏 JSON 帧都要被跳过或收下，不能整个崩掉。"""
    body = (
        ': keepalive\n'
        'not-a-frame\n'
        '{"choices":[{"delta":{"content":"甲"}}]}\n'
        'data: {不是JSON}\n'
        'data: {"choices":[{"delta":{"content":"乙"}}]}\n\n'
        'data: [DONE]\n\n'
    ).encode("utf-8")
    url = raw_sse("text/event-stream", body)
    events = list(llm.stream_chat(cfg_with(_ep("甲", url)), MSGS))
    assert "".join(p for k, p in events if k == "delta") == "甲乙"


def test_stream_chat_falls_back_when_upstream_not_sse(stub, no_sleep):
    """上游不支持 SSE（回普通 JSON）时退化成一次性 delta —— 调用方语义不变。"""
    stub.push_text("整段回复")
    events = list(llm.stream_chat(cfg_with(stub.endpoint("甲")), MSGS))
    assert [p for k, p in events if k == "delta"] == ["整段回复"]
    assert [p for k, p in events if k == "done"][0]["endpoint"] == "甲"


def test_stream_chat_empty_reply_falls_to_next_endpoint(monkeypatch, no_sleep):
    """空回复与 chat 同判：记失败、换下一个端点，而不是把空串当成功吐给前端。"""
    monkeypatch.setattr(llm, "_preflight", lambda *a, **k: "")

    def fake_sse(base_url, api_key, model, messages, temperature, timeout, max_tokens=llm.MAX_TOKENS):
        if model == "空模型":
            return
        yield "delta", "有货"

    monkeypatch.setattr(llm, "_sse_post", fake_sse)
    cfg = cfg_with(_ep("甲", "http://127.0.0.1:9/v1", models=["空模型"]),
                   _ep("乙", "http://127.0.0.1:9/v1", models=["满模型"]))
    dones = [p for k, p in llm.stream_chat(cfg, MSGS) if k == "done"]
    assert dones and dones[0]["endpoint"] == "乙"


def test_stream_chat_midstream_break_raises_instead_of_swapping(monkeypatch, no_sleep):
    """已经吐过字就不再换端点 —— 换端点会把两个模型的输出拼成一段精神分裂的回答；
    宁可报「中途断开」让用户重试。"""
    monkeypatch.setattr(llm, "_preflight", lambda *a, **k: "")

    def fake_sse(base_url, api_key, model, messages, temperature, timeout, max_tokens=llm.MAX_TOKENS):
        yield "delta", "前半段"
        raise ConnectionResetError("reset by peer")

    monkeypatch.setattr(llm, "_sse_post", fake_sse)
    gen = llm.stream_chat(cfg_with(_ep("甲", "http://127.0.0.1:9/v1")), MSGS)
    assert next(gen) == ("delta", "前半段")
    with pytest.raises(llm.LLMError, match="中途断开"):
        next(gen)


def test_stream_chat_upstream_error_translates_to_chinese(stub, no_sleep):
    """上游 4xx/5xx 不再是裸的「HTTP 400」：按正文与状态码翻成用户能照做的中文。"""
    stub.push_status_body(400, "insufficient balance")
    with pytest.raises(llm.LLMError) as ei:
        list(llm.stream_chat(cfg_with(stub.endpoint("甲")), MSGS))
    assert "余额不足" in str(ei.value), ei.value


def test_chat_preflight_reports_dead_gateway_in_chinese():
    """网关没开时 5s TCP 预检当场给中文结论，不再进 60s 读超时让用户干等。"""
    cfg = cfg_with(_ep("死端点", "http://127.0.0.1:9/v1"))
    with pytest.raises(llm.LLMError) as ei:
        llm.chat(cfg, MSGS)
    assert "连不上" in str(ei.value) and "网关未启动或端口不通" in str(ei.value), ei.value


def test_preflight_direct():
    assert llm._preflight("") == ""          # 空 host 不拦（交给后续请求去报错）
    msg = llm._preflight("http://127.0.0.1:9/v1")
    assert "连不上" in msg and "127.0.0.1:9" in msg, msg


@pytest.mark.parametrize("code,body,expect", [
    (429, "", "额度用尽/限流(429)"),
    (401, "", "Key 无效(401)"),
    (403, "", "Key 无效(403)"),
    (402, "", "余额不足(402)"),
    (404, "", "模型已下线(404)"),
    (500, "", "服务错误(500)"),
    (400, '{"error":{"message":"model not found","code":"model_not_found"}}', "模型名不存在"),
    (400, '{"error":{"message":"invalid model format"}}', "模型名格式不正确"),
    (400, '{"error":{"message":"insufficient balance"}}', "余额不足"),
    (400, '{"error":{"message":"rate limit exceeded"}}', "额度用尽"),
    (422, "", "请求被上游拒绝(422)"),
    (418, "", "HTTP 418"),
])
def test_error_text_translates_upstream_errors(code, body, expect):
    """分类顺序按「用户下一步动作」排：改 Key → 充值 → 换模型 → 等一会儿。"""
    msg = llm._error_text(code, body)
    assert expect in msg, msg
