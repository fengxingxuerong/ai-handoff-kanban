# -*- coding: utf-8 -*-
"""字段级白名单、上限与清洗（纯函数 / 纯常量，2026-10-24 从 `app/main.py` 拆出）。

为什么单独一个模块：这些常量原先住在 main 里，而 `app/schemas.py` 的请求模型要引用它们 ——
不搬就会成环（main → schemas → main）。它们本身是**叶子**（只有数字、字符串与一个纯函数），
放这里两边都取得到，也让"哪个字段上限多少"有了唯一一处。

⚠️ `DEFAULT_PRIORITY` 在这里是**白名单**（`["高","中","低"]`）；`app/assign.py` 里另有一个
同名的 `DEFAULT_PRIORITY = "高"`，那是"自动分配时的默认值"——两者不是一回事，别合并。
"""
from __future__ import annotations

# 合法值白名单：状态/优先级会被前端拼进 class 与文本，必须收口
DEFAULT_STATUS = ["规划中", "进行中", "已暂停", "已完成"]
DEFAULT_TASK_STATUS = ["待办", "进行中", "待审查", "已完成", "阻塞"]
DEFAULT_PRIORITY = ["高", "中", "低"]
# ---- 英文别名（2026-10-25）：契约要对**调用方**中立 ----
# 为什么加：MCP 的 inputSchema 与 HTTP 的 Literal 之前只收中文（`enum: ["待办", …]`），
# 而这工具的卖点是「**任何** agent 都能接手」—— 英文 agent 传 `status: "in_progress"`、
# `priority: "high"` 会直接 422 / failed，等于要求调用方懂中文。那是那句承诺里唯一货不对板的地方。
#
# 三条纪律：
#   ① 别名只出现在**入参**：归一到规范值以后才落盘，读出去的口径永远只有一套
#      （否则库里会同时出现「待办」与「todo」，召回、统计、界面全是坑）；
#   ② 别名表与规范值**同文件**（白名单与别名分家迟早漂），`with_aliases()` 是唯一把别名
#      广告给客户端的实现 —— 声明与执行都从这里取；
#   ③ **一张扁平表**：三组（项目状态/任务状态/优先级/记忆类型）放一起，靠"规范值本身不冲突"
#      成立（`规划中` 只在项目侧、`待办` 只在任务侧，`进行中`/`已完成` 两侧同名同义）。
#      于是**归一与语境无关、校验仍与语境有关**：给项目传 `todo` 会被归一成 `待办`，
#      随后被 ProjectIn 的 Literal 拒掉并报出它认识的四个值 —— 错误信息依然具体。
#
# 归一算法故意简单：trim + 小写 + 把 `-`/空格折成 `_`（`In Progress`、`in-progress`、
# `in_progress` 都命中）。**不认识的值原样返回** —— 让上层去拒，别在这一层把话说过头。
FIELD_ALIASES = {
    "规划中": ["planning", "plan", "planned"],
    "进行中": ["in_progress", "inprogress", "doing", "wip"],
    "已暂停": ["paused", "pause", "on_hold", "hold", "suspended"],
    "已完成": ["done", "complete", "completed", "closed", "finished"],
    "待办": ["todo", "to_do", "open"],
    "待审查": ["review", "in_review", "reviewing"],
    "阻塞": ["blocked", "stuck"],
    "高": ["high", "important"],
    "中": ["medium", "med", "mid", "normal"],
    "低": ["low", "minor", "later"],
    "决策": ["decision"],
    "坑": ["pitfall", "gotcha", "trap"],
    "约定": ["convention", "agreement", "rule"],
    "事实": ["fact", "info", "note"],
}


def _norm_key(value: str) -> str:
    """查表用的键：`In Progress` / `in-progress` / `in_progress` 折成同一个。"""
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _canonical(value, table: dict):
    """规范值放行；别名换成规范值；都不认识就原样返回（交给上层去拒）。"""
    if not isinstance(value, str):
        return value
    key = _norm_key(value)
    for canonical, aliases in table.items():
        if key == _norm_key(canonical) or key in aliases:
            return canonical
    return value


def canonical_status(value):
    """项目/任务状态：英文别名 → 规范值。"""
    return _canonical(value, FIELD_ALIASES)


def canonical_priority(value):
    """优先级：英文别名 → 规范值。"""
    return _canonical(value, FIELD_ALIASES)


def canonical_memory_type(value):
    """记忆类型：英文别名 → 规范值。"""
    return _canonical(value, FIELD_ALIASES)


def with_aliases(canonical: list[str]) -> list[str]:
    """规范值在前、别名在后 —— 给 MCP 的 `enum` 用（客户端照着广告的写法传即可）。

    规范值必须排在**最前**：客户端 UI 与模型多半照着第一项抄，而我们要它们抄规范值。
    """
    out = list(canonical)
    for value in canonical:
        out.extend(a for a in FIELD_ALIASES.get(value, []) if a not in out)
    return out


# 里程碑（P2 轻量版）上限：方案明确「追加型字段设上限，里程碑≦20 个」。
MAX_MILESTONES = 20
MAX_MILESTONE_NAME = 40   # 也用于任务的 milestone 归属字段（与名称同一截断口径）
# 阻塞原因上限（第六轮竞品吸取：tsk/plandeck 的"阻塞必须带原因"最小切片）。
# 上限只有一个数：TaskIn 清洗、MCP schema maxLength、界面 maxlength 共用（对账钉子）。
MAX_BLOCKED_REASON = 300
MAX_VIEW_NAME = 40
MAX_VIEW_QUERY = 200   # 视图里保存的搜索词上限
# 自定义视图（Plane Views 轻量版）：总览筛选条件的「命名 → 保存 → 切换」薄壳。
# 它是追加型存储（存 projects.json 顶层 `views`），按硬性约定 11 必须有上限。
MAX_SAVED_VIEWS = 30
# 备注被原样写进审计行：不设上限时，一次请求就能把一行撑成任意长
# （行数上限挡不住行内膨胀）。与 journal 同一条纪律：超限在边界被拒，不是被静默裁。
MAX_REPO_ACK_NOTE = 500
def _clean_milestones(v) -> list[dict]:
    """清洗项目的里程碑数组：`[{name, done?}]`。

    规则：只认带非空 name 的 dict（类型不对的整条丢弃）；name 去首尾空白并截断；
    同名去重（按首次出现的顺序保留）；`done` 只当布尔用，其余值一律按 False；
    总量封顶 `MAX_MILESTONES`。清出来的结构保证前端与统计模块拿到稳定形状。
    """
    out: list[dict] = []
    if isinstance(v, str):
        v = [{"name": x} for x in v.replace("\n", ",").split(",")]
    if not isinstance(v, (list, tuple)):
        return []
    for item in v:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:MAX_MILESTONE_NAME]
        if not name or name in [m["name"] for m in out]:
            continue
        out.append({"name": name, "done": bool(item.get("done"))})
        if len(out) >= MAX_MILESTONES:
            break
    return out
