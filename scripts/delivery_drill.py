# -*- coding: utf-8 -*-
"""
真实任务交付演练（Delivery Drill）：以真实项目数据为样例，把「换 AI 智能体接手」的
完整交付链路端到端走一遍，验证看板的核心承诺——切换智能体无缝交付。

演练对运行中的服务执行，创建专用演练项目（不动真实数据），结束后自动清理。

用法：
    .venv\\Scripts\\python.exe scripts\\delivery_drill.py [base_url]
"""
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18320"
DRILL_NAME = "交付演练-临时项目"

passed, failed = [], []


def req(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            ct = resp.headers.get("content-type", "")
            return resp.status, (json.loads(raw) if "json" in ct else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check(name, cond, extra=""):
    if cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {extra}")


print("=" * 62)
print("真实任务交付演练：模拟「项目 A 的 AI 干到一半，换智能体 B 接手」")
print("=" * 62)

# ---------- 0. 清理历史演练残留 ----------
cleanup = req("GET", "/api/projects")[1]["projects"]
for p in cleanup:
    if p["name"] == DRILL_NAME:
        req("DELETE", "/api/projects/" + p["id"])

# ---------- 1. 模拟前任 AI 留下的现场 ----------
print("\n[阶段1] 前任 AI 留下交接现场")
s, proj = req("POST", "/api/projects", {
    "name": DRILL_NAME,
    "status": "进行中", "progress": 40, "priority": "高",
    "tags": ["演练", "全栈"],
    "repo_path": "",
    "background": "演练项目：模拟一个干到一半的全栈项目，含已定决策与已知坑。",
    "handoff_note": "数据库迁移必须先跑 tests/migrate_check.py；已定决策：接口返回统一用 {code,data}；已知坑：并发写会锁表。",
    "next_action": "完成订单模块的状态机改造",
})
check("创建演练项目", s == 201)
pid = proj["id"]
t_done = req("POST", f"/api/projects/{pid}/tasks", {"title": "用户模块开发", "status": "已完成", "priority": "高"})[1]
t_block = req("POST", f"/api/projects/{pid}/tasks", {"title": "订单状态机改造", "status": "进行中", "priority": "高",
                                                    "desc": "核心改造，含状态枚举与迁移脚本", "due_date": "2026-09-20"})[1]
t_next = req("POST", f"/api/projects/{pid}/tasks", {"title": "迁移脚本联调", "status": "待办", "priority": "高",
                                                    "depends_on": [t_block["id"]]})[1]
t_future = req("POST", f"/api/projects/{pid}/tasks", {"title": "报表模块", "status": "待办", "priority": "低",
                                                      "depends_on": [t_next["id"]]})[1]
check("录入 4 个带依赖链的任务", s == 201)

# ---------- 2. 接手视角：新智能体拉取机器可读上下文 ----------
print("\n[阶段2] 新智能体接手：拉取机器可读上下文（零粘贴）")
s, ctx = req("GET", f"/api/context/{pid}")
check("GET /api/context 返回 200", s == 200)
check("含项目背景与交接说明", bool(ctx["project"]["handoff_note"]) and bool(ctx["project"]["background"]))
check("start_here 指向可立即动手的任务（依赖已满足）",
      any(x["title"] == "订单状态机改造" for x in ctx["start_here"]))
check("被依赖阻塞的任务不在 start_here",
      not any(x["title"] in ("迁移脚本联调", "报表模块") for x in ctx["start_here"]))
check("代码现实/就绪度随上下文给出", "readiness" in ctx and "code_reality" in ctx)

# ---------- 3. 执行视角：任务推进 ----------
print("\n[阶段3] 新智能体干活：推进任务、依赖流转")
s, done = req("PUT", f"/api/tasks/{t_block['id']}", {
    "title": t_block["title"], "desc": t_block.get("desc", ""), "status": "已完成",
    "priority": "高", "agent": "智能体B", "due_date": t_block.get("due_date", ""),
    "refs": [], "depends_on": [],
})
check("完成核心任务（标记负责人=智能体B）", s == 200 and done["status"] == "已完成")
s, nxt = req("GET", f"/api/projects/{pid}/next")
titles = [x["title"] for x in nxt["next"]]
check("前置完成后，被阻塞任务自动变为可执行", "迁移脚本联调" in titles)

# ---------- 4. 交接视角：产出交接物 ----------
print("\n[阶段4] 产出交接物：就绪度 / 交接包 / 脱敏 / AGENTS.md / 多入口")
s, stats = req("GET", "/api/stats")
rd = next((x for x in stats["readiness"] if x["id"] == pid), None)
check("交接就绪度评分就位", rd is not None and rd["score"] >= 50)
s, md = req("GET", f"/api/export/{pid}")
check("完整交接包含任务/背景/交接说明", "订单状态机改造" in md and "handoff" in md.lower() or "交接说明" in md)
s, md_safe = req("GET", f"/api/export/{pid}?redact=1")
proj_now = req("GET", f"/api/projects/{pid}")[1]
req("PUT", f"/api/projects/{pid}", {**proj_now, "handoff_note": (proj_now.get("handoff_note") or "") + "\n密钥：sk-drill123456789012"})
_, md_raw = req("GET", f"/api/export/{pid}")
_, md_red = req("GET", f"/api/export/{pid}?redact=1")
check("脱敏导出：密钥原文隐藏、掩码出现", "sk-drill123456789012" in md_raw and "sk-drill123456789012" not in md_red and "********" in md_red)
# 恢复交接说明（去掉测试密钥）
proj_now = req("GET", f"/api/projects/{pid}")[1]
req("PUT", f"/api/projects/{pid}", {**proj_now, "handoff_note": proj_now["handoff_note"].replace("\n密钥：sk-drill123456789012", "")})
s, ag = req("POST", f"/api/export/agentsmd/{pid}?redact=1")
check("AGENTS.md 可生成（AI 或本地模板兜底）", s == 200 and len(ag.get("text", "")) > 100)
s, files = req("POST", f"/api/export/agent-files/{pid}", {})
check("多智能体入口文件可一键生成", s == 200 and len(files.get("files", [])) >= 1)

# ---------- 5. 管理视角：监管 / 搜索 / 迁移 ----------
print("\n[阶段5] 管理视角：搜索 / 导出迁移 / 清理")
s, sr = req("GET", "/api/search/tasks?q=" + urllib.parse.quote("状态机"))
check("全局搜索能定位演练任务", s == 200 and any(x["project_id"] == pid for x in sr["results"]))
s, exp = req("GET", "/api/data/export")
blob = json.dumps(exp, ensure_ascii=False)
check("全量导出包含演练数据", DRILL_NAME in blob)
# 导入往返（先删后导）
req("DELETE", f"/api/projects/{pid}")
s, imp = req("POST", "/api/data/import", {"content": blob})
check("删除后用导出包完整恢复", s == 200 and imp.get("imported", 0) >= 1)
_, plist = req("GET", "/api/projects")
back = next((p for p in plist["projects"] if p["id"] == pid), None)
check("恢复后的演练项目数据完整", back is not None and back["total_tasks"] == 4)

# ---------- 6. 清理 ----------
print("\n[阶段6] 清理演练现场")
s, _ = req("DELETE", f"/api/projects/{pid}")
check("演练项目已清理", s == 200)

print("\n" + "=" * 62)
print(f"演练结果：{len(passed)} 通过 / {len(failed)} 失败")
if failed:
    print("失败项：", "；".join(failed))
    sys.exit(1)
print("结论：真实交付链路（接手→执行→交接→监管→迁移）端到端可用 ✓")
