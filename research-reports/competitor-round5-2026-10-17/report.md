# 第五轮竞品对标（2026-10-17）

> **方法**：GitHub search API（6 组关键词，带 GCM 凭据），去重后得 65 个候选仓库；
> 对其中同赛道的新面孔拉全文 README 深挖。判定四态沿用前几轮：
> **本轮新发现 / 已吸收 / 暂缓 / 不吸收**。
> 前三轮（10-01 plane、10-06 全方面、10-08 品类横评）与第四轮（10-11 claude-mem）的结论
> 见各自档案，本文只写**增量**。

## 一、本轮搜索命中的同赛道仓库（★ / 最近活跃，2026-10-17 数据）

| 仓库 | ★ | 活跃 | 赛道 | 一句话 |
| --- | --- | --- | --- | --- |
| StringKe/std-agent | 8 | 09-18 | 单源 → **26 个** AI 工具投影 | **本轮最大增量** |
| aliihsaad/the-vault | 8 | 10-10 | 桌面 + CLI + MCP 记忆层 | 记忆生命周期可逆 + 命名漂移合并 |
| Phil200727/contextops | 8 | 07-26 | 文件式 Kanban（FastAPI + 无构建前端） | 与本项目同构度最高 |
| lee-to/aif-handoff | — | — | 自治 Kanban（agent 自动规划/实现/评审） | agent 执行编排 |
| arxdsilva/agent-office | 2 | 10-14 | 桌面指挥中心 + MCP | 3D 办公室外壳 |
| semiherdogan/handoff | — | 06 | decisions + evidence + drift auditing | 漂移审计灵感源 |
| dkothule/ai-context | 11 | 09-10 | check-drift + session compaction | 漂移审计灵感源 |

> 注：历史档案里的头部（claude-mem 99k / mem0 67k / plane 61k 等）未被这 6 组关键词命中
> ——它们的关键词是 "memory"/"project management"，与本轮的 "handoff"/"agent context" 不同族。
> 也就是说本轮**没有发现新的头部玩家**，增量集中在长尾里那几个同构度很高的项目。

## 二、对标矩阵（只列**有增量**的条目）

| 优点 | 出处 | 内容 | 判定 | 理由 |
| --- | --- | --- | --- | --- |
| **逐目标漂移检测** | std-agent `status` / dkothule `check-drift` / semiherdogan `drift auditing` | 三个独立项目都做同一件事：比对"本应写的内容"与磁盘现状，报漂移 | **本轮落地** | 本项目的写入路径对已存在文件是**默认跳过**的，于是"仓库里那份入口早跟看板对不上了"从来没人体检过 —— 而漂移的入口正是接手方最信的东西 |
| 26 个工具投影目标 | std-agent | 单 `.stdai/` 源扇出到 26 个 AI CLI（Claude/Codex/Cursor/Copilot/Windsurf/Devin/Gemini/Aider/Cline/OpenCode/Crush/Amp/Warp/Factory/Junie/Antigravity/Qwen/Pi/Kilo/Augment/Jules/Grok/Kimi/Kiro/Goose/Zed/Muse） | **暂缓** | 本项目现有 9 个都对照过官方路径；扩到 26 需要逐个核对 frontmatter 方言与尺寸限制（std-agent 自己标注"last full audit: 2026-08"），收益/风险比不划算。**触发线：用户点名某个工具** |
| spec-accurate（对照官方文档核实每条路径） | std-agent | 每个输出路径、frontmatter 方言、尺寸限制都对照官方文档核实并标注审计日期 | **已吸收（精神）** | 本项目 `AGENTS.md` 的入口表与 `test_agent_files.py` 已钉住路径集合与 frontmatter；本轮把"审计日期"这个习惯补进了报告 |
| 记忆生命周期可逆（active→stale→archived→pending_delete→deleted） | the-vault | 记录不立即删，可回滚 | **不吸收（维持）** | 项目 AGENTS.md 已把"软删除回收站"判为负资产：每条读路径多一个分支，而"删错了"已由备份/快照恢复覆盖 |
| 命名漂移 / 重名项目合并 | the-vault | 检测项目名漂移、重复记录并合并 | **暂缓** | 本项目 `_dedupe_ids` 只管 id 不管名字。重名在单机单人场景下危害小，而"自动合并"是不可逆动作，需要人工闸门设计 |
| Open loops（未完成工作聚合 + 批量解决） | the-vault | 聚合 non-empty next_steps + 陈旧调试项，按优先级分桶 | **已吸收（同族）** | `/api/inbox`（待拍板）+ `/api/today`（今日可执行）+ 停滞检测三者合计覆盖同一问题 |
| heartbeat + stale 看门狗 | aif-handoff / the-vault | 心跳 + 陈旧阶段看门狗自动恢复卡住的 agent 阶段 | **不吸收** | 本项目是看板不是 agent 运行时；心跳属被编排方的事 |
| MCP 单点配置扇出 | std-agent | 一份 `.stdai/mcp.json` → `.mcp.json` / `.cursor/mcp.json` / `.vscode/mcp.json` | **暂缓** | 本项目是被 MCP 客户端**消费**的一方（提供 server），不负责替用户配置各客户端 |
| CLI 服务生命周期（start/stop/restart/status/health/test/init/reset） | contextops `ctl.sh` | 一条命令管全生命周期 | **已吸收** | `scripts/start_desktop.bat` + `scripts/ci_local.py`（自起隔离实例）+ `frozen_smoke.py` 已覆盖 |

## 三、本轮落地方案：入口投影体检

**改了什么**（复用 `POST /api/export/agent-files/{pid}`，加 `check_only` 开关，**不新增端点**）：

对白名单里的每个入口目标，逐目标比对本应写的内容与磁盘现状，返回五态：
`ok`（一致）/ `drift`（已漂移）/ `missing`（未写入）/ `blocked`（路径被黑名单拦）/ `unreadable`（读不了），
外加 `check_summary` 计数。**只读，一个字节都不落盘。**

两条容易被写错的口径（都有测试钉住）：

1. **行尾不算漂移** —— Windows 上 `write_text` 落的是 CRLF 而生成的是 LF，按字节比会永远报红（等于没有）。比对前两侧都归一化 `\r\n → \n`。
2. **没给目录是"没法体检"，不是"全都没写过"** —— 没传 `dir` 时保持 `preview=True` 且不返回 `checks`，避免把"没法查"报成"没写过"。

界面：AGENTS.md 弹窗里「预览入口」与「写入项目目录」之间加「🔍 体检入口」，结果逐行列出状态与路径，有漂移时提示「勾选覆盖再写入即可修复」——`fix` 就是已有的写入路径。

**实测**（真服务，四步）：
空目录 `missing:9` → 写入 `9 个` → 全检 `ok:9` → 手改一个 → `ok:8 / drift:1（AGENTS.md）`。

**测试**：`tests/test_agent_files.py` 新增 4 条（空目录→写入→一致的完整链、手改必报漂移、
CRLF 不算漂移、无目录不误报），全套 24 passed。

## 四、后续候选（本轮没做，留给下一轮）

1. **扩入口目标**（9 → 更多）—— 触发线：用户点名某个工具，逐个对照其官方文档核实后再加。
2. **项目命名漂移检测**（the-vault）—— 只做"提示重名/高度相似"，**不做自动合并**（不可逆动作要人工闸门）。
3. **引用失效检测** —— 任务 `refs`（结构化代码位置）指向的文件是否还存在；这是 drift auditing 在任务层的同族形态。
4. **体检结果进交接包** —— 现在体检只在界面上，接手的智能体看不到"这份入口是不是已经过期"。

## 五、本轮"明确不吸收"新增

- agent 执行编排（aif-handoff 的自动规划/实现/评审闭环）：维持历史判定，不是本项目的赛道。
- 记忆生命周期可逆状态机（the-vault）：维持历史判定（备份/快照已覆盖，多分支是负资产）。
- 展开到 26 个工具投影：需要逐工具核对官方规范，收益/风险比不划算。
