# -*- coding: utf-8 -*-
"""
对着「已经在跑的实例」做只读体检（替代原来的 smoke_test / regression_test / ai_smoke_test）。

为什么只剩这一个：
- 写入类用例已经全部收进 tests/（pytest，自带隔离数据目录），跑 `pytest` 即可；
- 启动冻结版 exe 的端到端验证由 scripts/frozen_smoke.py 负责；
- 唯一不能替代的场景是：**应用正带着你的真实数据跑着**，你只想确认它现在是健康的 ——
  那就必须只读，一个字节都不许写。这个脚本就干这件事。

用法：
    python scripts/live_check.py                       # 默认 http://127.0.0.1:18320
    python scripts/live_check.py http://127.0.0.1:18395
    python scripts/live_check.py --mcp                 # 顺带检查 MCP 工具目录
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 输出里有 ✓ 和中文：重定向到文件时 Python 用系统 ANSI 码页（本机 cp936），
# 打第一个 ✓ 就 UnicodeEncodeError 崩掉 —— 门禁脚本必须能被重定向与管道。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001  被替换掉的 stdout 可能没有 reconfigure
    pass

BASE = "http://127.0.0.1:18320"
WANT_MCP = "--mcp" in sys.argv
for arg in sys.argv[1:]:
    if arg.startswith("http"):
        BASE = arg.rstrip("/")

# 本机可能有代理，显式绕过，否则 127.0.0.1 也可能被拦成"超时"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
rows: list[tuple[str, str, str]] = []      # (名字, P/F/S, 现场)


def get(path: str, headers: dict | None = None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with opener.open(req, timeout=10) as r:
            raw = r.read().decode("utf-8", "replace")
            ct = r.headers.get("content-type", "")
            return r.status, (json.loads(raw) if "json" in ct else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                    # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def check(name: str, cond, actual: str) -> None:
    """cond 传 False/True 就是断言；传字符串就是**这一项没跑**，明确记为 SKIP。

    空实例上「项目字段齐全」以前写成 `not projects or all(...)` —— 恒绿。
    没数据可验不是"验过了"，得单独一列，让"12 通过"这句话重新有信息量。
    """
    state = "S" if isinstance(cond, str) else ("P" if cond else "F")
    rows.append((name, state, cond if isinstance(cond, str) else actual))
    label = {"P": "  [OK] ", "F": "  [FAIL] ", "S": "  [SKIP] "}[state]
    print(label + name + ("  " + (cond if isinstance(cond, str) else actual)
                          if state != "P" else ""))


def main() -> int:
    print(f"只读体检目标：{BASE}（不会写入任何数据）\n")

    st, health = get("/api/health")
    ok = st == 200 and isinstance(health, dict) and health.get("ok") is True
    check("服务存活 /api/health", ok, f"{st} {str(health)[:120]}")
    if not ok:
        print("\n服务没起来？先双击 dist\\AI交接看板.exe，或跑 scripts\\start_desktop.bat")
        return 1

    st, html = get("/")
    check("首页可访问", st == 200 and "AI 交接看板" in str(html), f"{st}")
    # ACTIONS 表在 web/js-core.js 里（2026-09-28 前端拆成多文件），首页只有引用标签；
    # 判据与 frozen_smoke 那份保持同口径：首页无内联事件 + 真引到了 js-core.js
    check("前端为事件委托版本（无内联事件）",
          'data-act="' in str(html) and 'src="web/js-core.js"' in str(html)
          and "onclick=" not in str(html), "内联事件或 js-core 引用缺失")

    st, data = get("/api/projects")
    projects = data.get("projects", []) if isinstance(data, dict) else []
    fields = {"id", "name", "status", "progress", "priority", "total_tasks"}
    check(f"项目列表可读（{len(projects)} 个项目）", st == 200, f"{st}")
    # 以前是 `not projects or all(...)`：空实例上恒绿，而 CI 起的恰恰是空实例
    check("项目字段齐全", all(fields <= set(p) for p in projects) if projects
          else "实例里没有项目，没有字段可验（ci_local 会先播一条种子）",
          "有项目缺少必需字段")

    st, stats = get("/api/stats")
    need = {"project_total", "total_tasks", "blocked_tasks", "status_dist", "readiness"}
    check("统计接口可读且字段齐全",
          st == 200 and isinstance(stats, dict) and need <= set(stats), f"{st}")

    st, cfg = get("/api/ai/config")
    eps = cfg["ai"]["endpoints"] if isinstance(cfg, dict) else []
    keys = [k for ep in eps for k in ep.get("api_keys", [])]
    # 同上：`all(...)` 对空列表恒真，"Key 已掩码"在没有 Key 的实例上什么都不说
    check(f"AI 配置可读（{len(eps)} 个端点）且 Key 已掩码",
          (st == 200 and all("****" in k for k in keys)) if keys
          else "该实例没有配置任何 Key，掩码无内容可验",
          f"{st} / 未掩码的 Key 若干")

    st, backups = get("/api/backups")
    check("备份列表可读", st == 200 and "backups" in backups, f"{st}")

    st, rec = get("/api/recoverable")
    check("可恢复来源可读", st == 200 and "recoverable" in rec, f"{st}")

    # 按目录找项目（只读）：先验**形状** —— 一个不存在的目录必须回一条能读的否定答案，
    # 而不是 404/500（「没登记」与「接口坏了」必须让人分得清）。
    probe = tempfile.gettempdir() + "/kanban_no_such_repo_for_live_check"
    st, loc = get("/api/repo-locate?path=" + urllib.parse.quote(probe))
    check("按目录找项目可读（未知目录 = 一条可读的否定答案）",
          st == 200 and isinstance(loc, dict) and {"ok", "match", "reason"} <= set(loc),
          f"{st} {str(loc)[:140]}")
    # 再验**真命中**：实例里有项目登记了 repo_path 的话，那条路径必须认得回来
    with_repo = [p for p in projects if p.get("repo_path")]
    if with_repo:
        one = with_repo[0]
        st2, hit = get("/api/repo-locate?path=" + urllib.parse.quote(one["repo_path"]))
        check(f"按目录找项目命中（{one['name']}）",
              st2 == 200 and isinstance(hit, dict) and hit.get("ok") is True
              and (hit.get("project") or {}).get("id") == one["id"],
              f"{st2} {str(hit)[:140]}")
    else:
        check("按目录找项目命中", "实例里没有项目登记 repo_path，这条没有内容可验", "")

    st, _ = get("/api/health", headers={"Origin": "http://evil.example.com"})
    check("跨站 Origin 被拒（403）", st == 403, f"返回 {st}")

    st, _ = get("/api/projects", headers={"Host": "evil.example.com"})
    check("伪 Host 被拒（403）", st == 403, f"返回 {st}")

    if WANT_MCP:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "mcp_server.py")],
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n",
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=60)
        line = (proc.stdout or "").strip().splitlines()
        tools = json.loads(line[0])["result"]["tools"] if line else []
        # `or ""` 不是摆设：读线程一旦解码失败，CompletedProcess.stdout/stderr 就是 None，
        # 于是"失败原因"本身先把体检脚本崩掉，反而看不到真问题（2026-09-19 撞到过）
        check(f"MCP 工具目录可读（{len(tools)} 个工具）", len(tools) >= 23,
              f"实际 {len(tools)}（2026-10-27 实测 25；这条只要求目录可读，所以门槛保持 ≥23，"
              f"精确数由 mcp_frozen_smoke 钉）"
              f" / stderr={(proc.stderr or '')[:120]} / rc={proc.returncode}")

    failed = [r for r in rows if r[1] == "F"]
    passed = [r for r in rows if r[1] == "P"]
    skipped = [r for r in rows if r[1] == "S"]
    print(f"\n结果：{len(passed)} 通过 / {len(failed)} 失败"
          + (f" / {len(skipped)} 未验（{'、'.join(r[0] for r in skipped)}）" if skipped else ""))
    if failed:
        print("失败项：" + " | ".join(r[0] for r in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
