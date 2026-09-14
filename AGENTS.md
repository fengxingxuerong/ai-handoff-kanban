# AGENTS.md — AI 交接看板

> 给 AI 编码代理的项目上下文。只写代码里看不出来的信息：命令、约定、坑、进度。

## 这是什么

多项目任务看板桌面应用，为「频繁切换 AI 智能体做项目」设计：每个项目带交接说明与下一步行动，
一键导出交接摘要 / 交接包 / AGENTS.md，让新智能体接手零成本。内置多端点 LLM 自我监管。

技术栈：FastAPI + 原生单页前端 + PyWebview 桌面壳，PyInstaller 打成单个 exe。

## 目录与职责

| 路径 | 职责 |
| --- | --- |
| `app/main.py` | 全部 HTTP 接口 + 数据层 + 监管/指令/交接/AGENTS.md 生成 + 调度线程 |
| `app/llm.py` | LLM 网关：端点排序、多 Key 轮询、失败降级、配置读写（含锁） |
| `web/index.html` | 全部前端（单文件，无构建、无 CDN 依赖） |
| `scripts/desktop.py` | 桌面壳：探测端口 → 后台起 uvicorn → 开 Pywebview 窗口 |
| `scripts/*_test.py` | smoke_test（基础）/ regression_test（回归）/ ai_smoke_test（真实调 LLM） |
| `build.bat` | 建 venv、装依赖、PyInstaller 打包 |

## 精确命令

```bat
rem 源码启动（前端改完直接刷新窗口即可，无需构建）
.venv\Scripts\start_desktop.bat

rem 只起后端（调试用；改前端后刷浏览器）
.venv\Scripts\python.exe -m uvicorn app.main:app --port 18320

rem 测试（务必用临时数据目录，别污染 ~/.ai-kanban）
set KANBAN_DATA_DIR=%TEMP%\kanban_test
.venv\Scripts\python.exe scripts\smoke_test.py http://127.0.0.1:18320
.venv\Scripts\python.exe scripts\regression_test.py http://127.0.0.1:18320

rem 打包（改完源码必须重打，否则 dist 里的 exe 是旧版本）
build.bat
```

打包等价命令（`build.bat` 内部就是这条；`--add-data "web;web"` 与两个 `--hidden-import` 缺一不可）：

```bat
.venv\Scripts\python.exe -m PyInstaller -F -w -n "AI交接看板" scripts/desktop.py ^
  --hidden-import app.main --hidden-import uvicorn --add-data "web;web" --paths . ^
  --distpath dist --workpath build --specpath build
```

## 硬性约定

1. **所有写操作必须走 `mutate()`**（`app/main.py`）。禁止 `get_projects_data()` 拿到数据、
   改完再 `write_projects_data()` —— 那样读-改-写不在同一临界区，并发请求会互相覆盖。
   历史事故：20 并发创建全部返回 201，实际只落盘 3 条，数据静默丢失。
   定位用 `mutate_project(pid, fn)` / `mutate_task(tid, fn)`。
2. **`fn(data)` 返回 `(changed, result)`**，`changed=False` 时不落盘，避免无意义写放大。
3. **前端不要往内联事件里拼数据**。历史事故：`onclick="confirmDeleteProject('id','${esc(name)}')"`
   里 `&#39;` 会被 HTML 解码回单引号，项目名带撇号时按钮直接报 JS 语法错误。
   动态列表一律 `data-*` + 事件委托（见 `bindCardEvents`）。
4. **状态与优先级只能取固定值**：项目 `规划中/进行中/已暂停/已完成`，任务 `待办/进行中/已完成/阻塞`，
   优先级 `高/中/低`。前后端各有一层白名单：后端 `ProjectIn/TaskIn` 用 `Literal` 返回 422，
   前端用 `STATUS_SET/PRIORITY_SET` 兜底（这两个值会被拼进 class 名与文本）。
5. **密钥绝不进项目目录**。Key 只存在于 `%USERPROFILE%\.ai-kanban\config.json` 或
   `keys.local.json`，初始化脚本从环境变量读（`KANBAN_KEY_<端点名>`）。
6. **示例数据（`_SAMPLE_DATA`）必须保持脱敏**，不能出现真实项目名与本机路径 —— 它会随 exe 分发。

## 容易踩的坑

- **改完源码忘了重打包**：`dist\AI交接看板.exe` 不会自动更新。判据是 exe 时间戳晚于 `app/` 与 `web/`。
- **数据目录**：默认 `%USERPROFILE%\.ai-kanban`（Windows 下优先 `USERPROFILE` 而不是 `HOME`，
  否则受 Git Bash 的 HOME 干扰）。测试请用 `KANBAN_DATA_DIR` 隔离。
- **端口占用不等于自己活着**：`desktop.py` 只看端口通不通，18320 被别的程序占了会在窗口里
  打开别人的页面。排查异常时先确认进程。
- **调度线程在模块 import 时启动**（`start_scheduler()`，幂等）。用 `--reload` 调试时每个 worker 一个线程，
  靠配置里的 `last_period` 防同周期重复触发。
- **LLM 输出不可信**：一律走 `_extract_json()` 容错提取；AI 结果的落地必须经用户确认（人工闸门）。
- **LLM 不可用要有降级**：AGENTS.md 生成失败会退回 `_build_agents_md_local()`，不要直接抛错。
- **窗口渲染**依赖系统 WebView2 运行时（`gui="edgechromium"`）。

## 当前进度与下一步

状态：可用（进度 90%）。2026-09-14 完成一轮加固：

- 修并发丢更新（单临界区 `mutate()`）+ 回归测试覆盖 20 并发落盘
- 状态/优先级白名单（后端 422 + 前端收敛），修掉卡片内联事件拼串
- 本地服务加 Host/Origin 校验、可选 `KANBAN_TOKEN`、导出路径限制系统目录
- 密钥移出项目目录，示例数据脱敏，新增 `.gitignore` 与 `scripts/regression_test.py`
- 调度器支持 weekly 真实语义 + `last_period` 防重

下一步候选：

1. `git init` 并把首次提交打上（当前项目无版本控制）
2. 把 `smoke_test` / `regression_test` 包成 pytest，接一次 CI
3. 前端 62 处静态内联 `onclick` 收敛为事件委托（目前只改了动态部分）
4. 多项目数据量变大后考虑分片存储或 SQLite（当前整份 JSON 读写）
