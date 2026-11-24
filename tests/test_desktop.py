from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_DATA_DIR", str(tmp_path / "desktop-data"))
    from scripts import desktop as module

    monkeypatch.setattr(module, "LOG_FILE", tmp_path / "startup.log")
    return module


def test_select_port_keeps_an_owned_instance(desktop, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "PORT", 18320)
    monkeypatch.setattr(desktop, "data_dir", lambda: tmp_path / "owned")
    monkeypatch.setattr(desktop, "port_in_use", lambda port: True)
    monkeypatch.setattr(desktop, "_health", lambda port: {"data_file": str(tmp_path / "owned" / "projects.json")})
    monkeypatch.setattr(desktop, "free_port", lambda: pytest.fail("不应换端口"))
    assert desktop.select_port() == 18320


@pytest.mark.parametrize("health", [None, {"ok": True}, {"data_file": "foreign/projects.json"}])
def test_select_port_rejects_foreign_or_unresponsive_instance(desktop, tmp_path, monkeypatch, health):
    monkeypatch.setattr(desktop, "PORT", 18320)
    monkeypatch.setattr(desktop, "data_dir", lambda: tmp_path / "owned")
    monkeypatch.setattr(desktop, "port_in_use", lambda port: True)
    monkeypatch.setattr(desktop, "_health", lambda port: health)
    monkeypatch.setattr(desktop, "free_port", lambda: 18444)
    assert desktop.select_port() == 18444


def test_http_alive_requires_the_expected_data_directory(desktop, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "PORT", 18320)
    monkeypatch.setattr(desktop, "data_dir", lambda: tmp_path / "owned")
    monkeypatch.setattr(desktop, "_health", lambda port: {
        "ok": True, "data_file": str(tmp_path / "foreign" / "projects.json")})
    assert desktop.http_alive(timeout=0.01) is False
    monkeypatch.setattr(desktop, "_health", lambda port: {
        "ok": True, "data_file": str(tmp_path / "owned" / "projects.json")})
    assert desktop.http_alive(timeout=0.01) is True


def test_desktop_window_uses_the_selected_port_and_expected_geometry(desktop, monkeypatch):
    created = {}
    started = {}

    def create_window(*args, **kwargs):
        created["args"] = args
        created["kwargs"] = kwargs

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create_window,
                                                               start=lambda **kw: started.update(kw)))
    monkeypatch.delenv("KANBAN_NO_WINDOW", raising=False)
    monkeypatch.setattr(desktop, "select_port", lambda: 18444)
    monkeypatch.setattr(desktop, "port_in_use", lambda port: True)
    monkeypatch.setattr(desktop, "http_alive", lambda: True)
    monkeypatch.setattr(desktop, "log", lambda message: None)
    monkeypatch.setattr(desktop, "ensure_standard_streams", lambda: None)

    desktop.main()

    assert created["args"] == ("AI 交接看板", "http://127.0.0.1:18444")
    assert created["kwargs"]["width"] == 1280
    assert created["kwargs"]["height"] == 820
    assert created["kwargs"]["min_size"] == (960, 640)
    assert started == {"gui": "edgechromium"}
    assert os.environ["KANBAN_PORT"] == "18444"
