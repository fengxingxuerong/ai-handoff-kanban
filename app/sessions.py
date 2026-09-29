# -*- coding: utf-8 -*-
"""会话扫描与文本提取。

**从 `app/main.py` 整体搬来（2026-09-26 大文件拆分），内容逐字未改。**
`app/main.py` 用 `from .sessions import ...` 保住既有名字 —— 测试里的
`from app.main import X` 仍然有效。
"""
from __future__ import annotations

import heapq
import json
import os
from pathlib import Path

from fastapi import HTTPException

from . import llm

def session_sources() -> list[dict]:
    """默认探测常见 AI 工具会话目录；自定义源存 config.ai.session_sources"""
    home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    defaults = [
        {"name": "Claude Code", "path": str(home / ".claude" / "projects"), "type": "claude_jsonl"},
        {"name": "Cline (VS Code)", "path": str(home / "AppData" / "Roaming" / "Code" / "User"
                                                / "globalStorage" / "saoudrizwan.claude-dev" / "tasks"), "type": "cline"},
        {"name": "Cline (独立版)", "path": str(home / ".cline" / "data" / "tasks"), "type": "cline"},
    ]
    cfg = llm.load_config()
    custom = cfg.get("ai", {}).get("session_sources") or []
    return defaults + [c for c in custom if isinstance(c, dict) and c.get("path")]

def _walk_bounded(root: Path, budget: int = 2000, max_depth: int = 3):
    """懒走目录，最多吐 budget 个文件、深 max_depth 层。

    自定义会话源常被随手指到很宽的目录（甚至整块盘）。原来写的是
    `[f for f in root.rglob("*") if f.is_file()][:200]` —— 切片在推导式**之后**，
    等于先把整棵树物化进内存才想起来只要 200 个，一个宽路径就能挂住整个请求。
    """
    base_depth = len(root.parts)
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if len(Path(dirpath).parts) - base_depth >= max_depth:
            dirnames[:] = []            # 不再往下走
        for name in filenames:
            seen += 1
            if seen > budget:
                return
            yield Path(dirpath) / name

def _newest_files(paths, n: int = 8) -> list[Path]:
    """按修改时间取最近 n 个。

    单个文件 stat 失败（扫描过程中被删/被占用）跳过就行，不能像原来那样
    把它留给 `sorted(key=f.stat)` —— 那会让整次 /api/sessions/scan 直接 500。
    """
    scored = []
    for f in paths:
        try:
            scored.append((f.stat().st_mtime, f))
        except OSError:
            continue
    return [f for _, f in heapq.nlargest(n, scored, key=lambda x: x[0])]

def _read_tail_text(f: Path, limit_bytes: int = 1 << 20) -> str:
    """只读文件末尾 limit_bytes 字节。

    会话文件动辄几百 MB，而提取逻辑本来就是"尾部优先"：整份 `read_text()`
    等于把内存和一次几秒的 IO 搭进去，只为拿最后 12000 字。
    """
    try:
        size = f.stat().st_size
        with open(f, "rb") as fh:
            if size > limit_bytes:
                fh.seek(-limit_bytes, os.SEEK_END)
            data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", "replace")

def _extract_session_text(path: str, max_chars: int = 12000) -> str:
    """从会话文件提取对话文本（尾部优先）。支持 Claude Code JSONL / cline JSON / 纯文本"""
    f = Path(path)
    if not f.exists():
        raise HTTPException(404, "会话文件不存在")
    texts: list[str] = []
    if f.suffix.lower() == ".jsonl":
        try:
            lines = _read_tail_text(f).splitlines()
        except Exception:
            lines = []
        for line in lines[-200:]:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            msg = obj.get("message") or {}
            role = str(obj.get("type") or msg.get("role") or "")
            content = msg.get("content")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content
                                   if isinstance(c, dict) and c.get("type") == "text")
            if isinstance(content, str) and content.strip():
                tag = "用户" if "user" in role.lower() else "助手"
                texts.append(f"{tag}：{content.strip()[:600]}")
    elif f.suffix.lower() == ".json":
        try:
            data = json.loads(_read_tail_text(f))
        except Exception:
            data = None
        if isinstance(data, list):
            for m in data[-120:]:
                if not isinstance(m, dict):
                    continue
                txt = m.get("text") or ""
                say = str(m.get("say") or m.get("type") or "")
                if txt and "api_req" not in say and "tool" not in say:
                    role = "用户" if "user" in say else "助手"
                    texts.append(f"{role}：{str(txt)[:600]}")
    if not texts:
        texts = [_read_tail_text(f)[-6000:]]
    return "\n\n".join(texts)[-max_chars:]

def _session_roots() -> list[Path]:
    """已配置的会话源根目录（默认源 + config 里用户自定义的）"""
    roots = []
    for src in session_sources():
        p = (src or {}).get("path") if isinstance(src, dict) else None
        if not p:
            continue
        try:
            roots.append(Path(p).resolve())
        except Exception:
            continue
    return roots

def is_allowed_session_file(raw_path: str) -> bool:
    """只允许读取「已配置的会话源」里的文件。

    为什么必须收口：preview/ingest 读到的内容会**送进 LLM** ——
    等于把文件内容搬到外部模型。不限源的话，任意本机路径（config.json 里的 API Key、
    .env、别的项目源码）都能被读走 12000 字符，直接掀翻「数据不出本机」这条边界。
    """
    if not raw_path:
        return False
    try:
        target = Path(raw_path).resolve()
    except Exception:
        return False
    if not target.is_file():
        return False
    for root in _session_roots():
        try:
            if target == root or root in target.parents:
                return True
        except Exception:
            continue
    return False
