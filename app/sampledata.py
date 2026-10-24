# -*- coding: utf-8 -*-
"""首次运行展示的示例数据（纯函数，2026-10-24 从 `app/main.py` 拆出，实现逐字未改）。

搬它的理由：108 行的字面量数据块夹在路由文件里，读代码时既挡视线又容易改错；
它本身只依赖 `datetime`，是最干净的一类搬迁对象。

⚠️ 两条纪律跟着搬：① 示例数据**必须保持脱敏**（不含真实项目名与本机路径，它会随 exe 分发）；
② 时间戳**按相对今天生成**，写死日期会让新用户两周后一进来就看到「37 天没记录」。
探针 `sample_dates_hardcoded_again` 的目标文件已同步到这里。
"""
from __future__ import annotations

from datetime import datetime, timedelta

def sample_data() -> dict:
    """首次运行展示的示例数据。

    时间戳**按相对今天生成**：写死日期的版本用两周后就会让新用户一进来看到
    「37 天没记录」「更新于 9 月」和成排的停滞告警，像自己的看板坏了。
    另外进度与任务完成数要自洽 —— `realityVerdicts` 会核对「进度 65% 却 0 条完成」
    这类矛盾，示例不能自带一条假告警。示例里的代码路径留空：指向不存在的本机目录
    只会让「代码现实」面板第一次打开就报读不到。
    """
    now = datetime.now()

    def at(days_ago: int = 0) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")

    def due(days: int) -> str:
        return (now + timedelta(days=days)).strftime("%Y-%m-%d")

    return {
        "projects": [
            {
                "id": "sample-a",
                "name": "示例项目 A · 后台系统",
                "status": "进行中",
                "progress": 65,
                "priority": "高",
                "tags": ["全栈", "monorepo"],
                "repo_path": "",
                "background": "全栈后台管理系统，monorepo 架构，已覆盖核心业务域决策。",
                "handoff_note": "接手前先看 docs 目录的架构说明；已有决策改动前先读对应文档；前端有部分页面是假实现，注意核对。",
                "next_action": "补完剩余接口联调，跑一遍全量测试基线",
                "tasks": [
                    {"id": "t1", "title": "接口联调", "desc": "前后端联调剩余接口，确认数据流",
                     "status": "进行中", "priority": "高", "agent": "",
                     "created_at": at(20), "updated_at": at(1), "due_date": due(7)},
                    {"id": "t2", "title": "全量测试基线回归", "desc": "跑通全部测试并修复失败用例",
                     "status": "待办", "priority": "高", "agent": "",
                     "created_at": at(15), "updated_at": at(2), "due_date": due(5),
                     "acceptance": ["全部用例通过", "失败项写进交接说明"]},
                    {"id": "t3", "title": "前端假实现核对", "desc": "排查哪些页面是 setTimeout 假实现，补齐真实接口",
                     "status": "待办", "priority": "中", "agent": "",
                     "created_at": at(10), "updated_at": at(3), "depends_on": ["t6"]},
                    {"id": "t6", "title": "基础框架搭建", "desc": "脚手架、路由与构建流程",
                     "status": "已完成", "priority": "高", "agent": "",
                     "created_at": at(25), "updated_at": at(6), "done_at": at(6)},
                ],
                "created_at": at(25),
                "updated_at": at(1),
            },
            {
                "id": "sample-b",
                "name": "示例项目 B · 内容流水线",
                "status": "进行中",
                "progress": 75,
                "priority": "中",
                "tags": ["AI 写作", "多模型流水线"],
                "repo_path": "",
                "background": "内容生产项目，多模型流水线持续演进。",
                "handoff_note": "长跑任务需与会话解耦启动；进程排查时注意别误杀流水线后台进程；重启长跑前先做编译+符号完整性冒烟。",
                "next_action": "继续验证流水线健壮性，处理下一轮生成结果质检",
                "tasks": [
                    {"id": "t4", "title": "流水线健壮性验证", "desc": "断点续传、守护进程、健康池冷却等机制验证",
                     "status": "进行中", "priority": "中", "agent": "",
                     "created_at": at(18), "updated_at": at(2), "due_date": due(10)},
                    {"id": "t5", "title": "生成结果质检", "desc": "对最新一轮生成内容做通读级终审",
                     "status": "待办", "priority": "中", "agent": "",
                     "created_at": at(12), "updated_at": at(2)},
                    {"id": "t7", "title": "多模型路由接入", "desc": "按任务类型选择模型并记录用量",
                     "status": "已完成", "priority": "中", "agent": "",
                     "created_at": at(22), "updated_at": at(8), "done_at": at(8)},
                    {"id": "t8", "title": "失败回退策略", "desc": "端点失败时切换备用 Key 与模型",
                     "status": "已完成", "priority": "高", "agent": "",
                     "created_at": at(24), "updated_at": at(9), "done_at": at(9)},
                ],
                "created_at": at(26),
                "updated_at": at(2),
            },
            {
                "id": "sample-c",
                "name": "示例项目 C · 文本润色工具",
                "status": "已暂停",
                "progress": 50,
                "priority": "中",
                "tags": ["文本处理"],
                "repo_path": "",
                "background": "中文文本润色工具，当前已暂停。",
                "handoff_note": "暂停中。恢复时先看项目文档确认当前实现进度与下一步计划。",
                "next_action": "暂停中，暂无下一步",
                "tasks": [],
                "created_at": at(35),
                "updated_at": at(20),
            },
            {
                "id": "sample-d",
                "name": "示例项目 D · 扫描器",
                "status": "已完成",
                "progress": 100,
                "priority": "低",
                "tags": ["安全", "对标评测"],
                "repo_path": "",
                "background": "命令行扫描器，已完成实战评测。",
                "handoff_note": "已完结。如需继续迭代可参考实战评测结果。",
                "next_action": "已完结",
                "tasks": [],
                "created_at": at(60),
                "updated_at": at(40),
            },
        ]
    }
