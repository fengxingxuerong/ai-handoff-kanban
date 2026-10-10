# GitHub 同类竞品对标 · 第三轮（2026-10-08）

> 接续 `对标同类开源项目-2026-10-01`（第一轮）与 `竞品优点吸取-2026-10-06`（第二轮）的增量扫描。
> 本轮范围：2026 年新品类「AI 编码代理的看板/交接层」的 2026 年新面孔 + 前两轮漏掉的长尾。
> 全部星标经 api.github.com 于 2026-10-08 实时核实（`github_metadata_round3.json`），README 全文存档于 `readmes/`。

## 一、本轮格局（9 仓实时核实）

| 仓库 | 星标 | 最近推送 | 定位 | 与本项目的关系 |
|---|---|---|---|---|
| nelsonPires5/herdr-board | 170 | 2026-10-06 | 卡片即 prompt、列即流水线阶段的 TUI 看板，agent 在可见 pane 里跑 | 执行编排，无交接资产 |
| TechDufus/openkanban | 148 | 2026-06-12 | TUI 看板 + 每 ticket 一个 git worktree + 内嵌终端 | 执行编排（AGPL-3.0） |
| Lexus2016/claude-code-studio | 143 | 2026-10-07 | Claude Code 的 Web/桌面工作台：看板+调度+Telegram 遥控+会话重放 | **赛道最近的新对手**，仍是执行侧 |
| trevor-nichols/agentrules-architect | 123 | 2026-08-30 | 六阶段流水线生成 AGENTS.md/CLAUDE.md（PyPI 分发） | AGENTS.md 生成器，无看板 |
| aenix-io/aeman | 34 | 2026-10-07 | git 仓库即存储的日计划板，K8s 式 API+WATCH，MCP 原生 | 存储路线不同，接口设计可借鉴 |
| yaront1111/Moe-s-Tavern | 33 | 2026-10-08 | IDE 内任务板 + Architect/Worker/QA/Governor 分角色 + 计划审批门 | 执行编排 + 角色化 |
| quentintou/agent-board | 28 | 2026-02-05 | OpenClaw 生态任务编排：6 列看板+DAG+自动重试+审计+12 MCP 工具 | **同生态直竞**（已停更 8 个月） |
| handyutils/sctxx | 19 | 2026-09-11 | 会话转录 → 7.9KB 验证过的交接工件（302MB→3.2k token，无模型） | 会话提炼赛道，压缩率口径可借鉴 |
| valzav/valcraft | 22 | 2026-10-08 | 仓库内 spec 驱动开发技能集：需求→验收标准→任务→独立审查 | 技能包形态，无看板 |

**格局判断**：2026 年「管 agent 的板」品类在 TUI/执行编排侧爆发（herdr/openkanban/claude-code-studio/Moe 全是 2026 年新建仓），但**没有一家做「交接资产」**——就绪度评分、交接包、多入口投影、脱敏、MCP 只读模式仍是本项目独有。执行侧竞品全部把「上下文交接」外包给 git worktree 或会话文件，验证了 10-01 轮"开创者"判断在第三轮依然成立。

## 二、十大候选优点判定

| # | 优点（来源） | 判定 | 理由 / 触发线 |
|---|---|---|---|
| ① | 审计轨迹 audit.jsonl（agent-board） | 已具备，补强 | 本项目已有 journal+usage.jsonl；agent-board 的「REST 与 MCP 变更都进审计」口径值得补：缺的是按项目导出操作史——journal 已有，补进交接包导出即可（与 10-06 journal 折叠语义对齐） |
| ② | 质量门 requiresReview（agent-board） | 已具备 | 任务状态机已有「待审查」列 + acceptance 验收清单（10-06 从 Backlog.md 吸收），语义等价 |
| ③ | DAG 依赖 + 环检测（agent-board） | 已具备 | taskgraph.py 拓扑排序 + _break_dep_cycles + inbox 依赖过滤（约定 27），本轮刚探针加固 |
| ④ | 客户视图 clientView（agent-board） | 值得吸收（暂缓） | 「只读分享/演示」本项目只有 MCP 只读开关；per-project 只读仪表盘（隐藏 agent 名与内部细节）面向"给老板看"场景，与脱敏管道复用度高。触发线：真实分享/演示需求出现。预计形态：export/page 加 `?view=client` 复用 redact 管道 |
| ⑤ | API Key per-agent 认证（agent-board） | 不吸收 | 安全边界 = 只绑 127.0.0.1 + 数据不出机（约定 9）；加 key 破坏"打开即用"单机定位。远程访问路线（claude-code-studio 的 HOST=0.0.0.0）出现时才需重建认证层 |
| ⑥ | 自动重试 auto-retry（agent-board） | 不吸收 | 本板不执行任务，无"失败"语义；重试属执行编排侧，维持 10-01「不做执行」结论 |
| ⑦ | 计划审批门 CONTROL/SPEED/TURBO（Moe-s-Tavern） | 部分吸收（已有） | AI 指令窗「解析→预览→确认执行」就是 CONTROL 模式；TURBO=跳过确认与"写操作必须人确认"红线冲突 |
| ⑧ | 停滞检测 stuck detection（agent-board stats） | 本轮落地 | stats 有分布/热力/阻塞链但无时间维度；补 stalled（进行中且 updated_at 超 7 天）进 stats 与 AI 监管简报——与 gitinfo.days_since_commit 的"代码侧停滞"形成对称的"看板侧停滞" |
| ⑨ | 会话交接压缩率口径（sctxx：302MB→7.9KB 无模型） | 借口径，不引依赖 | 会话提炼结果补「尺寸预算标注」（交接包已有 budget，提炼结果同款），给"是否过度压缩"一个可见度量；无模型纯规则压缩暂不引入 |
| ⑩ | 每请求一 commit 的 git 存储后端（aeman） | 不吸收 | 存储路线第三次判定不做；git-log-as-history 优雅但引入 push/fetch 复杂度，单机 JSON+快照+自愈已覆盖同等诉求 |

## 三、落地状态

- **⑧ 停滞检测：本轮已实施**——`app/project_stats.py` 新增 stalled 纯函数（进行中且 updated_at 超 7 天，窗口可注入），进 `/api/projects/{pid}/stats`；探针与测试钉子同步（改被盯代码同步变异表的项目纪律）。
- **④ 客户视图：暂缓**（触发线记录于上表，触发时从本文件直接开工）。

## 四、三轮合计判定账本

三轮累计：检索 ~25 组、API 核实 ~60 仓、深读 README 16 份；判定 20 条：已落地/具备 8、本轮落地 1、暂缓 3（全带触发线）、不吸收 8（全带理由）。
品类第三轮验证：**执行编排侧拥挤（2026 年至少 6 个新仓），交接资产侧仍空白**——本项目差异化定位第三次成立。
