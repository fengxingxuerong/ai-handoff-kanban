# AGENTS.md — AI 交接看板

> 给 AI 编码代理的项目上下文。只写代码里看不出来的信息：命令、约定、坑、进度。

## 这是什么

多项目任务看板桌面应用，为「频繁切换 AI 智能体做项目」设计：每个项目带交接说明与下一步行动，
一键导出交接摘要 / 交接包 / AGENTS.md，让新智能体接手零成本。内置多端点 LLM 自我监管。

技术栈：FastAPI + 原生单页前端 + PyWebview 桌面壳，PyInstaller 打成单个 exe。

## 目录与职责

| 路径 | 职责 |
| --- | --- |
| `app/main.py` | 全部 HTTP 接口 + 数据层 + 监管/指令/交接/AGENTS.md 生成 + 调度线程。**2026-09-26 已拆出 7 个纯模块（3596 → 3206 行），本文件只留路由编排与有全局状态的部分** |
| `app/taskgraph.py` | 任务依赖图：`clean_deps` / `task_graph` / `has_cycle` / `topo_order` / `_break_dep_cycles` / `executable_tasks` / `_dedupe_ids` / `clean_refs`（纯函数，从 main 逐字搬来） |
| `app/due.py` | 截止日期归一 `_clean_due_date` 与状态判定 `_due_state`（纯函数） |
| `app/redact.py` | 交接内容脱敏规则与 `redact_text`（纯函数） |
| `app/idem.py` | 创建幂等键 `client_ref` 的清洗与记账（纯函数） |
| `app/handoff_meta.py` | 交接包小节解析 `parse_handoff_sections` 与尺寸预算 `context_budget` / `_budget_note` |
| `app/sessions.py` | 会话扫描与文本提取（目录遍历有界、只读文件尾部、源内外校验） |
| `app/gitinfo.py` | git 集成（2026-10-06 从 main 搬出）：`git_snapshot` / `_run_git` / `_git_cache`。只读、列表传参、绝不拼 shell。**`_run_git`/`_git_cache`/`GIT_TIMEOUT` 的 monkeypatch 点在这里**（main 里是转发别名） |
| `app/agentfiles.py` | 多智能体入口文件投影（`AGENT_ENTRY_TARGETS` 白名单 → 各工具要求的相对路径，含第 9 个 `.claude/skills` 投影） |
| `app/llm.py` | LLM 网关：端点排序、多 Key 轮询、失败降级、配置读写（含锁） |
| `app/filelock.py` | 跨进程文件锁（msvcrt/fcntl 一份实现，`main` 与 `llm` 共用）。两边不能互相 import，所以锁不放在任何一边里 |
| `app/handoff_page.py` | 自包含「接手页」单文件 HTML（含分层依赖图 SVG）；**不 import main**（main 会 import 它，反过来成环） |
| `app/handoff_md.py` | 交接包 Markdown **纯渲染器**（2026-10-06 拆出，依赖注入式）：`build_handoff_markdown` 只收现成数据（活跃记忆/过期计数/git 回调/端口），不 import main、不读盘、不跑子进程；原签名 `_build_handoff_markdown` 是 main 侧的注入包装 |
| `app/agentsmd.py` | AGENTS.md 文档生成（2026-10-06 拆出，同法注入）：本地兜底版收 `memory_rows`+`now`，AI 版的记忆与兜底经回调注入；`_build_agents_md_local` / `_generate_agents_md` 是 main 的原签名包装 |
| `app/paths.py` | 数据目录定位（2026-10-06 拆出）：`data_dir` / `data_file`，所有存储模块的共同根 —— 后续拆出的模块从这里取，不必反向 import main。环境变量每次调用时读 |
| `app/aireports.py` | 监管报告的落盘/裁剪/历史/趋势（2026-10-06 拆出）：`reports_dir` / `_save_report` / `_trim_reports` / `_latest_report_before` / `_attach_trends` / 提示词构造。**`MAX_REPORTS` 的 monkeypatch 点在这里**（main 是转发别名）；`_run_analyze` 编排留在 main |
| `app/aiprompt.py` | AI 编排的只读计算层（2026-10-06 拆出，依赖注入式）：看板简报 `projects_brief`（记忆分桶/git 探测经回调注入，main 的 monkeypatch 照常有效）与会话提炼 `summarize_inner`（find_project 注入）。**不落库** —— 写动作仍走 main 的 mutate() 临界区 |
| `web/index.html` | 全部前端（单文件，无构建、无 CDN 依赖，交互统一走 `data-act` 事件委托） |
| `tests/` | pytest 套件（用例数见文末「验证基线」，此处不写死）+ 内置假 LLM 端点（`llm_stub.py`），覆盖率门槛 90%（实测数字只写在文末基线那一处）。含真实浏览器点击测试 `test_frontend_e2e.py` |
| `scripts/desktop.py` | 桌面壳：补标准流 → 探测端口 → 后台起 uvicorn → 真实 HTTP 探活 → 开 Pywebview 窗口；启动过程写 `<数据目录>\startup.log`。`KANBAN_NO_WINDOW=1` 时只服务不开窗（CI / 服务器 / 调试用） |
| `scripts/live_check.py` | 对着**已在跑**的实例打接口做只读体检（不写数据）；替代了原来的三个手工冒烟脚本 |
| `scripts/live_ui_check.py` | 对着**已在跑**的实例开真浏览器做只读校验（Playwright + 系统 Edge/Chrome）：证明界面真的渲染出来了，不是白屏。全程只点导航类操作，不写一个字节 |
| `scripts/scenario_test.py` | 真实场景测试 32 例（真服务 + 真 MCP + 真实业务数据，可导出报告） |
| `scripts/delivery_drill.py` | 交付演练：模拟换智能体接手，端到端 21 项（接手→执行→交接物→脱敏→迁移）。**不传参数时自起隔离实例**；传 base_url 则先读该实例 `/api/health` 的 `data_file` 自证不是真实数据目录，否则拒绝运行（演练会用 `/api/data/import` 整份覆盖） |
| `scripts/real_ai_test.py` | 真实模型 AI 功能测试：5 大功能 × 6 模型通道（消耗真实配额） |
| `scripts/release_regression.py` | 发版回归：自起独立实例（不碰真实数据），v1.1 新功能全链路（`--no-ai` 下 16 项真断言 + 2 项标「未验」；不带 `--no-ai` 才会跑 AI 链路）；真实 Key 从 `KANBAN_REAL_DATA_DIR` 指向的配置读取 |
| `scripts/test_all_llm.py` | LLM 端点全量实测：端点×Key×模型 健康矩阵 |
| `scripts/make_icon.py` | 纯标准库生成应用图标 app.ico |
| `scripts/frozen_smoke.py` | **冻结版自查**：启动 dist 里的 exe，真打一遍接口，自动关掉（每次打包后必跑）。除了活着，还钉三件事 —— 产物是不是**新的**比产物能不能跑更容易出问题：① exe 自报的 `version`/`build_time` 与仓库里的 `app/version.py`、`_build_stamp.py` 同源；② **首页返回的字节与 `web/index.html` 逐字节一致**（专治「改了前端忘了重打包」）；③ **构建戳之后 `app/*.py` 或 `web/index.html` 有没有再被改过**（后端打进 .pyc，没法比字节，只能按时间拦）。没有它们就是"测试全绿、发出去的 exe 少几个修复" |
| `scripts/mcp_server.py` | MCP 服务（stdio，23 工具 + 资源 + 提示词）：代理可列项目/拉接手包(context)/读写任务与记忆/删任务删项目/生成交接物/导出全项目包/拍数据快照，交接内容与接手包可 `resources/read`、接手流程可 `prompts/get`。每个工具在 `TOOL_KIND` 里登记读写属性与标题（决定 `annotations`，也决定只读模式下是否出现在 `tools/list`），入参按各自 `inputSchema` 的 required/enum/type/additionalProperties 真的校验；`kanban_delete_project` 是唯一要求 `confirm=true` 的写操作，**恢复快照故意不做成工具** |
| `scripts/bench.py` | 读写性能基准（先量再改） |
| `scripts/mutation_probe.py` | **变异探针**：往 `app/` 注入 41 处语义改动（临期窗口、Origin 反转、脱敏漏规则、journal 封顶、幂等键截断…），逐条跑相关用例；**改了还全绿 = 盲区，判失败**。覆盖率回答"这行跑过没有"，它回答"这行错了会不会红"。2026-09-29 把存活数从 12 清到 0（大文件拆分后 killer 清单没跟上搬家 + 6 处真盲区补测），2026-10-01 随「待审查」新增 readiness_active_narrowed 探针（旧 old 串被改掉后替换不中，探针变哑火——教训：改被探针盯着的代码要同步改 `old`。kill 用例：待审查任务也算推进，只认进行中即掉 10 分）。`ci_local.py` 的 full 模式含这一步（`--quick` 不含）；新增业务规则时**顺手往 `MUTATIONS` 里加一条**，否则新规则等于没有门禁 |
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

rem 一条命令跑完所有门禁（自己起隔离实例，绝不碰 ~/.ai-kanban）
rem 这是 CI 的本地等价物，也是**发版前的唯一判据**；--with-exe 会重新打包并跑冻结版冒烟
.venv\Scripts\python.exe scripts\ci_local.py
.venv\Scripts\python.exe scripts\ci_local.py --quick
.venv\Scripts\python.exe scripts\ci_local.py --with-exe

rem 依赖可复现：requirements*.txt 写的是兼容下限，requirements.lock.txt 是实测闭包
.venv\Scripts\python.exe scripts\make_lock.py        # 在跑过全量测试的 venv 里重新生成
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt   # 换机 / CI / 出包用

rem exe 版本资源（右键属性看得到版本与构建时间；由 build.bat 第 5 步自动调用）
.venv\Scripts\python.exe scripts\make_versionfile.py

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
6. **状态与优先级只能取固定值**：项目 `规划中/进行中/已暂停/已完成`，任务 `待办/进行中/待审查/已完成/阻塞`，
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
15. **整页只有一段内联脚本，它一崩所有 `data-act` 全部失效、界面变成一块死面板，
    而任何 Python 测试都不会红**。2026-09-20 实测：在 JS 里写了 Python 风格的相邻字符串
    拼接（`"甲" "乙"` —— **JS 没有隐式拼接**），500 多条测试全绿，直到真开浏览器才现形为
    `ReferenceError: openDetail is not defined`。门禁：`test_inline_script_is_syntactically_valid_js`
    抽出每段 `<script>` 交给 `node --check`。改完前端别只跑 pytest，至少开一次真浏览器
    （`scripts/live_ui_check.py` 就是干这个的）。
16. **编辑表单必须从单项目接口回填，不能用总览列表里那一份**：
    `GET /api/projects` 只给卡片要用的字段，**不含** `background` / `handoff_note`，
    而 `PUT /api/projects/{pid}` 是整体覆盖语义 —— 从列表回填就等于"一保存就清空这两段"
    （实测：从总览点编辑、只改一下进度、保存，用户手写的三周背景没了，界面毫无痕迹）。
    取不到详情时**宁可拒绝打开表单**也不要开一个"保存即吞内容"的表单。
    静态钉子：`test_project_edit_form_is_filled_from_the_single_project_endpoint`。
17. **界面上任何"结论性文案"的判据必须真的包含它所断言的东西**。详情页的
    「看板说的 vs 代码里真发生的」以前只写 `days>=7 && dirty==0` 两个常数，
    却打出"看板还在推进，代码却停了？"—— 判据里根本没有一项关于看板是否推进，
    于是 progress 停在 0%、三个月没动的项目同样喊这句话。现在两边各自成立才下结论
    （代码侧停滞 **且** 看板侧近 7 天真有动静；反向"代码刚提交、看板停在几周前"同样要说），
    并且**没分歧也要说一句对得上**（空白会被读成"没在检查"）。
    同类：异步才算完的结论区要先占位（"正在核对…"），否则中间那半秒是"两块卡片、没有结论"，
    会被读成"没查出问题"。
18. **`data-act` 带 `data-arg` 时，处理器必须真的收参数**：分发器是 `fn(el.dataset.arg, ev, el)`，
    写成 `openReport: () => openReport()` 会把 `rid` 丢掉 —— 而 `openReport(rid)` 里
    `rid` 是"回看某份历史报告"与"重新跑一次分析"的唯一开关，丢掉后**按钮看着有反应，
    做的却是另一件事**，截图都看不出差别。门禁：
    `test_actions_that_receive_data_arg_actually_forward_it`。
19. **"列表给的 id"必须能被"详情按 id 取"取回来**：`GET /api/ai/reports` 以前回的是
    **正文里的 `id` 字段**，而 `GET /api/ai/reports/{rid}` 是按**文件名** + `_REPORT_ID_RE`
    取文件的 —— 两边口径一分叉，界面上"点一条历史报告"就必然 404（做前端历史入口时真撞到）。
    现在列表用 `f.stem` 当 id，且**跳过文件名不合法规的条目**（宁可不列，也不列一个点不开的）。
    门禁：`test_every_report_id_in_the_list_is_fetchable`。
20. **同一张图/同一份渲染只允许有一份实现**：应用内的依赖图走
    `GET /api/projects/{pid}/graph`，它复用接手页那份 `handoff_page.dep_graph_svg`
    （只是多了 `theme` 两个填色参数）。**不在前端再画第二遍 SVG** —— 本项目反复出问题的
    形状就是"两条路给不同答案"（两份 `_projects_brief`、MCP 重算一遍 `/next`）。
    图 SVG 全程 `html.escape`，可以安全进 `innerHTML`（钉子：标题里放 `<img onerror>` 断言不原样出现）。
    另外两个诚实性细节：没有依赖时接口回 `empty` + **原因**（别让调用方拿空串猜是坏了还是没依赖）；
    层级超过 `MAX_COLS` 时回 `collapsed`，界面注明"末列内可能仍有先后"—— 图上看着像并行。
21. **发版判据只有一条命令：`scripts/ci_local.py`**。它自己起隔离实例（临时数据目录 + 空闲端口），
    跑 pytest + 五道脚本门禁 + 真浏览器 + 真 MCP，**每步取真实退出码、中途失败不短路**，
    最后打印一张表。别在外面手敲六条命令然后说"全绿"——那份清单没人能复算。
    两个已踩过的前提：
    - **包装层的 env 要分两套**。被测步骤用干净 env，只有"要起服务/要连服务"的步骤才带
      `KANBAN_PORT`。把运行开关漏进被测 env，会造出与代码无关的假红
      （`KANBAN_NO_SCHEDULER` 打死调度器测试、`KANBAN_PORT` 打死断言默认端口 18320 的直链测试）。
    - **预期内的 4xx 要转成正向断言，不要过滤掉**。详情页自动读 git，路径不存在时后端故意回 400；
      门禁以前因此假红。现在单独收这一路并断言"面板说清了读不到的原因"——
      既不放走真故障，也不逼产品代码为了门禁改契约。
22. **依赖声明两份文件各管一件事，别混**：`requirements*.txt` 是**兼容下限**（`>=`，人写的意图），
    `requirements.lock.txt` 是**实测闭包**（`scripts/make_lock.py` 生成，换机/CI/出包用它）。
    只留 `>=` 的话，"我这边是绿的"在别人机器上可能是另一套版本 —— 而这是个卖点是"交接"的工具。
    CI 工作流（`.github/workflows/ci.yml`）**按锁文件装、跑同一条 `ci_local.py --with-exe`**，
    且只在 `windows-latest` 上跑：发的是 Windows 桌面 exe，拿 ubuntu 测的是永不交付的路径。
23. **exe 的版本资源与 `/api/health` 必须是同一个事实**。`scripts/make_versionfile.py` 从
    `app/version.py` 取版本号、从 `make_stamp.py` 写的构建戳取时间，填进 PE 的
    `FileVersion`/`PrivateBuild`，于是右键属性能看到的数字和程序里报的一致。
    这里刚出过一个反例：第一版直接用了 `version.BUILD_TIME`，而它在源码态算的是
    "源码最后修改时间"（这本身是对的），结果同一次构建里构建戳 17:30、版本资源 15:40 ——
    **版本资源要的是"这个 exe 几点打的"**。验证要读回二进制（PowerShell `VersionInfo`），
    别只看生成脚本没报错；且读中文资源值时路径要纯 ASCII，否则 PowerShell 会
    非终止地报错却仍返回 0，把"验过了"变成空字符串蒙混过去。
24. **任务验收标准（`acceptance`）是一个跨层字段**（2026-10-06 吸取 Backlog.md 的 DoD 清单）：
    上限只有一对数（`MAX_ACCEPTANCE_ITEMS` / `MAX_ACCEPTANCE_TEXT`），界面提示、TaskIn 清洗、
    MCP `_clean_notes` 全用它们（对账钉子：`test_acceptance_hint_numbers_match_the_backend`）；
    落点有五处缺一不可 —— 交接包 Markdown（`- [ ]` 清单）、接手页任务表（`☐`）、接手包
    `slim.acceptance`（**已完成的不带**，它已经验过了）、`kanban_get_project` 的任务行下方
    `- [x]`、任务卡计数徽章。增量动词 `acceptance_add/remove` 走 `COLLECTION_OPS` 同一套机制，
    整体值与增量同给必须 400。探针：`acceptance_no_cap`。
25. **journal 超限是「折叠成摘要」，不是「静默丢弃」**（2026-10-06 吸取 Beads 语义压缩）：
    `compress_journal()` 纯函数，溢出的最旧条目折成一条带 `folded` 计数的摘要放在最前，
    `folded` 永远指**原始记录数**（再次溢出时继承累计）；`clean_journal()` 在读路径上做同一件事，
    所以导入的脏数据也折叠。前端 `.j-folded` 样式区分，交接包/接手包无需特殊处理。
    **教训重演**：`clean_journal_no_cap` 探针盯的旧代码（`raw[-MAX_JOURNAL:]`）随这次改动消失
    而没有同步改 `old`，探针就哑火了 —— 改被探针盯着的代码必须同步改变异表（现在是
    `journal_fold_dropped`）。
26. **记忆有保质期就标 `expires`，过期不召回且省略要留痕**（2026-10-06 吸取 OKM 知识代谢）：
    `expires` 只认 `YYYY-MM-DD`（到期**当天仍有效**，判定在 `_memory_expired` 一处）；
    四个出口 —— 交接包、接手包、AGENTS.md（`_project_memories`）、模型简报 —— 都不召回过期记忆，
    交接包省了几条要在正文开头说（否则"没写"会被读成"没有"），接手包顶层 `expired_memory_count`
    同理；界面划线标注、MCP 列表带 `⚠️已于…过期`。`expires` 格式不对就留空 = 变成长期有效，
    `kanban_memory_add` 必须在回执里说破（与 `due_date="下周三"` 同一类"丢得毫无声音"）。
    探针：`memory_expiry_ignored`。
27. **🔔 待拍板（`/api/inbox`）只列"等人的"**（2026-10-06 吸取 Multica 的 Inbox 哲学）：
    逾期 / 待审查 / 阻塞三类，一条任务只进一类，优先级 待审查 > 阻塞 > 逾期（最接近完成的在前）。
    **依赖没满足的任务不算**（它会随前置完成自然流转，那是「今日」的事）、临期不算（横幅已管）、
    **已完成的绝不能出现在任何一类里**（探针：`inbox_counts_completed`）。界面徽标数量来自
    同一个接口，别在前端拿 task_counts 另算一份。
28. **入口文件现在是 9 个投影**：第 9 个是 `.claude/skills/ai-kanban/SKILL.md`（Agent Skills，
    2026 起的技能包格式）。frontmatter 的 `name` 必须与目录名一致 —— 都取自 `SKILL_NAME`
    一份常量，别写两处。Skill 是投影不是第二份内容（正文仍是 AGENTS.md 那一份）。

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
  - **代理侧的脱敏收口在 `tools/call` 的分发处（`_tool_content`），不在各个 handler 里**。
    约定 9 说"代理侧默认脱敏"，而"23 个 handler 各自自觉"= 新增一条就漏一条：
    实测摘掉这一层，`kanban_list_projects` / `find_project` / `stats` / `update_project` /
    `delete_project` 立刻把带密钥的**项目名**原样回给代理（项目名那一路以前从来没盖过 ——
    补标题、补 next_action 时谁也没想到它）。报错正文同样过出口。
    handler 里原有的 `_agent_text` 保留：掩码对同一串重复施加是幂等的。
    钉子：`test_no_agent_reply_can_carry_a_key_stored_in_the_board` 把密钥写进**存量**位置
    （调用方这次没提供的那些字段），23 个工具逐个走真 `tools/call`；
    表里漏考任何一个工具也直接红（这条当场抓出我少考了 `kanban_agents_md`）。
    判据必须是"回执里没有原文"，不是"有掩码" —— 后者会因为内容本不该出现掩码而假绿。
  - **"现在能做什么"只有一份判定**：`kanban_next_task` 直接调 `next_tasks()`（也就是
    `GET /api/projects/{pid}/next`）。以前它自己 `_find()` 读一份 + `executable_tasks` +
    手搭 blocked —— 一次调用两次整份读盘，而且是第二套实现，改一边就会让代理看到的
    "可执行"和界面给的不一样。结构性钉子：
    `test_next_task_tool_delegates_instead_of_recomputing`。响应里的 `total`/`done`
    是用来分清"没任务 / 都完成 / 在等前置"三种情况的（文案要不同，别笼统说"都在等前置"）。
  - **resources / prompts 是同一批内容的另一个入口，不是第二套实现**：`resources/read`
    必须复用工具 handler，`prompts` 正文只引用现有工具与资源。指向不存在的对象时报
    `-32602`，不许回一份空壳装作成功；`CAPABILITIES` 里声明了什么，`handle()` 就得真能答。
    只读模式只摘写工具，资源与提示词照旧可用。
  - **创建类工具收 `client_ref`**（数据层约定 13）：智能体超时重试是常态，没有幂等键
    就会造出成对重复任务，而重复项会跟着进交接包和拓扑序。
    键**超长要哈希不能截断**：以前 HTTP 侧静默 `[:80]`，两条前 80 字符相同的不同请求
    撞成同一个键、第二次直接拿回别人那条的 id（实测）。命中与否还要在回执里说明，
    "id 和上次一样"不构成可辨识的信号（以前新建与重放的文案一字不差）。
  - **集合字段给三件套**：`tags` / `refs` / `depends_on` 除整体覆盖外另有 `<字段>_add` /
    `<字段>_remove`。只有整体覆盖时，代理加一条就得"读整份 → 拼好 → 写回"，而那次读在
    临界区**外** —— 期间桌面端加的条目会被整体写回抹掉，正是约定 1 的丢更新形状
    （Backlog.md 的 `task_edit` 动词族、GitHub labels 的 PUT/POST/DELETE 同一解法）。
    展开成整体值必须发生在 `patch_*` 的锁内（`_expand_ops`，由 `_merge_into` 调用）；
    整体值与增量同时给 = 语义冲突，报 400 而不是猜；用在对象没有的字段上也报 400。
  - **`_budget_note` 说"偏长建议裁剪"，就必须真有一条裁剪的路**：`kanban_handoff` /
    `kanban_export_all` 与对应 HTTP 端点收 `omit_sections`（`git`/`journal`/`memories`/
    `refs`/`done`，见 `HANDOFF_SECTIONS`）。不认识的名字要报错，不能静默当"没给"；
    裁掉什么要在正文**开头**留一行说明，否则接手方会把"没写"读成"没有"。
    `omit` 含 `git` 时连 `_git_line_for_handoff` 的子进程一起跳过。
  - **改了状态要说清"放行了谁"**：`kanban_update_task` 把前置改成已完成时，回执带
    `已解锁 …[id]`（`patch_task(with_project=True)` 在锁内前后各算一次 `executable_tasks`）。
    代理否则要再问一遍 `kanban_next_task` 才知道下一步做什么。
  - **清洗丢了东西要在回执里说话**：`_clean_notes(args, stored)` 比对"调用方给的"与
    "盘上落下的"。规则本身（日期只认 `YYYY-MM-DD`、refs/depends_on 去重限量）是对的，
    问题出在以前丢得毫无声音 —— 代理传 `due_date="下周三"` 会收到"已新增任务"，
    它以为设上了。只在 MCP 侧报：前端是日期控件，HTTP 侧没有可行动的对象。

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
     2026-09-20 补齐：`kanban_get_project` / `kanban_today` / `kanban_search_tasks` /
     `kanban_next_task` 以前**连这个开关都没有**（而 `kanban_get_project` 正是
     `initialize` 指引让代理第一个调的），现在四条统一走 `_agent_text()` 出口脱敏；
     `kanban_list_projects` 的 `next_action` 也补上了。新增代理读路径时默认走 `_agent_text`，
     schema 里的 `redact` 用共享常量 `REDACT_SCHEMA`（同一句描述写五遍必然漂）。
     钉子：`test_every_agent_read_path_masks_by_default`。
   - **模型简报里的任务标题现在会盖**，前提是 `_projects_brief` 的每条任务都带 `id`、
     且 `ai_command` 的提示词要求"按 `project_id` + `task_id` 回传、不要拿标题当匹配键"。
     这三件事是一个整体：**去掉任务 id 就会退回到按标题匹配，标题立刻盖不得**，
     于是"密钥写在任务标题里"这条最常见的写法又会从模型路径漏出去。
     配套：`GET /api/tasks/{tid}`（只有任务 id 时反查所属项目）、前端 `findProjectOfTask`
     走它。钉子：`test_projects_brief_gives_task_ids_and_masks_titles` 与
     `test_ai_command_prompt_keys_on_ids_not_titles`。
   - **任务标题在所有"外出"路径上都是盖的**：接手包（`project_context` 的 `slim` /
     `start_here` / `waiting_for`）、交接物（整篇 Markdown 后置盖）、模型简报（`_projects_brief`）。
     以前曾经只盖 desc/journal，于是"轮换 sk-xxx 这把"这种标题（最常见的那种写法）
     带着密钥从默认脱敏的出口漏出去；掩码只替换凭据那一段，标题照样认得出来，
     对齐靠 id 而不是靠标题。
     历史教训（2026-09-19 到 2026-09-20 之间）：简报这一度是**唯一留原文**的一条，
     原因是提示词写着"任务用现有任务标题匹配"、而简报里的任务没有 id。
     现在两边都改掉了 —— 别再以"要对齐标题"为理由把标题恢复成原文。
   - **"送进模型之前"这一侧也算输出边界，不止返回体**：`_generate_agents_md` 以前只对
     返回的正文盖掩码，送给模型的 `user_msg`（背景/交接说明/下一步/任务标题/仓库路径）
     整份原文 —— 而这条是把项目状态**被动**推给远端模型去写文档，不属于"用户点名润色
     自己写的文本"那个豁免档。钉子：`test_agentsmd_masks_what_it_sends_to_the_model`
     （断言的是**发出去的 payload**，不是返回值 —— 只看返回值会放过整类缺陷）。
     同类还有 `_projects_brief` 的 `git.last_commit_subject`：同函数四处都过了脱敏，
     独它没有，而 commit 正文同样是自由文本。
   - **脱敏文档里的直链必须同口径**：`_build_handoff_markdown(redact=True)` 与接手页
     `build_handoff_page(link_suffix="?redact=1")` 都要给那三条实时直链带上后缀，
     否则"已脱敏的交接物"递给对方的同时递了三条取原文的路。MCP 的 `kanban_context`
     早就这么做了（`link_suffix`），HTTP 侧是后补的 —— 又是两条路给不同答案。
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
14. **机器可读出口要声明结构版本**：`SCHEMA_VERSION` 出现在 `/api/context/{pid}` 与
    `/api/data/export` 的顶层，与发布号 `APP_VERSION` 是两件事（"字段形状是哪一版" vs
    "看板第几版"）。导入时若文件声明的版本比本机高**只说话不拒绝**（`schema_note`）——
    字段是超集，硬挡等于把用户关在门外，但事后发现字段被规范化掉更糟。
    接手包顶层与 `project` / `tasks` 的字段集合由
    `test_context_package_field_set_is_pinned` 钉住：加字段要有意为之
    （v2 = 2026-10-06：任务 slim 带 `acceptance`、顶层带 `expired_memory_count`）。
15. **同一条清单不许前后端各写一份**：导出扩展名白名单是 `EXPORT_SUFFIXES`（后端），
    前端 `saveAgentsMD` 那条正则必须与它一字不差 —— 以前前端少一条 `.markdown`，
    后端明明允许却被前端挡在门外，而提示文案同时漏了 `.agentsmd`。
    对账钉子：`test_save_extension_whitelist_matches_the_backend`。
16. **"模型建议 + 人点一下"这条通道的闸门**（`/api/ai/apply-suggestions`）：
    白名单 `SUGGESTION_FIELDS` 比 `AI_EDITABLE_PROJECT_FIELDS` 窄是**有意**的（只放自由文本
    与状态/优先级，改 name/tags/progress 要走用户自己打出的 `ai_command`）；
    值有 `MAX_SUGGESTION_VALUE` 上限（整份 JSON 每次请求都整读整写，模型抽风吐 40 万字符
    就会永久长在数据里 —— 实测改前能落 40 万字）；带 `MASK_MARK` 的值拒绝落地；
    回执是**逐条**的 `results[{index,ok,reason}]`（只给 `applied=6` 的话，那 2 条没落地的
    没人知道是哪两条、为什么），`?dry_run=1` 一个字都不写。
    `applied` / `skipped_masked` 两个老键保留，前端照旧读、额外优先显示 `results[0].reason`。
17. **"整份覆盖"的两个入口共用同一道筛选**：`_writable_projects` / `_writable_memories`。
    导入以前逐条筛 `id`/`name`、恢复只看"是不是列表" —— 于是从恢复这里放进一条缺 id 的项目，
    之后**每一个读接口都 500**（读路径按 `p["id"]` 硬下标取值，而 `_sanitize` 明写不补 id），
    主文件又是合法 JSON、自愈不介入：救命的退路变成一键变砖。丢掉的条目必须报出来
    （`dropped_invalid` / `dropped_note`，恢复前的预览还先给 `unusable_count`）——
    "少了两个项目"和"一个没少"在界面上长得一样。
    同处两条：`projects.json.before-restore.bak` 是**正式恢复来源**（以前只在写入处硬编码一次，
    白名单不认识它 = 界面向用户承诺的退路无路可走）；恢复**不复活** `_client_refs`
    （约定 13：那是本进程的重试窗口，导入已经不带你了）。
18. **部分更新只有一份实现**：`patch_project` / `patch_task`（补全发生在 `mutate` 锁内）。
    MCP 的 `kanban_update_*` 与 HTTP 的 `PATCH /api/projects/{pid}`、
    `PATCH /api/tasks/{tid}` 都走它们，集合字段的 `tags_add` / `refs_remove` 两侧一致可用。
    **改一个字段绝不许"GET 整份 → 改一处 → PUT 整份"**：那次 GET 在临界区外，
    期间另一方写进去的字段会被手里这份旧快照反向抹掉。前端因此必须用 PATCH
    （拖卡片改 `status`、AI 指令改单字段、写交接说明 —— 2026-09-23 之前全都在发整份 PUT，
    实盘复现过"拖一次卡片把智能体刚补的 refs/desc 清空，界面还提示 ✓"）。
    `PUT` 保留给"表单本来就拥有全部字段"的整份保存（新建/编辑弹窗），别混用。
19. **不认识的增量动词直接报错**：`_expand_ops` 先拒 `*_add` / `*_remove` 里基字段不存在于
    `COLLECTION_OPS` 的键。`tag_add`（少个 s）以前一路走到"只认模型字段"那道过滤被丢掉 ——
    200 加一句"已更新：tag_add"，谁都不说这个字段一个字没变。
20. **"模型给一批 + 人点执行"这条通道的幂等键**：`/api/ai/command` 与 `/api/ai/summarize`
    都回一个 `batch_ref`，前端在**进入执行时捕获一次**，建项目/任务时带
    `client_ref = batch_ref:序号`。两个反例都踩过：不清键 → 下一次执行撞同一把键；
    拿全局兜一个固定前缀 → **下一条**正常指令的第一条创建动作被判成重放而静默不建
    （静默不建比重复建更难查）。拿不到 `batch_ref` 就不带键，退回"可能重复"。
21. **过程记录的上限只有一个数**：`MAX_JOURNAL_TEXT` 同时是 HTTP 入参上限、MCP schema 的
    `maxLength`（由常量生成）、落盘截断长度、界面 `maxlength`。以前是"入参收 2000、
    落盘偷偷截到 500"—— 一条 1200 字的记录少掉 700 字（往往正是"为什么这么决定"那段），
    而回执写着"已记录"。**超限现在在边界被拒，而不是被静默裁**。
    对账钉子：`test_journal_limit_is_one_number_everywhere`（后端自己三个声明位）
    与 `test_journal_input_box_and_backend_share_one_limit`（界面向后端常量对齐）。
22. **schema 里写了的约束就必须真的执行**：`_validate_args` 以前只对 integer 执行
    `minimum`/`maximum`，string 的 `maxLength` 是写给客户端看的装饰 —— 于是"声明 500、
    实际收 5000、再被落盘截成 500"两侧都不报错。现在 integer 与 string 同等成立。
23. **记忆必须落在真存在的项目上**：`add_memory` 拒绝不存在的 `pid`（留空是**有意**的另一档：
    跨项目通用记忆）。挂在不存在项目上的记忆进不了交接包/接手包（两处都按 pid 过滤），
    只会被 `delete_project` 当孤儿清掉，而回执照样说"已记下 [决策] 到 <pid>"。
    同时 `kanban_memory_add` 那句"会在交接包、AGENTS.md 和 AI 监管里自动召回"现在是真的：
    `_project_memories` 按 pid 取最近 `AGENTS_MEMORY_LINES` 条，本地兜底版与送给模型的
    素材都带上它（送出前一并 `redact_text`）。
    **承诺与实现不一致时，优先把实现补齐，补不齐才收窄措辞** —— 反过来做就是让用户
    按错的说明办事。（这一改动顺带让 3 条老用例失效：它们一直往编造的 `pid="p1"` 上写记忆，
    现在改成留空 = 通用记忆，那三条测的是备份/并发/上限，本来不关心归属。）
24. **截断必须留痕，不许让调用方猜**：`/api/search/tasks` 回 `truncated`/`limit`，
    MCP 的措辞（"已到本次上限 N，后面还有"）与 Ctrl+K 的提示条都按它说；
    以前 MCP 那句"已到上限 50"是按一个常量猜的（默认 limit 是 30，两边都在猜对方的数）。
    `resources/list` 超过 `RESOURCE_LIMIT` 时占一条 `kanban://truncated` 说明条目
    （结构化列表没地方写脚注，而这个 uri 不可读 —— 遍历 name 的客户端一定会看见）。
25. **`repo_path` 的能力不对称是有意保留的，但必须披露**：AI 指令白名单永远不含它
    （那等于让被内容带偏的模型自己决定去探测本机哪个目录），代理这条路径保留
    （用户显式指挥）。保留能力不等于不吭声 —— `t_create_project`/`t_update_project`
    的回执现在写明"之后看板会在那里跑 git，分支/最近提交/未提交改动会进入接手包与 AI 上下文"。

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

> **2026-09-24 口径更正，读上面这张表之前先看这条**：表里所有数字都是在
> `repo_path="D:\work\demo-{i}"` 下测的，而那种路径**不存在** —— `git_snapshot` 在
> `repo.exists()` 就返回了。也就是说**这张表里 git 探测成本恒等于 0**，
> 而"列表接口 >300ms 才考虑换存储层"这条判断线一直是它在撑。
> `bench.py` 现在默认造真仓库（`--repos N`，各 3 条提交 + 1 个脏文件）并同时报
> 「冷」（每轮清缓存 = TTL 全过期，生产上的上界）与「热」两份，git 不可用时**明说**而不是悄悄退回旧测法。
> 实测一个仓库一次完整探测 = **168ms**（Windows 起 5 个 git 子进程），
> 所以 200 项目 / 6 个真仓库下 `GET /api/export-all` 冷测 ≈1.2s、不带仓库时 13ms。
> 同一轮把 `git_snapshot` 的缓存键从 `{repo}|{limit}` 改成只按 `{repo}`
> （以前同一次刷新里三个出口要 limit=1/3/5 ⇒ 同一仓库被扫 3 遍 = 15 个子进程；
> 判据数的是**真跑了几个子进程**，A/B 改回旧键立刻红）。

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
   不在这里重复一个会漂的数字）。**量的是分支覆盖**（`addopts` 里带 `--cov-branch`）：
   以前只量行覆盖，`if 条件: 走对的那支` 单独就算满，而 else / except 那支
   （缺陷常待的地方）压根不进分母。打开后 93% → 92%，门槛 90 不动，所以这不是
   把数字调好看，是把数字调真。
   别把 `branch = true` 写回 `[coverage:run]` —— pytest-cov 自己构造 source/开关，
   实测放那里**不生效**（报告表头连 Branch 列都没有），只有 addopts 那条算数。
   **覆盖率有一个必须说出来的盲区**：`scripts/mcp_server.py`（23 个工具的出口）是
   **子进程**跑的，行覆盖量不到它 —— 加 `--cov=scripts/mcp_server.py` 连 TOTAL 都不变，
   是惰性配置，别写进配置假装它在。它的契约靠 `tests/test_mcp_server.py` 走真
   `tools/call` 逐工具验（见「MCP 契约纪律」那条全覆盖判据）。
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
14. **门禁只有三档：通过 / 失败 / 未验，不许有第四种"默默算作通过"**
    （2026-09-23 整轮收口就是把这一条建立起来）：
    - `check(name, cond)` 的 `cond` 传**字符串**就是"这一项本轮没跑"，计入 SKIP 并印在
      结果行里；`check(..., True)` 这种占位是**禁项** —— 它把"没验"伪装成"验过"。
      `release_regression --no-ai` 以前这样报"18 通过 / 结论：新功能链路全部可用"，
      而会话捕获→提炼→入库这条链在门禁里从未跑过。
    - 空集合上的 `all(...)` 与 `not xs or all(...)` 一律不算通过：CI 起的实例本来是空的，
      "项目字段齐全"因此恒绿过好几次。要么**先播种**（`ci_local` 现在给常驻实例播一块
      含合成 Key 的小板子），要么明说 SKIP。
    - 阈值按**实测值**钉，不按"大概十几个"钉：ACTIONS `>=60`（实测 60）、工具数 `>=23`。
      门槛定在 30/10 时砍掉一半仍然全绿，那种门等于没有；要缩减就连带改这一行，
      这正是想要的摩擦。
    - 运算符优先级也是断言：`A and B or C` 那条形同只断言了 C（交接包整块丢任务清单照绿）。
    - 每条新判据都要做一次 **A/B**：把修法改回缺陷原样，看它是否真的变红，
      并把失败现场留在断言消息里（只报"哪个工具漏了"看不出漏在哪个字段）。
16. **活体用例（`KANBAN_LIVE=1`）也必须隔离数据目录**：`tests/test_ai.py` 的 `live_server`
    以前写的是 `env.pop("KANBAN_DATA_DIR")  # 用真实配置目录，才有 Key` —— 那句话直接
    违反本条第 8 点，而违反一条约定最省事的后果就是**大家都不跑它**（这 6 条从写下起
    一次都没执行过）。现在的做法：临时数据目录 + 把真实 Key 经 `PUT /api/ai/config`
    灌进这台临时实例（`llm.usable_endpoints()` 与发版回归共用那份读取逻辑），
    项目由夹具自己造，起服务后第一件事还是读 `/api/health` 的 `data_file` 自证。
    跑法与成本：`KANBAN_LIVE=1 .venv\Scripts\python.exe -m pytest tests/test_ai.py`
    ≈ **4-5 次真调用 / 半分钟**；2026-09-23 首跑 5 条全过，且跑前跑后对
    `~/.ai-kanban` 里 `projects.json`/`usage.jsonl`/`config.json`/`reports/` 数一份哈希 ——
    **一个字节都没变**，这句话要能复算，不能只是注释里写着。
    同轮删掉一条恒真用例：`test_agents_md_has_local_fallback` 断言的是
    `source in ("ai", "local")`（两个可能值全列上 = 永不会红），而本地兜底在
    `test_ai_endpoints.py:137/222`、`test_agent_files.py:159` 已用假端点真断言过三次。
15. **起实例后必须问它自己读的是哪份数据**：`ci_local` 现在在探活成功后读
    `/api/health` 的 `data_file`，与本次的临时目录不符就立刻停 —— 端口通了不等于起对了
    （Windows 上 `SO_REUSEADDR` 允许两个进程都"绑定成功"）。`delivery_drill` 早就这么做，
    现在这条纪律推广到 CI。

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
- **"这个端点没人用"要先连 scripts/ 和 tests/ 一起看**（2026-09-19 我就是这样误判过两条）：
  `GET /api/projects/{pid}/next` 前端与 MCP 当时都不用，但 `delivery_drill` 与
  `test_task_deps` 在用；`GET /api/ai/reports` 界面上进不去（`openReport(rid)` 的"历史报告"
  分支根本没有入口传 rid），可 `scenario_test` 与 `test_ai_endpoints` 在用。
  只看产品表面就下"无消费者"结论，删掉的会是自己的门禁。
  正确方向反而在另一端：历史报告是**界面少了一截**去接那个已经存在的列表端点。
- **同一秒内的两次备份会 collapse 成一份**：快照目录名是 `backup_%Y%m%d_%H%M%S`，只精确到秒，
  而"备份"恰恰最容易被连着触发（导入流程自己先备份一次、用户又点一次；代理批量改动前连拍两份）。
  同名会落进同一个目录、第二次把第一次的内容覆盖掉 —— 以为有两份退路，其实一份，且全程没提示。
  现在重名会往后缀 `-2`/`-3`，`_backup_label()` 负责把它显示成"（第 2 次）"。
  教训通用：**凡是"名字里带时间戳"的目录/文件，都要假设同一秒会被撞两次**。
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

**状态：v1.2.0（以 `app/version.py` 为准）、可用。** 下表六轮到 1.0.0 转正为止，1.1.0 的六个面见表格下方说明；1.1.0 → 1.2.0 之间叠了两轮对标吸取（2026-10-01 对标 plane：自动分配/里程碑/项目统计/保存视图/待审查状态；2026-10-06 GitHub 竞品全方面对标：验收标准/journal 折叠/记忆时效/待拍板/Skill 投影，报告见仓库根 `竞品优点吸取-2026-10-06.html`）。演进过程（按提交顺序，细节见各 commit）：

| 轮次 | 主题 | 关键产出 |
| --- | --- | --- |
| 一 | 功能与安全 | 并发丢更新（`mutate()` 单临界区）、状态白名单、Host/Origin 校验、密钥移出项目、示例数据脱敏、调度器防重 |
| 二 | 工程化 | 58 处内联事件收敛为 `data-act`、pytest 套件起步、CI、`git init` |
| 三 | 打包可用性 | 修掉冻结版 exe 起不来（见「容易踩的坑」第一条）、启动日志、HTTP 真探活、`frozen_smoke.py` |
| 四 | 交接三件套 + 自愈 | 8 个工具入口文件、MCP 服务（stdio，13 工具）、跨进程写锁、last-good 自愈 + 恢复入口 |
| 五 | 上下文与排期 | git 集成（只读）、任务 `refs`（代码位置）、任务 `depends_on`（依赖/拓扑排序/可执行任务） |
| 六 | 功能扩展与对标 | 截止日期、看板拖拽、数据导入导出、全局搜索（Ctrl+K）、深浅色主题、脱敏、健康趋势预警、交付演练脚本、真实模型端到端测试、版本号贯通、1.0.0 转正 |

**验证基线（2026-10-06 收口，v1.2.0 起）**：`pytest` 687 收集（682 通过 / 5 跳过，跳过的需 `KANBAN_LIVE=1`）/
32 个测试文件、645 个 `def test_` 且**无重名**（数一遍对不上 `--collect-only` 就是有用例被静默覆盖）。
2026-10-01 新增「待审查」状态后补：E2E 五列归位、MCP review 分支文案、readiness 认待审查三个方向各一条。
2026-10-06 竞品吸取轮补：`test_p1_absorb.py` 16 条（验收标准 / journal 折叠 / 记忆时效 / 待拍板）+
MCP 契约 2 条 + Skill 投影 + `test_frontend.py` 两张对账钉子（验收提示数字对账、弹窗数 13→14）。
未覆盖数在 ±4 之间漂，是 `-n auto` 下时序类用例走了不同分支造成的，**别把它抄成一个精确值**；
门槛 90，只统计 `app/`，`scripts/` 不进覆盖率 —— 原因见「测试约定 4」）。
公开接口面：HTTP 58 条自有端点（`method+path` 计 59，多的那条是框架自带的 `/openapi.json`），
其中 2026-09-23 新增 `PATCH /api/projects/{pid}`、`PATCH /api/tasks/{tid}` 两条（见数据层约定 18）、
2026-10-06 新增 `GET /api/inbox` 一条
+ MCP 23 个工具（工具数没变，但 `kanban_add_task`/`kanban_update_task` 的 schema 带了 `acceptance`、
`kanban_memory_add` 带了 `expires`），另有资源（`resources/list` / `resources/templates/list` / `resources/read`，
  4 类 uri）与提示词（`prompts/list` / `prompts/get`，3 个）。
门禁脚本（2026-10-01 全部复跑过一遍，`ci_local` 七步全绿、变异探针 **44** 条 —— 2026-10-06 新增 4 条且 4/4 被杀，
`ci_local --quick` 三步全绿）：`scenario_test` 32/32（以前那条 33 里有一条是把 A10 数了两遍）、
`release_regression --no-ai` 16 通过 + **2 项明确标"未验"**（`--no-ai` 下 AI 链路本就没跑，
不许再算进"全部可用"）、`delivery_drill` 21/0、`live_check --mcp` 12/0、
`frozen_smoke` 11/0、`live_ui_check` 16/0。并行（`-n auto`）冷跑约 56 秒。
> `release_regression` **去掉 `--no-ai` 实跑过一次：23 通过 / 0 失败**（2026-09-23，多花约 1 次真调用）
> —— 那句"新功能链路全部可用"第一次是跑出来的，不是印出来的。
`ci_local --with-exe` 全十步（含重打包与冻结版自查）实测约 90 秒；改过 `web/index.html` 之后
必须走这一档，否则「产物内嵌的前端就是仓库这一份」那条会如实拦住你。
⚠️ 全量跑常常**打完覆盖率表就没有 summary 行**（见「测试约定 10」），判绿只认退出码。

> 1.1.0 一次加了会话捕获 / 过程轨迹 / 记忆层 / 用量追踪 / 提醒 / 只读模式六个面，
> 语句数 1630 → 1943，覆盖率一度掉到 92%（余量 2 点）。补了 16 条新面用例后回到 93%。
> **加功能时同步补测试**，否则再叠一个版本就会撞上 90 的门槛。

> 用例数**只在这一处写死**，README 与本文档其他位置引用时写「见验证基线」。
> 数字以命令输出为准：`python -m pytest -o addopts="" --collect-only | tail -1`。
> 每次加测试同步改这一行 —— 散落在多处填数字必然漂（2026-09-16 就同时存在 248/269/278 三个版本）。

**下一步（按价值排序）**：

1. **`app/main.py` 继续拆分** —— 已拆出 `app/gitinfo.py`（有状态，patch 点随迁）、
   `app/handoff_md.py` / `app/agentsmd.py`（先做依赖注入再搬：渲染/生成本体只收现成数据，
   回退/取盘留在 main 包装里，原签名不变）、`app/paths.py` + `app/aireports.py`
   （数据目录根 + 报告落盘簇，编排 `_run_analyze` 留在 main）、`app/aiprompt.py`
   （AI 只读计算层：看板简报 + 会话提炼；`_memory_expired` 随日期判定迁入 `app/due.py`，
   探针目标已同步）。`ai_command` 的提示词**有意留在 main**：前端契约测试
   `test_ai_command_prompt_keys_on_ids_not_titles` 直接 inspect 端点源码，
   搬它要连测试一起迁，收益不值。再往下只剩数据层本体（mutate / _load_data / 自愈备份）——
   **那是临界区的心脏，不拆**；守住「写操作必须走 `mutate()`」，别顺手把临界区拆散。
2. **存储层**：仍是整份 JSON 读改写。触发条件是「列表接口日常 > 300ms」，
   那时直接换分片或 SQLite，**不要加缓存糊**（理由见「性能基线」）。
3. E2E 空白已全部补齐（2026-10-06）：入口文件预览/写盘、备份与恢复的实际动作、
   监管趋势图、接手页下载文件校验、会话捕获全流程（扫描→预览→提炼→人工确认入库）。
   新增浏览器层用例时优先挑"从未有人点过"的路径 —— 恢复面板多来源、报告遮罩与按钮
   同 data-act 这类坑就是这么现形的。
4. 剩下的未覆盖行（数目以「验证基线」为准，不在这里写死）基本是异常分支（`Path.resolve()` 失败、
   系统目录判定异常、各类 500 兜底）。
   要补就得构造畸形环境，收益递减 —— 别为了数字硬凑。
5. **审出来但当下故意没做的三条**（各自都需要一次"用户能不能接受"的判断，别顺手改）：
   - **备份目录里跟着复制了 `config.json`**（`/api/backup` 留 10 份，导入时也会自动备份一次）。
     都在 `.ai-kanban` 内、不违反「密钥不进项目目录」，但"整目录拷走换机"会带走多份明文凭据。
     改法是备份只存掩码后的端点 + 调度/会话源 —— 代价是**恢复后 Key 要重填**，所以要用户点头。
   - **journal 是列表接口最先撞线的维度**：1000 项目 × 10 任务下，journal 从 0 条加到 20 条/任务，
     文件 4.67MB → 81MB，`_load_data` 57ms → 336ms（与项目数无关）。
     ~~收口办法是把 `MAX_JOURNAL` 50→20、`MAX_JOURNAL_TEXT` 500→200~~（2026-10-06 起有更好的路：
     超限**折叠成摘要**而不是截条数，见硬性约定 25 —— 治的是"无声丢历史"，本条的截断方案仍需用户点头才动）。
   - **`_projects_brief` 有两份实现且语义已分叉**（`main` 那份给 LLM、任务 `[:40]` 截断；
     MCP 那份重抄了 `/api/projects` 的计数+就绪度）。合并会同时改两处工具输出，收益不值这个风险。
5b. **2026-10-06 全方面竞品对标后「明确不吸收」的**（研究档案：
    `research-reports/github-competitor-benchmark-2026-10-06/`，16 组检索 + 43 仓库 API 核实；
    吸收了五条：验收标准 / journal 折叠 / 记忆时效 / 待拍板 / Skill 投影，见硬性约定 24-28。
    这些是**想清楚不要做的**，别再提）：
   - **MCP 工具分档暴露**（Task Master 36 工具三档加载）：23 个工具还在可接受范围，已有只读模式
     这个可见性开关；再加分档会让客户端工具目录漂移 —— 触发线：工具数 > 30 再议。
   - **会话历史全文检索**（Wake 的 FTS5 trigram）：要引入索引层与新存储，本轮超预算；
     现有「扫描 → 预览 → 提炼入库」链路覆盖主要用法。列为下一步候选，做之前先量会话文件规模。
   - **handoff 引用化**（waggle 的 ~30 字节工件引用 token）：交接包是给人+模型读的 Markdown，
     引用 token 伤可读性；任务 `refs` 已经是"引用而非复制"的雏形。
   - **用量记账细化到 run/任务维度**（Multica per-issue 成本）：本板 AI 调用都是看板级
     （监管/指令/提炼），没有 run 概念，没有可挂的成本对象。
   - **agent 执行编排 / 团队协作 / git-worktree 并行 / 换存储引擎**：维持 10-01 结论（上文与
     「不要再做的事」）。另记两条风险警示：vibe-kanban（28k★）死于无商业模式、Task Master
     （28k★）四个月停更 —— 星标≠生命力；「Agents don't need memory, they need documentation」
     （HN 2026-10）对本项目"交接包/AGENTS.md 为中心"的定位是背书，对重度记忆层路线是刹车。
6. **接口扩面：2026-09-19 已经做过两轮**（用户点头后）。做出来的：
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
   - **补上记忆层的读与删、以及删项目**：`kanban_list_memories`（按项目/类型/关键词过滤，
     默认脱敏，50 条封顶且**超了就报数**）、`kanban_delete_memory`、`kanban_delete_project`。
     以前智能体只能往记忆层盲写：写进去几条、id 是什么、想撤一条，全都不知道。
   - **`kanban_delete_project` 要 `confirm=true` 才真删**：第一次调用只返回"将失去多少条任务
     （几条没做完）、几条项目记忆"并拒绝执行。全看板只有这一个工具要 confirm ——
     因为它是唯一无法单独撤销的操作：没有软删除，唯一退路是拿备份**整份**恢复，
     那会把其它项目一起回滚。别把这个门槛"为了顺手"去掉，也别推广到别的工具。
   - **全项目导出与快照**：`kanban_export_all`（复用 `build_export_all()` —— 把它从路由里抽出来，
     就是为了不让"MCP 与 HTTP 各有一份渲染"重演一次）、`kanban_backup`、`kanban_list_backups`。
   - **恢复（restore）故意不给代理**：一次误判就会把其它项目一起回滚，而且会无声吞掉人可能想救的内容。
     代理能做的是"拍一份快照 + 把快照清单报给用户"，最后一步由人在「数据」面板点。
     别为了"顺手"补一个 `kanban_restore`。
   - 还剩没做的：AI 配置读写。**这条我要先否掉**：允许代理写 `/api/ai/config` 就等于让它改
     `base_url`，之后所有调用都会被导向另一台服务器 —— 那是凭据外流的通路，不是便利。
   已判定为负资产、不要再提：游标分页（整份读改写下省不掉解析，只加边界 bug）、
   ETag/If-Match（`mutate()` + 跨进程锁已经串行化了两个写入进程）、
   webhook/SSE（要常驻订阅态与追加型事件表，撞约定 11）、软删除回收站（每条读路径多一个分支，
   "删错了"已经由备份/恢复覆盖）、`outputSchema` 声明式校验与 progress/cancellation
   （前者一旦与文本漂移会被客户端整包拒；后两者要为串行临界区上并发）。

**不要再做的事**（已经想清楚、别回头做）：

- 加更多功能 —— 功能面已经很宽，边际收益在下降，重心应转向验证与稳定性。
- worktree 并行隔离、团队协作与权限 —— 不是这个项目的赛道（见 README「和同类工具比」）。
- 给 `CLAUDE.md` 复制正文、给每个工具各存一份约定 —— 必然漂移，见「多智能体入口文件」。

