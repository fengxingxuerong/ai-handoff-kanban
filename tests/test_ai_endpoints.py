# -*- coding: utf-8 -*-
"""AI 接口端到端：全部打在本地假端点上（tests/llm_stub.py），进程内调用，不需要真实配额。

覆盖：连接测试 / 监管分析（含报告落盘与趋势）/ 会话提炼 / 自然语言指令 / 交接生成 /
AGENTS.md（AI 版与本地兜底）/ 建议落地（含非法字段与非法 status 收口）。
失败路径：模型输出非 JSON、HTTP 响应体本身就不是 JSON、端点全挂、项目不存在、入参过短。
"""
from __future__ import annotations

import json

import pytest
from conftest import Api
from llm_stub import StubLLM


@pytest.fixture
def ai(tmp_path, monkeypatch):
    stub = StubLLM().start()
    data_dir = tmp_path / "kanban-ai"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "ai": {
            "endpoints": [stub.endpoint("桩端点")],
            "active_model": "stub-model",
            "schedule": {"enabled": False, "frequency": "daily", "time": "09:00", "last_run": ""},
        }
    }
    (data_dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("KANBAN_DATA_DIR", str(data_dir))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        api = Api(client=c, data_dir=data_dir)
        api.stub = stub
        yield api
    stub.stop()


def first_project(api) -> dict:
    return api.get("/api/projects")[1]["projects"][0]


# ---------------- 正常路径 ----------------

def test_connection_ok(ai):
    status, body = ai.post("/api/ai/test")
    assert status == 200 and body["ok"] is True


def test_analyze_saves_report_and_feeds_trends(ai):
    pid = first_project(ai)["id"]
    ai.stub.push_json({
        "summary": "整体健康",
        "insights": [{"id": pid, "name": "示例", "health": 88, "reason": "节奏正常",
                      "risks": ["人手不足"],
                      "suggestions": [{"text": "补文档", "apply_to": "next_action"}]}],
        "global_suggestions": ["统一收口"],
    })
    status, report = ai.post("/api/ai/analyze")
    assert status == 200 and report["id"] and report["insights"][0]["health"] == 88
    assert report["trigger"] == "manual" and report["endpoint"] == "桩端点"

    _, listing = ai.get("/api/ai/reports")
    assert any(r["id"] == report["id"] and r["insight_count"] == 1 for r in listing["reports"])

    status, detail = ai.get(f"/api/ai/reports/{report['id']}")
    assert status == 200 and detail["summary"] == "整体健康"

    _, trends = ai.get("/api/ai/trends")
    series = {s["id"]: s for s in trends["series"]}
    assert pid in series and series[pid]["points"][0]["health"] == 88


def test_report_detail_404(ai):
    assert ai.get("/api/ai/reports/no-such-report")[0] == 404


def test_reports_list_skips_broken_files(ai):
    """历史报告文件损坏时列表不能整体失败"""
    reports_dir = ai.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "broken.json").write_text("{ 不是 JSON", encoding="utf-8")
    status, listing = ai.get("/api/ai/reports")
    assert status == 200 and isinstance(listing["reports"], list)


def test_summarize_extracts_tasks(ai):
    pid = first_project(ai)["id"]
    ai.stub.push_json({
        "summary": "聊了部署",
        "new_tasks": [{"title": "写部署文档", "desc": "含回滚步骤", "priority": "高", "status": "待办"}],
        "next_action": "先补部署文档",
        "handoff_note_updates": "部署走 CI",
    })
    status, body = ai.post("/api/ai/summarize", {"pid": pid, "text": "x" * 20})
    assert status == 200 and body["parsed"]["new_tasks"][0]["title"] == "写部署文档"


def test_command_parses_actions(ai):
    ai.stub.push_json({"actions": [{"type": "create_task", "project_id": "x", "title": "加个任务"}]})
    status, body = ai.post("/api/ai/command", {"text": "给第一个项目加个任务"})
    assert status == 200 and body["parsed"]["actions"][0]["title"] == "加个任务"


def test_handoff_generation(ai):
    pid = first_project(ai)["id"]
    ai.stub.push_text("# 项目概况\n\n这里是交接说明正文，写长一点以通过长度校验。" * 3)
    status, body = ai.post(f"/api/ai/handoff/{pid}", {"mode": "generate"})
    assert status == 200 and "项目概况" in body["text"] and body["endpoint"] == "桩端点"


def test_handoff_polish_mode_includes_existing_note(ai):
    pid = first_project(ai)["id"]
    ai.put(f"/api/projects/{pid}", {
        "name": "示例项目 A · 后台系统", "status": "进行中", "progress": 65, "priority": "高",
        "handoff_note": "旧的交接说明文本", "next_action": "继续",
    })
    ai.stub.push_text("润色后的交接说明" * 10)
    assert ai.post(f"/api/ai/handoff/{pid}", {"mode": "polish"})[0] == 200
    prompt = json.dumps(ai.stub.calls[-1]["payload"], ensure_ascii=False)
    assert "旧的交接说明文本" in prompt


def test_agentsmd_from_ai(ai):
    pid = first_project(ai)["id"]
    ai.stub.push_text("# AGENTS.md — 示例\n\n## 构建\n\npytest\n")
    status, body = ai.post(f"/api/export/agentsmd/{pid}")
    assert status == 200 and body["source"] == "ai" and "AGENTS.md" in body["text"]


def test_agentsmd_too_short_output_falls_back_to_local(ai):
    """模型只吐了几个字符时应判定不可用，退回本地规则版"""
    pid = first_project(ai)["id"]
    ai.stub.push_text("太短")
    status, body = ai.post(f"/api/export/agentsmd/{pid}")
    assert status == 200 and body["source"] == "local"


def test_apply_suggestions_writes_fields(ai):
    pid = first_project(ai)["id"]
    status, body = ai.post("/api/ai/apply-suggestions", [
        {"project_id": pid, "field": "next_action", "value": "来自建议的下一步"},
        {"project_id": pid, "field": "非法字段", "value": "不该被写入"},
        {"project_id": "不存在的项目", "field": "next_action", "value": "也不该"},
    ])
    assert status == 200 and body["applied"] == 1
    _, detail = ai.get(f"/api/projects/{pid}")
    assert detail["next_action"] == "来自建议的下一步" and "非法字段" not in detail


def test_apply_suggestions_sanitizes_illegal_status(ai):
    """AI 建议写进 status 时也要被白名单收口（否则会污染前端 class 与文本）"""
    pid = first_project(ai)["id"]
    ai.post("/api/ai/apply-suggestions", [
        {"project_id": pid, "field": "status", "value": "<script>alert(1)</script>"},
    ])
    _, detail = ai.get(f"/api/projects/{pid}")
    assert detail["status"] == "规划中"


# ---------------- 失败路径 ----------------

def test_analyze_with_garbage_content_returns_500(ai):
    """HTTP 正常但模型内容不是 JSON —— 解析失败，属于 500"""
    ai.stub.push_text("这不是 JSON，模型跑偏了")
    status, body = ai.post("/api/ai/analyze")
    assert status == 500, body


def test_malformed_http_body_triggers_endpoint_failover(ai):
    """响应体本身就不是合法 JSON —— 视为该端点不可用，切下一个（这里只有一个，故 502）"""
    ai.stub.push_raw("{ 坏掉的响应体")
    status, body = ai.post("/api/ai/analyze")
    assert status == 502 and "AI 调用失败" in str(body)


def test_analyze_with_all_endpoints_down_returns_502(ai):
    ai.stub.set_default_status(503)
    status, body = ai.post("/api/ai/analyze")
    assert status == 502 and "AI 调用失败" in str(body)


def test_summarize_with_all_endpoints_down_returns_502(ai):
    ai.stub.set_default_status(500)
    pid = first_project(ai)["id"]
    assert ai.post("/api/ai/summarize", {"pid": pid, "text": "x" * 20})[0] == 502


def test_command_with_all_endpoints_down_returns_502(ai):
    ai.stub.set_default_status(500)
    assert ai.post("/api/ai/command", {"text": "加个任务"})[0] == 502


def test_handoff_with_all_endpoints_down_returns_502(ai):
    ai.stub.set_default_status(500)
    pid = first_project(ai)["id"]
    assert ai.post(f"/api/ai/handoff/{pid}", {"mode": "generate"})[0] == 502


def test_agentsmd_falls_back_to_local_when_llm_down(ai):
    """LLM 全挂时必须退回本地规则版，而不是报错给用户"""
    ai.stub.set_default_status(500)
    pid = first_project(ai)["id"]
    status, body = ai.post(f"/api/export/agentsmd/{pid}")
    assert status == 200 and body["source"] == "local"
    assert "AGENTS.md" in body["text"] and body["endpoint"] == ""


def test_unknown_project_paths_404(ai):
    assert ai.post("/api/ai/handoff/nope", {"mode": "generate"})[0] == 404
    assert ai.post("/api/export/agentsmd/nope")[0] == 404
    assert ai.post("/api/ai/summarize", {"pid": "nope", "text": "x" * 20})[0] == 404


def test_summarize_rejects_too_short_text(ai):
    pid = first_project(ai)["id"]
    assert ai.post("/api/ai/summarize", {"pid": pid, "text": "短"})[0] == 422


def test_command_rejects_empty_text(ai):
    assert ai.post("/api/ai/command", {"text": ""})[0] == 422


def test_ai_config_endpoint_roundtrip(ai):
    """走一遍设置界面的读写：拿掩码配置 → 原样提交 → Key 不能变"""
    _, before = ai.get("/api/ai/config")
    eps = before["ai"]["endpoints"]
    assert all("****" in k for k in eps[0]["api_keys"])
    status, after = ai.put("/api/ai/config", {"endpoints": eps, "active_model": "stub-model"})
    assert status == 200 and after["ai"]["active_model"] == "stub-model"
    on_disk = json.loads((ai.data_dir / "config.json").read_text(encoding="utf-8"))
    assert on_disk["ai"]["endpoints"][0]["api_keys"] == ["sk-stub-000000000001"]
