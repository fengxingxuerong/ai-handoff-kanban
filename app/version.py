# -*- coding: utf-8 -*-
"""应用版本与构建时间。

单一事实来源：桌面壳启动日志、`/api/health`（界面上的版本条也从这里读）。

`BUILD_TIME` 的口径分两种情况，**都不再手填**：

- 源码运行：取 `app/` + `web/` + `scripts/desktop.py` 里最新的文件修改时间 —— 它就是
  "这份代码最后一次改是什么时候"，和真实情况永远一致。
- 打包运行（frozen）：源码被 PyInstaller 解包到临时目录，那里所有文件的 mtime 都是解包时间，
  取不到源码时间。所以由打包流程事先写入 `app/_build_stamp.py`（见 `scripts/make_stamp.py`，
  `build.bat` 与 CI 都会调），这里只负责读。

历史上这里是手填常量（`"2026-09-15 12:00"`），于是启动日志报的时间和 exe 真实构建时间差了一整天 —— 
"靠它判断 exe 是否落后于源码"也就完全落空。手填的时间戳一定会漂，所以改成算出来的。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# 语义化版本：主版本.次版本.修订
# 1.2.0（2026-10-06）：竞品吸取轮 —— 任务验收标准 / journal 折叠摘要 / 记忆时效 /
# 待拍板 Inbox / 第 9 个入口投影（.claude/skills）；含 10-01 未发版的
# 自动分配 / 里程碑 / 项目统计 / 保存视图。
# 1.2.1（2026-10-07）：修复 /api/inbox 漏掉「依赖没满足的不算」过滤（硬性约定 27）；
# 内部重构：journal/acceptance 清洗与上限拆出 app/journal.py，无行为变化。
# 1.3.0（2026-10-14）：第四轮对标轮（一次功能轮，故按次版本号）——
# 看板侧停滞检测（跨 7 天无动的进行中任务进 stats 与 AI 简报）、全项目交接包 pre-fetch
# git 快照（export-all 冷态 5.8s → <1s）、记忆按类型加权召回（对标 claude-mem 的 Awareness）、
# 条目级尺寸估算（记忆检索输出报每条成本）；另含两个修复：
# PATCH 非法枚举统一 422、中文密钥脱敏补规则。
# 1.4.0（2026-10-15）：桌面端视觉精修轮（一次面向用户的界面改版，故按次版本号）——
# style.css 重写为 token 化设计系统（深浅两套主题同源，层次/质感/动效统一）；
# 布局改 flex 自适应（提醒横幅出现不再顶出第二条滚动条、详情视图底部不再被裁）；
# 顶栏窄窗口改为可换行（此前按钮被撑出视口且无法滚动到 = 点不到）。
# 顺手修掉两个「JS 在用、CSS 未定义」的 token：--purple（任务统计堆叠条
# 「待审查」那一段 fill 无效、实际渲染成黑色）与 --line（恢复来源列表分隔线整条失效）。
# 前端契约零改动：类名 / data-act / a11y 契约 / 弹窗数 / --banner-h 高度 calc 全部保持。
# 1.7.1（2026-10-18）：总览雷达标记 —— 项目卡显示「代码变化」，列表标题显示变化项目数；30s 轮询。
# 1.7.0（2026-10-18）：仓库变化雷达 —— 按 repo_path 扫描 git，检测未提交/新提交，按任务完成度给建议进度；用户确认后才写 progress，并追加 repo_audit.jsonl。
# 1.6.2（2026-10-18）：详情页次级操作收进「更多」菜单；主操作只保留新建任务，删除入口加确认门测试。
# 1.6.1（2026-10-18）：宽屏看板空列压缩 + 空态提示；空列不再等宽占用主区域。
# 1.6.0（2026-10-18）：前端 UI 精修 —— 顶栏分组、单一强调色、项目列表层级、详情页主操作与交接工具网格、卡片基线与对比度收敛。
# 1.5.2（2026-10-18）：MCP 接手包收口 —— kanban_context 补待审查任务、refs 与验收标准；
# HTTP /api/context 的 start_here 同步带 acceptance；新增 1 条 MCP 回归。
# 1.5.1（2026-10-18）：交接文档收口 —— AGENTS.md 补 Definition of Done、refs/验收/依赖/截止；
# 修复新智能体接管时「验收标准：材料未提供」的缺口；46 条交接相关测试通过。
# 1.5.0（2026-10-17）：交付收口轮 —— 项目卡标题两行布局；KANBAN_TOKEN 注入桌面 UI；
# memories/config 增加 last-good + 损坏留证；批量删除改服务端预快照 + 全有或全无；
# 表单标签/弹窗标题/对比度可访问性修复。
# 1.7.2（2026-10-18）：可用性收口轮。①详情页「更多」菜单补齐收起路径 —— 它是个原生
# <details>，此前只有"再点一次标题"能收；点别处 / 按 Esc / 选完菜单项都不关
# （实测最别扭的一幕：点「删除」走完确认门、项目都没了，那个菜单还开着）。
# 键盘用户按 Esc 收起时焦点还回触发它的标题。
# ②零依赖的可访问名门禁：总览 + 详情里每个可见控件的可访问名不能为空，它当场抓出
# 搜索框只靠 placeholder 表达含义（placeholder 不算可访问名，且一输入就消失），已补 aria-label。
# 1.7.3（2026-10-18）：仓库变化雷达收口轮（外部评审发现，修复为主）。
# ① repo_audit.jsonl 补上滚动裁剪（MAX_AUDIT_LINES=2000 / KEEP_AUDIT_ON_TRIM=1000，
#    跨进程锁与 usage.jsonl 同套）—— 它此前是第三条无上限的追加型存储，违反硬性约定 11；
#    备注字段同步补 500 字上限（一次请求不能再把一行撑成任意长）。
# ② 删项目（单个/批量）连 repo_watch.json 的孤儿基线一起清（记忆层同款纪律）。
# ③ 总览 30s 巡检改吃 60s 快照缓存（点名看单个项目仍绕过缓存要现值）：
#    实测 8 个真仓库冷扫 1.58s / 40 次 git 子进程，缓存命中 2ms / 0 次；
#    前端两处轮询补 document.hidden 守卫（窗口不可见时不跑 git）。
# ④ 补 7 条雷达用例与 6 条变异探针（55→61），此前"首扫脏仓库""建议把进度往回拽"
#    等行为规则既无测试也无探针。
# ⑤ 备份带上 repo_watch.json、恢复随之回滚（老备份没有/坏掉 = 不动现状，与 memories
#    同语义；导出/导入有意不带 —— 跨机后 repo_path 指不到同一仓库，与 _client_refs 同理）。
#    同轮清掉 main.py 两处死代码（_ABBR / APP_NAME）。恢复链路 3 条用例 + 3 条探针（→64）。
# 1.7.4（2026-10-18）：桌面端 UI 收口轮（外部审计发现，五处缺陷/残留逐条修复）。
# ① --mono 从未定义：.repo-watch .rw-head 的 font-family 整条失效、.repo-flag 的
#    font: 9px var(--mono) 简写整条作废（字体停在继承值）——「代码变化」标记一直没按
#    设计渲染，还把卡片中段的行高悄悄撑高了约 4px；现已定义 --mono。
# ② 弹窗滚动架构：遮罩滚动时 sticky 页脚被钉在遮罩内容盒底、正文从按钮条下透出。
#    .modal 改 flex 列 + max-height: calc(100vh - 100px) 定框，.mbody 成为滚动容器
#    （mhead/mfoot 的 sticky 声明保留但惰性），JS modalScroll 保存/恢复改指 .mbody。
# ③ 浅色主题 --accent2 未随「单一强调色」归并（1.6.0 只改了深色）：进度条/统计数字/
#    下一步底色残留紫调；现别名归并到 --accent / --accent-soft。
# ④ 浅色顶栏「新建项目」白字白底（对比度约 1.1:1，等同看不见）：补 color: var(--accent)。
# ⑤ 交接说明 h3 折行出孤字（nowrap 对 CJK 无效）：flex-wrap: wrap + row-gap + 右侧
#    工具行 margin-left: auto。
# 新增 1 条静态钉子（.mbody 是滚动容器）与 1 条 E2E 重写（弹窗几何：页脚贴框底、
# 整框不出视口）；README 总览深浅两张截图随之重拍。
# 1.8.0（2026-10-19）：第六轮竞品吸取轮（五个提交批次，功能轮）。① 阻塞原因
# blocked_reason：只在状态=阻塞时保留、离开自动清（journal 留痕「原阻塞原因」）；
# 上卡片/待拍板/接手包/交接包四处出口，MCP 未记原因会提醒。② done_at + stale_done：
# 任务完成时刻入盘，「验收后仓库又有新提交」在两处出口（git 端点与雷达）小声提示，
# 只提示绝不自动改状态。③ refs 检查硬化：跳过 :// URL、剥 #片段。④ 记忆去重命中
# 返回**盘上已有那条**（旧实现给假 id，拿去删 404）。⑤ 任务号一键复制（界面）。
# 探针 64→68；新增 3 条 MCP 契约、2 条 API、1 条 git、2 条雷达、2 条 E2E。
# 1.8.1（2026-10-19）：评测轮修复（真实项目端到端测试发现的两处）。① 「现在可以做」
# 不再撒谎：/next、接手包 start_here 条目带 status（阻塞时带 blocked_reason），
# MCP next_task 与接手包文本行带 ⛔ 与原因 —— 实盘代理把无标记的阻塞任务当普通待办
# 直接开工；自动分配（allocatable）同步排除阻塞（推不动的石头不该派给谁）。
# ② LLM 网关把空回复（推理模型烧光 max_tokens、HTTP 200 但 content 空串）记为失败
# 并继续走降级链（含 kimi temp=1 重试分支）—— 以前当成功返回，其它可用端点永远
# 轮不到，症状是随机的 500「LLM 返回为空」与 AGENTS.md 静默退回本地版。
# 探针 68→72；SCHEMA_VERSION 2→3（start_here 条目形状变化）。
# 1.9.0（2026-10-21）：第七轮竞品吸取（功能轮）。接手包支持按需切片与增量：
# /api/context/{pid} 与 MCP kanban_context 新增 sections（可选小节白名单，
# project 始终返回）与 since（只列该时刻之后变化的 tasks/memories，含边界、缺时间戳
# fail-open）。start_here/readiness/code_reality 在增量里仍全量 —— 只给变化项会让代理
# 误以为"没变化 = 没活干"。顶层新增 slice 元数据（含省略小节、变化/未变计数与说明），
# SCHEMA_VERSION 3→4；探针 72→75（不认识的 sections 静默忽略、since 开区间、
# 增量裁掉 start_here）。
# 1.9.1（2026-10-22）：用户视角收口轮（修复为主，真实浏览器从零走查发现）。
# ① 详情就绪度随任务变更即时刷新：GET /api/projects/{pid} 直接带 readiness，
#    前端优先用它；此前只读全局 stats 旧快照，建完任务左侧还写着「已有任务清单 ✗」。
# ② 今日面板改显任务状态（此前误用项目状态），阻塞任务带原因；列表卡片补
#    tabindex/data-keyact，键盘可打开；MCP kanban_today 同步带上阻塞原因。
# ③ 任务弹窗把状态/优先级/截止/负责 AI 提到首屏（此前在 1440×900 下要滚动才看得到）。
# ④ 示例数据改为相对今天生成、进度与完成数自洽、不再指向不存在的本机仓库；
#    首次打开不再满屏「37 天没记录」与假告警。
# 探针 75→78。
# 1.9.2（2026-10-22）：详情页遮挡修复 + 用户体验轮（E2E 抓到的几何缺陷 + 窄屏/宽屏布局）。
# ① 详情头栏不再 sticky：原来 `position: sticky; z-index: 8` 让头栏浮在内容之上，
#    滚到「记忆表单」时头栏压住「存」按钮，浏览器报 intercepts pointer events ——
#    真人一样点不到。改为「常驻头栏（#view-detail 直接子元素）+ 独立滚动区」的
#    flex 两行结构：贴顶常驻，但不覆盖任何可点元素。
# ② 记忆表单在 328px 侧栏里不换行：类型/内容/有效期/存四个控件横向溢出 88px，
#    「存」按钮跑到隔壁列地盘上（elementFromPoint 实测命中隔壁列 div）。
#    改 flex-wrap: wrap + 内容框 min-width: 0。
# ③ 同轮收口上一轮未发的用户体验改动：搜索框清空按钮、空看板三步引导、可点 KPI。
# ④ 窄屏详情重排：980px 以下任务板与右栏提到侧栏面板之前（此前任务板要滚 2000px 以上）；
#    390px 修掉 508px 的横向溢出；宽屏滚动左侧面板时任务板停在视野里；触屏项目卡操作常显；
#    窄窗口顶栏隐藏副标题并压缩按钮行，少占一屏。
# ⑤ 空看板表头（「项目 / 按最近更新排序」）只在真有项目时显示；空态文案改成讲清价值与
#    就近的「新建第一个项目」按钮。
# ⑥ 搜索框按 Esc 退出本次搜索；零任务项目不再摊开五个空列，改成一个空态 + 新建入口；
#    筛选/搜索无结果时给「查看全部」按钮，一步清掉筛选与搜索词。
# 新增 4 条静态契约（头栏不在滚动区、表单换行、清空按钮接线、三步引导只给真空态）
# + 8 条浏览器用例（清空恢复全量、引导出现/收起、表单几何不溢出、窄屏顺序/无溢出、
#   宽屏吸顶/头栏常驻、KPI 键盘直达、空结果一键回全部、零任务单空态）
# + 5 条 filelock 直测（超时/解锁失败仍关句柄/自建锁文件/内容不动）。
# 探针 78→81（表单不换行、输入框不可压缩、头栏与滚动区合块）。
# 1.9.3（2026-10-23）：评测收口轮（外部评测逐项复核后修的四处 + 一个产品缺口）。
# ① **覆盖率门槛此前是装饰品**：pytest.ini 的 [coverage:report] fail_under 对退出码无效
#    （实测只跑 tests/test_version.py、TOTAL 16% 仍 exit 0、一句 fail 都不说），
#    而 ci_local 那一步的标签写着「含覆盖率门槛」。改为门禁从 pytest.ini 读 fail_under
#    并显式传 --cov-fail-under（数字只有一处）；钉子 test_coverage_floor_is_enforced_by_the_release_gate
#    两头都钉（必须传 + 不许塞 addopts，后者会让文档里有意只跑一部分的命令集体假红）。
#    --quick 跳过的四步在结论里逐条点名，不再印「可以发版」。
# ② **恢复前的安全副本失败不再静默**：回执带 pre_restore_backup（ok / skipped / failed）
#    与 path，失败时带说明；界面不许在没退路时打 ✓（确认框刚承诺过有退路）。
# ③ **入口文件写进用户仓库前报凭据数**（count_sensitive 与 redact_text 同口径）并给
#    「写前脱敏」开关；预览 / 写盘 / toast 三处是同一份文本。仓库通常会被 git push，
#    这是整个应用里唯一一条能把看板内容带出本机的路（约定 9 的盲区）。
# ④ **第二个产物 AI交接看板-MCP.exe**（控制台子系统，故意不加 -w）：只装 exe 的用户
#    没有 .venv，"MCP 让代理直接读写看板"这个最大卖点此前对他不可用。配套
#    scripts/mcp_frozen_smoke.py 九条断言（协议 / stdout 洁净 / 中文双向不乱码 / 真落盘）。
# 文档与注释修正：main.py:project_context 的过期注释（简报的任务标题其实也盖）、
#    AGENTS.md 指向不存在的 README 小节的失效引用、ci_local 顶部「仓库没有远端所以 CI
#    从未执行过一次」的过期自述。
# 探针 81→84（恢复退路静默、凭据不报数、脱敏开关被无视）。
__version__ = "1.9.3"

ROOT = Path(__file__).resolve().parent.parent

# 打包时由 scripts/make_stamp.py 生成；源码运行时这个模块不存在，属正常情况
try:
    from app._build_stamp import STAMP as _FROZEN_STAMP
except Exception:                                              # noqa: BLE001
    _FROZEN_STAMP = ""

_SOURCE_WATCH = ("app", "web", "scripts/desktop.py")
_SUFFIXES = {".py", ".html", ".js", ".css", ".bat"}
# 生成物不算"源码最后改动"：构建戳是打包时写出来的，
# 让它参与比较会把源码时间顶成构建时间，进而掩盖"exe 落后于源码"的信号
_GENERATED = {"_build_stamp.py"}


def _watched_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for rel in _SOURCE_WATCH:
        p = root / rel
        if p.is_file():
            if p.name not in _GENERATED:
                files.append(p)
        elif p.is_dir():
            files.extend(f for f in p.rglob("*")
                         if f.is_file() and f.suffix in _SUFFIXES and f.name not in _GENERATED)
    return files


def newest_source_mtime(root: Path = ROOT) -> float:
    """源码最后一次变更的时间戳（秒）；取不到任何文件时返回 0"""
    times = [f.stat().st_mtime for f in _watched_files(root)]
    return max(times) if times else 0.0


def source_build_time(root: Path = ROOT) -> str:
    ts = newest_source_mtime(root)
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "未知"


def resolve_build_time(*, frozen: bool, stamp: str, root: Path = ROOT) -> str:
    """打包版用构建期写死的戳，源码版实时算。

    打包版没有戳（老版本 exe、或漏跑 make_stamp）时，如实返回"未知"而不是编一个时间出来。
    """
    if frozen:
        return stamp or "未知"
    return source_build_time(root)


BUILD_TIME = resolve_build_time(frozen=bool(getattr(sys, "frozen", False)), stamp=_FROZEN_STAMP)

APP_VERSION = __version__
APP_NAME_VERSION = f"AI 交接看板 v{__version__}"
