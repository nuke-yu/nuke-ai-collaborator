# 核心身份与关联清单

更新时间：2026-08-09

本文档以当前代码中的 `backend/db/schema_split.py`、
`backend/memory/infrastructure/schema.py`、Session/Artifact/Observability
实现为准，记录执行链路中核心对象的主键、作用域、存储位置和关联规则。
它是后续 Capability Manifest、Provider Registry、Workflow/Replay 设计的
身份契约，不是新的运行时 schema。

## 1. 存储边界

| 存储域 | 主要内容 | 关系约束方式 |
| --- | --- | --- |
| central DB | `users`、`groups`、`group_memberships`、`members`、`permission_rules`、Bot skills 等 | 同一 SQLite 文件内使用 FK；跨 group DB 的引用只能由应用层校验 |
| group DB | 消息、Session、Session Event、Artifact、Workflow、Observability、Group Memory 等 | 组内 FK 可由 SQLite 保证；所有查询必须附带当前 `group_id` 或使用组 DB 路由 |
| Memory bounded context | `memory_records`、`memory_relations`、`skills`、`skill_versions`、投影 outbox 等 | 物理上属于 group DB；以 `MemoryScope.group_id` 和 ACL 作为入口约束 |
| personal scope | 用户个人记忆及其导出/重建流程 | 不得隐式投影进 Group；必须经过显式授权和投影用例 |

`group_id` 是租户隔离边界，不是一个可以由请求体自由替换的过滤字段。
请求进入 API、Worker 或 Memory 服务后，应先确定可信的 group scope，再打开
对应数据库。

## 2. 核心身份表

| 对象 | 当前规范身份 | 所属域 | 备注 |
| --- | --- | --- | --- |
| User | `users.id`（正整数） | central | 用户认证与 Membership 的主体 |
| Group | `groups.id`（正整数） | central | 组的租户身份；对应 `group_{id}/chat.db` |
| Member / Bot | `members.id`（正整数）+ `members.group_id` | central | Bot 是 `type='bot'` 的 Member；`bot_id` 在 group DB 中是跨库逻辑引用 |
| Permission rule | `permission_rules.id` | central | 通过 `bot_id` 归属 Bot；工具和参数匹配规则不是执行身份 |
| Session | `agent_sessions.id`（文本 ID）+ `group_id` | group DB | 一次 Agent 执行；`bot_id`、`group_id` 指向 central 的逻辑身份 |
| Session Event | `session_events.id`（自增整数） | group DB | Session 内不可变事件序列；通过 `session_id` 组内 FK 关联 Session |
| Evidence Link | `session_evidence_links.id`（自增整数） | group DB | Event 到 Memory/Skill 的持久化边；`evidence_ref` 是稳定引用，不是自由文本正文 |
| Model Request | `model_usage_ledger.request_id`（文本 ID） | group DB | 一次模型请求；由 `(session_id, request_ordinal)` 保证 Session 内幂等排序 |
| Artifact | `group_artifacts.artifact_id`（文本 ID） | group DB | 逻辑产物身份；`storage_locator` 只是存储位置，不能作为主键 |
| Workflow State | `workflow_state.group_id` | group DB | 当前组唯一的 Workflow 状态行 |
| Workflow Observation | `workflow_observations.observation_id`（文本 ID） | group DB | Workflow 事件身份；`id` 只用于本库排序和归档关联 |
| Observation Artifact | `observation_artifacts.artifact_id`（文本 ID） | group DB | Observability 载荷附件，和 `group_artifacts` 是两个当前独立命名空间 |
| Memory Record | `memory_records.record_id`（文本 ID）+ `group_id` | group DB | Group/Bot 记忆的规范身份；内容、状态、证据和版本字段在记录内 |
| Memory Relation | `memory_relations.relation_id`（文本 ID） | group DB | 通过 `from_record_id`、`to_record_id` 连接同组 Memory Record |
| Skill | `skills.skill_id` + `(group_id, bot_id, name)` | group DB | 当前版本由 `current_version` 指示 |
| Skill Version | `(skill_id, version)` | group DB | 内容身份由 `content_hash` 辅助确定；Skill 使用记录通过 `skill_usage` 关联 |

## 3. 主要关系

```text
User ──< GroupMembership >── Group ──< Member/Bot
                                  │          │
                                  │          └──< PermissionRule
                                  │
                                  ├──< Session ──< SessionEvent ──< EvidenceLink
                                  │       │             │
                                  │       │             └──> MemoryRecord / SkillVersion (logical ref)
                                  │       ├──< ModelRequest
                                  │       └──> Artifact (logical session_id)
                                  │
                                  ├── WorkflowState
                                  ├──< WorkflowObservation ──< ObservationArtifact (logical event_id)
                                  └──< MemoryRecord ──< MemoryRelation
```

具体规则：

1. `Group -> Member`、`User -> GroupMembership -> Group`、`PermissionRule -> Member`
   是 central DB 内部关系，当前 schema 有 FK（删除策略以实际 DDL 为准）。
2. `SessionEvent -> Session`、`EvidenceLink -> SessionEvent/Session`、
   `ModelRequest -> Session/Event` 是 group DB 内部关系，并支持级联清理。
3. `Session.bot_id -> central.members.id`、`Session.group_id -> central.groups.id`、
   `Artifact.bot_id/group_id`、`Workflow*.group_id`、Memory 的 `bot_id/group_id`
   都是跨域逻辑引用；SQLite 不跨文件强制 FK，必须在路由、服务层和查询条件中校验。
4. `session_evidence_links.evidence_ref` 当前只保存规范化字符串，例如
   `memory:<record_id>` 或 `skill:<skill_id>@v<version>`；它不替代正式的
   `MemoryRecord`/`SkillVersion` 主键，也不允许把记忆正文塞进事件边。
5. `group_artifacts` 和 `observation_artifacts` 当前是不同的表和生命周期。
   不能仅因为两者都有 `artifact_id` 就假设它们可以互相 JOIN；需要跨表展示时，
   必须显式标明来源命名空间。
6. `workflow_observations.session_id`、`event_id` 等字段目前是关联字段而非
   SQLite FK；查询必须同时带 `group_id`，防止把其他组的 ID 当作本组关联。

## 4. 对执行链路的约束

### 4.1 Session 身份冻结

Session 创建时至少要能确定：`session_id`、`group_id`、`bot_id`、`executor_id`
以及后续要落地的 capability manifest 身份。执行过程中的配置变更不应覆盖
已开始 Session 的历史语义；运行快照（`last_snapshot_json`）描述执行进度，
不是能力版本清单。

### 4.2 Memory 与 Evidence 的边界

Memory 查询和写入必须携带 `MemoryScope`；Bot/Group 操作必须有 `group_id`，
Personal 操作必须有 `user_id`。Evidence Link 只承担“本次执行使用/引用了哪条
证据”的可追溯关系，正文仍由 Memory/Skill 所属 bounded context 管理。

### 4.3 删除、归档和重放

- 删除 Session 时，组内 Event、Evidence Link、Model Usage 等 FK 关联记录可按
  当前级联策略处理；Artifact 和 Workflow 产物是否保留由其自身留存策略决定。
- 归档记录中的 `source`、`source_row_id`、`event_id` 必须保留来源命名空间，不能
  只保存一个无上下文的整数 ID。
- Replay 或 Timeline 读取必须以 `group_id` 作为第一层过滤，再使用 session、
  workflow、artifact 等二级关联；任何“只按 ID 查”的接口都视为隔离风险。

## 5. 当前缺口与后续实现影响

1. 跨 central/group DB 的逻辑 FK 没有统一的运行时校验器；后续应在统一的
   group scope/router 层收敛校验，而不是让每个业务查询自行猜测。
2. `agent_sessions.config_json` 和 `last_snapshot_json` 还没有 Capability
   Manifest 的版本、哈希和组成项；Manifest 应作为独立的“执行能力身份”补齐，
   不应把它混入 snapshot。
3. Artifact 有两个当前命名空间，后续若要统一展示，应增加明确的 artifact
   kind/source，而不是复用同名 ID 推断类型。
4. Workflow observation 与 Session Event 目前通过文本/逻辑字段关联；若后续要求
   强一致 replay，应定义统一的 correlation contract 和完整性检查。
5. Permission rule 是 central 配置，执行期的允许/拒绝结果仍应记录在 Session
   Event 或对应审计事件中；不能用当前 rule 表反推历史执行结果。

## 6. 后续任务的直接输入

本清单完成后，Capability Manifest 至少应能引用以下稳定身份：

```json
{
  "session_id": "<agent_sessions.id>",
  "group_id": 1,
  "bot_id": 2,
  "executor": {"id": "tool_loop_v1", "version": "..."},
  "providers": [],
  "skills": [],
  "permissions": [],
  "sandbox": {},
  "manifest_version": 1,
  "manifest_hash": "sha256:..."
}
```

其中 `session_id/group_id/bot_id` 是执行归属，Manifest 中的 provider、skill、
permission、sandbox 是执行能力版本；两者都不能由 Timeline snapshot 代替。
