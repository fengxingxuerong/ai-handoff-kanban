# -*- coding: utf-8 -*-
"""
回归测试：覆盖 2026-09-14 优化项
  1. 并发写入不丢数据（原 P0：20 并发只落盘 3 条）
  2. 特殊字符项目名（含单引号/尖括号）可正常创建、读取、删除
  3. 状态/优先级/进度越界被 422 拒绝（原来可写入任意字符串，会污染前端 class 与文本）
  4. Host / Origin 防护（拒绝非本机 Host 与跨站 Origin）
  5. 导出保存路径限制（拒绝系统目录）
  6. KANBAN_TOKEN 开启后写接口鉴权

用法：
    python scripts/regression_test.py [base_url]     # 默认 http://127.0.0.1:18320
需要服务已启动，且数据目录可用（建议用 KANBAN_DATA_DIR 指向临时目录跑）。
"""
import concurrent.futures as cf
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18320"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else None  # 服务端开了 KANBAN_TOKEN 时传入

passed, failed = [], []


def req(method, path, body=None, headers=None, raw_origin=None, raw_host=None, origin=None):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["X-Kanban-Token"] = TOKEN
    if headers:
        h.update(headers)
    if origin:
        h["Origin"] = origin
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            ct = resp.headers.get("content-type", "")
            return resp.status, (json.loads(raw) if "json" in ct else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("  [OK] " if cond else "  [FAIL] ") + name + ("" if cond else "  " + str(extra)[:200]))


def new_project(name, **kw):
    body = {"name": name, "status": "进行中", "progress": 10, "priority": "高"}
    body.update(kw)
    return req("POST", "/api/projects", body)


print("== 1. 特殊字符项目名 ==")
weird = "张三's 项目 <b>\"引号\"</b>"
s, r = new_project(weird)
check("含撇号/引号/尖括号的名称可创建", s == 201, f"s={s} {r}")
pid = r.get("id") if isinstance(r, dict) else None
s, r = req("GET", f"/api/projects/{pid}")
check("读回名称与写入一致", s == 200 and r.get("name") == weird, f"s={s} {str(r)[:120]}")
s, r = req("DELETE", f"/api/projects/{pid}")
check("可正常删除该名称的项目", s == 200, f"s={s} {r}")

print("== 2. 字段白名单（越界值应 422） ==")
s, _ = new_project("非法状态", status="进行中<script>")
check("非法 status 被拒", s == 422, f"s={s}")
s, _ = new_project("非法优先级", priority="超高")
check("非法 priority 被拒", s == 422, f"s={s}")
s, _ = new_project("越界进度", progress=250)
check("progress 越界被拒", s == 422, f"s={s}")

print("== 3. 并发写入不丢数据 ==")
N = 20
before = set(p["id"] for p in req("GET", "/api/projects")[1]["projects"])
with cf.ThreadPoolExecutor(max_workers=N) as ex:
    codes = list(ex.map(lambda i: new_project(f"并发-{i}")[0], range(N)))
after = set(p["id"] for p in req("GET", "/api/projects")[1]["projects"])
ok201 = sum(1 for c in codes if c == 201)
new = after - before
check(f"{N} 并发创建全部落盘（无丢更新）", ok201 == N and len(new) == N, f"201={ok201} 落盘={len(new)}")
for p in req("GET", "/api/projects")[1]["projects"]:
    if p["id"] in new:
        req("DELETE", f"/api/projects/{p['id']}")

print("== 4. Host / Origin 防护 ==")
s, _ = req("GET", "/api/health", origin="http://evil.example.com")
check("跨站 Origin 被拒绝", s == 403, f"s={s}")
s, _ = req("GET", "/api/health", origin=BASE)
check("同源 Origin 正常放行", s == 200, f"s={s}")
s, _ = req("GET", "/api/health", headers={"Host": "evil.example.com"})
check("非本机 Host 被拒绝", s == 403, f"s={s}")

print("== 5. 导出保存路径限制 ==")
tmp_dir = os.path.join(tempfile.gettempdir(), "kanban_regress")
ok_path = os.path.join(tmp_dir, "x.md")
s, r = req("POST", "/api/export/save", {"filename": "x.md", "content": "# hi", "path": r"C:\Windows\evil.md"})
check("拒绝写入系统目录", s == 400, f"s={s} {r}")
s, r = req("POST", "/api/export/save", {"filename": "x.md", "content": "# hi", "path": ok_path})
check("普通路径（临时目录）可写入", s == 200, f"s={s} {r}")
home_cfg = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "kanban_regress_evil.md")
s, r = req("POST", "/api/export/save", {"filename": "x.md", "content": "# hi", "path": home_cfg})
check("AppData 非临时目录被拒", s == 400, f"s={s} {r}")
s, r = req("POST", "/api/export/save", {"filename": "bad.exe", "content": "x", "path": os.path.join(tmp_dir, "bad.exe")})
check("后缀白名单拦截 .exe", s == 400, f"s={s}")
try:
    os.remove(ok_path)
except OSError:
    pass

print("== 6. Token 鉴权（仅当服务端开启 KANBAN_TOKEN 时有意义） ==")
if TOKEN:
    global_token_backup = TOKEN
    TOKEN = None
    s, _ = new_project("无 token 写入")
    TOKEN = global_token_backup
    check("无 Token 的写请求被拒 401", s == 401, f"s={s}")

print(f"\n结果：{len(passed)} 通过 / {len(failed)} 失败")
if failed:
    print("失败项：" + " | ".join(failed))
sys.exit(1 if failed else 0)
