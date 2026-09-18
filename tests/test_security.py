# -*- coding: utf-8 -*-
"""安全与数据完整性：字段白名单、特殊字符、Host/Origin 防护、保存路径、Token 鉴权"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

WEIRD = "张三's 项目 <b>\"引号\"</b>"


@pytest.fixture
def server(app_client):
    """进程内客户端（原为子进程实例）"""
    return app_client


@pytest.fixture
def fresh_server(app_client):
    """进程内客户端，每个用例一个干净数据目录"""
    return app_client


def test_special_char_name_roundtrip(fresh_server):
    """含撇号/引号/尖括号的项目名可创建、读回、删除（曾导致卡片按钮 JS 语法错误）"""
    status, project = fresh_server.create_project(WEIRD)
    assert status == 201
    pid = project["id"]

    status, detail = fresh_server.get(f"/api/projects/{pid}")
    assert status == 200 and detail["name"] == WEIRD
    assert fresh_server.delete(f"/api/projects/{pid}")[0] == 200


@pytest.mark.parametrize("field,value", [
    ("status", "进行中<script>"),
    ("status", "随便写"),
    ("priority", "超高"),
    ("progress", 250),
    ("progress", -5),
])
def test_field_whitelist_rejects_out_of_range(fresh_server, field, value):
    status, _ = fresh_server.create_project("白名单测试", **{field: value})
    assert status == 422, f"{field}={value} 应被拒绝"


@pytest.mark.parametrize("field,value", [
    ("status", "进行中"),
    ("priority", "低"),
    ("progress", 0),
    ("progress", 100),
])
def test_field_whitelist_accepts_valid(fresh_server, field, value):
    status, _ = fresh_server.create_project("白名单测试", **{field: value})
    assert status == 201


def test_task_status_whitelist(fresh_server):
    _, project = fresh_server.create_project("任务白名单")
    pid = project["id"]
    assert fresh_server.post(f"/api/projects/{pid}/tasks", {"title": "t", "status": "乱写"})[0] == 422
    assert fresh_server.post(f"/api/projects/{pid}/tasks", {"title": "t", "status": "阻塞"})[0] == 201


def test_cross_origin_rejected(server):
    assert server.get("/api/health", origin="http://evil.example.com")[0] == 403


def test_same_origin_allowed(server):
    assert server.get("/api/health", origin=server.base)[0] == 200


def test_foreign_host_rejected(server):
    assert server.get("/api/health", host="evil.example.com")[0] == 403


def test_localhost_host_allowed(server):
    assert server.get("/api/health", host="127.0.0.1")[0] == 200


@pytest.mark.parametrize("raw", ["[::1]", "[::1]:18321"])
def test_ipv6_host_is_parsed_not_truncated(server, raw):
    """带端口的 IPv6 Host 里冒号不止一个，`split(":")[0]` 只剩一个 `[` → 判成空 Host 放行。
    真正要挡的是 `[fe80::1]` / `[evil.example.com]` 这种同样带方括号、但并非本机的 Host。"""
    assert server.get("/api/health", host=raw)[0] == 200


@pytest.mark.parametrize("raw", ["[fe80::1]", "[evil.example.com]", "evil.example.com:80"])
def test_bracketed_foreign_host_rejected(server, raw):
    assert server.get("/api/health", host=raw)[0] == 403


def test_export_save_rejects_system_dir(fresh_server):
    status, _ = fresh_server.post("/api/export/save", {
        "filename": "x.md", "content": "# hi", "path": r"C:\Windows\evil.md"})
    assert status == 400


def test_export_save_rejects_non_markdown_suffix(fresh_server):
    tmp = os.path.join(tempfile.gettempdir(), "kanban_pytest_bad.exe")
    status, _ = fresh_server.post("/api/export/save", {
        "filename": "x.exe", "content": "x", "path": tmp})
    assert status == 400


def test_export_save_writes_to_temp(fresh_server):
    target = os.path.join(tempfile.gettempdir(), "kanban_pytest_ok.md")
    status, body = fresh_server.post("/api/export/save", {
        "filename": "x.md", "content": "# hi", "path": target})
    assert status == 200 and body["ok"] is True
    assert os.path.exists(target)
    os.remove(target)


def test_token_required_for_write(app_client, monkeypatch):
    """开了 KANBAN_TOKEN：无 Token 的写请求 401，带对了就通（读接口不受影响）"""
    monkeypatch.setenv("KANBAN_TOKEN", "pytest-token")
    body = {"name": "token 测试", "status": "进行中", "progress": 0, "priority": "中"}

    assert app_client.get("/api/health")[0] == 200

    assert app_client.request("POST", "/api/projects", body, token=None)[0] == 401
    assert app_client.request("POST", "/api/projects", body, token="wrong-token")[0] == 401

    status, project = app_client.request("POST", "/api/projects", body, token="pytest-token")
    assert status == 201
    assert app_client.delete(f"/api/projects/{project['id']}", token="pytest-token")[0] == 200


# ---------------- 保存导出文件：不许静默冲掉用户手写的文件 ----------------

def test_save_export_refuses_to_clobber_existing_file(app_client, tmp_path):
    target = tmp_path / "手写的AGENTS.md"
    target.write_text("我自己写的交接文件，一个字都不能少\n", encoding="utf-8")

    status, res = app_client.post("/api/export/save", {
        "filename": "AGENTS.md", "content": "生成的内容", "path": str(target)})
    assert status == 409, f"已存在的文件被直接覆盖了：{status} {res}"
    assert "已存在" in str(res.get("detail")), res
    assert "一个字都不能少" in target.read_text(encoding="utf-8"), "拒绝之前就已经写过了"

    status, res = app_client.post("/api/export/save", {
        "filename": "AGENTS.md", "content": "生成的内容", "path": str(target), "overwrite": True})
    assert status == 200, res
    assert target.read_text(encoding="utf-8") == "生成的内容"


def test_save_export_creates_missing_parents(app_client, tmp_path):
    target = tmp_path / "深层" / "子目录" / "交接包.md"
    status, res = app_client.post("/api/export/save", {
        "filename": "交接包.md", "content": "正文", "path": str(target)})
    assert status == 200, res
    assert target.exists() and target.read_text(encoding="utf-8") == "正文"


def test_save_export_auto_name_still_replaces_its_own_export(app_client):
    """不给 path 时走"桌面→数据目录 exports/"的自动命名，这是本应用自己写过的文件，
    重复导出应当直接替换（否则每天导出一次就会开始报错）。"""
    body = {"filename": "同日重复导出.md", "content": "第一版"}
    assert app_client.post("/api/export/save", body)[0] == 200
    assert app_client.post("/api/export/save", {**body, "content": "第二版"})[0] == 200


def test_agentsmd_save_sends_the_overwrite_flag():
    """界面必须把覆盖开关接到 /api/export/save，否则用户没有任何办法覆盖旧文件"""
    html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    i = html.index("async function saveAgentsMD")
    assert "overwrite:" in html[i:i + 1200], "保存按钮没带 overwrite"
    assert 'id="agentsmd-overwrite"' in html
