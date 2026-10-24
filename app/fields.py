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
