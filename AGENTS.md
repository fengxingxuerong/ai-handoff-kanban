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
| `tests/` | pytest 套件 248 项 + 内置假 LLM 端点（`llm_stub.py`），覆盖率门槛 90%（实测 93%）。含真实浏览器点击测试 `test_frontend_e2e.py` |
| `scripts/desktop.py` | 桌面壳：补标准流 → 探测端口 → 后台起 uvicorn → 真实 HTTP 探活 → 开 Pywebview 窗口；启动过程写 `<数据目录>\startup.log`。`KANBAN_NO_WINDOW=1` 时只服务不开窗（CI / 服务器 / 调试用） |
| `scripts/live_check.py` | 对着**已在跑**的实例打接口做只读体检（不写数据）；替代了原来的三个手工冒烟脚本 |
| `scripts/live_ui_check.py` | 对着**已在跑**的实例开真浏览器做只读校验（Playwright + 系统 Edge/Chrome）：证明界面真的渲染出来了，不是白屏。全程只点导航类操作，不写一个字节 |
| `scripts/scenario_test.py` | 真实场景测试 33 例（真服务 + 真 MCP + 真实业务数据，可导出报告） |
| `scripts/delivery_drill.py` | 交付演练：模拟换智能体接手，端到端 19 项（接手→执行→交接物→脱敏→迁移） |
| `scripts/real_ai_test.py` | 真实模型 AI 功能测试：5 大功能 × 6 模型通道（消耗真实配额） |
| `scripts/test_all_llm.py` | LLM 端点全量实测：端点×Key×模型 健康矩阵 |
| `scripts/make_icon.py` | 纯标准库生成应用图标 app.ico |
| `scripts/frozen_smoke.py` | **冻结版自查**：启动 dist 里的 exe，真打一遍接口，自动关掉（每次打包后必跑） |
| `scripts/mcp_server.py` | MCP 服务（stdio）：13 个工具，代理可列项目/拉接手包(context)/读写任务/生成交接物 |
| `scripts/bench.py` | 读写性能基准（先量再改） |
| `scripts/make_stamp.py` | **打包前**生成构建戳 `app/_build_stamp.py`（版本条显示的打包时间）；`build.bat` 与 CI 都会调，手动打包别忘了 |
| `build.bat` | 建 venv、装依赖、生成构建戳、PyInstaller 打包 |

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

rem 只跑前端真实点击测试（复用系统 Edge/Chrome，无需下载 Chromium）
.venv\Scripts\python.exe -m pytest tests\test_frontend_e2e.py -v

rem 性能基准（改数据层前先量）
.venv\Scripts\python.exe scripts\bench.py 1000

rem 对着已在跑的实例做只读体检（不写数据）
.venv\Scripts\python.exe scripts\live_check.py http://127.0.0.1:18320 --mcp

rem 对着已在跑的实例开真浏览器校验界面（只读；证明不是白屏）
.venv\Scripts\python.exe scripts\live_ui_check.py http://127.0.0.1:18320

rem 真实场景测试（真服务 + 真 MCP + 真实业务数据）
.venv\Scripts\python.exe scripts\scenario_test.py

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
  --icon "%~dp0app.ico" ^
  --distpath dist --workpath build --specpath build --noconfirm
```

打包**之前**还要跑一次 `.venv\Scripts\python.exe scripts\make_stamp.py`（写构建戳；
`build.bat` 里是第 4 步，CI 里有独立一步）。漏了不会报错，只是版本条显示"未知"。

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
4. **kimi 系模型只允许 temperature=1**。`app/llm.py` 的 `chat()` 已按模型名自动适配，
   并对 400 Temperature 错误自动用 1 重试兜底。新增模型时若报该错，优先在 `_model_temp` 加适配，不要绕过网关直调。
5. **杀 exe 进程树必须 `taskkill /F /T /PID`**。PyInstaller -F 单文件会拉起子进程跑实际服务，
   `terminate()` 只杀父进程，子进程残留会占用端口与 exe 文件（历史事故：打包时 PermissionError 拒绝访问）。
   `frozen_smoke.py` 已内置进程树清理 + 端口兜底检查。
6. **状态与优先级只能取固定值**：项目 `规划中/进行中/已暂停/已完成`，任务 `待办/进行中/已完成/阻塞`，
   优先级 `高/中/低`。前后端各有一层白名单：后端 `ProjectIn/TaskIn` 用 `Literal` 返回 422，
   前端用 `STATUS_SET/PRIORITY_SET` 兜底（这两个值会被拼进 class 名与文本）。
7. **密钥绝不进项目目录**。Key 只存在于 `%USERPROFILE%\.ai-kanban\config.json` 或
   `keys.local.json`，初始化脚本从环境变量读（`KANBAN_KEY_<端点名>`）。
8. **示例数据（`_SAMPLE_DATA`）必须保持脱敏**，不能出现真实项目名与本机路径 —— 它会随 exe 分发。

## MCP 服务（让代理直接读写看板）

`scripts/mcp_server.py`：stdio 传输、手写 JSON-RPC 2.0、零第三方依赖。

- **实现方式**：直接 import `app.main` 复用业务函数（含字段白名单与 `mutate()` 单临界区），
  **不要再写一套读写逻辑**，否则两套校验会漂移。新增工具 = 加 `TOOLS` 条目 + `HANDLERS` 注册，
  `test_mcp_server.py` 会校验 schema 完整性。
- **协议兼容**：2026-07-28 起 MCP 改为无状态（无 initialize 握手、结果带 `resultType`），
  旧版（2025-xx）仍走握手。本服务两者都支持，`server/discover` 也实现了。
- **stdout 只能有 JSON-RPC**：日志一律 `print(..., file=sys.stderr)`。往 stdout 写一个字都会污染协议。
- **跨进程写安全**：`mutate()` 里除线程锁外还有 `_cross_process_lock()`（Windows 用 `msvcrt.locking`，
  其他平台 `fcntl.flock`）。桌面应用与 MCP 服务是两个进程、同一份 JSON，**少了这把锁就是丢更新**。
- 短命进程不要后台线程：MCP 服务启动前设 `KANBAN_NO_SCHEDULER=1`。

自测（不需要客户端）：

```bat
echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | .venv\Scripts\python.exe scripts\mcp_server.py
.venv\Scripts\python.exe -m pytest tests\test_mcp_server.py -q --no-cov
```

## git 集成（只读，绝不拼 shell）

- `git_snapshot(repo_path, limit, use_cache)`：固定调用 `rev-parse` / `log` / `status --porcelain`，
  **一律列表形式传参**（`["git", *args]` + `cwd=`），仓库路径里塞 `; rm -rf` 之类也只是路径。
  6 秒超时，任何失败都转成 `{ok: False, error}` 而不是抛异常。
- **缓存只给批量场景用**：`_git_cache` TTL 60 秒，服务 AI 监管（逐项目取摘要，避免每条一次子进程）。
  `GET /api/projects/{pid}/git` 必须 `use_cache=False` —— 用户刚提交完点开却看到旧状态最容易被坑。
- 解析 `git status --porcelain` 时用 **rstrip 不能用 strip**：输出形如 `" M 文件"`，
  状态列前面的空格有意义，strip 掉会把文件名截错（曾经解析出 `ile0.txt`）。
- 读不到仓库时降级：接口 400 带可行动提示；交接包省略该节（不要塞空标题）。

## 数据层三条不显眼但重要的约定

1. **首次落盘丢弃示例数据**：`create_project` 在 `mutate` 里检查 `data_file().exists()`，
   磁盘上还没有数据文件时说明看板从未被真正使用过，内置示例只用于引导界面，不能跟着用户的第一条数据落盘
   （否则用户一建项目就多出 4 个删不掉的示例）。
   用户**编辑**过示例项目就不再丢弃 —— 他显然拿它当模板用了。
2. **数据文件损坏时自动从「最近一次正常保存」救回来**：
   `_save_data()` 每次成功保存都会刷一份 `projects.json.last-good`；`_load_data()` 解析失败时，
   先把损坏文件另存为 `.bak`（取证用），再从 `last-good` 恢复并写回，只有真的没得救才重置为空。
   两个坑都别踩：
   - 损坏时复制出来的 `.bak` **本身就是坏的**，救不了数据 —— 救命的是 last-good；
   - 自愈那次写入必须 `_save_data(..., update_last_good=False)`，否则会把 last-good 覆盖成空看板。
   上报用 `data_recovered` + `recovered_from`（读一次即清），前端据此提示。
3. **恢复入口**：`GET /api/recoverable` 列出来源与规模预览，`POST /api/restore`
   按白名单来源（`last-good` / `bak` / `backup_*`）覆盖恢复，**恢复前先把当前数据另存为
   `projects.json.before-restore.bak`**。恢复是覆盖语义，不是合并。
4. **写盘默认不覆盖已有文件**：`/api/export/agent-files` 遇到已存在的 `AGENTS.md`/`CLAUDE.md`
   默认跳过并回报 `skipped_existing`，要覆盖必须显式 `overwrite=true`。
   仓库里的入口文件很可能是用户手写的，静默覆盖等于内容丢失。

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
3. **前端分两层测，别只写一层**：
   - `test_frontend.py`：**静态契约** —— 页面里 `on*=` 必须为 0、每个 `data-act` 都已在 `ACTIONS` 注册、
     **且 ACTIONS 里调用的函数必须真的有定义**（这条是补的：`exportData`/`importData` 注册进表里
     但函数从未实现，按钮点了只有 ReferenceError，而旧契约测试只查注册，全程绿灯）。
   - `test_frontend_e2e.py`：**真实浏览器点击**（Playwright 驱动系统 Edge/Chrome，`channel="msedge"`，
     不下载 Chromium）。点真页面、断言真 DOM，任何 `console.error` / `pageerror` / 非 favicon 的 4xx-5xx 直接判失败。
     两个夹具：`ui`（普通实例）、`ai_ui`（**配了假 LLM 端点的实例** —— 报告/指令/提炼/测连接靠它才点得动）。
     没装 playwright 或机器上没浏览器会自动 skip，不影响常规套件的确定性。
   写 E2E 时容易踩的五点：
   - 详情视图的判据是 `#view-detail.active`（不是某个 `#detail-panel`，它不存在）；
   - 看板四列是 **class**（`.kcol.k-todo` / `.kcol.k-doing` …），不是 id；
   - **Playwright 默认自动取消 `confirm()`** —— 凡是有二次确认的流程（删除、提炼落地、导入覆盖）
     都必须先 `page.on("dialog", lambda d: d.accept())`，否则点了等于没点；
   - **favicon 的 404 是浏览器自发的**，监听器里要过滤掉，否则每条用例都会因为它变红；
   - 夹具已经把页面打开了，用 API 造完数据要 `page.reload()` 才看得见；
     卡片按**更新时间倒序**，别用 `.first` 猜是哪张 —— 用 `.pcard:has-text('名字')` 定位。
4. **覆盖率门槛写在 `pytest.ini`**（`fail_under = 90`，实测 93%，余量 3 个点）。
   剩下的未覆盖行基本是 `_is_forbidden_target` 的异常分支、`Path.resolve()` 失败、
   `_scheduler_loop` 的常驻循环等难以稳定触发的路径 —— **别为了数字构造畸形环境硬凑**。
   `test_coverage_gaps.py` 里记着"哪些逻辑真但一直没测到"，新增这类补测往那放。
5. **用例不能依赖墙钟**：涉及时间的断言（调度时间、周期键、截止日期）必须用相对时间构造。
   别写 `at="00:01"` 这种常量 —— 跑到那一分钟就会意外命中，用例变成看运气
   （2026-09-16 凌晨实际踩到：一条调度用例在 00:01 那天红了）。
6. **隐藏元素取文本要用 `text_content()`**：`inner_text()` 只返回"渲染出来"的文本，
   弹窗/折叠区里的文字（如设置面板里的版本号）拿不到，会误判成"没渲染"。

## 容易踩的坑

- **`-w` 打包后 `sys.stdout` / `sys.stderr` 是 `None`**（最坑的一个，2026-09-14 才挖出来）：
  uvicorn 的 `configure_logging()` 会调 `sys.stdout.isatty()` → `AttributeError`
  → `ValueError: Unable to configure formatter 'default'` → 后端线程直接死，
  现象是"双击 exe 没反应/窗口空白"，而且**没有任何报错**（-w 模式没有控制台）。
  `desktop.py` 里已有 `ensure_standard_streams()` 兜底 + `log_config=None` 降级重试，
  改桌面壳时不要删。排查同类问题先进 `<数据目录>\startup.log`。
- **Windows 路径探测别用 PowerShell 的 `Invoke-RestMethod`**：本机有代理时对 127.0.0.1
  也可能超时，会误判成"服务挂了"。用 `scripts/frozen_smoke.py`（urllib + `ProxyHandler({})`）。
- **无桌面会话时不要开窗**：CI（`windows-latest`）与服务器上没有桌面，pywebview 起窗会失败并带走整个进程。
  凡是"真启动 exe"的自动化，一律带 `KANBAN_NO_WINDOW=1`（只服务不开窗），
  `frozen_smoke.py` 在 CI 里就是这么跑的（数据目录由它自己指定为 `%TEMP%\kanban_frozen_smoke`）。
- **`--specpath build` + 相对 `--add-data`**：会按 spec 目录解析，报
  `Unable to find ...\build\web`。必须用 `%~dp0` 绝对路径。
- **改完源码忘了重打包**：`dist\AI交接看板.exe` 不会自动更新。判据：看版本条（设置面板里）
  或 `/api/health` 的 `build_time`，跟源码最新改动时间比 —— 源码态这个值由 `app/version.py`
  实时算（取 `app/` + `web/` + `scripts/desktop.py` 里最新的 mtime），打包态读构建戳
  `app/_build_stamp.py`（`scripts/make_stamp.py` 生成，`build.bat`/CI 自动跑）。
  **不要手填时间**：历史上它是常量 `"2026-09-15 12:00"`，而 exe 实际是 23:46 打的，
  于是"靠它判断是否落后"完全落空。
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

**状态：v1.0.0 转正、可用。** 演进过程（按提交顺序，细节见各 commit）：

| 轮次 | 主题 | 关键产出 |
| --- | --- | --- |
| 一 | 功能与安全 | 并发丢更新（`mutate()` 单临界区）、状态白名单、Host/Origin 校验、密钥移出项目、示例数据脱敏、调度器防重 |
| 二 | 工程化 | 58 处内联事件收敛为 `data-act`、pytest 套件起步、CI、`git init` |
| 三 | 打包可用性 | 修掉冻结版 exe 起不来（见「容易踩的坑」第一条）、启动日志、HTTP 真探活、`frozen_smoke.py` |
| 四 | 交接三件套 + 自愈 | 8 个工具入口文件、MCP 服务（stdio，13 工具）、跨进程写锁、last-good 自愈 + 恢复入口 |
| 五 | 上下文与排期 | git 集成（只读）、任务 `refs`（代码位置）、任务 `depends_on`（依赖/拓扑排序/可执行任务） |
| 六 | 功能扩展与对标 | 截止日期、看板拖拽、数据导入导出、全局搜索（Ctrl+K）、深浅色主题、脱敏、健康趋势预警、交付演练脚本、真实模型端到端测试、版本号贯通、1.0.0 转正 |

**验证基线（2026-09-16）**：`pytest` 278 通过 / 6 跳过 / 覆盖率 93%（门槛 90，余量 3 个点），约 75 秒。

**下一步（按价值排序）**：

1. **`app/main.py` 拆分** —— 1784 行单模块（接口/数据层/AI/git/入口文件生成全在一起）。
   拆的时候守住「写操作必须走 `mutate()`」这条，别顺手把临界区拆散。
2. **存储层**：仍是整份 JSON 读改写。触发条件是「列表接口日常 > 300ms」，
   那时直接换分片或 SQLite，**不要加缓存糊**（理由见「性能基线」）。
3. E2E 已覆盖到 AI 交互（报告渲染、指令的人工闸门、提炼落地、测连接）。
   还没进浏览器层的：AGENTS.md / 多工具入口文件的预览与写盘、备份与恢复面板的实际恢复动作、
   监管趋势图。需要哪个就把对应的 `data-act` 点一遍 —— 夹具 `ai_ui` 已经把假端点配好了。
4. 剩余未覆盖的 98 行基本是异常分支（`Path.resolve()` 失败、系统目录判定异常、各类 500 兜底）。
   要补就得构造畸形环境，收益递减 —— 别为了数字硬凑。

**不要再做的事**（已经想清楚、别回头做）：

- 加更多功能 —— 功能面已经很宽，边际收益在下降，重心应转向验证与稳定性。
- worktree 并行隔离、团队协作与权限 —— 不是这个项目的赛道（见 README「和同类工具比」）。
- 给 `CLAUDE.md` 复制正文、给每个工具各存一份约定 —— 必然漂移，见「多智能体入口文件」。

