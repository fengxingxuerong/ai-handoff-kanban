# MCP 服务契约

> 本文件是 `AGENTS.md` 的延伸（2026-10-24 按「是否每次都要读」切出）。
> **编号保持原样**：这里说的「数据层约定 11」与代码注释里引用的仍是同一条。
> 规则主文件是 [AGENTS.md](../AGENTS.md)，命令、硬性约定与高频坑都在那里。


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
    **可做列表必须带阻塞标记**（2026-10-19 v1.8.1）：状态=阻塞的任务也在 `next` 里
    （先解阻塞也算推进），条目带 `status`/`blocked_reason`，文本行由 `_blocked_mark()`
    加 " · ⛔ 阻塞中：原因（先解阻塞，别直接开工）"；blocked 小节同时说清"等谁"与
    "为什么卡"。实盘教训：不带标记的时代理把它当普通待办直接开工，而同一条任务在
    /today 里明明带着标记 —— **列表允许它出现，但必须让它无法被误认**。
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
rem 只装 exe 的用户走这条（同一份代码，控制台产物）：
echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | dist\AI交接看板-MCP.exe
.venv\Scripts\python.exe -m pytest tests\test_mcp_server.py -q --no-cov
```
