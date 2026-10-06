# -*- coding: utf-8 -*-
"""GitHub API 竞品探测：批量取仓库元数据 + 主题搜索。
输出 JSON 到研究目录 github_metadata.json，并打印紧凑摘要。
零依赖（urllib），带超时与重试，搜索间隔 8 秒防限流。
"""
import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

OUT = r"D:\lo\项目任务看板\research-reports\github-competitor-benchmark-2026-10-06\github_metadata.json"
UA = {"User-Agent": "loomy-research/1.0", "Accept": "application/vnd.github+json"}

REPOS = [
    # AI 原生任务管理（agent-first）
    "steveyegge/beads",
    "eyaltoledano/claude-task-master",
    "MrLesk/Backlog.md",
    "snarktank/ai-dev-tasks",
    "buildermethods/agent-os",
    # 会话记忆/上下文层（新品类）
    "xingkaixin/agent-dump",
    "EwanJasper/SessionRelay",
    "SamCuipogobongo/scrollback",
    "iAmCorey/wake",
    "dkothule/ai-context",
    "mattpocock/skills",
    # Agent 记忆基础设施
    "mem0ai/mem0",
    "letta-ai/letta",
    "basicmachines-co/basic-memory",
    "basicmachines-co/mcp-memory-service",
    # Agent 观测
    "langfuse/langfuse",
    "AgentOps-AI/agentops",
    # 通用开源 PM（复核既有对标 + 近期动向）
    "makeplane/plane",
    "hcengineering/platform",
    "kanboard/kanboard",
    "wekan/wekan",
    "plankanban/planka",
    "go-vikunja/vikunja",
    "Leantime/Leantime",
    "opf/openproject",
    "taigaio/taiga",
    "AppFlowy-IO/AppFlowy",
    # Agent 编排看板（vibe-kanban 与其后继们）
    "BloopAI/vibe-kanban",
    "smtg-ai/claude-squad",
    "pingdotgg/t3code",
]

SEARCHES = [
    "agent kanban orchestration created:>2026-04-01",
    "coding agent session memory",
    "handoff agent context",
    "AI task management agents created:>2026-06-01",
    "multica agent project",
]


def get(url, timeout=15):
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            if e.code in (403, 429):
                sys.stderr.write(f"RATE-LIMIT {e.code} on {url}: {body}\n")
                return {"_error": f"HTTP {e.code}", "_body": body}
            if attempt == 0 and e.code >= 500:
                time.sleep(2)
                continue
            return {"_error": f"HTTP {e.code}", "_url": url}
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            return {"_error": f"{type(e).__name__}: {e}", "_url": url}
    return {"_error": "unreachable", "_url": url}


def slim(d):
    if "_error" in d:
        return d
    return {
        "full_name": d.get("full_name"),
        "stars": d.get("stargazers_count"),
        "forks": d.get("forks_count"),
        "open_issues": d.get("open_issues_count"),
        "language": d.get("language"),
        "created_at": (d.get("created_at") or "")[:10],
        "pushed_at": (d.get("pushed_at") or "")[:10],
        "archived": d.get("archived"),
        "license": (d.get("license") or {}).get("spdx_id"),
        "description": (d.get("description") or "")[:160],
        "topics": (d.get("topics") or [])[:8],
    }


def main():
    out = {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "repos": {}, "searches": {}}
    for full in REPOS:
        d = get(f"https://api.github.com/repos/{full}")
        out["repos"][full] = slim(d)
        s = out["repos"][full]
        if "_error" in s:
            print(f"[ERR] {full}: {s['_error']}")
        else:
            print(f"{s['stars']:>7} | {s['pushed_at']} | {'ARCHIVED' if s['archived'] else '        '} | {full} | {s['description'][:80]}")
        time.sleep(0.6)
    for i, q in enumerate(SEARCHES):
        if i > 0:
            time.sleep(8)
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": q, "sort": "stars", "order": "desc", "per_page": 10}
        )
        d = get(url)
        items = [slim(x) for x in d.get("items", [])] if isinstance(d, dict) else []
        out["searches"][q] = {"error": d.get("_error") if isinstance(d, dict) and "_error" in d else None, "items": items}
        print(f"\n=== SEARCH: {q} (total {d.get('total_count', 'ERR')}) ===")
        for it in items:
            print(f"{it['stars']:>7} | {it['pushed_at']} | {it['full_name']} | {it['description'][:70]}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nSaved -> {OUT}")


if __name__ == "__main__":
    main()
