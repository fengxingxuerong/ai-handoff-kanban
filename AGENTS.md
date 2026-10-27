# AGENTS.md — AI 交接看板

> 给 AI 编码代理的项目上下文。只写代码里看不出来的信息：命令、约定、坑、进度。

## 这个文件怎么读（2026-10-24 起）

本文件**只放每次都要读的东西**：规则、命令、高频坑。参考资料按需读，全在 `docs/`：

| 什么时候读 | 去哪读 |
| --- | --- |
| 改数据层 / 写路径 / 备份恢复 / 脱敏边界 | [docs/数据层约定.md](docs/数据层约定.md)（18 条，编号与代码注释一致） |
| 改 MCP 工具 / 代理契约 | [docs/MCP-契约.md](docs/MCP-契约.md) |
| 写或改测试 | [docs/测试约定.md](docs/测试约定.md) |
| 想知道某轮为什么这么改 / 逐轮验证基线 | [docs/进度与版本档案.md](docs/进度与版本档案.md) |
| 改数据层性能或存储形态 | [docs/性能基线.md](docs/性能基线.md) |

> 为什么切：此前它是 154KB 单文件，而加载预算只有 65536 字节 —— 每次接手只能读到前一半，
> 丢的恰好是坑与测试约定。切分后常驻部分约 62KB（**能整体装进预算**），内容一个字没删。

## 这是什么

多项目任务看板桌面应用，为「频繁切换 AI 智能体做项目」设计：每个项目带交接说明与下一步行动，
一键导出交接摘要 / 交接包 / AGENTS.md，让新智能体接手零成本。内置多端点 LLM 自我监管。

技术栈：FastAPI + 原生单页前端 + PyWebview 桌面壳，PyInstaller 打成**两个** exe（GUI 主程序 + 给 AI 代理用的 MCP 控制台服务）。

## 目录与职责

| 路径 | 职责 |
| --- | --- |
| `app/main.py` | 全部 HTTP 接口 + 数据层 + 监管/指令/交接/AGENTS.md 生成 + 调度线程。**2026-09-26 起分轮拆出（3596 行 → 现 3255 行、下表 30 个模块）；2026-10-24 又拆出 fields / schemas / sampledata / service（3539 → 3255 行）**，本文件只留路由编排、数据层临界区与有全局状态的部分 |
| `app/taskgraph.py` | 任务依赖图：`clean_deps` / `task_graph` / `has_cycle` / `topo_order` / `_break_dep_cycles` / `executable_tasks` / `deps_blocking`（与 /api/inbox 共用的"被前置挡住"谓词，2026-10-07 抽出）/ `_dedupe_ids` / `clean_refs`（纯函数，从 main 逐字搬来） |
| `app/due.py` | 日期类判定（纯函数）：截止日期归一 `_clean_due_date`、状态判定 `_due_state`、记忆过期 `_memory_expired`、记忆召回排序 `recall_memories`（2026-10-11 对标 claude-mem Awareness：决策/坑/约定优先于事实，同类内时间新在前；AGENTS.md 的 12 条窗口与 AI 简报的 10 条窗口共用一套，避免晚写的背景事实把早记的坑挤出窗口） |
| `app/journal.py` | 过程轨迹与验收标准的清洗与上限（纯函数，2026-10-07 从 main 拆出）：`append_journal`（溢出**折叠成摘要**的写入侧）/ `compress_journal` / `clean_journal` / `clean_acceptance` 与 `MAX_JOURNAL*`、`MAX_ACCEPTANCE*`、`JOURNAL_SNIPPET`。**这些上限常量的 monkeypatch 点在这里**（main 是转发别名，patch 别名改不到函数读的全局）；时钟由 main 的 `_append_journal` 包装注入，本模块不 import main |
| `app/redact.py` | 交接内容脱敏规则与 `redact_text`（纯函数）+ `count_sensitive`（命中**条数**，2026-10-23 加）：逐条 `subn` 与 `redact_text` **同序同规则**，只是顺手把命中数加起来 —— 不许为"报个数"另写一套正则（那迟早和真正的脱敏漂成两个答案）。用途见数据层约定 4 的凭据预警 |
| `app/idem.py` | 创建幂等键 `client_ref` 的清洗与记账（纯函数） |
| `app/security.py` | 本地访问防护与落盘白名单（纯函数，2026-10-14 从 main 拆出）：`EXPORT_SUFFIXES` / `ALLOWED_HOSTS` / `host_of` / `origin_allowed` / `request_verdict`（Host→Origin→Token 三关判定，返回 `(状态码, 原因)`、0 = 放行）/ `forbidden_roots` / `is_forbidden_target`（**两条边界都 fail-closed**：target 解析失败拒绝；某条黑名单 root 解析失败**也拒绝** —— 2026-10-14 用户拍板收紧，原先 `continue` 等于"那条 root 不存在"的 fail-open 窗口。与放行名单 TEMP/TMP 的不对称是有意的：放行名单算不出来只是少给一个许可，安全方向）。main 里是 `_host_of` / `_forbidden_roots` / `_is_forbidden_target` 同名转发，测试与 monkeypatch 路径不变。**五条探针（`guard_host_ipv6` / `guard_token_methods` / `guard_origin_reversed` / `forbidden_roots_drops_appdata` / `forbidden_target_fails_open` / `forbidden_root_unresolvable_allowed`）的目标文件在这里** |
| `app/handoff_meta.py` | 交接包小节解析 `parse_handoff_sections` 与尺寸预算 `context_budget` / `_budget_note`，以及**条目级尺寸估算** `text_size` / `size_note` / `estimate_tokens`（2026-10-11 对标 claude-mem 的渐进式披露最小落地：给记忆检索输出报每条成本，HTTP / MCP / 前端记忆面板三处同一口径；口径与纪律见硬性约定 29） |
| `app/context_slice.py` | 接手包切片与增量（纯函数，2026-10-21 第七轮竞品吸取）：`CONTEXT_SECTIONS`（可选小节白名单，`project` 始终返回）/ `parse_context_sections`（不认识的 name 400，不静默忽略）/ `parse_since`（`YYYY-MM-DD` / `HH:MM` / ISO `T` 归一，非法 400）/ `changed_since`（分钟精度、含边界；缺时间戳 fail-open，避免老数据在增量里永久消失）/ `has_timestamp`（只用于"这几条没时间戳"计数）。**三条探针 `context_unknown_section_ignored` / `context_since_exclusive` / `context_delta_filters_start_here` 的目标文件涉及这里与 main 的 `project_context`** |
| `app/sessions.py` | 会话扫描与文本提取（目录遍历有界、只读文件尾部、源内外校验） |
| `app/gitinfo.py` | git 集成（2026-10-06 从 main 搬出）：`git_snapshot` / `_run_git` / `_git_cache`。只读、列表传参、绝不拼 shell。**`_run_git`/`_git_cache`/`GIT_TIMEOUT` 的 monkeypatch 点在这里**（main 里是转发别名） |
| `app/repo_watch.py` | 仓库变化雷达（2026-10-18）：按 `repo_path` 扫描 git，检测未提交/新提交，按任务完成度给建议进度；`repo_watch.json` 基线 + `repo_audit.jsonl` 确认审计（**第三条追加型存储，有上限**：超 `MAX_AUDIT_LINES=2000` 裁到 `KEEP_AUDIT_ON_TRIM=1000`，`ack` 的追加+裁剪共用跨进程锁；**2026-10-25 起基线本体的读-改-写也进了同一个临界区**（`_state_guard()`：线程锁 + 侧车文件锁 —— 此前只有线程锁，两个进程交叉时丢更新；探针 `repo_watch_baseline_write_unlocked`）；删项目走 `forget(pids)` 清孤儿基线，单个/批量两处都调）。**只有用户显式确认才写 progress**；总览巡检吃 `_git_cache` 60s 快照、点名单个项目绕过缓存要现值（2026-10-18 实测：8 真仓库冷扫 1.58s / 40 次子进程，缓存命中 2ms / 0 次）。**备份/恢复带上基线**（与项目数据同拍同回滚，来源里没有 = 不动现状；last-good 等与线上同目录的来源靠 `same_as_live` 跳过）；**导出/导入有意不带**（跨机后 repo_path 指不到同一仓库，与 `_client_refs` 同理）；`repo_audit.jsonl` 不随备份（与 `usage.jsonl` 同为本地操作记录）；**按目录反查项目 `match_project`**（HTTP 与 MCP 共用一份） |
| `app/agentfiles.py` | 多智能体入口文件投影（`AGENT_ENTRY_TARGETS` 白名单 → 各工具要求的相对路径，含第 9 个 `.claude/skills` 投影） |
| `app/llm.py` | LLM 网关：端点排序、多 Key 轮询、失败降级、配置读写（含锁）、掩码找 Key `merge_keys`（2026-10-07 从 main 迁入，与 `mask_matches` 同域） |
| `app/filelock.py` | 跨进程文件锁（msvcrt/fcntl 一份实现，`main` 与 `llm` 共用）。两边不能互相 import，所以锁不放在任何一边里 |
| `app/handoff_page.py` | 自包含「接手页」单文件 HTML（含分层依赖图 SVG）；**不 import main**（main 会 import 它，反过来成环） |
| `app/handoff_md.py` | 交接包 Markdown **纯渲染器**（2026-10-06 拆出，依赖注入式）：`build_handoff_markdown` 只收现成数据（活跃记忆/过期计数/git 回调/端口），不 import main、不读盘、不跑子进程；原签名 `_build_handoff_markdown` 是 main 侧的注入包装 |
| `app/agentsmd.py` | AGENTS.md 文档生成（2026-10-06 拆出，同法注入）：本地兜底版收 `memory_rows`+`now`，AI 版的记忆与兜底经回调注入；`_build_agents_md_local` / `_generate_agents_md` 是 main 的原签名包装 |
| `app/paths.py` | 数据目录定位与三个"救命路径"（2026-10-06 起步，2026-10-14 补齐）：`data_dir` / `data_file` / `last_good_file` / `before_restore_file` / `LAST_GOOD_SUFFIX`，所有存储模块的共同根 —— 后续拆出的模块从这里取，不必反向 import main（备份/恢复白名单与读写照用同一份定义，不会各写一份）。环境变量每次调用时读 |
| `app/readiness.py` | 交接就绪度评分（纯函数）：`readiness_items` / `readiness_score`，「待审查」也算推进 |
| `app/assign.py` | 任务自动分配（纯函数，2026-10-01）：`allocatable` / `plan_assign`，就绪判定复用 `executable_tasks`；路由只负责锁内写入 |
| `app/project_stats.py` | 项目统计聚合（纯函数，2026-10-01）：截止热力/任务计数，`/api/projects/{pid}/stats` 的口径 |
| `app/usage_stats.py` | LLM 用量聚合（纯函数，2026-10-01）：usage.jsonl 的按日/端点/模型聚合与预算水位，`/api/usage` 的口径 |
| `app/aireports.py` | 监管报告的落盘/裁剪/历史/趋势（2026-10-06 拆出）：`reports_dir` / `_save_report` / `_trim_reports` / `_latest_report_before` / `_attach_trends` / 提示词构造。**`MAX_REPORTS` 的 monkeypatch 点在这里**（main 是转发别名）；`_run_analyze` 编排留在 main |
| `app/aiprompt.py` | AI 编排的只读计算层（2026-10-06 拆出，依赖注入式）：看板简报 `projects_brief`（记忆分桶/git 探测经回调注入，main 的 monkeypatch 照常有效）与会话提炼 `summarize_inner`（find_project 注入）。**不落库** —— 写动作仍走 main 的 mutate() 临界区 |
| `app/service.py` | 端口兜底值 `DEFAULT_PORT`、「活数据直链」`local_url(port, path)` 与**挑空闲端口** `free_port()` 的唯一实现（前两者 2026-10-24 拆出：此前这个数写死三份、直链格式抄两份；`free_port()` 2026-10-25 收拢 —— 此前 `ci_local` / `delivery_drill` / `scenario_test` 各写了一遍，而 `release_regression` 写死 `18399`，于是它成了唯一会被残留实例咬的那个）。**端口当参数收**，不自读环境变量 —— `test_api.py` 钉着「直链端口必须来自 `SERVICE_PORT`」。钉子 `test_default_port_is_written_in_exactly_one_place`；探针 `live_url_wrong_port` 在这里。为什么这么切见模块 docstring |
| `app/fields.py` | 字段白名单、上限与清洗（纯常量 + 纯函数，2026-10-24 拆出）：`DEFAULT_*` 三个白名单、`MAX_MILESTONES` / `MAX_MILESTONE_NAME` / `MAX_BLOCKED_REASON` / `MAX_VIEW_*` / `MAX_SAVED_VIEWS` / `MAX_REPO_ACK_NOTE`、`_clean_milestones`。独立出来是为了**不成环**（`schemas` 要引用它们）。探针 `milestone_dedupe_removed` / `milestone_cap_removed` 在这里 |
| `app/schemas.py` | 17 个 HTTP 请求模型（Pydantic，2026-10-24 搬出、逐字未改）：入参形状与字段级清洗。只依赖叶子模块（`fields`/`journal`/`taskgraph`），**不 import main**；main 同名转发，`kanban.ProjectIn` 之类照旧。探针 `milestone_task_name_not_trimmed` / `repo_ack_note_uncapped` 在这里 |
| `app/sampledata.py` | 首次运行的示例数据 `sample_data()`（纯函数，2026-10-24 拆出）。两条纪律跟着搬：时间戳按相对今天生成、**保持脱敏**（随 exe 分发，硬性约定 8）。探针 `sample_dates_hardcoded_again` 在这里 |
| `app/repo_snapshot.py` | **看板快照 → 仓库内文件**（纯渲染器，2026-10-25）：把一块板渲染成 `docs/kanban/` 下一份人读 Markdown（目录页 + 每项目一份）+ 一份机读 `board.json` —— 于是 clone 了仓库、或没接 MCP 的 agent 也能看见看板（选项 A：数据仍以 `~/.ai-kanban` 为唯一真相源）。不读盘、不写盘、不认识脱敏（脱敏由调用方在渲染结果上统一做）。覆盖策略认文件里的**标记**：我们生成的才更新，用户手写的默认不动 |
| `web/index.html` + `web/js-*.js` + `web/style.css` | 前端。**2026-10-18 起 JS 按关注点分成 10 个文件**（加载顺序：`js-core` 基础设施与 ACTIONS 分发表 → `js-overview` 总览 → `js-detail` 详情 → `js-render` 渲染工具 → `js-reality` 代码现实 → `js-handoff` 交接物 → `js-edit` 表单编辑 → `js-ai` AI → `js-data` 数据 → `js-boot` 启动序列），index.html **零内联脚本**；无构建、无 CDN 依赖，全部函数是全局函数，交互统一走 `data-act` 事件委托。拆的是**关注点**不是字节数；静态契约一律用 `tests/frontend_src.py:frontend_source()` 对着"整个前端"判，**不要写具体文件名**（`test_p0_stats.py` 里曾写死 `js-core.js`，搬家当天就红） |
| `docs/` | AGENTS.md 的延伸（**按需读，不常驻**，2026-10-24 从本文件切出、内容逐字未删）：`数据层约定.md`（18 条）/ `MCP-契约.md` / `测试约定.md` / `性能基线.md` / `进度与版本档案.md`（逐轮叙事与验证基线）+ 界面截图。切分理由与「去哪看」表在本文件开头 —— **没读到不等于没有** |
| `tests/` | pytest 套件（用例数见文末「验证基线」，此处不写死）+ 内置假 LLM 端点（`llm_stub.py`），覆盖率门槛 90%（实测数字只写在文末基线那一处）。含真实浏览器点击测试 `test_frontend_e2e.py`（浏览器三级降级：系统 Edge → 系统 Chrome → **自带 chromium**；最后一级不可删 —— CI runner 没有系统浏览器，少了它就整份静默跳过；钉子 `test_e2e_keeps_the_bundled_chromium_fallback`） |
| `scripts/desktop.py` | 桌面壳：补标准流 → 探测端口 → 后台起 uvicorn → 真实 HTTP 探活 → 开 Pywebview 窗口；启动过程写 `<数据目录>\startup.log`。`KANBAN_NO_WINDOW=1` 时只服务不开窗（CI / 服务器 / 调试用） |
| `scripts/live_check.py` | 对着**已在跑**的实例打接口做只读体检（不写数据）；替代了原来的三个手工冒烟脚本 |
| `scripts/live_ui_check.py` | 对着**已在跑**的实例开真浏览器做只读校验（Playwright + 系统 Edge/Chrome）：证明界面真的渲染出来了，不是白屏。全程只点导航类操作，不写一个字节 |
| `scripts/e2e_ci_browser_check.py` | **在 CI 的浏览器环境下真跑 E2E**（2026-10-25）：进程内把浏览器链强制成「只有自带 chromium」（第一级必然失败，只有真 fallback 才起得来），并**记录实际使用的 channel** 证明它真用了第三级 —— 本机系统浏览器都在时默认跑的是第一级，那条路在这里才复算得了。不改源码（pytest 插件进程内改，避免「改文件再还原」的残渣风险）。CI 的 runner 没有系统浏览器，跑的就是这一级 |
| `scripts/scenario_test.py` | 真实场景测试 32 例（真服务 + 真 MCP + 真实业务数据，可导出报告） |
| `scripts/delivery_drill.py` | 交付演练：模拟换智能体接手，端到端 21 项（接手→执行→交接物→脱敏→迁移）。**不传参数时自起隔离实例**；传 base_url 则先读该实例 `/api/health` 的 `data_file` 自证不是真实数据目录，否则拒绝运行（演练会用 `/api/data/import` 整份覆盖） |
| `scripts/real_ai_test.py` | 真实模型 AI 功能测试：5 大功能 × 6 模型通道（消耗真实配额） |
| `scripts/release_regression.py` | 发版回归：自起独立实例（不碰真实数据），v1.1 新功能全链路（`--no-ai` 下 16 项真断言 + 2 项标「未验」；不带 `--no-ai` 才会跑 AI 链路）；真实 Key 从 `KANBAN_REAL_DATA_DIR` 指向的配置读取。**挑空闲端口 + 起跑自证「这个端口上是本轮实例」**（`free_port()` + `_own_instance_guard`，2026-10-25）：此前 `PORT` 写死 18399，端口上站着残留实例时会拿**别人的**回包当本轮结论（实测报出来的是一句「只读模式下写操作被拒」，真原因是端口被占，隔了十万八千里） |
| `scripts/test_all_llm.py` | LLM 端点全量实测：端点×Key×模型 健康矩阵 |
| `scripts/make_icon.py` | 纯标准库生成应用图标 app.ico |
| `scripts/frozen_smoke.py` | **冻结版自查**：启动 dist 里的 exe，真打一遍接口，自动关掉（每次打包后必跑）。除了活着，还钉三件事 —— 产物是不是**新的**比产物能不能跑更容易出问题：① exe 自报的 `version`/`build_time` 与仓库里的 `app/version.py`、`_build_stamp.py` 同源；② **首页返回的字节与 `web/index.html` 逐字节一致**（专治「改了前端忘了重打包」）；③ **构建戳之后 `app/*.py` 或 `web/index.html` 有没有再被改过**（后端打进 .pyc，没法比字节，只能按时间拦）。没有它们就是"测试全绿、发出去的 exe 少几个修复"。**MCP 产物有它自己的一份，见下行**（两者对标准流的处理完全不同）。**2026-10-24 起管进程卫生**：起跑前清场（清不掉就停）、收尾兜底并**验证**端口释放；**2026-10-25 起就绪判定也自证是本轮实例**（它固定用 18395：测的是打包产物，别人的回包会让「版本同源 / 内嵌前端一致」报出与真原因无关的失败）；`--sweep [exe]` 供 ci_local 打包前调（残留进程曾把下一轮打包毒成 WinError 5）。详版见档案 v1.9.4 |
| `scripts/mcp_frozen_smoke.py` | **MCP 冻结版自查**（2026-10-23，与 `frozen_smoke` 并列）：真的启动 `dist\AI交接看板-MCP.exe`，走真实 stdio 打一遍，钉四件事 —— ① `tools/list` 拿得到 25 个工具；② **stdout 每一行都是合法 JSON-RPC**（一行日志就能让客户端解崩、整个服务等于没发布）；③ **中文双向不乱码**（写进去再读回来逐字一致：出方向工具目录被编成 cp936 客户端就崩、入方向中文标题静默变乱码入库）；④ 真写进了隔离临时目录（不信回执的一面之词）。九条断言，退出码 0 才算过。**「打包才会坏、源码态永远看不出来」的那一类**，所以必须跟着 exe 一起跑（`ci_local --with-exe` 里是独立一步）；`--source` 可对着源码跑同一套断言（先自证门禁本身有效） |
| `app/mcp_server.py` | **MCP 服务**（stdio，25 工具 + 资源 + 提示词；2026-10-25 从 `scripts/` 搬进包里，`pyproject.toml` 才指得到它）：代理可列项目/拉接手包(context)/读写任务与记忆/删任务删项目/生成交接物/导出全项目包/拍数据快照，交接内容与接手包可 `resources/read`、接手流程可 `prompts/get`。每个工具在 `TOOL_KIND` 里登记读写属性与标题（决定 `annotations`，也决定只读模式下是否出现在 `tools/list`），入参按各自 `inputSchema` 的 required/enum/type/additionalProperties 真的校验；`kanban_delete_project` 是唯一要求 `confirm=true` 的写操作，**恢复快照故意不做成工具** |
| `scripts/mcp_server.py` | **上面那份的兼容壳**（2026-10-25）：十几个调用点写着这条路径，改它们换不来功能。用**透明别名**（`sys.modules[__name__] = 真模块`，不是 `import *`）。必须薄，两条钉子看着 |
| `scripts/bench.py` | 读写性能基准（先量再改） |
| `scripts/mutation_probe.py` | **变异探针**：往源码注入 113 处语义改动（临期窗口、Origin 反转、脱敏漏规则、journal 折叠、幂等键截断、待拍板过滤、记忆召回权重、条目尺寸、落盘黑名单 fail-open…），逐条跑相关用例；**改了还全绿 = 盲区，判失败**。覆盖率回答"这行跑过没有"，它回答"这行错了会不会红"。 **逐轮史**（哪一轮加了几条、为什么、每条 killer 是谁）已整段搬进 [docs/测试约定.md](docs/测试约定.md) 的「变异探针：逐轮史」一节 —— 内容搬走，不是删掉（**每一批加了哪几条、为什么**也在那一节）。**被外部信号打断时不会走到 `finally`**：2026-10-22 前台跑探针被沙箱 SIGTERM（进程外杀），变异留在 `web/index.html` 里没还原，下一次跑报"变异文本没匹配上"才暴露 —— 中断过的探针一律先 `git status` 自检再继续。**2026-10-24 起有启动预检**（`--preflight`，ci_local 两种模式都先跑）：old 命不中而 new 还在源码里 = **残渣**（报 `git checkout` 处置）；否则 = **表没同步**（哑火）。删除型变异 new 为空判不出残渣（空串 `in` 恒真，必须先排除）。**还原是「字节进、字节出」**（2026-10-25）：`read_bytes()` 进、`write_bytes()` 出，逐字节相等构造上成立；变异表里的 LF 文本由 `_native()` 适配文件自己的行尾 —— 为什么必须这样、两个方向各被什么咬过，见 [docs/测试约定.md](docs/测试约定.md)。钉子 `test_apply_and_run_restores_bytes_for_both_line_endings`。**第四批 4 条全静态**（见逐轮史）。**跑的时候挂 `running_lock()`**：第二份探针会**拒绝开跑**（退出码 3），陈锁自动清掉。id 一律全小写 snake_case。 |
| `scripts/make_stamp.py` | **打包前**生成构建戳 `app/_build_stamp.py`（版本条显示的打包时间）；`build.bat` 与 CI 都会调，手动打包别忘了 |
| `build.bat` | 建 venv、装依赖、生成构建戳、PyInstaller 打**两个**产物：`AI交接看板.exe`（GUI，`-w`，带 `web/`）与 `AI交接看板-MCP.exe`（**控制台子系统，故意不加 `-w`** —— MCP 走 stdio，而 `-w` 的 GUI 程序在 Windows 上拿不到标准流）。第二份是「只装了 exe 的用户没有 .venv，MCP 因此不可用」这个缺口的正解 |

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

rem 发 GitHub Release：--with-exe 全绿 → 推 main 等 CI 绿 → 打 tag（v + version.py 的数）推送
rem CI 在 tag 上自动发 Release（两个 exe）；tag 与 version.py 不同源会被 CI 拦下

rem 依赖可复现：requirements*.txt 写的是兼容下限，requirements.lock.txt 是实测闭包
.venv\Scripts\python.exe scripts\make_lock.py        # 在跑过全量测试的 venv 里重新生成
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt   # 换机 / CI / 出包用

rem exe 版本资源（右键属性看得到版本与构建时间；由 build.bat 第 5 步自动调用）
.venv\Scripts\python.exe scripts\make_versionfile.py

rem 只跑前端真实点击测试（复用系统 Edge/Chrome，无需下载 Chromium）
.venv\Scripts\python.exe -m pytest tests\test_frontend_e2e.py -v

rem 模拟 CI 的浏览器环境跑 E2E（只用自带 chromium，并核验真的用了那一级）
.venv\Scripts\python.exe scripts\e2e_ci_browser_check.py

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

rem 打包后必跑一：冻结版自查（会短暂弹出应用窗口，脚本自己关掉）
.venv\Scripts\python.exe scripts\frozen_smoke.py

rem 打包后必跑二：MCP 冻结版自查（只装 exe 的用户的命脉；--source 先对源码跑同一套断言）
.venv\Scripts\python.exe scripts\mcp_frozen_smoke.py
```

打包等价命令（`build.bat` 内部就是这条；`--add-data` 与两个 `--hidden-import` 缺一不可，
且路径必须用 `%~dp0` 绝对形式 —— `--specpath build` 会让相对路径按 spec 目录解析，导致找不到 `web`）：

```bat
.venv\Scripts\python.exe -m PyInstaller -F -w -n "AI交接看板" scripts\desktop.py ^
  --hidden-import app.main --hidden-import uvicorn --add-data "%~dp0web;web" --paths "%~dp0" ^
  --icon "%~dp0app.ico" ^
  --distpath dist --workpath build --specpath build --noconfirm
```

第二个产物（MCP 控制台服务）。**差别只在三处**：不加 `-w`（要标准流）、
不加 `--add-data web`（MCP 不服务前端）、入口换成 `scripts\mcp_server.py`：

```bat
.venv\Scripts\python.exe -m PyInstaller -F -n "AI交接看板-MCP" scripts\mcp_server.py ^
  --hidden-import app.main --paths "%~dp0" ^
  --version-file "%~dp0build\version_info.txt" ^
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
   **菜单里的「多步」动作要打 `data-keep-menu="1"`**：默认「选完就收起」会让第二下点不到（见 docs/测试约定.md）。
4. **kimi 系模型只允许 temperature=1**。`app/llm.py` 的 `chat()` 已按模型名自动适配，
   并对 400 Temperature 错误自动用 1 重试兜底。新增模型时若报该错，优先在 `_model_temp` 加适配，不要绕过网关直调。
5. **杀 exe 进程树必须 `taskkill /F /T /PID`**。PyInstaller -F 单文件会拉起子进程跑实际服务，
   `terminate()` 只杀父进程，子进程残留会占用端口与 exe 文件（历史事故：打包时 PermissionError 拒绝访问）。
   `frozen_smoke.py` 已内置进程树清理 + 端口兜底检查，且起跑/收尾都按**可执行路径**清同 exe
   残留（`--sweep`，ci_local 打包前也调）—— 路径不是映像名，按名杀会误杀用户装在别处的副本。
6. **状态与优先级只能取固定值**：项目 `规划中/进行中/已暂停/已完成`，任务 `待办/进行中/待审查/已完成/阻塞`，
   优先级 `高/中/低`。前后端各有一层白名单：后端 `ProjectIn/TaskIn` 用 `Literal` 返回 422，
   前端用 `STATUS_SET/PRIORITY_SET` 兜底（这两个值会被拼进 class 名与文本）。
   **入参也接受英文别名**（`in_progress`/`high`/`pitfall`…）并归一到规范值后落盘；
   表与函数在 `app/fields.py` 一处、MCP 的 `enum` 由 `with_aliases()` 广告 —— 见 [docs/MCP-契约.md](docs/MCP-契约.md)。
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
    （`focusInto` / `backToOpener`）；Tab 不许跑出最上面那层（`trapTab`）；
    滚动位置按 id 记在 `modalScroll`，长弹窗（AI 设置 / 交接包）重开不回首顶
    （**记的是 `.mbody` 的 scrollTop** —— 弹窗框 `max-height: calc(100vh - 100px)` 定高、
    内容在框内滚；别把滚动还给遮罩，否则 sticky 页脚会浮在正文上方，2026-10-18 修过）；
    `.mhead`/`.mfoot` 是 sticky 且带实色背景，否则滚起来标题下透出内容、按钮条会飘走。
14. **反馈分工**：AI 指令窗里的**过程**留在聊天气泡（那是转录，有历史价值），
    但**结果**必须再走一次 `toast()` —— toast 是全站唯一的 `aria-live` 区域，
    而且弹窗会被关掉。少了这句，"确认执行"到底成没成，键盘/读屏用户无从得知。
15. **前端 JS 一处语法错，所有 `data-act` 全部失效、界面变成一块死面板，
    而任何 Python 测试都不会红**。2026-09-20 实测：在 JS 里写了 Python 风格的相邻字符串
    拼接（`"甲" "乙"` —— **JS 没有隐式拼接**），500 多条测试全绿，直到真开浏览器才现形为
    `ReferenceError: openDetail is not defined`。门禁：`test_inline_script_is_syntactically_valid_js`
    对 `web/js-*.js` 每个文件跑 `node --check`（09-28 起 JS 从内联块拆成五个文件，index.html
    已零内联脚本 —— 逐文件检查比当年"整段抽取"更能指出是哪个文件）。改完前端别只跑 pytest，
    至少开一次真浏览器（`scripts/live_ui_check.py` 就是干这个的）。
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
    - **覆盖率门槛必须显式传给 pytest-cov**：`pytest.ini` 的 `[coverage:report] fail_under`
      **对退出码无效**（2026-10-23 实测：只跑 `tests/test_version.py`、TOTAL 16%，
      pytest 仍 exit 0、连一句 fail 都不说；加 `--cov-fail-under=90` 才 exit 1）。
      而这一步的标签上一直写着「含覆盖率门槛」—— **标签在撒谎**，等于删掉几百条用例
      也不会有人红。现在门禁用 `coverage_floor()` 从 `pytest.ini` 读出那个数（**数字只有一处**），
      显式传下去；钉子 `test_coverage_floor_is_enforced_by_the_release_gate` 两头都钉
      （门禁必须传 + 不许塞进 `addopts` —— 塞进去会让文档里那些有意只跑一部分的命令集体假红）。
      `pytest.ini` 那段自己就写着「不要指望这里的 `branch = true` 生效」，`fail_under` 是同一个坑。
    - **进不了分母的模块只能显式排除，且必须有人替它作证**（2026-10-25）：MCP 由真 stdio
      子进程跑、进程内量不到；搬进 `app/` 后总覆盖率 93.8%→81%，门禁当场红。排除写在
      **`.coveragerc`**，佐证是「每个工具都真被调过」+ `mcp_frozen_smoke`（详见 docs/测试约定.md）。
    - **`--quick` 少跑的步骤要在结论里点名**：它跳过的恰好是「改了语义会不会红」那一层，
      却照样印「全部通过，可以发版 ✓」—— 两句话在屏幕上长得一样，含义差一整层门禁。
      现在结论文案分档，并把跳过的那四步逐条列出来。
    - **跳过数必须按文件对账**（2026-10-25）：跳过在报告里与全绿同形 ——
      E2E 那 75+ 条因缺第三级浏览器在 CI 上整份跳过就是这么藏住的。现在门禁**读**
      conftest 那份点名：只有 `test_ai.py` 的 5 条（需 `KANBAN_LIVE=1`）允许跳过，
      别处出现跳过即红（`ALLOWED_SKIPS` / `_skip_audit`）。钉子
      `test_release_gate_audits_the_skip_count`；`KANBAN_LIVE` 在干净 env 里
      被显式摘掉，保证这个数是确定的、不随调用者环境漂。
    - **步骤清单也要有钉子**（2026-10-25 自查发现的缺口）：守门禁自己的钉子此前有三条
      （覆盖率门槛必须显式传、跳过数按文件对账、打包前先预检+清场），**但没有一条管「步骤还在不在」**。
      于是删掉「变异探针」那一步（十几分钟的语义门禁）之后，表格只会从 8 步变 7 步，
      结论**照样**印「全部通过，可以发版 ✓」—— 与当年 E2E 那 75+ 条整份静默跳过同一个形状。
      现在 `test_release_gate_step_list_cannot_shrink_silently` 钉住三组步骤
      （两种模式都跑 4 步 / full 专属 4 步 / `--with-exe` 专属 6 步）**加上标签指向的脚本** ——
      标签留着、命令换掉同样等于把门禁换掉（用 AST 取清单，不靠正则凑字符串）。
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
    这句承诺现在**有钉子看着**（2026-10-25 补）：`test_ci_runs_the_same_release_gate_with_the_exe`
    要求 CI **恰好一个** `run` 块跑 `ci_local.py`、且带 `--with-exe`、全文件不出现 `--quick` ——
    此前三种坏法都不会红：① 改成 `--quick`（跳过的恰好是「改了语义会不会红」那一层，
    而 CI 结论照样绿）；② 删掉 `--with-exe`（不打包、不跑冻结自查）；③ 换成手敲清单
    （正是工作流开头明确拒绝的形状）。另一条 `test_ci_installs_chromium_for_the_e2e_downgrade`
    钉住「CI 真装了 chromium」—— 与「代码里保留第三级」那是一条的对偶：只留代码不装浏览器，
    E2E 仍会整份静默跳过。两条都用**标准库**解析 `run:` 块（PyYAML 不在 requirements-dev 里，
    用它就得 `importorskip` —— 那等于让钉子在干净环境里静默跳过）。
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

29. **条目级尺寸估算只用于展示决策，不参与任何排序或截断**（2026-10-11 对标 claude-mem 的
    渐进式披露）：`handoff_meta.text_size` / `size_note` 是唯一实现，HTTP 的记忆列表行、
    MCP `kanban_list_memories`、前端「项目记忆」面板三处都取它（MCP 与前端经 `kanban.` 前缀 /
    `/api/memories` 复用，不另写一份）。
    三条纪律：① **按实际给出去的那份文本算** —— 脱敏会改长度，报 120 字却给出 90 字，
    消费方的预算就白做了；② `est_tokens` 是**近似**（CJK 1 字 ≈ 1 token、其余 4 字符 ≈ 1 token
    且上取整），**不要拿它做用量记账**，那一路是 `usage.jsonl` 的真实计数；界面上只显示字数，
     token 估算只放在 `title`（近似值不该看起来像精确读数）；③ 召回窗口仍由 `due.recall_memories`
     的类型加权决定，尺寸数字不得反过来影响召回。

30. **「只在该状态里存在的字段」必须自己闭合**（2026-10-19 第六轮吸取，两个字段同一形状）：
     `blocked_reason` 只在状态=阻塞时保留（离开 pop）、`done_at` 只在已完成时保留（离开 pop）。
     不清 = 改回进行中的卡片仍带着过时原因/完成时刻到处跑 —— 各处展示虽然都按状态门控，
     但**字段的语义应当自己成立**，将来新增的读路径忘了门控就是一次泄漏。
     两条配套纪律：① **清也不是静默丢** —— 离开阻塞时 journal 记「原阻塞原因」、停留中换原因记
     「旧 → 新」，`_apply_task` 里 old_status/old_reason 必须在 `t.update()` 之前捕获；
     ② **同生共死的清理只写在状态判定那一处**（create 与 update 两个入口），歧义时就近 goto；
     ③ **「验收后代码又变了」（`repo_watch.stale_done_tasks`）只提示绝不自动改任何状态** ——
     只认提交时间（脏文件没有时间戳）、没有 done_at 的老任务跳过、同分钟不报，count 是全部条数
     （截断留痕，同约定 24）。探针：`blocked_reason_survives_unblock` / `stale_done_never_flags`。

## MCP 服务（让代理直接读写看板）

**完整契约已移入 [docs/MCP-契约.md](docs/MCP-契约.md)**（改工具前必读：实现方式 / 只读模式 / 入参校验 / 协议兼容 / 代理侧脱敏收口 / resources·prompts 复用纪律）。
这里只留一条忘不得的：**stdout 只能有 JSON-RPC，而且必须是 UTF-8** —— 日志一律 `print(..., file=sys.stderr)`，往 stdout 写一个字都会污染协议。

## git 集成（只读，绝不拼 shell）

- `git_snapshot(repo_path, limit, use_cache)`：固定调用 `rev-parse` / `log` / `status --porcelain`，
  **一律列表形式传参**（`["git", *args]` + `cwd=`），仓库路径里塞 `; rm -rf` 之类也只是路径。
  6 秒超时，任何失败都转成 `{ok: False, error}` 而不是抛异常。
- **缓存只给批量场景用**：`_git_cache` TTL 60 秒，服务 AI 监管（逐项目取摘要，避免每条一次子进程）。
  `GET /api/projects/{pid}/git` 必须 `use_cache=False` —— 用户刚提交完点开却看到旧状态最容易被坑。
  总览雷达巡检（`/api/repo-watch` 不带 pid，30s 轮询）也吃这份快照缓存，点名单项目 `?pid=` 绕过要现值
  （2026-10-18 实测：8 真仓库冷扫 1.58s / 40 次子进程，缓存命中 2ms / 0 次）。
- 解析 `git status --porcelain` 时用 **rstrip 不能用 strip**：输出形如 `" M 文件"`，
  状态列前面的空格有意义，strip 掉会把文件名截错（曾经解析出 `ile0.txt`）。
- 读不到仓库时降级：接口 400 带可行动提示；交接包省略该节（不要塞空标题）。

## 数据层几条不显眼但重要的约定

**18 条细则已移入 [docs/数据层约定.md](docs/数据层约定.md)（编号不变）**。
动数据层写路径前必读 —— 那里是 `mutate()` 临界区、幂等键、备份/恢复的「缺键=不动」语义、
`_sanitize()` 的两条自愈不变式、以及脱敏「哪一侧」的判定所在。

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

## 性能基线

**逐项数字已移入 [docs/性能基线.md](docs/性能基线.md)**。这里只留触发线（别忘）：
**列表接口日常 > 300ms 才换分片/SQLite，不要加缓存糊** —— 整份 JSON 读改写是当前有意的取舍。

## 测试约定

**18 条细则已移入 [docs/测试约定.md](docs/测试约定.md)**。五条最常被踩的留在这里：
① **拒绝 mock 自证** —— 慢用例必须是真的（真服务 / 真 MCP / 真浏览器）；
② 覆盖率门槛量的是**分支**覆盖，而且**必须由 `ci_local.py` 显式传 `--cov-fail-under`**
（`pytest.ini` 里的 `fail_under` 对退出码无效，见硬性约定 21）；
③ **用例不能依赖墙钟**（日期类断了要能重跑，别让「9 天后再跑就红」活着）；
④ 改完前端**必须开一次真浏览器**再收工。
⑤ 真实浏览器的探测是**三级降级**（系统 Edge → 系统 Chrome → **自带 chromium**），最后一级不可删：CI runner 没有系统浏览器，少了它就整份静默跳过（钉子 `test_e2e_keeps_the_bundled_chromium_fallback`）。

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
- **CI 已经在真跑**（2026-10-11 接上远端 `origin` 之后）：`.github/workflows/ci.yml` 是**唯一一份**
  工作流（单入口：按锁文件安装 → `ci_local --with-exe` → 上传 exe，只在 `windows-latest`；
  2026-10-24 起另有 container 作业构建镜像推 GHCR）。累计数十次 run 全绿，逐 run 记录（含两次
  E2E 竞态的完整定性过程、线路中断观测、旧 test.yml 退役原因）在 [docs/进度与版本档案.md](docs/进度与版本档案.md)
  的「CI 运行史」。常驻教训：
  - **查 run 用 `?head_sha=` 只认 40 位完整 SHA**：短号不报错、静默返回空 `workflow_runs`，
    极易误读成"run 还没创建"（踩过两次）；先 `git rev-parse HEAD` 取全长。
  - **拉 job 日志**：`.../actions/jobs/{job_id}/logs` 会 302 到 blob 存储，跟随重定向时必须
    去掉 `Authorization` 头（`curl -sL` 自动去；urllib 自己跟会 401）；仓库路径写错一个字母
    API 回 `404` 而**不是**空列表，别读成"run 还没创建"。
  - **前端 E2E 在 runner 上红的头号形状**是用例赌"本机 fetch 比断言快"（两起同型）：
    定性用 `page.route`/`page.evaluate` 扣住对应请求做确定性复现，修法一律 `wait_for_function`
    条件等待，不加长睡眠。
  - **探活与推送各自独立成败**：权威确认只看 `git push` 的输出（`a1b2c3..d4e5f6 main -> main`）；
    探针必须打同一个 git 端点（`git ls-remote --heads origin main`），**不要** `curl https://github.com`
    （HTML 站点与 git 服务不是一条路）；shell 里 `git ls-remote | head` 的管道会盖掉退出码，
    直接看命令退出码。
  - **线路中断处置**：`github.com:443` 连不上而 `api.github.com` 200 = 主站被干扰不是断网；
    本地提交一个不动，有界重试（6-8 轮、轮间约 25s）后收手等恢复，别反复重试刷屏
    （2026-10-24 一晚三次，最长的一次多轮后才恢复）。
  - `git push` 只推提交，不会带走工作区未跟踪目录（`DELIVERY/` 之类推送时是安全的）。
  - **改 workflow 一律按"它会被真执行"来写**：runner 上没有系统 Edge/Chrome（Playwright
    chromium 降级真验过）、双产物打包、ASCII 资产名、tag 同源校验都已在干净 runner 上真跑过。
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
  **这条判据吃 mtime，所以任何"改文件又还原"的工具都会污染它** —— 变异探针就踩过
  （2026-10-18 起已修：`_restore_mtime` 把时间戳一起拨回去）。看到它点名一串文件、
  而"内嵌前端就是仓库这一份"却是过的，先怀疑是不是刚跑过探针，别急着重打包。
- **数据目录**：默认 `%USERPROFILE%\.ai-kanban`（Windows 下优先 `USERPROFILE` 而不是 `HOME`，
  否则受 Git Bash 的 HOME 干扰）。测试请用 `KANBAN_DATA_DIR` 隔离。
- **端口占用不等于自己活着**：`desktop.py` 只看端口通不通，18320 被别的程序占了会在窗口里
  打开别人的页面。排查异常时先确认进程。
- **调度线程在模块 import 时启动**（`start_scheduler()`，幂等）。用 `--reload` 调试时每个 worker 一个线程，
  靠配置里的 `last_period` 防同周期重复触发。
- **LLM 输出不可信**：一律走 `_extract_json()` 容错提取；AI 结果的落地必须经用户确认（人工闸门）。
- **LLM 不可用要有降级**：AGENTS.md 生成失败会退回 `_build_agents_md_local()`，不要直接抛错。
- **窗口渲染**依赖系统 WebView2 运行时（`gui="edgechromium"`）。
- **`release_regression.py --no-ai` 报「只读模式下写操作被拒」时，先分清两种原因**（2026-10-25 补第二种）：
  ① **编码**（历史原因）：它用 `decode("utf-8", "replace")` 读 `mcp_server` 子进程的 stdout，
  子进程按控制台码页（cp936）输出时，断言里匹配的「只读」被替换符打断。**看板逻辑没问题**
  （`_ensure_writable()` 就在 `t_add_task` 第一行）—— 别去改只读开关。
  ② **端口上是别人的实例**（2026-10-25 实测，同日撤销了"先查编码"这个默认反应）：`PORT` 当时
  写死 18399，残留实例占着它 → 所有 HTTP 打到别人身上（它报的 `data_file` 指向上一轮的临时目录），
  而 MCP 子进程拿的是本轮的**空**目录 → `mcp_readonly` 默认 False → 写没被拒。
  现在它**挑空闲端口 + 起跑自证**（`app.service.free_port()` / `_own_instance_guard`），这条不再可能：
  端口上要是别人，它**第 1 秒**就红并点名那个 data_file。
  **教训**：一条"已知假红"的注释会变成下一次误判的燃料 —— 遇到这条红，先看它**有没有点名**，
  没点名才有可能是编码。

## 当前状态与下一步

**状态：v1.9.6（以 `app/version.py` 为准）、可用。** 版本号是单一事实来源，改一处即贯通
（界面版本条 / `/api/health` / MCP `serverInfo` / exe 的 PE 版本资源 / `tests/test_version.py`），
且 `tests/test_docs.py` 会核对本文件与 README 写的是同一个数。

**验证基线**：`pytest` 877 收集（872 通过 / 5 跳过，跳过的需 `KANBAN_LIVE=1`）/ 分支覆盖率 93%（92.56%，门槛 90）/ 36 个测试文件、826 个 `def test_` 且无重名；
变异探针 113 条全被杀。**逐轮明细、每轮改了什么为什么、性能基线** → [docs/进度与版本档案.md](docs/进度与版本档案.md)。

**下一步（按价值排序，详版在档案里）**：
1. **`app/main.py` 的拆分已到头**（3596 → 3255 行，历次拆出与纪律见目录表与档案）：
   再往下只剩数据层本体（mutate / _load_data / 自愈备份）—— **那是临界区的心脏，不拆**；
   守住「写操作必须走 `mutate()`」。`ai_command` 的提示词**有意留在 main**
   （`test_ai_command_prompt_keys_on_ids_not_titles` 直接 inspect 端点源码，搬它要连测试迁，收益不值）。
   以后搬任何模块，先 `grep` 探针表把门禁的 `file` 指针一起搬（哑火教训见档案）。
2. **发版节奏（2026-10-24 起）**：`ci_local --with-exe` 全绿 → 推 main 等 CI 绿 →
   打 tag（`v` + version.py 的数，不同源 CI 会拦）→ CI 自动发 GitHub Release（两个 exe）
   并推 GHCR 镜像。仓库已更名 `fengxingxuerong/ai-handoff-kanban`（旧 URL 自动重定向），
   配了 description/topics 与英文 README；官方 Registry 的元数据在仓库根 `server.json`。
3. **条件触发的路线图（都已想清楚并论证，别提前做）**：evidence 完成证据（等真实
   「缺证据导致接手失败」案例）、任务 claim/租约（等同时跑 2+ agent 改同一块板）、
   SQLite（等列表接口日常 >300ms）、Smithery（等将来发包）。论证与触发线见档案
   「不要再做的事」与各轮叙事。分发侧的待办是用户的登录动作（glama/表单/Registry 发布），
   清单在 DELIVERY 材料，不属于工程。

> ⚠️ **本文件可能被加载预算截断**（实测 65536 字节）。若你读到的内容在中途断掉，
> 按上面那张「去哪看」的表去 `docs/` 取对应小节 —— **没读到不等于没有**。
