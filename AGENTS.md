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
| `web/index.html` | 全部前端（单文件，无构建、无 CDN 依赖，交互统一走 `data-act` 事件委托） |
| `tests/` | pytest 套件 116 项 + 内置假 LLM 端点（`llm_stub.py`），覆盖率门槛 90% |
| `scripts/desktop.py` | 桌面壳：补标准流 → 探测端口 → 后台起 uvicorn → 真实 HTTP 探活 → 开 Pywebview 窗口；启动过程写 `<数据目录>\startup.log` |
| `scripts/*_test.py` | 手工冒烟：smoke_test / regression_test / ai_smoke_test |
| `scripts/frozen_smoke.py` | **冻结版自查**：启动 dist 里的 exe，真打一遍接口，自动关掉（每次打包后必跑） |
| `build.bat` | 建 venv、装依赖、PyInstaller 打包 |

## 精确命令

```bat
rem 源码启动（改前端只需刷新窗口，无需构建）
scripts\start_desktop.bat

rem 只起后端（调试用）
.venv\Scripts\python.exe -m uvicorn app.main:app --port 18320

rem 测试（pytest 自动起隔离环境并回收；带覆盖率，门檻 90%）
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -v
set KANBAN_LIVE=1 && .venv\Scripts\python.exe -m pytest tests\test_ai.py -v

rem 性能基准（改数据层前先量）
.venv\Scripts\python.exe scripts\bench.py 1000

rem 打包（改完源码必须重打，否则 dist 里的 exe 是旧版本）
build.bat

rem 打包后必跑：冻结版自查（会短暂弹出应用窗口，脚本自己关掉）
.venv\Scripts\python.exe scripts\frozen_smoke.py
```

打包等价命令（`build.bat` 内部就是这条；`--add-data` 与两个 `--hidden-import` 缺一不可，
且路径必须用 `%~dp0` 绝对形式 —— `--specpath build` 会让相对路径按 spec 目录解析，导致找不到 `web`）：

```bat
.venv\Scripts\python.exe -m PyInstaller -F -w -n "AI交接看板" scripts\desktop.py ^
  --hidden-import app.main --hidden-import uvicorn --add-data "%~dp0web;web" --paths "%~dp0" ^
  --distpath dist --workpath build --specpath build --noconfirm
```

## 硬性约定

1. **所有写操作必须走 `mutate()`**（`app/main.py`）。禁止 `get_projects_data()` 拿到数据、
   改完再 `write_projects_data()` —— 那样读-改-写不在同一临界区，并发请求会互相覆盖。
   历史事故：20 并发创建全部返回 201，实际只落盘 3 条，数据静默丢失。
   定位用 `mutate_project(pid, fn)` / `mutate_task(tid, fn)`。
2. **`fn(data)` 返回 `(changed, result)`**，`changed=False` 时不落盘，避免无意义写放大。
3. **前端交互一律走 `data-act`，不写内联事件**。页面里 `onclick`/`onkeydown` 必须保持为 0：
   新增按钮 = 加 `data-act="名字"`（可带 `data-arg="值"`）+ 在 `ACTIONS` 表里注册实现，
   由 `runAction()` 统一分发（`tests/test_frontend.py` 会强制这条）。
   历史事故：`onclick="confirmDeleteProject('id','${esc(name)}')"` 里 `&#39;` 会被 HTML 解码回单引号，
   项目名带撇号时按钮直接报 JS 语法错误。动态列表另用 `data-pid` + 局部委托（见 `bindCardEvents`）。
4. **状态与优先级只能取固定值**：项目 `规划中/进行中/已暂停/已完成`，任务 `待办/进行中/已完成/阻塞`，
   优先级 `高/中/低`。前后端各有一层白名单：后端 `ProjectIn/TaskIn` 用 `Literal` 返回 422，
   前端用 `STATUS_SET/PRIORITY_SET` 兜底（这两个值会被拼进 class 名与文本）。
5. **密钥绝不进项目目录**。Key 只存在于 `%USERPROFILE%\.ai-kanban\config.json` 或
   `keys.local.json`，初始化脚本从环境变量读（`KANBAN_KEY_<端点名>`）。
6. **示例数据（`_SAMPLE_DATA`）必须保持脱敏**，不能出现真实项目名与本机路径 —— 它会随 exe 分发。

## 多智能体入口文件（别退化成 N 份副本）

`app/main.py` 的 `AGENT_ENTRY_TARGETS` + `build_agent_entries()` 把一份正文投影成各工具要求的入口：

```
AGENTS.md                          正文（唯一事实来源）
CLAUDE.md                          @AGENTS.md 桥接（Claude Code 不直接读 AGENTS.md）
.cursor/rules/project.mdc          frontmatter + 正文
.github/copilot-instructions.md
GEMINI.md / .clinerules/project.md / CONVENTIONS.md / .windsurfrules
```

**硬性约定**：`CLAUDE.md` 永远只写桥接，**不要把正文复制进去** —— 复制就意味着两份内容会漂移。
新增工具支持时改 `AGENT_ENTRY_TARGETS` 一处即可，`test_agent_files.py` 会校验路径集合。

## 性能基线（2026-09-14 实测，本机）

数据层是「整份 JSON 读改写」，延迟随项目数线性增长。`scripts/bench.py` 可复现：

| 数据集 | GET /api/projects | GET /api/stats | GET /api/projects/{pid} | GET /api/export-all | POST 新建任务 |
| --- | --- | --- | --- | --- | --- |
| 1000 项目 / 4.1 MB | 49 ms | 62 ms | 26 ms | 37 ms | 26 ms |
| 5000 项目 / 20.7 MB | 242 ms | 322 ms | 128 ms | 177 ms | 124 ms |

**结论：先不加缓存。** 这是本机单人看板，真实规模是几十个项目（≈个位数毫秒），
加 mtime 缓存会引入失效风险却换不来可感知收益。等到「列表接口 > 300ms」成为日常体验问题时
再考虑分片存储或 SQLite —— 那时也应该是先改存储层，而不是加一层缓存糊住。

## 测试约定

1. **默认用进程内客户端**（`app_client` / `seeded_client` 夹具，走 `TestClient`）：快，
   而且能被覆盖率统计。只有**并发用例**用子进程起的真服务（`fresh_server`），因为要真正的并行。
2. **AI 用例一律打本地假端点** `tests/llm_stub.py`（OpenAI 兼容，可注入文本/JSON/状态码/
   指定 Key 固定失败），不要在常规测试里消耗真实配额。需要真模型的用例放 `test_ai.py`，
   用 `KANBAN_LIVE=1` 显式启用。
3. **前端契约测试是硬约束**：页面里出现任何内联事件属性、或 `data-act` 没有在 `ACTIONS` 注册，
   `test_frontend.py` 直接红。新增按钮必须"加 `data-act` + 注册实现"。
4. 覆盖率门槛写在 `pytest.ini`（`fail_under = 90`）。剩下的未覆盖行基本是
   `_is_forbidden_target` 的异常分支、`_scheduler_loop` 的常驻循环等难以稳定触发的路径。

## 容易踩的坑

- **`-w` 打包后 `sys.stdout` / `sys.stderr` 是 `None`**（最坑的一个，2026-09-14 才挖出来）：
  uvicorn 的 `configure_logging()` 会调 `sys.stdout.isatty()` → `AttributeError`
  → `ValueError: Unable to configure formatter 'default'` → 后端线程直接死，
  现象是"双击 exe 没反应/窗口空白"，而且**没有任何报错**（-w 模式没有控制台）。
  `desktop.py` 里已有 `ensure_standard_streams()` 兜底 + `log_config=None` 降级重试，
  改桌面壳时不要删。排查同类问题先进 `<数据目录>\startup.log`。
- **Windows 路径探测别用 PowerShell 的 `Invoke-RestMethod`**：本机有代理时对 127.0.0.1
  也可能超时，会误判成"服务挂了"。用 `scripts/frozen_smoke.py`（urllib + `ProxyHandler({})`）。
- **`--specpath build` + 相对 `--add-data`**：会按 spec 目录解析，报
  `Unable to find ...\build\web`。必须用 `%~dp0` 绝对路径。
- **改完源码忘了重打包**：`dist\AI交接看板.exe` 不会自动更新。判据是 exe 时间戳晚于 `app/`、`web/`、`scripts/desktop.py`。
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

状态：可用（进度 95%）。2026-09-14 完成两轮加固：

第一轮（功能与安全）：

- 修并发丢更新（单临界区 `mutate()`）+ 回归测试覆盖 20/30 并发落盘
- 状态/优先级白名单（后端 422 + 前端收敛），修掉卡片内联事件拼串
- 本地服务加 Host/Origin 校验、可选 `KANBAN_TOKEN`、导出路径限制系统目录
- 密钥移出项目目录，示例数据脱敏，新增 `.gitignore` 与 `scripts/regression_test.py`
- 调度器支持 weekly 真实语义 + `last_period` 防重
- 修复 `build.bat` 因 `--specpath` 导致的打包失败

第二轮（工程化）：

- 全部 58 处内联事件收敛为 `data-act` + `ACTIONS` 统一委托（页面内联事件归零）
- 建立 pytest 套件 `tests/`（41 项：接口 / 并发 / 安全 / 前端契约），`pytest -v` 一键跑
- 加 CI（`.github/workflows/test.yml`）与 `requirements-dev.txt`
- `git init` + 首次提交

第三轮（打包可用性，2026-09-14 晚）：

- **修掉冻结版 exe 无法启动**：`-w` 下标准流为 None 导致 uvicorn 日志初始化崩溃，
  后端从未起来过（旧 exe 也一样）。修法见「容易踩的坑」第一条
- 桌面壳加启动日志 `startup.log` + 真实 HTTP 探活（不再只判断端口通不通）
- 新增 `scripts/frozen_smoke.py`：每次打包后一条命令验证 exe 真能服务

下一步候选：

1. 把手工冒烟脚本 `scripts/*_test.py` 收敛进 `tests/`（目前两套并存）
2. 把 `frozen_smoke.py` 接进 CI（打包 job 后跑一次）
3. 多项目数据量变大后考虑分片存储或 SQLite（当前整份 JSON 读写）
4. 前端拆文件（当前 2000+ 行单文件；如拆需引入轻量构建或改用 ES module + 本地静态服务）
