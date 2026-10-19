# -*- coding: utf-8 -*-
"""第六轮竞品探测（2026-10-19）：GitHub 搜索 + 元数据核实。

带 GCM 凭据（git credential fill，token 只在内存）。排除前五轮已收录仓库。
输出：competitor_probe_r6.json（本目录）+ 候选摘要到 stdout。
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

OUT = r"D:\lo\项目任务看板\research-reports\competitor-round6-2026-10-19\competitor_probe_r6.json"
UA = {"User-Agent": "kanban-research-r6/1.0", "Accept": "application/vnd.github+json"}


def get_token():
    p = subprocess.run(["git", "credential", "fill"],
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return ""


TOKEN = get_token()
if TOKEN:
    UA["Authorization"] = f"Bearer {TOKEN}"


def api(url, tries=2):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            if e.code in (403, 429):
                sys.stderr.write(f"RATE-LIMIT {e.code}: {body}\n")
                return {"_error": f"HTTP {e.code}"}
            if attempt == 0:
                time.sleep(2)
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
            else:
                return {"_error": repr(e)}
    return {"_error": "exhausted"}


# ── 已收录（前五轮）──
SURVEYED = set()
import glob
import os
prev = [
    r"D:\lo\项目任务看板\research-reports\github-competitor-benchmark-2026-10-06\github_metadata.json",
    r"D:\lo\项目任务看板\research-reports\github-competitor-benchmark-2026-10-06\github_metadata_round2.json",
    r"D:\lo\项目任务看板\research-reports\github-competitor-round3-2026-10-08\github_metadata_round3.json",
    r"D:\lo\项目任务看板\research-reports\competitor-round5-2026-10-17\competitor_probe.json",
]
for path in prev:
    if not os.path.exists(path):
        continue
    d = json.load(open(path, encoding="utf-8"))
    items = d if isinstance(d, list) else d.get("repos", d.get("items", d))
    if isinstance(items, dict):
        items = list(items.values())
    for it in items:
        if isinstance(it, dict):
            name = it.get("full_name") or it.get("repo") or it.get("name")
            if name:
                SURVEYED.add(name.lower())
SURVEYED.update({"thedotmack/claude-mem"})
print(f"已收录 {len(SURVEYED)} 个仓库，本轮排除", file=sys.stderr)

SEARCHES = [
    "handoff project state agent created:>2026-08-01",
    "agent task board created:>2026-08-01",
    "context handoff skill agent created:>2026-08-01",
    "project status dashboard AI agent created:>2026-08-01",
    "session continuity agent created:>2026-08-01",
    "spec driven development agent created:>2026-08-01",
    "kanban AI created:>2026-06-01",
    "MCP task manager server created:>2026-08-01",
    "agent progress tracker created:>2026-08-01",
    "multi agent project management in:name,description,readme created:>2026-08-01",
]


def search(q, pages=1):
    out = []
    for pg in range(1, pages + 1):
        url = ("https://api.github.com/search/repositories?q="
               + urllib.parse.quote(q) + f"&sort=stars&order=desc&per_page=30&page={pg}")
        d = api(url)
        if "_error" in d:
            sys.stderr.write(f"search error {q}: {d['_error']}\n")
            break
        out.extend(d.get("items", []))
        if len(d.get("items", [])) < 30:
            break
        time.sleep(3)
    return out


import urllib.parse  # noqa: E402  (放在 search 之前定义也可以，这里为了可读性)

results = {}
now = time.strftime("%Y-%m-%d")
for q in SEARCHES:
    items = search(q)
    for it in items:
        fn = it["full_name"]
        if fn.lower() in SURVEYED:
            continue
        if fn not in results:
            results[fn] = {
                "full_name": fn,
                "stars": it["stargazers_count"],
                "pushed_at": it["pushed_at"],
                "created_at": it["created_at"],
                "description": (it.get("description") or "")[:200],
                "topics": it.get("topics", []),
                "found_by": [q],
            }
        else:
            results[fn]["found_by"].append(q)
    sys.stderr.write(f"q={q!r} -> {len(items)} hits\n")
    time.sleep(3.5)

# 只留有动静的：star>=3 或 最近 60 天有推送
kept = {}
for fn, r in results.items():
    fresh = r["pushed_at"] >= "2026-08-20"
    if r["stars"] >= 3 or fresh:
        kept[fn] = r

json.dump({"generated": now, "query_count": len(SEARCHES),
           "total_hits_unique": len(results), "kept": kept},
          open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print(f"unique={len(results)} kept={len(kept)} -> {OUT}")
for fn, r in sorted(kept.items(), key=lambda kv: -kv[1]["stars"]):
    desc = (r["description"] or "")[:80]
    print(f"{r['stars']:>6}★ {r['pushed_at'][:10]} {fn:<55} {desc}")
