# -*- coding: utf-8 -*-
"""第七轮：对精选候选拉 README 全文（带 GCM 凭据）。输出 competitor_readmes_r7.json。"""
import json
import os
import subprocess
import sys
import time
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "competitor_readmes_r7.json")

REPOS = [
    "paperclipai/paperclip",
    "stablyai/orca",
    "nimbalyst/nimbalyst",
    "BradGroux/veritas-kanban",
    "saltbo/agent-kanban",
    "hasanyilmaz/operon",
    "antopolskiy/kanban-md",
    "PerpetualSoftware/pad",
    "kanban-rs/kanban",
    "Lexus2016/claude-code-studio",
    "ayanamislover/AyanamiTaskManager",
    "rasimme/FlowBoard",
    "shiquda/roadmap-skill",
    "ScriptedAlchemy/tracedecay",
    "jmagly/aiwg",
    "OthmanAdi/planning-with-files",
    "tcarac/taskboard",
    "yaront1111/Moe-s-Tavern",
    "defremont/Shipyard",
    "kirgx/task-rail",
    "Simon-Wong-hjz/switch",
    "jiva-studio/tandm-tasks",
    "dataking5d/context-handoff",
    "ziucode/agent-session-bridge",
    "greatfreedom/handoffd",
    "qurs/cc-autohandoff",
    "EKKOLearnAI/ekko-studio",
    "usekaneo/kaneo",
    "Fission-AI/OpenSpec",
    "sirsjg/flux",
    "CKang-J/Kith-space",
    "yulmucoffee/artiboard",
    "hume-n/agent-crew",
    "pranjal-joshi/claude-crew",
    "chillrain666/quadrant-md",
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
H = {"User-Agent": "kanban-research-r7/1.0", "Accept": "application/vnd.github.raw"}
if TOKEN:
    H["Authorization"] = f"Bearer {TOKEN}"

out = {}
for fn in REPOS:
    url = f"https://api.github.com/repos/{fn}/readme"
    try:
        req = urllib.request.Request(url, headers=H)
        with urllib.request.urlopen(req, timeout=25) as r:
            text = r.read().decode("utf-8", "replace")
        out[fn] = text[:26000]
        print(f"OK   {fn}  {len(text)} chars")
    except Exception as e:
        out[fn] = f"__ERROR__ {e!r}"
        print(f"FAIL {fn}  {e!r}", file=sys.stderr)
    time.sleep(1.0)

json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved", OUT)
