# -*- coding: utf-8 -*-
"""安全与数据完整性：字段白名单、特殊字符、Host/Origin 防护、保存路径、Token 鉴权"""
from __future__ import annotations

import os
import tempfile

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
