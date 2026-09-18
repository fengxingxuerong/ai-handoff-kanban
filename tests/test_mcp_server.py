# -*- coding: utf-8 -*-
"""MCP 服务：协议握手、工具目录、工具调用、错误处理，以及跨进程写锁

用真子进程 + 真 stdin/stdout 拉起来测（MCP 的传输就是这个），不做桩。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "scripts" / "mcp_server.py"


class McpClient:
    """一行一个 JSON-RPC 消息的 stdio 客户端"""
    def __init__(self, data_dir: Path, env_extra: dict | None = None):
        env = dict(os.environ)
        env["KANBAN_DATA_DIR"] = str(data_dir)
        env["KANBAN_NO_SCHEDULER"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(env_extra or {})
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            cwd=str(ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self._id = 0
        self.stderr = self.proc.stderr

    def send_raw(self, line: str) -> None:
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def call(self, method: str, params: dict | None = None, notify: bool = False) -> dict | None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.send_raw(json.dumps(msg, ensure_ascii=False))
        if notify:
            return None
        return self.read()

    def read(self, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if line:
                return json.loads(line)
        raise TimeoutError("等待 MCP 响应超时")

    def tool(self, name: str, arguments: dict | None = None) -> tuple[str, bool]:
        resp = self.call("tools/call", {"name": name, "arguments": arguments or {}})
        assert "result" in resp, resp
        res = resp["result"]
        text = "\n".join(c.get("text", "") for c in res.get("content", []))
        return text, bool(res.get("isError"))

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def mcp(tmp_path):
    client = McpClient(tmp_path / "kanban-mcp")
    yield client
    client.close()


def last_id(text: str) -> str:
    """从「已在项目 [pid] 新增任务 [tid] …」里取最后一个方括号中的 id"""
    return text.split("[")[-1].split("]")[0]


# ---------------- 协议 ----------------

def test_initialize_negotiates_and_advertises_tools(mcp):
    resp = mcp.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "pytest", "version": "1"}})
    res = resp["result"]
    assert res["protocolVersion"] == "2025-06-18", "客户端报的版本在支持列表内就沿用它"
    assert "tools" in res["capabilities"]
    assert res["serverInfo"]["name"] == "ai-kanban"
    assert "instructions" in res


def test_unknown_client_version_falls_back(mcp):
    resp = mcp.call("initialize", {"protocolVersion": "1999-01-01"})
    assert resp["result"]["protocolVersion"] in ("2026-07-28", "2025-11-25")


def test_stateless_style_works_without_initialize(mcp):
    """2026-07-28 起没有握手，直接打 tools/list 也必须能用"""
    resp = mcp.call("tools/list")
    assert "result" in resp and resp["result"]["tools"]
    assert resp["result"]["resultType"] == "complete"


def test_server_discover(mcp):
    res = mcp.call("server/discover")["result"]
    assert res["serverInfo"]["name"] == "ai-kanban" and len(res["tools"]) >= 10


def test_notification_gets_no_reply(mcp):
    """通知不能回包：先发通知、再发请求，第一个读到的必须是对请求的响应"""
    mcp.call("notifications/initialized", notify=True)
    resp = mcp.call("ping")
    assert resp.get("id") == 1 and "result" in resp


def test_malformed_json_does_not_kill_server(mcp):
    mcp.send_raw("{ 这不是 JSON")
    bad = mcp.read()
    assert bad["error"]["code"] == -32700
    assert mcp.call("ping")["result"] is not None, "解析错误后服务必须还活着"


def test_unknown_method_returns_jsonrpc_error(mcp):
    resp = mcp.call("no/such/method")
    assert resp["error"]["code"] == -32601


def test_resources_and_prompts_are_empty_not_error(mcp):
    assert mcp.call("resources/list")["result"]["resources"] == []
    assert mcp.call("prompts/list")["result"]["prompts"] == []


# ---------------- 工具目录 ----------------

def test_tool_schemas_are_wellformed(mcp):
    tools = mcp.call("tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names)), "工具名不能重复"
    for t in tools:
        assert t["name"].startswith("kanban_"), t["name"]
        assert t["description"] and len(t["description"]) > 10, f"{t['name']} 描述太短"
        schema = t["inputSchema"]
        assert schema["type"] == "object" and "properties" in schema
        for req in schema.get("required", []):
            assert req in schema["properties"], f"{t['name']} 的必填字段 {req} 没定义"


def test_write_tools_use_enums_for_status(mcp):
    """状态/优先级必须给 enum，避免模型自己编值"""
    tools = {t["name"]: t for t in mcp.call("tools/list")["result"]["tools"]}
    add_task = tools["kanban_add_task"]["inputSchema"]["properties"]
    assert set(add_task["status"]["enum"]) == {"待办", "进行中", "已完成", "阻塞"}
    assert set(add_task["priority"]["enum"]) == {"高", "中", "低"}
    upd = tools["kanban_update_project"]["inputSchema"]["properties"]
    assert set(upd["status"]["enum"]) == {"规划中", "进行中", "已暂停", "已完成"}


# ---------------- 工具调用 ----------------

def test_first_run_shows_sample_data(mcp):
    """首次运行（数据文件不存在）返回示例数据，而不是空看板"""
    text, is_err = mcp.tool("kanban_list_projects")
    assert not is_err and "示例项目" in text


def test_empty_board_message(tmp_path):
    """看板真的空（文件存在但没有项目）时给引导语"""
    data_dir = tmp_path / "kanban-empty"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "projects.json").write_text('{"projects": []}', encoding="utf-8")
    client = McpClient(data_dir)
    try:
        text, is_err = client.tool("kanban_list_projects")
        assert not is_err and "还没有项目" in text and "kanban_create_project" in text
    finally:
        client.close()


def test_create_read_update_roundtrip(mcp, tmp_path):
    text, is_err = mcp.tool("kanban_create_project", {
        "name": "MCP 测试项目", "status": "进行中", "priority": "高", "progress": 10,
        "background": "用来验证代理能直接读写看板", "next_action": "写第一版接口",
    })
    assert not is_err, text
    assert "已创建项目" in text
    pid = last_id(text)

    brief, _ = mcp.tool("kanban_list_projects")
    assert "MCP 测试项目" in brief and pid in brief

    detail, _ = mcp.tool("kanban_get_project", {"project_id": pid})
    assert "写第一版接口" in detail and "用来验证代理能直接读写看板" in detail

    t_text, _ = mcp.tool("kanban_add_task", {"project_id": pid, "title": "搭骨架", "priority": "高"})
    assert "新增任务" in t_text
    tid = last_id(t_text)

    u_text, _ = mcp.tool("kanban_update_task", {"task_id": tid, "status": "已完成"})
    assert "已完成" in u_text

    p_text, _ = mcp.tool("kanban_update_project", {"project_id": pid, "progress": 60, "next_action": "补测试"})
    assert "补测试" in p_text or "已更新" in p_text

    detail2, _ = mcp.tool("kanban_get_project", {"project_id": pid})
    assert "60%" in detail2 and "已完成" in detail2

    # 数据真的落盘了（不是只活在内存里）
    on_disk = json.loads((tmp_path / "kanban-mcp" / "projects.json").read_text(encoding="utf-8"))
    project = next(p for p in on_disk["projects"] if p["id"] == pid)
    assert project["progress"] == 60 and project["tasks"][0]["status"] == "已完成"


def test_find_project_by_name(mcp):
    mcp.tool("kanban_create_project", {"name": "独特名字的系统"})
    text, is_err = mcp.tool("kanban_find_project", {"query": "独特名字"})
    assert not is_err and "独特名字的系统" in text
    empty, _ = mcp.tool("kanban_find_project", {"query": "不存在的名字"})
    assert "没有匹配" in empty


def test_handoff_and_agents_md(mcp):
    pid = last_id(mcp.tool("kanban_create_project",
                           {"name": "交接用项目", "background": "背景", "next_action": "下一步"})[0])
    handoff, is_err = mcp.tool("kanban_handoff", {"project_id": pid})
    assert not is_err and "交接摘要" in handoff and "下一步" in handoff

    md, _ = mcp.tool("kanban_agents_md", {"project_id": pid})
    assert "# AGENTS.md" in md


def test_stats_reports_weak_projects(mcp):
    mcp.tool("kanban_create_project", {"name": "没填交接的项目"})
    text, is_err = mcp.tool("kanban_stats")
    assert not is_err and "项目总数" in text


def test_delete_task(mcp):
    pid = last_id(mcp.tool("kanban_create_project", {"name": "删除测试"})[0])
    tid = last_id(mcp.tool("kanban_add_task", {"project_id": pid, "title": "待删"})[0])
    text, is_err = mcp.tool("kanban_delete_task", {"task_id": tid})
    assert not is_err and "已删除" in text
    detail, _ = mcp.tool("kanban_get_project", {"project_id": pid})
    assert "待删" not in detail


# ---------------- 错误处理 ----------------

def test_add_task_schema_has_refs(mcp):
    """任务要能带上相关代码位置，否则接手的人还得自己翻一遍"""
    tools = {t["name"]: t for t in mcp.call("tools/list")["result"]["tools"]}
    props = tools["kanban_add_task"]["inputSchema"]["properties"]
    assert "refs" in props and props["refs"]["type"] == "array"
    assert "refs" in tools["kanban_update_task"]["inputSchema"]["properties"]


def test_add_task_with_refs_roundtrip(mcp):
    pid = mcp.tool("kanban_create_project", {"name": "带位置的活"})[0].split("[")[-1].split("]")[0]
    text, is_err = mcp.tool("kanban_add_task",
                            {"project_id": pid, "title": "改判定逻辑", "refs": ["app/main.py:120"]})
    assert not is_err and "app/main.py:120" in text, text

    detail, _ = mcp.tool("kanban_get_project", {"project_id": pid})
    assert "涉及：app/main.py:120" in detail


def test_next_task_tool(mcp):
    """接手时最该先问的问题：现在能动手的是哪个"""
    pid = last_id(mcp.tool("kanban_create_project", {"name": "有依赖的活"})[0])
    a = last_id(mcp.tool("kanban_add_task", {"project_id": pid, "title": "搭骨架"})[0])
    mcp.tool("kanban_add_task", {"project_id": pid, "title": "联调", "depends_on": [a]})

    text, is_err = mcp.tool("kanban_next_task", {"project_id": pid})
    assert not is_err and "搭骨架" in text and "联调" not in text.split("被前置挡住")[0], text
    assert "等：搭骨架" in text

    mcp.tool("kanban_update_task", {"task_id": a, "status": "已完成"})
    text2, _ = mcp.tool("kanban_next_task", {"project_id": pid})
    assert "联调" in text2.split("被前置挡住")[0], "前置做完后就该轮到下一个"


def test_cycle_via_mcp_is_rejected(mcp):
    pid = last_id(mcp.tool("kanban_create_project", {"name": "成环"})[0])
    a = last_id(mcp.tool("kanban_add_task", {"project_id": pid, "title": "A"})[0])
    b = last_id(mcp.tool("kanban_add_task", {"project_id": pid, "title": "B", "depends_on": [a]})[0])
    text, is_err = mcp.tool("kanban_update_task", {"task_id": a, "depends_on": [b]})
    assert is_err and ("循环" in text or "依赖" in text), text


def test_unknown_tool_is_tool_error_not_crash(mcp):
    text, is_err = mcp.tool("kanban_not_exist")
    assert is_err and "未知工具" in text
    assert mcp.call("ping")["result"] is not None


def test_missing_project_gives_actionable_error(mcp):
    text, is_err = mcp.tool("kanban_get_project", {"project_id": "nope"})
    assert is_err and "找不到项目" in text and "kanban_list_projects" in text


def test_invalid_status_is_rejected_by_whitelist(mcp):
    text, is_err = mcp.tool("kanban_create_project", {"name": "非法状态", "status": "瞎写"})
    assert is_err, "非法状态必须被白名单挡下，不能写进看板"


def test_missing_required_field_is_rejected(mcp):
    text, is_err = mcp.tool("kanban_add_task", {"project_id": "x"})
    assert is_err


# ---------------- 跨进程写安全 ----------------

def test_two_mcp_processes_do_not_lose_writes(tmp_path):
    """桌面应用和 MCP 服务会同时写同一份数据 —— 跨进程锁必须挡住丢更新"""
    data_dir = tmp_path / "kanban-race"
    a = McpClient(data_dir)
    b = McpClient(data_dir)
    try:
        n = 8
        for i in range(n):
            a.call("tools/call", {"name": "kanban_create_project",
                                  "arguments": {"name": f"进程A-{i}"}})
            b.call("tools/call", {"name": "kanban_create_project",
                                  "arguments": {"name": f"进程B-{i}"}})
        on_disk = json.loads((data_dir / "projects.json").read_text(encoding="utf-8"))
        names = [p["name"] for p in on_disk["projects"]]
        missing = [f"进程{x}-{i}" for x in "AB" for i in range(n) if f"进程{x}-{i}" not in names]
        assert not missing, f"跨进程丢数据：{missing}（共 {len(names)} 条）"
    finally:
        a.close()
        b.close()


def test_cross_process_lock_blocks_a_second_writer(tmp_path):
    """锁真的在起作用：另一个进程持锁时，本进程写入应等待而不是直接覆盖"""
    data_dir = tmp_path / "kanban-lock"
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import os,sys,time;"
         f"os.environ['KANBAN_DATA_DIR']=r'{data_dir}';"
         f"sys.path.insert(0, r'{ROOT}');"
         "from app.main import _cross_process_lock;"
         "\nwith _cross_process_lock(5):\n    time.sleep(2.0)"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(1.0)          # 让子进程先拿到锁
    env = dict(os.environ, KANBAN_DATA_DIR=str(data_dir), KANBAN_NO_SCHEDULER="1")
    code = (
        "import sys,time;"
        f"sys.path.insert(0, r'{ROOT}');"
        "from app.main import mutate;"
        "t=time.time();"
        "mutate(lambda d: (d.setdefault('projects', []).append({'id':'x','name':'等待后写入'}) or True, None));"
        "print(round(time.time()-t, 2))"
    )
    t0 = time.time()
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=30)
    elapsed = time.time() - t0
    holder.wait(timeout=10)
    assert out.returncode == 0, out.stderr
    assert elapsed >= 0.8, f"没有等到锁就写了（耗时 {elapsed:.2f}s）"
    data = json.loads((data_dir / "projects.json").read_text(encoding="utf-8"))
    assert any(p["name"] == "等待后写入" for p in data["projects"])


# ---------------- 实时直链（本机取活数据，而不是吃快照） ----------------

def test_initialize_instructions_advertise_local_url(mcp):
    resp = mcp.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    text = resp["result"]["instructions"]
    assert "/api/context/" in text
    assert "127.0.0.1" in text, "直链只能指向本机，不能引导智能体去公网取数据"


def test_context_tool_returns_live_link(mcp):
    _, err = mcp.tool("kanban_create_project", {"name": "直链测试项目", "status": "进行中"})
    assert not err
    text, err = mcp.tool("kanban_find_project", {"query": "直链测试项目"})
    assert not err
    pid = text.split("[")[-1].split("]")[0]

    text, err = mcp.tool("kanban_context", {"project_id": pid})
    assert not err
    assert f"127.0.0.1:18320/api/context/{pid}" in text


# ---------------- 字段保真：只传要改的字段时，不能把没提的字段抹掉 ----------------

def _task_on_disk(data_dir: Path, tid: str) -> dict:
    data = json.loads((data_dir / "projects.json").read_text(encoding="utf-8"))
    for p in data["projects"]:
        for t in p.get("tasks") or []:
            if t.get("id") == tid:
                return t
    raise AssertionError(f"落盘数据里找不到任务 {tid}")


def test_add_task_honours_due_date(tmp_path):
    data_dir = tmp_path / "kanban-mcp-due"
    c = McpClient(data_dir)
    try:
        pid = last_id(c.tool("kanban_create_project", {"name": "截止日保真"})[0])
        text, is_err = c.tool("kanban_add_task", {
            "project_id": pid, "title": "带截止日的任务", "due_date": "2030-05-06"})
        assert not is_err, text
        assert _task_on_disk(data_dir, last_id(text))["due_date"] == "2030-05-06"
    finally:
        c.close()


def test_update_task_preserves_fields_it_was_not_given(tmp_path):
    """回归：`t_update_task` 用 pick() 逐字段回填，早期漏了 due_date ——
    智能体每改一次状态就把截止日期静默清空，而且全程没有任何测试变红。"""
    data_dir = tmp_path / "kanban-mcp-preserve"
    c = McpClient(data_dir)
    try:
        pid = last_id(c.tool("kanban_create_project", {"name": "回填保真"})[0])
        tid = last_id(c.tool("kanban_add_task", {
            "project_id": pid, "title": "别抹我的字段", "priority": "高",
            "due_date": "2030-05-06", "refs": ["app/main.py:120"]})[0])
        assert _task_on_disk(data_dir, tid)["refs"] == ["app/main.py:120"]

        text, is_err = c.tool("kanban_update_task", {"task_id": tid, "status": "进行中"})
        assert not is_err, text
        task = _task_on_disk(data_dir, tid)
        assert task["status"] == "进行中"
        assert task["due_date"] == "2030-05-06", "只改状态不该顺手清掉截止日期"
        assert task["refs"] == ["app/main.py:120"], "只改状态不该顺手清掉代码位置"
        assert task["priority"] == "高", "只改状态不该顺手把优先级降成中"

        c.tool("kanban_update_task", {"task_id": tid, "due_date": ""})
        assert _task_on_disk(data_dir, tid)["due_date"] == "", "显式传空串才等于清掉截止日"
    finally:
        c.close()


def test_due_date_is_advertised_in_tool_schemas(mcp):
    """schema 里没有的字段，客户端不会传（且 additionalProperties=False 会被拒）"""
    tools = {t["name"]: t for t in mcp.call("tools/list")["result"]["tools"]}
    for name in ("kanban_add_task", "kanban_update_task"):
        assert "due_date" in tools[name]["inputSchema"]["properties"], name


