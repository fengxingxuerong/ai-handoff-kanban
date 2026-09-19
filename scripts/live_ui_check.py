# -*- coding: utf-8 -*-
"""对着「已经在跑的实例」做真浏览器只读校验（Playwright + 系统 Edge/Chrome）。

和 live_check.py 的分工：
- live_check.py 打接口，证明**后端**活着；
- 本脚本开真浏览器，证明**界面真的渲染出来了** —— 光接口通、页面白屏是最难发现的一种故障
  （曾经发生过：后端被回收，预览面板连不上，看到的就是白页）。

**全程只读**：只看、只点导航类操作（进详情、开搜索、翻页），不点任何保存/删除/执行按钮，
不往数据目录写一个字节。所以可以放心对着真实数据跑。

用法：
    python scripts/live_ui_check.py                          # 默认 http://127.0.0.1:18320
    python scripts/live_ui_check.py http://127.0.0.1:18395
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 输出里有 ✓ 和中文：重定向到文件时 Python 用系统 ANSI 码页（本机 cp936），
# 打第一个 ✓ 就 UnicodeEncodeError 崩掉 —— 门禁脚本必须能被重定向与管道。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001  被替换掉的 stdout 可能没有 reconfigure
    pass


BASE = "http://127.0.0.1:18320"
for arg in sys.argv[1:]:
    if arg.startswith("http"):
        BASE = arg.rstrip("/")

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
rows: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    rows.append((name, cond, extra))
    print(("  [OK] " if cond else "  [FAIL] ") + name + ("" if cond else "  " + extra))


def api(path: str):
    with opener.open(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要 playwright：.venv\\Scripts\\python.exe -m pip install playwright")
        return 1

    print(f"浏览器只读校验：{BASE}\n")
    projects = api("/api/projects")["projects"]
    names = [p["name"] for p in projects]

    with sync_playwright() as pw:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = pw.chromium.launch(channel=channel, headless=True)
                print(f"（用系统 {channel}）")
                break
            except Exception:                                  # noqa: BLE001
                browser = None
        if browser is None:
            print("没有可用的 Edge/Chrome，无法做界面校验")
            return 1

        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                if m.type == "error" and "favicon" not in m.text else None)
        page.on("response", lambda r: errors.append(f"HTTP {r.status}: {r.url}")
                if r.status >= 400 and "favicon" not in r.url else None)

        t0 = time.time()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_selector("[data-act='openProjectModal']", timeout=20000)
        check("页面加载完成（不是白页）", True, f"{int((time.time() - t0) * 1000)}ms")

        page.wait_for_selector(".pcard", timeout=20000) if names else None
        cards = page.locator(".pcard")
        check(f"卡片渲染数量与接口一致（{cards.count()} / {len(names)}）",
              cards.count() == len(names), f"DOM={cards.count()} API={len(names)}")

        body = page.locator("body").inner_text()
        missing = [n for n in names if n not in body]
        check("每个真实项目名都出现在界面上", not missing, f"缺：{missing}")

        stats = api("/api/stats")
        check("仪表盘数字已渲染（项目数/任务数）",
              str(stats["project_total"]) in body and str(stats["total_tasks"]) in body,
              f"stats={stats['project_total']}/{stats['total_tasks']}")

        # 版本号在设置弹窗里（隐藏元素）：inner_text() 只返回"渲染出来"的文本，
        # 隐藏元素要用 text_content()，并且它是异步载入的，得等它写完
        page.wait_for_function(
            "() => (document.getElementById('app-version')?.textContent || '').includes('v')",
            timeout=10000)
        ver_text = page.locator("#app-version").text_content() or ""
        check("版本号已显示", f"v{api('/api/health')['version']}" in ver_text, ver_text.strip())

        if names:
            cards.first.click()
            page.wait_for_selector("#view-detail.active", timeout=10000)
            check("点卡片能进详情页", True)
            cols = [".kcol.k-todo", ".kcol.k-doing", ".kcol.k-done", ".kcol.k-blocked"]
            check("看板四列都在", all(page.locator(c).count() == 1 for c in cols))
            check("详情页有内容（不是空壳）",
                  len(page.locator("#view-detail").inner_text()) > 50)

            page.click("[data-act='showOverview']")
            page.wait_for_selector(".pcard", timeout=10000)
            check("返回总览正常", page.locator(".pcard").count() == len(names))

        page.click("[data-act='openGlobalSearch']")
        page.wait_for_selector("#gs-input", timeout=10000)
        page.fill("#gs-input", names[0][:4] if names else "任务")
        page.wait_for_timeout(700)
        check("全局搜索能打开并返回结果", True,
              f"结果 {page.locator('.gs-item, .gs-empty').count()} 条")

        check("全程无 JS 报错 / 请求失败", not errors, " | ".join(errors[:3]))

        ctx.close()
        browser.close()

    failed = [r for r in rows if not r[1]]
    print(f"\n结果：{len(rows) - len(failed)} 通过 / {len(failed)} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
