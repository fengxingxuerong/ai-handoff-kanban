# -*- coding: utf-8 -*-
"""
AI 功能冒烟测试：监管分析 / NL 指令 / 交接生成 / 建议应用 / 报告列表
用法：python scripts/ai_smoke_test.py [base_url]
注意：会真实调用 LLM（每个功能 1 次），请确认配额充足。
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
        with urllib.request.urlopen(r, timeout=180) as resp:
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


print("== 1. AI 配置 ==")
s, r = req("GET", "/api/ai/config")
eps = r.get("ai", {}).get("endpoints", [])
check("配置读取", s == 200 and len(eps) >= 1)
check("Key 已掩码", all(("****" in k or k == "") for ep in eps for k in ep.get("api_keys", [])), str([ep["api_keys"] for ep in eps]))

print("== 2. 连接测试 ==")
s, r = req("POST", "/api/ai/test")
check("连通测试", s == 200 and r.get("ok") is True, str(r))

print("== 3. 监管分析 ==")
s, r = req("POST", "/api/ai/analyze")
check("分析成功", s == 200 and r.get("id"), str(r)[:200])
rid = r.get("id")
if s == 200:
    check("含摘要", bool(r.get("summary")))
    check("含项目洞察", isinstance(r.get("insights"), list))

print("== 4. 报告列表与详情 ==")
s, r = req("GET", "/api/ai/reports")
check("报告列表", s == 200 and any(x["id"] == rid for x in r.get("reports", [])))
s, r = req("GET", f"/api/ai/reports/{rid}")
check("报告详情", s == 200 and r.get("id") == rid)

print("== 5. NL 指令解析 ==")
s, r = req("POST", "/api/ai/command", {"text": "给第一个进行中的项目加一个任务：整理部署文档，优先级高"})
check("指令解析", s == 200 and ("actions" in r.get("parsed", {}) or "need_clarification" in r.get("parsed", {})), str(r)[:300])

print("== 6. 交接文档生成 ==")
s, plist = req("GET", "/api/projects")
pid = plist["projects"][0]["id"] if plist["projects"] else ""
if pid:
    s, r = req("POST", f"/api/ai/handoff/{pid}", {"mode": "generate"})
    check("交接生成", s == 200 and len(r.get("text", "")) > 50, str(r)[:200])
else:
    check("交接生成（跳过：无项目）", True)

print("== 7. 建议应用 ==")
s, r = req("POST", "/api/ai/apply-suggestions", [{"project_id": pid, "field": "next_action", "value": "由 AI 冒烟测试写入的下一步行动"}])
check("建议应用", s == 200 and r.get("applied") == 1, str(r))

print(f"\n结果：{len(passed)} 通过 / {len(failed)} 失败")
sys.exit(1 if failed else 0)
