# -*- coding: utf-8 -*-
"""补齐三块"逻辑真但一直没测到"的分支

覆盖率此前卡在 90%（门槛也是 90，等于没有余量）。逐条看未覆盖行，挑出**不是死代码**的三块：

1. 健康趋势对比 —— 只有存在"上一份报告"时才会走的分支，之前测试永远只造一份报告
2. 调度线程触发与防重 —— 之前一行没覆盖（常驻循环，需要把 time 换成假的才能进循环体）
3. 导出到"非临时目录但允许"的路径 —— 之前测试都写临时目录，函数提前 return 了
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest


# ---------------- 1. 健康趋势对比 ----------------

def _write_report(data_dir, rid: str, health_by_id: dict) -> None:
    import app.main as kanban

    d = kanban.reports_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rid}.json").write_text(json.dumps({
        "id": rid, "created_at": f"2026-01-01 0{rid[-6:-5]}:00",
        "insights": [{"id": pid, "name": f"项目 {pid}", "health": h, "reason": "", "risks": [],
                      "suggestions": []} for pid, h in health_by_id.items()],
        "global_suggestions": [],
    }, ensure_ascii=False), encoding="utf-8")


def test_trend_compares_with_previous_report(ai_client):
    """上一份报告存在时，要算出 down / up / flat 三档"""
    _write_report(ai_client.data_dir, "20260101_090000",
                  {"p1": 50, "p2": 70, "p3": 80, "p4": None})
    ai_client.stub.push_json({
        "summary": "对比用",
        "insights": [
            {"id": "p1", "name": "项目 p1", "health": 35, "reason": "", "risks": [], "suggestions": []},
            {"id": "p2", "name": "项目 p2", "health": 82, "reason": "", "risks": [], "suggestions": []},
            {"id": "p3", "name": "项目 p3", "health": 75, "reason": "", "risks": [], "suggestions": []},
            {"id": "p4", "name": "项目 p4", "health": 60, "reason": "", "risks": [], "suggestions": []},
            {"id": "p5", "name": "项目 p5", "health": 60, "reason": "", "risks": [], "suggestions": []},
        ],
        "global_suggestions": [],
    })
    status, report = ai_client.post("/api/ai/analyze")
    assert status == 200

    trends = {it["id"]: it["trend"] for it in report["insights"]}
    assert trends["p1"] == {"prev": 50, "delta": -15, "level": "down"}, trends["p1"]
    assert trends["p2"] == {"prev": 70, "delta": 12, "level": "up"}, trends["p2"]
    assert trends["p3"] == {"prev": 80, "delta": -5, "level": "flat"}, trends["p3"]
    assert trends["p4"]["level"] == "new", "上一份里没有健康分，应标 new"
    assert trends["p5"]["level"] == "new", "上一份里没有这个项目，应标 new"


def test_trend_is_new_when_no_previous_report(ai_client):
    ai_client.stub.push_json({
        "summary": "首份", "global_suggestions": [],
        "insights": [{"id": "p1", "name": "p1", "health": 50, "reason": "", "risks": [], "suggestions": []}],
    })
    _, report = ai_client.post("/api/ai/analyze")
    assert report["insights"][0]["trend"] == {"delta": None, "level": "new"}


def test_trend_ignores_broken_report_files(ai_client):
    """报告目录里混进坏文件时不能把趋势对比整个搞挂"""
    d = ai_client.data_dir / "reports"
    d.mkdir(parents=True, exist_ok=True)
    (d / "20260101_080000.json").write_text("{ 坏的", encoding="utf-8")
    _write_report(ai_client.data_dir, "20260101_090000", {"p1": 50})
    ai_client.stub.push_json({
        "summary": "带坏文件", "global_suggestions": [],
        "insights": [{"id": "p1", "name": "p1", "health": 40, "reason": "", "risks": [], "suggestions": []}],
    })
    status, report = ai_client.post("/api/ai/analyze")
    assert status == 200
    assert report["insights"][0]["trend"]["level"] == "down"


# ---------------- 2. 调度线程 ----------------

class FakeTime:
    """只替换 kanban 模块里的 time，不动全局 time 模块（否则会波及其它线程）"""

    def __init__(self, stop_after: int = 2):
        self.slept: list[int] = []
        self.stop_after = stop_after

    def sleep(self, sec: int) -> None:
        self.slept.append(sec)
        if len(self.slept) >= self.stop_after:
            raise StopIteration("退出循环")


def _sched_cfg(*, enabled=True, freq="daily", at=None, last_period="") -> dict:
    return {"ai": {"endpoints": [], "active_model": "", "schedule": {
        "enabled": enabled, "frequency": freq,
        "time": at or datetime.now().strftime("%H:%M"),
        "last_run": "", "last_period": last_period}}}


def _run_loop(monkeypatch, cfg, analyze=None, update=None):
    import app.main as kanban

    calls, updates, fake = [], [], FakeTime()
    monkeypatch.setattr(kanban, "time", fake)
    monkeypatch.setattr(kanban.llm, "load_config", lambda: cfg)
    monkeypatch.setattr(kanban, "_run_analyze",
                        analyze or (lambda trigger="manual": (calls.append(trigger),
                                                              {"id": "1", "created_at": "now"})[1]))
    monkeypatch.setattr(kanban.llm, "update_config",
                        update or (lambda fn: updates.append(fn)))
    with pytest.raises(StopIteration):
        kanban._scheduler_loop()
    return calls, updates, fake


def test_scheduler_fires_at_configured_time(app_client, monkeypatch):
    calls, updates, fake = _run_loop(monkeypatch, _sched_cfg())
    assert calls == ["schedule"], "到点了应该触发一次自动监管"
    assert fake.slept[0] == 61, "触发后应长睡，避免同一分钟内重复触发"
    assert updates, "触发后要把 last_run / last_period 落盘"

    cfg_after = {"ai": {}}
    updates[0](cfg_after)
    assert cfg_after["ai"]["schedule"]["last_run"] == "now"
    assert cfg_after["ai"]["schedule"]["last_period"], "没写周期键，重启后会重复触发"


def _other_minute() -> str:
    """一个**保证不等于当前分钟**的时间（+1 小时）。

    别写 `at="00:01"` 这种常量：跑到 00:01 那一分钟就会意外命中，
    用例变成"看运气红不红"（2026-09-16 凌晨实际踩到过一次）。
    """
    from datetime import timedelta

    return (datetime.now() + timedelta(hours=1)).strftime("%H:%M")


def test_scheduler_skips_when_disabled_or_wrong_minute(app_client, monkeypatch):
    assert _run_loop(monkeypatch, _sched_cfg(enabled=False))[0] == []
    assert _run_loop(monkeypatch, _sched_cfg(at=_other_minute()))[0] == [], "时间没到不该触发"
    assert _run_loop(monkeypatch, _sched_cfg(freq="hourly"))[0] == [], "只支持 daily/weekly"


def test_scheduler_skips_already_fired_period(app_client, monkeypatch):
    """同一周期内重启不应重复触发 —— last_period 就是干这个的"""
    import app.main as kanban

    period = kanban._period_key(datetime.now(), "daily")
    assert _run_loop(monkeypatch, _sched_cfg(last_period=period))[0] == []


def test_scheduler_survives_analyze_failure(app_client, monkeypatch):
    """监管失败不能让循环崩掉（更不该把 last_period 写进去）"""
    def boom(trigger="manual"):
        raise RuntimeError("模型全挂了")

    calls, updates, _ = _run_loop(monkeypatch, _sched_cfg(), analyze=boom)
    assert updates == [], "分析失败不该记 last_period，否则这一周期就被跳过了"


def test_scheduler_loop_swallows_config_errors(app_client, monkeypatch):
    """读配置就炸的情况（配置文件损坏）也要继续转，不能把线程带死"""
    import app.main as kanban

    fake = FakeTime()
    monkeypatch.setattr(kanban, "time", fake)
    monkeypatch.setattr(kanban.llm, "load_config",
                        lambda: (_ for _ in ()).throw(RuntimeError("配置坏了")))
    with pytest.raises(StopIteration):
        kanban._scheduler_loop()
    assert len(fake.slept) >= 1


# ---------------- 4. LLM 网关的错误分类与 temperature 重试 ----------------

@pytest.fixture
def no_sleep(monkeypatch):
    """把 llm 模块里的 time 换成假对象：避免真实的 0.4 秒退避拖慢用例，
    也不去 patch 全局 time.sleep（那会波及调度线程）"""
    import time as real_time

    import app.llm as llm

    class _NoSleep:
        def __getattr__(self, name):
            return getattr(real_time, name)

        def sleep(self, _sec: float) -> None:
            pass

    monkeypatch.setattr(llm, "time", _NoSleep())


def _chat(llm_config):
    import app.llm as llm

    return llm.chat(llm_config, [{"role": "user", "content": "你好"}])


def test_temperature_retry_on_400(no_sleep):
    """模型不吃低 temperature（kimi 系）：网关要自动用 1 重试一次"""
    from llm_stub import StubLLM

    stub = StubLLM().start()
    try:
        stub.push_status_body(400, "invalid temperature value: only 1 is allowed")
        stub.push_text("重试成功")
        cfg = {"ai": {"endpoints": [stub.endpoint("端点甲")], "active_model": "stub-model"}}
        text, ep = _chat(cfg)
        assert text == "重试成功" and ep == "端点甲"
        temps = [c["payload"].get("temperature") for c in stub.calls]
        assert temps[0] != 1 and temps[1] == 1, f"第二次必须用 temperature=1 重试，实际 {temps}"
    finally:
        stub.stop()


def test_temperature_retry_still_failing(no_sleep):
    """重试还是失败 → 记进错误聚合，不能把异常直接抛给上层"""
    import app.llm as llm
    from llm_stub import StubLLM

    stub = StubLLM().start()
    try:
        stub.set_default_status_body(400, "temperature must be 1")
        cfg = {"ai": {"endpoints": [stub.endpoint("端点甲")], "active_model": "stub-model"}}
        with pytest.raises(llm.LLMError) as e:
            _chat(cfg)
        assert "temp=1 重试仍失败" in str(e.value), str(e.value)
    finally:
        stub.stop()


def test_gateway_classifies_http_errors(no_sleep):
    """401/402/404/5xx/其它 4xx 要分类报错 —— 不然用户看到一句「调用失败」不知道是配额、key 还是模型下线"""
    import app.llm as llm
    from llm_stub import StubLLM

    stub = StubLLM().start()
    try:
        cfg = {"ai": {"endpoints": [stub.endpoint("端点甲", keys=[f"sk-k{i}" for i in range(5)])],
                      "active_model": "stub-model"}}
        for code in (401, 402, 404, 500, 418):
            stub.push_status(code)
        with pytest.raises(llm.LLMError) as e:
            _chat(cfg)
        msg = str(e.value)
        for frag in ("Key 无效(401)", "余额不足(402)", "模型已下线(404)", "服务错误(500)", "HTTP 418"):
            assert frag in msg, f"缺少分类「{frag}」：{msg}"
    finally:
        stub.stop()


def test_gateway_reports_all_endpoints_failed(no_sleep):
    """所有端点都挂 → 报错要把每个端点的原因都带上，便于排查"""
    import app.llm as llm
    from llm_stub import StubLLM

    stub = StubLLM().start()
    try:
        stub.set_default_status(429)
        cfg = {"ai": {"endpoints": [stub.endpoint("端点甲"), stub.endpoint("端点乙")],
                      "active_model": "stub-model"}}
        with pytest.raises(llm.LLMError) as e:
            _chat(cfg)
        msg = str(e.value)
        assert "端点甲" in msg and "端点乙" in msg and "限流(429)" in msg
    finally:
        stub.stop()


# ---------------- 5. 导出到"允许但不是临时目录"的路径 ----------------

def test_export_save_allows_normal_user_directory(app_client):
    """常规路径（既不是系统目录、也不在临时目录下）要放行。

    必须挑一个这样的路径才能走到函数最后那行 `return False`：
    pytest 的 tmp_path 在 %TEMP% 下（临时目录提前 return），
    而 AppData 又是被明确禁止的（系统目录那一支）。
    所以用项目目录里的探针目录，用完删掉。
    """
    probe = Path(__file__).resolve().parent.parent / ".pytest_probe"
    probe.mkdir(exist_ok=True)
    target = probe / "我的交接包.md"
    try:
        status, body = app_client.post(
            "/api/export/save", {"filename": "交接包.md", "content": "# 内容", "path": str(target)})
        assert status == 200, body
        assert target.read_text(encoding="utf-8") == "# 内容"
    finally:
        shutil.rmtree(probe, ignore_errors=True)


# ---------------- 6. 前端资源缺失时不该白屏（安装完整性） ----------------

def test_index_reports_500_when_web_assets_missing(app_client, monkeypatch):
    """打包漏了 web 目录时，首页要给出可读的 500，而不是静默空白"""
    import app.main as kanban

    monkeypatch.setattr(kanban, "WEB_DIR", Path(__file__).resolve().parent / "不存在的目录")
    status, body = app_client.get("/")
    assert status == 500
    assert "前端资源缺失" in body


# ---------------- 7. 脱敏开关对三种交接物都生效 ----------------

def test_redact_flag_applies_to_page_and_agentsmd(app_client):
    _, p = app_client.create_project("脱敏项目", status="进行中",
                                     handoff_note="联系邮箱 someone@example.com")
    status, page = app_client.get(f"/api/export/page/{p['id']}?redact=1")
    assert status == 200 and "someone@example.com" not in page

    status, body = app_client.post(f"/api/export/agentsmd/{p['id']}?redact=1")
    assert status == 200 and "someone@example.com" not in json.dumps(body, ensure_ascii=False)


# ---------------- 8. 保存交接包：所有候选位置都写不进去时报 500 ----------------

def test_export_save_reports_500_when_every_candidate_fails(app_client, monkeypatch):
    """目标是"说清为什么失败"，而不是吞掉异常让用户以为保存成功了"""
    monkeypatch.setattr(Path, "write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("磁盘不可写")))
    status, body = app_client.post("/api/export/save",
                                   {"filename": "x.md", "content": "# 内容"})
    assert status == 500
    assert "保存失败" in str(body)


# ---------------- 9. git 调用的三条降级路径 ----------------

def test_run_git_degrades_on_missing_git(monkeypatch):
    import app.main as kanban

    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(kanban.subprocess, "run", boom)
    code, msg = kanban._run_git(["status"], Path("."))
    assert code == 127 and "未找到 git" in msg


def test_run_git_degrades_on_timeout(monkeypatch):
    import subprocess

    import app.main as kanban

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=6)

    monkeypatch.setattr(kanban.subprocess, "run", boom)
    code, msg = kanban._run_git(["log"], Path("."))
    assert code == 124 and "超时" in msg


def test_run_git_degrades_on_other_error(monkeypatch):
    import app.main as kanban

    def boom(*a, **k):
        raise RuntimeError("权限被拒")

    monkeypatch.setattr(kanban.subprocess, "run", boom)
    code, msg = kanban._run_git(["log"], Path("."))
    assert code == 1 and "权限被拒" in msg


# ---------------- 10. 掩码 Key 回写：空值与空白要跳过 ----------------

def test_merge_keys_skips_blank_entries():
    """历史 bug 区：空串被当成有效 Key 写进配置，会把真 Key 挤掉"""
    from app.main import _merge_keys

    old = ["sk-aaaaaa1111111111"]
    out = _merge_keys(old, ["  ", "", "sk-bbbbbb2222222222"], [])
    assert out == ["sk-bbbbbb2222222222"], "空/空白条目必须丢掉，不能变成空 Key"










