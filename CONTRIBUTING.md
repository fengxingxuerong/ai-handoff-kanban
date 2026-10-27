# 参与贡献（CONTRIBUTING）

先读 [AGENTS.md](AGENTS.md)——它是这个项目的**唯一约定来源**：命令、纪律、坑、进度全在里面。
本文只回答"外面的人怎么参与"，不重复它的内容。

## 这个项目是什么，什么算好改动

一个给「频繁切换 AI 智能体做项目」的 Windows 桌面看板：数据在本机，
**交接**（把上下文交给下一个智能体）是它的核心。因此这里的质量标准偏执一点：

- **"测试全绿"不等于"做对了"** —— 这个项目吃过好几次"全绿但发出去是坏的"（AGENTS.md 里逐条记着）。
- **宁可红，不可伪绿**：判据松了、跳过了、只断言"非空"，都算缺陷，比功能缺失更严重。
- **一条清单只许一处**：同一个数字/路径/命令不许在第二个地方再写一遍（写了就一定会漂）。

## 环境与命令

Windows + Python 3.13（CI 与出包都用这个版本）。

```bat
rem 建环境
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

rem 跑测试（pytest 自己起隔离环境，绝不碰 ~/.ai-kanban）
.venv\Scripts\python.exe -m pytest

rem 发版前的唯一判据（自己起隔离实例；--with-exe 会重新打包并跑冻结版冒烟）
.venv\Scripts\python.exe scripts\ci_local.py
.venv\Scripts\python.exe scripts\ci_local.py --quick
.venv\Scripts\python.exe scripts\ci_local.py --with-exe
```

**别在外面手敲六条命令然后说"全绿"** —— 那份清单没人能复算。`--quick` 少跑的那四步
（语义层）会在结论里点名，它印出来的话与全量不一样。

## 改动的门槛

| 改什么 | 至少要带上 |
| --- | --- |
| 修 bug | 一条**先红后绿**的用例（先证明它能抓住这个 bug，再修） |
| 改语义 / 改判据 | 一条**变异探针**（把代码改坏，用例必须变红）+ A/B 两个方向都验过 |
| 改前端 | 真浏览器里点一遍（`scripts/live_ui_check.py` 或 `tests/test_frontend_e2e.py`）；JS 语法错会让整个界面变成死面板，而 Python 测试不会红 |
| 改文档里的数字 | 只改那一处；`tests/test_docs.py` 会对账，别的地方不许抄 |
| 新增 MCP 工具 | `TOOL_KIND` 登记（read/write/snapshot）、入参校验、每个工具都要被真的调一次、冻结冒烟的工具数是**精确断言** |

三条最容易踩的：

1. **还原/写盘一律"字节进、字节出"**（`read_bytes`/`write_bytes`）：`read_text` 会把 CRLF 归一成 LF，写回时又翻成 CRLF，于是"注入再还原"之后行尾变了 —— 内容看着一样，`git status` 却是脏的。
2. **用例不许赌"本机比断言快"**：并发/异步的地方用条件等待（`wait_for_function`），不要加睡眠。
3. **跳过 ≠ 通过**：任何"因为环境缺东西所以跳过"的用例，要么补上那一级，要么在门禁里点名。

## 提交信息

`类型(范围): 一句话说明`，正文讲**为什么**（不是改了什么，diff 已经说了）。类型用
`feat` / `fix` / `refactor` / `test` / `docs` / `chore`。

## 发版（维护者）

1. 改 `app/version.py`（**版本号是单一事实来源**：界面版本条 / `/api/health` / MCP `serverInfo` / exe 的 PE 版本资源都从它来）；
2. `.venv\Scripts\python.exe scripts\ci_local.py --with-exe` 全绿；
3. 推 `main`，等 CI 绿（同一条 `ci_local.py --with-exe`，只在 `windows-latest`）；
4. 打 tag（`v` + `version.py` 的数）并推送 —— tag 与版本号不同源会被 CI 拦下；CI 自动发 GitHub Release（两个 exe）。

## 不会被接受的改动

- 把看板数据推出本机的功能（公网分享链接、云同步、遥测、默认收集用量）；
- 让交接物默认带**未脱敏**原文就离开本机；
- 为了"看起来绿"而放松判据（删断言、加 `skip`、把精确断言改成"非空"）；
- 在仓库里复制第二份同样的清单/数字/命令。

安全问题请**不要**开公开 issue，见 [SECURITY.md](SECURITY.md)。
