# 第六轮竞品对标（2026-10-19）

> **方法**：GitHub search API（10 组关键词："handoff"/"task board"/"context handoff skill"/
> "session continuity"/"spec driven development"/"kanban AI"/"MCP task manager 服务器"/
> "agent progress tracker"/"multi agent project management" 等，`created:>2026-08-01`，
> 带 GCM 凭据），排除前五轮已收录的 ~106 个仓库 + thedotmack/claude-mem。
> 命中 258 个唯一仓库，保留 242 个（star≥3 或近期活跃），其中 20 个同赛道新面孔拉全文 README 深挖。
> 判定四态沿用前几轮：**本轮新发现 / 已吸收 / 暂缓 / 不吸收**。本文只写增量。
>
> 检索噪声说明：`created:>2026-08-01` 会把同期热榜的无关仓库（photocraft、Amagine3D 等）带进来，
> 242 个保留项里大部分是 long-tail 小仓库，已按赛道人工过滤后再深挖，未逐一收录。

## 一、本轮深挖的 20 个同赛道新面孔（★ / 活跃，2026-10-19 数据）

| 仓库 | ★ | 活跃 | 赛道 | 一句话 |
| --- | --- | --- | --- | --- |
| NxcoreAI/EverRoom | 2991 | 10-17 | 个人上下文工作台 | 把文件/会话归入 Context Room，证据账本可回溯 |
| ranxianglei/billion-context | 1857 | 10-18 | 上下文压缩层 | agent 自主压缩/还原长会话，每折有经济账 |
| aoci-spec/aoci-code | 1607 | 10-16 | 代码认知索引 MCP | FRAS 条目 + drift 分型 + 服务自报身份 |
| smarzban/tsk | 214 | 10-18 | 终端任务板 | TUI 给人 CLI 给 agent，共享一条队列 |
| nelsonPires5/herdr-board | 180 | 10-18 | 看板→agent 编排 | 列=流水线阶段，卡片在可见 pane 里跑起来 |
| dualface/kander | 157 | 10-16 | 多 agent 并行编排 | worktree+审查门+冲突感知调度 |
| ustoppble/overclick | 119 | 09-24 | 人决定 agent 执行看板 | 卡=契约（What/Why/How to confirm），Done≠Validated |
| makifbaysal/tasktrooper | 117 | 10-18 | 本地 agent 平台 | 13 列连续流+验收硬门+记忆纪律 |
| jklthinking/retinue | 117 | 10-08 | 任务板+审计层 | holder 写权限+append-only 回执链+数据新鲜度 |
| JoxoAI/joxo | 100 | 10-17 | 团队 agent 协作（闭源） | 一行 skill 分发，idle 会话自动接活 |
| Ericwong5021/better-codex | 90 | 10-16 | 会话↔任务双向链接 | 会话转任务、per-agent 配置、doctor 自检 |
| johnreginald/donewhen | 59 | 10-04 | done-when 看板 | 清单未勾不能进 In Review/Done（硬拒绝） |
| OthmanAdi/plandeck | 68 | 08-03 | 计划可视化看板 | 纯函数大脑、分列滞留阈值、drift 恢复 |
| ai4kanban/ai4kanban | 44 | 10-18 | 桌面 agent 工作区 | 草稿先行分工，人审后实现 |
| jan-bogaerts/md2 | 34 | 10-15 | 桌面 feature 卡 | 卡=共享上下文+可复用 action 模板 |
| LioraLabs/cliban | 15 | 10-13 | CLI 控制平面 | 结构化五段描述、按段替换、逐条 tick |
| webkubor/xiaobai-kanban | 18 | 09-29 | 中文教程仓库（非软件） | 需求四句模板+话术表 |
| keviccz/AgentKanban | 7 | 10-11 | Windows 浮窗+MCP | agent 自己登记待办，刻意省 token |
| azimov777/casefile | 6 | 10-16 | 任务案卷 MCP | append-only 案卷+ask/answer 问题机制 |
| imMamdouhaboammar/riqor | 5 | 09-06 | 本地证据门 | 通过验证后又变更→撤销先前证据 |

> 趋势观察：本轮的 long-tail 里**"handoff/session continuity skill"成了一整波**（几十个 0-2★
> 仓库在教人怎么写交接文档/skill）。这印证了本项目"交接包/AGENTS.md 为中心"的定位是对的；
> 差异化不在"会不会写交接"，而在**门禁与验证**（donewhen/overclick/riqor 这一簇在做的硬拒绝）。

## 二、对标矩阵（只列**有增量**的条目）

| 优点 | 出处 | 内容 | 判定 | 理由 |
| --- | --- | --- | --- | --- |
| **阻塞必带原因** | donewhen / casefile / retinue | 阻塞不是颜色而是信息：donewhen 要求填理由并集中列出；casefile 阻塞任务不许开工；retinue 回执链记录 block | **本轮落地** | 接手时"卡在哪、为什么"是第一问题，本项目目前阻塞只是状态；成本是一个可选字段 + 三个落点 |
| **「验收后代码又变了」** | riqor（证据状态机的最小切片） | 验证通过后仓库又有变更 → 标记"先前证据已撤销，需复核" | **本轮落地** | 正是"频繁切项目"的痛点：回来时不知道当时验收还算不算数；已有 repo_watch 快照可派生，零新存储 |
| **refs 引用失效检测** | plandeck `check` 回执卫生 / 第五轮候选③ | 任务 refs 指向的文件不存在 → 警告 | **本轮硬化**（检测本体已存在，2026-10-19 实施时修正判定） | refs 是给接手智能体的代码坐标，指向不存在的文件等于误导；`check_refs_exist` 与五个出口早已落地，本轮补两处硬化：带 `://` 的 URL 跳过且不计入 total（外部链接不是本机坐标，以前全被误报 missing）、`#片段` 剥掉后按文件判 |
| **记忆写入去重** | tasktrooper | 近似重复不存两次；命中返回已有 id | **本轮落地** | `MAX_MEMORIES` 与 12 条召回窗口是稀缺资源，代理重试/复述会成对制造重复 |
| **任务号一键复制** | tsk | 任务号一键复制，用户把它贴进 agent 对话作认领凭据 | **本轮落地（顺手）** | 换智能体场景高频动作；按 id 对齐与脱敏纪律同向；纯前端 |
| 状态流转自动回执 | retinue | claim/progress/handoff 全记录、不可改 | **已覆盖** | `_apply_task` 现在已写 `状态：A → B`；差的只有"操作者"维度（界面/代理/调度），单机单人下归因价值低 |
| 数值出口诚实性 | overclick / retinue | 没上报就显示"未知"；quota 卡显示报告年龄与最后成功时间 | **部分已吸收** | 三档门禁（通过/失败/未验）、`truncated`、`reason` 已有同源纪律；"报告年龄"标注暂缓 |
| 报告话术纪律 | kander | 结论先行、固定状态词、每条结论注明验证方式、拍板选项编号 | **已吸收（精神）** | 与 `ci_local` 三档门禁、`_budget_note` 口径同源；不新增机械模板 |
| 单源→多形态投影 | joxo | 一行 skill 分发（plugin/skill/MCP 三形态） | **已覆盖（印证）** | 9 入口投影 + Skill 已是同一思路 |
| done-when 硬 gate / 逐条勾选 | tasktrooper / donewhen / casefile / cliban | 验收清单未核验不能进 Review/Done；逐条 tick | **暂缓** | 四源收敛度最高，但需要 acceptance 从字符串列表改成带核验态的条目（跨层字段+五落点改造）；触发线：出现"代理踩着未勾验收标完成"的实际事故 |
| 任务级未答问题进 inbox | casefile | ask/answer 问题机制，未答问题等人回答 | **暂缓** | 与"待拍板只列等人的"哲学同族；触发线：代理开始把提问留在 journal 人漏看 |
| 一键让本机 agent 接入看板 MCP | AgentKanban | 设置里列出本机客户端、写各自配置前备份+列清单+确认 | **暂缓** | 23 个工具已就绪但用户要手抄配置；写别人家配置文件需要安全姿势设计；触发线：用户点名要 |
| 可裁剪项的按节成本账 | billion-context | 每折净省 token 账；可行动的成本可见化 | **暂缓** | `_budget_note` 现在说"偏长建议裁剪"却不说是哪节；触发线：有人抱怨不知道裁哪节 |
| 接手包内容指纹 | aoci-code | 包带指纹，代理据此判断"指纹未变可复用" | **暂缓** | 需要扩 `/api/context` 顶层 pinned 字段集（有 v2 先例）；触发线：代理开始复用旧包 |
| MCP 自报"连的是哪份数据" | aoci-code | 回执带 `mcp_service_version` + `runtime_repository_root` | **暂缓** | 我们已有"起实例先读 /api/health"纪律；代理侧没有同款，但收益中等 |
| 入口体检分型 | aoci-code | Missing/Orphan/Stale/Unbaselined 分型各自有动作 | **暂缓** | 第五轮已落"漂移/缺失/拦截/读不了"五态；触发线：投影扩面时 |
| 分列滞留阈值 | plandeck | blocked 1d / doing·review 2d / ready 5d 各自阈值 | **暂缓** | 本项目停滞检测只盯"进行中 7 天"是**有意**口径（待审查/阻塞停滞语义不同）；触发线：先拍板语义 |
| 提交关联任务 id | md2 | commit 主题里的任务 id 与卡片互链 | **暂缓** | repo_watch 派生即可；随雷达后续轮 |
| 任务↔源会话引用 | better-codex | 会话转任务时带上源会话标识 | **暂缓** | refs 已支持任意前缀，成本低；触发线：提炼会话后想追原始讨论 |
| 需求四句骨架 / 指令模板 | xiaobai-kanban / md2 | 新建任务占位骨架（背景/目标/范围/验收）；指令窗可存模板 | **暂缓** | 纯前端低风险，随下轮 UI 轮 |
| 私密待办（不外出标记） | AgentKanban | 任务带"不外出"标记，四个外出出口过滤 | **暂缓** | 字段小但出口多（MCP 读/交接包/AGENTS.md/简报）；触发线：用户提出"有些任务不想给代理看" |
| 复制任务 | herdr-board | 同类任务再来一条，保留验收与 refs | **暂缓** | 边际价值低于本轮其它项 |
| 项目命名漂移 / 重名合并 | the-vault（第五轮遗留） | 只提示重名，不自动合并 | **暂缓（维持）** | 同上轮判定；单机单人下危害小 |
| 体检结果进交接包 | （第五轮遗留④） | 接手的智能体看到入口是否过期 | **暂缓（维持）** | 需要先有人真的用体检结果做事 |
| 旧格式夹具回归 | aoci-code | 早期版本数据格式进测试夹具 | **无需动作** | 导入/恢复/`_sanitize` 的兼容用例已覆盖同族风险 |

## 三、本轮落地候选（方案，待拍板）

五条小改动，彼此独立：

1. **阻塞必带原因** —— `_apply_task` 在状态进入「阻塞」时记录 `blocked_reason`（界面拖入时弹小输入框，
   可跳过；MCP/HTTP 可选传），离开阻塞自动清空（历史在 journal）；`/api/inbox` 阻塞类带原因，
   交接包 `waiting_for` 带原因，详情页显示。
2. **「验收后代码又变了」** —— 任务进「已完成」时记 `done_at`；`repo_watch.scan_project` 对比仓库提交时间，
   产出 `stale_done` 列表；详情页代码现实面板给"当时验收，之后仓库有变化，需复核"提示（只提示不改状态）。
3. **refs 引用失效检测** —— 仅当 repo_path 可读时，对形如本地路径的 ref 检查存在性，
   缺失在详情页给温和警告（绝不阻止流转）。**实施备注（2026-10-19）**：检测本体
   （`gitinfo.check_refs_exist` 与详情/交接物五个出口）早在 v1.0 之前就已落地，本轮误判为"新功能"，
   实际交付的是两处硬化 —— 带 `://` 的 URL 跳过且不计入 total、`#片段` 剥掉后按文件判。
4. **记忆去重** —— `add_memory` 锁内规范化比对（同 pid+同类型+文本相同），命中返回已有 id 并说明。
5. **任务号一键复制** —— 详情/卡片复制按钮 + toast（纯前端）。

## 四、本轮"明确不吸收"新增

- EverRoom 的上下文工作台本体（Context Room / 证据账本 / 文档事务 / 连接器 / 记忆流水线）：重存储与编排。
- billion-context 的可逆压缩与传输代理：与"桌面无服务端 + journal 折叠已封顶"冲突；per-token 经济账不做。
- aoci-code 的索引服务与游标交付：缓存式全库索引是重层；本项目任务 refs 已是"引用而非复制"的雏形。
- 编排/团队/worktree 一整簇（headcount、kander、herdr-board 的 daemon、tasktrooper 列→agent、
  joxo 团队+手机、better-codex runtime relay、md2 手机 serve/公网、ai4kanban worktree、
  cliban claim/waves、casefile OAuth/固定转换表）：维持历史判定。
- plandeck 的 `stateHash` 轮询（ETag 类已否决）；AgentKanban 的 SQLite/桌宠/自动完成推断（看板不替 agent 判活）。

## 五、落地实测（2026-10-19，v1.8.0）

五条全部落地（用户拍板「五条全落」），验证明细：

- **`ci_local --with-exe` 11 步全绿**：pytest 763 收集 / 758 通过 / 5 跳过（KANBAN_LIVE）、分支覆盖率 93%；
  变异探针 **68 条全被杀**（四条新探针先 `--only` 单跑确认被杀）；场景 32/32；发版回归 16 通过 + 2 明示未验；
  交付演练 21/0；只读体检 12/0；真实浏览器 16/0；重打包（构建戳 2026-10-19 17:10、PE v1.8.0）+ `frozen_smoke` 11/0。
- **界面闭环**（真实浏览器点击）：改阻塞 → 填原因 → ⛔ 原因上卡 → 重开弹窗回填 → 📋 复制编号
  （toast 带真 id）→ 改回进行中原因即清（`.tcard` 文本 + 盘上字段双断言）；stale 复核提示用打桩 git 端点验证。
- **两条实施修正**（研究阶段的误判，实施时才发现）：
  ① refs 失效检测**已存在**，本轮只做硬化（URL 跳过 / `#` 片段剥离）；
  ② 记忆去重**部分已存在**，但命中时返回新造的 entry（id 盘上不存在，删除 404）—— 本轮修成
  返回盘上已有那条，并把匹配口径收敛为同项目+同类型+同文本。
