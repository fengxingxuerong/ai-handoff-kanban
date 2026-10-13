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


@pytest.mark.parametrize("raw", ["[abc]", "[abc]:80", "[ lookout]"])
def test_host_fallback_branch_still_rejects_foreign_names(server, raw):
    """_host_of 的 ValueError 兜底分支（2026-09-29 补）。

    2026-09-26 重构后主机解析主路径走 urlparse：`[::1]:18320`、`[fe80::1]`、
    `[evil.example.com]` 全由主路径判定，兜底分支成了既有用例的盲区 ——
    变异 guard_host_ipv6（把兜底改回历史缺陷写法）因此存活没人红。
    只有"方括号里不是合法 IP"的输入（urlparse 抛 ValueError）才会落到兜底：
    这里用 `[abc]` 这类输入钉住它 —— 兜底解析出的主机名同样必须过白名单。
    """
    assert server.get("/api/health", host=raw)[0] == 403


def test_export_save_rejects_system_dir(fresh_server):
    status, _ = fresh_server.post("/api/export/save", {
        "filename": "x.md", "content": "# hi", "path": r"C:\Windows\evil.md"})
    assert status == 400


# ---------------- 判定本体拆到 app/security.py 后按矩阵穷举（2026-10-14） ----------------
# 以前这些判定埋在 main.py 里只能靠 TestClient 端到端间接验证，"哪些 Host 放行、
# Token 覆盖哪些方法、哪些路径禁写"这个矩阵没人逐格看过。

def test_request_verdict_matrix_is_exhaustive():
    """Host / Origin / Token 三关的判定矩阵（纯函数，直接打边界）。

    搬出来之后能穷举的正是这些格子：Host 缺失、端口、大小写、方括号、跨站 Origin、
    本机 Origin、Token 只挡写方法、Token 配了但头不对、没配 Token 时完全放行。
    """
    from app.security import request_verdict

    # Host 关：空 Host 放行（非浏览器客户端），端口与大小写不影响判定
    assert request_verdict(host="") == (0, "")
    assert request_verdict(host="127.0.0.1:18320") == (0, "")
    assert request_verdict(host="LOCALHOST") == (0, "")
    assert request_verdict(host="[::1]:18320") == (0, "")
    # 外站 Host 直接 403，且不再往下判 Origin/Token
    code, why = request_verdict(host="evil.example.com", origin="http://127.0.0.1",
                                method="POST", token_expected="t", token_header="wrong")
    assert (code, why) == (403, "拒绝非本机 Host 访问")

    # Origin 关：没有 Origin 不当跨站；本机 Origin 放行；外站 403
    assert request_verdict(host="127.0.0.1") == (0, "")
    assert request_verdict(host="127.0.0.1", origin="http://localhost:18320") == (0, "")
    assert request_verdict(host="127.0.0.1", origin="https://evil.example.com")[0] == 403

    # Token 关：只挡写方法；GET 不受影响；头不对 401；没配 Token 时一切照旧
    assert request_verdict(method="POST", token_expected="s3cret")[0] == 401
    assert request_verdict(method="POST", token_expected="s3cret",
                           token_header="s3cret") == (0, "")
    assert request_verdict(method="GET", token_expected="s3cret") == (0, "")
    assert request_verdict(method="PATCH", token_expected="s3cret", token_header="")[0] == 401
    assert request_verdict(method="POST", token_expected=None) == (0, "")


def test_forbidden_target_fails_closed_when_resolve_raises(monkeypatch, tmp_path):
    """解析失败必须**当成禁止**（fail-closed），不能因为"我没算出来"就放行。

    这条分支以前是未覆盖行：写盘黑名单算不出真实路径时，最该做的是拒绝。
    顺带覆盖 forbidden_roots 里"某个 root 解析失败跳过"的 continue 分支。
    """
    from app import security

    def boom(self, *a, **kw):
        raise OSError("解析失败（模拟）")

    monkeypatch.setattr(Path, "resolve", boom)
    assert security.is_forbidden_target(Path("D:/x/y.md")) is True, \
        "路径解析失败时放行 = 黑名单形同虚设"


def test_forbidden_target_allows_temp_but_blocks_appdata_and_system(monkeypatch, tmp_path):
    """临时目录放行、AppData（非临时）与系统目录禁止 —— 纯函数版本，可逐格断言。"""
    from app import security

    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.delenv("TMP", raising=False)   # 只设 TEMP 也够（真实系统上就这么两种写法）
    assert security.is_forbidden_target(tmp_path / "ok.md") is False
    home = os.environ.get("USERPROFILE") or str(Path.home())
    assert security.is_forbidden_target(Path(home) / "AppData" / "Roaming" / "x.md") is True
    assert security.is_forbidden_target(Path("D:/work/demo/AGENTS.md")) is False


def test_forbidden_target_skips_unresolvable_temp_and_roots(monkeypatch, tmp_path):
    """TEMP 或某个黑名单 root 解析不出来时：跳过它继续判，**宁可拒绝也不误放行**。

    这两条 continue 分支以前一次都没走到（覆盖率报告点出来的 101-102 / 108-109）：
    "临时目录算不出来" 和 "某个系统目录算不出来" 都是真实可能（精简环境、非 Windows）。
    顺带覆盖 forbidden_roots 里环境变量缺失的格子 —— 真实场景是 Linux/mac CI。
    """
    from app import security

    ghost = str(tmp_path / "ghost_tmp")
    monkeypatch.setenv("TEMP", ghost)
    monkeypatch.setenv("TMP", ghost)
    for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        monkeypatch.delenv(key, raising=False)

    real_resolve = Path.resolve
    dead_root = str(security.forbidden_roots()[0])   # 顺序里第一条（AppData），目标是后面的 /etc

    def flaky(self, *a, **kw):
        if str(self).startswith(ghost) or str(self) == dead_root:
            raise OSError("模拟：这个路径解析不出来")
        return real_resolve(self)

    monkeypatch.setattr(Path, "resolve", flaky)
    # 前面那条 root 算不出来只跳过它、判定照常继续 —— 命中后面的 /etc 仍然拒绝。
    # 记下既有语义（本轮只搬移，不收紧）：root 解析失败 = 跳过该条；
    # 真正 fail-closed 的是 **target 自己**解析不出来那一条（见上一个用例）。
    assert security.is_forbidden_target(Path("/etc/passwd")) is True, \
        "某条 root 解析失败不该让整条判定失效"
    assert security.is_forbidden_target(tmp_path / "ok.md") is False, \
        "TEMP 算不出来时也不能把普通临时路径误判成系统目录"


def test_text_size_helpers_are_shared_by_all_three_exits():
    """尺寸估算只有一份实现：HTTP 列表 / MCP / 前端都取 handoff_meta.text_size。

    这里钉住"同一段文本在三处的读数一致"这个前提；前端那侧由 E2E 断言（≈7 字）看住。
    """
    from app.handoff_meta import size_note, text_size

    assert text_size("已经过期的方案")["chars"] == 7
    assert size_note("已经过期的方案") == "≈7 字/≈7 tok"


def test_export_save_rejects_appdata_outside_temp(app_client, monkeypatch, tmp_path):
    """AppData 黑名单分支单独钉住（2026-09-29 补）。

    变异 forbidden_roots_drops_appdata（黑名单摘掉 AppData）曾存活：
    既有"拒绝系统目录"用例打的是 Windows 主目录（走 SystemRoot 分支），
    而 AppData 被摘后没有任何用例红 —— 保存/写盘通路对 AppData 大开（C:\\Windows 字样写在注释里需转义）。
    临时目录在 AppData 下是唯一放行位置，所以这里把 TEMP 指到 tmp_path，
    用一个真实存在的 AppData 子目录验证它必须被拒。
    """
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    target = Path(os.environ["USERPROFILE"]) / "AppData" / "Roaming" / "kanban_escape_probe.md"
    status, body = app_client.post("/api/export/save", {
        "filename": "x.md", "content": "# hi", "path": str(target)})
    assert status == 400, body


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


def test_save_export_requires_absolute_path(app_client, tmp_path, monkeypatch):
    """相对路径是按**服务进程当前目录**解析的：源码态是仓库根、打包态是 exe 目录。
    于是 "AGENTS.md" 会写到应用自己身上，而系统目录黑名单一点也拦不到。"""
    monkeypatch.chdir(tmp_path)
    status, res = app_client.post("/api/export/save", {
        "filename": "AGENTS.md", "content": "生成的内容", "path": "AGENTS.md"})
    assert status == 400, f"相对路径被接受了：{status} {res}"
    assert not (tmp_path / "AGENTS.md").exists(), "拒绝之前就已经落盘了"


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


def test_token_guard_holds_in_a_real_subprocess(token_server):
    """Token 鉴权必须在**真起进程**的那条路上也成立。

    `test_token_required_for_write` 走的是进程内客户端 + monkeypatch，
    只能证明"运行时读环境变量"这个写法还对；而打包后的 exe 恰恰是另一条路 ——
    环境变量在进程启动时就带进来，中间件读的是 `os.environ`。
    这两条路分叉过一次（历史：`test_security.py` 的 server 夹具从子进程改成进程内，
    `token_server` 从此零引用、子进程那条路没人再验），所以补这一条钉住它。
    """
    body = {"name": "子进程鉴权", "status": "进行中", "progress": 0, "priority": "中"}

    assert token_server.get("/api/health")[0] == 200, "读接口不该被 Token 挡住"
    assert token_server.request("POST", "/api/projects", body, token=None)[0] == 401
    assert token_server.request("POST", "/api/projects", body, token="wrong")[0] == 401

    status, project = token_server.request("POST", "/api/projects", body)
    assert status == 201, f"带对 Token 的写请求应当放行：{status} {project}"


def test_agentsmd_save_sends_the_overwrite_flag():
    """界面必须把覆盖开关接到 /api/export/save，否则用户没有任何办法覆盖旧文件"""
    from frontend_src import frontend_source

    html = frontend_source()          # JS 已拆出 index.html，契约要对着"整个前端"判
    i = html.index("async function saveAgentsMD")
    assert "overwrite:" in html[i:i + 1200], "保存按钮没带 overwrite"
    assert 'id="agentsmd-overwrite"' in html
