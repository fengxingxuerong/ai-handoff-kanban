# -*- coding: utf-8 -*-
"""AI 能力：默认全部跳过，需要真实模型配额时用 KANBAN_LIVE=1 启用。

    set KANBAN_LIVE=1
    .venv\\Scripts\\python.exe -m pytest tests\\test_ai.py -v

以前这里写的是"默认使用本机真实数据目录（%USERPROFILE%\\.ai-kanban）以便读到已配置的 Key，
会真实产生模型调用与一笔监管报告" —— 那是直接违反「测试约定 8：用例不许写真实数据目录」，
而违反一条约定最省事的后果就是大家都不跑它（这 6 条从写下起一次都没执行过）。
现在：**只读**复制一份真实 config.json 到临时数据目录 —— Key 拿得到，
`reports/`、`usage.jsonl`、`projects.json` 一个字节都不会落到真板上；
用例要的项目由夹具自己造，不依赖用户那块板子上有什么。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from app import llm                     # 取真实 Key 的那两个函数与发版回归共用同一份
from conftest import Api, ROOT

LIVE = os.environ.get("KANBAN_LIVE") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE, reason="需要真实 LLM 配额，设置 KANBAN_LIVE=1 才运行"
)


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """真 Key + **隔离数据目录**的实例。

    两条都得满足：没 Key 跑不了 AI；用真实数据目录跑就直接违反「测试约定 8」——
    `/api/ai/analyze` 会往 `reports/` 落一份真监管报告、`usage.jsonl` 记一笔真人账。
    Key 是通过 `PUT /api/ai/config` 灌进本次临时目录那份 config 的，
    所以它只存在于这个用完就删的目录里。
    """
    data_dir = tmp_path_factory.mktemp("kanban-live")
    eps = llm.usable_endpoints()
    if not eps:
        pytest.skip("本机没有可用的真实端点与 Key（可用 KANBAN_REAL_DATA_DIR 指到别处）")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    env["KANBAN_DATA_DIR"] = str(data_dir)        # 以前这里是 `env.pop(...)` = 写真实目录
    env["KANBAN_NO_SCHEDULER"] = "1"              # 别让活体用例顺手起一个调度线程
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
    # 自证：这台实例读的必须是本次的临时目录。端口通了不等于起对了
    # （Windows 上 SO_REUSEADDR 允许两个进程都"绑定成功"，那个静默活着的就是你的真看板）
    _, health = api.get("/api/health")
    assert str(data_dir) in str(health.get("data_file", "")), (
        f"实例读的是 {health.get('data_file')}，不是本次临时目录 → 立刻停，绝不往下写")
    st, _ = api.put("/api/ai/config", {"endpoints": eps})
    assert st == 200, "Key 没能灌进隔离实例"
    # 用例要的项目自己造：不依赖用户那块板子上正好有什么
    st2, project = api.post("/api/projects", {
        "name": "活体用例项目", "status": "进行中", "progress": 40, "priority": "高",
        "background": "这是活体测试自建的临时项目，只用来让 AI 有东西可读。",
        "next_action": "跑完就随临时目录一起删"})
    assert st2 == 201, f"临时项目没建出来：{st2} {project}"
    api.project_id = project["id"]
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
    assert "actions" in parsed or "need_clarification" in parsed, body
    # batch_ref 是"同一批执行两次只建一遍"那把幂等键的来源；活体路径也得带上，
    # 否则真模型跑通的恰好是桩里没走过的那一半。
    assert body.get("batch_ref"), f"AI 指令没回 batch_ref：{list(body)}"


def test_ai_analyze_produces_report(live_server):
    status, report = live_server.post("/api/ai/analyze")
    assert status == 200 and report.get("id")
    assert "insights" in report


def test_ai_handoff_generation(live_server):
    """真实模型写得出交接说明（这份正文只落在临时目录里）"""
    status, body = live_server.post(f"/api/ai/handoff/{live_server.project_id}", {"mode": "generate"})
    assert status == 200 and len(body.get("text", "")) > 50


# 这里原来还有一条 `test_agents_md_has_local_fallback`，断言写的是
# `source in ("ai", "local")` —— 两个可能取值全列上，永远不会红，
# 而"LLM 不可用时退回本地规则版"那一支它一次也没验过。
# 同一件事在 `test_ai_endpoints.py:137/222`、`test_agent_files.py:159` 已经用假端点
# 真断言过三次（`assert source == "local"`），活体这边不需要再造一份。
# 2026-09-23 删除；AGENTS.md 的本地兜底能力由那三条守着。
