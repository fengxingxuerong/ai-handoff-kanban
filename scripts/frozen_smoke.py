# -*- coding: utf-8 -*-
"""
冻结版自查：启动 dist\\AI交接看板.exe，用真实 HTTP 打一遍关键接口，最后自动关掉。

为什么需要它：PyInstaller -w 打包后没有控制台，后端如果启动失败（例如
标准流为 None 导致 uvicorn 日志初始化报错），现象只是"双击没反应/窗口空白"，
光看源码完全看不出来。这个脚本能在 30 秒内给出结论。

用法：
    python scripts/frozen_smoke.py                    # 默认 dist\\AI交接看板.exe
    python scripts/frozen_smoke.py 路径\\到\\x.exe 18395
    python scripts/frozen_smoke.py --sweep [x.exe]    # 只清同路径残留进程（ci_local 打包前调）
退出码 0 表示通过。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 输出里有 ✓ 和中文：重定向到文件时 Python 用系统 ANSI 码页（本机 cp936），
# 打第一个 ✓ 就 UnicodeEncodeError 崩掉 —— 门禁脚本必须能被重定向与管道。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001  被替换掉的 stdout 可能没有 reconfigure
    pass

EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "AI交接看板.exe"
PORT = sys.argv[2] if len(sys.argv) > 2 else "18395"
BASE = f"http://127.0.0.1:{PORT}"
TIMEOUT = 45

# 显式不走代理：本机 Clash 之类的代理会把 127.0.0.1 的请求也拦掉，造成"服务超时"的假象
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

passed, failed = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (passed if cond else failed).append(name)
    print(("  [OK] " if cond else "  [FAIL] ") + name + ("" if cond else "  " + extra))


def _parse_pid_lines(text: str) -> list[int]:
    """PowerShell 输出里只信纯数字行：空行/告警/本地化文案一律忽略。"""
    return [int(ln.strip()) for ln in text.splitlines() if ln.strip().isdigit()]


def _ps_pids(exe: Path) -> list[int]:
    """按**可执行文件路径**精确找残留进程的 PID（读不到路径的进程直接不算）。

    为什么过滤条件是路径而不是映像名：用户可能正开着装在别处的副本（安装版、
    桌面拷贝），按名字 `taskkill /IM` 等于杀掉他正在用的应用 —— 那是真正的破坏。
    而 PyInstaller 打包要求没有任何进程握着这个 exe 文件，所以"同一路径的实例"
    恰好是唯一**可以杀也必须杀**的对象：杀得少算失败（打包会 WinError 5 红出来），
    杀错了才是事故。与 `app/security.py` 落盘黑名单同一 asymmetry：宁可少清，不可错杀。
    """
    if os.name != "nt":
        return []
    ps = ("Get-Process -Name '{name}' -ErrorAction SilentlyContinue | "
          "Where-Object {{ $_.Path -and $_.Path -ieq '{path}' }} | "
          "ForEach-Object {{ $_.Id }}").format(
        name=exe.stem.replace("'", "''"), path=str(exe).replace("'", "''"))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):                # noqa: BLE001
        return []
    return _parse_pid_lines(r.stdout or "")


def sweep_stale(exe: Path) -> list[int]:
    """清掉握着这个 exe 的同路径残留进程，返回清完后仍存活的 PID。

    双层用法：`ci_local` 打包前调一次（残渣会让 PyInstaller WinError 5，2026-10-24
    实测）；本脚本自己的收尾也兜一轮 —— taskkill /T 可能漏掉竞态期刚拉起的孩子，
    之前两轮连续警告"端口仍在监听"就是这么漏的。
    """
    pids = _ps_pids(exe)
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    if pids:
        time.sleep(1.0)                     # 等 socket/文件句柄释放，再复查才算数
    return _ps_pids(exe)


def main() -> int:
    if not EXE.exists():
        print(f"找不到 {EXE}，先运行 build.bat")
        return 1

    # 先清场再开跑：上一轮如果没清干净，残留实例会占着端口/数据目录，
    # 本轮的所有检查就会打到那个旧进程上 —— 结论全是它的。清不掉就停，
    # 别带着一个已知污染源继续跑（结论作废比结论错误好修得多）。
    stale = sweep_stale(EXE)
    if stale:
        print(f"警告：同路径实例残留且清不掉（PID {stale}），为避免检查打到旧进程，本轮不跑")
        return 1

    data_dir = Path(tempfile.gettempdir()) / "kanban_frozen_smoke"
    if data_dir.exists():
        for f in data_dir.iterdir():
            f.unlink()
    data_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["KANBAN_PORT"] = PORT
    env["KANBAN_DATA_DIR"] = str(data_dir)

    print(f"启动 {EXE.name}（端口 {PORT}，数据目录 {data_dir}）")
    proc = subprocess.Popen([str(EXE)], env=env)

    try:
        # 1. 等服务就绪（真实 HTTP，不只是端口通）
        deadline = time.time() + TIMEOUT
        health = None
        last = ""
        while time.time() < deadline:
            try:
                with opener.open(BASE + "/api/health", timeout=3) as r:
                    health = json.loads(r.read().decode("utf-8"))
                    break
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(1.0)
        # 「就绪」要同时满足两件事：**它答了**，而且**它报的数据目录是我们这一个**。
        # 只问"答没答"的话，端口上站着别人（残留实例，或你手动起的调试实例）时会拿它的回包
        # 当本轮结论 —— 后面那几条「版本同源 / 内嵌前端一致」就会报出与真原因无关的失败。
        # 同型事故 2026-10-25 在 release_regression 上真发生过（报的是「只读模式下写操作被拒」，
        # 真原因是端口上是个残留实例）。这也正是硬性约定「起实例后必须问它自己读的是哪份数据」。
        ours = (health is not None
                and Path(str(health.get("data_file") or "")).parent == data_dir)
        why = last if health is None else \
            f"端口 {PORT} 上那个实例报的 data_file={health.get('data_file')}，本轮是 {data_dir}"
        check("后端 HTTP 就绪且是本轮实例（数据目录自证）", ours, why)
        if not ours:
            return report(data_dir)

        # 1b. 版本戳这条链必须真的一致：exe 自报的版本与构建时间要和仓库里那两份对得上。
        # 以前这八项里没有一条看 build_time，"exe 是哪次构建的"只能靠人比 mtime ——
        # 2026-09-20 就漂过一次（版本资源写的是源码 mtime，戳却是另一个时间点）。
        def _read(p: Path) -> str:
            try:
                return p.read_text(encoding="utf-8")
            except Exception:                                      # noqa: BLE001
                return ""
        stamp = re.search(r'STAMP = "([^"]*)"', _read(ROOT / "app" / "_build_stamp.py"))
        ver = re.search(r'^__version__ = "([^"]*)"', _read(ROOT / "app" / "version.py"), re.M)
        check("exe 自报版本与构建戳同源",
              bool(stamp) and bool(ver)
              and health.get("build_time") == stamp.group(1)
              and health.get("version") == ver.group(1),
              f"exe={health.get('version')}/{health.get('build_time')} "
              f"仓库={ver and ver.group(1)}/{stamp and stamp.group(1)}")

        # 构建戳之后如果 `app/` 或 `web/` 里任何一个源文件又被改过，这个 exe 就是旧的。
        # 只比前端字节（上面那条）拦不住后端改动 —— PyInstaller 打的是 .pyc，
        # 没法从产物里把源码比回来，所以这里用时间戳兜一层：宁可拦住要求重打包，
        # 也不要"测试全绿、发出去的 exe 少几个修复"。
        built_at = stamp.group(1) if stamp else ""
        try:
            built_ts = datetime.strptime(built_at, "%Y-%m-%d %H:%M").timestamp() if built_at else 0.0
        except ValueError:
            built_ts = 0.0
        newer = []
        if built_ts:
            # web/ 下现在是多个文件（index.html + style.css + js-*.js）：
            # 只看 index.html 会漏掉"改了某个 js 却没重打包"（2026-09-28 拆分后的新盲区）。
            for f in list((ROOT / "app").glob("*.py")) + \
                    [p for p in (ROOT / "web").rglob("*") if p.is_file()]:
                try:
                    if f.stat().st_mtime > built_ts + 60:      # 一分钟容差：打包本身要时间
                        newer.append(f.name)
                except OSError:
                    continue
        check("产物不比源码旧（改了后端也得重打包）", not newer,
              f"构建戳 {built_at} 之后又改过：{'、'.join(newer[:6])}")

        with opener.open(BASE + "/", timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
        check("首页可访问且含看板标记", "AI 交接看板" in html)
        # 产物里嵌的前端必须就是仓库里这一份：改完前端不重打包，测试全绿而用户看到的
        # 还是旧界面 —— 这条比对是"重打 exe"这件事本身的门禁（不一致就得重新打包）。
        src_html = ROOT / "web" / "index.html"
        want = hashlib.sha256(src_html.read_bytes()).hexdigest() if src_html.exists() else ""
        got = hashlib.sha256(html.encode("utf-8")).hexdigest()
        check("exe 内嵌的前端就是仓库里这一份（不一致说明该重打包）",
              bool(want) and got == want,
              f"sha256 产物={got[:12]} 仓库={want[:12]}")
        # ACTIONS 表现在在 web/js-core.js 里（2026-09-28 拆出），首页只留有引用标签
        check("内嵌前端为事件委托版本",
              'data-act="' in html and 'src="web/js-core.js"' in html)
        check("内嵌前端无内联事件", "onclick=" not in html and "onkeydown=" not in html)

        # 3. 写读删闭环
        body = json.dumps({"name": "冻结版冒烟", "status": "进行中", "progress": 20, "priority": "高"},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(BASE + "/api/projects", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=10) as r:
            created = json.loads(r.read().decode("utf-8"))
        check("创建项目", created.get("name") == "冻结版冒烟")

        with opener.open(BASE + "/api/projects", timeout=10) as r:
            total = len(json.loads(r.read().decode("utf-8"))["projects"])
        check("项目列表可读", total >= 1, f"total={total}")

        dr = urllib.request.Request(BASE + f"/api/projects/{created['id']}", method="DELETE")
        with opener.open(dr, timeout=10) as r:
            check("删除项目", json.loads(r.read().decode("utf-8")).get("ok") is True)

        # 4. 安全防护在冻结版里同样生效
        try:
            rq = urllib.request.Request(BASE + "/api/health", headers={"Origin": "http://evil.example.com"})
            opener.open(rq, timeout=5)
            check("跨站 Origin 被拒绝", False, "竟然放行了")
        except urllib.error.HTTPError as e:
            check("跨站 Origin 被拒绝", e.code == 403, f"code={e.code}")

        return report(data_dir)
    finally:
        # PyInstaller -F 单文件模式：主进程会拉起子进程跑实际服务，
        # terminate 只能杀父进程，子进程会残留（占用端口与 exe 文件）。
        # 必须用 taskkill /T 杀整棵进程树；杀完 socket 释放有延迟，需等待后复查。
        import socket
        import time as _time

        def port_listening(port: int) -> bool:
            s = socket.socket()
            s.settimeout(0.5)
            r = s.connect_ex(("127.0.0.1", port)) == 0
            s.close()
            return r

        try:
            port = int(BASE.rsplit(":", 1)[1])
        except Exception:
            port = 18395
        for _ in range(3):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            _time.sleep(1.2)  # 等 socket 释放
            if not port_listening(port):
                break
        # 兜底：树杀可能漏掉竞态期刚拉起的孩子（2026-10-24 实测连续两轮警告
        # "端口仍在监听"，残留进程把下一轮打包毒成 WinError 5）。按可执行路径
        # 再扫一轮同 exe 残留，然后才下"清干净了"的结论。
        remain = sweep_stale(EXE)
        if port_listening(port) or remain:
            print(f"警告：端口仍在监听/进程未清干净（残留 PID {remain}），"
                  f"会毒害下一次打包 —— 手动 taskkill /F /T /PID 后重跑")
        else:
            print("已关闭应用")


def report(data_dir: Path) -> int:
    print(f"\n结果：{len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + " | ".join(failed))
        log = data_dir / "startup.log"
        if log.exists():
            print("\n--- startup.log ---")
            print(log.read_text(encoding="utf-8", errors="replace"))
    return 1 if failed else 0


if __name__ == "__main__":
    # --sweep：只清这个 exe 的同路径残留，不起服务。ci_local 打包前调它；
    # 有残留清不掉时 exit 1（打包反正会 WinError 5，早说早处理）。
    if len(sys.argv) > 1 and sys.argv[1] == "--sweep":
        target = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "dist" / "AI交接看板.exe"
        hit = len(_ps_pids(target))
        remain = sweep_stale(target)
        if hit:
            print(f"清掉 {hit - len(remain)} 个残留进程"
                  + (f"，仍有 {len(remain)} 个清不掉（PID {remain}）" if remain else ""))
            raise SystemExit(1 if remain else 0)
        raise SystemExit(0)
    raise SystemExit(main())
