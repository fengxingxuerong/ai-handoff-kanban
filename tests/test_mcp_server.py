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


def test_server_version_matches_app_version(mcp):
    """MCP 报的版本必须与看板本体同源。

    这里曾经手填 "1.0.0"，而应用已经升到 1.1.0 —— 客户端拿 serverInfo.version
    判断能力就会误判。所以断言的是"两边一致"，而不是某个具体数字。
    """
    sys.path.insert(0, str(ROOT))
    from app.version import APP_VERSION

    resp = mcp.call("initialize", {"protocolVersion": "2025-06-18"})
    assert resp["result"]["serverInfo"]["version"] == APP_VERSION


def test_local_link_port_follows_kanban_port_env(mcp):
    """交接包/指令里的直链端口与本体同一个口径（读 KANBAN_PORT）"""
    resp = mcp.call("initialize", {"protocolVersion": "2025-06-18"})
    port = os.environ.get("KANBAN_PORT", "18320")
    assert f"127.0.0.1:{port}" in resp["result"]["instructions"]


def test_stdio_is_utf8_even_when_the_client_sets_nothing(tmp_path):
    """客户端不会替你设 PYTHONIOENCODING，而 MCP 的 stdio 规范就是 UTF-8。

    中文 Windows 上子进程的三条标准流默认跟随控制台码页（cp936）：
    出方向 `ensure_ascii=False` 的中文工具描述被编成 GBK，客户端按 UTF-8 解 → 解码异常，
    整个工具目录等于没发布；入方向的中文标题被按 GBK 解 → 静默写进看板一堆乱码。
    所以服务自己必须把标准流钉成 UTF-8，这条用例**故意**把变量从环境里摘掉。
    """
    data_dir = tmp_path / "kanban-mcp-utf8"
    data_dir.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    env["KANBAN_DATA_DIR"] = str(data_dir)
    env["KANBAN_NO_SCHEDULER"] = "1"
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "kanban_create_project",
                      "arguments": {"name": "入方向中文标题测试", "status": "进行中"}}}
    p = subprocess.run([sys.executable, str(SERVER)],
                       input=(json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"),
                       capture_output=True, cwd=str(ROOT), env=env, timeout=90)
    assert p.returncode == 0
    out = p.stdout.decode("utf-8")          # 不解得动就是出方向被编成了码页字节
    assert "入方向中文标题测试" in out, "响应里的中文丢了"
    stored = json.loads((data_dir / "projects.json").read_text(encoding="utf-8"))
    assert [x["name"] for x in stored["projects"]] == ["入方向中文标题测试"], \
        f"写进盘上的标题被解错了：{stored}"


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


def test_resources_and_prompts_answer_even_with_an_empty_board(mcp):
    """空看板上也要答得出来（不是 error、也不是空数组）：
    客户端在列表阶段就按"有没有这个能力"决定要不要显示入口。"""
    res = mcp.call("resources/list")["result"]
    assert "kanban://projects" in {r["uri"] for r in res["resources"]}, res
    assert len(mcp.call("prompts/list")["result"]["prompts"]) == 3
    # 指向不存在项目的资源仍然是错误，不能返回一份空壳装作成功
    resp = mcp.call("resources/read", {"uri": "kanban://project/nope/handoff"})
    assert resp.get("error") or resp["result"].get("isError"), resp


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


def test_unknown_tool_is_a_jsonrpc_protocol_error(mcp):
    """「工具名不存在」是调用方的形状错误 → JSON-RPC -32602，不是工具执行失败（isError）。

    规范把两类错误分开：-32602 给"你叫了一个不存在的东西"，isError 给"这个工具跑了但没成"。
    以前都走 isError，客户端于是把"名字写错"当成"这个工具坏了"来显示。
    """
    resp = mcp.call("tools/call", {"name": "kanban_not_exist", "arguments": {}})
    assert resp.get("error", {}).get("code") == -32602, resp
    assert "未知工具" in resp["error"]["message"], resp
    # 入参校验不过仍然是工具错误 —— 那条要让模型自己纠
    text, is_err = mcp.tool("kanban_add_task", {"project_id": "x"})
    assert is_err and "缺少必填参数" in text, text
    assert mcp.call("ping")["result"] is not None, "协议错误之后服务必须还能用"


def test_server_discover_matches_the_stateless_spec(mcp):
    """无状态模式下客户端靠 server/discover 选协议版本：只报一个 protocolVersion 不够。

    规范（2026-07-28）要求 discover 回 supportedVersions，并且 `resultType:"complete"`
    的结果带缓存提示；serverInfo 走 `_meta`。旧客户端读的那份顶层字段仍然保留。
    """
    res = mcp.call("server/discover")["result"]
    assert res["supportedVersions"] and "2026-07-28" in res["supportedVersions"]
    assert res["protocolVersion"] in res["supportedVersions"]
    assert res["ttlMs"] > 0 and res["cacheScope"]
    assert res["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "ai-kanban"
    # initialize 与 discover 报的能力必须是同一份，不然客户端按哪一份走都不对
    init = mcp.call("initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["capabilities"] == res["capabilities"], "两处 capabilities 漂移了"


def test_redact_is_on_by_default_for_agent_outputs(mcp):
    """智能体拿到的交接包/接手包默认脱敏：这些文本会进别的机器上的上下文窗口。

    以前 MCP 这条路根本没有 redact 参数 —— 同一个模型走 HTTP 能要求脱敏，
    走 MCP 就只能拿原文，边界形同虚设。
    """
    text, err = mcp.tool("kanban_create_project", {
        "name": "脱敏默认值", "status": "进行中",
        "background": "登录用的第三方 api_key: zzzz9998887776666，联系人 dev@corp.example.com"})
    assert not err, text
    pid = last_id(text)

    safe, err = mcp.tool("kanban_handoff", {"project_id": pid})
    assert not err, safe
    assert "zzzz9998887776666" not in safe and "********" in safe, "默认没脱敏"
    assert "dev@corp.example.com" not in safe

    raw, err = mcp.tool("kanban_handoff", {"project_id": pid, "redact": False})
    assert not err, raw
    assert "zzzz9998887776666" in raw, "显式传 false 应当拿原文"

    ctx, err = mcp.tool("kanban_context", {"project_id": pid})
    assert not err, ctx
    assert "zzzz9998887776666" not in ctx, "接手包同样要默认脱敏"
    assert "pid" in ctx or pid in ctx, "项目 id 不能被一起盖掉"


def test_boolean_arg_type_is_enforced(mcp):
    """schema 里声明了 boolean 就得真的校验：给字符串要说清楚，而不是悄悄当"没给" """
    resp = mcp.call("tools/call", {"name": "kanban_handoff",
                                   "arguments": {"project_id": "随便", "redact": "yes"}})
    text = "\n".join(c.get("text", "") for c in resp["result"].get("content", []))
    assert resp["result"]["isError"] is True, resp
    assert "必须是布尔值" in text, text


def test_search_limit_schema_matches_the_actual_clamp(mcp):
    """schema 声明的上限就是代码里那道夹：写 200 而只给 50 条，模型以为"就这么多" """
    tools = {t["name"]: t for t in mcp.call("tools/list")["result"]["tools"]}
    search = tools["kanban_search_tasks"]["inputSchema"]["properties"]["limit"]
    assert search["maximum"] == 50, search
    today = tools["kanban_today"]["inputSchema"]["properties"]["limit"]
    assert today["maximum"] == 200, "today 内部夹的是 200，别一起改掉"


def test_list_projects_warns_after_self_heal(tmp_path):
    """数据刚自愈过时，代理必须在动手之前知道这件事"""
    data_dir = tmp_path / "kanban-mcp-heal"
    data_dir.mkdir(parents=True)
    (data_dir / "projects.json").write_text("{ 不是 JSON", encoding="utf-8")
    c = McpClient(data_dir)
    try:
        c.tool("kanban_create_project", {"name": "救回来的项目"})
        text, err = c.tool("kanban_list_projects")
        assert not err, text
        assert text.startswith("⚠") and "写入前请先与用户确认" in text, f"没提示数据是救回来的：{text[:200]}"
        # 标记是一次性的：报过一次就该闭嘴，否则每条回复都在喊
        text2, _ = c.tool("kanban_list_projects")
        assert not text2.startswith("⚠"), f"恢复标记没被清掉：{text2[:120]}"
    finally:
        c.close()


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



# ---------------- 读写属性声明（客户端靠它决定要不要二次确认） ----------------

def _mcp_server_mod():
    """在进程内 import mcp_server 拿目录元数据（它自带 __main__ 守卫，不会起服务）"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import mcp_server
    return mcp_server


def test_every_tool_is_registered_with_a_kind():
    """TOOL_KIND 漏登记时注解会退化成"写"（保守但不诚实），所以直接要求全部登记。"""
    m = _mcp_server_mod()
    names = {t["name"] for t in m.TOOLS}
    assert names == set(m.TOOL_KIND), (
        f"工具目录与读写登记表不一致：{names ^ set(m.TOOL_KIND)}")
    assert names == set(m.HANDLERS), "有工具没实现，或有实现没声明"


def test_annotations_match_the_declared_kind(mcp):
    m = _mcp_server_mod()
    tools = {t["name"]: t for t in mcp.call("tools/list")["result"]["tools"]}
    for name, tool in tools.items():
        kind = m.TOOL_KIND[name][0]
        assert tool["annotations"] == m.ANNOTATIONS[kind], name
        assert tool["title"], f"{name} 没有人类可读标题"
    assert tools["kanban_list_projects"]["annotations"]["readOnlyHint"] is True
    assert tools["kanban_delete_task"]["annotations"]["destructiveHint"] is True, \
        "删除必须标成破坏性，客户端才会弹二次确认"


def test_readonly_mode_hides_write_tools_from_the_catalog(tmp_path):
    """只读模式过去只在**调用时**拒绝：客户端看得见工具还是会去调，一次次拿到错误，
    像服务坏了。目录里直接不给看才是正解（调用侧那道闸保留不动）。"""
    data_dir = tmp_path / "kanban-ro"
    data_dir.mkdir(parents=True)
    (data_dir / "config.json").write_text(
        json.dumps({"ai": {"mcp_readonly": True, "endpoints": [], "active_model": ""}}),
        encoding="utf-8")
    c = McpClient(data_dir)
    try:
        names = {t["name"] for t in c.call("tools/list")["result"]["tools"]}
        assert "kanban_list_projects" in names and "kanban_stats" in names
        assert "kanban_add_task" not in names, "写工具还挂在只读目录里"
        assert "kanban_delete_task" not in names
        assert "kanban_agents_md" not in names, "生成类会花真实配额，只读模式一并摘掉"
        text, is_err = c.tool("kanban_add_task", {"project_id": "x", "title": "偷偷写"})
        assert is_err and "只读" in text, text
        text, is_err = c.tool("kanban_stats", {})
        assert not is_err, text
    finally:
        c.close()


# ---------------- 入参校验：声明了就得真的执行 ----------------

def test_misspelled_argument_is_rejected_not_ignored(mcp):
    """过去 required/enum/additionalProperties 只是装饰：字段名拼错照样"调用成功"，
    模型看到的是"我明明改了却没改"。"""
    text, is_err = mcp.tool("kanban_stats", {"porject_id": "abc"})
    assert is_err, "拼错的参数被静默忽略了"
    assert "porject_id" in text and "可用参数" in text, f"错误信息得能指路：{text}"

    proj, _ = mcp.tool("kanban_create_project", {"name": "校验靶项目"})
    pid = proj.split("[")[-1].split("]")[0]
    text, is_err = mcp.tool("kanban_add_task", {"project_id": pid, "titel": "标题字段拼错"})
    # 必填项缺失先报，报的正是被拼错的那个字段名 —— 足够指路
    assert is_err and "title" in text, text


def test_missing_required_argument_is_rejected(mcp):
    text, is_err = mcp.tool("kanban_get_project", {})
    assert is_err and "project_id" in text, text
    text, is_err = mcp.tool("kanban_get_project", {"project_id": "   "})
    assert is_err and "project_id" in text, "空白字符串也算没传"


def test_enum_and_range_are_enforced(mcp):
    proj, _ = mcp.tool("kanban_create_project", {"name": "枚举靶项目"})
    pid = proj.split("[")[-1].split("]")[0]
    text, is_err = mcp.tool("kanban_update_project", {"project_id": pid, "status": "摸鱼中"})
    assert is_err and "摸鱼中" in text, f"非法枚举值应被拒且回显原值：{text}"
    text, is_err = mcp.tool("kanban_update_project", {"project_id": pid, "progress": 180})
    assert is_err and "100" in text, text


# ---------------- 与 HTTP 对齐的四个新工具 ----------------

def test_today_tool_lists_actionable_tasks(mcp):
    proj, _ = mcp.tool("kanban_create_project", {"name": "今日工具项目", "status": "进行中"})
    pid = proj.split("[")[-1].split("]")[0]
    t1, _ = mcp.tool("kanban_add_task", {"project_id": pid, "title": "前置", "priority": "高"})
    tid1 = t1.split("[")[-1].split("]")[0]
    mcp.tool("kanban_add_task", {"project_id": pid, "title": "被挡住", "depends_on": [tid1]})

    text, is_err = mcp.tool("kanban_today", {})
    assert not is_err, text
    assert "前置" in text and "今日工具项目" in text
    assert "被挡住" not in text, "前置没完成的不该出现在今日待办里"


def test_search_tool_returns_task_ids(mcp):
    proj, _ = mcp.tool("kanban_create_project", {"name": "搜索靶项目"})
    pid = proj.split("[")[-1].split("]")[0]
    t, _ = mcp.tool("kanban_add_task", {"project_id": pid, "title": "重采样音频"})
    tid = t.split("[")[-1].split("]")[0]
    text, is_err = mcp.tool("kanban_search_tasks", {"query": "重采样"})
    assert not is_err, text
    assert tid in text and "搜索靶项目" in text, "拿到 id 才能接着调 update"


def test_log_progress_tool_appends_journal(tmp_path):
    """智能体干完一段活，得能自己把过程记录写进任务（界面同一条路是 /api/tasks/{tid}/journal）"""
    data_dir = tmp_path / "kanban-journal"
    c = McpClient(data_dir)
    try:
        proj, _ = c.tool("kanban_create_project", {"name": "过程记录靶"})
        pid = proj.split("[")[-1].split("]")[0]
        task, _ = c.tool("kanban_add_task", {"project_id": pid, "title": "要记一笔的任务"})
        tid = task.split("[")[-1].split("]")[0]

        text, is_err = c.tool("kanban_log_progress",
                             {"task_id": tid, "text": "定位到是采样率不匹配"})
        assert not is_err, text

        data = json.loads((data_dir / "projects.json").read_text(encoding="utf-8"))
        stored = [x for p in data["projects"] for x in (p.get("tasks") or []) if x.get("id") == tid][0]
        assert any("采样率不匹配" in e.get("text", "") for e in stored.get("journal") or []),             f"过程记录没落盘：{stored.get('journal')}"

        text, is_err = c.tool("kanban_log_progress", {"task_id": "不存在的任务", "text": "x"})
        assert is_err and "任务不存在" in text, text
        # 404 的原文来自主体（HTTP 那边 id 在 URL 里），代理这边必须把 id 补回去
        assert "不存在的任务" in text, f"错误里没说是哪个任务：{text}"
    finally:
        c.close()


def test_memory_add_tool_writes_into_memory_layer(mcp):
    """记忆层此前只有界面能写，智能体发现了坑却记不进来"""
    proj, _ = mcp.tool("kanban_create_project", {"name": "记忆靶项目"})
    pid = proj.split("[")[-1].split("]")[0]
    text, is_err = mcp.tool("kanban_memory_add",
                            {"project_id": pid, "type": "坑", "text": "8k 采样要重采样"})
    assert not is_err, text
    ctx, _ = mcp.tool("kanban_context", {"project_id": pid})
    assert "8k 采样要重采样" in ctx, f"记忆没被接手包召回：{ctx}"


def test_handoff_tool_reports_package_size(mcp):
    """智能体是把整包读进上下文窗口的，得知道它有多大、以及偏大时改走 kanban_context"""
    proj, _ = mcp.tool("kanban_create_project", {
        "name": "超长交接项目",
        "background": "背景行\n" * 400,
    })
    pid = proj.split("[")[-1].split("]")[0]
    text, is_err = mcp.tool("kanban_handoff", {"project_id": pid})
    assert not is_err, text
    assert "本包约" in text and "KB" in text, f"交接包尾部没有尺寸提示：{text[-200:]}"

    small, _ = mcp.tool("kanban_create_project", {"name": "小项目"})
    sid = small.split("[")[-1].split("]")[0]
    text2, _ = mcp.tool("kanban_handoff", {"project_id": sid})
    assert "本包约" not in text2, "正常长度不该打扰"


# ---------------- resources / prompts：把已有内容换个入口摆出来 ----------------

def _result(mcp, method, params=None):
    resp = mcp.call(method, params or {})
    assert "result" in resp, resp
    return resp["result"]


def test_resources_are_listed_and_readable(mcp):
    """交接内容摆成资源：客户端把它们渲染成用户可选项，不占每轮对话的工具目录"""
    pid = last_id(mcp.tool("kanban_create_project", {"name": "资源项目", "status": "进行中"})[0])
    resources = {r["uri"]: r for r in _result(mcp, "resources/list")["resources"]}
    assert "kanban://projects" in resources and "kanban://board/today" in resources
    handoff = f"kanban://project/{pid}/handoff"
    ctx = f"kanban://project/{pid}/context"
    assert handoff in resources and ctx in resources, list(resources)[:6]
    assert resources[handoff]["mimeType"] == "text/markdown"

    body = _result(mcp, "resources/read", {"uri": handoff})
    assert body["contents"][0]["uri"] == handoff
    assert "资源项目" in body["contents"][0]["text"]
    assert body["ttlMs"] and body["cacheScope"], "resultType:complete 的结果要带缓存提示"

    parsed = json.loads(_result(mcp, "resources/read", {"uri": ctx})["contents"][0]["text"])
    assert parsed["project"]["id"] == pid and parsed["code_reality"]["available"] is False

    templates = _result(mcp, "resources/templates/list")["resourceTemplates"]
    assert any("{pid}" in t["uriTemplate"] for t in templates)


def test_unknown_resource_and_prompt_are_protocol_errors(mcp):
    """规范：没有这个资源/提示词是调用方的形状错误 → -32602（客户端也接受 -32002，但我们只发一种）"""
    for method, params in (("resources/read", {"uri": "kanban://没有这个东西"}),
                           ("prompts/get", {"name": "nope"})):
        resp = mcp.call(method, params)
        assert resp.get("error", {}).get("code") == -32602, (method, resp)


def test_declared_capabilities_match_what_the_server_answers(mcp):
    """声明了 resources/prompts 才谈得上被发现；而且 initialize 与 discover 必须报同一份"""
    init = _result(mcp, "initialize", {"protocolVersion": "2025-06-18"})
    disc = _result(mcp, "server/discover")
    for caps in (init["capabilities"], disc["capabilities"]):
        assert {"tools", "resources", "prompts"} <= set(caps), caps
    assert init["capabilities"] == disc["capabilities"]
    # 能力声明与真能回答的方法要一致：声明了却没实现，客户端一调就报错
    assert "resources" in _result(mcp, "resources/list")
    assert "prompts" in _result(mcp, "prompts/list")


def test_prompts_offer_takeover_daily_and_wrapup(mcp):
    """三段提示词就是原本来写在 instructions 里的规矩，摆成用户可点的入口"""
    names = {p["name"] for p in _result(mcp, "prompts/list")["prompts"]}
    assert names == {"takeover", "daily", "wrapup"}
    mcp.tool("kanban_create_project", {"name": "接手演练", "status": "进行中"})

    got = _result(mcp, "prompts/get", {"name": "takeover", "arguments": {"project": "接手演练"}})
    text = got["messages"][0]["content"]["text"]
    assert got["messages"][0]["role"] == "user"
    assert "kanban_context" in text and "接手演练" in text, text[:160]

    daily = _result(mcp, "prompts/get", {"name": "daily", "arguments": {}})
    assert "现在就能动手" in daily["messages"][0]["content"]["text"]

    wrap = _result(mcp, "prompts/get", {"name": "wrapup", "arguments": {"project": "接手演练"}})
    for step in ("kanban_update_task", "kanban_log_progress", "kanban_memory_add",
                 "kanban_update_project"):
        assert step in wrap["messages"][0]["content"]["text"], step


def test_prompt_project_reference_must_be_unambiguous(mcp):
    mcp.tool("kanban_create_project", {"name": "同名项目"})
    mcp.tool("kanban_create_project", {"name": "同名项目"})
    resp = mcp.call("prompts/get", {"name": "takeover", "arguments": {"project": "同名"}})
    assert resp.get("error", {}).get("code") == -32602, resp
    assert "歧义" in resp["error"]["message"], resp


def test_readonly_mode_still_serves_resources(tmp_path):
    """只读模式关掉的是写工具；资源与提示词本来就是读，不该一起消失"""
    data_dir = tmp_path / "kanban-mcp-readonly-res"
    data_dir.mkdir(parents=True)
    cfg = data_dir / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"ai": {"endpoints": [], "mcp_readonly": True}},
                              ensure_ascii=False), encoding="utf-8")
    c = McpClient(data_dir)
    try:
        assert c.tool("kanban_create_project", {"name": "只读下不该建成"})[1], "只读模式没生效"
        assert "resources" in _result(c, "resources/list"), "只读模式把资源一起摘掉了"
        assert _result(c, "prompts/list")["prompts"]
    finally:
        c.close()


# ---------------- 创建幂等键 ----------------

def test_client_ref_dedupes_retried_create(mcp):
    """智能体超时重试是常态：同一个 client_ref 第二次必须拿回同一条而不是再建一条。

    重复任务不是"多一条数据"这么轻：它会跟着进交接包、进拓扑序，
    事后只能靠"标题一模一样"人肉分辨哪条是真的。
    """
    pid = last_id(mcp.tool("kanban_create_project", {"name": "幂等项目"})[0])
    first, err = mcp.tool("kanban_add_task", {
        "project_id": pid, "title": "只应该存在一条", "client_ref": "op-2026-09-19-1"})
    assert not err, first
    tid = last_id(first)
    again, err = mcp.tool("kanban_add_task", {
        "project_id": pid, "title": "只应该存在一条", "client_ref": "op-2026-09-19-1"})
    assert not err and last_id(again) == tid, f"重试建出了新任务：{again}"

    other, _ = mcp.tool("kanban_add_task", {
        "project_id": pid, "title": "另一条", "client_ref": "op-2026-09-19-2"})
    assert last_id(other) != tid, "不同的键应当各建一条"

    detail = _result(mcp, "tools/call", {"name": "kanban_get_project",
                                         "arguments": {"project_id": pid}})
    text = detail["content"][0]["text"]
    assert text.count("只应该存在一条") == 1, "重复项还是落盘了"
    assert "另一条" in text


def test_create_without_client_ref_still_creates_twice(mcp):
    """不传键时行为不变 —— 幂等是可选的，不能把正常连建两条也吞掉"""
    pid = last_id(mcp.tool("kanban_create_project", {"name": "无键项目"})[0])
    a = last_id(mcp.tool("kanban_add_task", {"project_id": pid, "title": "重复标题"})[0])
    b = last_id(mcp.tool("kanban_add_task", {"project_id": pid, "title": "重复标题"})[0])
    assert a != b


# ---------------- 记忆列/删 与 删项目 ----------------

def test_memories_can_be_listed_filtered_and_deleted(mcp):
    """以前智能体只能往记忆层盲写：写进去几条、id 是什么、想撤一条，全都不知道"""
    pid = last_id(mcp.tool("kanban_create_project", {"name": "记忆可见性"})[0])
    mcp.tool("kanban_memory_add", {"project_id": pid, "type": "决策", "text": "导出统一走流式"})
    mcp.tool("kanban_memory_add", {"project_id": pid, "type": "坑", "text": "Windows 文件锁会误报"})
    mcp.tool("kanban_memory_add", {"type": "事实", "text": "跨项目的通用约定"})

    all_rows, err = mcp.tool("kanban_list_memories")
    assert not err and "3 条" in all_rows, all_rows
    only_proj, _ = mcp.tool("kanban_list_memories", {"project_id": pid})
    assert "2 条" in only_proj and "通用约定" not in only_proj, only_proj
    pits, _ = mcp.tool("kanban_list_memories", {"type": "坑"})
    assert "文件锁" in pits and "流式" not in pits, pits
    hits, _ = mcp.tool("kanban_list_memories", {"query": "流式"})
    assert "1 条" in hits, hits

    mid = hits.split("[")[1].split("]")[0]
    text, is_err = mcp.tool("kanban_delete_memory", {"memory_id": mid})
    assert not is_err, text
    assert "流式" not in mcp.tool("kanban_list_memories")[0]
    again, is_err = mcp.tool("kanban_delete_memory", {"memory_id": mid})
    assert is_err and "记忆不存在" in again and mid in again, f"重复删除应当报错并说清是哪条：{again}"


def test_listed_memories_are_redacted_by_default(mcp):
    pid = last_id(mcp.tool("kanban_create_project", {"name": "记忆脱敏"})[0])
    mcp.tool("kanban_memory_add", {"project_id": pid, "text": "预发 token=zzzz9998887776666"})
    safe, _ = mcp.tool("kanban_list_memories", {"project_id": pid})
    assert "zzzz9998887776666" not in safe and "********" in safe, safe
    raw, _ = mcp.tool("kanban_list_memories", {"project_id": pid, "redact": False})
    assert "zzzz9998887776666" in raw, "显式传 false 才给原文"


def test_memory_list_reports_truncation(mcp):
    """一次列 50 条封顶，超了要说 —— 不然模型以为记忆层就这么点东西"""
    pid = last_id(mcp.tool("kanban_create_project", {"name": "记忆很多"})[0])
    for i in range(55):
        mcp.tool("kanban_memory_add", {"project_id": pid, "text": f"要点 {i:03d}"})
    text, err = mcp.tool("kanban_list_memories", {"project_id": pid})
    assert not err
    assert "55 条" in text, text[:120]
    assert "另有 5 条未列出" in text, "超上限却没告诉调用方"


def test_delete_project_asks_before_destroying(mcp):
    """删项目是全看板破坏性最大的一步：第一次调用只报清单，确认后才真删。

    没有软删除也没有回收站，唯一退路是拿备份**整份**恢复（会把其它项目一起回滚），
    所以这里宁可多一次往返。
    """
    a = last_id(mcp.tool("kanban_create_project", {"name": "要删掉的项目"})[0])
    b = last_id(mcp.tool("kanban_create_project", {"name": "留下不要动"})[0])
    mcp.tool("kanban_add_task", {"project_id": a, "title": "没做完的活"})
    mcp.tool("kanban_add_task", {"project_id": a, "title": "完成的活", "status": "已完成"})
    mcp.tool("kanban_memory_add", {"project_id": a, "text": "这个项目的约定"})

    warn, is_err = mcp.tool("kanban_delete_project", {"project_id": a})
    assert is_err, "没给 confirm 就直接删了"
    assert "2 条任务" in warn and "1 条还没做完" in warn and "1 条项目记忆" in warn, warn
    assert "confirm=true" in warn, warn
    assert not mcp.tool("kanban_get_project", {"project_id": a})[1], "拒绝阶段不该动数据"

    done, is_err = mcp.tool("kanban_delete_project", {"project_id": a, "confirm": True})
    assert not is_err, done
    assert "已删除项目" in done and "2 条任务" in done, done
    assert mcp.tool("kanban_get_project", {"project_id": a})[1], "项目没删掉"
    assert "这个项目的约定" not in mcp.tool("kanban_list_memories")[0], "孤儿记忆没跟着清"
    assert not mcp.tool("kanban_get_project", {"project_id": b})[1], "别的项目被牵连了"


def test_readonly_mode_hides_destructive_but_keeps_reads(tmp_path):
    """只读模式该摘掉的：删项目、删记忆；该留下的：查记忆（它本来就是读）"""
    data_dir = tmp_path / "kanban-mcp-readonly-del"
    cfg = data_dir / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"ai": {"endpoints": [], "mcp_readonly": True}},
                              ensure_ascii=False), encoding="utf-8")
    c = McpClient(data_dir)
    try:
        names = {t["name"] for t in _result(c, "tools/list")["tools"]}
        assert "kanban_list_memories" in names
        for gone in ("kanban_delete_project", "kanban_delete_memory", "kanban_delete_task"):
            assert gone not in names, gone
        _, is_err = c.tool("kanban_delete_project", {"project_id": "x", "confirm": True})
        assert is_err and "只读" in _, f"只读模式下竟然执行了删除：{_}"
    finally:
        c.close()
