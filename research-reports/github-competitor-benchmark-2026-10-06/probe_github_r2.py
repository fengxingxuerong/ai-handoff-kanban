# -*- coding: utf-8 -*-
"""第二轮探测：补漏仓库元数据 + 抓取重点竞品 README（权威一手来源）。"""
import json
import os
import time
import urllib.request
import urllib.error

BASE = r"D:\lo\项目任务看板\research-reports\github-competitor-benchmark-2026-10-06"
OUT = os.path.join(BASE, "github_metadata_round2.json")
UA = {"User-Agent": "loomy-research/1.0", "Accept": "application/vnd.github+json"}
UA_RAW = {"User-Agent": "loomy-research/1.0", "Accept": "application/vnd.github.raw"}

REPOS2 = [
    "multica-ai/multica",
    "letta-ai/letta-code",
    "mksglu/context-mode",
    "eugeniughelbur/obsidian-second-brain",
    "vshulcz/deja-vu",
    "Dicklesworthstone/cass_memory_system",
    "parcadei/Continuous-Claude-v3",
    "modiqo/waggle",
    "emiliojohann/FocusClaw",
    "getzep/graphiti",
    "GembaCore/gemba-core",
    "alamops/agetor",
    "cortexkit/magic-context",
]

READMES = [
    "multica-ai/multica",
    "steveyegge/beads",
    "MrLesk/Backlog.md",
    "iAmCorey/wake",
    "pingdotgg/t3code",
    "eugeniughelbur/obsidian-second-brain",
    "buildermethods/agent-os",
]


def get(url, headers, timeout=20):
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return f"_HTTPERROR_{e.code}"
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            return f"_ERROR_{type(e).__name__}: {e}"
    return "_ERROR_unreachable"


def main():
    out = {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "repos": {}}
    for full in REPOS2:
        raw = get(f"https://api.github.com/repos/{full}", UA)
        if raw.startswith("_"):
            out["repos"][full] = {"_error": raw}
            print(f"[ERR] {full}: {raw}")
        else:
            d = json.loads(raw)
            s = {
                "full_name": d.get("full_name"),
                "stars": d.get("stargazers_count"),
                "language": d.get("language"),
                "created_at": (d.get("created_at") or "")[:10],
                "pushed_at": (d.get("pushed_at") or "")[:10],
                "archived": d.get("archived"),
                "license": (d.get("license") or {}).get("spdx_id"),
                "description": (d.get("description") or "")[:200],
            }
            out["repos"][full] = s
            print(f"{s['stars']:>7} | {s['pushed_at']} | {full} | {s['description'][:90]}")
        time.sleep(0.6)

    os.makedirs(os.path.join(BASE, "readmes"), exist_ok=True)
    for full in READMES:
        raw = get(f"https://api.github.com/repos/{full}/readme", UA_RAW)
        fn = os.path.join(BASE, "readmes", full.replace("/", "__") + ".md")
        if raw.startswith("_"):
            print(f"[README ERR] {full}: {raw}")
            continue
        with open(fn, "w", encoding="utf-8") as f:
            f.write(raw[:20000])
        print(f"[README OK] {full}: {len(raw)} chars -> {fn}")
        time.sleep(0.6)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nSaved -> {OUT}")


if __name__ == "__main__":
    main()
