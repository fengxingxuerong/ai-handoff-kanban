# -*- coding: utf-8 -*-
"""OpenAI 兼容的假 LLM 端点，用于在无配额、无网络的前提下测试全部 AI 路径。

用法：
    stub = start_stub()
    stub.push_json({"summary": "ok", "insights": []})    # 下一次调用返回这个 JSON
    stub.push_text("纯文本回复")                          # 下一次调用返回文本
    stub.push_status(429)                                # 下一次调用返回 429
    stub.status_for_key["sk-bad"] = 401                  # 指定 Key 固定失败（测轮询）
    stub.url  ->  http://127.0.0.1:port/v1
    stub.calls -> [(auth_header, payload), ...]
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):        # 静音
        pass

    def do_POST(self):  # noqa: N802
        stub: StubLLM = self.server.stub
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {"_raw": raw.decode("utf-8", "replace")}
        auth = self.headers.get("Authorization") or ""

        with stub.lock:
            stub.calls.append({"path": self.path, "auth": auth, "payload": payload})

        # 指定 Key 固定失败
        for key, code in stub.status_for_key.items():
            if auth.endswith(key):
                self._send(code, {"error": {"message": "stub forced failure"}})
                return

        with stub.lock:
            item = stub.queue.pop(0) if stub.queue else stub.default

        if item is None:
            self._send(500, {"error": {"message": "stub has no response configured"}})
            return
        if isinstance(item, int):
            self._send(item, {"error": {"message": f"stub status {item}"}})
            return
        if isinstance(item, tuple):
            kind, value = item
            if kind == "raw":
                self._send_raw(200, value)
            elif kind == "text":
                self._send(200, {"choices": [{"message": {"content": value}}]})
            elif kind == "json":
                self._send(200, {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]})
            elif kind == "status_body":
                code, message = value
                self._send(code, {"error": {"message": message}})
            else:
                self._send(500, {"error": {"message": "unknown stub item"}})
            return
        self._send(200, {"choices": [{"message": {"content": str(item)}}]})

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_raw(self, code: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StubLLM:
    def __init__(self) -> None:
        self.queue: list = []
        self.default = ("text", "stub-ok")
        self.status_for_key: dict[str, int] = {}
        self.calls: list[dict] = []
        self.lock = threading.Lock()
        self._srv: ThreadingHTTPServer | None = None

    # ---- 构造响应 ----
    def push_json(self, obj) -> None:
        self.queue.append(("json", obj))

    def push_text(self, text: str) -> None:
        self.queue.append(("text", text))

    def push_raw(self, text: str) -> None:
        self.queue.append(("raw", text))

    def push_status(self, code: int) -> None:
        self.queue.append(code)

    def push_status_body(self, code: int, message: str) -> None:
        """带自定义错误正文的状态码：测「按正文分支」的逻辑（如 400 里的 temperature 提示）"""
        self.queue.append(("status_body", (code, message)))

    def set_default_status_body(self, code: int, message: str) -> None:
        self.default = ("status_body", (code, message))

    def set_default_json(self, obj) -> None:
        self.default = ("json", obj)

    def set_default_status(self, code: int) -> None:
        self.default = code

    def reset(self) -> None:
        self.queue.clear()
        self.status_for_key.clear()
        self.calls.clear()
        self.default = ("text", "stub-ok")

    # ---- 生命周期 ----
    @property
    def url(self) -> str:
        host, port = self._srv.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> "StubLLM":
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._srv.stub = self          # type: ignore[attr-defined]
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    def endpoint(self, name: str = "桩端点", keys: list[str] | None = None, models=None) -> dict:
        return {
            "name": name,
            "base_url": self.url,
            "api_keys": keys if keys is not None else ["sk-stub-000000000001"],
            "models": models or ["stub-model"],
            "enabled": True,
        }
