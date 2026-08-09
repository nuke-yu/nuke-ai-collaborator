# Nuke AI 完整开发路线与 Channel 独立集成计划

> 基线：2026-08-09，后端全量测试 `2493 passed, 2 skipped`。
>
> 本文汇总当前已交付能力、未完成治理事项，以及 Channel 从独立模块演进为 Group 集成成员的完整开发路线。

## 0. 本轮执行结果

C0–C8 已按顺序完成，并且每个任务均独立提交、可单独检出：

| 任务 | Commit | 当前状态 |
|---|---|---|
| C0 契约与边界 | `918dfcf` | 已完成 |
| C1 Channel-owned 消息/投递状态 | `7cd4456` | 已完成 |
| C2 通用签名 Webhook Connector | `a305b2e` | 已完成（真实平台 Connector 待选择） |
| C3 Channel–Group Binding | `2cb2912` | 已完成 |
| C4 Integration Member | `71d45cc` | 已完成 |
| C5 入站路由到配置 Bot | `ed65487` | 已完成（当前是 Bridge 路由基础层） |
| C6 Group 事件出站 Outbox | `75da38c` | 已完成 |
| C7 脱敏、审计、重试、死信 | `90cf644` | 已完成 |
| C8 独立进程 Bridge 边界 | `0efd8e9` | 已完成（部署编排待接入） |

本轮 Channel 回归测试为 `30 passed`；全量后端测试为 `2493 passed, 2 skipped`。这里的“完成”指计划中的独立模块和边界基础设施已落地，不等同于某个真实企业平台的生产级端到端上线。

## 1. 总体架构原则

Nuke AI 的核心边界保持不变：Group 是协作隔离单元，Bot 是执行主体，Channel 是外部通信模块，Bridge 是两者之间唯一受控的连接边界。

```text
独立 Channel Module
    ├── Channel Core
    ├── Platform Connector
    └── Channel-owned Store
            │
            │ explicit binding / authorization
            ▼
      Channel-Group Bridge
            │
            ▼
      Group Integration Member
            │
      Group / Bot / Workflow
```

必须遵守：

- Channel 未绑定 Group 时，不能读取 Group 数据、触发 Bot 或订阅 Workflow。
- Group 不直接依赖飞书、Slack、企业微信等平台 SDK；平台差异留在 Connector。
- Channel 与 Group 之间只通过版本化 Bridge Envelope 通信。
- 入站消息和出站通知都要经过身份映射、权限策略、脱敏、幂等和审计。
- 所有外部发送都在 Group 状态 SQLite commit 成功后进入 Delivery Outbox。
- Channel 不能因为是“集成成员”而获得 Bot 工具权限、Personal Vault 权限或 HIL 绕过能力。
- 每项功能先补契约和测试，再接入运行时；每项功能形成独立 Commit。

## 2. 当前已经完成

以下基础能力已经交付并逐项提交：

1. Timeline 基线、Group SQL 隔离、执行详情、恢复/取消恢复入口和未投影事件告警。
2. Artifact 版本、派生关系、软删除、撤回、Lineage 查询和 Group 隔离。
3. Capability Manifest、Provider/Model Descriptor、Provider Governance。
4. WebSocket Envelope、Replay Cursor、Catch-up 和客户端去重。
5. Personal Memory 使用来源、Session 使用审计和记录影响范围查询。
6. Store Registry 的 owner、canonical/projection、migration、retention、deletion、backup 元数据。
7. Plugin IPC 的 Manifest hash、方法白名单、HIL、输入输出额度、超时、取消和崩溃状态治理。
8. MCP Bridge/Proxy/Collector 的边界校验、无命名空间 HIL 保护和 per-server OAuth lock 清理。
9. 通用签名 Webhook Channel Adapter 雏形：签名校验、租户/Group 映射、消息幂等、mention、回复和附件接口。

## 3. 非 Channel 剩余任务

### 3.1 Timeline 产品化加固（P1）

- 增加正式的 Session retry 入口，并与现有 recovery 语义区分。
- 接入 Workflow recovery/retry 入口。
- 增加普通成员视图与管理员审计视图的字段和权限分层。
- 为 Timeline 投影增加分页、性能指标和大 Session 限制。
- 对所有未注册事件提供统一告警和事件 Schema 迁移策略。

验收：重试、恢复、权限拒绝、未知事件和大时间线均有 API/UI/测试闭环。

### 3.2 Artifact Workflow 治理（P1）

- 将 Workflow Deliverable 统一登记为 Artifact。
- 明确 Workflow 阶段输入、输出和交接关系。
- 增加引用计数或引用索引，避免删除仍被使用的 Artifact。
- 实现 retention 到期后的物理文件清理执行器。
- 明确 `storage_locator` 的路径守卫、外部 locator 和附件下载权限。

验收：Workflow 交付可追溯到 Session、Bot、Stage 和父 Artifact；删除不会破坏审计链。

### 3.3 Personal Memory Governance（P1）

- 保留撤回前后的影响快照。
- 支持用户确认、纠正和替换个人记忆。
- 将撤回后的旧 Projection 从未来上下文中可靠排除。
- 将影响范围展示扩展到 Group、Bot、Session 和使用时间。
- 为 Personal Vault 删除建立不可恢复但不含正文的审计记录。

### 3.4 Store 策略执行（P1/P2）

- 将 Registry 元数据接入启动期一致性检查。
- 为 migration、retention、deletion、backup 建立可执行接口。
- 为每种 Store 增加 owner 和故障处置 Runbook。
- 明确 canonical Store 与 projection Store 的重建、补偿和校验流程。

### 3.5 Plugin 全面迁移（P2）

- 选择高风险 Coding Executor 作为第一个正式迁移对象。
- 接入 Worker → Plugin IPC 的正式路由。
- 完成资源额度、权限衰减、HIL、取消和崩溃恢复的运行时绑定。
- 迁移其他高风险 Executor，并保留旧 Executor 的兼容回退期。
- 增加插件版本升级、回滚和协议兼容策略。

## 4. Channel 总体目标

Channel 不是 Group 内的 Bot，而是一个独立的双向通信模块：

```text
外部平台消息
    → Channel Connector
    → Channel Core
    → Channel-Group Bridge
    → Integration Member
    → 指定 Bot / Group 消息

Workflow / Session / Artifact 完成
    → Group Event
    → Channel-Group Bridge
    → Channel Delivery Outbox
    → Channel Connector
    → 外部平台
```

第一版只选择一个真实平台完成端到端验证，平台候选为飞书、Slack 或企业微信；不要同时铺开多个 Connector。

## 5. Channel 分阶段开发计划

### C0：冻结 Channel 契约与边界（已完成）

定义稳定的 transport-neutral contracts：

- `InboundEnvelope`
- `OutboundEnvelope`
- `ChannelIdentity`
- `ChannelConversation`
- `DeliveryReceipt`
- `BridgeEnvelope`

每个 Envelope 必须包含：`channel`、`tenant`、外部消息 ID、Group/Binding ID（已绑定时）、方向、事件类型、trace ID、幂等键和版本号。

验收：Channel Core 可以在没有 Group 导入的情况下独立单元测试和运行。

建议 Commit：

```text
feat(channel): define standalone channel and bridge contracts
```

### C1：独立 Channel Core（基础能力已完成）

目录建议：

```text
backend/channels/
├── core/
│   ├── contracts.py
│   ├── signatures.py
│   ├── dedup.py
│   ├── inbound.py
│   ├── outbound.py
│   └── delivery.py
├── connectors/
├── bridge/
└── stores/
```

实现：

- 签名验证和 timestamp/replay 防护。
- 外部消息幂等，不能只依赖进程内 `set`。
- 外部用户、租户、会话和消息的本地模型。
- 出站 Delivery 状态：`pending`、`sending`、`sent`、`retrying`、`failed`、`dead_letter`。
- 限流、超时、重试和平台错误归一化。

验收：Channel 单独启动时不会初始化 Group DB、Worker、Bot 或 MCP。

### C2：Platform Connector（通用参考 Connector 已完成）

当前已完成平台无关的 Signed Webhook Connector：

- webhook/event payload 解析；
- 外部消息发送；
- thread/reply 关联；
- mention 解析；
- 附件下载和上传；
- 平台 token/secret 管理；
- 平台限流和错误码映射。

Connector 不允许直接调用 `dispatch_bots()`、Workflow Runner 或 Group DB。

验收：Connector 可以使用 fake transport 完成入站/出站契约测试。

### C3：Channel-Group Binding（已完成）

新增独立 Binding 模型：

```text
group_channel_bindings
├── binding_id
├── group_id
├── channel_instance_id
├── integration_member_id
├── external_tenant_id
├── external_conversation_id
├── default_bot_id
├── allowed_bot_ids
├── mention_required
├── inbound_policy_json
├── outbound_policy_json
├── status
├── config_version
└── created_by / created_at / updated_at
```

Binding 状态：

```text
configured → pending_approval → active → suspended → revoked
```

只有 `active` Binding 可以收发 Group 相关消息。

验收：未绑定、暂停和撤销状态均不能读取或发送 Group 内容。

### C4：Integration Member（已完成）

Channel 在 Group 中以 Integration Member 展示，但不混入 Bot 调度语义。

推荐使用独立的 `group_integrations` 或 `integration_members` 实体，并在 Group 成员 API 中以虚拟成员展示，避免现有 `member.type == "bot"` 逻辑误把 Channel 当成 Bot。

Integration Member 负责：

- 展示名称和头像；
- Channel 归属；
- Binding 状态；
- 可订阅事件；
- 可使用的 Bot 路由；
- 最小权限策略。

验收：Channel 可以被 Group 识别为成员，但不会出现在 Bot 执行列表、模型上下文或 Tool Registry 中。

### C5：Inbound Channel → Group/Bot（Bridge 路由基础层已完成）

入站流程：

```text
外部事件
 → Connector 验证
 → Channel Core 幂等
 → Bridge 校验 Binding
 → 外部用户映射 Group Member
 → mention/default_bot 路由
 → Group message / Session / Workflow
```

当前已支持：

- 默认 Bot；
- mention 指定 Bot；
- Binding/Group 范围校验；
- reply-to、附件和外部身份进入 Bridge payload；
- 未允许 Bot、多个 Bot mention 和 mention-required 的 fail-closed 拒绝。

真实外部用户到 Group Member 的授权映射、附件 Artifact 化以及 Group 权限/HIL 运行时接入，属于平台端到端接入前的后续工作。

验收：外部消息只触发绑定的 Group 和允许的 Bot，不能跨 Group。

### C6：Group/Bot → Channel Outbound（已完成）

出站订阅事件至少包括：

- `workflow_completed`
- `workflow_failed`
- `permission_requested`
- `artifact_produced`
- `session_recovered`
- `task_stuck`

流程：

```text
Group SQLite commit
 → channel_delivery_outbox
 → 异步 Dispatcher
 → Connector.send()
 → DeliveryReceipt
```

任务开始时应保存 Binding 快照，防止执行期间管理员更换默认 Bot 或目标会话后，结果投递到错误位置。

验收：外部平台失败不回滚 Group 任务；重试幂等；成功发送能关联外部消息 ID 和内部 Event/Session/Artifact。

### C7：安全、审计和运维（基础闭环已完成）

- Channel Secret 只能保存引用，不进 Group DB 或事件 Payload。
- 外发前执行脱敏、长度限制和 Artifact 权限检查。
- Channel 只看得到绑定 Group 的数据。
- 所有绑定、暂停、撤销、路由和投递动作进入审计时间线。
- 增加 dead-letter 队列、人工重放和暂停发送能力。
- 为每个 Channel/Binding 提供健康状态、最后成功投递时间和失败计数。
- 外部平台重复事件、乱序事件和过期事件必须可处理。

### C8：进程与部署隔离（协议边界已完成）

已完成独立 JSONL `BridgeEnvelope` 进程客户端和响应校验；模块依赖保持可分离。生产部署编排仍建议采用：

```text
Channel Process
    ↕ structured IPC / HTTP internal contract
Supervisor / Bridge Worker
    ↕
Group Worker / MCP Collector
```

Channel 进程不能持有 MCP Client、Group SQLite Writer 或 Worker 内存对象。

## 6. 推荐 Commit 顺序

```text
1. feat(channel): define standalone channel and bridge contracts
2. feat(channel): add channel-owned message and delivery state
3. feat(channel): add first platform connector
4. feat(channel): add group binding and integration member model
5. feat(channel): route inbound messages to configured bots
6. feat(channel): deliver group events through channel outbox
7. feat(channel): add channel audit, retry and dead-letter controls
8. feat(channel): add isolated process bridge boundary
```

每个 Commit 必须：

- 可以独立检出；
- 有直接相关测试；
- 不修改无关 Group 执行语义；
- 不把平台 SDK 泄漏到 Group/Core 层；
- 通过 `git diff --check`。

## 7. 后续阶段建议

基础开发已完成。下一阶段应选择一个真实平台（飞书、Slack 或企业微信）做端到端接入，补齐外部用户授权映射、附件 Artifact、平台限流/重放防护、Supervisor 部署编排和真实平台回归测试；不要在未选定平台前虚构平台特性。

这样后续即使替换平台，Group、Bot、Workflow 和 Artifact 的内部模型也不需要重写。
