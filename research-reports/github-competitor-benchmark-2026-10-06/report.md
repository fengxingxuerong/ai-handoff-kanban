# Research Report: AI 交接看板 × GitHub 同类竞品全方面对标

- Date: 2026-10-06
- Goal: 在 2026-10-01 两轮对标（通用开源 PM + makeplane/plane）基础上，做增量全方面竞品扫描，产出可落地的优化吸取方案
- Status: research complete, awaiting output-format decision

## Research Questions

1. 2026-09 ~ 2026-10 期间 GitHub 上新出现或重大更新的同类/相邻竞品有哪些？
2. 五大类目（AI 原生任务管理 / 通用开源 PM / Agent 记忆层 / 会话捕获与观测 / 交接上下文工具）各自的头部竞品有什么可借鉴设计？
3. 哪些优点能在本项目红线（单机、隐私不出本机、脱敏、单 exe、零第三方前端依赖）内被吸收？
4. 既有对标结论需不需要修正（vibe-kanban 关停事实、Plane 新动向、Beads 生态等）？

## Search Log

| # | 查询 | 渠道 | 动机 | 关键命中 |
|---|---|---|---|---|
| 1 | open source AI project management tools GitHub 2026 agent task tracker | web | 广度扫描 | OneManCompany 趋势、T3/Kandev/herdr/Lific 栈、Multica 线索 |
| 2 | vibe-kanban shutdown BloopAI alternative 2026 | web | 交叉验证 10-01 文档的关停结论 | nimbalyst/aidenapp/vibefactory/parallelcode 四源一致：2026-04-10 Bloop 关停 |
| 3 | Beads Steve Yegge issue tracker for AI coding agents | web | 记忆升级类目 | beads 27,637★（10-05）、深度技术调研 gist（Dolt 后端/压缩/MCP） |
| 4 | AI coding agent session handoff context capture tool GitHub 2026 | web | 会话捕获类目 | SessionRelay、ai-context、Session-Agent、mattpocock handoff skill（含脱敏）、scrollback、agent-dump、AgentRecall、ai-data-extractor、Wake |
| 5 | Mem0 Letta basic-memory agent memory layer comparison 2026 MCP | web | 记忆层类目 | Mem0 41→56k★；Letta 三层记忆+Context Repositories（git 版记忆版本控制）；Zep/Graphiti 双时态；基准数字争议（厂商 LoCoMo 复现即跌） |
| 6 | claude-task-master Task Master AI parse-prd next task workflow | web | AI 原生任务管理 | 27.2k★（4月）、36 个 MCP 工具分三档加载、npm 3 万/周、MIT+Commons Clause；已 49 天无发版 |
| 7 | Backlog.md markdown CLI kanban AI agents | web | AI 原生任务管理 | 官网+README 镜像：三评审检查点、DoD 默认清单、草稿态、看板导出 |
| 8 | iAmCorey Wake session manager features | web | 会话捕获深挖 | 星标轨迹 347→766→1374；FTS5 trigram 中文/代码全文检索；wake-mcp 只读服务器 |
| 9 | AGENTS.md standard Linux Foundation AAIF ecosystem 2026 | web | 标准演进 | AAIF（2025-12-09 成立）247 成员组织；AGENTS.md 60k+ 仓库采用；Linux 内核讨论采用（2026-09-25） |
| 10 | Multica human agent hybrid team project management | web | 直接竞品深挖 | "像 Linear 但 agent 是一等队友"；2026-01 上线，8 月 46k★，9-30 51.7k★（+292/天） |
| 11 | makeplane Plane changelog September October 2026 AI agents | web | 复核 10-01 结论时效 | **Plane AI Memory（09-15）+ Plane Agents（09-18）上线**；Plane Compose git 化项目模板（09-23）|
| 12 | pingdotgg t3code what is agent harness Theo 2026 | web | 编排控制面类目 | "不是 harness，是管理 harness 的应用"；17.6k★（8月）→ API 25.7k★（10-06） |
| 13 | GitHub search API: agent kanban orchestration created:>2026-04-01 | API | 新品类普查 | 153 个新仓库（agetor/hermes-ai-team/oh-my-symphony/flow/gemba-core/contextops/maestro…），无一超百星——品类爆发但无整合者 |
| 14 | GitHub search API: coding agent session memory | API | 记忆品类普查 | context-mode 25,493★（上下文窗口优化）、obsidian-second-brain 4,679★、magic-context 2,270★、deja-vu 1,133★（35+ agent）、cass 442★ |
| 15 | GitHub search API: handoff agent context | API | 交接品类普查 | Continuous-Claude-v3 3,940★（hooks+ledgers+handoffs）、waggle 646★（可解析工件引用 token）、catchup 75★、OrbitRelay（交接质检） |
| 16 | GitHub repo API 批量核实 43 个仓库 | API | 权威星标/活跃度 | 见 github_metadata*.json；multica 52,016★、beads 27,658★(10-06 仍推送)、task-master 28,160★(4-28 停更)、mem0 66,642★、graphiti 31,467★、langfuse 35,420★、mattpocock/skills 277,285★ |

## Findings by Dimension

### A. 直接竞品层：「人机同板」成为2026 最热线赛道

| 仓库 | 星标* | 活跃 | 定位 | 对本项目威胁/借鉴度 |
|---|---|---|---|---|
| multica-ai/multica | 52,016 | 10-04 | Human+Agent 团队工作区（Linear 形态，agent 一等队友） | **最高**：赛道正面重叠但走团队/云端/重栈路线（Go+PG17+Electron+Expo），本项目差异化仍在单机交接+脱敏 |
| pingdotgg/t3code | 25,713 | 10-06 | agent harness 控制面（手机/Web/桌面遥控本机 agent CLI） | 中：执行编排侧，与交接定位互补 |
| BloopAI/vibe-kanban | 28,266 | 09-19（社区维护） | 已关停（2026-04-10），README 自挂 sunsetting | 商业教训样本：纯免费无模式→关停 |
| smtg-ai/claude-squad | 8,572 | 08-20 | tmux+worktree 多 agent 终端管理 | 低：纯执行编排 |
| makeplane/plane | 60,415 | 10-05 | **09-18 起有 Plane Agents + 09-15 AI Memory** | 高（修正 10-01 结论）：通用 PM 巨头正式进入 agent 赛道 |
| hcengineering/platform (Huly) | 27,835 | 10-02 | 一体化团队平台 | 低：团队赛道 |

\* 2026-10-06 经 api.github.com 实时核实。

**Multica 可借鉴设计**（README 一手）：① run 级执行日志（每个 tool call/命令/报错带时间戳可回放）；② Inbox——「agent 需要拍板才 ping 你，不是每步都 ping」的通知过滤哲学；③ 失败 run 自动重试/超时并说明原因；④ 用量分析按 run/agent/issue 三维记账；⑤ Skills——把解决过的问题沉淀成所有 agent 复用的 playbook；⑥ Autopilots——cron 定时跑 standup/审计/报告；⑦ 评审门（work lands in review, not in main）；⑧ 遥测自律：自托管每日仅发匿名快照、DO_NOT_TRACK=1 可关、文档写明采集内容。

**Plane 可借鉴**：AI Memory 分用户/项目/工作区三级、**可见可编辑**（记忆不是黑箱）；Agents 可被指派工作项、评论提及、触发器/定时驱动——「agent 是成员」的数据模型。

### B. AI 原生任务管理层（agent-first issue tracker）

| 仓库 | 星标 | 活跃 | 核心思想 |
|---|---|---|---|
| steveyegge/beads | 27,658 | 当日仍推送 | git/Dolt 版控图状 issue 追踪器：`bd prime` 注入开工上下文+持久记忆、`bd ready` 自动报可领取任务、`--claim` 原子领取、层级 ID（Epic→Task→Subtask）、语义压缩（旧任务记忆衰减摘要）、消息型 issue（threading+ephemeral）、schema 版本守卫（旧二进制开新库给可行动报错）、`bd setup <agent>` 按工具装 hooks |
| eyaltoledano/claude-task-master | 28,160 | **04-28 停更** | PRD→任务分解、复杂度分析、36 个 MCP 工具分三档按需加载 |
| MrLesk/Backlog.md | 6,938 | 09-28 | Markdown 即任务（进仓库随 git 版控）、**三评审检查点（spec→plan→code）**、可复用 DoD 默认清单自动挂每个新任务、依赖双向可视（等谁/谁等我）、docs+decisions 一等公民、看板导出 Markdown、web 只监听 127.0.0.1、`--json` 稳定版本化输出+watch |
| snarktank/ai-dev-tasks | 7,796 | 2025-11 停更 | YAML 任务+agent 指令 |
| buildermethods/agent-os | 5,472 | 08-29 | 标准发现→按任务注入标准→spec 塑形的「规范注入」路线 |
| emiliojohann/FocusClaw | 18 | 10-05 | agent-native 任务管理（local-first API+SQLite）——长尾新秀 |

**对本项目最值钱的四个点**：Beads 的 `prime/ready/claim` 三件套（开工即注入上下文+可领取队列+原子领取——与本项目 `kanban_next_task`/交接包同族但更贴 agent 工作流）；Beads 的**语义压缩**（旧记录衰减摘要，治本项目 journal 膨胀痛点，AGENTS.md 已承认 journal 是最先撞线的维度）；Task Master 的 **MCP 工具分档加载**（23 个工具全量暴露在 tools/list 会吃客户端上下文预算，可学只读档/常用档/全量档）；Backlog.md 的**三评审检查点+DoD 默认清单**（本项目任务模型没有验收标准字段）。

### C. 会话捕获与检索层（本项目「会话捕获」功能的正面竞品，2026-08 起爆发）

| 仓库 | 星标 | 核心能力 | 借鉴点 |
|---|---|---|---|
| iAmCorey/wake | 1,409（39 天从 0） | 22 种 agent 会话统一浏览/FTS5 trigram 全文检索（中文+代码子串）/一键终端恢复/统计热力图/**wake-mcp 只读 5 工具**/wake-cli 与 MCP 输出逐字节一致/远程主机会话镜像/SessionStart hook 会话启动即注入近期会话 | **检索深度**（本项目会话捕获只能扫描→提炼，不能全文搜历史会话）；**诚实能力矩阵**（Model/Via 列注明"agent 本地数据没记录该字段，不是缺功能"）；星标/置顶存自己库、删除进系统回收站+tombstone、索引可重建而偏好独立存活 |
| mksglu/context-mode | 25,493 | agent 工具输出沙箱化（上下文占用降 98%） | 上下文预算管理思路（同本项目 context_budget） |
| eugeniughelbur/obsidian-second-brain | 4,679 | Karpathy LLM Wiki 进化版：库自我重写、矛盾自动调和、**OKM 知识代谢（每条事实标记 timeless/dated/pointer + 双时态）**、AI-first 笔记格式（"## For future agent" 前言+时效标记+引用）、4 个定时 agent（晚报/晨会/周审/vault 体检）、CRITICAL_FACTS.md ~120 token 载入身份 | **记忆分层与时效语义**直接对应本项目记忆面板的决策/坑/事实三分类——可加"时效性"维度与过期复核；定时 agent 与本项目监管调度同构 |
| vshulcz/deja-vu | 1,133 | 从 35+ agent 会话历史构建记忆 | 覆盖广度 |
| Dicklesworthstone/cass_memory_system | 442 | 程序性记忆：散落会话史→持久 playbook | 「经验→可复用流程」的沉淀方向（与 Multica Skills 同向） |
| parcadei/Continuous-Claude-v3 | 3,940（1 月后停更） | hooks+ledgers 维护跨会话状态、MCP 执行 | 账本式状态维护 |
| modiqo/waggle | 646 | 交接用**可解析工件引用**（~30 字节 token 替代大段复制，解析时还原） | 交接包引用化：refs 已有雏形，可学「引用而非复制」进一步压包体积 |
| xingkaixin/agent-dump、SamCuipogobongo/scrollback、EwanJasper/SessionRelay、hengruiyun/Session-Agent 等 | <100 | 会话导出/检索/接力长尾 | 品类热度佐证；SessionRelay（华人作品）五源捕获+中文语义检索+MCP 16 工具+遗忘权+HOP 交接包，思路与本项目最像但无看板 |

### D. Agent 记忆基础设施层

- **mem0ai/mem0** 66,642★（当日活跃）：extract-and-retrieve CRUD 记忆 API，Apache-2.0，agent 集成（Claude Code/Codex/OpenCode/Antigravity）
- **letta-ai/letta** 25,039★（主仓已成 landing page，代码迁至 letta-code 3,525★ 当日活跃）：Core/Recall/Archival 三层自编辑记忆；**Context Repositories＝git 版控记忆（diff/回滚/分支）**
- **getzep/graphiti** 31,467★：实时时序知识图谱，双时态有效期窗口（current truth vs superseded history）
- **basicmachines-co/basic-memory** 4,104★（当日活跃）：本地 Markdown 知识库，agent 对话落盘可人工编辑，Obsidian 兼容
- **langfuse/langfuse** 35,420★：agent 评测+观测（Trace/evaluate/improve）；AgentOps 5,886★（6 月后放缓）
- 社区警示（adityaarsharma 2026-09-30）：**看星标选记忆层会翻车**（letta 主仓 24.6k★ 实为壳）；厂商基准复现即跌（Mem0 LoCoMo 94.4% → 第三方 73.8%）——本项目引用竞品数据时须同样标注口径

### E. 标准与生态位

- **AAIF**（Agentic AI Foundation，Linux Foundation，2025-12-09 成立）：OpenAI/Anthropic/Block 捐出 AGENTS.md/MCP/goose；247 成员组织（2026-08-14 TechRepublic）；AGENTS.md 被 60,000+ 开源仓库采用、25+ 工具支持；Linux 内核讨论引入 AGENTS.md（2026-09-25）——**本项目「8 入口文件投影」踩在正确的标准上**
- **Skills 生态爆发**：mattpocock/skills 277,285★（`npx skills add` 安装范式）；Wake/Beads/Multica 都发行了自己的 skill 包——**「可安装的 agent skill」成为新入口文件格式**，本项目尚未覆盖（可新增第 9 类投影目标：.claude/skills/ 或 .agents/ 技能包）
- HN 热议（2026-10-04）：《Agents don't need memory, they need documentation》——文档优于对话检索的观点与本项目「交接包/AGENTS.md 为中心、会话提炼只是输入」的哲学同向，可作定位背书

### F. 长尾新品类（GitHub search created:>2026-04-01）

153 个 agent kanban 编排新仓（无一百星以上）、FocusClaw（agent-native 任务管理+local-first API）、amenbo（"记录活在 AI 之外"）、roadmapped（仓库内平铺文件 PM）、agetor（local-first kanban harness 编排）——**品类在爆炸但没有整合者**，「交接+记忆+会话+看板」四合一的单机产品仍是空位。

## Cross-checks（交叉验证记录）

| 事实 | 来源 A | 来源 B | 结论 |
|---|---|---|---|
| Bloop AI 2026-04-10 关停 vibe-kanban | nimbalyst/aidenapp/vibefactory/parallelcode 四家独立报道 | GitHub API：仓库未 archive、最后推送 09-19（社区维护） | 一致成立；补 nuance：仓库转社区维护非删除 |
| Multica 星标轨迹 | besthub 46k（08-20）、aibars 51,765（09-30） | API 52,016（10-06 实测） | 一致，单调上升 |
| Task Master 停更 | chatforest（04-20：49 天无发版，最后提交 04-23） | API pushed_at=2026-04-28 | 一致 |
| Plane Agents/AI Memory 上线 | chatgate.ai（09-22 报道 09-18 上线） | plane.so 官方 changelog（09-15 AI Memory）一手 | 一致（官方一手确认 AI Memory；Agents 日期取二手+Product Hunt 佐证） |
| Wake 星标轨迹 | zentor 347（08-21）→ 上海交大 766（09-11）→ baeseokjae 1,374（09-28） | API 1,409（10-06） | 一致 |
| Beads 星标 | aibars 27,637（10-05） | API 27,658（10-06） | 一致 |

## Source List（按权威度）

**高（官方一手）**：api.github.com 43 仓库元数据+5 组搜索（本地存档 `github_metadata.json`/`github_metadata_round2.json`）；7 份官方 README 全文（`readmes/`）；plane.so/changelog 官方更新日志。
**中（知名媒体/行业）**：TechRepublic（AAIF 247 成员）、OSSInsight（zero-human company 趋势）、mlllm.io（agentic 栈盘点）、linux.do（scrollback 发布帖）。
**低-中（垂直博客/二手，仅作线索）**：nimbalyst/aidenapp/vibefactory/parallelcode/continuumcode/clauday/baeseokjae/80aj/jxxy/夜雨聆风/CSDN/掘金/dev.to 等评测与介绍；所有关键数字均已回落到 GitHub API 一手核实或明确标注二手口径。

## Synthesis（综合结论要点，终稿按用户选定格式展开）

1. **赛道验证**：2026 年是「agent 工作管理」品类大爆发之年（Multica 52k★ 九个月、Plane 亲自下场、153 个新 kanban 编排仓）——本项目 2026-09 的判断（AI 会话自动捕获成品类趋势）被完全验证，且窗口仍在。
2. **定位仍然独特**：没有竞品同时做「单机 exe+数据不出机+全链路脱敏+交接包/AGENTS.md 标准投影+MCP 23 工具+记忆层+会话提炼」。Multica/Plane 走团队云、Wake 只做会话、Beads 只做 issue 图、记忆层工具无看板。**四合一单机位仍无人占**。
3. **十大可吸收优点**（按价值/成本，全部不违红线）：
   ① Beads 语义压缩治 journal 膨胀（本项目已知痛点，AGENTS.md 承认最先撞线）；
   ② MCP 工具分档暴露（Task Master 36 工具三档加载，省客户端上下文）；
   ③ 任务级验收标准/DoD 默认清单（Backlog.md，交接质量的抓手）；
   ④ 会话历史全文检索+MCP 检索工具（Wake FTS5 trigram 中文/代码子串，本项目会话捕获只能"读了提炼"不能"搜"）；
   ⑤ 记忆时效维度（OKM timeless/dated/pointer+双时态，防记忆层堆积过期事实）；
   ⑥ Inbox 式提醒哲学（只在需要人拍板时打扰——本项目逾期横幅的进化方向）；
   ⑦ handoff 引用化（waggle ~30 字节工件引用 token，交接包进一步瘦身）；
   ⑧ Skill 包投影（第 9 类入口文件：.claude/skills/，踩 npx skills add 生态）；
   ⑨ 诚实能力矩阵与可行动报错（Wake "—是数据没记录不是缺功能"、Beads schema 守卫报错带修复命令——与本项目「声明不能撒谎」纪律同族，可再收紧）；
   ⑩ 用量记账细化到 run/任务维度（Multica per-issue 成本，本项目 usage.jsonl 只有 feature 标签）。
4. **不建议吸收**：agent 执行编排/runtime（Multica daemon、t3code 控制面——赛道声明已排除）；团队协作/角色权限（红线）；git-worktree 并行（10-01 结论维持）；换存储引擎（Beads 的 Dolt 路线证明"图+版控"有价值，但本项目 JSON+锁在实测规模下仍是正解，触发线不变）。
5. **风险警示**：vibe-kanban（28k★）死于无商业模式；Task Master（28k★）四个月停更；Letta 主仓变壳——**星标≠生命力，交接看板若开源化需想清维护策略**；同时「Agents don't need memory, they need documentation」思潮对"重度记忆层"路线是刹车信号，对本项目"文档/交接包为中心"是背书。
