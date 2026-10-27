# -*- coding: utf-8 -*-
"""冻结版 MCP 服务自查：真的启动 `dist\\AI交接看板-MCP.exe`，走真实 stdio 打一遍。

**为什么需要它**：MCP 服务是**控制台子系统**的程序（stdio 传输），而主产物
`AI交接看板.exe` 是 `-w` 打的 GUI 程序 —— 两者对标准流的处理完全不同，而"exe 用户没有
.env，MCP 因此不可用"正是 2026-10-23 评测里最要命的产品缺口。所以这个产物必须有自己的门禁，
不能靠"源码跑得过"外推。它钉四件事（都是打包才会坏、源码态永远看不出来的）：

1. **协议真的能用**：stdin 逐行 JSON-RPC → stdout 逐行 JSON-RPC，`tools/list` 拿到 25 个工具。
2. **stdout 只有协议**：任何一行不是合法 JSON 就算失败 —— 一行日志就能把整个服务变成"没发布"
   （客户端解析失败 → 工具目录为空）。
3. **中文双向不乱码**：写进去一个中文名、再读回来逐字一致。中文 Windows 上子进程默认跟随
   控制台码页（cp936），打包态更容易踩 —— 这是它必须自己 `reconfigure("utf-8")` 的原因。
4. **真写进了数据**（而不是只回了一句"已创建"）：落盘在本脚本指定的隔离目录里，
   **绝不碰 `%USERPROFILE%\\.ai-kanban`**。

用法：
    python scripts/mcp_frozen_smoke.py                 # 默认 dist\\AI交接看板-MCP.exe
    python scripts/mcp_frozen_smoke.py 路径\\到\\x.exe
    python scripts/mcp_frozen_smoke.py --source        # 用源码跑同一套断言（先自证门禁本身有效）
退出码 0 表示通过。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001  被替换掉的 stdout 可能没有 reconfigure
    pass

DEFAULT_EXE = ROOT / "dist" / "AI交接看板-MCP.exe"
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
USE_SOURCE = "--source" in sys.argv

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (passed if cond else failed).append(name)
    print(("  [OK] " if cond else "  [FAIL] ") + name + ("" if cond else "  " + extra))


def build_command() -> list[str]:
    if USE_SOURCE:
        return [sys.executable, str(ROOT / "scripts" / "mcp_server.py")]
    exe = Path(ARGS[0]) if ARGS else DEFAULT_EXE
    if not exe.exists():
        print(f"找不到 {exe} —— 先 `build.bat`（或 `python scripts/ci_local.py --with-exe`）")
        raise SystemExit(1)
    return [str(exe)]


def talk(cmd: list[str], env: dict, messages: list[dict]) -> tuple[str, str, int]:
    payload = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    r = subprocess.run(cmd, input=payload.encode("utf-8"), capture_output=True,
                       env=env, timeout=120)
    return (r.stdout.decode("utf-8", "replace"),
            r.stderr.decode("utf-8", "replace"), r.returncode)


def main() -> int:
    cmd = build_command()
    data_dir = Path(tempfile.gettempdir()) / "kanban_mcp_frozen_smoke"
    if data_dir.exists():
        for f in data_dir.rglob("*"):
            if f.is_file():
                f.unlink()
    data_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["KANBAN_DATA_DIR"] = str(data_dir)
    env["KANBAN_NO_SCHEDULER"] = "1"
    env.pop("PYTHONIOENCODING", None)      # 客户端不会替你设，门禁也不许设（约定：别靠 env 兜底）
    env.pop("PYTHONUTF8", None)

    print(f"MCP 冻结版自查：{'源码' if USE_SOURCE else 'exe'} = {cmd[-1]}\n数据目录 = {data_dir}\n")

    cname = "中文项目名·交接冒烟✓"
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "kanban_create_project",
                    "arguments": {"name": cname, "status": "进行中", "client_ref": "smoke-1"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "kanban_list_projects", "arguments": {}}},
    ]
    out, err, code = talk(cmd, env, messages)

    lines = [ln for ln in out.splitlines() if ln.strip()]
    check("stdout 有三条响应", len(lines) == 3, f"实测 {len(lines)} 行：{lines[:3]}")

    parsed = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except Exception as exc:  # noqa: BLE001
            check("stdout 每一行都是合法 JSON-RPC", False, f"{ln[:80]!r} → {exc}")
            parsed.append(None)
    check("stdout 每一行都是合法 JSON-RPC", len(parsed) == len(lines) and all(parsed))

    if len(parsed) >= 1 and parsed[0]:
        tools = (parsed[0].get("result") or {}).get("tools") or []
        check("tools/list 拿到 25 个工具", len(tools) == 25, f"实测 {len(tools)}")
        names = {t.get("name") for t in tools}
        check("工具目录里有 kanban_context", "kanban_context" in names)

    if len(parsed) >= 2 and parsed[1]:
        # 回执里的中文要能原样读回来（读不回来 = 出方向被编成了别的码页）
        raw2 = json.dumps(parsed[1], ensure_ascii=False)
        check("创建回执里的中文没乱码", "\ufffd" not in raw2 and cname in raw2, raw2[:200])

    # 真落盘：读隔离目录里的 projects.json 自己数，不信回执的一面之词
    pj = data_dir / "projects.json"
    check("数据真的落到隔离目录", pj.exists(), f"{pj} 不存在")
    if pj.exists():
        saved = json.loads(pj.read_text(encoding="utf-8"))
        names = [p.get("name") for p in saved.get("projects", [])]
        check("中文项目名逐字入库（入方向不乱码）", cname in names, f"盘上是 {names}")
    check("日志走 stderr（stdout 没被污染）", bool(err.strip()), "stderr 是空的，日志可能跑到 stdout 了")
    check("进程正常退出", code == 0, f"退出码 {code}")

    print("\n" + "=" * 78)
    print(f"MCP 冻结版自查：{len(passed)} 通过 / {len(failed)} 失败")
    for name in failed:
        print(f"  ✗ {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())