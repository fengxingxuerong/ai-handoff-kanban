# -*- coding: utf-8 -*-
"""pytest 公共夹具：为每个测试会话起一个真实 uvicorn 服务（隔离数据目录）。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    """极简 HTTP 客户端，支持自定义 Host / Origin / Token 以便测试防护逻辑。"""

    def __init__(self, base: str, token: str | None = None):
        self.base = base.rstrip("/")
        self.token = token

    def request(self, method, path, body=None, origin=None, host=None, token=...):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        use_token = self.token if token is ... else token
        if use_token:
            headers["X-Kanban-Token"] = use_token
        if origin:
            headers["Origin"] = origin
        if host:
            headers["Host"] = host
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                ct = resp.headers.get("content-type", "")
                return resp.status, (json.loads(raw) if "json" in ct else raw)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def __getattr__(self, name):
        if name in ("get", "post", "put", "delete", "patch"):
            return lambda path, body=None, **kw: self.request(name.upper(), path, body, **kw)
        raise AttributeError(name)

    def create_project(self, name, **kw):
        body = {"name": name, "status": "进行中", "progress": 10, "priority": "高"}
        body.update(kw)
        return self.request("POST", "/api/projects", body)

    def project_ids(self):
        _, data = self.get("/api/projects")
        return [p["id"] for p in data["projects"]]


def _wait_health(api: Api, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = api.get("/api/health")
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """默认实例：隔离数据目录、无 Token"""
    data_dir = tmp_path_factory.mktemp("kanban-data")
    with _server_ctx(data_dir, {}) as (api, proc):
        yield api
        assert proc.poll() is None, "服务进程意外退出"


class _server_ctx:
    def __init__(self, data_dir: Path, extra_env: dict, token: str | None = None):
        self.data_dir = data_dir
        self.extra_env = extra_env
        self.token = token
        self.proc = None

    def __enter__(self):
        port = free_port()
        env = dict(os.environ)
        env.update(self.extra_env)
        env["KANBAN_DATA_DIR"] = str(self.data_dir)
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("KANBAN_PORT", None)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        api = Api(f"http://127.0.0.1:{port}", token=self.token)
        if not _wait_health(api):
            self.proc.terminate()
            raise RuntimeError("服务在 25 秒内未就绪")
        return api, self.proc

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


@pytest.fixture
def token_server(tmp_path):
    """带 KANBAN_TOKEN 的实例，用于验证写接口鉴权"""
    token = "pytest-token"
    with _server_ctx(tmp_path / "kanban-token-data", {"KANBAN_TOKEN": token}, token=token) as (api, _proc):
        yield api


@pytest.fixture
def fresh_server(tmp_path):
    """每个用例一个干净数据目录的实例，用于会大量写数据的用例"""
    with _server_ctx(tmp_path / "kanban-fresh", {}) as (api, _proc):
        yield api
