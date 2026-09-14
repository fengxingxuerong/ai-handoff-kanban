# -*- coding: utf-8 -*-
"""AI 能力：默认全部跳过，需要真实模型配额时用 KANBAN_LIVE=1 启用。

    set KANBAN_LIVE=1
    .venv\\Scripts\\python.exe -m pytest tests\\test_ai.py -v

注意：默认使用本机真实数据目录（%USERPROFILE%\\.ai-kanban）以便读到已配置的 Key，
会真实产生模型调用与一笔监管报告。隔离数据目录下没有 Key，跑不了 AI 用例。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import Api, ROOT

LIVE = os.environ.get("KANBAN_LIVE") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE, reason="需要真实 LLM 配额，设置 KANBAN_LIVE=1 才运行"
)


@pytest.fixture(scope="module")
def live_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    env.pop("KANBAN_DATA_DIR", None)      # 用真实配置目录，才有 Key
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    api = Api(f"http://127.0.0.1:{port}")
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            if api.get("/api/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail("服务未就绪")
    yield api
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_ai_config_keys_are_masked(live_server):
    status, cfg = live_server.get("/api/ai/config")
    assert status == 200
    endpoints = cfg["ai"]["endpoints"]
    assert endpoints, "未配置任何端点"
    for ep in endpoints:
        for key in ep.get("api_keys", []):
            assert key == "" or "****" in key, f"Key 未掩码：{key}"


def test_ai_connection(live_server):
    status, body = live_server.post("/api/ai/test")
    assert status == 200
    assert body.get("ok") is True, f"端点全部不可用：{body}"


def test_ai_natural_language_command_parse(live_server):
    status, body = live_server.post("/api/ai/command",
                                    {"text": "给第一个进行中的项目加一个任务：整理部署文档，优先级高"})
    assert status == 200
    parsed = body.get("parsed", {})
    assert "actions" in parsed or "need_clarification" in parsed


def test_ai_analyze_produces_report(live_server):
    status, report = live_server.post("/api/ai/analyze")
    assert status == 200 and report.get("id")
    assert "insights" in report


def test_ai_handoff_generation(live_server):
    _, data = live_server.get("/api/projects")
    projects = data["projects"]
    if not projects:
        pytest.skip("没有项目可用于生成交接说明")
    status, body = live_server.post(f"/api/ai/handoff/{projects[0]['id']}", {"mode": "generate"})
    assert status == 200 and len(body.get("text", "")) > 50


def test_agents_md_has_local_fallback(live_server):
    """LLM 不可用时必须退回本地规则版，而不是报错"""
    _, data = live_server.get("/api/projects")
    projects = data["projects"]
    if not projects:
        pytest.skip("没有项目")
    status, body = live_server.post(f"/api/export/agentsmd/{projects[0]['id']}")
    assert status == 200
    assert body.get("source") in ("ai", "local")
    assert "AGENTS.md" in body.get("text", "")
