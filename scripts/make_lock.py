# -*- coding: utf-8 -*-
"""生成 requirements.lock.txt —— 把"这台机器上实测通过的那一套版本"钉下来。

为什么要它：`requirements*.txt` 写的是 `>=`，表达的是**兼容下限**（意图），
但它保证不了可复现 —— 换台机器重装会拿到完全不同的版本，而这是一个卖点是"交接"的工具，
"我这边是绿的"必须能被别人复算。所以两份文件各管一件事：

    requirements*.txt      人写的意图：最低需要什么
    requirements.lock.txt  机器算的事实：实测通过的是哪一套

重新生成（在跑过全量测试的那个 venv 里）：
    .venv\\Scripts\\python.exe scripts/make_lock.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "requirements.lock.txt"
# 这三个是安装器自身，钉住反而会让 `pip install -r` 在别的 pip 版本上自找麻烦
SKIP = {"pip", "setuptools", "wheel", "pkg-resources"}

HEADER = (
    "# 实测通过的完整依赖闭包 —— 由 scripts/make_lock.py 生成，别手改。\n"
    "# 用途：换机器 / CI / 出包时 `pip install -r requirements.lock.txt` 求可复现；\n"
    "#       日常开发看 requirements.txt 与 requirements-dev.txt（那里写的是兼容下限）。\n"
    "# 生成环境：{py}\n\n"
)


def main() -> int:
    r = subprocess.run([sys.executable, "-m", "pip", "freeze", "--all"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("pip freeze 失败：", (r.stderr or r.stdout)[:400])
        return 1
    lines = []
    for raw in (r.stdout or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("-e ") or " @ " in line:
            continue
        name = line.split("==")[0].split("[")[0].strip().lower()
        if name in SKIP:
            continue
        lines.append(line)
    body = HEADER.format(py=sys.version.split()[0] + " / " + sys.executable)
    OUT.write_text(body + "\n".join(sorted(lines, key=str.lower)) + "\n", encoding="utf-8")
    print(f"写入 {OUT.name}：{len(lines)} 个包")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
