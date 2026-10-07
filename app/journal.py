"""过程轨迹（journal）与验收标准（acceptance）的清洗与上限 —— 纯函数簇。

2026-10-07 从 main 拆出。journal 存在 projects.json 里 —— 那是每次读写都要整份解析的
核心文件，状态流转还会自动追加，不设上限就是"悄悄变胖"（硬性约定 11）；
但"只留最近 N 条"意味着早期记录无声消失 —— 所以溢出部分**折叠成摘要**而不是丢弃
（硬性约定 25，对标 Beads 的语义压缩）。

**上限常量的 monkeypatch 点在这里**（main 里是转发别名）：下面的函数读的是本模块的
全局，patch `app.main.MAX_JOURNAL` 改不到行为 —— 测试请 patch `app.journal.MAX_JOURNAL`。
时钟由调用方注入：main 的 `_append_journal` 包装负责把 `when` 缺省成 `now_str()`，
本模块不 import main（方向只能是 main → journal）。
"""

# 上限只有一个数（硬性约定 21 的 journal 版）：HTTP/MCP 的入参上限、schema 的 maxLength、
# 落盘截断长度、界面 maxlength 全用它们。对账钉子：test_journal_limit_is_one_number_everywhere
MAX_JOURNAL = 50           # 每个任务最多留多少条过程记录
MAX_JOURNAL_TEXT = 500     # 单条正文上限：这一个数同时是 HTTP/MCP 的入参上限与落盘长度
JOURNAL_SNIPPET = 60       # 折叠摘要里每条旧记录保留的字符数（摘要整体仍受 MAX_JOURNAL_TEXT 约束）

# 验收标准（对标 Backlog.md 的可复用 DoD 清单）：任务"怎么算完成"要明说，
# 否则"待审查→已完成"全凭当时的人/智能体心情，接手方也没有验收清单可照。
MAX_ACCEPTANCE_ITEMS = 10  # 每个任务最多多少条验收标准
MAX_ACCEPTANCE_TEXT = 120  # 单条验收标准长度上限
                           # （以前入参收 2000、落盘悄悄截到 500，两侧的"1200 字记录"
                           # 会少掉 700 字而回执不说 —— 现在超限在边界就被挡）


def append_journal(t: dict, text: str, when: str) -> None:
    """往任务追加一条过程轨迹，超出上限的旧条目**折叠成摘要**而不是直接丢掉。

    `when`（时间戳）由调用方给 —— main 的包装负责缺省成当前时间，本模块不碰时钟。
    就地修改 `t`。
    """
    j = t.get("journal")
    if not isinstance(j, list):
        j = t["journal"] = []
    j.append({"time": when, "text": (text or "")[:MAX_JOURNAL_TEXT]})
    if len(j) > MAX_JOURNAL:
        t["journal"] = compress_journal(j)


def compress_journal(entries: list[dict]) -> list[dict]:
    """把超出 `MAX_JOURNAL` 的最旧条目折成一条摘要，总条数压回上限内。

    纯函数：不修改入参。摘要条目带 `folded` 计数（累计折进去多少条原始记录，
    再次折叠时继承），`time` 是被折区间的首尾日期，正文仍是受限的普通文本 ——
    前端/交接包不需要任何特殊处理就能显示，只是这条以【早期…已折叠】开头。
    """
    entries = [e for e in entries if isinstance(e, dict)]
    if len(entries) <= MAX_JOURNAL:
        return entries
    cut = len(entries) - MAX_JOURNAL          # 溢出条数；再多折 1 条进来给摘要腾位置
    overflow, keep = entries[:cut + 1], entries[cut + 1:]
    folded = sum(int(e.get("folded") or 0) or 1 for e in overflow)
    digest = "；".join((e.get("text") or "")[:JOURNAL_SNIPPET] for e in overflow)
    summary = {
        "time": f"{(overflow[0].get('time') or '')[:10]}~{(overflow[-1].get('time') or '')[:10]}",
        "text": (f"【早期 {folded} 条已折叠】{digest}")[:MAX_JOURNAL_TEXT],
        "folded": folded,
    }
    return [summary] + keep


def clean_journal(raw) -> list[dict]:
    """规范化过程轨迹：只留字典条目、限长，超出 `MAX_JOURNAL` 的折叠成摘要
    （导入的脏数据与手改的 projects.json 也走这里，读路径上自愈）"""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        e = {
            "time": str(item.get("time") or "")[:32],
            "text": str(item.get("text") or "")[:MAX_JOURNAL_TEXT],
        }
        # folded 计数跟着条目走：折叠摘要再次落入溢出区时要按它折进来的原始条数累计
        if isinstance(item.get("folded"), int) and item["folded"] > 0:
            e["folded"] = item["folded"]
        out.append(e)
    return compress_journal(out)


def clean_acceptance(raw) -> list[str]:
    """规范化验收标准（对标 Backlog.md 的 DoD 清单）：只留字符串、去空去重、限量限长。

    接受字符串或列表（列表元素逐条清洗）；非字符串条目转成字符串保留 ——
    智能体经 MCP 传数字/布尔时，转写比整条丢弃更接近调用方本意。
    """
    if isinstance(raw, str):
        raw = raw.replace("\r", "").split("\n")
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        s = (item if isinstance(item, str) else str(item)).strip()[:MAX_ACCEPTANCE_TEXT]
        if s and s not in out:
            out.append(s)
        if len(out) >= MAX_ACCEPTANCE_ITEMS:
            break
    return out
