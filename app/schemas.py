# -*- coding: utf-8 -*-
"""HTTP 请求模型（Pydantic）：17 个入口的入参形状与字段级清洗。

2026-10-24 从 `app/main.py` 整体搬出（**实现逐字未改**，只是把散在各端点之间的声明收成一处）。
搬它们的理由：这些是**纯声明 + 纯清洗**（不碰文件、不读全局状态），原先散在 3500 行的
路由文件里，只能靠端到端间接验证；独立成模块后可以单独过一遍边界。

依赖方向：本模块只依赖**叶子模块**（`fields` / `journal` / `taskgraph`），
**不 import main** —— 反过来 main 会 import 它。字段白名单与上限现在住在 `app/fields.py`
（原先在 main，模型引用它们会成环）。

`app/main.py` 用 `from .schemas import ...` 保住既有名字：
测试与调用点的 `kanban.ProjectIn` / `main.TaskIn` 照旧可用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .fields import (
    canonical_priority,
    canonical_status,  # 字段白名单与上限：**唯一一处**，前端另有对账钉子
    DEFAULT_STATUS,
    MAX_BLOCKED_REASON,
    MAX_MILESTONE_NAME,
    MAX_REPO_ACK_NOTE,
    MAX_VIEW_NAME,
    MAX_VIEW_QUERY,
    _clean_milestones,
)
from .journal import MAX_JOURNAL_TEXT, clean_acceptance
from .taskgraph import clean_refs

class ProjectIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    status: Literal["规划中", "进行中", "已暂停", "已完成"] = "规划中"
    # 英文别名（todo / in_progress …）先归一，Literal 再判规范值 ——
    # mode="before" 跑在 Literal 校验**之前**，否则别名到不了 Literal
    _canon_status = field_validator("status", mode="before")(canonical_status)
    progress: int = Field(0, ge=0, le=100)
    priority: Literal["高", "中", "低"] = "中"
    _canon_priority = field_validator("priority", mode="before")(canonical_priority)
    tags: list[str] = []
    repo_path: str = ""
    background: str = ""
    handoff_note: str = ""
    next_action: str = ""
    # 类型放宽为 list | None：清洗全部交给 _clean_milestones（非 dict 元素按规则丢弃、
    # 不 422 —— 方案口径是「类型不对的整条丢弃」）。若钉死 list[dict]，
    # pydantic 会在 validator 之前就把非 dict 元素拒掉，容错永远走不到。
    # None 视为 []：读改写客户端可能缓存了旧版 GET 返回的 null，别让它 422（2026-10-24）。
    milestones: list | None = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        out = []
        for t in v or []:
            t = (t or "").strip()[:24]
            if t and t not in out:
                out.append(t)
        return out[:12]

    @field_validator("milestones")
    @classmethod
    def _clean_milestones_val(cls, v):
        return _clean_milestones(v or [])
class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    desc: str = ""
    status: Literal["待办", "进行中", "待审查", "已完成", "阻塞"] = "待办"
    # 英文别名（todo / in_progress …）先归一，Literal 再判规范值 ——
    # mode="before" 跑在 Literal 校验**之前**，否则别名到不了 Literal
    _canon_status = field_validator("status", mode="before")(canonical_status)
    priority: Literal["高", "中", "低"] = "中"
    _canon_priority = field_validator("priority", mode="before")(canonical_priority)
    agent: str = ""
    due_date: str = ""            # 截止日期 YYYY-MM-DD，空=未设置
    refs: list[str] = []          # 相关代码位置，如 app/main.py:120；交接时用来说明"改哪里"
    depends_on: list[str] = []    # 前置任务 id（同一项目内），用于排"先做哪个"
    milestone: str = ""           # 归入的项目里程碑名（空=未归入）；普通字符串校验，非 Literal
    acceptance: list[str] = []    # 验收标准（对标 Backlog.md 的 DoD）：逐条"怎么算完成"，
                                  # 交接包与接手页带着走，待审查→已完成时照着验
    blocked_reason: str = ""      # 为什么被卡住：**只在状态=阻塞时保留**，离开阻塞自动清空
                                  # （历史留在 journal 的状态流转条目里）。见 _apply_task。
    @field_validator("blocked_reason", mode="before")
    @classmethod
    def _clean_blocked_reason(cls, v):
        v = (v or "").strip()
        return v[:MAX_BLOCKED_REASON] if v else ""
    @field_validator("due_date", mode="before")
    @classmethod
    def _clean_due(cls, v):
        v = (v or "").strip()
        if not v:
            return ""
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return ""
        return v

    @field_validator("milestone", mode="before")
    @classmethod
    def _clean_milestone(cls, v):
        v = (v or "").strip()
        return v[:MAX_MILESTONE_NAME] if v else ""

    @field_validator("refs", mode="before")
    @classmethod
    def _clean_refs(cls, v):
        return clean_refs(v)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _clean_deps(cls, v):
        if isinstance(v, str):
            v = [p for p in v.replace("\n", ",").split(",")]
        if not isinstance(v, (list, tuple)):
            return []
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("acceptance", mode="before")
    @classmethod
    def _clean_acceptance(cls, v):
        return clean_acceptance(v)
class AssignIn(BaseModel):
    """任务自动分配请求：把项目内『指定优先级、未完成、依赖已就绪』的任务批量指派给某智能体。

    `overwrite=True` 时连已有负责人的任务一起覆盖（默认跳过，保护手工指派）。
    """
    agent: str = Field(..., min_length=1, max_length=60, description="要指派给的智能体名，如 cline / 豆包")
    priority: Literal["高", "中", "低"] = "高"
    overwrite: bool = False
class ViewIn(BaseModel):
    """持久化自定义视图（Plane Views 轻量版）：`filter` 放行项目状态白名单，`q` 是搜索词。

    保存语义是「upsert-by-name」：同名再存 = 更新该视图并返回原 id（带 `updated: true`），
    不产生重复条目 —— 菜单里"把当前筛选存成同名视图"是常见动作，报 409 只会让人困惑。
    """
    name: str = Field(..., min_length=1, max_length=MAX_VIEW_NAME, description="视图名，同名即覆盖更新")
    filter: str = Field("全部", description="项目状态筛选：全部/规划中/进行中/已暂停/已完成")
    q: str = Field("", max_length=MAX_VIEW_QUERY, description="保存的搜索词，应用视图时回填搜索框")

    @field_validator("filter")
    @classmethod
    def _clean_view_filter(cls, v):
        v = (v or "").strip()
        return v if v in ("全部", *DEFAULT_STATUS) else "全部"
class BulkDeleteIn(BaseModel):
    """批量删除：一次请求完成预快照 + 全有或全无，避免逐条 DELETE 的半删状态。"""
    ids: list[str] = Field(..., min_length=1, max_length=100)
    confirm: bool = False
class RepoAckIn(BaseModel):
    """确认仓库已读；progress 为空时只清变化标记，不改百分比。"""
    progress: int | None = Field(None, ge=0, le=100)
    note: str = Field("", max_length=MAX_REPO_ACK_NOTE)
class JournalIn(BaseModel):
    # 上限与 `MAX_JOURNAL_TEXT` 同一个数（以前声明收 2000、落盘偷偷截到 500 ——
    # 智能体写 1200 字的记录会无声掉一半，而回执还写着"已记录"）。
    # 对账钉子：test_journal_limit_is_one_number_everywhere
    text: str = Field(..., min_length=1, max_length=MAX_JOURNAL_TEXT)
class SaveExportBody(BaseModel):
    filename: str = "交接包.md"
    content: str = ""
    path: str | None = None  # 可选：显式保存路径（用户输入）
    overwrite: bool = False  # 显式路径时是否允许覆盖已有文件（默认不覆盖）
class AgentFilesBody(BaseModel):
    dir: str = ""                       # 目标仓库根目录；留空表示只预览不写盘
    targets: list[str] | None = None
    content: str | None = None          # 前端已预览过的内容，传回来可省一次模型调用
    overwrite: bool = False             # 默认不覆盖仓库里已有的入口文件（可能是用户手写的）
    check_only: bool = False            # 只体检：逐目标比对本应写的内容与磁盘现状，不写盘
    redact: bool = False                # 写盘前先脱敏（默认关：这是往用户自己的仓库写自己的文档）
class RepoSnapshotBody(BaseModel):
    dir: str = ""                       # 目标仓库根；留空 = 用项目自己的 repo_path
    redact: bool = True                 # **默认脱敏**（与 agent-files 默认关不同，理由见端点注释）
    overwrite_foreign: bool = False     # 同名但**不是我们生成的**文件：默认一动不动
    commit: bool = False                # 显式要求才提交，而且只提交本次写的那些路径
    dry_run: bool = False               # 只报计划不写盘


class ImportBody(BaseModel):
    content: str = Field(..., min_length=2, max_length=10_000_000)
class MemoryIn(BaseModel):
    pid: str = ""
    type: str = "事实"
    text: str = Field(..., min_length=1, max_length=2000)
    source: str = "手动"
    expires: str = ""   # 时效（YYYY-MM-DD，空=长期有效）；过期后不进交接包/AGENTS.md

    @field_validator("expires", mode="before")
    @classmethod
    def _clean_expires(cls, v):
        v = (v or "").strip()
        if not v:
            return ""
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return ""
        return v
class RestoreBody(BaseModel):
    source: str = "bak"
class SummarizeBody(BaseModel):
    pid: str = ""
    text: str = Field(..., min_length=10, max_length=20000)
class AIConfigIn(BaseModel):
    endpoints: list[dict] | None = None
    active_model: str | None = None
    schedule: dict | None = None
    mcp_readonly: bool | None = None
    session_sources: list[dict] | None = None
    budget_limit: float | None = None
class SuggestionUpdate(BaseModel):
    project_id: str
    field: str = "next_action"
    value: str = ""
class HandoffBody(BaseModel):
    mode: str = "generate"  # generate=新写 / polish=润色现有
class CommandBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
