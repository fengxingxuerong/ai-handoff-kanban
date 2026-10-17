# -*- coding: utf-8 -*-
"""pytest 公共夹具：为每个测试会话起一个真实 uvicorn 服务（隔离数据目录）。"""
from __future__ import annotations

import collections
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


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """**任何用例都不许碰真实数据目录**（`%USERPROFILE%\\.ai-kanban`）。

    历史事故（2026-09-17 实测）：`test_llm_gateway.py` 直接调 `llm.chat()`，
    而 `llm` 自己按 `KANBAN_DATA_DIR` 解析数据目录 —— 那些用例没设这个变量，
    于是 `_record_usage()` 落到**用户真实目录**的 `usage.jsonl`：
    跑一次测试就给真实用量账本掺 12 行假数据（endpoint「甲」/model「stub-model」），
    `/api/usage` 的调用次数与成功率跟着失真。

    在**环境变量层**隔离，覆盖所有进程内代码路径（llm / main / mcp 都从同一处解析），
    不依赖每条用例自觉。需要真实配置的用例（如 test_ai.py 的 live 用例）自己 pop 这个变量。
    """
    d = tmp_path / "kanban-autouse"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KANBAN_DATA_DIR", str(d))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    """极简客户端，支持自定义 Host / Origin / Token 以便测试防护逻辑。

    两种传输：
    - client=None  → 真打 HTTP（子进程起的 uvicorn，保真度最高）
    - client=TestClient(...) → 进程内直接调 ASGI，快很多且能被覆盖率统计
    """

    def __init__(self, base: str = "", token: str | None = None, client=None, data_dir=None):
        self.base = base.rstrip("/")
        self.token = token
        self.client = client
        self.data_dir = data_dir

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

        if self.client is not None:
            r = self.client.request(method, path, content=data, headers=headers)
            ct = r.headers.get("content-type", "")
            if "json" in ct:
                try:
                    return r.status_code, r.json()
                except Exception:
                    return r.status_code, r.text
            return r.status_code, r.text

        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # 真实模型/交接生成可能超过 20s
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


def _wait_health(api: Api, timeout: float = 25.0, expect_dir: Path | None = None) -> bool:
    """等实例就绪。**端口通了不等于连对了** —— 必须让它自证读的是本次的数据目录。

    为什么加 `expect_dir`：`free_port()` 是 bind 一下再关掉，子进程随后才 bind 同一个端口，
    中间这段窗口里端口可能被别的进程（同事跑的桌面版、xdist 的另一个 worker、上一次
    没退干净的实例）抢走。这时 `/api/health` 照样 200，而用例读到的是**别人的看板** ——
    表现为凭空多出几个项目（2026-09-25 实测：`test_export_button_really_downloads`
    在别人实例上读到"对账用甲/乙项目"而假红），更坏的情况是写入别人的数据目录。
    判据跟 `scripts/ci_local.py` 那条"实例自证"一致：**问它自己读的是哪份数据**。
    """
    want = os.path.normcase(os.path.abspath(str(expect_dir))) if expect_dir else None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, body = api.get("/api/health")
            if status == 200:
                if want is None:
                    return True
                got = os.path.normcase(os.path.abspath(str((body or {}).get("data_file", ""))))
                if got.startswith(want):
                    return True
                # 连上了但不是我们起的：继续等没用，直接判失败（调用方会终止进程）
                raise RuntimeError(
                    f"端口 {api.base} 上是别的实例（它读 {got}，本次应为 {want}）—— "
                    f"拒绝在别人的数据上跑用例")
        except RuntimeError:
            raise
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
        try:
            ok = _wait_health(api, expect_dir=self.data_dir)
        except RuntimeError:
            self.proc.terminate()          # 连错实例时别留下自己这个抢不到端口的进程
            raise
        if not ok:
            self.proc.terminate()
            raise RuntimeError(
                f"服务在 25 秒内未就绪（端口 {port}，数据目录 {self.data_dir}）")
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


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """进程内客户端：不起子进程，快且能被覆盖率统计。

    注意 base_url 必须是 127.0.0.1，否则 Host 校验中间件会把默认的 testserver 拒掉。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    data_dir = tmp_path / "kanban-inproc"
    monkeypatch.setenv("KANBAN_DATA_DIR", str(data_dir))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield Api(client=c, data_dir=data_dir)


@pytest.fixture
def ai_client(tmp_path, monkeypatch):
    """进程内客户端 + 假 LLM 端点（不消耗真实配额）；api.stub 可用来注入响应"""
    from fastapi.testclient import TestClient
    from llm_stub import StubLLM

    from app.main import app

    stub = StubLLM().start()
    data_dir = tmp_path / "kanban-ai"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "config.json").write_text(json.dumps({
        "ai": {
            "endpoints": [stub.endpoint("桩端点")],
            "active_model": "stub-model",
            "schedule": {"enabled": False, "frequency": "daily", "time": "09:00", "last_run": ""},
        }
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("KANBAN_DATA_DIR", str(data_dir))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        api = Api(client=c, data_dir=data_dir)
        api.stub = stub
        yield api
    stub.stop()


def _seed_config(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "ai": {
            "endpoints": [
                {"name": "端点甲", "base_url": "https://a.example.com/v1",
                 "api_keys": ["sk-aaaaaa1111111111", "sk-bbbbbb2222222222"],
                 "models": ["model-a", "model-b"], "enabled": True},
                {"name": "端点乙", "base_url": "https://b.example.com/v1",
                 "api_keys": ["sk-cccccc3333333333"],
                 "models": ["model-c"], "enabled": False},
            ],
            "active_model": "model-a",
            "schedule": {"enabled": False, "frequency": "daily", "time": "09:00", "last_run": ""},
        }
    }
    (data_dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def seeded_client(tmp_path, monkeypatch):
    """进程内客户端 + 预置 config.json（含假 Key）"""
    from fastapi.testclient import TestClient

    from app.main import app

    data_dir = tmp_path / "kanban-seed-inproc"
    _seed_config(data_dir)
    monkeypatch.setenv("KANBAN_DATA_DIR", str(data_dir))
    monkeypatch.delenv("KANBAN_TOKEN", raising=False)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield Api(client=c, data_dir=data_dir)


def pytest_terminal_summary(terminalreporter):
    """把被跳过的用例**念出名字**写进报告尾巴。

    为什么需要它：跳过在报告里只是一个 `s`，和通过的 `.` 颜色相近、位置分散，
    而"某个文件整份被 skipif 掉了"（本机没有 node / 没有真实 LLM Key / 没有浏览器）
    看起来跟"全绿"毫无区别。2026-09 实测：本机跑一次就有 5 条 live 用例永久跳过，
    而报告的 `529 passed` 里根本看不出这件事 —— 覆盖率照样 92%，因为那些行本来
    就没进分母。跳过的用例是**没验**，不是**验过了**，必须让它出现在结尾。
    """
    skipped = terminalreporter.stats.get("skipped", [])
    if not skipped:
        return
    by_file = collections.Counter()
    for rep in skipped:
        by_file[str(rep.fspath).replace("\\", "/").split("/")[-1]] += 1
    terminalreporter.write_sep("=", f"跳过 {len(skipped)} 条（没验，不是通过）", bold=True)
    for name, n in sorted(by_file.items(), key=lambda x: -x[1]):
        terminalreporter.write_line(f"  {name}: {n} 条")
    terminalreporter.write_line(
        "  整份跳过的文件意味着这条能力在本机从未被验证过；跑 CI 或补齐环境后再看这个数。")


@pytest.fixture
def seeded_server(tmp_path):
    """子进程版：预置 config.json（含假 Key）"""
    data_dir = tmp_path / "kanban-seed"
    _seed_config(data_dir)
    with _server_ctx(data_dir, {}) as (api, _proc):
        api.data_dir = data_dir
        yield api
