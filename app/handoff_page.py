# -*- coding: utf-8 -*-
"""自包含「接手页」：一个 HTML 文件装下接手所需的全部上下文。

设计取舍（为什么是文件而不是链接）：
- 链接要出本机，而看板的安全边界就是「数据留在 %USERPROFILE%\\.ai-kanban」——公开 URL 直接推翻前提
- 链接是快照还会过期；文件同样离线可用，且人和智能体都能读（本地文件 100% 可达）
- 依赖图用纯 SVG 内联，不引 mermaid CDN、不把任务标题发给第三方渲染服务

本模块刻意不 import app.main：main 会 import 本模块，反过来会成环。
需要的拓扑信息在这里就地算，只依赖传入的 dict。
"""
from __future__ import annotations

import html

# 依赖图布局
COL_W, ROW_H, PAD = 230, 58, 16
NODE_W, NODE_H = 200, 42
MAX_COLS = 4          # 再深的层级并到最后一列，图不无限右伸
MAX_NODES = 30        # 超出只画前 30 个，页面上注明

STATUS_COLOR = {
    "待办": "#5F5E5A",
    "进行中": "#378ADD",
    "已完成": "#639922",
    "阻塞": "#E24B4A",
}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _layers(tasks: list[dict]) -> dict[str, int]:
    """每个任务的层级：无依赖=0，否则=max(前置层级)+1。有环时按 0 处理（写入时已拦环）"""
    by_id = {t.get("id"): t for t in tasks}
    depth: dict[str, int] = {}

    def d(tid: str, seen: set) -> int:
        if tid in depth:
            return depth[tid]
        if tid in seen:
            return 0
        deps = [x for x in (by_id.get(tid, {}).get("depends_on") or []) if x in by_id]
        v = 0 if not deps else 1 + max(d(x, seen | {tid}) for x in deps)
        depth[tid] = v
        return v

    for t in tasks:
        d(t.get("id"), set())
    return depth


def dep_graph_svg(tasks: list[dict]) -> str:
    """任务依赖图（分层 DAG，纯 SVG）。没有任务或没有依赖时返回空串，由调用方决定是否展示"""
    if not tasks:
        return ""
    has_dep = any((t.get("depends_on") or []) for t in tasks)
    if not has_dep:
        return ""

    shown = tasks[:MAX_NODES]
    by_id = {t.get("id"): t for t in shown}
    depth = _layers(shown)

    buckets: dict[int, list[dict]] = {}
    for t in shown:
        buckets.setdefault(min(depth.get(t.get("id"), 0), MAX_COLS - 1), []).append(t)

    pos: dict[str, tuple[int, int]] = {}
    for layer, items in buckets.items():
        for i, t in enumerate(items):
            pos[t.get("id")] = (PAD + layer * COL_W, PAD + i * ROW_H)

    cols = max(buckets) + 1 if buckets else 1
    rows = max(len(v) for v in buckets.values()) if buckets else 1
    w = PAD + cols * COL_W
    h = PAD + rows * ROW_H

    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           f'aria-label="任务依赖图" xmlns="http://www.w3.org/2000/svg">',
           '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
           'markerHeight="6" orient="auto-start-reverse">'
           '<path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" '
           'stroke-linecap="round"/></marker></defs>']

    for t in shown:
        tid = t.get("id")
        deps = [x for x in (t.get("depends_on") or []) if x in by_id and x in pos]
        for dep in deps:
            sx, sy = pos[dep][0] + NODE_W, pos[dep][1] + NODE_H / 2
            ex, ey = pos[tid][0], pos[tid][1] + NODE_H / 2
            out.append(f'<path d="M{sx} {sy} C{sx + 30} {sy} {ex - 30} {ey} {ex} {ey}" '
                       f'fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#a)"/>')

    for t in shown:
        x, y = pos[t.get("id")]
        color = STATUS_COLOR.get(t.get("status", "待办"), "#5F5E5A")
        title = (t.get("title") or "")[:16]
        out.append(f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="8" '
                   f'fill="#1E1E22" stroke="{color}" stroke-width="1.5"/>')
        out.append(f'<text x="{x + 12}" y="{y + 16}" fill="#E8E6E1" font-size="12" '
                   f'font-family="system-ui,sans-serif">{esc(title)}</text>')
        out.append(f'<text x="{x + 12}" y="{y + 32}" fill="{color}" font-size="11" '
                   f'font-family="system-ui,sans-serif">{esc(t.get("status", "待办"))}'
                   f' · {esc(t.get("priority", "中"))}</text>')
    out.append("</svg>")
    return "\n".join(out)


_CSS = """
:root{color-scheme:dark}
body{margin:0;padding:32px;background:#141417;color:#E8E6E1;
 font:14px/1.7 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:22px;font-weight:500;margin:0 0 6px}
h2{font-size:16px;font-weight:500;margin:28px 0 10px;padding-bottom:6px;border-bottom:1px solid #2C2C32}
.sub{color:#9A9A92;font-size:13px}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:16px 0}
.meta div{background:#1E1E22;border-radius:8px;padding:8px 12px}
.meta span{display:block;color:#9A9A92;font-size:12px}
pre{background:#1E1E22;border-radius:8px;padding:12px;overflow:auto;white-space:pre-wrap}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #2C2C32;padding:6px 10px;text-align:left;font-size:13px}
th{background:#1E1E22}
code{background:#1E1E22;padding:1px 5px;border-radius:4px}
a{color:#85B7EB}
nav{background:#1E1E22;border-radius:8px;padding:10px 14px;margin:18px 0}
nav a{margin-right:14px;font-size:13px}
button{background:#2C2C32;color:#E8E6E1;border:1px solid #3A3A42;border-radius:8px;
 padding:8px 14px;font-size:13px;cursor:pointer;margin-right:8px}
button:hover{border-color:#85B7EB}
.hint{color:#9A9A92;font-size:12px;margin-top:8px}
footer{margin-top:32px;padding-top:12px;border-top:1px solid #2C2C32;color:#9A9A92;font-size:12px}
"""


def build_handoff_page(p: dict, md: str = "", *, git_block: str = "",
                       port: str = "18320", generated_at: str = "") -> str:
    """生成单文件接手页 HTML。

    p           项目 dict
    md          已生成的 Markdown 交接包（用于"复制全文"，可为空）
    git_block   代码现状文本（由调用方采集，读不到就留空）
    port        本机服务端口，用于打印实时直链
    """
    pid = p.get("id") or ""
    tasks = p.get("tasks") or []
    by_id = {t.get("id"): t for t in tasks}

    def esc_md_block(text: str) -> str:
        return esc(text)

    rows = []
    for t in tasks:
        deps = [d for d in (t.get("depends_on") or []) if d in by_id]
        dep_txt = "、".join(esc(by_id[d].get("title") or d) for d in deps) if deps else "—"
        refs = "、".join(f"<code>{esc(r)}</code>" for r in (t.get("refs") or []))
        rows.append(
            f"<tr><td>{esc(t.get('title'))}</td><td>{esc(t.get('status', '待办'))}</td>"
            f"<td>{esc(t.get('priority', '中'))}</td><td>{esc(t.get('agent') or '—')}</td>"
            f"<td>{dep_txt}</td><td>{esc(t.get('desc') or '—')}</td>"
            f"<td>{refs or '—'}</td></tr>")

    svg = dep_graph_svg(tasks)
    graph_html = ""
    if svg:
        graph_html = f"<h2 id='graph'>任务依赖</h2>{svg}"
        if len(tasks) > MAX_NODES:
            graph_html += f"<p class='hint'>任务较多，图里只画前 {MAX_NODES} 个；完整清单见下表。</p>"
    elif tasks:
        graph_html = "<h2 id='graph'>任务依赖</h2><p class='hint'>当前任务之间没有依赖关系，照表做即可。</p>"

    sections = []
    if p.get("background"):
        sections.append(f"<h2 id='bg'>项目背景</h2><pre>{esc(p['background'])}</pre>")
    sections.append(
        "<h2 id='handoff'>交接说明（务必先读）</h2>"
        f"<pre>{esc(p.get('handoff_note') or '（未填写 —— 这是接手时最该补的一项）')}</pre>")
    sections.append(
        "<h2 id='next'>下一步行动</h2>"
        f"<pre>{esc(p.get('next_action') or '（未填写）')}</pre>")
    if git_block:
        sections.append(f"<h2 id='git'>代码现状（git）</h2><pre>{esc(git_block)}</pre>")
    if rows:
        sections.append(
            "<h2 id='tasks'>任务清单</h2><table><tr><th>任务</th><th>状态</th><th>优先级</th>"
            "<th>负责</th><th>前置</th><th>描述</th><th>代码位置</th></tr>"
            + "".join(rows) + "</table>")

    # 复制用的 Markdown 放 <template> 里：
    # 它的内容会被解析成 DOM，读取 .content.textContent 拿到的是原始文本；
    # 不像 <script> 那样是 raw text 区（放进 script 就得手工防 "</script>" 提前闭合）
    md_block = esc(md or "")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>接手页 · {esc(p.get('name'))}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>{esc(p.get('name'))}</h1>
<div class="sub">{esc(p.get('status', ''))} · 进度 {int(p.get('progress') or 0)}%
 · 优先级 {esc(p.get('priority', '中'))}
 · 最近更新 {esc(p.get('updated_at') or '—')}</div>
<div class="meta">
<div><span>代码路径</span>{esc(p.get('repo_path') or '未指定')}</div>
<div><span>标签</span>{esc('、'.join(p.get('tags') or []) or '无')}</div>
<div><span>生成时间</span>{esc(generated_at or '—')}</div>
<div><span>交接就绪度</span>{_readiness_line(p)}</div>
</div>
<nav>
<a href="#handoff">交接说明</a><a href="#next">下一步</a><a href="#tasks">任务清单</a>
<a href="#graph">依赖图</a><a href="#raw">Markdown 全文</a><a href="#live">实时直链</a>
</nav>
{''.join(sections)}
{graph_html}
<h2 id="raw">Markdown 全文</h2>
<button onclick="cp()">复制全文（粘给智能体）</button>
<span class="hint">复制的是标准 Markdown 交接包，任何 AI 客户端都能直接读。</span>
<details><summary style="margin-top:10px;cursor:pointer">展开查看</summary>
<pre id="rawmd">{esc_md_block(md)}</pre></details>
<h2 id="live">实时直链（活数据，不是快照）</h2>
<pre>结构化接手包 {esc('http://127.0.0.1:' + str(port) + '/api/context/' + pid)}
Markdown 交接包 {esc('http://127.0.0.1:' + str(port) + '/api/export/' + pid)}
自包含接手页   {esc('http://127.0.0.1:' + str(port) + '/api/export/page/' + pid)}</pre>
<p class="hint">仅限本机（127.0.0.1），服务没开时取不到 —— 这时用本页内容即可。</p>
<footer>由 AI 交接看板生成 · 单文件离线可用 · 数据未离开本机</footer>
</div>
<template id="kanban-md">{md_block}</template>
<script>
function cp() {{
  var md = document.getElementById('kanban-md').content.textContent;
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(md).then(function(){{ alert('已复制，可直接粘给智能体'); }});
  }} else {{
    var ta = document.createElement('textarea');
    ta.value = md; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    alert('已复制，可直接粘给智能体');
  }}
}}
</script>
</body></html>"""


def _readiness_line(p: dict) -> str:
    """就绪度不 import main（会成环），这里按同一套五项口径就地算"""
    items = [
        ("交接说明", bool((p.get("handoff_note") or "").strip()), 35),
        ("下一步行动", bool((p.get("next_action") or "").strip()), 25),
        ("项目背景", bool((p.get("background") or "").strip()), 15),
        ("任务清单", bool(p.get("tasks")), 15),
        ("任务有推进", any(t.get("status") in ("进行中", "已完成") for t in (p.get("tasks") or [])), 10),
    ]
    score = sum(w for _, ok, w in items if ok)
    missing = [n for n, ok, _ in items if not ok]
    tail = "（缺：" + "、".join(missing) + "）" if missing else "（齐备）"
    return f"{score}/100 {tail}"
