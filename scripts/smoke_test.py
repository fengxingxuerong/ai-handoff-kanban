# -*- coding: utf-8 -*-
"""
API 冒烟测试：验证看板后端全部接口可用
用法：python scripts/smoke_test.py [base_url]  （默认 http://127.0.0.1:18320）
"""
import json
import sys
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18320"

passed, failed = [], []


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            ct = resp.headers.get("content-type", "")
            return resp.status, (json.loads(raw) if "json" in ct else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check(name, cond, extra=""):
    if cond:
        passed.append(name)
        print(f"  ✓ {name}")
    else:
        failed.append(name)
        print(f"  ✗ {name}  {extra}")


print("== 1. 基础接口 ==")
s, r = req("GET", "/api/health")
check("health", s == 200 and r.get("ok") is True)
s, r = req("GET", "/")
check("首页 index.html", s == 200 and "AI 交接看板" in r)

print("== 2. 项目 CRUD ==")
s, r = req("GET", "/api/projects")
check("项目列表", s == 200 and "projects" in r, f"s={s}")

s, r = req("POST", "/api/projects", {
    "name": "冒烟测试项目", "status": "规划中", "progress": 10,
    "priority": "高", "tags": ["测试"], "repo_path": "D:\\test\\demo",
    "background": "用于 API 冒烟测试的项目",
    "handoff_note": "这是一个测试交接说明，包含中文与标点！",
    "next_action": "跑通冒烟测试",
})
check("创建项目", s == 201 and r.get("name") == "冒烟测试项目", f"s={s}")
pid = r.get("id")

s, r = req("GET", f"/api/projects/{pid}")
check("项目详情", s == 200 and r["name"] == "冒烟测试项目")

s, r = req("POST", f"/api/projects/{pid}/tasks", {
    "title": "联调登录模块", "desc": "前后端联调，验收标准为可正常登录",
    "status": "待办", "priority": "高", "agent": "cline",
})
check("创建任务", s == 201 and r["title"] == "联调登录模块", f"s={s}")
tid = r.get("id")

s, r = req("PUT", f"/api/tasks/{tid}", {
    "title": "联调登录模块", "desc": "前后端联调，验收标准为可正常登录",
    "status": "进行中", "priority": "高", "agent": "cline",
})
check("更新任务状态", s == 200 and r["status"] == "进行中")

print("== 3. 交接导出 ==")
s, r = req("GET", f"/api/export/{pid}")
check("导出 Markdown 交接文档", s == 200 and "冒烟测试项目" in r and "交接说明" in r)

print("== 4. 删除 ==")
s, r = req("DELETE", f"/api/tasks/{tid}")
check("删除任务", s == 200)
s, r = req("DELETE", f"/api/projects/{pid}")
check("删除项目", s == 200)
s, r = req("GET", f"/api/projects/{pid}")
check("删除后 404", s == 404)

print(f"\n结果：{len(passed)} 通过 / {len(failed)} 失败")
sys.exit(1 if failed else 0)
