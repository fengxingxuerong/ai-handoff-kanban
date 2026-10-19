# -*- coding: utf-8 -*-
"""第六轮：对精选候选拉 README 全文（带 GCM 凭据）。输出 competitor_readmes_r6.json。"""
import json
import subprocess
import sys
import time
import urllib.request

OUT = r"D:\lo\项目任务看板\research-reports\competitor-round6-2026-10-19\competitor_readmes_r6.json"

REPOS = [
    "NxcoreAI/EverRoom",
    "ranxianglei/billion-context",
    "aoci-spec/aoci-code",
    "smarzban/tsk",
    "nelsonPires5/herdr-board",
    "dualface/kander",
    "ustoppble/overclick",
    "makifbaysal/tasktrooper",
    "jklthinking/retinue",
    "JoxoAI/joxo",
    "Ericwong5021/better-codex",
    "johnreginald/donewhen",
    "ai4kanban/ai4kanban",
    "OthmanAdi/plandeck",
    "jan-bogaerts/md2",
    "keviccz/AgentKanban",
    "azimov777/casefile",
    "imMamdouhaboammar/riqor",
    "LioraLabs/cliban",
    "webkubor/xiaobai-kanban",
]


def get_token():
    p = subprocess.run(["git", "credential", "fill"],
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return ""


TOKEN = get_token()
H = {"User-Agent": "kanban-research-r6/1.0", "Accept": "application/vnd.github.raw"}
if TOKEN:
    H["Authorization"] = f"Bearer {TOKEN}"

out = {}
for fn in REPOS:
    url = f"https://api.github.com/repos/{fn}/readme"
    try:
        req = urllib.request.Request(url, headers=H)
        with urllib.request.urlopen(req, timeout=25) as r:
            text = r.read().decode("utf-8", "replace")
        out[fn] = text[:24000]
        print(f"OK   {fn}  {len(text)} chars")
    except Exception as e:
        out[fn] = f"__ERROR__ {e!r}"
        print(f"FAIL {fn}  {e!r}", file=sys.stderr)
    time.sleep(1.2)

json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved", OUT)
