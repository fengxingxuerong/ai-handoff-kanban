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
__version__ = "1.7.3"

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
