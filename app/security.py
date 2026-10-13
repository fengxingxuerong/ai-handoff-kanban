# -*- coding: utf-8 -*-
"""本地访问防护与落盘路径白名单（纯函数，2026-10-14 从 `app/main.py` 拆出）。

威胁模型：单机单用户，不做用户鉴权（任何本机进程与用户同权限）。
这里只挡两类真实可被触发的攻击：恶意网页跨站打洞、DNS rebinding。

为什么值得单独成模块：这些判定原先埋在 main.py 里，只能靠 TestClient 端到端间接验证 ——
"哪些 Host 放行""Token 覆盖哪些方法""哪些路径禁写"是个**矩阵**，穷举起来才看得清边界。
搬出来的都是纯函数（读 env、不碰文件、不读全局状态），可以直接按矩阵测。

`app/main.py` 用 `from .security import ... as ...` 保住既有名字
（`_host_of` / `_forbidden_roots` / `_is_forbidden_target` / `EXPORT_SUFFIXES` 仍可从 main 取），
所以测试与 monkeypatch 路径不变。**改动被探针盯着的这几段时，记得同步
`scripts/mutation_probe.py` 里 `guard_host_ipv6` / `guard_token_methods` /
`guard_origin_reversed` / `forbidden_roots_drops_appdata` 的 `file` 与 `old` 串。**
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

# 导出落盘允许的扩展名。前端 `saveAgentsMD` 里那条正则必须与这份一字不差
# （以前前端少一条 `.markdown`，后端明明允许却被前端挡在门外），
# 对账由 tests/test_frontend.py 钉住。
EXPORT_SUFFIXES = (".md", ".txt", ".markdown", ".agentsmd")

# 允许的 Host / Origin 主机名
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def host_of(raw: str) -> str:
    """Host 头里只取主机名，端口丢掉。

    原来写的是 `raw.split(":")[0]` —— 碰到 IPv6 的 `[::1]:18320` 只切出一个 `[`，
    再 strip("[]") 就成了空串，于是 `if host` 短路，任何带方括号的 Host 都放行。
    """
    raw = (raw or "").strip()
    try:
        host = urlparse("//" + raw).hostname
    except ValueError:
        # 方括号里塞的不是 IPv6（`[evil.example.com]`）—— hostname 会当场抛，
        # 但这恰恰是最该挡的形式，不能因为解析失败就当成"没有 Host"放过。
        host = raw[1:].split("]")[0] if raw.startswith("[") else raw.split(":")[0]
    return (host or "").strip().lower()


def origin_allowed(origin: str | None) -> bool:
    """Origin 头是否来自本机。

    没有 Origin 时这里返回 `(空串 in 白名单) = False`，**但调用方只在有 Origin 时才看它**
    —— 非浏览器客户端（curl / MCP / 桌面壳）本来就不发这个头，不该被当成跨站。
    """
    return (urlparse(origin or "").hostname or "").lower() in ALLOWED_HOSTS


def request_verdict(*, host: str = "", origin: str | None = None, method: str = "GET",
                    token_header: str | None = None,
                    token_expected: str | None = None) -> tuple[int, str]:
    """把中间件的判定收成一个纯函数：返回 `(状态码, 原因)`，**状态码 0 = 放行**。

    顺序即优先级（Host → Origin → Token）：Host 都不对的请求不必再做跨站判定。
    Token **只挡写方法**（GET 等只读请求不受影响，这是对外的契约）。
    """
    h = host_of(host)
    if h and h not in ALLOWED_HOSTS:
        return 403, "拒绝非本机 Host 访问"
    if origin and not origin_allowed(origin):
        return 403, "拒绝跨站请求"
    if token_expected and method in ("POST", "PUT", "DELETE", "PATCH"):
        if token_header != token_expected:
            return 401, "缺少或错误的 X-Kanban-Token"
    return 0, ""


# 允许"用户填路径保存"，但不允许落到系统目录 —— 防止被诱导覆盖系统/程序文件
def forbidden_roots() -> list[Path]:
    roots = []
    for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        v = os.environ.get(key)
        if v:
            roots.append(Path(v))
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if home:
        roots.append(Path(home) / "AppData")
    roots += [Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"), Path("/System")]
    return roots


def is_forbidden_target(target: Path) -> bool:
    try:
        resolved = target.resolve()
    except Exception:
        return True
    # 临时目录放行（AppData 下唯一允许的位置）
    for env in ("TEMP", "TMP"):
        tmp = os.environ.get(env)
        if tmp:
            try:
                t = Path(tmp).resolve()
            except Exception:
                continue
            if resolved == t or t in resolved.parents:
                return False
    for root in forbidden_roots():
        try:
            r = root.resolve()
        except Exception:
            continue
        if resolved == r or r in resolved.parents:
            return True
    return False
