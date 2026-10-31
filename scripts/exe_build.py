# -*- coding: utf-8 -*-
"""打包 exe 时与「正握着产物文件的外部进程」打交道的两件事（纯函数 + 文件操作，不解包）。

**它存在的理由（2026-10-31 实测的真事故）**：`dist\\AI交接看板-MCP.exe` 被**用户自己的
AI 代理宿主**当成 MCP 服务在跑（DSH 的 `cordis.patch.yml` 里 `command:` 就写着这个路径）。
于是「打包前清场」把那个进程杀掉 → 宿主发现服务掉了 → **一秒内按同一个路径重新拉起** →
文件又握着 → PyInstaller 写 `dist\\AI交接看板-MCP.exe` 报
`PermissionError: [WinError 5] 拒绝访问`。清场脚本自己报的是「仍有 2 个清不掉」，
但打包那一步照样红 —— **杀不掉的不是残留，是有人在正常使用它**。

两条对策，都在这里：

1. `move_aside_if_locked()`：Windows 允许**改名**一个正在运行的 exe（实测），只是不许覆盖它。
   所以占着就改名挪走，让打包把新产物写到契约路径（DSH 配置指的就是这个名字），
   旧进程继续从改名后的文件上服务到自己退出为止。
2. `build_exe_step()`：把这套接进一条**带一次重试**的打包步骤。重试不能只是重跑同一条命令 ——
   清场→重启是有竞态的（实测就撞上了），所以重试前再清一次、再挪一次；
   第一次失败与第二次失败的报错要**不一样**，否则「重试过了也没用」会被读成「从没重试」。

为什么**不改 DSH 配置指向别处**：exe 的契约路径就是 `dist\\AI交接看板-MCP.exe`
（README、`mcp_frozen_smoke` 的默认值、用户配置都指着它）。让打包去适应一个消费者的配置，
等于把「产物在哪」变成环境相关的 —— 那是把问题挪走，不是修掉。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 挪走的旧产物放这里：不留在 dist/ 根下，免得被「dist 里有什么」的检查与 PyInstaller 自己扫到
STALE_SUBDIR = ".stale"


def sweep_stale_binaries(exe: Path, runner=subprocess.run) -> list[int]:
    """按可执行路径清同 exe 残留（复用 `frozen_smoke.sweep_stale`），返回清不掉的 PID。

    为什么借 `frozen_smoke` 那一份而不在这儿再写一遍：那条"按**路径**清、不按映像名清"
    的规则是有代价换来的（按名杀会误杀用户装在别处的副本），只能有一份实现。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from frozen_smoke import sweep_stale              # noqa: PLC0415
    except Exception:                                      # noqa: BLE001
        return []
    try:
        return sweep_stale(exe)
    except Exception:                                      # noqa: BLE001
        return []


def _try_remove(p: Path) -> bool:
    """尽力删掉一个上一轮留下的旧产物；删不掉（还被人握着）就留着，不抛。"""
    if not p.exists():
        return True
    try:
        p.unlink()
        return True
    except OSError:
        return False


def move_aside_if_locked(exe: Path, *, stale_dir: Path) -> str:
    """清场后仍被占着就改名挪走，返回一句话说明（没被占就返回空串）。

    判定「占着没有」用的是**删除探针**：先把文件删掉，再原样写回来。

    - 删得掉 → 没有任何人握着它 → 内容原样恢复，返回空串（**不制造无意义的改名**）；
    - `PermissionError` / `OSError` → 被占着 → 挪进 `stale_dir` 让位；
      挪不动（连改名都不行）就如实回一句"让不开"，由调用方继续跑并在打包那一步红。

    为什么探针是"删一次再写回来"而不是"改个名再改回来"（2026-10-31 写第一版时选错了）：
    **改名探针对"握着"这件事是盲的** —— Windows 允许改名一个正在运行的 exe，所以测试与实现
    都会一路绿（我第一版就是，测试自己把这个盲点掩过去了）。而 PyInstaller 要做的恰恰是
    **删掉/覆盖**目标文件，那才是被锁拒掉的那件事。探针必须打在真实约束上：删不掉 = 会被拒；
    删掉了 = 恢复原样，代价是一次同目录写入。

    为什么不用 `open(exe, "ab")` 试写（更接近覆盖）：追加一个字节会让产物**在本轮打包前**
    就变成"被人改过的 exe"，而这个函数跑在打包之前、失败路径上还可能跑两遍 —— 宁可多一次
    删除+重写，也不要让产物在门禁自己手里变脏（且删+写是原子的两步、失败时已把文件恢复回来）。
    """
    if not exe.exists():
        return ""
    try:
        kept = exe.read_bytes()
        exe.unlink()
    except OSError:
        pass                                # 删不掉 = 有人握着它 → 往下让位
    else:
        exe.write_bytes(kept)               # 没被占：原样放回，什么都不改
        return ""
    stale_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = stale_dir / f"{exe.stem}.old-{stamp}{exe.suffix}"
    try:
        exe.rename(dest)
    except OSError as e:                        # 连改名都不行：如实说，别装作让开了
        return f"{exe.name} 被占用且让不开（{type(e).__name__}）"
    return f"{exe.name} 被外部进程占用 → 已改名让位（{dest.name}）"


def clean_stale_binaries(stale_dir: Path) -> list[str]:
    """清掉上一轮挪走的旧产物，返回仍清不掉的（还被人握着的）文件名。

    只在**每一轮打包开头**调，不在这里重试 —— 被占着的那一刻删不掉是正常的，
    等宿主重启过一轮，下一次打包开头自然就清掉了（旧且大，别留在盘上烂）。
    """
    if not stale_dir.is_dir():
        return []
    left: list[str] = []
    for p in sorted(stale_dir.iterdir()):
        if p.is_file() and not _try_remove(p):
            left.append(p.name)
    return left


def build_exe_step(argv: list[str], dist_exe: Path, *,
                   timeout: int = 1500, runner=subprocess.run) -> tuple[bool, float, str]:
    """带「清场 → 让位 → 打包 → 失败再来一轮」的打包步骤，返回 `(是否通过, 耗时, 摘要)`。

    分工：
    · 清场与让位是**先做**的（不是等失败了才做）—— 毒害打包的那个进程在打包前就存在，
      等它咬一口再来一轮等于白花一次打包的钱；
    · 重试只有一次。第二次还红就把两次的原因都带出来 —— 「重试过了也没用」必须能被看见，
      否则它和「压根没重试」在屏幕上一模一样（这个项目已经栽过好几回"两句话长得一样"）。

    没用 `ci_local.run()`：那一份的 300 字摘要规则是为一步一跑设计的，
    两步一体的摘要要自己拼才说得出"哪一轮红的"。
    """
    import time
    stale_dir = dist_exe.parent / STALE_SUBDIR
    notes: list[str] = []
    left = clean_stale_binaries(stale_dir)
    if left:
        notes.append(f"旧产物仍被占用，留待下轮：{'、'.join(left[:3])}")

    for attempt in (1, 2):
        t0 = time.time()
        remain = sweep_stale_binaries(dist_exe, runner)
        if remain:
            notes.append(f"清不掉同路径实例 PID {remain}（多半是被 MCP 宿主重新拉起的）")
        aside = move_aside_if_locked(dist_exe, stale_dir=stale_dir)
        if aside:
            notes.append(aside)
        try:
            r = runner(argv, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
            ok = getattr(r, "returncode", 1) == 0
            out = (getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")
        except subprocess.TimeoutExpired:
            ok, out = False, f"超时（>{timeout}s）"
        except OSError as e:                    # 连子进程都起不来（极端环境）
            ok, out = False, f"{type(e).__name__}: {e}"
        dt = time.time() - t0
        if ok:
            return True, dt, " ⏎ ".join([*notes, out.strip().splitlines()[-1] if out.strip() else "ok"])[:300]
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        tail = next((ln for ln in lines if "Error" in ln or "error:" in ln), lines[-1] if lines else "无输出")
        notes.append(f"第 {attempt} 轮失败：{tail}")
        if attempt == 1:
            notes.append("（重新清场让位后再打一轮）")
    return False, 0.0, " ⏎ ".join(notes)[:300]
