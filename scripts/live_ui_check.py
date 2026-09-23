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
rows: list[tuple[str, str, str]] = []      # (名字, P/F/S, 现场)


def check(name: str, cond, extra: str = "") -> None:
    """cond 传 bool 是断言；传字符串是**这一项没跑**（没数据可点），记为 SKIP 而不是通过。"""
    state = "S" if isinstance(cond, str) else ("P" if cond else "F")
    detail = cond if isinstance(cond, str) else extra
    rows.append((name, state, detail))
    print({"P": f"  [OK] {name}",
           "F": f"  [FAIL] {name}  {detail}",
           "S": f"  [SKIP] {name}  {detail}"}[state])


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
        # 三级降级：系统 Edge → 系统 Chrome → Playwright 自带 chromium。
        # CI 容器里前两个都不存在，只装了 `playwright install chromium`；
        # 没有第三级时这条门禁会在 CI 上直接判失败，而不是"这台机器没浏览器"地跳过。
        for channel in ("msedge", "chrome", None):
            try:
                browser = pw.chromium.launch(channel=channel, headless=True)
                print(f"（用 {'Playwright 自带 chromium' if channel is None else channel}）")
                break
            except Exception:                                  # noqa: BLE001
                browser = None
        if browser is None:
            print("没有可用的浏览器（Edge / Chrome / playwright chromium 都起不来）；"
                  "CI 上请先 `python -m playwright install --with-deps chromium`")
            return 1

        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []
        # 「代码现实」读不到时后端**故意**回 400（带可行动提示），面板也已经把原因显示出来了。
        # 把它和真正的页面故障混在一起，会让本门禁在任何"仓库路径不在这台机器上"的数据上假红。
        # 所以单独收一路，并反过来断言"它被解释了"。
        git_unreadable: list[str] = []

        def _on_response(r):
            if r.status < 400 or "favicon" in r.url:
                return
            if r.status == 400 and r.url.split("?")[0].endswith("/git"):
                git_unreadable.append(r.url)
            else:
                errors.append(f"HTTP {r.status}: {r.url}")

        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        # 只收真正的脚本错误。"Failed to load resource" 是浏览器对同一次 4xx/5xx 的重复播报，
        # 且**不带 URL** —— 上面的 response 已经按 URL 记了更精确的一份，这里收两遍只会让人
        # 去查同一条线索两次。
        page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("response", _on_response)

        t0 = time.time()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_selector("[data-act='openProjectModal']", timeout=20000)
        # 「不是白页」要量的是屏幕上真有东西，以前是 `check(..., True)` —— 恒绿
        shown = page.locator("body").inner_text().strip()
        dash = page.locator("#dash").inner_text().strip()
        check("页面加载完成（仪表盘真渲染出内容，不是白页）",
              len(dash) > 10 and len(shown) > 200,
              f"{int((time.time() - t0) * 1000)}ms / 仪表盘 {len(dash)} 字 / 整页 {len(shown)} 字")

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
            # 以前这句是 check(..., True)：进去的若是另一个项目，它照样绿
            check("点卡片进的是刚点的那个项目的详情页",
                  names[0] in page.locator("#view-detail").inner_text(),
                  f"期望 {names[0]}")
            cols = [".kcol.k-todo", ".kcol.k-doing", ".kcol.k-done", ".kcol.k-blocked"]
            check("看板四列都在", all(page.locator(c).count() == 1 for c in cols))
            check("详情页有内容（不是空壳）",
                  len(page.locator("#view-detail").inner_text()) > 50)

            # 「看板说的 vs 代码里真发生的」是详情页的核心一块：它一坏，
            # 界面就退回"只能看自报进度"，而这正是这个产品要解决的问题。
            page.wait_for_function(
                "() => (document.getElementById('reality-body')?.innerText || '').length > 10",
                timeout=10000)
            reality = page.locator("#reality-body").inner_text()
            check("看板/代码现实两侧都渲染了",
                  "看板自报" in reality and "代码现实" in reality, reality.replace("\n", " ")[:90])
            # 结论要等 git 回来才算完，所以等的是"已经不是占位那句"，
            # 而不是"存在一行 verdict"——占位那一行本身就会让它平凡通过
            page.wait_for_function(
                "() => { const v = document.querySelectorAll('#reality-verdicts .verdict');"
                "  return v.length >= 1 && !/正在核对/.test(v[0].innerText); }", timeout=15000)
            check("对照给了一句结论（有分歧要说、没分歧也要说）",
                  page.locator("#reality-verdicts .verdict").count() >= 1,
                  page.locator("#reality-verdicts").inner_text().replace("\n", " ")[:90])

            # 依赖图：真实板子上可能确实没有依赖，所以断言的是"接口口径自洽"，
            # 而不是硬要求画出一张图来（后者会让门禁在多数板子上假红）
            g = api("/api/projects/" + projects[0]["id"] + "/graph") if names else None
            if g is not None:
                ok_shape = (g.get("empty") and g.get("reason")) or (
                    not g.get("empty") and g.get("svg", "").lstrip().startswith("<svg"))
                check("依赖图接口口径自洽（空要有原因，非空要是 SVG）", bool(ok_shape),
                      f"empty={g.get('empty')} reason={g.get('reason')} nodes={g.get('nodes')}")
                shown = page.locator("#graph-panel").is_visible()
                check("界面与接口对'有没有图'的判断一致", shown == (not g.get("empty")),
                      f"面板可见={shown} 接口 empty={g.get('empty')}")
            if git_unreadable:
                # 这个 400 是**预期内**的（路径不在这台机器上），但预期内不等于可以不解释：
                # 面板必须说清"读不到"，不能留白、也不能永远停在"读取中…"
                code_txt = page.locator("#reality-code").inner_text()
                verdict_txt = page.locator("#reality-verdicts").inner_text()
                check("仓库读不到时面板说了原因（不是空白、不是卡在读取中）",
                      ("读不到" in code_txt or "不存在" in code_txt)
                      and "读取中" not in code_txt and "正在核对" not in verdict_txt,
                      (code_txt + " || " + verdict_txt).replace("\n", " ")[:110])

            page.click("[data-act='showOverview']")
            page.wait_for_selector(".pcard", timeout=10000)
            check("返回总览正常", page.locator(".pcard").count() == len(names))

        page.click("[data-act='openGlobalSearch']")
        page.wait_for_selector("#gs-input", timeout=10000)
        # 这个框搜的是**任务**（标题/描述/负责人/引用），不搜项目名 —— 拿项目名去断言
        # 会恒红（2026-09-23 就是这么红的），拿一个不存在的词去搜又恒绿，
        # 所以关键词必须从接口里取一条真实任务标题的前几个字。
        probe = ""
        for pr in projects:
            det = api("/api/projects/" + pr["id"])
            if det.get("tasks"):
                probe = (det["tasks"][0].get("title") or "")[:4]
                if probe:
                    break
        page.fill("#gs-input", probe or "任务")
        page.wait_for_timeout(700)
        items = page.locator(".gs-item").count()
        # 有真实任务标题可搜却不返回任何一条，就是搜索链路坏了；没数据时不能拿"空"当通过
        check("全局搜索用真实任务标题能搜到东西",
              (items >= 1 if probe else "这块板上没有任务可搜"),
              f"命中 {items} 条 / 关键词「{probe}」")

        check("全程无 JS 报错 / 请求失败", not errors, " | ".join(errors[:3]))

        ctx.close()
        browser.close()

    failed = [r for r in rows if r[1] == "F"]
    passed = [r for r in rows if r[1] == "P"]
    skipped = [r for r in rows if r[1] == "S"]
    print(f"\n结果：{len(passed)} 通过 / {len(failed)} 失败"
          + (f" / {len(skipped)} 未验（{'、'.join(r[0] for r in skipped)}）" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
