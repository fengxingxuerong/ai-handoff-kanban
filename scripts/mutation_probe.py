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
3. 探针只改本地工作区文件，**跑完一定还原**（内存备份 + finally，不依赖 git）。
   探针运行期间不要并行跑别的测试。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
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
        "file": "app/main.py",
        "old": '        host = raw[1:].split("]")[0] if raw.startswith("[") else raw.split(":")[0]',
        "new": '        host = ""',
        "why": "兜底分支把非 IP 方括号 Host 解析成空串放行：[abc] 等异常 Host 绕过白名单",
        "tests": ["tests/test_security.py"],
    },
    {
        "id": "guard_token_methods",
        "file": "app/main.py",
        "old": '    if token and request.method in ("POST", "PUT", "DELETE", "PATCH"):',
        "new": '    if token and request.method in ("POST", "PUT", "DELETE", "PATCH", "GET"):',
        "why": "开了 Token 后读接口也要鉴权：本该『只读不受影响』的契约被破坏",
        "tests": ["tests/test_security.py"],
    },
    {
        "id": "guard_origin_reversed",
        "file": "app/main.py",
        "old": '        if (urlparse(origin).hostname or "").lower() not in _ALLOWED_HOSTS:',
        "new": '        if (urlparse(origin).hostname or "").lower() in _ALLOWED_HOSTS:',
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
        "old": "    return [t for t in executable_tasks(tasks) if t.get(\"priority\") == priority]",
        "new": "    return executable_tasks(tasks)",
        "why": "自动分配不按优先级筛选：把『中/低』任务也派出去（接口契约变宽）",
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
        "file": "app/main.py",
        "old": '    return f"http://127.0.0.1:{SERVICE_PORT}{path}"',
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
        "file": "app/main.py",
        "old": '        roots.append(Path(home) / "AppData")',
        "new": "        pass",
        "why": "系统目录黑名单去掉 AppData：除临时目录外的 AppData 都可写（诱导落盘通路）",
        "tests": ["tests/test_security.py", "tests/test_api.py", "tests/test_agent_files.py"],
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
        "file": "app/main.py",
        "old": '        if not name or name in [m["name"] for m in out]:',
        "new": "        if not name:",
        "why": "里程碑同名去重失效：两条同名里程碑并存，归组任务时按 name 匹配会分到第一条（语义漂移）",
        "tests": ["tests/test_milestone.py"],
    },
    {
        "id": "milestone_cap_removed",
        "file": "app/main.py",
        "old": "        out.append({\"name\": name, \"done\": bool(item.get(\"done\"))})\n        if len(out) >= MAX_MILESTONES:",
        "new": "        out.append({\"name\": name, \"done\": bool(item.get(\"done\"))})\n        if len(out) >= MAX_MILESTONES + 999:",
        "why": "里程碑上限失效：海量里程碑写入核心数据文件并被整份解析，追加型存储失控",
        "tests": ["tests/test_milestone.py"],
    },
    {
        "id": "milestone_task_name_not_trimmed",
        "file": "app/main.py",
        "old": '        v = (v or "").strip()\n        return v[:MAX_MILESTONE_NAME] if v else ""',
        "new": '        v = (v or "")\n        return v[:MAX_MILESTONE_NAME] if v else ""',
        "why": "任务 milestone 归属不去首尾空白：『 甲 』与『甲』归不进同一组，进度条错",
        "tests": ["tests/test_milestone.py"],
    },
]


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


def apply_and_run(m: dict, timeout: int) -> dict:
    path = ROOT / m["file"]
    original = path.read_text(encoding="utf-8")
    if m["old"] not in original:
        return {**m, "status": "inconclusive", "note": "变异文本没匹配上（源码改过？更新变异表）",
                "dt": 0.0}
    try:
        path.write_text(original.replace(m["old"], m["new"], 1), encoding="utf-8")
        status, dt = run_tests(m["tests"], timeout)
        return {**m, "status": status, "note": "", "dt": dt}
    finally:
        path.write_text(original, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="变异探针：找测试看不出来的语义盲区")
    ap.add_argument("--only", default="", help="只跑这些 id（逗号分隔）")
    ap.add_argument("--timeout", type=int, default=600, help="每个变异的测试超时秒数")
    ap.add_argument("--json", default="", help="结果写到 JSON 文件")
    args = ap.parse_args()

    want = {s.strip() for s in args.only.split(",") if s.strip()}
    todo = [m for m in MUTATIONS if not want or m["id"] in want]

    print(f"变异探针：{len(todo)}/{len(MUTATIONS)} 条（判据：改了还全绿 = 盲区）\n")
    results = []
    for m in todo:
        res = apply_and_run(m, args.timeout)
        results.append(res)
        mark = {"killed": "被杀 ✓", "survived": "存活 ✗（盲区）",
                "inconclusive": "未生效 ?", "error": "探针异常 !"}[res["status"]]
        print(f"  {mark:16s} {res['id']:32s} {res['dt']:6.1f}s  {res['note']}", flush=True)

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
