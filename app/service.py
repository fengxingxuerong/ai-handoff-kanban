# -*- coding: utf-8 -*-
"""本机服务的端口兜底值与「活数据直链」——**唯一一处实现**（2026-10-24 拆出）。

为什么单独一个模块：这两件事此前各写了三份 ——
  · 端口兜底值 `18320`：`app/main.py` / `scripts/desktop.py` / MCP 那边引用（三者口径必须一致，
    改一处漏一处就会出现"exe 在 18321 上跑、交接包里的直链指向 18320"）；
  · 直链格式 `http://127.0.0.1:<port><path>`：`app/main.py:_local_url` 与
    `scripts/mcp_server.py:local_url` 各抄了一份（本项目最忌的"两条路给不同答案"）。
本模块只依赖标准库，**任何一侧都可以安全 import**（含桌面壳这种要在导入重型依赖之前拿到端口的地方）。

两条硬边界跟着这里走（改这个函数前先想清楚）：
  · **只给 `127.0.0.1`**，不给局域网地址、不给 `0.0.0.0` —— "数据不出本机"是硬性约定 9；
  · 端口只是**兜底值**，真正的口径是环境变量 `KANBAN_PORT`（桌面壳与后端都用它）。
"""
from __future__ import annotations

import socket

# 兜底端口：桌面壳起服务、后端打印直链、MCP 给活数据链接，三者必须同源。
DEFAULT_PORT = "18320"


def local_url(port: str, path: str) -> str:
    """本机服务的直链。`path` 形如 `/api/context/p1`（带前导斜杠）。

    端口**当参数收**而不是读环境变量：调用方各持有自己的时机（后端有全局 `SERVICE_PORT`、
    MCP 那边取 `kanban.SERVICE_PORT`），而 `tests/test_api.py` 明确钉着
    "直链端口必须来自 `SERVICE_PORT`、不许写死"（它 monkeypatch 的正是 main 那个全局）。
    把它做成参数，这条契约就不会因为搬家而失效。
    """
    return f"http://127.0.0.1:{port}{path}"


def free_port() -> int:
    """挑一个当前空闲的本机端口 —— 门禁自起实例时用它，**别写死端口**。

    为什么单独成函数（2026-10-25）：这段选端口此前在 `ci_local` / `delivery_drill` /
    `scenario_test` 里**各写了一遍**（三份近似副本），而 `release_regression` 干脆写死
    `18399` —— 于是它成了唯一会被"残留实例"咬的那一个：端口上一旦有别人（上次跑挂留下的
    uvicorn，"残留进程握着东西"在本项目是实测过的坑），readiness 只等 `port_listening()`
    就会立刻"以为起来了"，随后**所有请求都打到那个别人的实例上**（实测它报的 `data_file`
    指向上一轮的临时目录，而 MCP 子进程读本轮的空目录 → 只读开关默认关 → 报出一句
    与真原因隔了十万八千里的「只读模式下写操作被拒」）。

    所以"自起实例"的标准配方是两条一起用，缺一不可：
      ① **挑空闲端口**（这里）—— 让残留无处可撞；
      ② **起完自证**（`/api/health` 的 `data_file` 指向本轮的数据目录）—— 挡 `SO_REUSEADDR`
         允许两个进程"都绑定成功"的那个窗口，以及"挑完到绑定之间被别人抢走"的窗口。

    实现说明：绑 `0` 让内核分配，拿到端口后**立刻关掉**，所以它只是"刚刚空闲"，不是预定
    （这也是为什么 ② 不能省）。只探 `127.0.0.1`：本机服务从不对外（硬性约定 9）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])