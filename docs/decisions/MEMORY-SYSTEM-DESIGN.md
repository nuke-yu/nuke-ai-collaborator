# Memory & Learning 系统设计

> 状态：唯一权威设计文档；同时记录当前实现、目标架构与分阶段验收
> 初版日期：2026-07-20；合并日期：2026-07-21
> 适用范围：Nuke AI Collaborator 的 Group Memory、Bot Learning、Personal Knowledge 与运行时上下文装配

## 1. 文档定位

本文是 Memory & Learning 的唯一实现依据。它不是对现有 `ai/memory.py` 的局部增强方案，而是将 Memory & Learning 建设为 Nuke AI Collaborator 的独立产品领域。

目标是把系统从以 Code Agent 为主的群组协作平台，逐步升级为：

> 一个以 Group 隔离为基础，能够理解项目、理解人、积累 Bot 执行经验、复用历史经验，并将经过真实执行验证的经验沉淀为技能的工业级 AI 协作平台。

本文使用以下状态标记避免混淆：

```text
[CURRENT]      当前代码已经实现，修改时必须理解和兼容
[TARGET]       最终目标设计，是新增实现的权威要求
[TRANSITION]   从 CURRENT 迁移到 TARGET 的阶段性方案
[DEFERRED]     已讨论但暂不实施
```

当 CURRENT 与 TARGET 不一致时：新行为和验收以 TARGET 为准，迁移过程必须保护现有数据、Group 隔离、ToolRouter/MCP 路由和安全不变量。一个阶段完成后，应在本文中把对应条目从 TARGET/TRANSITION 更新为 CURRENT，不再维护第二份“现状文档”。

### 1.1 当前实现基线 [CURRENT]

#### 2026-07-21 实施增量 [CURRENT]

以下目标链路已经进入生产代码，不再只是 TARGET：

- Group 私库中的 `agent_runs`、`agent_cases`、`memory_records`、`pipeline_jobs`；
- 稳定 `run_id / step_id / attempt_id` 与结构化 `run_decisions`，不保存原始 CoT；
- Run 终态确定性生成 Case，Outcome Evaluator 仅对高信息增益 Case 继续处理；
- 失败后修正成功的 Case 生成 Experience，普通成功跳过蒸馏；
- Experience 按 Group/Bot 隔离，采用词项与 Chroma vector 融合召回，固定 Top-K 和上下文预算；
- Experience 的强化、反证、暂停、衰减和使用成本追踪；
- durable/idempotent Case pipeline job，包含 lease、重试上限和 dead 状态；
- 受预算约束、最多一次的 Execution Reflexion，并保留结构化 Decision Trace；
- canonical `skills / skill_versions / skill_usage`；
- 仅允许 S0/S1 声明式 Learned Skill，禁止 shell、任意代码和权限旁路；
- Trial → Active → Stable 的执行结果晋升，以及失败暂停；
- canonical Skill 到 Bot workspace 的受信任、可重建投影。
- 每用户独立 Personal Knowledge SQLite Vault，物理路径不位于任何 Group；
- Personal Record 的来源、说话者、观点主体、authority、敏感度和显式/观察状态；
- Personal → Group/Bot/Purpose 的显式 Scoped Projection，secret 禁止投影；
- 登录身份经 WebSocket → IPC → WorkUnit → ExecutionContext 可信传播，禁止从 member_id 猜测用户；
- 仅在存在授权 Projection 时注入 Personal Context，并受固定字符预算限制；
- Habit 的多独立样本、跨场景、14 天跨度和反例门控；
- Personal Vault 的来源 intake、导出、删除、过期清理与重建 API。

#### 2026-07-28 证据语义增量 [CURRENT]

- Experience/Skill usage 已升级为
  `injected → adopted → executed → verified_success | verified_failure`
  单向状态机，旧 finalization 仅保留 shadow telemetry；
- 只有 Decision Trace、匹配行动和 Outcome Adapter 组成完整因果证据链后，
  才能强化、反驳 Experience 或改变 Skill 成熟度；
- 已实现 Shell exit、Pytest、Build、Lint、File change、API response 和
  Workflow state 的确定性 adapter；普通工具成功不再等同于任务成功；
- Case 持久化 `outcome_status`、验证 adapter 和 correction evidence；
- `corrected_success` 必须满足同一验证目标失败、发生不同纠正动作、同目标重试成功、
  Run 最终完成四项条件；只有这种 Case 可以蒸馏 Experience；
- 已提供 Group 内只读 shadow metrics，用于比较“旧规则会强化”与“新规则有因果证据”
  的数量差异。

#### 2026-07-28 Case / Experience 结构化增量 [CURRENT]

- Case 使用独立 `agent_case_attempts` 保存有序 Attempt Trace，包含稳定 step/attempt ID、
  phase、action target、短 observation、verifier 与结果状态；不保存原始 CoT；
- Experience 已升级为 `experience-v2`，包含 environment/failure signature、可观察纠正动作、
  验证证据、限制和 source Case IDs；未被证据确认的 root cause 明确标记为 unresolved；
- Task 同时保留 exact signature 与结构化 semantic cluster key，并保存 task family、
  concepts 和 file extensions；
- Experience 合并要求 Group/Bot、semantic cluster、environment signature 和 failure
  signature 同时匹配；召回在 lexical/vector 之外增加有界的 structured-cluster 信号。

仍属于 TARGET 的主要内容包括 Gmail/Outlook、日历和任务系统等具体 Connector、Personal Memory 管理 UI、观点演变的高级关系建模，以及 S2/S3 安全基础设施。Capability Registry、可信验证和完整 Evaluation Harness 按已确认决策继续后置。

当前系统已经具备可插拔的 `MemoryProvider` 接缝：

```text
recall
    turn 前组装并注入记忆上下文

observe
    turn 后并发触发事实抽取、摘要、反思和工具事件压缩

forget
    删除指定 Bot 在 Group 内的记忆
```

默认实现为 `ChromaMemoryProvider`，显式关闭时使用 `NullMemoryProvider`。Tool loop 只依赖 Provider 协议，不直接感知内部 facts、summaries、reflections 和 tool episodes。

当前数据与链路：

| 当前能力 | 存储/实现 | 当前语义 |
|---|---|---|
| 原子事实 | Chroma `mem_type=fact` | 从 Bot 最终回复抽取，带 importance、timestamp、bot/group/thread |
| 高阶反思 | Chroma `mem_type=reflection` | 按水位线从多条 Fact 归纳，带 source IDs |
| 工具经验 | SQLite `tool_events` + Chroma `tool_episode` | 工具事件确定性记录，达到条数门槛后批量压缩 |
| 会话摘要 | Group SQLite `role_summaries` | 按 Bot、Group、Thread 保存和召回 |
| 反思进度 | Group SQLite `reflection_state` | 按 Bot/Thread 水位线增量处理 |
| 原始工具检索 | SQLite FTS5 | `search → timeline → fetch` 三层协议 |
| 长期语义检索 | Chroma | `bot_id + group_id` 前置过滤后进行向量召回和精排 |
| Bot 可读记忆 | workspace `MEMORY.md` | Bot 私有启动文件，目前不是 canonical memory store |

当前召回精排包含：

```text
0.5 × semantic similarity
+ 0.3 × recency
+ 0.2 × importance
+ keyword boost
+ reflection bonus
+ thread affinity bonus
```

当前安全与隔离不变量：

- 生产 Group 关系数据落每 Group 独立 SQLite DB；
- Chroma 使用 `group_id + bot_id` 强制过滤；
- 写入长期记忆前执行 secret redaction；
- 召回、写入和 Chroma 库级异常 fail-soft，不阻断 Bot 前台回复；
- schema/migration 缺口必须显著记录，不能被普通 fail-soft 静默吞掉；
- builtin/skill/shell 继续走 `tool_executor.execute()` 的权限和危险命令守卫；
- MCP 连接只存在于 Collector，Worker 仅通过 Proxy/Bridge 调用；
- Learned Skill 不得成为上述路由和权限体系的执行旁路。

### 1.2 当前实现与目标设计的迁移差异 [TRANSITION]

| 当前实现 | 目标设计 | 迁移要求 |
|---|---|---|
| Chroma 是 Fact/Reflection 的主要记录 | SQLite canonical，Chroma 是可重建索引 | 先建 canonical record 和回填，再切换读写 |
| 冲突 Fact 被物理删除 | ADD-only + temporal supersede | 保留旧事实、来源、有效时间和替代关系 |
| `observe()` 内多条临时后台协程 | 持久化 Pipeline Job | 前台仍 fail-soft，后台支持幂等、恢复和 dead-letter |
| 工具事件按数量压缩 | durable Run → Case → Experience | 先补稳定 run/step/attempt identity |
| `maybe_reflect()` 只从 Fact 归纳 | Execution Reflexion + Consolidation Reflection | 两类反思分别建模，不共用模糊状态 |
| Skill 主要是 workspace 文件 | canonical Skill/Version + workspace 投影 | 文件由受信任 Compiler 生成，可重建和回滚 |
| 仅 Group/Bot 记忆 | 增加独立 Personal Knowledge Vault | Person 数据不落 Group DB，通过 Scoped Projection 使用 |

迁移期间不允许直接删除存量 Chroma、`role_summaries`、`reflection_state` 或 `tool_events`。任何切换必须具备回填统计、dry-run、幂等重跑、隔离验证和回滚路径。

## 2. 产品目标

### 2.1 理解项目

由 Group Memory 承担：

- 项目事实、需求与当前状态；
- 架构决策、API 契约和数据模型；
- 公共原则、安全规则和工程约束；
- 事实的证据、历史版本及有效时间。

### 2.2 理解人

由 Personal Knowledge Vault 承担：

- 用户的专业知识和长期目标；
- 稳定偏好、判断方式和工作方法；
- 沟通风格、联系人和组织关系；
- 当前承诺、工作重点及其时间有效性。

### 2.3 让 Bot 持续成长

由 Bot Learning 承担：

- 个体执行经历；
- 成功和失败经验；
- 可供相似任务参考的历史解决路径；
- 反思、教训和方法边界；
- 实验性技能及经过真实复用验证的稳定技能。

运行时最终组合：

```text
Group 当前现实
+ Person 的知识与工作方式
+ Bot 的历史经验
+ Bot 已验证的技能
→ 当前任务的理解、计划与执行
```

## 3. 核心设计原则

### 3.1 所有权明确

```text
Person
    人的知识、偏好、判断和工作模型

Group
    项目原则、公共事实、决策和约束

Bot
    个体知识、执行经验、反思和技能

Role
    后续承接经验证的岗位通用技能
```

新执行经验默认属于执行它的 Bot。原始经验不会因为形成高层产物而迁移或删除；晋升应创建带来源关系的新产物。

### 3.2 Group 保持物理隔离

- 每个 Group 继续使用独立 SQLite DB；
- Group 之间不直接共享记忆、经验或技能；
- Bot 记忆和技能继续位于所属 Group 内；
- Person 数据不写入任意 Group DB；
- Person 数据通过受权限、目的和敏感度约束的 Scoped Projection 提供给指定 Group/Bot。

### 3.3 原始证据 ADD-only

消息、工具事件、测试结果和任务轨迹原则上只追加，不允许 LLM 物理覆盖或删除。

派生事实通过状态和时间演化：

```text
active → superseded → expired/deprecated
```

除合规删除外，应保留来源、历史和失效原因。

### 3.4 真相源与索引分离

```text
Per-Group / Per-Person SQLite
    canonical records

FTS5
    精确检索与 BM25

Chroma
    派生语义索引，可重建

Markdown / Skill Files
    人与 Agent 可读的运行时投影
```

Chroma 不再承担复杂记忆状态的唯一真相源。

### 3.5 人工不是学习热路径依赖

任务和经验验证主要依赖：

- 工具退出状态；
- 测试、build、lint 和 API 验证；
- workflow 和 durable run 状态；
- 文件或数据库实际变化；
- 下游是否成功消费；
- 错误、重试和回滚。

人的明确纠正是高权威证据，但系统不能等待人工审批才完成日常记忆沉淀和 Bot 学习。

### 3.6 经验先产生价值，技能后成熟

```text
一次高价值执行
→ Experience，可立即作为低权重参考

Experience 在新的独立任务中成功复用
→ Skill Candidate / Trial Skill

继续获得真实执行证据
→ Active / Stable Skill
```

普通任务闭环不自动产生长期记忆或能力。是否蒸馏取决于信息增益和执行证据，而不是固定任务次数。

### 3.7 领域对象、派生关系和物理存储分离

Evidence、Case、Memory、Experience、Skill 是不同领域语义，不是同一个 Artifact 依次经历的十级状态，也不要求每个概念、候选或摘要对应一张数据库表。

```text
Evidence
    ↓ 组装
Case
    ├── 产生事实、教训、偏好或反思 → Memory
    └── 产生可参考的执行方法       → Experience

Experience + 新的独立执行证据
    ↓ 编译和验证
Skill
```

必须区分领域对象、派生关系、对象自身生命周期和物理 Schema。禁止实现跨类型的全局线性状态机，例如：

```text
Raw Event → Episode → Digest → Approved Memory → Promotion Candidate → Active Skill
```

每类对象使用独立、短小的生命周期：

```text
Memory
    provisional → active → superseded / expired / rejected

Experience
    provisional → validated / contradicted / deprecated

Skill
    candidate → trial → active → stable
                       ↘ suspended / deprecated / rejected
```

第一阶段物理存储保持克制：

```text
messages / tool_events     原始 Evidence（复用现有表）
agent_cases                Case
memory_records             Fact/Lesson/Reflection/Experience 等派生记录
memory_relations           来源、强化、冲突、替代和派生关系
skills / skill_versions    Skill canonical record
pipeline_jobs              后台可靠性
```

是否将 Experience 与 Memory 物理分表由访问模式和性能验证决定，不改变两者的领域语义。新增 Artifact 类型前必须先证明现有 canonical record + kind/metadata 无法表达，避免 Schema 和事务复杂度随分类数量线性增长。

## 4. 目标领域模型

### 4.1 Evidence

Evidence 是不可由模型静默改写的原始证据层，复用并扩展现有：

- messages；
- tool_events；
- test/build results；
- documents；
- user corrections；
- workflow states；
- 后续的 email、calendar 等外部来源。

统一身份至少包含：

```text
evidence_type
evidence_id
group_id / person_id
run_id
source
timestamp
sensitivity
```

### 4.2 Run

Run 是一次后台 Bot 执行的可靠边界，不等同于聊天窗口或整个 session：

```text
run_id
group_id
bot_id
thread_id
trigger_message_id
status
started_at
completed_at
```

终态至少包括：

```text
completed | failed | cancelled | abandoned
```

### 4.3 Case

Case 是对一个 Run 的结构化任务闭环：

```text
task
task_signature
approach
tools_used
files_touched
attempts
errors
outcome
outcome_confidence
verification_signals
summary
```

Case 记录“发生过什么”，通常只用于审计、检索索引和后续蒸馏，不直接完整注入模型上下文。

### 4.4 Experience

Experience 是从高信息增益 Case 中提炼、可立即参考但尚未完全验证的方法：

```text
task_pattern
situation
approach
failure_mode
corrective_action
verification
limitations
outcome
confidence
source_case_ids
```

Experience 告诉 Bot“类似任务上次怎样处理”；Skill 告诉 Bot“这类任务已经验证应该怎样执行”。

### 4.5 Memory Record

Memory Record 统一承载：

```text
fact | decision | constraint | preference | lesson | reflection
```

共同字段至少包含：

```text
owner_type
owner_id
group_id
status
valid_from
valid_to
superseded_by
importance
confidence
authority
source_ids
algorithm_version
```

`algorithm_version`、模型和 Prompt 版本必须可追踪，以支持重建、回滚和新版蒸馏算法重跑。

### 4.6 Skill

Skill 是带生命周期的领域对象，`SKILL.md` 只是运行时投影：

```text
Skill
├── scope
├── current_version
├── trigger
├── procedure
├── verification
├── limitations
├── permissions
├── source_cases
├── usage metrics
└── lifecycle status
```

生命周期：

```text
candidate
→ trial
→ active
→ stable
→ suspended / deprecated / rejected
```

Skill 默认采用声明式表达。任何脚本、模板或二进制资产都只是 Skill Version 的受治理资产，不能因为被某个 Skill 引用就获得额外权限或形成新的执行通道。详细安全分级见 §8.4。

### 4.7 Personal Knowledge

Personal Knowledge 独立于 Group，至少包括：

```text
Profile
Expertise
Decision Pattern
Work Pattern
Communication Pattern
Social Relation
Temporal Commitment
```

个人知识必须记录说话者、观点持有人、来源、敏感度、上下文和有效时间，避免把第三方观点错误归因给用户。

### 4.8 Pipeline Job

后台蒸馏使用持久化 Job：

```text
job_id
job_type
group_id / person_id
input_id
input_version
status
attempt
idempotency_key
error
created_at
completed_at
```

它用于崩溃恢复、幂等、重试、积压观测、算法重跑和 dead-letter 管理。

## 5. 核心处理链路

### 5.1 Bot 执行学习链

```text
RUN_COMMITTED
    ↓
ASSEMBLE_CASE
    ↓
EVALUATE_OUTCOME
    ↓
检测信息增益
    ├── 普通成功：只保存 Case/指标
    ├── 新事实：生成 Memory
    ├── 失败后修正成功：生成 Experience/Lesson
    └── 新方法且强证据成功：生成 Skill Candidate
    ↓
INDEX
    ↓
后续任务召回并使用
    ↓
记录 retrieved / injected / adopted / executed / outcome
    ↓
更新 Experience 价值与 Skill 成熟度
```

### 5.2 项目知识链

```text
聊天 / 文档 / 执行结果
    ↓
原子事实与决策抽取
    ↓
关系判断
    ├── duplicate
    ├── reinforces
    ├── refines
    ├── contradicts
    └── supersedes
    ↓
写入新版本
    ↓
旧版本软失效
    ↓
当前有效事实召回
```

第一阶段使用 SQLite 关系表实现，不引入图数据库。

### 5.3 人的知识蒸馏链

```text
聊天 / 文档 / 邮件 / 任务 / 工作记录
    ↓
来源、说话者、权限、时间和敏感度识别
    ↓
Human Episode
    ↓
Profile / Knowledge / Decision / Workflow / Social Context
    ↓
冲突与时间版本处理
    ↓
Personal Knowledge Vault
    ↓
按任务和权限生成 Scoped Projection
    ↓
提供给指定 Group/Bot
```

Personal Knowledge Vault 建议使用每用户独立数据库。Group 之间仍不共享数据；同一用户只把自己拥有的知识按目的投影到指定 Group。

### 5.4 经验复用链

```text
新任务
    ↓
构造 Task Signature
    ↓
检索 Experience Index
    ↓
选取 Top 1–2 Experience Digest
    ↓
在制定计划前按预算注入
    ↓
观察是否被采用及执行结果
    ↓
记录 token、工具调用、延迟和结果变化
```

Experience 的直接产品价值是减少重复推理、无关文件阅读、工具调用和失败重试，不必等待 Skill 成熟。

### 5.5 技能生成链

```text
成功 Case / Experience Cluster
    ↓
生成 Candidate
    ↓
检查新颖性、重复、trigger、procedure、verification、权限和适用边界
    ↓
Trial Skill
    ↓
编译为 Bot workspace Skill 投影
    ↓
后续独立任务自动复用
    ↓
成功 → active / stable
失败 → 修订 / suspended / rejected
```

技能应从实际成功轨迹反向编译：保留必要步骤、验证方法和限制，删除探索噪声与无效步骤。

### 5.6 推理执行与反思基线

ReAct、Reflexion 和 Chain-of-Thought 解决的是不同问题，不应被建模成三个同级记忆类型：

| 机制 | 产品职责 | 所属阶段 |
|---|---|---|
| ReAct | 在当前任务中根据观察持续选择行动并修正计划 | 在线执行 |
| Execution Reflexion | 在当前 Run 失败或部分完成后生成纠错反馈并受限重试 | 在线纠错学习 |
| Consolidation Reflection | 跨 Case/Experience 归纳长期规律和方法边界 | 后台知识巩固 |
| Chain-of-Thought | 模型内部推理策略 | 不作为产品持久化数据 |

目标链路：

```text
Plan / Decision Trace
        ↓
ReAct: Intent → Action → Observation → Decision
        ↓
Step / Attempt Evidence
        ↓
Outcome Evaluator
        ├── success
        ├── partial
        └── failure
                 ↓
        Execution Reflexion
        failure → correction → bounded retry
                 ↓
        Durable Run Terminal State
                 ↓
Case → Experience → Lesson / Skill
        ↓
Consolidation Reflection
```

#### 5.6.1 ReAct 作为 Execution Trace Protocol

现有 `tool_loop_v1` 已具有 ReAct 的基本执行形态，不再引入第二套 ReAct runtime。升级重点是将执行轨迹结构化，使其可以支持 Case 组装、经验蒸馏、技能编译和成本分析。

建议的 Step/Attempt 字段：

```text
step_id
attempt_id
run_id
phase                  investigate | plan | execute | verify | recover
intent
action.tool
action.target
observation.status
observation.summary
decision.next_action
decision.reason_code
experience_ids_used
skill_ids_used
```

其中工具名、目标、结果状态和部分 phase 应优先由确定性代码生成。`intent`、`decision` 只保存与任务相关的短摘要，不要求模型输出长篇自由推理。

Execution Trace 必须能够回答：

- 这一步试图达成什么；
- 调用了什么工具并观察到什么；
- 观察是否改变了后续行动；
- 该步骤属于探索、执行、验证还是恢复；
- 哪条 Experience/Skill 被召回、采用并实际执行。

#### 5.6.2 Execution Reflexion

Execution Reflexion 与现有从多条 Fact 中归纳洞察的 `maybe_reflect()` 不同。它发生在同一个 Run 内，用于识别可修正失败、生成纠错方案并通过下一次尝试验证。

```text
Attempt 1 失败或部分完成
→ Outcome Evaluator 判断失败是否可修正
→ 生成结构化 Reflexion
→ 在当前 Run 的下一 Attempt 注入
→ 使用 test/build/tool/workflow 结果验证
```

结构化输出至少包含：

```text
failure_type
observed_evidence_ids
wrong_assumption
corrective_action
verification
scope
confidence
```

只有 Reflexion 后的真实执行得到成功证据，才能形成 `verified Experience`。Reflexion 文本本身不是成功证据，也不能直接成为 Active Skill。

Execution Reflexion 仅在以下情况考虑触发：

- 同一目标连续失败；
- 测试失败但根因不能由确定性规则直接识别；
- 实际结果与计划假设矛盾；
- Bot 进入重复循环；
- 任务部分完成且存在可执行的修正路径。

网络瞬时失败、权限拒绝、明确的文件不存在、依赖缺失等可确定性处理的问题，优先由错误分类和恢复策略处理，不调用 Reflexion。

初始成本基线：

```yaml
execution_reflexion:
  enabled: true
  max_per_run: 1
  max_retry_after_reflexion: 1
  require_evidence: true
```

#### 5.6.3 Consolidation Reflection

现有 `maybe_reflect()` 应归类为 Consolidation Reflection，并逐步从只消费孤立 Fact，升级为按类型处理：

```text
Fact Reflection
    归纳项目事实和稳定规律

Case Reflection
    识别 Bot 重复失败或执行模式

Experience Consolidation
    合并、修订或否定多个相似经验

Skill Reflection
    分析 Skill 的成功边界、反例和退化原因
```

Consolidation Reflection 是异步、跨 Case、低频的后台任务，不参与当前 Run 的即时重试。

#### 5.6.4 不持久化原始 Chain-of-Thought

产品不得依赖模型暴露完整 Chain-of-Thought，也不得把原始 CoT 写入 Memory、Chroma、Case、Experience 或 Skill。原因包括：

- 不同模型和版本的 reasoning 暴露能力不一致；
- 原始 CoT 包含临时猜测、重复推理和大量检索噪声；
- 模型推理声明不能替代工具、测试和系统状态证据；
- 长篇 CoT 会增加存储、召回和上下文成本。

需要持久化的是结构化 Decision Trace：

```text
goal
assumptions
considered_options
selected_option
rejected_options
reason_codes
verification_plan
```

Decision Trace 必须短、任务相关、可审计、跨模型，并能回链到产生判断的 Evidence。它是执行记录和蒸馏输入，不被视为事实证明。

#### 5.6.5 实现不变量

- ReAct 轨迹只扩展现有 tool loop，不替换 Supervisor/Worker/MCP Collector 拓扑；
- builtin/skill/shell 和 MCP 的执行路由及安全守卫保持不变；
- Reflexion 不能绕过 HIL、权限、shell danger guard 或 MCP 审批；
- Reflexion 失败不得改变前台已提交的 durable terminal state；
- 原始 Observation 先脱敏再进入模型上下文和派生记忆；
- Decision Trace 与 Reflexion 必须带 `run_id`、`attempt_id`、来源 Evidence 和算法版本；
- 所有后台 Consolidation 继续遵守 Group 物理隔离。

## 6. 知识蒸馏分层

### 6.1 事实蒸馏

回答“当前项目或用户的事实是什么”，产物是 Fact、Decision、Constraint 或 Preference。

### 6.2 经验蒸馏

回答“这次任务发生了什么、什么修正有效”，产物是 Experience 或 Lesson。

### 6.3 能力蒸馏

回答“多个经验中哪些步骤可以稳定复用”，产物是 Skill Candidate 和 Skill Version。

高层蒸馏不能只消费摘要的摘要。生成 Skill 或高阶 Reflection 时，应从相邻层摘要回查必要的原始 Evidence，防止环境条件、反例和不确定性在多层压缩中丢失。

### 6.4 未来模型蒸馏数据

系统应从现在开始保留：

```text
task
retrieved_experience
retrieved_skill
execution_trace
outcome
correction
final_solution
token_cost
tool_cost
```

这些数据未来可用于训练任务分类器、reranker、Outcome Evaluator、小型蒸馏模型或构建正负偏好对；当前阶段不直接使用个人数据训练模型。

## 7. 检索与上下文装配

### 7.1 四级检索协议

```text
Level 1: Index
    ID、标题、类型、时间、状态、分数、关键路径

Level 2: Digest
    100–500 token 的事实、Experience、Skill 或 Decision Pattern 摘要

Level 3: Detail
    完整 Case、Memory 或 Skill

Level 4: Evidence
    原始消息、工具事件、测试结果、邮件或文档
```

默认上下文不注入完整 Case 或原始 Evidence。

### 7.2 独立预算

以下内容必须分别设定 Top-K 和 token 预算，不允许只放入同一个向量结果列表竞争：

- Group facts/principles；
- Personal scoped projection；
- Bot experiences；
- Active/trial skills；
- Current thread summary。

参考优先级：

```text
Group 有效原则和事实
> Active/Stable Skill
> 高价值 Experience
> 普通 Reflection
> 原始 Case/Evidence
```

该优先级不代替分层配额。

### 7.3 经验注入保护

Experience 在以下情况不应注入：

- 相关度不足；
- 已被反例否定；
- 环境或代码版本差异过大；
- 已被成熟 Skill 完全覆盖；
- 只有 Bot 自我声明成功；
- 所需权限当前 Bot 不具备；
- 注入成本高于预期探索收益；
- 多条 Experience 冲突且尚未解决。

## 8. 自动验证与成熟度

### 8.1 Outcome Adapter

验证引擎应逐步支持：

```text
ShellExitCodeAdapter
PytestAdapter
BuildAdapter
LintAdapter
FileChangeAdapter
ApiResponseAdapter
WorkflowStateAdapter
DownstreamConsumptionAdapter
ModelJudgeAdapter
```

权威等级：

```text
确定性执行结果
> 结构化系统状态
> 下游消费成功
> Model Judge
> Bot 自我声明
```

### 8.2 Experience 使用状态

必须区分：

```text
retrieved
injected
adopted
executed
verified_success
verified_failure
```

状态只能按证据单向推进：

```text
injected → adopted → executed → verified_success | verified_failure
```

- `adopted`：Decision Trace 明确引用 memory ID，且后续行动与该建议匹配；
- `executed`：已观察到匹配行动实际发生，并保存结构化执行证据；
- `verified_*`：Outcome Adapter 已给出确定性或结构化验证结果。

不得跳级、回退或覆盖终态。模型自我声明最多作为 adoption 的弱信号，不能单独形成
`executed` 或 `verified_*`。召回次数、注入次数和 Run 的整体完成状态都不能作为有效性
证据；只有与该 Experience/Skill 建立因果关联的执行和验证结果，才能支持或反驳它。

### 8.3 Skill 成熟度

建议初始规则：

- 第一次有强证据的新方法可生成 `trial` Candidate；
- 新的独立 Case 再次成功后进入 `active`；
- 多次独立成功后进入 `stable`；
- Trial 失败可回到 `draft/rejected`；
- Active Skill 在最近窗口内重复失败进入 `suspended`；
- 新版本替代旧版本时使用 `deprecated`，不物理删除来源。

成功次数不是唯一依据，还必须考虑任务独立性、适用范围、工具权限、回滚和环境变化。

### 8.4 Declarative-first Skill 安全模型

自动技能学习采用声明式优先策略。成熟度与执行风险是两个正交维度：一个多次成功的 Skill 仍可能因为包含任意代码、网络访问或高风险写操作而不能自动发布。

#### 8.4.1 Skill 风险分级

| 等级 | 形式 | 自动生成 Trial | 运行方式 | 自动进入 Active |
|---|---|---:|---|---:|
| S0 | Knowledge / Checklist | 是 | 注入 Prompt，不直接执行 | 是，依据复用结果 |
| S1 | Declarative Workflow | 是 | 编排已有注册工具，每步重新经过路由和安全守卫 | 是，依据确定性验证 |
| S2 | Parameterized Template | 有条件 | 受 schema、allowlist 和参数校验约束 | 有条件 |
| S3 | Executable Asset | 否，默认仅 Candidate | sandbox/container 中执行 | 默认否，需完整安全与验证策略 |

S0/S1 示例：

```yaml
name: verify-group-db-migration
trigger:
  task_types: [group_db_schema_change]
checklist:
  - inspect backend/db/schema_split.py
  - inspect backend/db/migrations.py
  - require an idempotent migration
steps:
  - tool: read_file
  - tool: run_tests
verification:
  - test a newly-created group DB
  - test an existing group DB upgrade
```

S1 只能声明已有工具和步骤。Skill 不能嵌入绕过 ToolRouter 的执行器；每个工具调用仍作为普通调用重新经过：

- builtin/skill/shell 的 `tool_executor.execute()` before-hooks；
- shell danger guard；
- Worker 侧 HIL/权限策略；
- MCP Proxy → Bridge → Collector；
- 子 Agent 权限衰减；
- 输出脱敏。

S2 必须声明：

```text
parameter_schema
allowed_tools
allowed_paths
output_contract
dangerous_parameter_rules
```

S3 至少需要：

- 静态安全扫描；
- 依赖和权限 manifest；
- sandbox/container；
- 网络和文件 allowlist；
- CPU、内存和执行时间限制；
- 确定性测试；
- 内容 hash、版本和来源；
- 发布、暂停和回滚能力；
- 禁止修改安全策略、权限规则或自身发布状态。

在以上能力完成前，自动学习管线不得把生成的 Python、JavaScript、shell 或二进制资产发布为可执行 Active Skill。

#### 8.4.2 权限不增原则

```text
effective_skill_permissions
= intersection(bot_ruleset, group_policy, skill_manifest)
```

Skill 可以收窄执行范围，不能扩大 Bot 原有权限。Skill 从 Bot 晋升到 Role 或 Group 时必须重新计算权限和运行环境，不能携带原 Bot 的临时 allow、凭证或 bypass 状态。

#### 8.4.3 Canonical Compiler

自动学习管线只能先写 canonical Skill Candidate/Version，再由受信任的 Skill Compiler 生成 workspace 投影。Bot 不得通过普通文件写工具直接把自生成内容发布为 Active Skill，也不得覆盖已有稳定版本。

## 9. 后台可靠性

### 9.1 前台与学习解耦

前台任务成功不依赖后台学习成功：

```text
Run 持久化终态
→ 发布学习事件
→ 后台异步处理
```

### 9.2 幂等与恢复

建议使用以下幂等身份：

```text
(group_id/person_id, job_type, input_id, input_version)
```

Job 状态：

```text
pending
running
completed
retryable_failed
dead_letter
```

支持指数退避、最大重试、重放、积压观测和迁移缺口显著报警。

### 9.3 成本门控

每个高成本阶段先执行确定性门控：

- 是否有信息增益；
- 是否存在强证据；
- 是否与已有内容高度重复；
- 是否达到聚类门槛；
- 是否超过 Group/Bot 的模型预算。

普通成功通常只更新 Case 和已有 Experience/Skill 指标，不额外生成记忆或调用蒸馏模型。

## 10. Personal Knowledge 安全与权限

### 10.1 Personal Vault 与 Group Projection

```text
Personal Knowledge Vault
        ↓ 按用户、目的、敏感度和目标范围授权
Scoped Projection
        ↓
Group / Bot Runtime Context
```

Projection 应至少包含：

```text
principal
target_group
target_bot
purpose
allowed_domains
denied_domains
expires_at
```

### 10.2 邮件知识与邮件工具权限分离

```text
Memory permission
    Bot 是否知道联系人、职责和历史决策

Email read permission
    Bot 是否能读取相关邮件

Email send permission
    Bot 是否能代表用户发送邮件
```

三者不能自动绑定。凭证、验证码、密钥和不必要的私人正文不得进入普通 Memory、Chroma、`MEMORY.md` 或 Skill。

### 10.3 数据来源权威

建议支持：

```text
explicit_user
user_authored_document
observed_behavior
authoritative_project_source
third_party_statement
model_inference
```

第三方陈述和模型推断不能自动成为用户观点或 Group 硬约束。

### 10.4 Behavior Habit 防过拟合策略

Behavior Habit 表达人在特定条件下反复出现的工作或沟通模式。单次口语、一次紧急任务、一个项目中的临时要求或第三方描述，不能自动固化为长期个人偏好。

必须区分：

```text
explicit preference
    用户明确声明的偏好、原则或长期要求

observed habit
    系统从多个独立行为样本推断的模式

temporary instruction
    只对当前 Run、Thread 或时间窗口有效的指令
```

显式用户声明可以按声明的 scope 立即成为 Active Personal Memory。Observed Habit 默认只能创建低权重 `provisional` 记录，并通过以下多维证据门控：

```text
sample_count
independent_context_count
time_span
context_consistency
contradiction_count
urgency_context
source_authority
scope
```

建议初始策略：

```yaml
habit_inference:
  min_independent_samples: 3
  min_contexts: 2
  observation_window_days: 14
  max_contradictions: 0
  exclude_urgent_context: true
  inferred_initial_status: provisional
```

14 天只是可配置的时间跨度信号，不是单独的晋升条件。时间经过不能替代独立样本和跨场景证据；高权威的显式声明也不必等待冷静期。

Habit 必须保存条件和例外，禁止将场景偏好压缩成无条件人格标签：

```text
behavior
trigger_context
exceptions
scope
supporting_evidence_ids
contradicting_evidence_ids
valid_from
last_observed_at
```

Observed Habit 的使用约束：

- 默认仅作为低权重 planning/communication guidance；
- 不自动成为 Group 原则或安全策略；
- 不改变工具、邮件或外部行动权限；
- 遇到自然语言纠正、反例或场景变化时降权、收窄 scope 或标记 contradicted；
- 高敏感领域提高样本门槛或禁止行为推断；
- 原始邮件、聊天和第三方陈述继续按来源权限管理。

## 11. 策略配置

不同 Group/Bot 应支持独立策略，例如：

```yaml
capture:
  tool_events: true
  messages: true

case:
  enabled: true

distillation:
  mode: adaptive
  novelty_threshold: 0.65

reflection:
  min_related_cases: 3
  max_frequency: daily

execution_reflexion:
  enabled: true
  max_per_run: 1
  max_retry_after_reflexion: 1
  require_evidence: true

skill_learning:
  enabled: true
  auto_trial: true
  active_success_threshold: 2
  declarative_first: true
  max_auto_publish_risk_level: S1

habit_inference:
  min_independent_samples: 3
  min_contexts: 2
  observation_window_days: 14
  exclude_urgent_context: true

retrieval:
  core_budget: 1200
  memory_budget: 1800
  experience_budget: 800
  skill_budget: 1200
```

产品可逐步提供“关闭记忆”“仅记录不学习”“标准学习”“高强度学习”等策略，但所有状态仍按 Group/Bot 隔离。

## 12. 可观测性与评测

### 12.1 Capture 与 Pipeline

- Event 数量、丢弃和脱敏数量；
- Case 组装成功率；
- ReAct step/attempt 完整率与重复循环识别率；
- Execution Reflexion 触发率、纠错成功率和额外 token；
- Job backlog、retry、dead-letter；
- 每阶段延迟、模型调用和费用。

### 12.2 Memory 与 Retrieval

- 各类记忆写入量；
- duplicate/reinforce/supersede 比例；
- Precision@K、Recall@K、MRR；
- 当前有效事实和时间问题准确率；
- 最终注入 token；
- 哪条记忆被后续执行采用。

### 12.3 Experience 价值

- retrieved → adopted 转化率；
- 复用成功率；
- 重复失败、无关文件读取和工具调用下降；
- run 迭代数、token 和延迟变化。

### 12.4 Skill 价值

- candidate/trial/active/stable 分布；
- Trial → Active 转化率；
- Skill 使用成功、失败和回滚率；
- 与未使用 Skill 基线相比的任务成功率和成本；
- suspended/deprecated 和重复 Skill 数量。
- 各 S0–S3 风险等级的 Candidate、发布、拦截和 sandbox 失败数量。

### 12.5 Personal Knowledge 质量

- 用户纠正率；
- 偏好预测和历史决策引用准确率；
- 观点归属准确率；
- 过期信息误用率；
- 敏感信息和跨 Group 泄漏率。
- Habit provisional→active 转化率、反例率和错误泛化率。

最终使用净收益衡量：

```text
节省的任务 token、工具、延迟和失败成本
- 采集、抽取、索引、蒸馏和召回成本
```

## 13. 分阶段实施计划

### Phase 0：设计冻结与基线评测

交付：

- Memory & Learning 总体 ADR；
- 所有权、隔离和 Projection 规范；
- Run/Case/Memory/Experience/Skill schema；
- Execution Trace、Decision Trace、Attempt 和 Reflexion 契约；
- Artifact 派生关系与独立生命周期，明确禁止十级线性全局状态机；
- Behavior Habit 推断、作用域和反例策略；
- S0–S3 Skill 风险分级与权限不增原则；
- 数据权威、版本和投影规则；
- 当前 token、工具调用、记忆质量和重复失败基线；
- 测试数据集、迁移和回滚方案。

### Phase 1：可靠数据与任务底座

实现：

- 稳定 `run_id`；
- 所有 tool events 关联 Run；
- `step_id`、`attempt_id`、执行 phase 和结构化 Observation；
- 不包含原始 CoT 的 Decision Trace；
- durable Run 终态事件；
- `agent_cases`、`memory_records`、来源/关系和 `pipeline_jobs`；
- 使用少量 canonical tables 承载完整领域语义，不按每种 Artifact/Candidate 建表；
- Chroma 从 canonical record 建立可重建索引；
- 事实 ADD-only 与软失效；
- 存量数据回填；
- Group/Bot 删除、幂等、崩溃恢复和隔离测试。

验收：

- 每个后台 Run 可重建完整 Case；
- 每个关键行动可回链到 Step、Attempt、Observation 和使用过的 Experience/Skill；
- Worker 重启不重复写入；
- Chroma 可从 SQLite 重建；
- 旧事实不再因冲突被物理删除；
- 跨 Group 查询零泄漏。

### Phase 2：经验沉淀与低成本复用

实现：

- Outcome Evaluator；
- 可修正失败分类与受预算约束的 Execution Reflexion；
- Reflexion 后最多一次受控重试及真实结果验证；
- 信息增益检测；
- Experience Distiller；
- Task Signature；
- Case/Experience 的 FTS + vector 混合检索；
- Index → Digest → Detail → Evidence；
- plan 前 Experience 注入；
- Experience 使用状态和 token/tool/latency 指标；
- 普通成功跳过蒸馏；
- 失败→修正→成功优先蒸馏。

验收：

- 相似任务可召回关键路径和失败教训；
- 同 Run 的“失败→纠错→重试→成功”可生成 verified Experience；
- 未经执行验证的 Reflexion 不会自动成为有效 Experience 或 Skill；
- Experience 注入受固定预算控制；
- 普通任务不产生大量无效记忆；
- 能证明部分任务的迭代、工具调用或 token 净下降。

### Phase 3：Bot 技能学习

实现：

- `skills`、`skill_versions`、`skill_usage`；
- Skill Candidate Compiler；
- Declarative-first 的 S0/S1 Skill canonical schema 与受信任 Compiler；
- 阻止 Bot 通过普通 workspace 写入直接发布 Active Skill；
- Candidate 去重和合并；
- trial/active/stable 生命周期；
- workspace Skill 投影；
- Skill 调用与执行轨迹匹配；
- 自动成功/失败统计；
- 降级、暂停、废弃和版本更新；
- 权限需求检查和失败溯源。

验收：

- 一次成功最多生成 Trial Candidate；
- 后续独立成功才能 Active；
- Skill 文件可由 canonical record 重建；
- 失败 Skill 可自动降级；
- Skill 使用结果完整可审计。
- Learned Skill 不形成 ToolRouter、安全守卫或权限系统的执行旁路；
- S0/S1 可按证据自动演化，S3 在安全基线完成前不会自动发布。

### Phase 4：个人知识库与工作助手

实现：

- 每用户独立 Personal DB；
- Personal Knowledge Vault；
- 聊天、文档、邮件、任务等 source ingestion；
- 说话者、观点归属、权限和敏感度；
- Profile/Expertise/Decision/Workflow/Social 模型；
- Explicit Preference、Observed Habit 和 Temporary Instruction 分离；
- Habit 多样本、跨时间、跨场景、反例和紧急上下文门控；
- Scoped Projection；
- Core Personal Context；
- 邮件知识与工具权限分离；
- 时间有效性和观点演变；
- 个人知识导出、删除和重建。

验收：

- Person 数据不落任意 Group DB；
- Group 只能获得授权投影；
- A Group 数据不会进入 B Group；
- 第三方观点不会被误认为用户观点；
- 敏感正文和凭证不进入普通记忆；
- Bot 能减少用户重复解释背景和工作方式。
- 临时口语或紧急偏好不会被错误固化为无条件长期习惯。

### Phase 5：反思、关系与高级推理

进入条件：Phase 2/3 已有真实收益和足够数据，且基础混合检索确实出现瓶颈。

候选能力：

- Case cluster reflection；
- Experience consolidation；
- Skill reflection；
- entity/relation 和多跳检索；
- temporal query；
- Role Skill 晋升；
- Core Memory 自动巩固；
- 自适应成本策略；
- 必要时评估 Kuzu/Graphiti。

## 14. 暂不实施

在真实数据证明必要前，不纳入第一阶段：

- Neo4j/FalkorDB；
- 使用 LangGraph 替换 Supervisor/Worker；
- 独立 Memory 微服务；
- 每个任务启动独立 Critic Agent；
- 持久化或检索模型原始 Chain-of-Thought；
- 每任务多轮训练；
- LanceDB 与 Chroma 双向量库并存；
- 自动修改 Group 原则；
- 跨 Group 共享 Bot 经验；
- 默认允许 Bot 代表用户发送邮件；
- 直接使用个人数据训练模型；
- 把全部聊天和邮件写入向量库；
- 把人工审批放进后台学习热路径。

## 15. 外部项目参考定位

本节是外部参考的吸收统计和实现追踪表，不表示引入这些项目作为运行依赖。统计口径是“已经进入 Nuke 设计并有当前代码落点的能力组”；仅作为未来候选、尚未进入当前实现的内容不计入已吸收数。

| 参考项目 | 已吸收能力组数 | 已吸收并进入当前实现的特点 | Nuke 当前代码落点 | 明确不复制或后置 |
|---|---:|---|---|---|
| mem0 | 4 | 选择性提炼而非保存全部原文；ADD/upsert 的幂等记忆写入；向量与关键词混合召回；按用户、Group、Bot 和用途限定作用域 | `ai/experiences.py`、`ai/personal_vault.py`、`api/personal_memory.py` | 完整 SDK/Provider、第二套 runtime、把所有对话直接写入向量库 |
| EverOS | 5 | Run → Case → Experience → Skill 分层；来源证据可追溯；Case 与 Outcome Evaluation 分离；经验到技能的晋升；数据库为权威、Markdown/Workspace 仅作投影 | `ai/execution_runs.py`、`ai/cases.py`、`ai/experiences.py`、`ai/skill_learning.py` | Markdown + SQLite + LanceDB 三写权威体系，不复制其产品运行架构 |
| Graphiti | 3 | `valid_from/valid_to` 时间有效性；冲突与反证不直接覆盖旧结论；来源、说话人和观点主体分离 | `ai/personal_vault.py`、`ai/experiences.py` | 当前不引入图数据库、不做全量实体关系抽取；高级观点图谱仍按 Phase 5 进入条件决定 |
| AutoGen Task-Centric Memory | 3 | Task/Run 组装成可学习 Case；Insight 必须经过结果验证；纠正或重试成功比普通成功更有学习价值 | `ai/cases.py`、`ai/pipeline.py`、`ai/experiences.py` | 不复制依赖标准答案、为每个任务运行训练循环的完整方案 |
| Voyager | 4 | 从成功轨迹提炼技能候选；按任务签名检索技能；技能具有试用、激活、稳定、暂停生命周期；真实复用结果反向更新技能 | `ai/skill_learning.py`、`ai/pipeline.py` | 不复制自动课程；当前只生成 S0/S1 声明式技能，不生成任意可执行代码 |
| LangGraph | 4 | Run/attempt 持久身份；durable pipeline job；lease、幂等和失败恢复；执行状态与派生学习状态分离 | `ai/execution_runs.py`、`ai/pipeline.py`、`ai/reflexion.py` | 不以 LangGraph 替换 Supervisor → Worker → MCP Collector 拓扑 |
| Letta | 3 | Core Context 与长期存储分离；上下文预算控制；长期个人知识必须经过选择后进入当前上下文 | `ai/personal_vault.py`、`executors/base.py`、`runtime/dispatch.py` | 不引入 Letta runtime，不允许 Agent 任意改写用户核心记忆 |
| OpenMemory | 4 | Personal Vault 独立存储；显式 Projection；导出、删除、重建生命周期；管理面与执行面分离 | `ai/personal_vault.py`、`api/personal_memory.py`、`runtime/dbpaths.py` | 不复制其已 sunset 的后端和 MCP 架构；管理 UI 不属于当前实现基线 |
| Ruflo / claude-flow | 5 | 执行证据优先于代理评分；确定性蒸馏门槛；相似经验先聚合再晋升；弱因果关系不自动升级；学习收益和 token 成本必须可统计 | `ai/cases.py`、`ai/experiences.py`、`ai/skill_learning.py`、`ai/pipeline.py` | 不复制联邦、共识、神经网络式宣传能力或另一套多 Agent runtime |

### 15.1 吸收统计

| 统计项 | 当前结果 |
|---|---:|
| 已审阅并进入设计映射的外部项目 | 9 |
| 已进入当前实现的主要能力组 | 35 |
| 作为运行时依赖直接引入的外部框架 | 0 |
| 被外部框架替换的 Nuke 核心拓扑 | 0 |

这 35 个能力组不是 35 个独立产品功能，而是用于追踪设计来源的归类统计。多个参考项目可能共同影响同一个 Nuke 能力，例如 EverOS、AutoGen Task-Centric Memory 和 Ruflo 都影响了 Case/Experience 的证据门槛；实现中只保留一套 Nuke 数据模型，不建立三套重复机制。

### 15.2 推理与学习方法的吸收

ReAct、Reflexion 和 Chain-of-Thought 是方法，不作为外部产品框架计入上面的 9 项统计：

| 方法 | Nuke 的吸收方式 | 当前边界 |
|---|---|---|
| ReAct | 扩展现有 `tool_loop_v1` 的 Action/Observation/Decision 轨迹并绑定 Run/Attempt | 不引入第二套 ReAct runtime |
| Reflexion | 对可修正失败生成结构化纠错信息，并最多允许一次受控重试 | 反思文本不能绕过权限，也不能未经执行验证直接成为 Experience/Skill |
| Chain-of-Thought | 只保留可审计的 Decision Trace、失败分类、证据和结果 | 不持久化、不检索模型原始隐式推理文本 |

所有外部项目只作为领域和算法来源。Nuke 的 Group-first、Bot-private、Collector-only MCP、安全守卫和进程拓扑是上位约束。

## 16. 推荐实施顺序与最终定义

```text
Phase 0 设计与基线
    ↓
Phase 1 Run / Case / Canonical Memory
    ↓
Phase 2 Experience 复用与 token 收益
    ↓
Phase 3 Skill 学习与生命周期
    ↓
Phase 4 Personal Knowledge 与工作助手
    ↓
Phase 5 高级关系和反思
```

最终产品定义：

> 系统先可靠记录工作，再从高价值执行中提炼经验；经验立即帮助后续任务减少推理和试错；经过真实复用的经验形成 Bot 技能；同时持续蒸馏人的知识和工作方式，使 Bot 从会执行代码，逐渐成长为真正理解人的长期工作伙伴。
