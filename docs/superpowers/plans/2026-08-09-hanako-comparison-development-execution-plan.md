# Nuke AI / Hanako 对比后的开发执行计划

> 基线：2026-08-09；任务状态以本文件所在提交及其父提交为准。
>
> 关联评审：[Hanako-comparison-2026-08-02.md](../../Hanako-comparison-2026-08-02.md)
>
> 总路线与 Channel 专项计划：[2026-08-09-complete-development-roadmap.md](2026-08-09-complete-development-roadmap.md)

## 1. 执行目标

将当前 Nuke AI 从“可恢复的 Group 协作系统”继续推进为“能力可复现、事件可恢复、资源可审计、记忆可撤回”的 AI 团队执行平台。

执行原则：

- 先修复当前代码和测试基线，再新增基础设施。
- 先建立稳定身份和协议，再扩展 Artifact、渠道和插件生态。
- 冻结 Bot 的执行能力，不冻结 Group 的协作现实。
- 所有跨进程数据通过结构化 IPC，不跨进程拼接原始指标文本。
- 所有外部副作用在 SQLite commit 成功后执行。
- 所有进入存储、事件、Trace 和模型上下文的内容先经过脱敏和长度限制。
- 每个功能点形成可独立检出的原子 Commit，并先跑直接相关测试。

## 2. 当前基线

### 已完成（代码已交付）

- Group / Worker / MCP Collector 隔离。
- Workflow State、Session WAL、Snapshot、Parent Session、Recovery。
- Unified Group Timeline 和单 Session Execution Timeline Drawer。
- Artifact Registry、上传登记、`write_file` 登记。
- Event Policy、Payload Policy、Evidence Links、Model Usage Ledger。
- Retention、OTel、Prometheus 导出闭环。
- Personal Vault 后端、Scoped Projection 和基础管理 UI。
- Electron、Onboarding、Theme 相关代码已进入提交历史。
- Capability Manifest / Execution Identity：Provider/Model Descriptor、Manifest 持久化和 canonical hash。
- 业务 WebSocket Envelope、Replay Cursor、Catch-up 和客户端事件去重。
- Timeline 执行详情、Group SQL 隔离和敏感输出脱敏。
- Artifact 生命周期元数据：版本、派生关系、软删除和生命周期状态。
- Personal Memory 使用来源与 Session 审计记录。
- Provider Registry、能力校验、废弃/替换和预算治理。
- 通用签名 Webhook Channel Adapter 基础层。
- 单插件 JSONL IPC 隔离试点，包含超时、取消、输出上限和脱敏。
- Store Registry 可执行元数据注册表。

### 仍缺失（后续治理或生产接入）

- 真实飞书、Slack 或企业微信渠道的端到端接入。
- Plugin IPC 试点向全部高风险 Executor 的迁移，以及正式崩溃恢复闭环。
- Store Registry 的 migration、owner、retention、deletion 和灾备治理。
- Personal Memory 撤回后的影响展示和产品化操作界面。
- Timeline 恢复/重试入口、性能优化和用户/管理员视图分层。
- Artifact Workflow 交付接入和物理存储清理执行器。

### 当前测试基线（2026-08-09）

后端全量测试为 `2493 passed, 2 skipped, 57 warnings, 40 subtests passed`。
健康检查测试已改为使用自有临时数据库，不再依赖前序测试留下的全局 `DB_PATH`。
剩余 warning 主要来自第三方依赖和既有弃用 API，不影响本次测试通过结果。

## 3. 阶段路线

```text
Phase 0  基线修复（已完成）
    ↓
Phase 1  Provider Identity + Capability Manifest（已完成基础交付）
    ↓
Phase 2  WebSocket Event Contract + Timeline Hardening（基础交付已完成，产品化加固待完善）
    ↓
Phase 3  Artifact Lifecycle（生命周期治理已完成，Workflow/物理清理待完善）
    ↓
Phase 4  Memory Provenance / Personal Vault Audit（影响审计已完成）
    ↓
Phase 5  Provider Governance（基础治理已完成）
    ↓
Phase 6  Channel Adapter（C0–C8 基础模块已完成，当前 Gate 2–4，运行时 wiring 尚未完成）
    ↓
Phase 7  Plugin Process Isolation（IPC 治理已完成，全面迁移待完善）
```

## 4. Phase 0：稳定当前基线

### 工作项

1. 修复 Timeline Projector 的两个旧测试。
2. 增加最近 Timeline 能力的测试：Tool arguments、Console output、Thinking、Artifact、失败状态。
3. 验证 Timeline API 的 Group membership 和 SQL 层隔离。
4. 验证上传文件和 `write_file` 的 Artifact 自动登记。
5. 建立 Session、Workflow、Artifact、Memory、Permission 的主键和关联清单。

### 验收

- 直接相关 Timeline / Artifact / Observability 测试全部通过。
- `git diff --check` 通过。
- 文档中的 CURRENT / TRANSITION / TARGET 与代码一致。

### Commit

```text
test(timeline): align projector tests with group database routing
```

## 5. Phase 1：Provider Identity 与 Capability Manifest

### 5.1 最小 Provider Descriptor

建立轻量 Provider Registry，统一 `ai/client.py`、`ai/model_limits.py`、`ai/pricing.py` 和 `AIService` 的 Provider / Model 身份解析。

Descriptor 至少包含：

```python
ProviderDescriptor(
    provider_id,
    model_id,
    context_window,
    max_output_tokens,
    supports_tools,
    supports_vision,
    supports_thinking,
    pricing_version,
)
```

### 5.2 Capability Manifest

在 `agent_sessions` 增加：

```text
manifest_json
manifest_hash
manifest_version
```

Manifest 固定：

- Provider / Model；
- Executor 版本；
- Prompt / Trait / Skill 版本或内容 Hash；
- Tool Schema Hash；
- Permission Rules Hash；
- Sandbox Policy；
- Memory Revision。

Manifest 不保存 Prompt、Token、Secret 或无界原文。

Group 最新消息、共享文件、最新 Bot 交付和当前有效事实继续动态读取，不进入能力快照。

### 验收

- 新 Session 必须生成 Manifest。
- Skill、Tool Schema、Permission 变化可以通过 Hash 检测。
- Retry 事件记录 `manifest_hash`。
- Resume 可以区分能力变化和 Group 上下文变化。
- 实际 Provider / Model 与 Model Usage Ledger 一致。

### Commit

```text
feat(provider): add canonical provider model descriptors
feat(session): persist execution capability manifest
```

### 测试

```bash
python3 -m pytest tests/test_model_usage_ledger.py tests/test_sessions.py -q
```

## 6. Phase 2：Event Contract 与 Timeline Hardening

### 6.1 WebSocket Envelope

先统一最小 Envelope，不一次性创建大量事件 Schema：

```json
{
  "protocol_version": 1,
  "event_id": "evt_...",
  "event_type": "tool_result",
  "occurred_at": "...",
  "group_id": 1,
  "session_id": "...",
  "workflow_id": "...",
  "request_id": "...",
  "payload": {}
}
```

涉及：

- `backend/runtime/ipc/protocol.py`；
- WebSocket dispatch / broadcast；
- `frontend/src/wsrpc.js`；
- `frontend/src/store/chatStore.js`。

### 6.2 Reconnect / Catch-up

定义客户端最后收到的 Event ID / Cursor，重新连接后服务端补发缺失事件，客户端按 Event ID 去重。

必须覆盖：

- 网络断开；
- Worker 重启；
- Supervisor 重启；
- 重复事件；
- 旧客户端无 Cursor；
- 未知事件类型。

### 6.3 Timeline 加固

- 普通用户视图与管理员审计视图分层；
- 失败节点关联恢复 / 重试入口；
- Tool 输出有长度限制并保持脱敏；
- Diagnostic 事件默认不进入业务视图；
- Timeline 与 Artifact、Memory、Skill、Permission 稳定下钻；
- 大量 Diagnostic 事件查询和分页保持稳定。

### 验收

- 重连不丢事件、不重复事件。
- 非成员无法读取其他 Group Timeline。
- Session 失败后可以从 Timeline 定位恢复入口。
- 旧客户端仍能接收基础事件。

### Commit

```text
feat(protocol): add versioned websocket event envelope
feat(protocol): add reconnect cursor and event deduplication
feat(timeline): harden execution timeline recovery and access views
```

## 7. Phase 3：Artifact Lifecycle

当前已有 Artifact Registry，本阶段不重新引入 Artifact ID，而是补齐：

```text
artifact_version
parent_artifact_id
derives_from
created_by
deleted_at
lifecycle_status
```

统一接入：

- 上传文件；
- Workspace 文件；
- Tool 输出；
- Workflow Deliverable；
- Coding Agent 输出；
- 未来 Connector 附件。

必须明确：

- 删除登记是否删除物理文件；
- 文件移动后的 locator 语义；
- 多 Workflow 引用时的删除规则；
- 跨 Bot 交接是否需要显式 grant；
- Artifact 是否允许跨 Group。

### 验收

- 所有 Workflow Deliverable 都有 Artifact ID。
- Artifact 可追踪来源 Session、Bot、Workflow。
- Artifact 版本关系可查询。
- 删除和撤销权限语义明确。
- 跨 Group 查询失败。

### Commit

```text
feat(artifacts): add artifact lifecycle and derivation metadata
```

### 测试

```bash
python3 -m pytest tests/test_artifacts.py tests/test_workflow.py -q
```

## 8. Phase 4：Memory Provenance 与 Personal Vault Audit

当前已有 Personal Vault、Scoped Projection、Session Evidence Links。本阶段补充使用审计：

```text
memory_usage_events
├── user_id
├── record_id
├── projection_id
├── group_id
├── bot_id
├── session_id
├── purpose
└── used_at
```

### 工作项

1. 展示记忆来源、置信度和授权范围。
2. 记录哪个 Group / Bot / Session 使用过某条个人记忆。
3. 展示撤回 Projection 后的影响范围。
4. 区分用户声明、系统观察、模型推断和用户确认事实。
5. 保持跨 Group 使用必须经过 Scoped Projection。

### 验收

- 用户可以查看一条记忆的来源。
- 用户可以查看授权的 Group / Bot。
- 用户可以查看使用该记忆的 Session。
- 删除或撤回后，未来执行不会继续注入。
- 不允许默认全局注入。

### Commit

```text
feat(memory): add personal memory usage provenance
```

### 测试

```bash
python3 -m pytest tests/test_authorized_personal_memory.py tests/test_memory_refs.py tests/test_personal_memory_api.py -q
```

## 9. Phase 5：Provider Governance

在 Phase 1 的 Descriptor 基础上补充：

- Tool Calling；
- Vision；
- Thinking；
- Context Window；
- Token 参数映射；
- OAuth / API Key；
- Pricing；
- Model Deprecated / Replacement；
- Fallback；
- Cost Budget / Quota。

### 验收

- Provider 差异不再由各执行路径自行判断。
- Ledger 的 Provider、Model、Pricing Version 可追溯。
- 模型废弃时有明确错误或替换策略。
- 预算限制在 Worker 侧生效。

### Commit

```text
feat(provider): add model capability and budget governance
```

## 10. Phase 6：Channel Adapter

Channel 必须保持独立模块，只有通过显式 Channel-Group Bridge 和 active Binding 后，才能作为 Group Integration Member 双向通信。完整 C0-C8 路线见：[Channel 专项计划](2026-08-09-complete-development-roadmap.md)。

第一版先选择一个渠道完成端到端验证，必须先解决外部租户、外部用户到 Group/Member 的映射、消息幂等、mention、回复关联、附件 Artifact 化、权限继承和 Group commit 后的 Delivery Outbox。

统一入口：

```text
InboundEnvelope
→ identity mapping
→ Group authorization
→ mention / trigger selection
→ existing dispatch
→ workflow
→ Group broadcast / connector reply
```

必须支持：

- 外部租户；
- 外部用户；
- Group / Member 映射；
- mention；
- reply context；
- message idempotency；
- Artifact 附件；
- 权限继承；
- 失败重试。

### 验收

- 同一外部消息重复投递不会重复创建 Session。
- 外部用户不能访问未授权 Group。
- 附件进入 Artifact。
- Bot 回复能关联原消息。
- 外部渠道与 Web UI 的执行结果一致。

### Commit

```text
feat(channel): add first external channel adapter
```

## 11. Phase 7：Plugin Process Isolation

不要一次迁移全部插件，先选择一个高风险 Executor 或 Coding Agent 做独立进程试点：

```text
Worker
  → Plugin IPC Client
      → Plugin Process
          → restricted capability
```

需要定义：

- Plugin Manifest；
- Plugin Capability；
- IPC Request / Response；
- timeout；
- cancellation；
- resource quota；
- filesystem / network scope；
- crash recovery；
- version compatibility。

### 验收

- 插件崩溃不影响 Worker。
- Worker 可以取消插件调用。
- 插件只能访问声明资源。
- 插件不能绕过 HIL。
- 插件结果经过统一 redaction。
- 插件版本进入 Capability Manifest。

### Commit

```text
feat(plugins): isolate one executor behind process IPC
```

## 12. Git 与测试执行策略

每个功能点形成独立 Commit，Commit 之间保持可独立检出、独立测试。

推荐提交顺序：

```text
1. test(timeline): align projector tests with group database routing
2. feat(provider): add canonical provider model descriptors
3. feat(session): persist execution capability manifest
4. feat(protocol): add versioned websocket event envelope
5. feat(protocol): add reconnect cursor and event deduplication
6. feat(timeline): harden execution timeline recovery and access views
7. feat(artifacts): add artifact lifecycle and derivation metadata
8. feat(memory): add personal memory usage provenance
9. feat(provider): add model capability and budget governance
10. feat(channel): add first external channel adapter
11. feat(plugins): isolate one executor behind process IPC
12. feat(store): add executable store registry
```

后端每完成一个功能点，只运行直接相关测试：

```bash
python3 -m pytest <直接相关测试> -q
```

每个 Phase 结束时执行：

```bash
python3 -m pytest
npm run build
git diff --check
```

## 13. 当前优先级

| 优先级 | 项目 | 当前动作 |
|---|---|---|
| P0 | Capability Manifest / Execution Identity | 新建基础设施 |
| P0 | 最小 WS Event Envelope / Catch-up | 新建基础设施 |
| P1 | Timeline Hardening | 完成恢复/重试、用户/管理员视图和性能加固 |
| P1 | Artifact Lifecycle Governance | 接入 Workflow 交付和物理文件清理执行器 |
| P1 | Personal Memory Usage Audit | 维护影响审计和撤回后的产品操作 |
| P1 | 最小 Provider Registry | 为 Manifest、Ledger 和成本治理提供稳定身份 |
| P1/P2 | Store Registry | 已完成可执行治理元数据；后续接入 migration/retention 执行 |
| P1/P2 | Channel Adapter | 取决于企业渠道战略 |
| P2 | Plugin Process Isolation | 已完成 IPC 治理试点；后续迁移全部高风险 Executor |
| CURRENT | Observability 闭环 | 转入维护、性能和安全加固 |
