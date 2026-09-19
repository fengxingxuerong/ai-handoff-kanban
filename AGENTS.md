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
| `app/filelock.py` | 跨进程文件锁（msvcrt/fcntl 一份实现，`main` 与 `llm` 共用）。两边不能互相 import，所以锁不放在任何一边里 |
| `app/handoff_page.py` | 自包含「接手页」单文件 HTML（含分层依赖图 SVG）；**不 import main**（main 会 import 它，反过来成环） |
| `web/index.html` | 全部前端（单文件，无构建、无 CDN 依赖，交互统一走 `data-act` 事件委托） |
| `tests/` | pytest 套件（用例数见文末「验证基线」，此处不写死）+ 内置假 LLM 端点（`llm_stub.py`），覆盖率门槛 90%（实测数字只写在文末基线那一处）。含真实浏览器点击测试 `test_frontend_e2e.py` |
| `scripts/desktop.py` | 桌面壳：补标准流 → 探测端口 → 后台起 uvicorn → 真实 HTTP 探活 → 开 Pywebview 窗口；启动过程写 `<数据目录>\startup.log`。`KANBAN_NO_WINDOW=1` 时只服务不开窗（CI / 服务器 / 调试用） |
| `scripts/live_check.py` | 对着**已在跑**的实例打接口做只读体检（不写数据）；替代了原来的三个手工冒烟脚本 |
| `scripts/live_ui_check.py` | 对着**已在跑**的实例开真浏览器做只读校验（Playwright + 系统 Edge/Chrome）：证明界面真的渲染出来了，不是白屏。全程只点导航类操作，不写一个字节 |
| `scripts/scenario_test.py` | 真实场景测试 33 例（真服务 + 真 MCP + 真实业务数据，可导出报告） |
| `scripts/delivery_drill.py` | 交付演练：模拟换智能体接手，端到端 19 项（接手→执行→交接物→脱敏→迁移）。**不传参数时自起隔离实例**；传 base_url 则先读该实例 `/api/health` 的 `data_file` 自证不是真实数据目录，否则拒绝运行（演练会用 `/api/data/import` 整份覆盖） |
| `scripts/real_ai_test.py` | 真实模型 AI 功能测试：5 大功能 × 6 模型通道（消耗真实配额） |
| `scripts/release_regression.py` | 发版回归：自起独立实例（不碰真实数据），v1.1 新功能全链路 18 项（`--no-ai`；带真实 AI 步骤为 23 项）；真实 Key 从 `KANBAN_REAL_DATA_DIR` 指向的配置读取 |
| `scripts/test_all_llm.py` | LLM 端点全量实测：端点×Key×模型 健康矩阵 |
| `scripts/make_icon.py` | 纯标准库生成应用图标 app.ico |
| `scripts/frozen_smoke.py` | **冻结版自查**：启动 dist 里的 exe，真打一遍接口，自动关掉（每次打包后必跑） |
| `scripts/mcp_server.py` | MCP 服务（stdio）：代理可列项目/拉接手包(context)/读写任务/记过程与记忆/生成交接物。每个工具在 `TOOL_KIND` 里登记读写属性与标题（决定 `annotations`，也决定只读模式下是否出现在 `tools/list`），入参按各自 `inputSchema` 的 required/enum/additionalProperties 真的校验 |
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
   改完再另起一次锁写回 —— 那样读-改-写不在同一临界区，并发请求会互相覆盖。
   曾经用来"自己持锁整份覆盖"的 `write_projects_data()` 已删除：全库零调用点，
   而它唯一的用处就是让端点写成两段式（导入端点就中过一次）。
   定位用 `mutate_project(pid, fn)` / `mutate_task(tid, fn)`。
   **"先校验再写入"的校验也算这一条**：`create_task`/`update_task` 曾在锁外读快照
   做依赖清洗与判环，两个并发请求各自都判得过、合起来却能写进一个环 ——
   兄弟任务一律在 `fn` 里从 `p` 取，`fn` 抛异常即放弃写入（不会留半写）。
   同理 `find_task()`（锁外整份读）已随该事故删除。
2. **`fn(data)` 返回 `(changed, result)`**，`changed=False` 时不落盘，避免无意义写放大。
3. **前端交互一律走 `data-act`，不写内联事件**。页面里 `onclick`/`onkeydown` 必须保持为 0：
   新增按钮 = 加 `data-act="名字"`（可带 `data-arg="值"`）+ 在 `ACTIONS` 表里注册实现，
   由 `runAction()` 统一分发（`tests/test_frontend.py` 会强制这条）。
   历史事故：`onclick="confirmDeleteProject('id','${esc(name)}')"` 里 `&#39;` 会被 HTML 解码回单引号，
   项目名带撇号时按钮直接报 JS 语法错误。动态列表另用 `data-pid` + 局部委托（见 `bindCardEvents`）。
   **项目弹窗里的标签 chip 必须与 `selTags` 一一对应**（`tagChip()` / `resetTagChips()`）：
   `saveProject` 保存的是 `[...selTags]`，曾经出现过"标签在 Set 里、界面上没有 chip"，
   用户看不见也没法取消，以及上一个项目的自定义 chip 留在原地可被点。
   **关闭弹窗也只有一条路**：✕ / 「完成」/ 点遮罩 / Esc 全部走 `closeOneModal(id)`
   （Esc 经 `closeTopModal()` 一次只关最上面一层）。以前它们各自直接摘 `.show`，
   漏掉了 `closeModal` 的动画复位（弹窗只有第一次打开有 pop），Esc 更不清 `pendingActions`
   —— 重开指令窗口后点「确认执行」会执行上一次的解析结果。
   `test_frontend.py` 用「全文只有 4 处 `classList.remove("show")`」钉住这条，新增绕过点会红。
   **可点的卡片（`.pcard`/`.tcard`/`.gs-item`/`.sess-item`）都要带 `tabindex="0"` +
   `data-keyact="1"`**：它们全是 `div`，没这两个属性键盘就用不了（全局 keydown 负责 Enter/空格）。
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
9. **交接物默认不出本机**：导出的直链只允许 `127.0.0.1`（`app/main.py:_local_url`、
   `scripts/mcp_server.py:local_url`）。想给远端智能体就必须走 `redact=1` 脱敏 + 手工确认，
   **不要加"公网可访问的分享链接"这种能力** —— 那等于把看板数据推出 `%USERPROFILE%\.ai-kanban`。
10. **接手页（`app/handoff_page.py`）必须自包含**：不引任何 CDN、不联网、单文件双击可用；
   依赖图用内联 SVG，不要引 mermaid 之类的在线渲染（任务标题会发给第三方）。
   `tests/test_handoff_page.py` 会强制这两条（无外链、字段转义）。

11. **任何"追加型"存储必须有上限**。已经踩过/收过口的两处：
    `usage.jsonl` 超 `MAX_USAGE_LINES` 裁到最近 `KEEP_ON_TRIM` 条；
    任务 `journal` 有 `MAX_JOURNAL` 条数与 `MAX_JOURNAL_TEXT` 长度上限 ——
    它存在 `projects.json` 里，**不设上限就是让每次读写都要整份解析的核心文件悄悄变胖**。
    新增同类字段（日志、轨迹、事件流）时先想清楚这一层，别等它涨起来才发现。
12. **会被送进 LLM 的读取必须限定来源**。`preview_session` / `ingest_session` 只接受
    `is_allowed_session_file()` 放行的路径（已配置的会话源目录内）。
    不限源 = 任意本机文件（`config.json` 里的 Key、`.env`、别的项目源码）都能被搬到外部模型，
    直接掀翻第 9 条。

13. **弹窗是一套固定的 a11y 契约**，新增弹窗必须一起给齐（`test_frontend.py` 会数数量）：
    `.modal` 上 `role="dialog" aria-modal="true"` + 标签（有 `<h2 id>` 用 `aria-labelledby`，
    否则 `aria-label`）；打开时焦点送进弹窗（优先第一个输入框）、关闭时还给触发元素
    （`focusInto` / `backToOpener`）；Tab 不许跑出最上面那层（`trapTab`；滚动容器是遮罩本身）；
    滚动位置按 id 记在 `modalScroll`，长弹窗（AI 设置 / 交接包）重开不回首顶；
    `.mhead`/`.mfoot` 是 sticky 且带实色背景，否则滚起来标题下透出内容、按钮条会飘走。
14. **反馈分工**：AI 指令窗里的**过程**留在聊天气泡（那是转录，有历史价值），
    但**结果**必须再走一次 `toast()` —— toast 是全站唯一的 `aria-live` 区域，
    而且弹窗会被关掉。少了这句，"确认执行"到底成没成，键盘/读屏用户无从得知。

## MCP 服务（让代理直接读写看板）

`scripts/mcp_server.py`：stdio 传输、手写 JSON-RPC 2.0、零第三方依赖。

- **实现方式**：直接 import `app.main` 复用业务函数（含字段白名单与 `mutate()` 单临界区），
  **不要再写一套读写逻辑**，否则两套校验会漂移。新增工具要同时动三处：`TOOLS` 条目、
  `HANDLERS` 注册、`TOOL_KIND` 登记读写属性与标题 —— 漏第三处会被
  `test_mcp_server.py::test_every_tool_is_registered_with_a_kind` 拦下。
- **读写属性与只读模式**：`TOOL_KIND` 决定 `annotations`（`readOnlyHint`/`destructiveHint`/
  `idempotentHint`，客户端据此免确认或弹警告）；打开只读模式后 `visible_tools()` 会把写工具与
  生成类（会花真实配额）**直接从 `tools/list` 摘掉**，只在调用时拒绝是不够的 —— 客户端看得见
  就会去调，反复拿到错误像是服务坏了。调用侧 `_ensure_writable()` 保留，作为第二道闸。
- **入参真的校验**：`_validate_args()` 按各工具 `inputSchema` 执行 required / enum /
  additionalProperties / 数值范围。此前这些声明只是装饰，字段名拼错会静默被忽略，
  智能体看到的是"我改了但没生效"。
- **协议兼容**：2026-07-28 起 MCP 改为无状态（无 initialize 握手、结果带 `resultType`），
  旧版（2025-xx）仍走握手。本服务两者都支持，`server/discover` 也实现了。
- **stdout 只能有 JSON-RPC，而且必须是 UTF-8**：日志一律 `print(..., file=sys.stderr)`。
  往 stdout 写一个字都会污染协议。三条标准流在服务启动时自己 `reconfigure(encoding="utf-8")` ——
  MCP 规范就是 UTF-8，而中文 Windows 上子进程默认跟随控制台码页（cp936）：不钉的话
  出方向工具目录里的中文把客户端解崩（整个服务等于没发布），入方向中文标题**静默变成乱码入库**。
  别改成"要求客户端设 `PYTHONIOENCODING`"，客户端不会替你设（钉子：`test_stdio_is_utf8_even_when_the_client_sets_nothing`）。
- **跨进程写安全**：`mutate()` 里除线程锁外还有 `_cross_process_lock()`（Windows 用 `msvcrt.locking`，
  其他平台 `fcntl.flock`）。桌面应用与 MCP 服务是两个进程、同一份 JSON，**少了这把锁就是丢更新**。
- 短命进程不要后台线程：MCP 服务启动前设 `KANBAN_NO_SCHEDULER=1`。
- **代理侧的契约纪律**（改工具时逐条对齐，`test_mcp_server.py` 有对应钉子）：
  - 「工具名不存在」是 JSON-RPC 协议错误 `-32602`（调用方的形状问题）；
    「工具跑了但失败」（含入参校验不过、项目/任务找不到）才是 `isError:true` —— 后者要让模型自己纠。
    两类混成 `isError` 时，客户端会把"名字写错"显示成"这个工具坏了"。
  - `server/discover` 必须回 `supportedVersions`（无状态客户端靠它选版本），
    且 `resultType:"complete"` 的结果要带 `ttlMs`/`cacheScope`；`capabilities` 与 `serverInfo`
    在 initialize / discover 之间共用同一份常量（`CAPABILITIES`），两处各写一遍必然漂。
  - schema 声明的 `maximum`/`enum`/`type` 就是代码里真的执行的那道（`_validate_args`），
    包括 `boolean` —— 声明与实现不一致时，模型以为"要 150 条"而实际拿 50 条，
    它会当成"只有这么多"。
  - 写工具的"部分更新"不许在锁外读快照（见数据层约定 11，走 `kanban.patch_*`）。
  - **resources / prompts 是同一批内容的另一个入口，不是第二套实现**：`resources/read`
    必须复用工具 handler，`prompts` 正文只引用现有工具与资源。指向不存在的对象时报
    `-32602`，不许回一份空壳装作成功；`CAPABILITIES` 里声明了什么，`handle()` 就得真能答。
    只读模式只摘写工具，资源与提示词照旧可用。
  - **创建类工具收 `client_ref`**（数据层约定 13）：智能体超时重试是常态，没有幂等键
    就会造出成对重复任务，而重复项会跟着进交接包和拓扑序。

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

## 数据层几条不显眼但重要的约定

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
   默认跳过并回报 `skipped_existing`，`/api/export/save` 带显式 `path` 且目标已存在时返回
   **409** —— 两处都必须显式 `overwrite=true` 才覆盖。仓库里的入口文件很可能是用户手写的，
   静默覆盖等于内容丢失（`saveAgentsMD` 的默认路径就是 `<仓库>\AGENTS.md`，与"写入项目目录"
   共用界面上同一个勾选框）。不给 `path` 的自动命名导出仍然直接替换：那是本应用自己写过的文件。
5. **记忆层是第二个文件，projects 的规矩一条都盖不到它**：`memories.json` 与
   `projects.json` 分开存，所以它的读-改-写必须走 `_mutate_memories()`（与 `mutate()`
   同一套线程锁 + 跨进程锁），条数受 `MAX_MEMORIES` / `KEEP_MEMORIES_ON_TRIM` 约束（约定 11），
   并且**备份、全量导出、导入、恢复四条路都要带上它** —— 少一处，换机或回滚后
   「决策/坑/约定」这一层就整体消失。删项目要连带清掉该 `pid` 的记忆：
   孤儿既不显示也永远占着上限名额，还会跟着导出跑到别的机器上。
   老导出文件里没有 `memories` 键，导入时必须按"没有这个键 = 不动现有记忆"处理。
6. **依赖图不许有环，三条入口都要拦**：写入路径在 `mutate` 的 `fn` 里判环（见约定 1）；
   `_sanitize()` 里 `_break_dep_cycles()` 贪心剪环，兜住**导入外部 JSON** 与**从备份恢复**
   这两条绕过端点校验的路。环一旦进存储不会报错，只会让 `topo_order` 静默退回原序、
   `executable_tasks` 永远等不齐前置 —— 用户看到的就是"顺序怎么不对/任务永远动不了"。
   剪环按入列顺序保留能保留的依赖，不抛异常（读路径上抛错会把整个看板打不开）。
7. **交接物要报尺寸**：`context_budget()` 给出行数 / UTF-8 字节 KB / 档位（150 行内 ok，
   151~300 warn，再往上 over）。接手的模型受上下文窗口限制，包不是越长越负责 ——
   上游对 CLAUDE.md 的官方建议也是"超过约 200 行遵循度开始下降"。
   出口只有两处：MCP 的 `kanban_handoff`/`kanban_agents_md` 尾部（`_budget_note`，
   只在 warn/over 时说话）与界面交接包弹窗下的计量条。**不要**把提示塞进
   `_build_handoff_markdown()` 本体（会被复制进仓库、也会污染全项目合并包）。
   界面上的阈值是 JS 里另写的一份常量，改 `context_budget` 的默认阈值要同步改 `PKG_BUDGET`。
8. **`_sanitize()` 还负责两条"读读看就自愈"的不变式**：
   - **同一项目内任务 id 唯一**（`_dedupe_ids`，项目级同理）。重复 id 不是显示问题：
     所有按 id 找的逻辑都是 first-match，于是 PUT 只改第一条、DELETE 一次删掉同 id 的几条、
     `topo_order` 少输出一条（交接包丢任务），而 `{t["id"]: t}` 是 last-wins，
     结果真环能以 `has_cycle=False` 入库 —— 直接违反约定 6。
     后来的那条**确定性改名**（`t1~2`）而不是发新 uuid：这一步挂在每次读上，
     随机化会让同一条数据在两次读之间换身份，界面拿旧 id 点进去就 404。
     没有 id 的条目**不补**（导入端点明写并测过"缺 id 或缺名直接丢"）。
   - **`due_date` 归一成 `YYYY-MM-DD`**（只在本来就有值时归一，不给老数据凭空加键）。
     它处处按字符串比较，`2026-9-5` 会排在 `2026-08-01` 之后，于是"今天该干什么"与逾期
     判断一起看错，而且错得看不出来。规范形状先看形状放过 —— 挂在每次读上，
     一万条任务逐条 `strptime` 实测给列表接口加约 20ms。
9. **脱敏（`redact_text`）的边界要说清在哪一侧**：
   - 导出类：`?redact=1` 由用户勾，覆盖交接包 / 全项目包 / 接手页 / AGENTS.md。
   - **送进模型之前**：会话提炼（`_summarize_inner`）与监管·指令的项目简报
     （`_projects_brief` 里的背景 / 下一步 / 过程记录 / 记忆四处自由文本）。
     前者的输入是别人的对话日志（里面躺着贴进去的 Key 很常见），后者是**整板内容被动随
     一次分析请求外出** —— 这两条不是"用户主动把某段文本交给模型"，所以必须默认收口。
     id / 标题 / 状态原样保留，否则模型回传的建议对不上项目。
     而 `ai_generate_handoff` 那类"用户点名让润色自己写的交接说明"的出口**不脱敏**：
     润色结果就是要写回去的原文，盖成星号等于毁内容。
   - 反向闸门：模型看到的文本带着 `MASK_MARK`，就可能把掩码当原文抄回来，
     所以 `apply-suggestions` 遇到带掩码的值**拒绝落地并回报 `skipped_masked`**；
     界面也不许在 `applied=0` 时宣布"已应用 ✓"。
   - **代理侧（MCP）默认就是脱敏的**：`kanban_handoff` / `kanban_agents_md` / `kanban_context`
     都有 `redact`（默认 true，显式传 false 才拿原文），`/api/context/{pid}?redact=1` 同口径。
     理由：代理拿到的文本必然进别的机器上的上下文窗口，而"要不要脱敏"不该由客户端记得问。
     **`_validate_args` 会强制 boolean 类型** —— 别把它放宽成"什么都能塞"。
10. **`usage.jsonl` 的追加与裁剪共用一把跨进程锁**（`llm._usage_locked` → `app/filelock.py`）。
    桌面 exe 与 MCP 是两条进程写同一份账，只有线程锁时 `_trim_usage` 的
    「整读 → 重写 → os.replace」会把读走之后别人刚追加的行一起替换掉（实测吞行），
    表现为 `/api/usage` 长期偏小且无声。
11. **"没给的字段保持原值"必须发生在临界区内**：部分更新走 `patch_project` / `patch_task`
    （它们在读-改-写的同一个锁内取当前值再合并），整体覆盖仍走 `PUT` 的 `update_*`。
    MCP 的三个写工具以前是"客户端先读一整份、改几个字段、再提交整份"，
    那份快照读在锁外 —— 桌面端在这之间改过的字段会被旧值反向抹掉（丢更新，且只在并发时显形），
    顺带每次写还多读一遍盘。`test_patch_task_rejects_illegal_values_and_reads_once`
    钉的就是"一次部分更新只有一次整份读"。
12. **给模型看的声明不能撒谎**：inputSchema 里的 `maximum`、`required`、`enum` 与代码里那道夹
    必须同数（`kanban_search_tasks` 的 50 与 `kanban_today` 的 200 就不一样，别抄错）；
    任何截断都要在返回里留痕（`_projects_brief` 超 40 条任务时带 `tasks_note`，
    `/api/today` 带 `truncated`，搜索满 50 条时在文本里说明）；读不到东西要说"为什么读不到"
    （`/api/context` 的 `code_reality.available` + `reason`）。
    静默截断与静默降级都会让智能体拿一份残缺状态当事实继续写。
    还有一条同类：**数据刚自愈过就要说话** —— `take_recovered_flag()` 现在界面和
    `kanban_list_projects` 两边都能取（一次性标记），否则代理不知道自己在改一份救回来的数据。
13. **创建可以带幂等键**：`client_ref`（HTTP 查询参数 / MCP 工具入参）命中时返回第一次
    建出的那条，并且**一次落盘都不做** —— `mutate_project` 认 `fn` 显式返回 `False`
    表示"没改过"。键表在 `projects.json` 的 `_client_refs`，`MAX_IDEM` 先进先出；
    键指向的记录被删了就顺手作废该键（否则重试会拿一个 404 的 id 当真）。
    导出/导入**不带**这张表：它是本进程的"刚才那次重试"窗口，跨机复活只会吞掉新创建。
    同一个键配不同正文按"重试"处理（与 Stripe 同口径）：返回第一次的结果，不覆盖。

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

## 性能基线（2026-09-19 复测，本机，`scripts/bench.py` 中位数 / 20 轮）

数据层是「整份 JSON 读改写」，延迟随项目数线性增长。`scripts/bench.py` 可复现：

| 数据集 | GET /api/projects | GET /api/stats | GET /api/projects/{pid} | GET /api/export-all | POST 新建任务 |
| --- | --- | --- | --- | --- | --- |
| 1000 项目 / 4.1 MB（1.0.0 时代） | 49 ms | 62 ms | 26 ms | 37 ms | 26 ms |
| 1000 项目 / 4.14 MB（2026-09-18） | 63 ms | 78 ms | 40 ms | 158 ms | 40 ms |
| 1000 项目 / 4.14 MB（本次，含规模优化） | 60 ms | 77 ms | 39 ms | **74 ms** | 42 ms |
| 5000 项目 / 20.74 MB（本次） | 369 ms | 456 ms | 260 ms | 423 ms | 256 ms |
| 5000 项目 / 20.7 MB（旧值，**已证伪**） | 242 ms | 322 ms | 128 ms | 177 ms | 124 ms |

5000 那一行旧值低估了一倍上下（export-all 差 7 倍），别再引用。`/api/projects` 交叉 300ms
那条线的实测位置在 **约 4400 个项目**；常态量级实测：30 个项目四个读接口中位数 2.7–4.0ms、
80 个项目 4.8–7.7ms（每项目 10 任务）。

这一轮的改动都不是"加功能"，是把**只在规模下才成立的浪费**去掉。`export-all` 158→74ms
是两处叠加：逐项目重复读 memories.json 改成一次读+分桶，以及 `topo_order` 不再是
"每层重扫一遍全列表"（后者同时也是 `_build_handoff_markdown` 的成本）。
`topo_order` / `executable_tasks` 的复杂度钉子见 `tests/test_scale.py`，
按**全量遍历遍数**判定，不用耗时断言 —— 后者在 `-n auto` 下会假红（实测同一份代码
空载 0.33ms、跑批时超 4ms 预算）。

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
4. **覆盖率门槛写在 `pytest.ini`**（`fail_under = 90`；当前实测值见文末「验证基线」，
   不在这里重复一个会漂的数字）。
   剩下的未覆盖行基本是 `_is_forbidden_target` 的异常分支、`Path.resolve()` 失败、
   `_scheduler_loop` 的常驻循环等难以稳定触发的路径 —— **别为了数字构造畸形环境硬凑**。
   `test_coverage_gaps.py` 里记着"哪些逻辑真但一直没测到"，新增这类补测往那放。
5. **用例不能依赖墙钟**：涉及时间的断言（调度时间、周期键、截止日期）必须用相对时间构造。
   别写 `at="00:01"` 这种常量 —— 跑到那一分钟就会意外命中，用例变成看运气
   （2026-09-16 凌晨实际踩到：一条调度用例在 00:01 那天红了）。
6. **隐藏元素取文本要用 `text_content()`**：`inner_text()` 只返回"渲染出来"的文本，
   弹窗/折叠区里的文字（如设置面板里的版本号）拿不到，会误判成"没渲染"。
7. **故意制造网络失败的用例别用 `ui` 夹具**：`ui` 把任何 `console.error` 都判失败，
   而 `page.route(...abort())` 或打一个不存在的 id 必然记一条 "Failed to load resource"。
   这类"验证失败态"的用例用 `ui_lenient` —— 它只放过资源加载错误，
   `pageerror`（真正的 JS 异常）照旧一律判红。**别为了绕开红点把 `ui` 改掉。**
8. **用例不许写真实数据目录**（`%USERPROFILE%\.ai-kanban`）。
   `conftest.py` 有 autouse 夹具在**环境变量层**把 `KANBAN_DATA_DIR` 指到临时目录 ——
   因为 `app/llm.py` 是**自己**按这个变量解析数据目录的，不认 `main.data_dir()` 的 monkeypatch。
   历史事故（2026-09-17 实测复现）：`test_llm_gateway.py` 直接调 `llm.chat()`，
   其中的 `_record_usage()` 落到真实 `usage.jsonl`，跑一次测试掺 12 行假数据
   （endpoint「甲」/model「stub-model」），用户的「用量」页面数字随之失真。
   新增直连 `app/llm.py` 的用例时**不要**去掉这层隔离；确实需要真实配置的用例自己
   `monkeypatch.delenv("KANBAN_DATA_DIR")`（如 test_ai.py 的 live 用例）。
   `tests/test_isolation.py` 守着这条。
9. **并行是默认行为**：`pytest.ini` 的 addopts 带 `-n auto`（实测 135s → 41s）。
   慢用例都是真子进程（uvicorn / MCP stdio）与真浏览器，彼此独立，叠起来就是净赚。
   定位单个问题用串行更易读：`pytest -o addopts="-q" tests/test_x.py`。
10. **受管环境可能禁止批量删除**（杀软 / 沙箱 / 企业终端 —— 2026-09-16 在本机实际踩到）：
   pytest 收尾清理 `%TEMP%\pytest-of-*`、以及 pytest-cov 重建 `.coverage`，都会一次删几百个条目，
   被安全策略直接掐掉进程。现象是**跑到 100% 却没有 summary**、或某个用例报 `SystemExit: 1`，
   **不是用例失败、也不是代码问题**。能用的姿势：
   - 分批跑（按文件切，每批 30-60 个用例）
   - `--basetemp .pytest_tmp`（落在项目内，已在 `.gitignore`；`tmp_path_retention_policy=none` 已设）
     ⚠️ **副作用**：临时目录成了本仓库的子目录，git 类用例会往上找到项目的 `.git`，
     "这个目录不是仓库"的断言会**假红**（2026-09-16 踩到两条）。已在 `tests/test_git.py`
     用 autouse fixture 设 `GIT_CEILING_DIRECTORIES=tmp_path`，把向上查找钉住，用例从此与位置无关。
   - 覆盖率数据换名：`COVERAGE_FILE=.cov_x`，且指向**不存在**的新文件
   - 真要全量：交给 CI（没有这层限制）
11. **分批跑覆盖率必须 combine**：分两批各带 `--cov=app` + 各自的 `COVERAGE_FILE`，
   再 `coverage combine .c1 .c2 && coverage report`。
   直接用 `coverage run -m pytest -n auto` 会丢掉 xdist worker 的数据，数字会假低。
12. **规模/复杂度钉子放在 `tests/test_scale.py`，并且只断言"遍历遍数"不断言耗时**。
    语义类用例都在个位数数据上跑，所以"逐个项目多读一次盘""每个依赖重建一次 id 集合"
    这类只在规模下暴露的问题以前一条都没测到。耗时预算在本机就不成立：同一段代码
    空载 0.33ms、`-n auto` 跑批时超 4ms，只会造出假红 ——
    用 `_Counting` 列表数全量遍历的遍数（旧实现 1201 遍 / 新实现 4 遍）是确定的数。
    读盘次数同理：monkeypatch `_load_memories` 计数，比测延迟可靠。
13. **跨语言的声明要有一张对账测试**：Python 侧告诉模型的字段名单（`_CMD_TYPES` /
    `AI_EDITABLE_*`）与前端真放行的白名单（`web/index.html` 的 `AI_PROJECT_FIELDS` /
    `AI_TASK_FIELDS`）是两份文本，靠 `test_frontend.py` 逐字比对。同类还有
    `test_readiness.py`（两份就绪度实现）、`test_agent_files.py`（入口文件目标集合）。
    **凡是"两边各写一份"的东西，都必须有一张测试把它们钉在一起**，否则改一边不会变红 ——
    这个项目已经为此反复出过事故（版本条手填时间、MCP 版本号漂在应用后面）。

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
- **CI 流水线一次都没有真跑过**：仓库没有远端（`git remote -v` 为空），所以 `.github/workflows/test.yml`
  从来没有被 Actions 执行过 —— 它"看起来对"不作数。2026-09-19 逐条静态核对查出三处必红/必空：
  package job 只装 `requirements.txt` 而 pyinstaller 只在 `requirements-dev.txt` 里（下一步
  `python -m PyInstaller` 直接 ModuleNotFoundError）；顶层没有 `PYTHONIOENCODING`，而 scenario 与
  package 步骤要打印中文、还要按 UTF-8 解子进程输出，GBK runner 上是假红；打好的 exe 没有上传，
  打包成功也拿不到产物。**改 workflow 一律按"它会被真执行"来写**；哪天真跑通了，把结论和用时补回这里。
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
- **`release_regression.py --no-ai` 在 GBK 控制台下会假红一条**：它用 `decode("utf-8", "replace")`
  读 `mcp_server` 子进程的 stdout，而子进程在管道里按系统代码页（cp936）输出，断言里匹配的
  「只读」两个字被替换符打断 → 判失败。实测同一份代码：裸跑 17 通过 / 1 失败，
  加 `PYTHONIOENCODING=utf-8` 后 18 通过 / 0 失败。**看板逻辑没问题**
  （`_ensure_writable()` 就在 `t_add_task` 第一行），遇到这条红先查编码，别去改只读开关。

## 当前进度与下一步

**状态：v1.1.0（以 `app/version.py` 为准）、可用。** 下表六轮到 1.0.0 转正为止，1.1.0 的六个面见表格下方说明。演进过程（按提交顺序，细节见各 commit）：

| 轮次 | 主题 | 关键产出 |
| --- | --- | --- |
| 一 | 功能与安全 | 并发丢更新（`mutate()` 单临界区）、状态白名单、Host/Origin 校验、密钥移出项目、示例数据脱敏、调度器防重 |
| 二 | 工程化 | 58 处内联事件收敛为 `data-act`、pytest 套件起步、CI、`git init` |
| 三 | 打包可用性 | 修掉冻结版 exe 起不来（见「容易踩的坑」第一条）、启动日志、HTTP 真探活、`frozen_smoke.py` |
| 四 | 交接三件套 + 自愈 | 8 个工具入口文件、MCP 服务（stdio，13 工具）、跨进程写锁、last-good 自愈 + 恢复入口 |
| 五 | 上下文与排期 | git 集成（只读）、任务 `refs`（代码位置）、任务 `depends_on`（依赖/拓扑排序/可执行任务） |
| 六 | 功能扩展与对标 | 截止日期、看板拖拽、数据导入导出、全局搜索（Ctrl+K）、深浅色主题、脱敏、健康趋势预警、交付演练脚本、真实模型端到端测试、版本号贯通、1.0.0 转正 |

**验证基线（2026-09-19 收口）**：`pytest` 486 收集（480 通过 / 6 跳过，跳过的需 `KANBAN_LIVE=1`）/
覆盖率 94%（语句 2261 / 缺 140，门槛 90，余量 4 个点；只统计 `app/`，`scripts/` 不进覆盖率）。
公开接口面：HTTP 47 条端点（`method+path` 计 48，多的那条是框架自带的 `/openapi.json`）
+ MCP 17 个工具，另有资源（`resources/list` / `resources/templates/list` / `resources/read`，
  4 类 uri）与提示词（`prompts/list` / `prompts/get`，3 个）。
门禁脚本：`release_regression --no-ai` 18/0、`delivery_drill`（自起隔离实例）19/0、
`scenario_test` 33/33、`live_check --mcp` 12/12、`frozen_smoke` 8/0。
并行（`-n auto`，已写进 `pytest.ini`）冷跑约 50 秒、热跑约 20 秒。
⚠️ 全量跑常常**打完覆盖率表就没有 summary 行**（见「测试约定 10」），判绿只认退出码。

> 1.1.0 一次加了会话捕获 / 过程轨迹 / 记忆层 / 用量追踪 / 提醒 / 只读模式六个面，
> 语句数 1630 → 1943，覆盖率一度掉到 92%（余量 2 点）。补了 16 条新面用例后回到 93%。
> **加功能时同步补测试**，否则再叠一个版本就会撞上 90 的门槛。

> 用例数**只在这一处写死**，README 与本文档其他位置引用时写「见验证基线」。
> 数字以命令输出为准：`python -m pytest -o addopts="" --collect-only | tail -1`。
> 每次加测试同步改这一行 —— 散落在多处填数字必然漂（2026-09-16 就同时存在 248/269/278 三个版本）。

**下一步（按价值排序）**：

1. **`app/main.py` 拆分** —— 2971 行单模块（接口/数据层/AI/git/入口文件生成全在一起）。
   拆的时候守住「写操作必须走 `mutate()`」这条，别顺手把临界区拆散。
2. **存储层**：仍是整份 JSON 读改写。触发条件是「列表接口日常 > 300ms」，
   那时直接换分片或 SQLite，**不要加缓存糊**（理由见「性能基线」）。
3. E2E 已覆盖到 AI 交互（报告渲染、指令的人工闸门、提炼落地、测连接）。
   还没进浏览器层的：AGENTS.md / 多工具入口文件的预览与写盘、备份与恢复面板的实际恢复动作、
   监管趋势图。需要哪个就把对应的 `data-act` 点一遍 —— 夹具 `ai_ui` 已经把假端点配好了。
4. 剩下的未覆盖行（数目以「验证基线」为准，不在这里写死）基本是异常分支（`Path.resolve()` 失败、
   系统目录判定异常、各类 500 兜底）。
   要补就得构造畸形环境，收益递减 —— 别为了数字硬凑。
5. **审出来但当下故意没做的三条**（各自都需要一次"用户能不能接受"的判断，别顺手改）：
   - **备份目录里跟着复制了 `config.json`**（`/api/backup` 留 10 份，导入时也会自动备份一次）。
     都在 `.ai-kanban` 内、不违反「密钥不进项目目录」，但"整目录拷走换机"会带走多份明文凭据。
     改法是备份只存掩码后的端点 + 调度/会话源 —— 代价是**恢复后 Key 要重填**，所以要用户点头。
   - **journal 是列表接口最先撞线的维度**：1000 项目 × 10 任务下，journal 从 0 条加到 20 条/任务，
     文件 4.67MB → 81MB，`_load_data` 57ms → 336ms（与项目数无关）。
     收口办法是把 `MAX_JOURNAL` 50→20、`MAX_JOURNAL_TEXT` 500→200 —— **会看不到既有过程记录**。
   - **`_projects_brief` 有两份实现且语义已分叉**（`main` 那份给 LLM、任务 `[:40]` 截断；
     MCP 那份重抄了 `/api/projects` 的计数+就绪度）。合并会同时改两处工具输出，收益不值这个风险。
6. **接口扩面：2026-09-19 已经做过一轮**（用户点头后）。做出来的三样：
   - **MCP resources**：`kanban://projects`、`kanban://board/today` 两个总览 +
     每个项目 `.../handoff`（Markdown）与 `.../context`（JSON，**已脱敏**）；
     `resources/list` / `resources/templates/list` / `resources/read` 都实现了，
     读的时候**复用工具的那批 handler**（`t_list_projects` / `t_today` / `t_handoff` /
     `project_context`），不许出现第二套渲染。理由：交接内容摆成资源就不占每轮对话的工具目录。
   - **MCP prompts**：`takeover` / `daily` / `wrapup` 三个，正文就是原先只写在
     `initialize.instructions` 里的规矩。项目参数按名称解析，**歧义时报错并列候选**，
     不猜。
   - **创建幂等键 `client_ref`**：`POST /api/projects`、`POST /api/projects/{pid}/tasks`
     （查询参数）与两个 MCP 建工具都收；命中时返回第一次建出的那条且**一个字节都不写**
     （`mutate_project` 认 `fn` 返回 `False` 表示"没改过"）。表在 `projects.json` 的
     `_client_refs` 里，`MAX_IDEM=500` 先进先出；键指向的记录被删掉时顺手作废该键；
     **导出/导入不带它**（那是本进程的重试窗口，不该跨机复活）。
   仍然没做、下次要单独决定的：MCP 侧的记忆"列/删"、删项目、全量导出 —— 那是新能力不是深化。
   已判定为负资产、不要再提：游标分页（整份读改写下省不掉解析，只加边界 bug）、
   ETag/If-Match（`mutate()` + 跨进程锁已经串行化了两个写入进程）、
   webhook/SSE（要常驻订阅态与追加型事件表，撞约定 11）、软删除回收站（每条读路径多一个分支，
   "删错了"已经由备份/恢复覆盖）、`outputSchema` 声明式校验与 progress/cancellation
   （前者一旦与文本漂移会被客户端整包拒；后两者要为串行临界区上并发）。

**不要再做的事**（已经想清楚、别回头做）：

- 加更多功能 —— 功能面已经很宽，边际收益在下降，重心应转向验证与稳定性。
- worktree 并行隔离、团队协作与权限 —— 不是这个项目的赛道（见 README「和同类工具比」）。
- 给 `CLAUDE.md` 复制正文、给每个工具各存一份约定 —— 必然漂移，见「多智能体入口文件」。

