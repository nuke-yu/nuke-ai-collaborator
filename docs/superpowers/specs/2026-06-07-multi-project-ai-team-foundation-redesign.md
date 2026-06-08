# 多项目 AI 团队底层框架重设计

> 最后更新：2026-06-07
> 状态：设计稿（待评审）
> 关联：[TOOL-ROUTER-STRATEGIC-SOLUTION.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/TOOL-ROUTER-STRATEGIC-SOLUTION.md)、[TOOL-LAYER-GAP-ANALYSIS.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/TOOL-LAYER-GAP-ANALYSIS.md)、[SKILL-ARCHITECTURE.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/SKILL-ARCHITECTURE.md)

## 1. 产品定位

**每个聊天群 = 一个隔离的项目租户。** 群里住着 1–2 个真人 + 一支角色化 bot 团队（BA / 开发 / 测试 …）。项目是硬隔离边界：知识不跨项目共享，bot 因长期驻留而最懂本项目。

设计目标（用户三诉求）：
- **架构扩展性** —— 加角色、加能力、加项目都不改核心代码。
- **产品合理性** —— 职责清晰、权限符合"公司"直觉、协作可控。
- **可用性** —— 群里 1–2 个真人能轻松派活、把关、观察，审批不疲劳。

## 2. 已确认的设计决策

| # | 决策 | 取值 | 北极星 |
| :- | :- | :- | :- |
| ① | 层级 | **Project 为顶**，组织/公司层留接口暂不实现（YAGNI） | 未来加 Org 层（跨项目共享模板/报表） |
| ② | 协作编排 | **当前 POC 用强工作流**（BA→开发→测试 状态机 + 4 门人控） | 涌现式对话（`EmergentOrchestrator`） |
| ③ | 权限/知识分层 | **L1 通用共享；L2/L3/L4 项目私有**；权限随分层 + 角色派生，子 agent 沿角色树衰减 | 同 |

## 3. 领域模型

三类实体，隔离边界**钉在 Project 上**：

- **RoleTemplate（全局共享）**：角色的通用能力定义——"BA/开发/测试会什么、能用哪些工具类别、性格"。可复用、可扩展的"职位说明书"。**只贡献通用能力，不含任何项目知识。**
- **Project（= Group，硬隔离单元）**：一个租户。挂在其下且互不串：记忆、知识、workspace、session、权限 ruleset、资源预算、MCP/工具集、编排状态。
- **BotEmployee（= RoleTemplate 在某 Project 的实例）**：`角色模板 + 本项目私有状态/记忆/习得技能`。同一"BA 模板"在 A、B 两项目是互不知情的两个员工。

> 核心拆分：**通用能力（模板，共享） vs 项目知识（实例，私有）** —— 前者给扩展性，后者给隔离与"专属感"。

## 4. 知识/能力四层模型（与现有 `skills/discovery.py` 对齐）

现状四层：L1 System / L2 Group / L3 Role / L4 Learned。本设计的**隔离规则**：

| 层 | 现状路径 | 共享范围（本设计） | 变更 |
| :- | :- | :- | :- |
| L1 System | `SYSTEM_SKILLS_ROOT` | **跨项目共享**（唯一共享层） | 不变 |
| L2 Group | `group_{id}/shared/skills` | 项目私有 | 不变 |
| L3 Role | `ROLES_ROOT/{role}/skills` | **项目私有** | ⚠️ **现为全局，需改造**（见下） |
| L4 Learned/Personal | `bot_ws(bot_id)/...` | 项目私有 | 不变 |

**L3 Role 现状偏差**：当前 `ROLES_ROOT/{role}/skills` 是全局的，违反"角色在项目内的积累应私有"。改造：把 L3 拆为
- **L3a 角色模板技能（全局，只读）**：折叠进"通用能力"，等价 L1 的共享语义；
- **L3b 角色项目实例技能（项目私有，可写/可习得）**：落到 `group_{id}/roles/{role}/skills`。

习得（L4）与 L3b 的写入永远落在项目命名空间内，**物理上无法跨项目读取**。

## 5. 权限模型：角色派生能力档案

替换现状的 per-bot 零散 ruleset：

- **CapabilityProfile（绑定 RoleTemplate）**：声明角色的工具类别能力。例：
  - BA = `read + ticket + skill`（**物理上无 write/shell**）
  - 开发 = BA + `write_file + run_shell + create_pr`
  - 测试 = `read + run_shell(test-scoped) + ticket`，但**不可改生产代码**
- **项目级覆盖**：Project 可在角色档案上叠加 allow/deny（不可放大到超过角色基线）。
- **子 agent 衰减**：spawn 时子 = `min(父权限, 子角色档案, 子任务所需)`，永远 ≤ 父（对标 opencode `deriveSubagentSessionPermission`）。
- **落点**：权限裁决继续走 `tool_executor` before-hook（plan A），但 ruleset 来源从"per-bot DB 规则"改为"角色档案 + 项目覆盖"派生。

## 6. 协作编排：可插拔策略（POC = 强工作流）

定义编排策略接口，POC 先实现工作流，未来可换涌现式而不动其它层：

```
OrchestrationStrategy (interface)
  ├─ WorkflowOrchestrator   # POC：BA→开发→测试 状态机 + 4 门人控
  └─ EmergentOrchestrator   # 北极星：对话/@ 交接涌现（暂不实现）
```

- **WorkflowOrchestrator（POC）**：固定阶段机；每阶段绑定允许的角色与工具子集（BA 阶段只读/提单，物理上写不了代码）；阶段产物驱动下一阶段。
- **4 道门 = 人控关卡 + 会话收敛锚**：需求 / 设计 / 测试 / 发布，真人在群里以交互卡片审批（复用现有 bridge 回调卡）。每道门也是会话的天然收敛点，杜绝 bot 间无限 ping-pong。
- **接口隔离**：阶段推进、产物传递、门控审批都经接口；切到 `EmergentOrchestrator` 只换实现。

## 7. 健壮 / 稳定 / 灵活 落到本模型

- **隔离即故障域**：一个项目跑飞/崩溃绝不波及其它项目（独立 session / 预算 / 命名空间）。
- **资源预算（Project + 会话级）**：`max_tool_calls / max_spawn / max_wall_clock / token_budget` 挂在 `ExecutionContext`，逐轮递减，超限优雅终止。租户级配额防单项目拖垮全局。
- **会话收敛**：以"门"为收敛锚 + agent 间调用环检测 + 轮次预算（对话驱动的头号风险）。
- **会话可恢复**：per-project session 持久化，崩溃中途幂等续跑（飞行中工具/spawn 状态可 checkpoint）。
- **工具层加固（承接 gap 分析）**：输出脱敏（多 agent 共享上下文防泄漏）、spawn 故障隔离 + 超时、MCP 健康检查/重连、非安全工具并发闸。

## 8. 扩展性的分层抽象

- **RoleTemplate 注册表**：加角色（架构师/运维…）= 加模板，零代码改动。
- **能力 = ToolProvider 集合**：角色档案绑定能力集；plan B 的统一 router 在此自然成为"每项目一套工具治理面"（按既定判据演进）。
- **Project 即租户**：多项目水平扩展；Org 层留空接口。
- **OrchestrationStrategy 可插拔**：workflow ↔ emergent 自由切换。

## 9. 可用性（群内 1–2 真人体验）

- **真人 = 项目经理**：派活、过门、审批；bot 的权限请求/到门通知以交互卡片在群里呈现。
- **降低审批疲劳**：借 gsd-2 的智能"始终允许"规则（按命令子命令深度合成 `Bash(git push:*)` 等），人批一次后续同类自动放行。
- **透明可观测**：人能看到 bot 的工具调用 / 交接 / 到门（trace 产品化）。
- **开新项目 = 选角色组队**：建群 → 选模板（BA/开发/测试）→ 自动实例化专属团队。

## 10. 分阶段落地

1. **阶段 1（POC 地基）**：领域模型（RoleTemplate/Project/BotEmployee）+ L3 角色层项目私有化改造 + CapabilityProfile 角色权限 + WorkflowOrchestrator(4 门)。
2. **阶段 2（健壮）**：子 agent 权限衰减 + Project/会话资源预算 + spawn 隔离 + 输出脱敏。
3. **阶段 3（稳定）**：会话收敛/去环 + session 可恢复 + MCP 健康恢复 + 并发闸。
4. **阶段 4（灵活，按需）**：EmergentOrchestrator、plan B 统一治理面、Org 层、deferred 工具加载。

## 11. 非目标（明确不做）

- 不在 POC 阶段做组织/公司层。
- 不做涌现式编排（接口预留，实现延后）。
- 不为单一 MCP server 建多传输/OAuth（YAGNI）。
- 不把命令安全寄望于堆子串规则（要做就上分类器/容器，列入阶段 3+）。

## 12. 待确认

- ① 组织层：当前按"不做、留接口"。如需现在就做请反馈。
- L3 Role 私有化的迁移：是否有存量全局角色技能需要迁移到项目命名空间？
