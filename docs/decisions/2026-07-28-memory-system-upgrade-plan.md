# Memory System Upgrade & Refactoring Plan (2026-07-28)

## 1. 概述与核心目标 (Executive Summary)

本规划文档定义了 **Nuke AI Collaborator** 记忆系统（Memory Bounded Context）的完整升级与演进计划。

升级核心目标：
1. **可靠性与崩溃恢复 (P0)**：消除现有临时内存协程 (`bg.spawn`) 在 Worker 崩溃时丢失观察数据的缺陷，建立持久化、具备 Lease 竞力与自动恢复能力的 `LearningJobDispatcher`；
2. **闭环因果追溯 (P0)**：在真实 Tool Loop 中打通 `injected -> adopted -> executed -> verified_success` 因果证据链，消除假采纳与凭空晋升；
3. **消除即时晋升漏洞 (P0)**：修正 Skill 晋升逻辑，强制要求从 `Trial` 晋升到 `Active` / `Stable` 必须建立在 Skill 创建**之后**的跨 Run 独立验证之上；
4. **全量检索与规模化 (P1)**：为 Group Fact 建立 SQLite `FTS5` 索引，打破 `ORDER BY updated_at DESC LIMIT 200` 硬瓶颈，并将 Experience/Skill 改为有界候选融合检索；
5. **可中断重建与僵尸 Run 清理 (P1)**：提供基于游标的可暂停/恢复投影重建（Projection Rebuild）与僵尸 Run 恢复；
6. **个人知识治理与安全擦除 (P2)**：加固个人 Vault 敏感度策略，提供并发安全的数据库与 WAL/SHM 文件擦除；
7. **产品级评测与故障注入 (P3)**：建立固定 Benchmark 与崩溃/排队/超时故障注入 Harness。

---

## 2. 冻结的架构设计决策 (Architectural Decision Records)

在开始任何代码变更前，已冻结以下三项核心架构决策：

| 决策 ID | 决策项 | 决策内容与架构裁决 |
|---|---|---|
| **ADR-01** | **Job Dispatcher 进程拓扑** | `LearningJobDispatcher` 运行在持有 Group Lease 的 Worker 进程内，**不新增独立 Memory 守护进程**。与现有的 `Worker process × N` 隔离模型保持一致。 |
| **ADR-02** | **Skill 晋升判定规则** | 新创建的 Skill 初始状态强制为 `Trial`。必须在 **Skill 创建之后** 的全新、独立 Run 中捕获到 `adopted -> executed -> verified_success` 证据链，才能晋升为 `Active`。同一 Case 不能同时作为诞生证据和晋升证据。 |
| **ADR-03** | **Personal Provisional 投影策略** | 个人观察数据（Provisional Data）**不完全禁止**投影，但仅允许显式授权、单独低权重展示，并清楚标记；`Secret` 敏感级别**绝对禁止**任何形式的投影。 |

---

## 3. 执行规则与质量门槛 (Execution Rules & Quality Gates)

每一个 Task 的落地方案均必须严格遵从以下闭环规范：

1. **原子 Commit 规约**：
   * 一个问题对应一个 Commit，每个 Commit 必须具备**独立可回滚**能力。
   * 严禁在一个 Commit 中混入格式化、重构或无关的代码修复。
   * Commit 消息只描述变更本身，作者统一为 `nuke`（无 AI 署名）。
2. **测试门槛 (Quality Gate)**：
   * 变更必须附带新增或更新的单元/集成测试。
   * 提交前必须执行静态检查与 `pytest` 全量回归测试，确保 100% 通过。
3. **反馈汇报**：
   * 每个 Task 提交后，需明确汇报：`Commit Hash`、`测试结果` 及 `剩余风险`。

---

## 4. 详细实施路线图 (Task-by-Task Roadmap)

### 第一阶段：P0 可靠性与学习闭环

```
Task 1 (Dispatcher) ──> Task 2 (Turn Observation) ──> Task 3A (Stable References)
                                                                 │
Task 4 (Skill Promotion Rule) <── Task 3B (Causal Usage) <──────┘
```

#### Task 1：建立真正的 Learning Job Dispatcher
* **目标**: 让 `pipeline_jobs` 具备自动消费、Lease 抢占和崩溃恢复能力。
* **修改范围**:
  - 为 Job 建立 Handler Registry，第一种类型为 `evaluate_case`；
  - 周期扫描 Worker 当前持有的 Active Group，消费 `pending`、`failed` 及 `lease_until` 过期的 `running` 任务；
  - 保留并强化现有的 Lease Fencing、Max Attempts 及 Dead 状态机制；
  - Group Hydration 时立即触发一次恢复扫描；Worker 周期循环持续处理 backlog；
  - 单个 Job 失败不影响其他 Group；增加 Backlog/Retry/Dead/Expired 监控指标；
  - `process_case()` 改为 `enqueue_job()` 异步派发，剥离前台同步阻塞。
* **关键不变量**:
  - 同一 Job 最多只有一个有效 Lease Owner；
  - 旧 Worker 过期 Lease 完成后无法覆盖新 Worker 的执行结果；
  - Group Eviction 后停止 Claim 新 Job；Job Handler 必须幂等；
  - 前台 Bot 回复不依赖学习 Job 的成功。
* **预计 Commit**: `feat(memory): add durable learning job dispatcher`

#### Task 2：将 post-turn Memory Observation 改为 Durable Capture Job
* **依赖**: Task 1
* **目标**: 消除 `bg.spawn(observe())` 在进程崩溃时丢失 Fact、Summary、Reflection 的时间窗口。
* **修改范围**:
  - 增加 `observe_turn` Job 类型；
  - Job Input 仅使用稳定的 `(message_id, bot_id, algorithm_version)`，大段文本从持久化消息延迟拉取，避免 JSON 膨胀；
  - Fact、Summary、Reflection、Tool Compression 各步骤具备独立的子幂等水标；
  - Hydration 时扫描已保存但缺失 Capture Job 的 Bot 消息，自动补齐；
  - 移除热路径上的临时 `bg.spawn(runner.memory.observe(...))` 协程。
* **预计 Commit**: `fix(memory): make turn observation crash recoverable`

#### Task 3A：为 Experience/Skill 注入稳定引用身份
* **目标**: 让模型和执行轨迹能明确表达“采纳了哪条 Experience/Skill”。
* **修改范围**:
  - 注入 Prompt 的内容中包含结构化稳定 ID（如 `exp_ref:rec_123` / `skill_ref:sk_456@v1`）；
  - 标记引用 ID 只能作为结构化引用，不能作为系统指令（Untrusted Data）；
  - `DecisionTrace` 和 `ToolCall` 中增加结构化 `memory_refs` 数组；
  - 对未知、跨 Group 或未注入的 ID 拒绝记录 Adoption。
* **预计 Commit**: `feat(memory): carry stable learning references through execution`

#### Task 3B：接入 adopted → executed → verified 因果状态机
* **依赖**: Task 3A
* **目标**: 将使用率状态机连接到真实 Tool Loop 中。
* **修改范围**:
  - Decision Trace 明确引用后记录 `adopted`；
  - 后续工具行动与 Experience/Skill 建议匹配后记录 `executed`；
  - Outcome Adapter 对同一目标给出结果后记录 `verified_success` / `verified_failure`；
  - 禁止仅凭 Run Completed 跨级跳过验证；
  - 多条记忆同时注入时分别建立独立证据链。
* **预计 Commit**: `feat(memory): connect causal usage evidence to tool execution`

#### Task 4：修正 Skill 晋升语义
* **依赖**: Task 3B
* **目标**: 移除同一个 Case 中 Trial 创建后即时 Active 的快捷方式。
* **修改范围**:
  - 移除 `process_case()` 中的即时 `promote_skill()`；
  - 新创建的 Skill 初始状态强制为 `Trial`；
  - `Active` 必须要求至少包含一次 **Skill 创建之后** 的独立 verified success；
  - `Stable` 要求多个独立 Run 的累计成功验证；
  - Promotion Audit 保存完整的 Evidence、Run ID 和 Version 关系。
* **预计 Commit**: `fix(memory): require post-trial evidence for skill promotion`

---

### 第二阶段：P1 检索与规模化

#### Task 5：Group Fact 全量 FTS 候选召回
* **目标**: 消除“只看最近 200 条”的长期记忆盲区。
* **修改范围**: 为 Active Group Fact 建立 SQLite `FTS5` 索引与触发器，融合 Lexical、Exact Match、Authority、Importance 与 Recency 重排，移除 `LIMIT 200` 硬限制。
* **预计 Commit**: `feat(memory): index canonical group facts with FTS`

#### Task 6A & 6B：Experience 与 Skill 有界候选检索
* **修改范围**: 改全量读 JSON 为基于 FTS、Vector 及 Cluster 的多路候选提取，设置严格的字符/Token 预算。
* **预计 Commit**:
  - `perf(memory): bound experience retrieval candidates`
  - `perf(memory): bound learned skill retrieval candidates`

#### Task 7：Projection Rebuild 分页与进度控制
* **修改范围**: 区分 `incremental`、`repair` 和 `full_rebuild` 模式，引入游标与状态记录，支持长重建任务的可暂停、断点续传与恢复。
* **预计 Commit**: `feat(memory): make projection rebuild incremental and resumable`

#### Task 8：补齐 Abandoned Run Recovery
* **修改范围**: 在 Hydration 时自动检测无有效 Session/Lease 的僵尸 Run，打断并标记为 `abandoned`，清理相关 Job。
* **预计 Commit**: `fix(memory): recover abandoned execution runs`

---

### 第三阶段：Personal Knowledge 治理

#### Task 9A & 9B：Personal Projection 策略加固与 Safe Vault 安全删除
* **修改范围**:
  - 严格限制敏感域（Secret 绝对禁止，Provisional 显式低权重标记）；
  - 实现 Personal Vault 数据库文件（`.db`, `-wal`, `-shm`）的并发锁、连接池刷新与彻底物理擦除。
* **预计 Commit**:
  - `fix(memory): enforce personal projection sensitivity policy`
  - `fix(memory): make personal vault deletion concurrency safe`

---

### 第四阶段：产品级评测

#### Task 10：Memory Evaluation Harness 与故障注入
* **修改范围**: 构建固定 Benchmark 数据集测量 Precision@K/Recall@K/MRR，并实现 Worker Crash、Expired Lease、SQLite BUSY 和 Outbox Backlog 的故障注入测试。
* **预计 Commit**:
  - `test(memory): add retrieval quality evaluation harness`
  - `test(memory): add durability fault injection scenarios`

---

## 5. 执行批次与进展追踪 (Execution Checklist)

### 批次一：P0 可靠性与闭环（当前焦点）
- [ ] **Task 1**: `feat(memory): add durable learning job dispatcher`
- [ ] **Task 2**: `fix(memory): make turn observation crash recoverable`
- [ ] **Task 3A**: `feat(memory): carry stable learning references through execution`
- [ ] **Task 3B**: `feat(memory): connect causal usage evidence to tool execution`
- [ ] **Task 4**: `fix(memory): require post-trial evidence for skill promotion`

### 批次二：P1 检索与规模化
- [ ] **Task 5**: `feat(memory): index canonical group facts with FTS`
- [ ] **Task 6A**: `perf(memory): bound experience retrieval candidates`
- [ ] **Task 6B**: `perf(memory): bound learned skill retrieval candidates`
- [ ] **Task 7**: `feat(memory): make projection rebuild incremental and resumable`
- [ ] **Task 8**: `fix(memory): recover abandoned execution runs`

### 批次三：P2 个人治理与 P3 评测
- [ ] **Task 9A**: `fix(memory): enforce personal projection sensitivity policy`
- [ ] **Task 9B**: `fix(memory): make personal vault deletion concurrency safe`
- [ ] **Task 10A**: `test(memory): add retrieval quality evaluation harness`
- [ ] **Task 10B**: `test(memory): add durability fault injection scenarios`
