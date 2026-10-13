# 优点矩阵 · 第四轮对标（2026-10-11）

> 本轮为前三轮（2026-10-01 plane 对标、2026-10-06 GitHub 竞品全方面对标、2026-10-08
> 第三轮品类横评）之后的新一轮增量调研：核实 22 个同类仓库实时状态（GitHub API，
> 2026-10-10/11 数据），深挖前几轮未展开的核心竞品（claude-mem 为本轮最大新发现），
> 并重审旧条目在新证据下的判断。判定四态：**本轮新发现 / 已吸收 / 暂缓 / 不吸收**。

## 一、重点仓库状态核实行（GitHub API，2026-10-10/11）

| 仓库 | ★ | 最近活跃 | 赛道 | 本轮要点 |
| --- | --- | --- | --- | --- |
| thedotmack/claude-mem | 99,166 | 10-09 | 会话记忆 | **本轮最大新发现**：渐进式上下文披露 3 层工作流 |
| mem0ai/mem0 | 66,951 | - | 记忆层 | Agent memory 基础设施 |
| makeplane/plane | 60,639 | - | 项目管理 | 前几轮已吸收（P0-1/P0-2/P1/P2/T1） |
| multica-ai/multica | 52,405 | 10-10 | 任务看板 | Inbox 哲学已吸收（/api/inbox 待拍板） |
| getzep/graphiti | 31,622 | - | 记忆图谱 | 时序知识图谱 |
| gastownhall/beads | 27,782 | 10-10 | 个人任务编排 | prime/ready/claim + schema 守卫 + divergence ledger |
| MrLesk/Backlog.md | 6,989 | - | 任务管理 | DoD 已吸收（acceptance 验收标准） |
| smtg-ai/claude-squad | 8,588 | - | 并行 agent | 执行编排（已判不吸收） |
| basicmachines-co/basic-memory | 4,130 | - | 记忆 | 本地 markdown 记忆 |
| parcadei/Continuous-Claude-v3 | 3,943 | - | 长时运行 | 自驱动循环 |
| iAmCorey/Wake | 1,439 | 10-10 | 会话中心化 | v0.9.1；MCP 只读 5 工具 + 诚实能力矩阵（已记录） |
| modiqo/waggle | 647 | - | 交接引用 | 引用 token（已判不吸收） |
| 其余（herdr/agent-board/Moe-s-Tavern/valcraft/aeman/sctxx 等） | <500 | - | 小项目 | 10-08 已阅，无新信号 |

## 二、claude-mem（99.2k★）深挖 —— 本轮核心增量

架构：MCP 3 层工作流 `search`（索引层）→ `timeline`（时间线）→ `get_observations`（详情层），
加上生命周期钩子捕获（SessionStart/UserPromptSubmit/PostToolUse/Stop/SessionEnd）写入
Worker 服务（SQLite FTS5 + Chroma 向量），Awareness push 把高价值观察（decision/bugfix/
security_alert/sensitive）以日期行永久注入。

| 优点 | 内容 | 对本项目的判定 | 理由 |
| --- | --- | --- | --- |
| 渐进式上下文披露 | 先给紧凑索引（50-100 token/条），按需取详情（500-1000 token/条），≈10x token 节省 | **已吸收（同族）** | 项目已有 `_budget_note` 预算提示与"截断+另报数"纪律；本轮补可度量标注 |
| Awareness push 类型优先 | 高价值记忆（决策/坑/安全告警）优先于普通事实被注入 | **已吸收（本轮实施）** | 项目记忆类型固定 4 类（决策/坑/约定/事实）；召回加权让"坑/决策"不因时间排后被挤出窗口 |
| 生命周期钩子捕获 | 5 个钩子自动记上下文 | **不吸收** | 项目是看板不是 agent 运行时；已有会话捕获 + 提炼流程 |
| `<private>` 隐私标签 | 敏感观察不进共享库 | **不吸收** | 项目是本地单机看板，方向相反；且已有脱敏/掩码收口 |
| Citations 引用 ID | 新观察引用过去 observation ID | **暂缓** | 任务 `refs` 已是"引用而非复制"雏形；为记忆加引用链收益低 |
| SQLite+Chroma 混合检索 | 全文 + 向量双路 | **不吸收** | 个人单机规模，`_load_memories` 全量过滤毫秒级（10-06 已实测），索引层是负资产 |

## 三、Beads（27.8k★）深挖

| 优点 | 内容 | 判定 | 理由 |
| --- | --- | --- | --- |
| `bd prime` 开工上下文 | 打印工作流上下文 + 持久记忆 | **已吸收（前几轮）** | AGENTS.md 生成时注入 `_project_memories` |
| `bd ready --claim` 原子认领 / `close` 释放阻塞 | 认领即原子变更，完成即解除 | **已吸收（同族）** | `executable_tasks` + `patch_task` 已实现"前置完成自动解锁"，MCP 回执说清放行了谁 |
| `bd remember` 持久项目事实 | 一行记忆 | **已吸收** | `kanban_memory_add` 且承诺自动召回已兑现 |
| Schema Version Guard | 报可行动错误 + escape hatch | **不吸收（维持）** | 项目导入策略是"高版本只说话不拒绝"（避免把用户关门外），方向相反，已拍板 |
| HTTP divergence ledger | 显式记录 HTTP 与本地模式的差异 | **不吸收（更强替代）** | 项目靠"一分实现 + 结构性钉子"消除分歧，比"记录分歧"更前一步 |
| Compaction 语义衰减 | 旧关闭任务总结成记忆 | **已吸收（同族）** | journal 折叠（`compress_journal`）| 
| Hash-based IDs | 零冲突 id | **不吸收** | 项目 id 用 uuid 截断 + 自愈去重，无冲突问题 |

## 四、旧条目重审（新证据下）

| 旧判定 | 新证据 | 修正 |
| --- | --- | --- |
| MCP 工具分档暂缓（>30 再议） | claude-mem 3 层工具设计是"内容分层"而非"能力分档"；本项目 23 工具 + 只读模式已覆盖可见性 | **维持暂缓**：不加分层工具，用现有出口的截断+标注满足 |
| FTS5 全文检索不吸收 | 10-06 探测数据（3 文件 <0.1MB / 2ms）仍有效；claude-mem 靠索引是因其海量历史 | **维持不吸收** |
| handoff 引用化不吸收 | claude-mem Citations 属同族 | **维持不吸收**（refs 已是引用雏形） |
| 待拍板 /api/inbox 已吸收 | multica 10-10 仍活跃 | 维持 |
| 验收标准 acceptance 已吸收 | Backlog.md DoD | 维持 |
| 记忆时效 expires 已吸收 | OKM 知识代谢 | 维持 |
| 停滞检测 stalled 已吸收 | 10-08 轮实施 | 维持 |

## 五、本轮唯一新落地方向

claude-mem 的 **Awareness 类型加权召回** 是前三轮未覆盖的点：`_project_memories`
和 AI 简报取记忆都是"时间倒序取前 N 条"，高价值的"坑/决策"可能因晚写的事实被挤出
窗口。对齐做法：**召回时按类型加权（决策=坑=约定 > 事实），同类内仍时间倒序**——
不增加条数上限、不改变接口形状，纯排序调整，低风险；配套补记忆检索输出的
**条目级尺寸估算**（渐进披露的最小落地，不新增工具）。