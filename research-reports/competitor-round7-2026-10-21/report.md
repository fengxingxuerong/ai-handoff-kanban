# 第七轮竞品对标（2026-10-21）

> **方法**：GitHub search API，17 组关键词（AI agent task board / agent handoff / MCP kanban /
> 任务看板 / 智能体任务管理 / topic 检索 / 最近两天新仓库），带 GCM 凭据，排除前六轮已收录的
> 349 个仓库。命中 321 个唯一仓库，按赛道人工过滤后对 33 个候选拉全文 README（2 个仓库已 404/空仓）。
> 判定沿用：**值得盯 / 已覆盖 / 暂缓 / 不吸收**。星数与推送时间为 2026-10-21 当日 GitHub 数据。
>
> 检索噪声说明：不限创建日期的宽查询会把 awesome 列表、通用 agent 框架带进来（本轮 321 个里
> 大部分是 long-tail），下表只保留与「多项目任务板 + 交接/上下文 + 编码智能体」直接相关的。

## 一、最接近的 7 个（重点盯）

| 仓库 | ★ | 最近推送 | 形态 | 一句话 | 相对我们的强项 |
| --- | ---: | --- | --- | --- | --- |
| **ayanamislover/AyanamiTaskManager** | 104 | 10-20 | Windows 桌面（Rust + WebView2）/ SQLite / MCP | 「让 Codex、Claude 与每一次 Session 共享同一份可信项目事实」 | 会话 brief/delta、领取与租约、验收证据、不可变修订、自动写各客户端 MCP 配置 |
| **rasimme/FlowBoard** | 96 | 10-13 | 本地 Web / OpenClaw 插件 | 给 agent 的项目工作区：目标、决策、任务状态、spec 随项目走 | 上下文按需懒加载、任务 claim/checkpoint/review、Idea Canvas、Telegram |
| **PerpetualSoftware/pad** | 195 | 10-20 | 单二进制 / SQLite / CLI + Web | 「agent 时代的项目管理」 | CLI 一等公民、变更版本史（人/agent 归属）、playbook 入职流程 |
| **BradGroux/veritas-kanban** | 843 | 10-19 | 本地 Web / TypeScript | 本地优先任务板，编排/工作流/治理都是可选层 | 治理门、workflow engine、OpenClaw/webhook、可插拔集成面 |
| **saltbo/agent-kanban** | 493 | 10-02 | Cloudflare 云原生 | agent 优先的 mission control，把人类移出执行环 | 人工审核门、执行来源（provenance）、依赖、公开只读板 |
| **nimbalyst/nimbalyst** | 1946 | 10-19 | 桌面（macOS/Win/Linux）+ iOS | 可视化 agent 工作区：会话、worktree、任务、文档、代码 | session kanban、红绿 diff 审阅、并行 worktree、MCP 可视化 |
| **antopolskiy/kanban-md** | 225 | 10-05 | Go 单二进制 / 文件 / TUI | 面向自主 agent 循环的文件看板 | 任务 claim + 工作区隔离、`--compact` 省 token、随仓库走的看板 |

补充两个「单点很像」的：

- **shiquda/roadmap-skill**（82★，最后推送 2026-03-18）：MCP + 看板 + 依赖图，本地优先，和我们的
  `graph` / 任务依赖几乎同一思路；已停更，功能面比我们窄。
- **defremont/Shipyard**（29★，10-09）：Electron 本地指挥中心，项目/任务/Git/终端/agent 一屏，
  MCP 29 工具、JSON 存储、可选云同步。更偏「开发工作台」，没有交接物/多入口投影。

## 二、第二梯队与只沾一边的

| 仓库 | ★ | 定位 | 与我们的关系 |
| --- | ---: | --- | --- |
| yaront1111/Moe-s-Tavern | 33 | IDE 内的 agent 任务板，角色/计划/QA/账号轮换 | 强在「多 agent 角色与审核流」，弱在跨项目总览与交接物 |
| kirgx/task-rail | 3 | Windows 三列小浮窗 + MCP bridge | 形态最像（Windows 桌面 + MCP + 验收 + 版本防冲突），但只做单项目轻量板 |
| Simon-Wong-hjz/switch | 0 | Codex/Claude Code 会话看板，读最新回复、一键 resume | 解决「多会话切换」，不解决「项目交接」；可借鉴「读回复再切换」 |
| hasanyilmaz/operon | 262 | Obsidian 任务插件（人 + agent） | 笔记库内任务，不是独立看板；验证「Markdown 为真相源」路线 |
| OthmanAdi/planning-with-files | 27550 | skill/hook：把计划写进磁盘，扛 `/clear` | 交接/持久化赛道最强的「非看板」竞品；证明痛点真实 |
| dataking5d/context-handoff / ziucode/agent-session-bridge / greatfreedom/handoffd / qurs/cc-autohandoff | 0-1 | 交接文档 / 跨 provider 续接 / 上下文阈值自动交接 | 一整波「handoff skill」新仓库；交接文档本身正在商品化 |
| Fission-AI/OpenSpec | 72532 | spec-driven development | 方法论层，和我们的 acceptance/交接说明可互补，不是直接竞品 |
| paperclipai/paperclip / stablyai/orca / EKKOLearnAI/ekko-studio / usekaneo/kaneo / Lexus2016/claude-code-studio | 9.5k-105k | 团队 agent 编排 / 多 agent 工作台 / 通用 PM | 体量大、偏团队与执行编排；不是「单人频繁换 agent」这个切口 |

## 三、和本项目的功能对照

图例：✅ 有且较完整；◐ 有但不完整/另一种做法；— 没有。

| 能力 | 本项目 | ATM | FlowBoard | Pad | Veritas | agent-kanban | nimbalyst | kanban-md |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 本地优先 / 数据不出机 | ✅ | ✅ | ✅ | ✅ | ✅ | ◐（云） | ✅ | ✅ |
| Windows 桌面单文件 exe | ✅ | ✅（安装/便携） | —（本地 Web） | —（二进制 + Web） | —（本地 Web） | —（云） | ✅（Electron） | —（TUI） |
| 多项目总览 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ◐（按仓库） |
| 项目级交接说明 / 下一步 | ✅ | ✅ | ✅ | ◐ | ◐ | ◐ | ◐ | ◐ |
| 一键交接包（人读 + 机读） | ✅ | ✅ brief | ✅ 上下文层 | ◐ | ◐ | ◐ | ◐ | ◐ |
| 9 种 agent 入口文件投影 | ✅ | ◐（AGENTS/CLAUDE/skills） | ◐（AGENTS/CLAUDE） | ◐（skill） | ◐ | — | ◐ | ◐（skills） |
| MCP 工具面 | ✅ 23 | ✅ core/memory 两档 | ◐ | ✅ | ✅ | ◐（OpenAPI） | ✅ | ✅ |
| 任务依赖 / 拓扑 | ✅ | ✅ | ◐ | ◐ | ◐ | ✅ | ◐ | ✅ |
| 任务 claim / 租约 / 并发归属 | — | ✅ | ✅ | ✅（claim/release） | — | ✅ | ◐（worktree） | ✅ |
| 验收标准 | ✅ 字符串清单 | ✅ 标准 + 证据 | ✅ | — | — | ◐（审核门） | — | — |
| 会话 brief / delta | —（全量 context） | ✅ | ✅ 懒加载 | ◐ | ◐ | ◐ | ◐ | ◐ |
| 记忆 / 跨项目知识 | ✅（时效 + 加权召回） | ✅（不可变修订） | ◐ | ◐ | — | — | ◐ | — |
| LLM 自我监管 / 健康分 | ✅ | — | — | — | ◐（治理） | ◐（审核） | — | — |
| 仓库变化雷达 / 验收后变化 | ✅ | — | — | — | ◐（Git 集成） | ◐ | ◐（Git 集成） | ◐ |
| 自动写各客户端 MCP 配置 | — | ✅ | ◐ | ◐ | — | — | ◐ | ✅ |
| 手机 / 远程访问 | — | ✅（可选中继） | ✅（Telegram/隧道） | ◐（Cloud） | ◐（自托管） | ✅（公开板） | ✅（iOS） | — |
| 团队 / 权限 / 云同步 | — | — | — | ◐（Cloud） | ◐ | ✅ | ◐ | — |

## 四、这轮看出来的真缺口（按性价比排序）

> 前提：本项目当前阶段的重心是验证与稳定性，不是继续加功能（见 AGENTS.md「不要再做的事」）。
> 下面只记录「如果要做，先做哪条」，不自动开工。

1. **上下文按需切片 / delta（FlowBoard、ATM）** —— 最贴合我们的主场景。
   现在我们给的是整份 `context`；对手给的是「开工 brief + 上次之后的变化」。
   最小落点：`/api/context/{pid}` 与 MCP `kanban_context` 增加可选的 `sections` / `since`
   参数（复用 `omit_sections` 与 journal/记忆的时间戳），不新增存储。
   收益：换 agent 时少读无关段落；成本：一个参数 + 两处渲染 + 测试。
2. **任务 claim / 租约（kanban-md、FlowBoard、ATM）** —— 多 agent 并行时才需要。
   现在只有自动分配，没有「谁正在做、租约到什么时候」。真要做会动任务状态机、MCP、界面与
   过期回收，属于中等改造。**暂缓**，触发线：用户实际同时跑两个以上 agent 改同一块板。
3. **完成时带证据（ATM）** —— 与现有 acceptance / `done_at` / `stale_done` 同族。
   最小落点：任务完成时可选写一条 `evidence`（提交 sha / 测试命令 / 文件 ref），
   进交接包与详情；不做证据状态机。**候选**，成本低。
4. **一键写各客户端 MCP 配置（ATM、AgentKanban）** —— 我们 23 个工具已就绪，但装 MCP 仍要手抄。
   涉及改别人家配置文件（备份 + 最小合并 + 明示写入清单），安全姿势要先设计。**暂缓**。
5. **变更版本史（Pad、ATM）** —— 谁在什么时候改了哪个字段。我们有 journal + 备份，
   单机单人下收益有限；全字段版本化会让 `projects.json` 再次变胖。**不吸收**。

## 五、明确不吸收（本轮新增）

- **agent 编排 / worktree 并行 / 团队权限 / 预算治理**（orca、nimbalyst、Shipyard、paperclip、
  veritas、agent-kanban）：不是这个项目的赛道，且与「单人频繁换 agent」的切口相反。
- **云同步 / 手机 / 公网分享**（ATM 中继、FlowBoard Telegram、Pad Cloud）：违反「交接物默认不出本机」
  的硬约定；维持本地直链 + 脱敏导出。
- **SQLite / 全字段版本化 / 证据状态机**：当前整份 JSON 在真实规模下仍是毫秒级，
  换存储层的触发线仍是「列表接口日常 > 300ms」。
- **把看板塞进仓库 / 每仓库一块板**（kanban-md、nimbalyst）：与「跨项目总览 + 集中交接」冲突。
- **spec-driven 全流程**（OpenSpec、FlowBoard Specify）：我们已有背景/交接说明/验收标准，
  再叠一层 spec 生命周期只会增加填写负担。

## 六、结论

- 这个赛道 2026 年下半年明显变拥挤：**「本地优先 + MCP + agent 任务板」已成标配**，
  单做「看板 + MCP」不再有区分度；「handoff / session continuity」也从卖点变成一整波 skill 的通用能力。
- 我们的位置仍然清楚：**不是 agent 执行平台，而是「人换 agent 时的项目交接台」**。
  真正的护城河是四件一起给：多项目总览 + 一键多形态交接物（含 9 入口投影）+ 记忆/验收/依赖的
  上下文质量 + LLM 自我监管。对手大多只覆盖其中一两件。
- 最需要盯的两个：**AyanamiTaskManager**（同形态、同平台、会话模型更强）与
  **FlowBoard**（上下文层 + agent 原生看板）。前者证明 Windows 本地桌面这条路线有需求，
  后者证明「按需上下文」是下一层竞争点。
- 本轮不建议直接改代码。若只做一条，选 **上下文切片 / delta**；它是纯读取侧改造，
  与现有 `context` / `_budget_note` / 记忆召回同一套口径，风险最低。
