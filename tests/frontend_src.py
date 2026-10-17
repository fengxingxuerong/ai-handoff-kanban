# -*- coding: utf-8 -*-
"""给静态契约测试用的「前端源码」读取器。

为什么需要它：`web/index.html` 在 2026-09-28 拆成了
`index.html` + `style.css` + 若干 `js-*.js`（2026-10-18 起按关注点分成 10 份）。
此前所有静态契约测试都直接 `read_text("web/index.html")` —— 拆完之后
JS 与 CSS 都不在那个文件里了，**"内联事件为零" / "每个 data-act 都有实现" /
"主题色走变量"** 这些断言会静默变成"没找到"（要么假绿、要么假红）。

这里的做法是把前端**按真实加载顺序拼回一整份**再交给正则 ——
契约测的是"整个前端"，从来不是"某一个文件"。文件名怎么变都不影响这里，
所以**静态契约不要写死 `js-xxx.js`**（写了就等于把测试焊在文件布局上）。
"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def _ordered_assets() -> list[Path]:
    """按 index.html 里真实的引用顺序返回所有前端文件（CSS 先、JS 按 script 顺序）。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    out: list[Path] = [WEB / "index.html"]
    # 只取样式表：第一条 <link> 是 data: URI 的 favicon，不是文件。
    # 引用带 `/web/` 前缀（服务端把 web 目录挂在 /web 下），这里只取文件名。
    for href in re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', html):
        if not href.startswith(("data:", "http")):
            out.append(WEB / href.rsplit("/", 1)[-1])
    for src in re.findall(r'<script[^>]+src="([^"]+)"', html):
        if not src.startswith(("data:", "http")):
            out.append(WEB / src.rsplit("/", 1)[-1])
    missing = [str(p) for p in out if not p.exists()]
    if missing:
        raise AssertionError(f"index.html 引用了不存在的前端文件：{missing}")
    return out


def frontend_source() -> str:
    """整个前端的源码（index.html + CSS + JS），顺序与浏览器加载顺序一致。"""
    return "\n".join(p.read_text(encoding="utf-8") for p in _ordered_assets())


def web_dir() -> Path:
    return WEB
