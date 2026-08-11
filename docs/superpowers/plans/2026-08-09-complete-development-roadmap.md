# Nuke AI 完整开发路线与 Channel 独立集成计划

> 基线：2026-08-11。Channel 本地双平台 E2E 已覆盖；后端全量回归：`2576 passed, 2 skipped, 57 warnings, 40 subtests passed`。
>
> 本文汇总当前已交付能力、未完成治理事项，以及 Channel 从独立模块演进为 Group 集成成员的完整开发路线。

## 0. 本轮执行结果与 Gate 状态

C0–C8 的基础代码已按顺序提交，但经 Review 校准，不能把“契约/模块测试通过”直接称为生产完成。采用五级 Gate：

1. Contract merged
2. Module tested
3. Runtime wired
4. Failure-tested
5. Deployable

| 任务 | 基础 Commit | 当前 Gate | 说明 |
|---|---|---|---|
| C0 契约与边界 | `918dfcf` | 2 | Contract merged + module tested；契约本身不单独部署 |
| C1 Channel-owned 消息/投递状态 | `7cd4456`, `6501e8a` | 4（failure-tested） | 入站 pending/dispatched 恢复、Group 去重、出站 lease/重试/ambiguous dead-letter 已破坏性测试 |
| C2 飞书与个人微信 Connector | `680eb63`, `fef9960`, `2e7054f` | 4（failure-tested） | 飞书 webhook/OpenAPI、个人微信 iLink 轮询/发送/扫码登录已接入；尚缺真实账号 smoke 和二进制附件 Artifact 闭环 |
| C3 Channel–Group Binding | `2cb2912`, `45cd48c`, `5ac65d4` | 4（failure-tested） | Group Owner 配置/提交/审批/暂停/恢复/撤销 API；审批与 Integration Member 原子创建 |
| C4 Integration Member | `71d45cc`, `8d03335`, `5ac65d4` | 4（failure-tested） | Group 成员投影、最小能力、状态联动和跨 Group 授权边界已接入 |
| C5 入站路由到配置 Bot | `ed65487`, `51f1b9c`, `6501e8a` | 4（failure-tested） | 飞书/微信真实 payload 进入 Binding、Integration Member、指定 Bot；崩溃重放和 Worker 端去重已测试 |
| C6 Group 事件出站 Outbox | `75da38c`, `a4c4939`, `ea55c17`, `741efd5`, `594c8c7`, `7c7bcd0` | 4（failure-tested） | Workflow 同事务写 projection queue，Supervisor 补偿投影到 Group outbox，再 relay 到 Channel outbox；多 Binding 原子投影、lineage、重试与 poison dead-letter 已破坏性测试 |
| C7 脱敏、审计、重试、死信 | `90cf644` + review fixes + `3995fcd`, `5c15f0b`, `d2bcac4`, `c432e42`, `6501e8a` | 4（failure-tested） | 脱敏、lease、pause/resume、双层 dead-letter、ambiguous success、健康恢复和 Prometheus 已接入；尚缺部署级告警演练 |
| C8 运行时与部署边界 | `f74c5e3`, `2e7054f` | 4（failure-tested） | 当前单服务产品使用 Supervisor-owned 原生 Connector；Docker/Compose、最小 Secret 环境和生命周期已接入。ProcessClient 保留为未来外置 Connector 边界，不把 OS 沙箱作为当前 Gate 5 前置条件 |

Review 修复 commits：`cf35651`（回执校验）、`ba9b2e4`（存储边界脱敏）、`c66bd83`（delivery lease）、`ecdd9fe`（状态/审计原子事务）、`057d380`（canonical event_id）、`f633bf9`（ProcessClient 故障恢复）、`a4c4939`（Group durable outbox relay）、`a87fbff`（持久化入站去重）、`458e53c`（协议版本和无碰撞 key）、`5bb0370`（raw bytes 验签/replay）、`45cd48c`（Binding/Member/Router 状态一致性）、`3475b40`（payload fail-closed/lease heartbeat）。

第二轮 Review 修复 commits：`f74c5e3`（生产 Connector Manifest 注册和 active Binding fail-fast）、`7f011d6`（历史 open delivery 缺 Connector 时 fail-fast）、`7c7bcd0`（持久化投影队列与 Supervisor 补偿）、`572e9c3`（instance ID canonical migration/quarantine）、`a549001`（source event lineage）、`d4417e4`（Group relay dead-letter/审计/指标）、`c432e42`（可恢复健康语义）、`43228e5`（封死 Binding 审批逃生口）。

当前已选择并交付两个目标平台：飞书和个人微信 iLink。两者均进入 Supervisor 生命周期、Channel Delivery Dispatcher、Docker 配置和本地协议 E2E；不再包含 Slack/企业微信计划。当前状态是 **Gate 4 的 production candidate**，不是 Gate 5：必须使用真实飞书应用和真实微信扫码账号完成测试环境 smoke、速率限制观察和 go/no-go 清单后，才能标记 Deployable。云平台、多租户和 OS/container Connector 隔离属于未来阶段，不是当前小团队单服务部署的阻断项。

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

第一版平台范围已冻结为：**飞书 + 个人微信 iLink**。Slack 和企业微信不在本轮范围内。

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

### C2：Platform Connector（飞书 + 个人微信已进入生产运行时）

当前已完成：

- 飞书明文/加密 webhook、verification token、签名、timestamp、防回声和事件标准化；
- 飞书 tenant token 缓存、文本/富文本通知发送、reply 关联和业务错误映射；
- 个人微信 iLink QR 登录协议、durable cursor、长轮询、上下文回复和 4000 字分段；
- 微信 `context_token` 与媒体引用加密落库，Bot token 不落数据库；
- 平台网络/429/5xx 类型化错误；不确定外部成功进入人工确认死信，不自动重发；
- 入站文本、post 和附件元数据标准化。

尚未闭合：飞书/微信二进制附件下载、上传并登记 Group Artifact 的端到端链路；该能力不得因“附件 metadata 已存在”而标记完成。

Connector 不允许直接调用 `dispatch_bots()`、Workflow Runner 或 Group DB。

验收：Connector 可以使用 fake transport 完成入站/出站契约测试。

### C3：Channel-Group Binding（Gate 4）

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

### C4：Integration Member（Gate 4）

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

### C5：Inbound Channel → Group/Bot（Gate 4）

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

外部会话只有与 active Binding 的 tenant/conversation 精确匹配时，才能以无工具权限的 Integration Member 进入 Group；Group Owner API 是唯一正式配置/审批入口。附件 Artifact 化仍是后续工作。

验收：外部消息只触发绑定的 Group 和允许的 Bot，不能跨 Group。

### C6：Group/Bot → Channel Outbound（Gate 4：failure-tested）

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
 → group_channel_projection_queue（同一 Group 事务，持久化补偿状态）
 → group_channel_event_outbox（同一 Group 事务）
 → Supervisor GroupChannelRelayService
 → channel_delivery_outbox
 → 异步 Dispatcher
 → 注册到 Supervisor 的飞书/个人微信 Connector.send()
 → DeliveryReceipt
```

任务开始时应保存 Binding 快照，防止执行期间管理员更换默认 Bot 或目标会话后，结果投递到错误位置。

验收：外部平台失败不回滚 Group 任务；重试幂等；成功发送能关联外部消息 ID 和内部 Event/Session/Artifact。

### C7：安全、审计和运维（Gate 4：failure-tested）

- Channel Secret 只能保存引用，不进 Group DB 或事件 Payload。
- 外发前执行脱敏、长度限制和 Artifact 权限检查。
- Channel 只看得到绑定 Group 的数据。
- 所有绑定、暂停、撤销、路由和投递动作进入审计时间线。
- 增加 dead-letter 队列、人工重放和暂停发送能力。
- 为每个 Channel/Binding 提供健康状态、最后成功投递时间和失败计数。
- 外部平台重复事件、乱序事件和过期事件必须可处理。

### C8：运行时与部署边界（Gate 4：failure-tested）

当前产品是面向小公司/独立项目组的单服务部署。飞书与个人微信 Connector 运行在 Supervisor 进程，由 `NUKE_CHANNEL_PLATFORMS_JSON` 声明，Secret 只通过命名环境变量解析；active Binding 没有对应 Connector 时启动或审批失败。Dockerfile 与三套 Compose 均包含依赖和配置入口。独立 JSONL Process Server/Client 仍保留给未来外置 Connector，但 OS/container 沙箱不是当前单服务版本的必要条件。

配置示例（Secret 值继续由显式 Resolver 从命名环境变量读取）：

```json
[{"type":"feishu","channel_instance_id":"feishu:prod","app_id_env":"FEISHU_APP_ID","app_secret_env":"FEISHU_APP_SECRET","verification_token_env":"FEISHU_VERIFY_TOKEN","encrypt_key_env":"FEISHU_ENCRYPT_KEY"},{"type":"wechat_ilink","channel_instance_id":"wechat:personal","bot_id_env":"WECHAT_ILINK_BOT_ID","bot_token_env":"WECHAT_ILINK_BOT_TOKEN"}]
```

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

基础模块与双平台本地 E2E 已完成。下一阶段不是继续增加平台，而是使用真实测试账号执行 [飞书与个人微信部署验收](../../channels-feishu-wechat-deployment.md)，补齐附件 Artifact、平台真实限流观察、告警阈值和值班演练。完成真实账号 smoke 前保持 Gate 4，不得改为 Gate 5。

这样后续即使替换平台，Group、Bot、Workflow 和 Artifact 的内部模型也不需要重写。
