# -*- coding: utf-8 -*-
"""变异探针：往源码里注入"一个真实可能发生的改动"，看测试会不会红。

为什么需要它：**覆盖率只能证明"这行被执行过"，不能证明"这行错了会被发现"**。
`if x: A else: B` 两支行覆盖都是 100%，但断言只看了 A 的返回值时，把 B 改成
`return None` 依然全绿 —— 这正是"数字很好看、缺陷照样上线"的那一类洞。
本脚本把这类洞量化：

- **killed（被杀）**  改了就红 → 测试真的钉住了这个语义，好
- **survived（存活）** 改了还全绿 → 这条语义**没人验**，是盲区，要补断言
- **inconclusive**    变异本身没生效（文本没匹配上）或探针跑挂了 → 别当成"过了"

用法：
    python scripts/mutation_probe.py                 # 全跑
    python scripts/mutation_probe.py --only due_soon # 只跑某几条（逗号分隔）
    python scripts/mutation_probe.py --json out.json # 机器可读结果（给 CI/报告用）

判据纪律（与 AGENTS.md 的"拒绝 mock 自证"同源）：
1. 每条变异必须是**语义改动**，不是语法糖替换；等价改写（换个写法但行为一样）
   跑出来"存活"没有任何意义，不要往表里加。
2. 存活不等于"测试写错了"，也可能是"这条规则确实没被测到"—— 两者都要补断言，
   补完再跑一次本脚本自证（存活 → 被杀）才闭环。
3. 探针只改本地工作区文件，**跑完一定还原**（内存备份 + finally，不依赖 git），
   而且**内容与 mtime 都要还原**（见 `_restore_mtime`：还原只写内容会把 mtime 刷成"刚刚"，
   让 frozen_smoke 的"产物比源码旧"那条门禁在每次探针之后稳定假红）。
   探针运行期间不要并行跑别的测试。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

# ---------------------------------------------------------------------------
# 变异表
#
# 每条 = 一处**语义**改动 + 该语义对应的测试子集（子集不是偷懒：
# 全量 88s × 20 条 = 半小时，跑不动；子集选的是"覆盖这条语义"的那几个文件，
# 判据是变异被杀就算数，杀不了再上全量复核）。
# ---------------------------------------------------------------------------
MUTATIONS: list[dict] = [
    {
        "id": "due_soon_window",
        "file": "app/due.py",
        "old": "    if (d - today).days <= 2:",
        "new": "    if (d - today).days <= 1:",
        "why": "临期窗口从 2 天改成 1 天：/api/stats 的 duesoon 与卡片徽章一起错",
        "tests": ["tests/test_pure_modules.py", "tests/test_api.py", "tests/test_v110_features.py"],
    },
    {
        "id": "due_overdue_boundary",
        "file": "app/due.py",
        "old": "    if d < today:",
        "new": "    if d <= today:",
        "why": "今天到期算成逾期：边界错了半天（今天整天被判逾期）",
        "tests": ["tests/test_pure_modules.py", "tests/test_api.py", "tests/test_v110_features.py"],
    },
    {
        "id": "due_ignore_done",
        "file": "app/due.py",
        "old": '    if not due or t.get("status") == "已完成":',
        "new": "    if not due:",
        "why": "已完成的任务也报逾期：仪表盘凭空多出逾期数",
        "tests": ["tests/test_pure_modules.py", "tests/test_api.py", "tests/test_v110_features.py"],
    },
    {
        "id": "due_normalize_formats",
        "file": "app/due.py",
        "old": '    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):',
        "new": '    for fmt in ("%Y-%m-%d",):',
        "why": "只认连字符：`2026/9/5` 与 `2026.09.05` 导入后被清空（截止日期静默丢失）",
        "tests": ["tests/test_pure_modules.py", "tests/test_api.py", "tests/test_data_io.py", "tests/test_v110_features.py"],
    },
    {
        "id": "guard_host_ipv6",
        "file": "app/security.py",
        "old": '        host = raw[1:].split("]")[0] if raw.startswith("[") else raw.split(":")[0]',
        "new": '        host = ""',
        "why": "兜底分支把非 IP 方括号 Host 解析成空串放行：[abc] 等异常 Host 绕过白名单",
        "tests": ["tests/test_security.py"],
    },
    {
        "id": "guard_token_methods",
        "file": "app/security.py",
        "old": '    if token_expected and method in ("POST", "PUT", "DELETE", "PATCH"):',
        "new": '    if token_expected and method in ("POST", "PUT", "DELETE", "PATCH", "GET"):',
        "why": "开了 Token 后读接口也要鉴权：本该『只读不受影响』的契约被破坏",
        "tests": ["tests/test_security.py"],
    },
    {
        "id": "guard_origin_reversed",
        "file": "app/security.py",
        "old": "    if origin and not origin_allowed(origin):",
        "new": "    if origin and origin_allowed(origin):",
        "why": "跨站拦截反过来：本机 Origin 被拒、外站 Origin 放行（DNS rebinding 洞）",
        "tests": ["tests/test_security.py"],
    },
    {
        "id": "deps_drop_unknown_filter",
        "file": "app/taskgraph.py",
        "old": "        if not dep or dep in seen or dep not in valid_ids or len(out) >= MAX_DEPS:",
        "new": "        if not dep or dep in seen or len(out) >= MAX_DEPS:",
        "why": "不再过滤『指向不存在任务』的脏依赖：拓扑序与可执行判断会被幽灵前置卡死",
        "tests": ["tests/test_pure_modules.py", "tests/test_task_deps.py", "tests/test_api.py"],
    },
{
        # 2026-10-07 executable_tasks 的就绪判定抽成共享谓词 deps_blocking（与 /api/inbox 同一口径），
        # 变异文本同步：把「未知前置 = 已了结」退回成「未知前置 = 未完成」。
        "id": "executable_unknown_dep_blocks",
        "file": "app/taskgraph.py",
        "old": "    return any(d in known_ids and d not in done_ids\n"
               "               for d in (t.get(\"depends_on\") or []))",
        "new": "    return any(d not in done_ids\n"
               "               for d in (t.get(\"depends_on\") or []))",
        "why": "未知前置按『未完成』处理：今日待办少一批任务（依赖脏数据时不该永久消失）",
        "tests": ["tests/test_pure_modules.py", "tests/test_task_deps.py", "tests/test_v110_features.py"],
    },
    {
        "id": "assign_ignores_priority",
        "file": "app/assign.py",
        "old": "    return [t for t in executable_tasks(tasks)\n            if t.get(\"priority\") == priority and t.get(\"status\") != \"阻塞\"]",
        "new": "    return [t for t in executable_tasks(tasks)\n            if t.get(\"status\") != \"阻塞\"]",
        "why": "自动分配不按优先级筛选：把『中/低』任务也派出去（接口契约变宽）",
        "tests": ["tests/test_assign.py"],
    },
    {
        "id": "assign_takes_blocked_tasks",
        "file": "app/assign.py",
        "old": "            if t.get(\"priority\") == priority and t.get(\"status\") != \"阻塞\"]",
        "new": "            if t.get(\"priority\") == priority]",
        "why": "阻塞中的任务被自动分配：卡片上写着『缺权限被卡住』，分配器却照派不误 —— "
               "2026-10-19 前的真实行为（allocatable 里根本没有阻塞过滤）",
        "tests": ["tests/test_assign.py"],
    },
    {
        "id": "assign_overwrites_taken",
        "file": "app/assign.py",
        "old": '        if cur and not overwrite:',
        "new": "        if cur:",
        "why": "默认也覆盖已有负责人的任务：手工指派被自动分配冲掉（跳过规则失效）",
        "tests": ["tests/test_assign.py"],
    },
    {
        "id": "assign_blank_agent_proceeds",
        "file": "app/assign.py",
        "old": "    if not agent:",
        "new": "    if agent is None:",
        "why": "空串 agent 也能触发分配：空白『负责人』批量写入任务",
        "tests": ["tests/test_assign.py"],
    },
    {
        "id": "readiness_placeholder_action",
        "file": "app/readiness.py",
        "old": '        "next": bool(next_action) and next_action not in PLACEHOLDER_ACTIONS,',
        "new": '        "next": bool(next_action),',
        "why": "占位词（无 / 暂停中 等）也算下一步：就绪度虚高，正是当初两份实现漂移的那条规则",
        "tests": ["tests/test_readiness.py", "tests/test_api.py"],
    },
    {
        "id": "readiness_handoff_weight",
        "file": "app/readiness.py",
        "old": '    ("handoff", "交接说明已填写（给智能体）", 35),',
        "new": '    ("handoff", "交接说明已填写（给智能体）", 20),',
        "why": "交接说明权重从 35 降到 20：总分与缺什么的排序都变，界面与接手页必须一起变",
        "tests": ["tests/test_readiness.py", "tests/test_api.py", "tests/test_handoff_page.py"],
    },
    {
        "id": "idem_ref_truncate",
        "file": "app/idem.py",
        "old": "    if len(ref) <= 80:\n        return ref",
        "new": "    if len(ref) <= 80:\n        return ref[:48]",
        "why": "复现历史真缺陷：超长幂等键改回简单截断 → 两条不同请求撞同一个键，静默返回错记录",
        "tests": ["tests/test_pure_modules.py", "tests/test_api.py", "tests/test_misc.py"],
    },
    {
        "id": "redact_skips_chinese_password",
        "file": "app/redact.py",
        "old": '    (r"(?i)(密码|口令|密钥|授权码)(\\s*[：:=]\\s*)([^\\s，。；,;、]{4,})", r"\\1\\2" + MASK_MARK),\n',
        "new": "",
        "why": "脱敏规则去掉中文密码行：密码=Sup3rSecret! 这类最常见的中文写法原样进交接包（黑盒探索 2026-10-08）",
        "tests": ["tests/test_pure_modules.py", "tests/test_api.py"],
    },
    {
        "id": "patch_merge_reports_400_not_422",
        "file": "app/main.py",
        "old": '        raise HTTPException(422, f"字段校验失败：{e.errors()[0].get(\'msg\', \'非法值\')}") from None',
        "new": '        raise HTTPException(400, f"字段校验失败：{e.errors()[0].get(\'msg\', \'非法值\')}") from None',
        "why": "PATCH 非法枚举回 400 而不是 422：约定 6 的白名单契约按 422 口径，前端与智能体按 422 提示校验错误（黑盒探索 2026-10-08）",
        "tests": ["tests/test_api.py"],
    },
    {
        "id": "export_all_prefetch_disabled",
        "file": "app/main.py",
        "old": '    if "git" not in (omit or set()):\n        prefetch_snapshots([p.get("repo_path", "") for p in projects])',
        "new": "",
        "why": "全项目交接包回到 git 串行探测：冷态 1000 项目中位 5.8s/max 56s（bench 2026-10-08），导出接口被几十个仓库拖死",
        "tests": ["tests/test_git.py", "tests/test_api.py"],
    },
{
        "id": "stats_stalled_never_fires",
        "file": "app/project_stats.py",
        "old": "        if idle_days > window_days:",
        "new": "        if False:",
        "why": "停滞检测永不触发：进行中的任务停多久都不进 stats，'看板在推进'变成无人验证的口号（第三轮竞品吸取 2026-10-08）",
        "tests": ["tests/test_p0_stats.py"],
    },
    {
        "id": "stats_stalled_ignores_injected_clock",
        "file": "app/project_stats.py",
        "old": "    now = now or datetime.now()",
        "new": "    now = datetime.now()",
        "why": "project_stats 丢弃注入的时钟回退墙钟：test_p0_stats 注入 2026-10-08 判 s2 不算停滞，墙钟走到 10-11 后 s2 变 8 天误判停滞，测试随运行日期漂移变红（2026-10-11 实锤）",
        "tests": ["tests/test_p0_stats.py"],
    },
    {
        "id": "brief_stalled_omitted",
        "file": "app/aiprompt.py",
        "old": '        item["stalled"] = (stalled_fn or project_stats_stalled)(p.get("tasks") or [])',
        "new": '        pass',
        "why": "AI 监管简报看不到停滞信号：看板侧停摆从此无人发现（与 git.days_since_commit 的代码侧停滞对称，第三轮竞品吸取 2026-10-08）",
        "tests": ["tests/test_scale.py"],
    },
    {
        "id": "redact_drops_email",
        "file": "app/redact.py",
        "old": '    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "****@****"),\n',
        "new": "",
        "why": "脱敏规则里去掉邮箱：交接包把邮箱原样发给别的机器",
        "tests": ["tests/test_api.py"],
    },
    {
        "id": "sections_unknown_silently_ignored",
        "file": "app/handoff_meta.py",
        "old": '        if name not in HANDOFF_SECTIONS:\n'
               '            raise HTTPException(400, f"{param} 不认识「{name}」，'
               "可选：{'、'.join(HANDOFF_SECTIONS)}\")",
        "new": "        if name not in HANDOFF_SECTIONS:\n            continue",
        "why": "裁剪小节时遇到不认识的名字静默忽略：调用方以为裁掉了，实际整包原样发出",
        "tests": ["tests/test_api.py", "tests/test_mcp_server.py"],
    },
    {
        "id": "budget_thresholds_loosened",
        "file": "app/handoff_meta.py",
        "old": "def context_budget(text: str, warn_lines: int = 150, over_lines: int = 300) -> dict:",
        "new": "def context_budget(text: str, warn_lines: int = 600, over_lines: int = 1200) -> dict:",
        "why": "尺寸档位阈值整体放宽 4 倍：过载提示永远不出现，长包静默超窗",
        "tests": ["tests/test_api.py", "tests/test_misc.py", "tests/test_mcp_server.py"],
    },
    {
        "id": "cycle_break_disabled",
        "file": "app/taskgraph.py",
        "old": "    if not has_cycle(graph):\n        return",
        "new": "    if True:\n        return",
        "why": "导入/恢复进来的依赖环不再自愈：任务永远等不到前置，用户只看到顺序不对",
        "tests": ["tests/test_task_deps.py", "tests/test_data_io.py"],
    },
    {
        "id": "max_deps_shrunk",
        "file": "app/taskgraph.py",
        "old": "MAX_DEPS = 10",
        "new": "MAX_DEPS = 3",
        "why": "单任务依赖上限 10 → 3：超过的依赖被静默丢弃，交接包里的先后顺序悄悄变",
        "tests": ["tests/test_pure_modules.py", "tests/test_task_deps.py", "tests/test_api.py"],
    },
    {
        "id": "today_hard_cap_shrunk",
        "file": "app/main.py",
        "old": "    shown = max(1, min(limit, 200))",
        "new": "    shown = max(1, min(limit, 5))",
        "why": "今日待办硬上限 200 → 5：limit 传 30 也只回 5 条（截断契约被改）",
        "tests": ["tests/test_scale.py", "tests/test_v110_features.py", "tests/test_api.py", "tests/test_mcp_server.py"],
    },
    {
        "id": "live_url_wrong_port",
        "file": "app/service.py",
        "old": '    return f"http://127.0.0.1:{port}{path}"',
        "new": '    return f"http://127.0.0.1:18321{path}"',
        "why": "交接包里的实时直链端口写死成 18321：自定义端口部署时智能体拿到死链",
        "tests": ["tests/test_api.py", "tests/test_mcp_server.py"],
    },
    {
        "id": "readiness_active_narrowed",
        "file": "app/readiness.py",
        "old": '        "active": any(t.get("status") in ("进行中", "待审查", "已完成") for t in tasks),',
        "new": '        "active": any(t.get("status") in ("进行中",) for t in tasks),',
        "why": "只把『进行中』算推进：全做完的项目反而掉 10 分（已完成不算数）",
        "tests": ["tests/test_readiness.py", "tests/test_api.py"],
    },
    {
        "id": "progress_no_clamp",
        "file": "app/main.py",
        "old": '            p["progress"] = max(0, min(100, int(p.get("progress", 0))))',
        "new": '            p["progress"] = int(p.get("progress", 0))',
        "why": "进度不再钳到 0-100：导入脏数据后进度条画出界（-5 / 300）",
        "tests": ["tests/test_data_io.py", "tests/test_api.py"],
    },
    {
        "id": "today_loses_priority_sort",
        "file": "app/main.py",
        "old": '    out.sort(key=lambda x: (pri_rank.get(x.get("priority"), 1), '
               'x.get("due_date") or "9999-12-31"))',
        "new": '    out.sort(key=lambda x: x.get("due_date") or "9999-12-31")',
        "why": "今日待办不再按优先级排：高优先任务被埋在无截止的杂事后面",
        "tests": ["tests/test_v110_features.py", "tests/test_api.py"],
    },
    {
        "id": "save_export_allows_relative_path",
        "file": "app/main.py",
        "old": "        if not target.is_absolute():",
        "new": "        if False:",
        "why": "保存交接包允许相对路径：按服务进程当前目录解析，黑名单完全拦不到（会写到应用自己身上）",
        "tests": ["tests/test_security.py", "tests/test_api.py", "tests/test_agent_files.py"],
    },
    {
        # 2026-10-07 对抗性审查：同形守卫在 main.py 有两处（save_export 的 409 与
        # export_agent_files 的 skipped_existing），old 串必须带上各自的注释行才唯一，
        # 否则 replace(…,1) 撞上先出现的那个就静默变异错位置。
        "id": "save_export_silent_overwrite",
        "file": "app/main.py",
        "old": '        if target.exists() and not body.overwrite:\n            # 与 /api/export/agent-files 同一口径',
        "new": "        if False:\n            # 与 /api/export/agent-files 同一口径",
        "why": "已存在文件也不拦：默认指向仓库 AGENTS.md 的那一次会把用户手写内容静默覆盖",
        "tests": ["tests/test_security.py", "tests/test_api.py", "tests/test_agent_files.py"],
    },
    {
        "id": "save_export_suffix_whitelist_off",
        "file": "app/main.py",
        "old": "        if suffix not in EXPORT_SUFFIXES:",
        "new": "        if False:",
        "why": "后缀白名单关掉：可以把交接内容写成 .exe / .bat（落盘即执行面）",
        "tests": ["tests/test_security.py", "tests/test_api.py", "tests/test_agent_files.py"],
    },
    {
        "id": "forbidden_roots_drops_appdata",
        "file": "app/security.py",
        "old": '        roots.append(Path(home) / "AppData")',
        "new": "        pass",
        "why": "系统目录黑名单去掉 AppData：除临时目录外的 AppData 都可写（诱导落盘通路）",
        "tests": ["tests/test_security.py", "tests/test_api.py", "tests/test_agent_files.py"],
    },
    {
        # 2026-10-14 新增：判定本体拆到 app/security.py 时顺手把这条 fail-closed 分支纳入门禁。
        # 解析失败的路径放行 = 黑名单形同虚设（"算不出来"恰恰是最该拒的情况）。
        "id": "forbidden_target_fails_open",
        "file": "app/security.py",
        "old": "    try:\n        resolved = target.resolve()\n    except Exception:\n        return True\n",
        "new": "    try:\n        resolved = target.resolve()\n    except Exception:\n        return False\n",
        "why": "写盘黑名单在路径解析失败时放行（fail-open）：算不出真实路径反而更该拒绝",
        "tests": ["tests/test_security.py"],
    },
    {
        # 2026-10-14 新增（用户拍板收紧后）：黑名单边界算不出来时也必须拒绝。
        # 放开成 continue 就是"那条 root 等于不存在"的 fail-open 窗口。
        "id": "forbidden_root_unresolvable_allowed",
        "file": "app/security.py",
        "old": "        try:\n            r = root.resolve()\n        except Exception:\n            return True\n",
        "new": "        try:\n            r = root.resolve()\n        except Exception:\n            continue\n",
        "why": "黑名单某条 root 解析失败时跳过它 = 少拦一条（fail-open），目标可能正在那条 root 下面",
        "tests": ["tests/test_security.py"],
    },
    {
        "id": "search_truncated_flag_off",
        "file": "app/main.py",
        "old": '                    return {"results": out, "truncated": True, "limit": limit}',
        "new": '                    return {"results": out, "truncated": False, "limit": limit}',
        "why": "搜索命中上限却报 truncated=false：智能体把『只给你 30 条』当成『只有 30 条』",
        "tests": ["tests/test_api.py", "tests/test_mcp_server.py"],
    },
    {
        "id": "search_limit_ceiling_raised",
        "file": "app/main.py",
        "old": "    limit = max(1, min(50, int(limit)))",
        "new": "    limit = max(1, min(5000, int(limit)))",
        "why": "搜索上限 50 → 5000：一次拖出整份看板，与前端/MCP 的封顶口径分叉",
        "tests": ["tests/test_api.py", "tests/test_mcp_server.py"],
    },
    {
        # 2026-10-07 清洗实现搬进 app/journal.py，探针目标随迁（与 monkeypatch 点同址）
        "id": "journal_no_cap",
        "file": "app/journal.py",
        "old": "    if len(j) > MAX_JOURNAL:",
        "new": "    if False:",
        "why": "过程记录条数不再封顶：projects.json 悄悄变胖（读写变慢、备份变大、交接包变长）",
        "tests": ["tests/test_api.py", "tests/test_storage_limits.py"],
    },
    {
        # 2026-10-07 随实现迁到 app/journal.py；时钟注入后该行不再含 now_str()
        "id": "journal_text_no_cut",
        "file": "app/journal.py",
        "old": '    j.append({"time": when, "text": (text or "")[:MAX_JOURNAL_TEXT]})',
        "new": '    j.append({"time": when, "text": (text or "")})',
        "why": "单条记录不再截到 500 字：与声明的上限分叉（智能体写 1200 字，落盘却是全量）",
        "tests": ["tests/test_storage_limits.py", "tests/test_api.py"],
    },
    {
        # 2026-10-06 起 journal 超限改为「折叠成摘要」而不是直接截断（对标 Beads 语义压缩）。
        # 原来的 clean_journal_no_cap（摘掉 raw[-MAX_JOURNAL:]）盯的旧代码已随这次改动搬家，
        # 变异改盯新语义：把折叠退回成丢弃。2026-10-07 实现再迁 app/journal.py，目标同步。
        "id": "journal_fold_dropped",
        "file": "app/journal.py",
        "old": "    return [summary] + keep",
        "new": "    return keep",
        "why": "溢出的旧过程记录从『折叠成摘要』退化回『静默丢弃』：早期决策证据无声消失",
        "tests": ["tests/test_storage_limits.py", "tests/test_p1_absorb.py"],
    },
    {
        # 2026-10-07 clean_acceptance 随清洗簇搬进 app/journal.py，探针目标同步
        "id": "acceptance_no_cap",
        "file": "app/journal.py",
        "old": "        if len(out) >= MAX_ACCEPTANCE_ITEMS:",
        "new": "        if False:",
        "why": "验收标准条数不再封顶：模型吐 500 条就长在任务里，交接包跟着膨胀",
        "tests": ["tests/test_p1_absorb.py", "tests/test_mcp_server.py"],
    },
    {
        "id": "memory_expiry_ignored",
        "file": "app/due.py",   # 2026-10-06 _memory_expired 随日期判定迁入 due.py，探针目标同步
        "old": "    return exp < today",
        "new": "    return False",
        "why": "过期记忆被当『当前事实』召回：交接包/AGENTS.md/模型简报带着过期结论接手",
        "tests": ["tests/test_p1_absorb.py"],
    },
    {
        # 2026-10-07 inbox 补上「依赖没满足的不算」后循环体改写，变异文本同步到新代码
        "id": "inbox_counts_completed",
        "file": "app/main.py",
        "old": '        for t in tasks:\n            if t.get("status") == "已完成":\n                continue',
        "new": "        for t in tasks:\n            if False:\n                continue",
        "why": "已完成的任务也进待拍板清单（逾期的已完成任务凭空出现在铃铛里）",
        "tests": ["tests/test_p1_absorb.py"],
    },
    {
        # 2026-10-07 新增：约定 27 的「依赖没满足的不算」实现漏了过滤、测试空转 ——
        # 修复当天按纪律补探针，别让新规则回到"没有门禁"的状态。
        "id": "inbox_ignores_blocked_deps",
        "file": "app/main.py",
        "old": "            if deps_blocking(t, done_ids, known_ids):\n                continue",
        "new": "            if False:\n                continue",
        "why": "被前置挡住的任务也进待拍板：它随前置完成自然流转，不是等人的事，铃铛被噪音淹没",
        "tests": ["tests/test_p1_absorb.py"],
    },
{
        "id": "recovered_flag_never_clears",
        "file": "app/main.py",
        "old": "    recovered, _DATA_RECOVERED = _DATA_RECOVERED, False",
        "new": "    recovered, _DATA_RECOVERED = _DATA_RECOVERED, _DATA_RECOVERED",
        "why": "自愈标记上报后不清：每次列表都弹『数据刚被救回来』，且 MCP 会反复警告",
        "tests": ["tests/test_restore.py", "tests/test_api.py"],
    },
    {
        # 2026-10-07 对抗性审查：`return min(n, hi)` 在 usage_stats.py 有两处
        # （clamp_price / clamp_budget），靠后随的 def 行区分目标。
        "id": "usage_price_clamp_removed",
        "file": "app/usage_stats.py",
        "old": "    return min(n, hi)\n\n\ndef clamp_budget",
        "new": "    return n\n\n\ndef clamp_budget",
        "why": "端点单价钳制失效：配置塞 1e9 会被原样持久化，读侧按它估的成本直接爆表（写读两侧共用这一道钳）",
        "tests": ["tests/test_p0_stats.py"],
    },
    {
        "id": "due_heat_window_shrunk",
        "file": "app/project_stats.py",
        "old": "        elif d <= today + timedelta(days=6):   # 未来 7 天窗口：今天 + 后 6 天",
        "new": "        elif d <= today + timedelta(days=5):   # 未来 7 天窗口：今天 + 后 6 天",
        "why": "截止热力窗口从『未来 7 天』收窄成 6 天：第 7 天的任务被划进『更晚』，临期提醒漏一天",
        "tests": ["tests/test_p0_stats.py"],
    },
{
        "id": "done_tasks_enter_due_heat",
        "file": "app/project_stats.py",
        "old": "        if t.get(\"status\") == \"已完成\":\n            continue",
        "new": "        if t.get(\"status\") == \"已完成\":\n            pass",
        "why": "已完成任务也进截止热力：早关掉的任务因逾期日期被标成『临期/逾期』，界面误报",
        "tests": ["tests/test_p0_stats.py"],
    },
    {
        "id": "milestone_dedupe_removed",
        "file": "app/fields.py",
        "old": '        if not name or name in [m["name"] for m in out]:',
        "new": "        if not name:",
        "why": "里程碑同名去重失效：两条同名里程碑并存，归组任务时按 name 匹配会分到第一条（语义漂移）",
        "tests": ["tests/test_milestone.py"],
    },
    {
        "id": "milestone_cap_removed",
        "file": "app/fields.py",
        "old": "        out.append({\"name\": name, \"done\": bool(item.get(\"done\"))})\n        if len(out) >= MAX_MILESTONES:",
        "new": "        out.append({\"name\": name, \"done\": bool(item.get(\"done\"))})\n        if len(out) >= MAX_MILESTONES + 999:",
        "why": "里程碑上限失效：海量里程碑写入核心数据文件并被整份解析，追加型存储失控",
        "tests": ["tests/test_milestone.py"],
    },
    {
        "id": "milestone_task_name_not_trimmed",
        "file": "app/schemas.py",
        "old": '        v = (v or "").strip()\n        return v[:MAX_MILESTONE_NAME] if v else ""',
        "new": '        v = (v or "")\n        return v[:MAX_MILESTONE_NAME] if v else ""',
        "why": "任务 milestone 归属不去首尾空白：『 甲 』与『甲』归不进同一组，进度条错",
        "tests": ["tests/test_milestone.py"],
    },
    {
        # 2026-10-11 新增：claude-mem Awareness 对齐后记的坑被背景事实挤出窗口 = 交接丢最贵的知识。
        # 变异抹平类型权重（退回纯时间序），新增的加权召回钉子必须变红，否则规则没有门禁。
        "id": "memory_recall_not_weighted",
        "file": "app/due.py",
        "old": '_MEMORY_RECALL_WEIGHT = {"决策": 0, "坑": 0, "约定": 0, "事实": 1}\n',
        "new": '_MEMORY_RECALL_WEIGHT = {"决策": 1, "坑": 1, "约定": 1, "事实": 1}\n',
        "why": "记忆召回退回纯时间序：晚写的背景「事实」把更早的「坑/决策」挤出 AGENTS.md/模型简报窗口",
        "tests": ["tests/test_p1_absorb.py"],
    },
    {
        # 2026-10-11 新增：条目级尺寸恒 0 = 消费方按"几乎不占地方"做预算，渐进披露的
        # 决策依据整体失效 —— 而界面上它看起来"有个数字"，不是缺字段那种一眼可见的坏。
        "id": "memory_size_always_zero",
        "file": "app/handoff_meta.py",
        "old": '    return {"chars": len(text), "kb": round(len(text.encode("utf-8")) / 1024, 2),\n'
               '            "est_tokens": estimate_tokens(text)}\n',
        "new": '    return {"chars": 0, "kb": 0.0, "est_tokens": 0}\n',
        "why": "记忆条目尺寸恒为 0：消费方按偏小的预算决定要不要读，渐进披露的决策依据失效",
        "tests": ["tests/test_p1_absorb.py"],
    },
    {
        # 2026-10-18 新增（v1.7.3 雷达收口轮）：雷达首扫带未提交文件的行为此前无测试无探针。
        "id": "repo_watch_first_dirty_ignored",
        "file": "app/repo_watch.py",
        "old": '    changed = bool(prev.get("last_seen_signature") and prev["last_seen_signature"] != sig) or (first and snap.get("dirty_count", 0) > 0)',
        "new": '    changed = bool(prev.get("last_seen_signature") and prev["last_seen_signature"] != sig)',
        "why": "首见仓库时已有未提交文件不再提醒：雷达看见了脏仓库却装着没看见（首扫静默）",
        "tests": ["tests/test_repo_watch.py"],
    },
    {
        "id": "repo_watch_suggestion_pulls_progress_down",
        "file": "app/repo_watch.py",
        "old": "    suggested = completion if (completion is not None and completion > current) else None",
        "new": "    suggested = completion if completion is not None else None",
        "why": "任务完成度低于当前进度时也给建议：点一次「确认并同步」把进度往回拽",
        "tests": ["tests/test_repo_watch.py"],
    },
    {
        "id": "repo_watch_audit_uncapped",
        "file": "app/repo_watch.py",
        "old": "    if len(lines) <= MAX_AUDIT_LINES:\n        return",
        "new": "    if True:\n        return",
        "why": "确认审计不再裁剪：追加型存储无上限（硬性约定 11），recent_audit 的整读成本无界增长",
        "tests": ["tests/test_repo_watch.py"],
    },
    {
        "id": "repo_watch_overview_bypasses_cache",
        "file": "app/main.py",
        "old": 'repo_metrics.scan_projects([p for p in projects if p.get("repo_path")], use_cache=not pid)',
        "new": 'repo_metrics.scan_projects([p for p in projects if p.get("repo_path")], use_cache=False)',
        "why": "总览轮询也每次都冷扫：每 30s 对全板仓库起一轮 git 子进程（8 库实测 1.6s / 40 次调用）",
        "tests": ["tests/test_repo_watch.py"],
    },
    {
        "id": "repo_watch_delete_leaves_orphan_baseline",
        "file": "app/main.py",
        "old": "    repo_metrics.forget(pid)\n",
        "new": "",
        "why": "删除项目不再清雷达基线：repo_watch.json 里孤儿条目永存（记忆层有过同款纪律）",
        "tests": ["tests/test_repo_watch.py"],
    },
    {
        "id": "repo_ack_note_uncapped",
        "file": "app/schemas.py",
        "old": '    note: str = Field("", max_length=MAX_REPO_ACK_NOTE)',
        "new": '    note: str = ""',
        "why": "确认备注不设上限：一次请求就能把一行审计撑成任意长（行数上限挡不住行内膨胀）",
        "tests": ["tests/test_repo_watch.py"],
    },
    {
        # 2026-10-18 晚（v1.7.3 续）：雷达基线随备份/恢复同拍同回滚的三条规则。
        "id": "backup_drops_watch_state",
        "file": "app/main.py",
        "old": '    for name in ("projects.json", "config.json", "memories.json", "repo_watch.json"):',
        "new": '    for name in ("projects.json", "config.json", "memories.json"):',
        "why": "备份不带雷达基线：回滚后项目数据回到过去、基线却停在现场，两者不是同一时刻的对子",
        "tests": ["tests/test_restore.py"],
    },
    {
        "id": "restore_clears_watch_state_on_bad_source",
        "file": "app/main.py",
        "old": "        if isinstance(loaded_watch, dict):\n            watch_count = repo_metrics.replace_state(loaded_watch)",
        "new": "        watch_count = repo_metrics.replace_state(loaded_watch if isinstance(loaded_watch, dict) else {})",
        "why": "来源里基线坏了/缺了就当空状态清空：老备份或截断文件一次恢复把现有基线抹掉",
        "tests": ["tests/test_restore.py"],
    },
    {
        "id": "restore_reports_fake_watch_rollback",
        "file": "app/main.py",
        "old": "    if not same_watch_as_live and watch_src.exists():",
        "new": "    if watch_src.exists():",
        "why": "last-good 等与线上同目录的来源也被当成“备份”：把现值盖回现值，还报出一个假回滚",
        "tests": ["tests/test_restore.py"],
    },
    {
        # 2026-10-19 第六轮吸取：blocked_reason 与状态同生共死（离开阻塞必须清）。
        "id": "blocked_reason_survives_unblock",
        "file": "app/main.py",
        "old": '    else:\n        t.pop("blocked_reason", None)\n    if body.status == "已完成":',
        "new": '    else:\n        pass\n    if body.status == "已完成":',
        "why": "离开阻塞后原因字段留在盘上：字段语义不闭合（卡片虽按状态门控，交接导出与将来新增的读路径未必）",
        "tests": ["tests/test_api.py"],
    },
    {
        "id": "stale_done_never_flags",
        "file": "app/repo_watch.py",
        "old": "        dd = _as_dt(str(t.get(\"done_at\") or \"\"))\n        if dd is None or dd >= hd:\n            continue",
        "new": "        dd = _as_dt(str(t.get(\"done_at\") or \"\"))\n        if False:\n            continue",
        "why": "「验收后代码又变了」判定失效：没时间戳/与提交同分钟的完成全被报成待复核（噪声淹掉真问题）",
        "tests": ["tests/test_repo_watch.py"],
    },
    {
        "id": "memory_dedupe_returns_fresh_entry",
        "file": "app/main.py",
        "old": '                return False, {"ok": True, "deduped": True, "memory": m}',
        "new": '                return False, {"ok": True, "deduped": True, "memory": entry}',
        "why": "去重命中却返回盘上不存在的 id：调用方拿它删除/引用会 404（2026-10-19 修的旧缺陷）",
        "tests": ["tests/test_api.py", "tests/test_v110_features.py"],
    },
    {
        "id": "refs_url_flagged_missing",
        "file": "app/gitinfo.py",
        "old": '            if "://" in sref:\n                continue',
        "new": "            if False:\n                continue",
        "why": "带 :// 的 URL 引用被当本机坐标检查：外部文档/PR 链接全进 missing（真问题被噪音淹）",
        "tests": ["tests/test_git.py"],
    },
    {
        # 2026-10-19 v1.8.1：空回复按成功直接返回（实盘：推理模型烧光 max_tokens 后
        # content 空、HTTP 200，降级链不触发，上层拿到 500「LLM 返回为空」）。
        "id": "llm_empty_reply_taken_as_success",
        "file": "app/llm.py",
        "old": "                if _empty_reply(content) or finish == \"length\":\n"
               "                    _record_usage(feature or \"未标注\", ep[\"name\"], model, usage, time.time() - t0, False)\n"
               "                    errors.append(f\"{ep['name']}/{model}: 返回为空/截断\"\n"
               "                                  f\"（提升 max_tokens={MAX_TOKENS * 4} 重试仍失败 finish={finish}）\")\n"
               "                    time.sleep(0.4)\n"
               "                    continue",
        "new": "                if False and (_empty_reply(content) or finish == \"length\"):\n"
               "                    _record_usage(feature or \"未标注\", ep[\"name\"], model, usage, time.time() - t0, False)\n"
               "                    errors.append(f\"{ep['name']}/{model}: 返回为空/截断\"\n"
               "                                  f\"（提升 max_tokens={MAX_TOKENS * 4} 重试仍失败 finish={finish}）\")\n"
               "                    time.sleep(0.4)\n"
               "                    continue",
        "why": "空回复/截断当成功返回：别的可用端点永远轮不到，症状是随机的 500 与静默降级"
               "（本地兜底版冒充 AI 版）。2026-10-24 随预算提升改写：盯的是提升重试**之后**的"
               "那次拒绝检查 —— 它失效时，4 倍预算也救不回的空/截断内容会被当成功直接返回",
        "tests": ["tests/test_llm_gateway.py"],
    },
    {
        "id": "next_ready_drops_blocked_mark",
        "file": "app/main.py",
        "old": "    if d[\"status\"] == \"阻塞\":\n        d[\"blocked_reason\"] = mask(t.get(\"blocked_reason\") or \"\")",
        "new": "    if False:\n        d[\"blocked_reason\"] = mask(t.get(\"blocked_reason\") or \"\")",
        "why": "「可做」条目丢掉阻塞原因：/next 与接手包 start_here 的阻塞任务看起来和普通待办一样，代理照单开工",
        "tests": ["tests/test_task_deps.py"],
    },
    {
        "id": "mcp_next_unmarked_blocked",
        "file": "app/mcp_server.py",
        "old": "    if t.get(\"status\") != \"阻塞\":\n"
               "        return \"\"\n"
               "    reason = t.get(\"blocked_reason\") or \"\"\n"
               "    return f\" · ⛔ 阻塞中{'：' + reason if reason else ''}（先解阻塞，别直接开工）\"",
        "new": "    return \"\"",
        "why": "MCP 可做列表不带 ⛔ 标记：/today 里的阻塞标记到了 next_task 就消失，代理拿到的『现在可以做』是句谎话",
        "tests": ["tests/test_mcp_server.py"],
    },
    {
        # 2026-10-21 第七轮竞品吸取：接手包 sections/since（上下文切片与增量）
        "id": "context_unknown_section_ignored",
        "file": "app/context_slice.py",
        "old": "        if name not in CONTEXT_SECTIONS:\n"
               "            raise HTTPException(\n"
               "                400, f\"{param} 不认识「{name}」，可选：{_SECTION_HINT}（project 始终返回）\")\n"
               "        out.add(name)",
        "new": "        if name not in CONTEXT_SECTIONS:\n"
               "            continue\n"
               "        out.add(name)",
        "why": "不认识的 sections 被静默忽略：调用方以为只要了 tasks，实际拿到一整包（或以为字段名拼对了）",
        "tests": ["tests/test_pure_modules.py", "tests/test_api.py"],
    },
    {
        "id": "context_since_exclusive",
        "file": "app/context_slice.py",
        "old": "    return parsed.strftime(\"%Y-%m-%d %H:%M\") >= since[:16]",
        "new": "    return parsed.strftime(\"%Y-%m-%d %H:%M\") > since[:16]",
        "why": "since 改成开区间：与 generated_at 同分钟发生的改动会漏掉（重复可忍，漏一条会让代理以为没发生）",
        "tests": ["tests/test_pure_modules.py", "tests/test_api.py"],
    },
    {
        "id": "context_delta_filters_start_here",
        "file": "app/main.py",
        "old": "    ready = executable_tasks(tasks) if wanted_section(\"start_here\") else []",
        "new": "    ready = ([t for t in executable_tasks(tasks) if task_changed(t)]\n"
               "             if wanted_section(\"start_here\") else [])",
        "why": "增量包把 start_here 也裁成「只列变化的」：没变化时看起来没事可做，恰恰丢掉接手方最需要的一节",
        "tests": ["tests/test_api.py"],
    },
    {
        "id": "project_readiness_not_in_detail",
        "file": "app/main.py",
        "old": '    p["readiness"] = {"score": _readiness_score(p), "items": _readiness_items(p)}',
        "new": '    p.pop("readiness", None)',
        "why": "详情不再随项目返回就绪度：前端只能退回全局 stats 旧快照，建完任务左侧还写着「没有任务清单」",
        "tests": ["tests/test_api.py"],
    },
    {
        "id": "today_blocked_reason_dropped",
        "file": "app/main.py",
        "old": '                "blocked_reason": t.get("blocked_reason") or "",',
        "new": '                "blocked_reason": "",',
        "why": "今日列表不再带阻塞原因：代理只看到「阻塞中」，仍可能把它当普通待办直接开工",
        "tests": ["tests/test_api.py", "tests/test_mcp_server.py"],
    },
    {
        "id": "repo_watch_baseline_write_unlocked",
        "file": "app/repo_watch.py",
        "old": '            with blocking_lock(_state_file().with_suffix(".lock"), timeout=10.0,\n'
               '                               what="仓库雷达基线"):',
        "new": '            if True:  # 探针：基线读改写退回只有线程锁',
        "why": "基线 repo_watch.json 的读-改-写退回只有线程锁：第二个服务进程写同一份时"
               "「A 读 → B 读 → A 写 → B 写」，A 的改动静默消失（没有任何报错）",
        "tests": ["tests/test_repo_watch.py"],
    },
    # ---- 前端（web/js-*.js）：2026-10-25 首批 ----
    # 此前前端只有 3 条探针，且全在 style.css / index.html 的**布局形状**上；
    # 10 个 js-*.js（全部交互逻辑）零覆盖。这批每条都挑「有明确 killer」的语义：
    # 静态的走 tests/test_frontend.py（快），行为只能开浏览器的走 E2E 单点（慢但真）。
    {
        "id": "js_open_report_handler_drops_data_arg",
        "file": 'web/js-core.js',
        "old": '  openReport: (arg) => openReport(arg),',
        "new": '  openReport: () => openReport(),',
        "why": '把 2026-09-20 那个真实事故原样放回去：处理器不收 data-arg，「回看某份历史报告」变成「重新跑一次分析」—— 按钮看着有反应，做的却是另一件事，连截图都看不出差别',
        "tests": ['tests/test_frontend.py::test_actions_that_receive_data_arg_actually_forward_it'],
    },
    {
        "id": "js_esc_stops_escaping_angle_brackets",
        "file": 'web/js-core.js',
        "old": '  return String(s ?? "").replace(/[&<>"\']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;" }[c]));',
        "new": '  return String(s ?? "").replace(/[&"\']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;" }[c]));',
        "why": '转义表不再管尖括号：项目名里的 <b> 会被当标签渲染（注入），而卡片文本看着还是那个名字',
        "tests": ['tests/test_frontend_e2e.py::test_special_chars_in_name_do_not_break_buttons'],
    },
    {
        "id": "js_edit_form_backfills_from_list_again",
        "file": 'web/js-edit.js',
        "old": '    try { full = await api("/api/projects/" + id); } catch (e) { full = null; }',
        "new": '    try { full = await api("/api/projects"); } catch (e) { full = null; }',
        "why": '编辑表单又改从总览列表回填：列表接口不含 background / handoff_note，而 PUT 是整体覆盖 —— 一保存就把用户手写的两段清空，界面毫无痕迹',
        "tests": ['tests/test_frontend.py::test_project_edit_form_is_filled_from_the_single_project_endpoint'],
    },
    {
        "id": "js_milestone_delete_by_parent_again",
        "file": 'web/js-edit.js',
        "old": '  const row = btn.closest(".ms-row");',
        "new": '  const row = btn.parentElement.parentElement;',
        "why": '里程碑行删除退回「按 DOM 位置找行」：data-arg 是渲染时写死的序号，删过一行后与 DOM 下标错位，点第三行的 ✕ 会删错行甚至删不到',
        "tests": ['tests/test_frontend.py::test_milestone_rows_delete_by_element_not_stale_index'],
    },
    {
        "id": "js_nomatch_claims_empty_board_again",
        "file": 'web/js-overview.js',
        "old": '    msg.textContent = `没有匹配「${q}」的项目（共 ${projects.length} 个）—— 换个词，或清空搜索框`;',
        "new": '    msg.textContent = "还没有项目。先建一个，把交接说明和下一步写进去；换智能体时一键复制即可。";',
        "why": '搜索打了个不匹配的词就宣布「还没有项目」—— 手里有 5 个项目的人看着像数据被谁清空了（三种「屏幕上没卡片」必须分开说：真没项目 / 筛选空 / 搜索不匹配）',
        "tests": ['tests/test_frontend_e2e.py::test_search_with_no_match_does_not_claim_the_board_is_empty'],
    },
    # ---- 前端（web/js-*.js）第二批：2026-10-25，每条都把当年那个真 bug 放回去 ----
    # 成本说明：其中两条是**行为**判据（分歧文案、五列归位），静态契约碰不到它们 ——
    # 全仓确认过没有第二处钉子 —— 于是红的时候要等满 E2E 的超时（实测 54s / 39s/条）。
    # 这个成本是有意接受的：规则本身只有开浏览器才验得了；另两条（PATCH / 截断留痕）
    # 用静态或快速 E2E，分别 8.9s / 2.5s。
    {
        "id": "js_verdict_hidden_without_repo_path",
        "file": "web/js-reality.js",
        "old": 'function realityVerdicts(p, g) {\n  const out = [];',
        "new": 'function realityVerdicts(p, g) {\n  const out = [];\n  if (!p.repo_path) return out;   // 探针：又把与 git 无关的判据挡在门外',
        "why": '把那个真实事故放回去：`!p.repo_path` 一上来就 return，把**与 git 无关**的两条判据（「进度写 90% 却一条任务没完成」）一起挡在门外 —— 而「没填代码路径」恰恰是多数项目的常态，最需要被指出的项目反而永远听不到这句话',
        "tests": ['tests/test_frontend_e2e.py::test_progress_vs_tasks_verdict_shows_without_a_repo_path'],
    },
    {
        "id": "js_blocked_task_lands_in_todo_column",
        "file": "web/js-detail.js",
        "old": '  const cols = { "待办": "col-todo", "进行中": "col-doing", "待审查": "col-review", "已完成": "col-done", "阻塞": "col-blocked" };',
        "new": '  const cols = { "待办": "col-todo", "进行中": "col-doing", "待审查": "col-review", "已完成": "col-done", "阻塞": "col-todo" };',
        "why": '把「阻塞」又兜底塞进待办列：阻塞列永远是空的，卡住的任务看起来只是「还没开始」（2026-10-01 之前就是这个形状，而套件里当时没有任何用例看过 col-blocked）',
        "tests": ['tests/test_frontend_e2e.py::test_task_statuses_land_in_their_own_column'],
    },
    {
        "id": "js_ai_update_uses_whole_put_again",
        "file": "web/js-ai.js",
        "old": '    await api("/api/projects/" + a.project_id, { method: "PATCH",',
        "new": '    await api("/api/projects/" + a.project_id, { method: "PUT",',
        "why": '单字段改动退回「读整份 → 改一处 → PUT 整份」：那次读发生在临界区外，期间别人写进同一项目的字段会被带回来的旧值反向抹掉（约定 1 的形状），而界面看起来只是「改了个进度」',
        "tests": ['tests/test_frontend_e2e.py::test_ai_command_update_sends_a_patch_not_a_read_modify_put'],
    },
    {
        "id": "js_search_truncation_hint_dropped",
        "file": "web/js-data.js",
        "old": '        + `只列出前 ${esc(String(r.limit || list.length))} 条，后面还有 —— 换个更具体的关键词</div>`',
        "new": '        + `只列出前 ${esc(String(r.limit || list.length))} 条 —— 换个更具体的关键词</div>`',
        "why": '命中到上限却不说「后面还有」：第 31 条在用户眼里就是不存在，而界面看起来一切正常（与 MCP 那条同源：接口回 truncated/limit，人看到的版本也得说）',
        "tests": ['tests/test_frontend.py::test_global_search_tells_the_user_the_list_was_cut'],
    },
    # ---- 前端第三批：2026-10-25，全部用**静态**判据做 killer（每条 ~2.5s）----
    {
        "id": "js_stats_sum_forgets_review_status",
        "file": "web/js-render.js",
        "old": '    const n = ["待办", "进行中", "待审查", "已完成", "阻塞"].reduce((a, k) => a + (bs[k] || 0), 0);',
        "new": '    const n = ["待办", "进行中", "已完成", "阻塞"].reduce((a, k) => a + (bs[k] || 0), 0);',
        "why": '统计面板的状态求和漏了「待审查」（2026-10-06 加第五列时漏改前端的那个死角）：项目里只有待审查任务时，面板会说「该项目还没有任务」，与看板第五列直接矛盾',
        "tests": ['tests/test_frontend.py::test_stats_panel_counts_all_five_statuses'],
    },
    {
        "id": "js_execute_result_skips_toast",
        "file": "web/js-ai.js",
        "old": '  toast(fail ? "已执行：成功 " + ok + " 个，失败 " + fail + " 个" : "已执行 " + ok + " 个操作 ✓");',
        "new": '  void (fail ? "已执行：成功 " + ok + " 个，失败 " + fail + " 个" : "已执行 " + ok + " 个操作 ✓");',
        "why": '「确认执行」的结果只留在聊天气泡里，不再播报到 toast（全站唯一的 aria-live 区域）：弹窗一关，键盘/读屏用户就无从得知到底成没成（硬性约定 14）',
        "tests": ['tests/test_frontend.py::test_action_results_are_announced_outside_the_modal'],
    },
    {
        "id": "js_scan_cleanup_leaves_finally",
        "file": "web/js-boot.js",
        "old": '  } catch (e) { list.innerHTML = \'<div style="color:var(--red);font-size:12px">扫描失败：\' + esc(e.message) + "</div>"; }\n  finally { btn.classList.remove("ai-loading-btn"); }   // 空结果那条分支以前直接 return，按钮永久转圈',
        "new": '  } catch (e) { list.innerHTML = \'<div style="color:var(--red);font-size:12px">扫描失败：\' + esc(e.message) + "</div>"; btn.classList.remove("ai-loading-btn"); }',
        "why": '扫描的收尾从 finally 挪进 catch：空结果那条分支是**直接 return** 的，于是按钮永久停在转圈态（本机有真实会话、走不到空分支，所以只能静态钉）',
        "tests": ['tests/test_frontend.py::test_scan_button_uses_finally_for_its_loading_state'],
    },
    {
        "id": "js_esc_close_bypasses_close_one_modal",
        "file": "web/js-edit.js",
        "old": '  if (e.key === "Escape") { closeTopModal(); return; }',
        "new": '  if (e.key === "Escape") { const t = topLayer(); if (t) document.getElementById(t.id).classList.remove("show"); return; }',
        "why": 'Esc 绕过 closeOneModal 直接摘 .show：弹窗少一次动画复位（只有第一次打开有 pop），而且不清 pendingActions —— 重开指令窗口后点「确认执行」会执行上一次的解析结果（硬性约定 3）',
        "tests": ['tests/test_frontend.py::test_all_modal_close_paths_share_one_implementation'],
    },
    {
        "id": "js_clear_search_skips_button_sync",
        "file": "web/js-overview.js",
        "old": 'function clearSearch() {\n  if (!searchInput) return;\n  searchInput.value = "";\n  if (activeViewId) {\n    activeViewId = "";\n    syncViewSelect();\n  }\n  updateSearchClear();\n  renderOverview();\n  searchInput.focus();',
        "new": 'function clearSearch() {\n  if (!searchInput) return;\n  searchInput.value = "";\n  if (activeViewId) {\n    activeViewId = "";\n    syncViewSelect();\n  }\n  renderOverview();\n  searchInput.focus();',
        "why": '清空搜索后不再刷新按钮自身的显隐：清空按钮留在原地，与「有输入才出现」这条规则自相矛盾',
        "tests": ['tests/test_frontend.py::test_search_clear_button_appears_only_with_input'],
    },
    # ---- 前端第四批：2026-10-25，全部静态判据（每条都有真事故记载）----
    {
        "id": "js_set_filter_keeps_stale_view_name",
        "file": "web/js-overview.js",
        "old": 'function setFilter(el, f) {\n  filter = f;\n  activeViewId = "";                                   // 手动改筛选 = 脱离已保存视图快照\n  syncViewSelect();                                    // 下拉还显示着已脱离的视图名会误导（搜索框路径是同步的）\n  document.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));\n  el.classList.add("active");\n  renderOverview();\n}',
        "new": 'function setFilter(el, f) {\n  filter = f;\n  activeViewId = "";                                   // 手动改筛选 = 脱离已保存视图快照\n  document.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));\n  el.classList.add("active");\n  renderOverview();\n}',
        "why": '手动改筛选（点 chip）后不把视图下拉同步回占位：下拉还挂着已保存视图的名字，用户会以为当前筛选仍是那个视图 —— 搜索框那条路径早就同步了，chip 这条当初漏了',
        "tests": ['tests/test_frontend.py::test_set_filter_detaches_the_saved_view_in_the_dropdown_too'],
    },
    {
        "id": "js_keyboard_activation_only_enter",
        "file": "web/js-edit.js",
        "old": 'document.addEventListener("keydown", e => {\n  if (e.key === "Escape") { closeTopModal(); return; }\n  if (e.key !== "Enter" && e.key !== " ") return;\n  const el = document.activeElement;\n  if (el && el.dataset && el.dataset.keyact && el.click) { e.preventDefault(); el.click(); }\n});',
        "new": 'document.addEventListener("keydown", e => {\n  if (e.key === "Escape") { closeTopModal(); return; }\n  if (e.key !== "Enter") return;\n  const el = document.activeElement;\n  if (el && el.dataset && el.dataset.keyact && el.click) { e.preventDefault(); el.click(); }\n});',
        "why": '键盘激活只认 Enter、不认空格：卡片是 div，能 Tab 到却按空格没反应 —— 而空格才是按钮类的常规键（可点卡片的 a11y 契约就是这两条一起给）',
        "tests": ['tests/test_frontend.py::test_clickable_cards_are_keyboard_reachable'],
    },
    {
        "id": "js_toast_fixed_short_timeout",
        "file": "web/js-core.js",
        "old": '  const ms = Math.min(7000, 2200 + Math.max(0, String(msg).length - 24) * 60);',
        "new": '  const ms = 2200;',
        "why": 'toast 退回固定 2.2 秒：保存路径、失败原因常常很长，读不完就消失了 —— 按时长放宽（上限 7 秒）正是为这个加的',
        "tests": ['tests/test_frontend.py::test_toast_gives_long_messages_time_to_be_read'],
    },
    {
        "id": "js_toast_can_overflow_the_screen",
        "file": "web/style.css",
        "old": '  max-width: min(760px, calc(100vw - 40px)); white-space: pre-wrap; word-break: break-word;',
        "new": '  max-width: 9000px;',
        "why": 'toast 不再限制宽度：一条长路径（几百字符不换行）会把整条吐司撑出屏幕外，用户只看到半句 —— max-width + word-break 是配套的',
        "tests": ['tests/test_frontend.py::test_toast_gives_long_messages_time_to_be_read'],
    },
    {
        "id": "status_alias_ignored",
        "file": "app/fields.py",
        "old": "        if key == _norm_key(canonical) or key in aliases:",
        "new": "        if key == _norm_key(canonical):",
        "why": "别名只认规范值本身、不再认英文名：于是 `status=\"in_progress\"` / "
               "`priority=\"high\"` / `type=\"pitfall\"` 全都被当「不认识的值」往下传，"
               "HTTP 侧撞 Literal 的 422、MCP 侧撞 enum —— 也就是英文 agent 又只能靠中文。"
               "这条钉的是「任何 agent 都能接手」那句承诺里唯一货不对板的地方。",
        "tests": ["tests/test_api.py::test_english_status_and_priority_aliases_are_normalized",
                  "tests/test_mcp_server.py::test_english_aliases_work_over_mcp"],
    },
    {
        "id": "port_default_written_twice",
        "file": "app/handoff_page.py",
        "old": '                       port: str = DEFAULT_PORT, generated_at: str = "",',
        "new": '                       port: str = "18320", generated_at: str = "",',
        "why": "端口兜底值又被写死一份：桌面壳起在 18321 时，接手页里那三条实时直链"
               "仍指向 18320 —— 接手方点一个没人听的端口，而且不报任何错",
        "tests": ["tests/test_suite_hygiene.py"],
    },
    {
        "id": "sample_dates_hardcoded_again",
        "file": "app/sampledata.py",
        "old": "    def at(days_ago: int = 0) -> str:\n"
               "        return (now - timedelta(days=days_ago)).strftime(\"%Y-%m-%d %H:%M\")",
        "new": "    def at(days_ago: int = 0) -> str:\n"
               "        return \"2026-09-14 09:30\"",
        "why": "示例时间戳退回写死：新装用户过几周就看到「37 天没记录」和成排停滞告警",
        "tests": ["tests/test_misc.py"],
    },
    {
        "id": "memory_form_stops_wrapping",
        "file": "web/style.css",
        "old": "#memory-form { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }",
        "new": "#memory-form { display: flex; flex-wrap: nowrap; gap: 8px; margin-bottom: 8px; }",
        "why": "记忆表单不换行：四个控件在 328px 侧栏里把「存」按钮挤出面板 88px，点上去命中隔壁列",
        "tests": ["tests/test_frontend.py", "tests/test_frontend_e2e.py"],
    },
    {
        "id": "memory_text_input_cannot_shrink",
        "file": "web/style.css",
        "old": "#memory-form #memory-text { flex: 1 1 120px; min-width: 0; }",
        "new": "#memory-form #memory-text { flex: 1 1 120px; }",
        "why": "输入框保留默认 min-width：内容再短也不肯收缩，整行照样溢出面板",
        "tests": ["tests/test_frontend.py", "tests/test_frontend_e2e.py"],
    },
    {
        "id": "detail_head_back_into_scroll_area",
        "file": "web/index.html",
        "old": '  <div class="detail-scroll">\n    <div id="repo-watch"',
        "new": '  <div class="detail-scroll d-head">\n    <div id="repo-watch"',
        "why": "头栏与滚动区被合成一个块（复制类名的典型手误）：滚动时头部跟着内容跑，或反过来盖住内容",
        "tests": ["tests/test_frontend.py"],
    },
    {
        "id": "restore_safety_copy_failure_silent",
        "file": "app/main.py",
        "old": '                safety["state"] = "failed"\n'
               '                safety["error"] = f"{type(exc).__name__}: {exc}"',
        "new": "                pass",
        "why": "恢复前安全副本失败被吞：确认框刚承诺过「当前数据会先另存为 before-restore.bak」，"
               "拷贝失败却照旧回 ok:true —— 用户以为手里有退路，其实没有，且全程没提示",
        "tests": ["tests/test_restore.py"],
    },
    {
        "id": "agent_files_credential_warning_dropped",
        "file": "app/main.py",
        "old": "    sensitive_hits = count_sensitive(content)",
        "new": "    sensitive_hits = 0",
        "why": "入口文件里的凭据不再报数：这份文档会被写进用户的仓库（通常要 git push），"
               "不报数就等于让人蒙着眼把 Key 提交进版本史",
        "tests": ["tests/test_agent_files.py"],
    },
    {
        "id": "agent_files_redact_switch_ignored",
        "file": "app/main.py",
        "old": "        entries = {rel: redact_text(text) for rel, text in entries.items()}",
        "new": "        entries = dict(entries)",
        "why": "「写前脱敏」开关被无视：勾了还是把原文写进仓库，而回执与界面都会宣称已脱敏 ——"
               "这正是「开关看着有反应、做的却是另一件事」的形状",
        "tests": ["tests/test_agent_files.py"],
    },
    {
        "id": "reasoning_budget_lift_removed",
        "file": "app/llm.py",
        "old": "max_tokens=MAX_TOKENS * 4",
        "new": "max_tokens=MAX_TOKENS",
        "why": "推理烧光/截断后的预算提升被退回原预算：同 Key 重试等于原样重放，"
               "推理型模型依旧空返回/截断，监管分析继续连败（2026-10-24 实测 4 连败的根因）",
        "tests": ["tests/test_llm_gateway.py"],
    },
    {
        "id": "milestones_null_passes_through",
        "file": "app/main.py",
        "old": "            p[\"milestones\"] = []",
        "new": "            p.pop(\"milestones\", None)",
        "why": "存量的 null 里程碑原样透传给读路径：读改写客户端 GET 拿到 null、PUT 422，"
               "「读-改-写」在 API 边界断掉（2026-10-24 实测）",
        "tests": ["tests/test_milestone.py"],
    },
]


def _js_syntax_ok(path: Path) -> tuple[bool, str]:
    """变异后的 JS 必须**语法仍然合法**，否则这次「红」不算数。

    为什么需要它（2026-10-25，随第一批 js 探针一起加）：JS 一旦语法错，整页脚本不执行、
    E2E 会全红 —— 那看起来像「被杀」，其实测的是「页面整个坏了」，与「语义改动被抓住」
    是两件事（Python 侧的对应情形是导入失败 → pytest 收集错误 → 状态是 error 而非 killed）。
    没有 node 时报「无法判定」，也算 error：**宁可红，不可伪绿** ——
    与门禁的跳过审计同一口径（跳过 = 没验，不是验过）。
    """
    node = shutil.which("node")
    if not node:
        return False, "本机没有 node，无法确认变异后的 JS 语法仍然合法（这条不算被杀）"
    r = subprocess.run([node, "--check", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip().splitlines()
        return False, ("变异把 JS 改出了语法错 —— 这种红是「页面整个坏了」，不算语义被抓："
                       + (detail[0][:150] if detail else ""))
    return True, ""


def run_tests(files: list[str], timeout: int) -> tuple[str, float]:
    """跑测试子集。返回 (killed|survived|error, 耗时)。"""
    t0 = time.time()
    try:
        r = subprocess.run(
            [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-cov",
             "-x", "-n", "0", *files],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "error", time.time() - t0
    dt = time.time() - t0
    if r.returncode == 0:
        return "survived", dt
    if r.returncode == 1:                       # 有用例失败 = 变异被抓到
        return "killed", dt
    return "error", dt                          # 2=中断 3=内部错误 4=用法错 5=没收集到


def _restore_mtime(path: Path, st: os.stat_result) -> None:
    """把还原后的文件时间戳拨回注入之前。

    为什么必须拨回去：`frozen_smoke` 判断"这个 exe 是不是落后于源码"靠的正是
    `app/*.py` / `web/*` 的 mtime 与构建戳比较 —— 后端被 PyInstaller 打进 .pyc，
    没法逐字节比对，mtime 是唯一可用的代理。而本脚本每跑一次都会把探过的文件
    逐个改写两遍（注入一遍、还原一遍），不拨回去它们就全部变成"刚刚改过"，
    于是**每次全量探针之后那条门禁都会稳定假红**，直到重打包为止。
    实测（2026-10-18）：`frozen_smoke` 10 通过 / 1 失败，失败项点名 aiprompt.py、
    assign.py、due.py、handoff_meta.py、idem.py、journal.py 等一串"刚改过"的文件 ——
    而 exe 内嵌前端的字节比对是同过的，说明产物其实不旧。一条本该只报"改了源码
    忘重打包"的门禁被噪音盖住，人很快会对这个红点脱敏，那才是真损失。
    """
    try:
        os.utime(path, (st.st_atime, st.st_mtime))
    except OSError:                                             # pragma: no cover
        pass


def preflight(todo: list[dict] | None = None, root: Path | None = None) -> list[tuple[dict, str]]:
    """第 0 秒预检：每条待跑变异的 `old` 必须能命中，返回 (变异, 问题) 列表（空 = 干净）。

    为什么值得单独一步：`apply_and_run` 对"old 命不中"只会沉默地记一条 inconclusive，
    几分钟后混进"存活/异常 N"的计数里 —— 2026-10-24 实测：一处残渣让 pytest 先红
    80 秒、探针再空跑 8.5 分钟，结论里却只有一行谁也读不懂的计数，根因还要靠人去
    对着变异表逐字比对才发现。两种死法必须在开跑前说破：

    - old 缺、new 在（且 new 非空）：**残渣** —— 上次探针被外部信号打断（SIGTERM 等
      进程外击杀不会走到 finally），注入文本留在源码里。处置：`git checkout -- <file>`
      （该文件若另有手工改动就手工摘出那一行）；删除型变异的 new 为空，这条路判不出来。
    - 其余 old 缺：**表没同步** —— 源码改了而 MUTATIONS 没跟上，这条探针已经哑火
      （硬性约定里"改被探针盯着的代码要同步改 old"防的就是这个）。
    """
    root = root if root is not None else ROOT
    todo = MUTATIONS if todo is None else todo
    contents: dict[str, str | None] = {}
    problems: list[tuple[dict, str]] = []
    for m in todo:
        f = str(m["file"])
        if f not in contents:
            try:
                contents[f] = (root / m["file"]).read_text(encoding="utf-8")
            except OSError:
                contents[f] = None
        text = contents[f]
        if text is None:
            problems.append((m, "文件读不到"))
        elif m["old"] not in text:
            if str(m["new"]).strip() and m["new"] in text:
                problems.append((m, "残渣：注入文本还在源码里（上次中断未还原）"
                                    f"→ git checkout -- {m['file']}"
                                    "（该文件若另有手工改动，先手工摘出这一行再重跑）"))
            else:
                problems.append((m, "old 命不中：源码改了、MUTATIONS 没同步"
                                    "（或删除型变异的残渣）—— 对照现源码同步 old/new"))
    return problems


def _native(text: str, crlf: bool) -> str:
    """把变异表里**按 LF 写**的文本，适配成这个文件自己的行尾。

    变异表是人写的（一律 LF）；而工作区里的文件行尾可能两样都有（本仓没有 `.gitattributes`，
    `core.autocrlf` 让同一目录里 CRLF 与 LF 混着）。匹配与替换都在**文件自己的文本**上做，
    所以表里的串要先换成文件那一套 —— 否则 CRLF 文件里根本匹配不上。
    """
    return text.replace("\n", "\r\n") if crlf else text


def apply_and_run(m: dict, timeout: int) -> dict:
    """注入 → 跑 killer → **按原始字节**还原。

    还原为什么用 `write_bytes(raw)` 而不是重写文本（2026-10-25，两条都被实测咬过）：
      · 第一版用 `Path.write_text`（文本模式）：Windows 上把 `\\n` 翻成 `\\r\\n`，
        于是 LF 文件被写成 CRLF —— "还原"之后行尾变了；
      · 改成 `write_text(..., newline="")` 只修了**写**侧：`read_text` 在读的时候
        （universal newlines）已经把 CRLF 归一成 LF 了，于是反过来把 CRLF 文件写成 LF。
    两头都治的办法只有一个：**读原始字节、还原时把原始字节原样写回** —— 逐字节相等
    就不再是"尽量做到"，而是构造上成立。这条缺口它自己的用例一直没抓到，因为
    `read_text` 两次读回来是一样的（钉子见 `test_apply_and_run_restores_bytes_not_just_text`，
    里面两种行尾各验一条）。
    """
    path = ROOT / m["file"]
    raw = path.read_bytes()                       # ← 还原用的就是它，原样写回
    crlf = b"\r\n" in raw
    original = raw.decode("utf-8")
    old, new = _native(m["old"], crlf), _native(m["new"], crlf)
    if old not in original:
        return {**m, "status": "inconclusive", "note": "变异文本没匹配上（源码改过？更新变异表）",
                "dt": 0.0}
    st = path.stat()
    try:
        path.write_bytes(original.replace(old, new, 1).encode("utf-8"))
        if path.suffix == ".js":                       # 前端探针：先确认语法没被改坏
            ok, why = _js_syntax_ok(path)
            if not ok:
                return {**m, "status": "error", "note": why, "dt": 0.0}
        status, dt = run_tests(m["tests"], timeout)
        return {**m, "status": status, "note": "", "dt": dt}
    finally:
        path.write_bytes(raw)                      # 逐字节还原（构造上就相等）
        _restore_mtime(path, st)


def running_lock() -> Path:
    """「探针正在跑」的标记文件（放系统 TEMP，**不污染工作区**）。

    为什么需要它（2026-10-25 实测，我自己撞的）：探针在后台跑的时候，我顺手跑了
    `pytest tests/test_mutation_probe.py` —— 那一刻源码正被变异着，于是三条契约用例
    报出「表里的 old 不在文件里」这种**与真故障无关**的 ERROR。这正是本项目最忌的形状：
    噪音会训练人对红脱敏。处置不是"记得别同时跑"，而是让**同时跑这件事自己有名字**。

    路径按仓库路径派生（同一台机器上可能有好几个检出，各有各的锁），
    放 TEMP 是因为它没有"该不该提交"的歧义。
    """
    key = hashlib.sha1(str(ROOT).encode("utf-8")).hexdigest()[:10]
    return Path(tempfile.gettempdir()) / f"kanban_probe_{key}.lock"


def _pid_alive(pid: int) -> bool:
    """这个 PID 还活着吗。

    ⚠️ **Windows 上不许用 `os.kill(pid, 0)`** —— 这是检修时被端到端验证揪出来的：
    CPython 在 Windows 上把 `os.kill` 实现成 `TerminateProcess`，`sig=0` 不是「探测」
    而是「以退出码 0 结束那个进程」；权限不足时它抛 OSError。于是这个"存活检测"
    要么把对方杀掉、要么报错 —— 两种都不是我们要的。**实测后果**：另一份探针持锁时，
    `_pid_alive` 返回了 False（把活进程当成了陈锁），于是第二份探针**照常开跑**，
    护栏形同虚设（退出码 0，而不是拒绝的 3）。
    而当时那两条单测都 monkeypatch 了本函数，所以从没验过真实现 ——
    正是本项目「拒绝 mock 自证」警告的那件事。

    Windows 改用 `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject(h, 0)`：
    拿到句柄且**没**被信号（= 还没退出）就是活着。判别失败原因用 `GetLastError`：
    `ERROR_INVALID_PARAMETER` = 这个 PID 不存在；`ERROR_ACCESS_DENIED` = 存在但没权限
    —— 后者按**活着**处理（宁可少跑一份探针，也不能两份同时改源码）。
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        ERROR_INVALID_PARAMETER = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            err = ctypes.get_last_error()
            return err != ERROR_INVALID_PARAMETER      # 没权限也算活着（见 docstring）
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)                     # POSIX 上这才是正确的存活探测
        return True
    except PermissionError:                 # 活着但不属于我们 —— 仍算活着
        return True
    except OSError:
        return False


def _probe_is_running() -> str:
    """有**另一份**探针正在跑时返回一句人话（否则空串）。陈锁顺手清掉。"""
    p = running_lock()
    if not p.exists():
        return ""
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001
        return f"锁文件读不出内容：{p}（删掉它再跑）"
    pid = info.get("pid")
    if isinstance(pid, int) and _pid_alive(pid) and pid != os.getpid():
        return (f"另一份探针正在跑（PID {pid}，起于 {info.get('started', '?')}）："
                f"它会边跑边改源码，此时跑别的门禁会得到与真故障无关的红。等它结束（或先停掉它）")
    p.unlink(missing_ok=True)               # 陈锁：那个进程已经不在了
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="变异探针：找测试看不出来的语义盲区")
    ap.add_argument("--only", default="", help="只跑这些 id（逗号分隔）")
    ap.add_argument("--timeout", type=int, default=600, help="每个变异的测试超时秒数")
    ap.add_argument("--json", default="", help="结果写到 JSON 文件")
    ap.add_argument("--preflight", action="store_true",
                    help="只做启动预检（old 能否命中），不跑变异")
    args = ap.parse_args()

    busy = _probe_is_running()
    if busy:
        print(f"不跑：{busy}")
        return 3

    want = {s.strip() for s in args.only.split(",") if s.strip()}
    todo = [m for m in MUTATIONS if not want or m["id"] in want]

    problems = preflight(todo)
    if problems:
        print("预检失败 —— 以下变异的 old 命不中，先处理再跑（否则注入/还原都不完整）：")
        for m, why in problems:
            print(f"  ✗ {m['id']}  {m['file']}\n      {why}")
        print("探针被外部信号打断不会走到 finally，会留残渣；处理完重跑。")
        return 1
    print(f"预检：{len(todo)}/{len(MUTATIONS)} 条变异的 old 都能命中\n")
    if args.preflight:
        return 0

    print(f"变异探针：{len(todo)}/{len(MUTATIONS)} 条（判据：改了还全绿 = 盲区）\n")
    # 从这一刻起源码会被反复变异 —— 挂上标记，让「同时跑别的门禁」这件事有名字
    lock = running_lock()
    lock.write_text(json.dumps({"pid": os.getpid(), "todo": len(todo),
                                "started": time.strftime("%Y-%m-%d %H:%M:%S")},
                               ensure_ascii=False), encoding="utf-8")
    results = []
    try:
        for m in todo:
            res = apply_and_run(m, args.timeout)
            results.append(res)
            mark = {"killed": "被杀 ✓", "survived": "存活 ✗（盲区）",
                    "inconclusive": "未生效 ?", "error": "探针异常 !"}[res["status"]]
            print(f"  {mark:16s} {res['id']:32s} {res['dt']:6.1f}s  {res['note']}", flush=True)
    finally:
        # 外部信号杀掉时走不到这里 —— 所以 _probe_is_running() 还得认"进程已不在"的陈锁
        lock.unlink(missing_ok=True)

    bad = [r for r in results if r["status"] != "killed"]
    print("\n" + "=" * 78)
    print(f"变异 {len(results)} 条：被杀 {len(results) - len(bad)}    存活/异常 {len(bad)}")
    for r in bad:
        print(f"  - {r['id']}：{r['why']}")

    if args.json:
        (ROOT / args.json).write_text(
            json.dumps([{k: v for k, v in r.items() if k != "old" and k != "new"}
                        for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.json}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())