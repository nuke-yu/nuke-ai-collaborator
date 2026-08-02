# Nuke AI Collaborator 与 OpenHanako 横向对比

> 状态：讨论稿（Draft）
> 日期：2026-08-02
> 目的：用于后续讨论、校准优先级，并最终形成产品与架构决策版本。

## 1. 执行摘要

Nuke AI Collaborator 与 OpenHanako 并不是同一类产品的两种实现。

- OpenHanako 的核心是“一个人的长期 Agent OS”：围绕 Agent、Session、个人记忆、工具、自动化和跨渠道连续性展开。
- Collaborator 的核心是“以 Group 为边界的 AI 团队协作平台”：围绕多用户、多角色 Bot、项目隔离、共享工作区、工作流和组织知识展开。

因此，Collaborator 不应整体模仿 OpenHanako。更合理的方向是保留自身的 Group、Workflow、Memory、Worker 和权限架构，同时吸收 Hanako 在以下方面的成熟能力：

1. 执行能力快照与可复现性。
2. 统一 Artifact / SessionFile 资源模型。
3. 版本化事件协议与断线恢复。
4. 集中的 Model / Provider Registry。
5. 用户可理解的执行时间线、检查点和恢复界面。
6. 跨渠道 Channel Adapter。
7. 持久化资产登记和迁移治理。
8. 个人记忆的可见性、授权和撤回体验。

其中最需要避免照搬的部分是：冻结整个 Group 上下文、超大 SessionCoordinator、以 Markdown 代替 canonical memory、在没有代码隔离前扩张插件能力，以及巨型前端全局 Store。

## 2. 对比范围与口径

本次对比基于两边实际代码，而不只依据 README 功能列表。

仓库位置：

- Collaborator：`/Users/Nuke/claudeFolder/nuke-ai-collaborator`
- OpenHanako：`/Users/Nuke/openhanako`

对 Collaborator 的判断区分为三种状态：

- **CURRENT**：已有代码路径支撑，可视为当前实现。
- **TRANSITION**：已有部分实现，但仍处于迁移、双写或产品化过程中。
- **TARGET**：设计文档或路线图中的目标，不视为当前已交付功能。

当前工作树中的 Electron、Onboarding 和主题相关文件仍有未提交内容，因此本文不把它们视为 Collaborator 已稳定交付的桌面能力。

## 3. 根本产品模型

### 3.1 OpenHanako

```text
用户
  → Agent
      → 长期会话
          → 记忆 / 工具 / 自动化 / 外部渠道
```

核心资产是一个持续成长、理解个人、能够跨会话和渠道存在的 Agent。

在 Hanako 中，Session 是主要产品对象。系统围绕 Session 提供稳定身份、恢复、分支、休眠、能力快照和媒体资源管理。

### 3.2 Collaborator

```text
人类成员
  → Group
      → 多角色 Bot
          → Workflow / Shared Workspace / Project Memory
```

核心资产是一个持续积累项目知识、能够分工协作和恢复执行的 AI 团队。

在 Collaborator 中：

- Group 是用户可见的长期协作空间。
- 群消息是公共讨论记录。
- `agent_session` 更接近一次 Bot 执行或工作流单元的 WAL。
- Bot 必须读取 Group 中其他成员的最新交付，不能把整个项目现实冻结在旧会话中。

因此，可以借鉴 Hanako 的会话能力快照，但不能把 Collaborator 的 Group 简化为一个冻结会话。

## 4. 功能横向矩阵

| 维度 | Nuke AI Collaborator | OpenHanako | 判断 |
|---|---|---|---|
| 产品定位 | AI 项目团队、群聊协作 | 个人长期 AI 助手 / Agent OS | 定位不同，不应互相替代 |
| 真实用户 | JWT、多用户、Group membership | 主要面向本地单用户 | Collaborator 更适合团队 |
| 核心协作单位 | Group | Agent Session / Channel | 各自匹配产品定位 |
| Bot 组织 | BA、Dev、QA、PM 等角色共同参与 | 多 Agent，主要围绕个人任务 | Collaborator 更强 |
| 数据隔离 | 每 Group 独立 SQLite、Workspace 和 Worker 路由 | Agent 目录隔离，部分个人数据全局共享 | Collaborator 隔离更彻底 |
| 群聊功能 | @、回复、撤回、置顶、Reaction、已读、公告、Recap | 以 Agent 会话管理为中心 | Collaborator 团队体验更强 |
| 会话能力 | Bot 执行 WAL、事件、parent、恢复 | 稳定 ID、locator、branch、fork、hibernate、快照 | Hanako 更成熟 |
| 多 Agent 编排 | 声明式 workflow、stage、gate、恢复、Coding Agent | workflow、subagent、后台任务 | Collaborator 更适合业务流程 |
| 子 Agent | 同步/后台派生、最大深度、权限衰减 | 子 Agent、任务线程、隔离会话 | 能力接近 |
| 长期记忆 | SQLite canonical + outbox + Chroma + Fact/Reflection/Tool Episode | SQLite FTS + Summary/Markdown 编译 | Collaborator 模型更先进 |
| 个人记忆 | Vault/API 边界已有，产品 UI 不完整 | 个人资料、Pinned Memory、长期画像更成熟 | Hanako 产品化更完整 |
| 自学习 | Experience → Draft Skill → 审批 → Active | 有 Skills/Experience，治理链较弱 | Collaborator 更强 |
| Skills | System/Role/Group/Learned/External 分层与懒加载 | 多来源 Skill 与会话快照 | Collaborator 更适合组织复用 |
| Tool Loop | 多轮工具调用、并发、compact、WAL | 成熟 AgentSession + Tool Loop | 接近 |
| MCP | 独立 Collector 持有连接，Worker 通过 Proxy 调用 | Server 进程内统一管理 | Collaborator 多进程边界更清晰 |
| 权限 | HIL、持久化规则、Shell 守卫、子 Agent 权限衰减 | read-only/ask/auto/operate + OS sandbox | 各有所长 |
| 沙箱 | Group Workspace、容器沙箱、路径守卫 | Seatbelt/bwrap/Windows restricted token | Hanako 跨平台成熟，Collaborator 项目隔离更强 |
| 插件 | Python Executor/Orchestrator/PluginHost | SDK、协议、Tool、Route、Provider、页面、Widget | Hanako 功能更完整 |
| 插件安全 | 动态 Python 代码运行在主运行时 | 插件 JavaScript 同样缺少真正代码沙箱 | 两边都有高风险边界 |
| 外部渠道 | WebSocket Web UI；GitHub/Jira 属于工具集成 | Telegram、飞书、QQ、微信、钉钉 | Hanako 明显更强 |
| 自动化 | Cron、Workflow、Coding Agent Task | Heartbeat、Automation、Scheduler | Hanako 更偏个人主动性 |
| 模型管理 | 多模型支持，兼容逻辑仍较分散 | ModelManager、Provider Registry、OAuth 和兼容层 | Hanako 更成熟 |
| 文件与媒体 | 上传、图片、文档、Workspace 文件 | 统一 SessionFile/Media，跨入口复用 | Hanako 抽象更成熟 |
| 前端 | 群聊体验丰富，Group/Chat Store | 复杂 Agent/Session 控制台，多 Slice Store | 各自匹配产品重心 |
| 部署与伸缩 | Supervisor + Worker × N + MCP Collector | Electron + 独立 Server 子进程 | Collaborator 更适合服务化 |
| 崩溃恢复 | Group 分片、Worker 恢复、Session WAL、Workflow State | UI/Server 分离、Session 恢复与休眠 | Collaborator 后端拓扑更强 |
| 可观察性 | trace_id、metrics、session event、workflow state | Usage ledger、session timeline、checkpoint | 可以互补 |

## 5. Collaborator 已形成的相对优势

### 5.1 Group 是物理隔离边界

Collaborator 不只是用 `group_id` 做逻辑过滤，而是把 Group 映射到独立 SQLite 和 Workspace，并由 Supervisor 做 Worker 路由。

关键代码：

- `backend/runtime/dbpaths.py`
- `backend/runtime/supervisor.py`
- `backend/runtime/worker.py`
- `backend/workspace/layout.py`
- `backend/api/groups.py`

由此形成的能力包括：

- Group 独立 DB。
- Group 独立 Workspace。
- Bot 私有目录和 Group 共享目录。
- Worker affinity。
- Group 级任务取消和恢复。
- Group 删除时清理对应 DB 与 Workspace。
- 记忆召回强制带 `group_id + bot_id` 过滤。

这比 Hanako 的个人多 Agent 模型更适合企业和项目隔离，不能为了复制 Hanako 的个人连续性而弱化。

### 5.2 多 Bot 协作是执行编排，不只是对话模拟

Collaborator 已有：

- 声明式 Orchestrator。
- Workflow Stage。
- 人工确认 Gate。
- Structured Completion Signal。
- Workflow State 持久化。
- 崩溃恢复。
- Coding Agent Task。
- Side-effectful Session WAL。

关键代码：

- `backend/core/orchestration/declarative.py`
- `backend/core/workflow.py`
- `backend/core/workflow_store.py`
- `backend/core/runner.py`
- `backend/plugins/agent_dashboard/orchestrator.py`
- `backend/sessions/recovery.py`

Hanako 的 workflow 更接近个人 Agent 的长任务；Collaborator 已开始形成面向团队业务的工作流运行时。

### 5.3 记忆真相源与向量索引分离

Collaborator 当前的记忆演进方向是：

```text
Per-Group SQLite canonical records
  → durable projection outbox
      → Chroma projection
          → semantic recall
```

关键代码：

- `backend/memory/module.py`
- `backend/memory/application/bot_facts.py`
- `backend/memory/application/reflections.py`
- `backend/memory/infrastructure/projection_outbox.py`
- `backend/ai/memory_provider.py`

这一设计支持：

- Fact/Reflection 的 provenance。
- `active → superseded → expired/deprecated` 生命周期。
- Durable outbox。
- Projection 重建。
- 旧 projection tombstone。
- Group/Bot ownership。
- 语义召回和结构化真相源分离。

Hanako 的 Markdown + FTS 更简单、透明，但 Collaborator 不应退回以文本文件为 canonical memory 的实现。

### 5.4 MCP 单进程原则更适合多 Worker

Collaborator 明确由 MCP Collector 独占 MCP 连接：

```text
Worker
  → MCP_CALL
      → Supervisor
          → MCP Collector
              → MCP Server
```

关键代码：

- `backend/runtime/mcp_collector.py`
- `backend/executors/mcp_bridge.py`
- `backend/executors/providers/mcp_proxy.py`

这个设计规避了 MCP/anyio 连接跨任务和跨进程生命周期问题，也比在每个 Worker 直接建立连接更节省资源。

## 6. 值得借鉴的能力

### 6.1 P0：Execution Capability Manifest

Hanako 创建 Session 时会固定模型、Provider、System Prompt、Skills、Tools、权限、Thinking Level、记忆版本和授权目录。

这解决的是可复现性问题：同一个任务为什么在重试后产生不同结果？

Collaborator 已通过 `agent_sessions.config_json` 保存部分执行配置，但还缺少完整、版本化、可核验的能力清单。建议在现有 Session 模型上增加 `execution capability manifest`，例如：

```json
{
  "manifest_version": 1,
  "executor": "tool_loop_v1@3",
  "provider": "anthropic",
  "model": "claude-x",
  "system_prompt_hash": "sha256:...",
  "traits": [
    {"name": "python_dev", "version": "2", "hash": "sha256:..."}
  ],
  "skills": [
    {"name": "code_review", "version": "4", "hash": "sha256:..."}
  ],
  "tool_schema_hash": "sha256:...",
  "permission_rules_hash": "sha256:...",
  "memory_revision": 381,
  "sandbox_policy": "group-container-v2"
}
```

冻结边界必须适配 Collaborator：

| 应冻结 | 应保持动态 |
|---|---|
| Model / Provider | 最新群聊消息 |
| Prompt / Trait 版本 | BOARD / SPEC |
| Skill 版本 | Group 共享文件 |
| Tool Schema | 项目代码 |
| Permission Policy | 其他 Bot 的最新交付 |
| Executor 版本 | Group 当前有效事实 |

原则是：**冻结 Bot 的执行能力，不冻结 Group 的协作现实。**

### 6.2 P0：统一 Artifact 模型

Collaborator 当前存在多种资源路径：

- 消息附件。
- Workspace 文件。
- Bot 交付物。
- 工具生成文件。
- Git Worktree 文件。
- 将来的外部渠道附件。

建议参考 Hanako 的 SessionFile/Media 抽象，建立稳定 Artifact ID：

```text
Artifact
├── artifact_id
├── group_id
├── bot_id / session_id / workflow_run_id
├── origin: upload | tool | workspace | connector
├── mime_type
├── checksum
├── storage_locator
├── display_name
├── authorization_scope
├── created_by
└── lifecycle
```

价值包括：

- 外部渠道文件可直接进入工具链。
- Bot 报告可作为聊天交付卡片发送。
- Workflow 可把 Artifact 作为阶段输入输出。
- 文件移动后仍可通过稳定 ID 引用。
- 审计可以追踪文件由谁、在哪个步骤产生。
- 多 Bot 可以显式交接产物，而不是依靠不稳定路径字符串。

### 6.3 P0：版本化事件协议与断线 Cursor

Collaborator 的 Python 服务端和 React 前端之间存在大量手写事件：

- message / typing
- tool event
- permission request / response
- workflow state
- recap
- skill draft
- resource update

建议建立共享协议目录：

```text
protocol/
├── envelope.schema.json
├── message.schema.json
├── tool-event.schema.json
├── workflow-event.schema.json
├── permission.schema.json
└── resource-event.schema.json
```

从同一份 Schema 生成：

- Python/Pydantic 类型。
- TypeScript 类型。
- 运行时 Validator。
- Protocol Version。
- Event Cursor。
- Reconnect / Catch-up 语义。

这能减少服务端事件已变化、前端却静默忽略的协议漂移问题。

### 6.4 P1：Model / Provider Registry

建议集中管理：

- `provider_id + model_id` 唯一身份。
- Tool Calling 能力。
- Vision 能力。
- Thinking 参数映射。
- Context Window。
- OAuth / API Key。
- Provider 特殊字段兼容。
- 定价。
- 模型废弃与替换关系。

Bot 配置只保存稳定标识和策略，不让各执行路径自行判断 Provider 差异。

### 6.5 P1：把 Session WAL 产品化为 Execution Timeline

Collaborator 已有：

- `agent_sessions`
- `session_events`
- snapshot
- crash recovery
- parent session
- token usage

这些目前主要服务后端恢复。可以进一步提供用户可理解的执行详情：

```text
需求输入
  → 使用了哪些上下文
  → 采用哪版 Skill
  → 调用了哪些工具
  → 权限在哪一步批准
  → 修改了哪些文件
  → 执行了哪些测试
  → 产生了哪些记忆或 Experience
  → 最终交付了哪些 Artifact
```

Execution Timeline 可以同时承担：

- 审计记录。
- 失败定位。
- Workflow 重试入口。
- Experience 学习证据。
- 用户信任界面。

不建议为普通群聊引入任意分支；优先为 Coding Task、Workflow Run 和长任务提供 checkpoint/fork。

### 6.6 P1：Channel Adapter

OpenHanako 已有 Telegram、飞书、QQ、微信和钉钉适配。Collaborator 当前的 `integrations/` 主要是 Git/GitHub/Jira 工具集成，还没有真正的聊天渠道归一化层。

适合 Collaborator 的输入模型：

```text
InboundEnvelope
├── connector
├── external_tenant
├── external_channel
├── external_user
├── mapped_group_id
├── mapped_member_id
├── message
├── mentions
├── artifacts
└── reply_context
```

归一化后复用现有链路：

```text
select_triggered_bots
  → dispatch
      → workflow
          → group broadcast / connector reply
```

建议先选择飞书或 Slack 中的一个完成端到端验证，不同时铺开多个渠道。

### 6.7 P1：Persistence Store Registry

Collaborator 已有多种持久化载体：

- Central SQLite。
- Per-Group SQLite。
- Chroma。
- Workspace Markdown。
- Skill 文件。
- Session WAL。
- Workflow State。
- Plugin Task Store。
- Projection Outbox。
- Git Worktree。

建议增加统一 Store Registry，描述：

- Owner。
- Scope。
- Canonical / Projection 属性。
- 生命周期。
- 迁移方式。
- 恢复方式。
- 是否可重建。
- 是否允许 quarantine。
- 删除和导出语义。

示例：

```text
memory_records       canonical
Chroma               rebuildable projection
MEMORY.md            human-readable projection
session_events       ADD-only execution evidence
workflow_state       recoverable runtime state
deliverables         project artifacts
```

### 6.8 P2：Personal Knowledge Vault 产品化

Collaborator 已有 Personal Memory 后端接口与授权边界：

- `backend/api/personal_memory.py`
- `backend/ai/personal_vault.py`
- `backend/memory/application/authorized_personal.py`

但还缺少用户可感知的完整体验：

- 系统记住了什么。
- 记忆来自哪里。
- 哪些 Group/Bot 可以使用。
- 何时被使用过。
- 删除后影响什么。
- 用户固定事实与模型推断如何区分。

应继续使用 Collaborator 自己的授权模型：

```text
Person Fact
  → 用户授权
      → Scoped Projection
          → 指定 Group / Bot / Purpose
```

不应照搬 Hanako 把全局个人画像默认注入所有 Agent 的方式。

## 7. 不建议照搬的部分

### 7.1 不冻结整个 Group 上下文

如果冻结 BOARD、SPEC、共享代码、其他 Bot 交付和 Group Facts，Bot 会在协作中读取旧现实。只应冻结执行能力和权限版本。

### 7.2 不创建超大 CollaborationCoordinator

Hanako 的 `core/session-coordinator.ts` 已超过 8,000 行，同时承担创建、恢复、分支、工具、持久化、媒体和插件等多种职责。

Collaborator 已经拥有 runtime、orchestration、session、memory、tool、permission 等边界，应继续保持分层，不要增加一个统管所有协作行为的中心类。

### 7.3 不用 Markdown 取代 Canonical Memory

Markdown 可以作为人类可读投影，但不应取代已有的 canonical record、provenance、relation、validity 和 outbox。

### 7.4 不在代码隔离前扩张插件能力

Collaborator 的外部 Executor/Orchestrator Python 会直接 import 到运行时；Hanako 的 restricted 插件同样不是代码沙箱。

正确演进顺序应是：

```text
独立插件进程
  → Capability Manifest
      → 结构化 IPC
          → 权限与资源额度
              → 生命周期管理
                  → UI / Route / Provider Contributions
```

### 7.5 不复制巨型前端 Store

会话产品化后，应继续按 Group、Chat、Workflow、Execution、Artifact、Memory 分领域管理状态，避免全部进入一个全局 Store。

## 8. 推荐实施路线

### 阶段一：提高可复现性和可信度

1. 在现有 `agent_sessions` 上增加 Capability Manifest。
2. 保存 Model、Prompt、Trait、Skill、Tool、Permission 和 Executor 版本 Hash。
3. 建立 Execution Timeline API 和基础 UI。
4. 将 Skill 学习证据绑定到 Session、Tool Event 和 Test Result。
5. 给 WebSocket 协议增加版本与 Cursor。

### 阶段二：统一协作资源

1. 引入 Artifact ID。
2. 统一上传、Workspace 文件、工具输出和 Workflow Deliverable。
3. Artifact 接入权限、审计和生命周期体系。
4. 为外部渠道预留 `origin` 和 `external_locator`。

### 阶段三：扩展入口与生态

1. 建立 Channel Adapter。
2. 先接入一个真实企业聊天平台。
3. 插件迁移到独立进程。
4. 建立 Provider Registry。
5. 在安全边界稳定后，再开放页面、Widget、Connector、Provider 等插件贡献点。

### 阶段四：个人连续性

1. Personal Knowledge Vault UI。
2. 展示记忆来源、置信度、授权和撤回。
3. 跨 Group 使用必须经过 Scoped Projection。
4. Scheduler 执行绑定 Bot Identity、Permission Snapshot 和审计记录。
5. 将主动提醒通过 Web/飞书等 Channel 投递。

## 9. 建议优先级

| 优先级 | 项目 | 主要价值 | 主要风险 |
|---|---|---|---|
| P0 | Capability Manifest | 可复现、可审计、支持可靠重试 | 快照与动态 Group 状态边界需要定义清楚 |
| P0 | Artifact Model | 统一多 Bot 交付、附件和渠道文件 | 需要迁移现有路径引用 |
| P0 | Versioned Event Protocol | 降低前后端协议漂移，增强断线恢复 | 需要兼容旧客户端 |
| P1 | Execution Timeline | 增强信任、调试、学习证据 | UI 容易过度暴露底层噪声 |
| P1 | Provider Registry | 降低模型兼容分散和重复判断 | 需要迁移现有 Bot 配置 |
| P1 | Store Registry | 降低迁移、灾备和删除语义风险 | 需要持续维护，不应只写一次文档 |
| P1 | Channel Adapter | 让 AI 团队进入真实工作沟通渠道 | 身份映射、权限和消息幂等复杂 |
| P1 | Plugin Process Isolation | 控制第三方代码风险 | IPC、调试和兼容成本较高 |
| P2 | Personal Vault UI | 增强个人连续性和记忆可控性 | 必须严格遵守跨 Group 授权 |

## 10. 待讨论决策

以下问题需要在 Final 版本前明确：

1. Capability Manifest 的边界是每次 Bot Run、每个 Workflow Stage，还是可复用的 Execution Profile？
2. Skill 运行时是否严格锁定版本，还是只记录实际内容 Hash？
3. Group Memory 的哪一部分参与执行快照，哪一部分始终动态读取？
4. Artifact 的 canonical locator 放在 Group DB、Central DB，还是独立 Metadata Store？
5. Artifact 是否需要版本关系和派生关系，例如 `report-v2 derives_from report-v1`？
6. Execution Timeline 面向普通用户和管理员是否需要两种视图？
7. Channel 第一优先级是飞书、Slack，还是企业微信？
8. 外部渠道身份如何映射到 Central User、Group Member 和 Personal Vault？
9. 插件隔离采用长期 Worker、每插件子进程，还是按调用启动的 Sandbox Process？
10. Provider Registry 是否同时承担费用预算、模型路由和降级策略？
11. Personal Projection 默认是 deny、ask，还是按 Group 显式配置？
12. 是否把 Store Registry 实现为可执行注册表和测试，而不只是设计文档？

## 11. 代码索引

### Collaborator

- `backend/runtime/supervisor.py`：Supervisor 与 Group/Worker 路由。
- `backend/runtime/worker.py`：Worker 生命周期和消息分发。
- `backend/runtime/mcp_collector.py`：MCP 单进程连接所有权。
- `backend/runtime/dbpaths.py`：Per-Group DB 路径。
- `backend/executors/base.py`：ExecutionContext、Executor、PluginManifest 基础协议。
- `backend/executors/plugins/tool_loop_v1.py`：主 Tool Loop。
- `backend/executors/plugins/tool_loop_v1_helpers.py`：上下文、子 Agent 和恢复辅助逻辑。
- `backend/executors/plugins/workspace_tools.py`：Workspace、Shell、Spawn Agent 和权限 Hook。
- `backend/permissions/engine.py`：权限决策与子 Agent 权限衰减。
- `backend/core/orchestration/declarative.py`：声明式 Orchestrator。
- `backend/core/orchestration/prompt_builder.py`：Prompt、Traits 和 Skills 组装。
- `backend/core/workflow_store.py`：Workflow 状态恢复。
- `backend/sessions/store.py`：Session WAL。
- `backend/sessions/recovery.py`：崩溃恢复。
- `backend/memory/`：Canonical Memory bounded context。
- `backend/ai/memory_provider.py`：Tool Loop 与 Memory Provider 接缝。
- `backend/plugins/host.py`：Plugin Host。
- `backend/plugins/agent_dashboard/orchestrator.py`：Coding Agent Task。
- `backend/api/personal_memory.py`：Personal Memory API。

### OpenHanako

- `core/engine.ts`：总装配和运行时门面。
- `core/agent.ts`：Agent 初始化、人格和 Prompt。
- `core/session-coordinator.ts`：Session 生命周期核心。
- `core/session-manifest/store.ts`：稳定 Session ID、locator 和 capability snapshot。
- `core/desktop-session-submit.ts`：统一桌面会话提交。
- `lib/memory/fact-store.ts`：SQLite/FTS Fact Store。
- `lib/memory/memory-ticker.ts`：长期记忆定时处理。
- `lib/memory/compile.ts`：Markdown 记忆编译。
- `lib/bridge/bridge-manager.ts`：外部渠道桥接。
- `core/plugin-manager.ts`：插件管理。
- `shared/persistence/store-registry.ts`：持久化资产登记。
- `desktop/src/react/services/websocket.ts`：前端 WebSocket 与重连。

## 12. 可观测性事件政策实现状态

> 实现日期：2026-08-02
> 状态：第九阶段 Memory/Skill Evidence Links 已进入运行链路（CURRENT）

已新增可执行的 Event Policy Registry：

- `backend/observability/event_policy.py`
- `backend/observability/__init__.py`

当前实现不是让模型判断事件是否重要，而是由平台根据事件类型、工具名称和实际参数进行确定性分类。

### 12.1 当前事件分类

```text
EventClass
├── audit
├── timeline
├── diagnostic
├── metric
└── ephemeral

EffectClass
├── read
├── durable_write
├── external_write
├── authorization
├── control_flow
├── recovery
├── billable
├── learning
├── verification
├── lifecycle
└── unknown
```

每条已持久化的 Session Event 会自动获得：

- `event_id`
- `policy_version`
- `classes`
- `effects`
- `retention`
- `payload_policy`
- `business_significant`
- `allow_sampling`
- `reason`
- 当前 Trace Context 存在时的 `trace_id`

元数据写入现有 Payload 的保留字段 `_observability`，因此无需数据库迁移，也不破坏现有 Session Recovery 格式。

### 12.2 当前 Tool Effect 分类

工具不是只按名称统一分类。`run_shell` 会结合实际命令区分：

| 示例 | 分类 |
|---|---|
| `ls -la` | Diagnostic / Read |
| `git status -sb` | Diagnostic / Read |
| `pytest -q` | Timeline + Metric / Verification |
| `python3 -m pytest` | Timeline + Metric / Verification |
| `npm run build` | Timeline + Metric / Verification |
| `write_file` | Audit + Timeline / Durable Write |
| `git push origin main` | Audit + Timeline / External Write |
| `cat ~/.ssh/id_rsa` | Security Audit / Sensitive Read |
| `sed -i ...` | Audit + Timeline / Durable Write |
| 复合或重定向 Shell | Security Audit / Unknown，保守处理 |
| 未登记的插件工具 | Security Audit / Unknown，默认不采样 |

### 12.3 Session 生命周期事件

`sessions.update_session_status()` 现在会在状态真正发生变化时，以同一数据库事务写入 `session_status` 事件。

以下状态被视为业务事件：

- `completed` / `failed`：Timeline + Metric / Lifecycle。
- `needs_review` / `awaiting_recovery` / `recovering`：Audit + Timeline / Recovery。
- 其他非终态变化：Diagnostic / Lifecycle。

`session_status` 已被 Session Recovery 明确识别为非对话 WAL 事件，恢复时不会污染模型消息，也不会产生未知事件警告。

### 12.4 当前验证

已新增和更新：

- `backend/tests/test_event_policy.py`
- `backend/tests/test_sessions.py`
- `backend/tests/test_permissions.py`

第一阶段定向回归覆盖 Event Policy、Session Store、Token Tracking、Interaction、Executor 解耦、后台 finalize 和 Recovery Resume：

```text
49 passed
3 subtests passed
1 dependency deprecation warning
```

第二阶段定向回归覆盖权限引擎、builtin/shell 权限 hook、MCP proxy/provider、Session Recovery 与 Event Policy：

```text
153 passed
3 subtests passed
1 dependency deprecation warning
```

### 12.5 Permission 审计事件（第二阶段）

权限引擎现在通过 Worker runner 注入的异步 Event Recorder 写入当前 Session，不直接依赖数据库，也不跨越 Supervisor / Worker / Collector 的进程边界。

事件包括：

- `permission_requested`：真正需要人工决策时产生。
- `permission_approved`：人工批准始终产生；持久规则、once grant、workspace confinement 或 bypass 模式自动放行时，仅在 Tool Effect Policy 判定该工具具有业务意义时产生。
- `permission_denied`：人工拒绝、deny rule、dontAsk、子 Agent 权限衰减、超时或客户端断线时产生。

每一次权限判断生成一个稳定的 `permission_id`。需要人工确认时，WebSocket 的 `request_id`、请求事件和最终决策事件使用同一个 `perm_*` ID，因此可以从 UI 请求一路关联到 Session Timeline。

权限事件保存：

- `permission_id`
- `tool_name`
- `arguments_sha256`
- `bot_id` / `group_id` / `spawn_depth`
- `decision_source`
- `persistence`（`once` / `always`，存在时）
- `force_ask`

原始工具参数不会复制到权限审计事件，只保存确定性 SHA-256 指纹；参数正文仍由既有 `tool_call` WAL 事件负责。这样既可做同一次调用的关联，又减少 token、密钥和命令正文被重复写入安全审计日志的风险。

`decision_source` 当前可区分：

- `human_required` / `human_response`
- `allow_rule` / `deny_rule`
- `once_grant`
- `workspace_confined`
- `bypass_permissions` / `dont_ask`
- `subagent_attenuation`
- `timeout`
- `group_disconnected`

权限事件均由 Event Policy Registry 标记为 `Audit + Timeline / Authorization`、`security_audit` retention、禁止采样。Session Recovery 将它们视为控制面元数据，不会把它们还原成模型对话，也不会产生未知事件警告。

为了避免把“经过权限函数”误判成“具有业务意义”，安全读取的自动放行不会生成权限审计事件。例如 `read_file` 和 `list_workspace` 仍可保留为可采样 Diagnostic Tool Event，但不会额外产生 `permission_approved`；人工请求、任何拒绝，以及写入、外部副作用、验证、控制流等有业务意义工具的自动批准仍会记录。

事件记录采用 fail-open observability：Event Recorder 故障会记录服务端异常，但不会改变权限引擎已经依据安全规则做出的允许或拒绝结果。这里的 fail-open 仅指“可观测性写入失败不改变授权语义”，不是授权检查本身 fail-open。

### 12.6 Workflow Observation Envelope（第三阶段）

默认编排器 `workflow_v1` 已将 Stage / Gate 状态转换接入独立的 group-local `workflow_observations` 存储。这些事件不绑定某一个 Bot Session，因为一个 Workflow 可能跨越多个 Bot、多个 Session 和多次人工门禁。

统一 Envelope 包含：

- `schema_version`
- `event_id`
- `occurred_at`
- `event_type`
- `aggregate.type = workflow`
- `aggregate.id = workflow_id`
- `context.group_id`
- `context.orchestrator_id`
- `context.workflow_id`
- `context.stage_id` / `stage_index`
- `context.gate_id` / `gate_instance_id`
- `context.session_id`
- `actor`
- `payload`
- `policy`

关联 ID 的语义为：

```text
group_id
└─ workflow_id                 一次完整工作流实例
   ├─ stage_id                 逻辑阶段
   ├─ gate_id                  前端兼容的逻辑门 ID
   ├─ gate_instance_id         每次真实挂门的唯一实例
   └─ session_id               引发该转换的 Bot Session（存在时）
```

`gate_id` 继续保持原有的 `{group}-{stage}` 格式，不破坏前端确认卡片协议。新增的 `gate_instance_id = gate_*` 用来区分同一阶段在修订、返工和多次进入时产生的不同门禁实例。

当前已接入的事件：

- `workflow_started`
- `stage_entered`
- `stage_completed`
- `gate_requested`
- `gate_approved`
- `gate_revision_requested`
- `stage_rework_started`
- `workflow_paused`
- `workflow_completed`
- `workflow_recovered`

`ExecutionResult` 现在可携带 `session_id`。Runner 在 Bot 产出导致 Stage/Gate 转换时，会把该 Session ID 写入 Envelope，因此可以从 Workflow Timeline 下钻到 Session Event，再从 Session Event 下钻到 Tool Call / Permission Decision。

崩溃恢复时，存量 Workflow 若没有 `workflow_id` 或挂起门没有 `gate_instance_id`，恢复器会补齐并立即回写快照，然后产生 `workflow_recovered` 事件。

Workflow Observation 复用 Event Policy Registry，不在编排器中重新定义 retention/sampling 规则。其持久化同样采用 fail-open observability：观测存储故障会记录服务端异常，但不会阻止工作流推进或改变 Gate 决策。

第三阶段定向回归覆盖 Workflow 状态机、Envelope 持久化、Schema Split、DB Migration 与 Event Policy：

```text
195 passed
3 subtests passed
1 dependency deprecation warning
```

### 12.7 Unified Timeline API（第四阶段）

已新增 Group 级统一查询接口：

```http
GET /api/groups/{group_id}/timeline
```

它不是简单拼接两张表，而是在读取边界把三类业务对象归一为同一种 Envelope：

```text
workflow_observations ────────────────┐
                                      ├─ Unified Timeline Envelope
session_events ──普通执行事件────────┤
               └─ permission_* ──────┘  source = permission
```

统一结果包含 `schema_version`、`event_id`、`occurred_at`、`source`、`event_type`、
`aggregate`、`context`、`actor`、`payload` 和 `policy`。其中 Permission 仍存储在
Session WAL 内，以保持权限决策与实际工具调用的事务邻近性；查询时则提升为独立的
`source = permission`，并以稳定的 `permission_id` 作为 aggregate ID。

默认只返回 `business_significant = true` 的事件，因此安全读取等可采样 Diagnostic
不会淹没产品时间线。API 支持：

- `source`：`workflow` / `session` / `permission`，可重复传入。
- `event_type` 与 `event_class` 筛选。
- `business_significant=true|false` 精确筛选。
- `workflow_id` / `session_id` 关联下钻。
- `limit`（1–200）和不透明 `cursor`，按 `occurred_at + source rank + row id`
  稳定倒序翻页，避免同毫秒事件丢失或重复。

Group 隔离在 SQL 层执行，而不是只相信请求参数：Session 分支必须通过
`session_events JOIN agent_sessions` 且匹配 `agent_sessions.group_id`；Workflow 分支
必须匹配 `workflow_observations.group_id`。路由还会先在中央库验证当前用户的
`group_memberships`，未授权时返回 404，并且不会初始化或打开目标 Group DB；验证通过后
才绑定该 Group 的私有 SQLite，不会查询其他 Group DB。

读取器兼容第一阶段以前没有 `_observability` 元数据的历史 Session Event：查询时用
同一 Event Policy Registry 确定性补充分级，并用本地行 ID 生成稳定兼容 ID，不会在
每次翻页时产生不同身份。损坏或非对象 JSON Payload 会降级为空对象，不影响整条时间线。

第四阶段定向测试覆盖跨来源排序、Permission 提升、默认业务筛选、Diagnostic 查询、
关联筛选、游标无重复/无遗漏、无效输入和 SQL 层 Group 隔离：

```text
11 passed
1 dependency deprecation warning
```

Timeline 已在主聊天界面接入独立侧栏，可从 Header 的指南针入口打开。界面提供：

- Workflow / Session / Permission 三类来源切换。
- 业务事件与 Diagnostic 事件范围切换。
- Event Policy effect 标签和本地时间展示。
- Workflow / Session / Permission 关联 ID 下钻。
- Payload 按需展开，默认不占用主时间线视觉空间。
- 基于后端不透明 Cursor 的“加载更早事件”。
- 加载、空数据、错误重试和手动刷新状态。

前端定向验证：

```text
4 passed
Vite production build passed
```

### 12.8 Payload Policy Enforcement（第六阶段）

Event Policy Registry 中原本只是声明性的 `payload_policy`，现在已在 Session Event
和 Workflow Observation 两个持久化入口集中执行：

```text
原始 Payload
  → 确定 Event Policy
  → 递归 Secret Redaction
  → inline / summary / reference-only 判定
  ├─ 小 Payload：直接保存脱敏结构
  └─ 大 Payload：公开事件保存摘要 + Artifact Reference
                  Group 私有 Artifact 保存完整脱敏 JSON
```

三种策略的执行语义：

- `redacted`：递归处理字符串、对象和数组；超过 16 KiB 后自动 Artifact 化。
- `summary`：4 KiB 内保留脱敏结构；超过阈值后仅保留关联字段、2,000 字符有界摘要
  和 `_artifact` 引用。
- `reference_only`：无论大小都不内联正文，只保存 Artifact 引用。

Artifact 表位于 Group 私有 SQLite，保存 `artifact_id`、`event_id`、Policy、SHA-256、
字节数和完整脱敏 JSON。Artifact 与 Session Event / Workflow Observation 在同一数据库
事务写入；摘要引用携带相同 SHA-256。读取时会验证存储摘要和引用摘要，缺失或篡改时
抛出 `PayloadArtifactError`，不会把不完整上下文静默用于恢复。

Session Recovery 显式使用 `hydrate_artifacts=true` 读取完整脱敏 Payload；普通 Session API
和 Unified Timeline 默认只返回公开摘要，因此执行恢复能力与产品可见数据最小化可以同时
成立。经 Group membership 校验后，可通过以下接口按需读取 Artifact：

```http
GET /api/groups/{group_id}/observability/artifacts/{artifact_id}
```

当前集中脱敏复用执行器已有的 PEM、JWT、AWS、GitHub、OpenAI、Anthropic、Slack、Google、
Bearer、凭据 URL 和 Secret Assignment 高精度规则，不另建一套容易漂移的正则。

第六阶段定向回归覆盖三种 Policy、嵌套 Secret、Session 摘要/水合、缺失 Artifact fail-closed、
Workflow Artifact、迁移、Schema Split、Timeline 与 Recovery：

```text
180 passed
1 dependency deprecation warning
```

### 12.9 Workflow Atomic Transition（第七阶段）

Workflow Runner 不再依次执行“保存状态 → 提交 Observation”两个独立事务，而是通过
`workflow_store.commit_transition()` 在同一个 Group 私有 SQLite Writer 和事务内完成：

```text
BEGIN
  UPSERT workflow_state  或  DELETE terminal workflow_state
  INSERT observation_artifacts（需要时）
  INSERT workflow_observations
COMMIT
```

如果 Envelope 构造、Artifact 或 Observation 写入失败，复用 Writer 的异常回滚机制撤销
整个事务，不会留下新状态配旧事件或旧状态配新事件。Runner 只有在事务成功提交后才发布
`workflow_update`；终态会在同一事务删除快照并保存 `workflow_completed`。崩溃恢复时补写的
稳定 ID 快照和 `workflow_recovered` 也已合并为同一次原子提交。

不产生 Observation 的旧插件仍走兼容的 `save_state()` / `clear_state()`，下一阶段完成插件
Observation 覆盖后会自然进入原子路径。Worktree promotion 仍先于 terminal transaction，
防止代码合并失败却把 Workflow 标记为完成。

第七阶段定向测试覆盖正常提交、非法 Observation 整体回滚、终态清理与完成事件原子性、
Runner Gate/Workspace 顺序和恢复路径。

### 12.10 Non-default Orchestrator Observation Coverage（第八阶段）

第八阶段已把同一套 Workflow Observation 契约扩展到 Worker 实际注册的全部非默认
Orchestrator：`round_robin_v1`、`discussion_v1` 和 `coding_agent_v1`。三者现在都会生成并
持久化稳定的 `workflow_id`，在开始、业务阶段进入/完成、暂停、完成和崩溃恢复时返回纯
Observation descriptor，因此自动复用第七阶段的状态—事件原子事务。

这里刻意按业务阶段而非执行次数建模：Round Robin 的多轮 Bot 发言属于一个
`round_robin` Stage；Discussion 只在 `discussion → summary` 时产生 Stage 边界；Coding
Agent 使用单一 `implementation` Stage。轮次、参与者数量、完成来源和暂停原因进入 payload，
既能定位执行上下文，又不会把每个 Bot 回合误报成具有业务意义的 Stage 迁移。现有旧快照在
恢复时会补铸 `workflow_id`，随后与 `workflow_recovered` 在同一事务回写，完成兼容升级。

第八阶段定向测试覆盖三种 Orchestrator 的稳定 ID、生命周期事件、Discussion 阶段切换、
暂停原因、终态事件与恢复关联。

### 12.11 Memory/Skill Evidence Links（第九阶段）

第九阶段新增 group-local `session_evidence_links`，把 Session Event 与稳定的 Memory/Skill
reference 建成可查询的多对多关系。链接同时保留 `session_event_id → evidence` 正向遍历和
`evidence_ref → Session Events` 反向遍历；反查 API 为
`GET /api/groups/{group_id}/observability/evidence/events?evidence_ref=...`，沿用 Group 成员
鉴权和 Group DB 绑定，不允许跨 Group 查询。

证据关系明确区分三种语义：

- `injected`：Memory/Skill 被放进模型上下文，只证明“模型可见”，不证明模型采用。
- `cited`：模型在通过 allowlist 校验的工具参数中引用了该 reference，可作为因果使用证据。
- `invoked`：`run_skill` 实际解析并装载了对应 Skill。

Learned Experience/Skill 继续使用 canonical `exp:*` / `skill:*@vN`；文件型 Skill 使用不暴露
本地路径的 content-addressed reference：`skill:file:{layer}:{name}@sha256:{digest}`。常驻
Skill 在注入时记录，动态 Skill 在实际调用后记录；工具执行的 Memory refs 只有通过现有
allowlist 验证后才落 `cited` 链接。Session Event payload 即使因 Payload Policy 被投影成
Artifact，独立关联表和 Timeline 顶层 `evidence_links` 仍可直接查询，不依赖全文水合。

链接与 Session Event 在同一个 Writer 事务提交，非法 kind/ref/relation 会在事件写入前失败，
不会形成孤立事件。第九阶段定向测试覆盖迁移与 Schema Split、正反遍历、事务回滚、Timeline
投影、Memory/Skill 类型识别、文件 Skill 稳定哈希和 Event Policy。

### 12.12 Request-level Model Usage Ledger（第十阶段）

第十阶段把原先只累加在 Session 上的 Token 总数，升级为逐次 Provider 请求可核对的账本。
每一次经 Session-bound `AIService` 发出的请求都会先写 `model_request_started`，再以
`model_request_completed` 或 `model_request_failed` 关闭；上下文溢出后的重试是新的
`request_id`，通过 `retry_of` 指向原请求，避免把两次真实 Provider I/O 合并成一次。

账本逐请求保存：

- 稳定 `request_id`、Session 内单调序号、`session_id` 和可选 `ticket_id` 归属。
- 实际使用的 `provider` / `model`，不再用 Bot 默认模型代替路由后的真实模型。
- `operation`：`tool_loop_iteration`、`final_response`、`final_draft`、审查、重写和
  `skill_fork` 等业务用途。
- 流式标记、响应类型、耗时、成功/失败状态与有界 `error_type`。
- input、output、cache read、cache creation 四个 Token 维度。
- 按实际 Provider/Model 计算的 USD 成本和 `pricing_version`，为未来价格表变化保留审计依据。

`session_events`、`model_usage_ledger` 投影、Session Token 汇总和 Ticket 成本更新共用同一个
Group SQLite Writer 事务。终态重复、缺失 start、Provider/Model 身份不一致或非法负数都会使
整个事件事务回滚，不会出现“事件成功但账本失败”或重复计费。Session 恢复后即使新的
`AIService` 本地序号重新从 1 开始，持久化投影也会延续数据库中的单调序号。

失败请求只记录状态、耗时与错误类别，不臆造 Token 或费用；事件和账本均不保存 prompt、
response 正文或异常消息。完成事件仍进入 Event Policy Registry，成为可进入 Timeline 和
Metric 的 billable 业务事件；start 仅作为可采样 Diagnostic，减少产品时间线噪声。

经 Group membership 校验后，可读取逐请求明细和不受分页 `limit` 影响的精确汇总：

```http
GET /api/groups/{group_id}/observability/model-usage
GET /api/groups/{group_id}/observability/model-usage?session_id={session_id}&limit=200
```

汇总包含总请求、完成、失败、仍打开请求、四类 Token、总费用和总耗时。当前覆盖范围是
绑定 Agent Session 的主推理、最终输出、质量审查/重写和 fork 子 Agent 请求；独立后台摘要
等未绑定 Session、直接调用底层 Provider client 的维护任务不进入此 Session 账本，后续若要
统一计费，应先为它们定义独立 workload identity，而不是伪挂到某个用户 Session。

第十阶段定向测试覆盖非流式/流式请求、真实模型身份、失败零用量、溢出重试关联、Session
恢复序号、原子汇总、重复终态回滚、迁移、Schema Split、Event Policy 和旧 Token 接口兼容。

### 12.13 Retention Policy Executor（第十一阶段）

第十一阶段把 Event Policy Registry 中的 `retention` 从声明变为实际存储行为。Worker 对自己
持有 lease 的 Group 在 hydration 后立即执行一次，此后随 Lifecycle Maintenance 每日执行；
不会由主进程跨库扫描，也不会让两个 Worker 同时维护同一个 Group。

当前策略语义为：

| Policy | 执行动作 |
|---|---|
| `stream_lifetime` | Session 已终态后立即从持久化观察流移除 |
| `diagnostic_14_days` | 14 天后归档凭证并删除事件正文 |
| `execution_90_days` | 90 天后归档凭证并删除事件正文 |
| `group_lifetime` | 执行器不自动删除，随 Group 生命周期保留 |
| `security_audit` | 执行器不自动删除，避免安全审计记录被普通清理策略降级 |

这里的“归档”不是把过期敏感正文换一个表继续无限保存。`observability_retention_archive` 只留下
`source`、原行 ID、稳定 Event ID、事件类型、Retention Policy、发生时间和原存储正文的
SHA-256；不保存 Payload、Prompt、Response、工具结果或异常信息。这既能证明某事件在何时按
什么策略被清理，又真正兑现数据最小化。归档凭证自身随 Group 保留。

清理使用 Group SQLite 的单 Writer，在一个事务内完成：

```text
INSERT payload-free retention receipts
  → DELETE model_usage_ledger（适用时）
  → DELETE session_events / workflow_observations
  → CASCADE session_evidence_links
  → DELETE 对应 observation_artifacts
COMMIT
```

只有 `completed / failed / cancelled / abandoned / superseded / expired` 等明确终态 Session 才会
清理 Session Event；运行中、等待恢复和正在恢复的 Session 即使超过时间阈值也保持完整，避免
破坏 WAL 重建。Model Request 的 start、terminal 和 request ledger 作为一个生命周期整体到期，
不会留下半条请求或悬空外键；Session/Ticket 已结算的累计值不会反向扣减。

执行器同时兼容带 v1 `_observability` / Workflow `policy` 的新事件和没有元数据的历史事件；
后者通过同一 deterministic classifier 回算策略。大批量 ID 查询和删除采用有界 SQL batch，
支持 `dry_run` 预演，重复执行幂等。Lifecycle Stats 会暴露最近一次每 Group 的清理计数和时间。

第十一阶段定向测试覆盖 14/90 天边界、stream lifetime、Security/Group 永久保留、活跃 Session
保护、Payload-free Receipt、Artifact/Evidence 收敛、Model Ledger 原子过期、Workflow 事件、
dry-run、幂等重跑、Migration、Schema Split 和 Worker lease 调度。

### 12.14 尚未实现的边界

前十一阶段已经形成分类、持久化、关联查询、请求计量和 Retention 闭环，以下仍属于后续工作：

- OpenTelemetry Exporter。
- Prometheus 对 Event Policy 的低基数聚合。

这些后续能力应继续使用当前 Registry，而不是在各模块重新硬编码一套“重要事件”判断。

## 13. 当前结论

Collaborator 的长期护城河应当是：

```text
项目事实
+ 多角色 Bot
+ 可恢复 Workflow
+ 可验证 Experience / Skill Learning
+ Group 级安全隔离
```

OpenHanako 的主要优势是：

```text
个人 Agent 连续性
+ Session 产品化
+ 跨渠道存在感
+ 统一资源与 Provider 管理
```

两者最合理的结合方向是：

```text
保留 Collaborator 的 Group / Workflow / Memory / Worker 架构
  +
吸收 Hanako 的 Capability Manifest / Artifact / Channel / Provider / Timeline
  =
兼具团队协作能力与长期连续性的 AI 项目组织系统
```

本文仍是讨论稿。Final 版本应在“待讨论决策”得到结论后，进一步补充：

- 明确的产品边界。
- 目标架构图。
- 数据模型草案。
- 分阶段交付范围。
- 兼容迁移方案。
- 可验收的成功指标。
