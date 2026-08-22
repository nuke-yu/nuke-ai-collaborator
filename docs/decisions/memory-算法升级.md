# Memory 算法升级

> 状态：算法升级执行基线  
> 上位设计：`docs/decisions/MEMORY-SYSTEM-DESIGN.md`  
> 目标：将现有 Memory & Learning 工程闭环从 deterministic baseline 升级为可审计、可验证、真实复用参考框架核心算法的工业级实现。

## 1. 背景与纠偏

当前代码已经建立：

```text
Run → Case → Evaluation → Experience → Skill → Reuse Feedback
```

但其中若干关键节点仍由简化规则实现：

- Outcome Evaluation 主要依据 terminal outcome 和工具错误；
- Experience 由固定模板拼装；
- 相似经验按精确 `task_signature` 哈希分桶；
- Retrieval 使用集合 Jaccard 与向量分数直接加权；
- Skill procedure 使用固定两步模板；
- Skill 晋升主要依据成功/失败计数；
- Reflexion 主要依据错误关键词分类；
- Graphiti 只吸收了部分时间、来源和冲突字段，没有图算法。

这些实现是安全、持久和可测试的基础设施基线，但不能被视为已经达到 EverOS、mem0、Graphiti、AutoGen Task-Centric Memory、Voyager、LangGraph、Letta 等项目的同等算法能力。

此后，任何宣称“借鉴某框架能力”的实现必须至少满足一个条件：

1. 直接依赖并调用原框架核心算法；
2. 在许可证允许的情况下移植核心代码，并保留来源与许可证；
3. 因 Nuke 架构边界不能直接调用时，完整复现算法流程、输入输出契约和验证方法；
4. 通过对照测试证明 Nuke 实现达到同等功能，不得用简单规则替代核心算法。

## 2. 独立模块架构基线

本次升级必须将 Memory 建设为独立、可复用、可替换、可独立测试和可独立部署的业务模块，不得继续把算法、数据访问和 Worker 调用逻辑堆放在 `backend/ai` 内。

现有 `backend/ai/memory_provider.py` 已将基础 `recall / observe / forget` 从 tool loop 中抽离，可作为迁移起点，但还不是完整模块边界：

- `cases`、`experiences`、`skill_learning`、`reflexion`、`tool_events` 和 `pipeline` 仍直接依赖 `ai.memory` 的私有数据库函数；
- tool loop 仍直接调用 Experience、Skill 和 Personal Vault 具体实现；
- Chroma、SQLite、LLM provider 与记忆领域逻辑混合；
- 后台学习编排直接 import 具体蒸馏和技能生成函数；
- 现有接口只覆盖对话记忆，没有覆盖 Case、Experience、Skill、Graph、Personal Projection 和审计。

### 2.1 模块边界

目标代码布局：

```text
backend/memory/
├── domain/          # 纯领域对象、状态机、规则和不变量
├── application/     # use cases：observe、recall、learn、consolidate、forget
├── ports/           # 存储、模型、embedding、reranker、graph、checkpoint 协议
├── adapters/
│   ├── persistence/ # Group SQLite、Chroma/向量库、图存储
│   ├── algorithms/  # mem0、EverOS、Graphiti、AutoGen、Voyager 适配器
│   └── runtime/     # Worker in-process、IPC 或远程客户端
├── contracts/        # 版本化 command、query、event、DTO 与 error
└── bootstrap.py      # 组装依赖，领域代码不得反向依赖它
```

`backend/ai` 和 `backend/executors` 只允许依赖 `memory/contracts` 与对外 facade，不得 import `memory/adapters` 或底层表结构。API 层也只调用 application use case，不直接访问 Personal Vault 数据库。

### 2.2 稳定对外契约

对外能力按职责拆分，避免一个过大的 `MemoryProvider` 接口：

| Port | 责任 |
|---|---|
| `MemoryCommandPort` | `observe`、`forget`、`correct`、`project` |
| `MemoryQueryPort` | `recall`、`get_timeline`、`explain_provenance` |
| `LearningPort` | Case 提取、Experience 蒸馏、Skill 候选与验证 |
| `PersonalKnowledgePort` | Personal Fact/Profile/Habit 的增量演化和授权 Projection |
| `MemoryAdminPort` | 导出、删除、重建索引、回滚和按 scope 禁用 |
| `MemoryEventPort` | 发布版本化的 accepted/rejected/updated/deprecated/promoted 事件 |

每个 command/query 必须显式携带不可变 `MemoryScope`：

```text
tenant/user identity
group_id (required for group execution; absent for an unprojected Personal Vault operation)
bot_id (required for bot-private experience; optional for group knowledge)
thread_id / run_id / purpose
actor and authorization context
```

禁止使用隐式全局 Group、默认 Group 或仅靠 metadata 过滤代替物理隔离。跨 Group 请求在进入算法和存储层之前必须被拒绝。Personal Vault 可以不绑定 Group；只有经过显式授权的 Projection 才携带目标 `group_id` 进入 Group 召回路径。

### 2.3 依赖方向与框架隔离

```text
Worker / API / Scheduler
        ↓ contracts
Memory application use cases
        ↓ ports
Domain  ← adapters (mem0 / EverOS / Graphiti / storage / model)
```

- Domain 和 application 不得 import FastAPI、Worker、Chroma、Graphiti、mem0 或具体模型 SDK；
- 参考框架算法只能出现在 adapter 后面，并转换为 Nuke 的统一领域结果；
- 替换向量库、图库、LLM 或 reranker 不得修改业务 use case；
- 算法适配器必须声明 `algorithm_id / source / version / license / capabilities`，并将实际版本写入 provenance；
- 任一外部算法不可用时，模块按 Group/Bot 策略降级或 fail-open，不阻断主任务。

### 2.4 独立性与复用性验收

Memory 模块只有同时满足以下条件才算完成解耦：

- 使用假存储和假模型可在不启动 FastAPI、Supervisor、Worker 和 MCP Collector 时运行全部领域测试；
- 主程序通过契约测试对接，没有对 Memory 表、Chroma collection 或算法类的直接依赖；
- 支持 in-process adapter，同一契约可扩展为 IPC/独立服务，业务调用方无需改写；
- 模块可按 Group/Bot 独立启用、禁用、版本切换和回滚；
- 使用 contract tests 验证每个存储、算法与 runtime adapter，使用 architecture tests 禁止逆向 import；
- 记忆的完整数据、索引和运行时依赖均可通过标准管理契约导出、重建和删除；
- 不得把 MCP 连接移入 Memory 模块；如需工具结果，只消费 Worker 传入的已授权、已脱敏 Observation。

### 2.5 迁移约束

采用 Strangler 方式渐进迁移，不一次性重写现有路径：

1. 先创建 contracts、domain、ports 和架构依赖测试；
2. 用 legacy adapters 包装现有 `ai.memory / experiences / skill_learning / personal_vault`，保持行为不变；
3. 将 tool loop、API 和后台 pipeline 切换到新契约；
4. 在模块内按本文后续批次逐个替换 legacy adapter 为真实算法 adapter；
5. 新旧双读/影子运行通过对照后，再删除旧入口，数据迁移必须可回滚。

独立模块化是后续所有算法批次的前置条件，不是完成算法后再做的重构优化。

## 3. 记忆提取与更新

| ID | 功能 | 引用框架与算法 | 核心源码 | Nuke 实现目标 |
|---|---|---|---|---|
| M1 | 对话记忆提取 | mem0 Phased Batch Memory Pipeline | `mem0/mem0/memory/main.py`：`AsyncMemory.add()`、`_add_to_vector_store()` | 使用“已有记忆召回 → 单次结构化提取 → batch embedding → ADD/UPDATE/DELETE 决策”替换手工写入式提取 |
| M2 | 记忆变更决策 | mem0 ADD/UPDATE/DELETE/NOOP Memory Operations | `mem0/mem0/memory/main.py`、`memory/storage.py` | 新事实与已有事实比较后执行新增、修订、删除或忽略，并保存完整 history |
| M3 | Agent Case 提取 | EverOS `AgentCaseExtractor` | `EverOS/src/everos/memory/strategies/extract_agent_case.py` | 从完整工具轨迹提取 `task_intent / approach / key_insight / quality_score`，替换字段拼装 Case |
| M4 | Personal Episode/Fact/Profile 提取 | EverOS User Memory Pipeline | `memory/extract/pipeline/user_memory.py`、`extract_atomic_facts.py`、`extract_user_profile.py` | 将聊天与工作记录拆成 Episode、Atomic Fact、Profile Candidate，并保留来源 |
| M5 | Task-Centric Failure Learning | AutoGen `MemoryController._iterate_on_task()` | `autogen_ext/experimental/task_centric_memory/memory_controller.py` | 失败 → 提炼 Insight → 带 Insight 重试 → 只有验证成功才保存 Insight |

M1/M2 优先评估直接集成 mem0 `AsyncMemory`，但必须提供 Nuke 的 Group/Bot scope adapter，禁止 mem0 创建跨 Group 全局存储。

M3/M4 的 EverOS orchestration 使用 Apache-2.0；真正算法位于独立 PyPI 包：

- `everalgo-agent-memory==0.3.1`
- `everalgo-user-memory==0.3.1`
- `everalgo-rank==0.4.1`
- `everalgo-knowledge==0.1.1`

引入前必须分别核验这些包的许可证、源码可审计性、版本锁定和离线降级行为。

## 4. 经验聚类与蒸馏

| ID | 功能 | 引用框架与算法 | 核心源码 | Nuke 实现目标 |
|---|---|---|---|---|
| E1 | Case 语义聚类 | EverOS `cluster_by_llm` | `trigger_skill_clustering.py` | embedding 生成候选簇，LLM 判断合并或新建，替换精确 `task_signature` 分桶 |
| E2 | 几何时间聚类 | EverOS `cluster_by_geometry` | `trigger_profile_clustering.py` | 使用 cosine centroid、时间窗口、簇计数和增量质心更新，对习惯与 Profile 证据聚类 |
| E3 | Case 质量门控 | EverOS `quality_score`、`skip_quality_threshold` | `extract_agent_case.py`、`trigger_skill_clustering.py` | Case Extractor 输出质量评分，低质量 Case 不进入技能学习 |
| E4 | Experience Consolidation | EverOS Select → Merge → Re-extract → Deprecate | `memory/reflection/orchestrator.py` | 同簇碎片经验定期合并、重新提取，旧经验 deprecated 并保留 lineage |
| E5 | 失败 Insight 验证 | AutoGen Grader + learn-from-failure | `memory_controller.py`、`_prompter.py`、`utils/grader.py` | 使用 Nuke 测试、工具结果和任务验收信号替代 expected answer，验证 Insight 是否纠正失败 |

Experience 不得继续使用固定的 `Subsequent execution completed successfully`。正式结构至少包括：

```text
problem_pattern
preconditions
failed_assumption
failure_mode
corrective_action
verification
applicability
limitations
counterexamples
source_case_ids
```

## 5. 工业级混合检索

| ID | 功能 | 引用框架与算法 | 核心源码 | Nuke 实现目标 |
|---|---|---|---|---|
| R1 | Sparse + Dense Recall | EverOS BM25 + Cosine ANN | `memory/search/recall/base.py`、`agent_case.py`、`agent_skill.py` | 使用真正 BM25 与向量 ANN，替换集合 Jaccard 关键词评分 |
| R2 | 排名融合 | EverOS/Graphiti Reciprocal Rank Fusion | `everalgo.rank.fusion.rrf`、`graphiti_core/search/search.py::rrf()` | BM25 和向量结果通过 RRF 融合，不直接混合不可校准分数 |
| R3 | Cross-Encoder Rerank | EverOS Skill Hybrid Search | `memory/search/skill_hybrid.py` | 对融合候选进行 cross-encoder 相关性判断 |
| R4 | 多样性控制 | Graphiti Maximal Marginal Relevance | `graphiti_core/search/search.py::maximal_marginal_relevance()` | 对重复经验去重，使结果覆盖不同策略 |
| R5 | 适用性验证 | AutoGen `validate_insight()` | `memory_controller.py`、`_prompter.py` | 最终候选由模型判断是否适用于当前任务，不只依赖语义相似度 |
| R6 | Agentic Retrieval | EverOS `aagentic_retrieve()` | `memory/search/agentic.py`、`agentic_agent.py` | 复杂任务使用查询改写、两轮检索、cluster scope 和 rerank；简单任务使用低成本 HYBRID |

目标检索链：

```text
Query classification
→ BM25 recall
→ Vector ANN recall
→ RRF fusion
→ Scope/validity filter
→ Cross-encoder rerank
→ MMR diversity
→ Applicability validation
→ Token-budget packing
```

## 6. 技能生成与验证

| ID | 功能 | 引用框架与算法 | 核心源码 | Nuke 实现目标 |
|---|---|---|---|---|
| S1 | Skill Induction | EverOS `AgentSkillExtractor` | `extract_agent_skill.py` | 输入目标 Case、已有相关 Skill、支持 Case，输出 add/update/retire 操作 |
| S2 | Skill 候选选择 | EverOS cluster-scoped cosine top-K | `extract_agent_skill.py::_select_existing_skills()` | 只选择同簇且与目标 Case 最相关的技能 |
| S3 | Supporting Case Lineage | EverOS supporting-case hydration | `extract_agent_skill.py::_select_supporting_cases()` | Skill 每个结论必须能追溯支持 Case |
| S4 | 自动结果 Critic | Voyager `CriticAgent` | `Voyager/voyager/agents/critic.py` | 根据执行状态、测试、文件变化和任务约束输出 success/critique |
| S5 | Skill Library | Voyager `SkillManager` | `Voyager/voyager/agents/skill.py` | 为技能生成描述、embedding 索引、Top-K 检索和版本化保存 |
| S6 | 成功后入库 | Voyager success-gated skill acquisition | `Voyager/voyager/voyager.py` | 只有 Critic 成功且验证信号满足门槛，技能才进入 trial |
| S7 | 技能对照验证 | AutoGen before/after teachability evaluation | `eval_teachability.py`、`eval_learning_from_demonstration.py` | 比较不使用/使用技能时的成功率、Token、工具次数和延迟 |
| S8 | 安全声明编译 | Nuke 安全增强 | `backend/ai/skill_learning.py` | EverOS/Voyager 负责归纳，Nuke validator 限制为 S0/S1 并禁止绕过安全层 |

正式声明式 Skill 至少生成：

```text
trigger
preconditions
procedure
branches
allowed_tools
failure_recovery
verification
limitations
evidence_ids
```

## 7. 观点演变和时序知识

| ID | 功能 | 引用框架与算法 | 核心源码 | Nuke 实现目标 |
|---|---|---|---|---|
| G1 | Episode → Entity/Relation | Graphiti Combined Extraction | `combined_extraction.py::extract_nodes_and_edges()` | 单次结构化调用同时提取实体和关系，减少孤立实体 |
| G2 | Entity Resolution | Graphiti `resolve_extracted_nodes()` | `node_operations.py` | 精确规范化、embedding 候选、LLM 去重，统一人物、概念与项目 |
| G3 | Edge Resolution | Graphiti edge extraction/resolution | `edge_operations.py` | 关系去重、属性提取、证据 Episode 绑定 |
| G4 | Temporal Invalidation | Graphiti valid/invalid edge algorithm | `edge_operations.py`、`prompts/extract_edges.py` | 新旧观点冲突时设置 `invalid_at`，不覆盖或删除旧观点 |
| G5 | Episode Graph Pipeline | Graphiti `Graphiti.add_episode()` | `graphiti_core/graphiti.py` | previous episodes → node extraction → resolution → edge resolution → invalidation → hydration |
| G6 | Graph Retrieval | Graphiti hybrid graph search | `graphiti_core/search/search.py` | Full-text、cosine、BFS、RRF、MMR、cross-encoder 联合检索 |
| G7 | Group Partition | Graphiti `group_id` + Nuke physical DB | `Graphiti.add_episode(group_id=...)` | Graphiti 逻辑分区之外，继续执行每 Group 物理隔离 |

如果保留“吸收 Graphiti”的产品表述，G1–G6 必须真实实现；否则从已实现能力中删除该表述。

## 8. 持久执行与恢复

| ID | 功能 | 引用框架与算法 | 核心源码 | Nuke 实现目标 |
|---|---|---|---|---|
| D1 | Checkpoint Tuple | LangGraph `CheckpointTuple` | `langgraph/checkpoint/base/__init__.py` | 保存 checkpoint、metadata、parent config 和 pending writes |
| D2 | Pending Writes | LangGraph `put_writes()` | `BaseCheckpointSaver.put_writes()` | 学习节点中间结果独立持久化，崩溃后不重做已完成节点 |
| D3 | SQLite Checkpointer | LangGraph `AsyncSqliteSaver` | `checkpoint-sqlite/langgraph/checkpoint/sqlite/aio.py` | Memory Learning DAG 使用真实 checkpoint saver，而不只记录 job 状态 |
| D4 | Resume/Fork/Prune | LangGraph checkpoint lineage | `BaseCheckpointSaver` | 支持节点恢复、算法版本 fork、祖先 lineage 和安全 prune |

LangGraph 只用于后台 Memory Learning DAG 的 checkpoint 核心，不替换 Supervisor → Worker → MCP Collector。

## 9. Personal Knowledge 与上下文预算

| ID | 功能 | 引用框架与算法 | 核心源码 | Nuke 实现目标 |
|---|---|---|---|---|
| P1 | Core Memory Blocks | Letta `Memory`/`Block` | `letta/schemas/memory.py`、`schemas/block.py` | Profile、Preference、Workflow、Relationship 按独立 Block 和预算管理 |
| P2 | Context Window Accounting | Letta `ContextWindowCalculator` | `services/context_window_calculator/context_window_calculator.py` | 使用模型 tokenizer 计算真实 Token，替换字符近似预算 |
| P3 | Archival Recall | Letta archival passage search | `helpers/tpuf_client.py`、`schemas/memory.py` | Core Block 常驻，长期 Personal Records 通过检索按需进入上下文 |
| P4 | Personal Memory ACL | OpenMemory Access Control | `openmemory/api/app/utils/permissions.py`、`app/models.py` | Projection 使用 subject/object/effect ACL，并记录访问日志 |
| P5 | Access Audit | OpenMemory `MemoryAccessLog` | `openmemory/api/app/mcp_server.py` | 搜索、读取、Projection、删除记录用户、Group、Bot、用途和时间 |
| P6 | Personal Fact Evolution | mem0 ADD/UPDATE/DELETE + history | `mem0/memory/main.py`、`storage.py` | 用户知识可追踪地新增、修订、失效和恢复，不重复追加 |

## 10. ReAct 与 Reflexion

| ID | 功能 | 算法 | Nuke 实现目标 |
|---|---|---|---|
| X1 | ReAct Trace | Thought/Action/Observation 循环的结构化协议 | 每个 Attempt 保存计划摘要、Action、Observation、Decision 和结果；不保存隐藏 CoT |
| X2 | Reflexion | Actor → Evaluator → Self-Reflection → Retry | 使用真实 Evaluator/Critic 生成结构化反思，不只依赖错误关键词 |
| X3 | Reflexion Memory | 失败反思的 episodic memory | 反思在下一次执行验证成功后才进入 Experience |
| X4 | Bounded Retry | Nuke 安全约束 | 最多一次反思重试，不能绕过 HIL、权限、Shell Guard 和 MCP Collector |

## 11. 许可证与引用策略

| 项目 | 已确认代码许可证 | 引用策略 |
|---|---|---|
| mem0 | Apache-2.0 | 可直接依赖或移植，保留 NOTICE 和来源 |
| EverOS | Apache-2.0 | orchestration 可复用；`everalgo` 包必须单独核验 |
| Graphiti | Apache-2.0 | 可直接依赖 `graphiti-core` 或移植核心模块 |
| AutoGen code | MIT，以 `LICENSE-CODE` 为准 | 可移植 Task-Centric Memory 核心代码 |
| Voyager | MIT | 可移植 SkillManager/Critic 算法 |
| LangGraph | MIT | 可直接依赖 checkpoint 核心包 |
| Letta | Apache-2.0 | 优先移植 Block/Context Calculator，不引入整个 runtime |
| OpenMemory | mem0 仓库 Apache-2.0 | 移植 ACL 与 Access Log 模型 |

Ruflo 当前只有设计材料，没有本地源码，因此不能满足“引用核心代码”的要求。在获得源码并完成许可证审查前：

- 不把 Ruflo 算法计入已实现能力；
- 不声称使用其聚类、因果或强化算法；
- 重复能力使用可审计的 EverOS、Graphiti、AutoGen 实现。

所有直接复制或实质移植的源码必须：

- 在文件头或 `THIRD_PARTY_NOTICES` 标明项目、版本、原文件和许可证；
- 保留原版权声明；
- 锁定依赖版本与源码 commit；
- 建立供应链和许可证扫描；
- 禁止未经审计的远程动态代码进入执行路径。

## 12. 实施批次

> 本节是实施路线，不是能力状态。当前状态以
> [Memory Capability Wiring Status](MEMORY-CAPABILITY-STATUS.md) 为准。

| 批次 | 当前状态 | 说明 |
|---:|---|---|
| 1–2 | 已完成 | Contracts/domain/ports 已建立，主要应用入口已切换到模块契约 |
| 3 | 已在线 | Mem0 风格事实决策接入 canonical memory flow |
| 4–7 | 部分在线 | Case/Experience/Skill durable pipeline 在线；不是完整 EverOS OME runtime |
| 8 | 部分在线 | RRF/MMR/Graph 适配器存在；默认热召回并非所有查询都经过完整 hybrid 链 |
| 9 | 部分在线 | Voyager critic、门控和声明式执行计划在线；不是可执行代码 Skill Library |
| 10–12 | 已在线/部分在线 | checkpoint、Letta budget、Personal ACL/审计按各自边界接入 |
| 13 | 已实现/已组合 | Graphiti temporal adapter 可用，但默认热召回未启用 |
| 14 | 持续进行 | 需要持续补充无记忆、Experience、Skill 的生产形态对照测试 |

每个批次独立开发、验证和提交：

1. 建立独立模块 contracts/domain/ports、legacy adapters 和架构测试；
2. 将 tool loop、API 和 Learning Pipeline 从直接 import 切换到模块契约；
3. mem0 记忆提取与 ADD/UPDATE/DELETE adapter；
4. EverOS/everalgo Agent Case Extractor adapter；
5. AutoGen Failure Insight Learning 与结果验证；
6. EverOS Case Clustering；
7. EverOS Agent Skill Extractor；
8. EverOS + Graphiti BM25/ANN/RRF/Cross-Encoder/MMR 检索；
9. Voyager Critic 和成功门控；
10. LangGraph Learning DAG Checkpoint；
11. Letta Token Budget 与 Core/Archival Blocks；
12. OpenMemory ACL 和访问审计；
13. Graphiti Entity/Edge/Temporal Invalidation；
14. 全链路对照测试：无记忆、使用 Experience、使用 Skill。

每批开始前必须完成：

- 需求与 Nuke Group-first/Bot-private 原则对齐；
- 原算法源码、版本、许可证确认；
- 输入输出契约和数据迁移设计；
- 降级、回滚、成本预算和安全边界设计。

## 13. 工业级验收标准

一项能力只有同时满足以下条件才可以标记为完成：

- 引用的算法真实运行，不是同名规则或固定模板；
- 核心来源能够定位到项目、版本、文件、类和函数；
- 有许可证与第三方代码记录；
- 有算法级单元测试和集成测试；
- 有与旧简化实现或无记忆基线的对照结果；
- 测量准确率、成功率、Token、工具尝试、延迟和失败率；
- 证明不会破坏 Group 物理隔离、Bot 私有经验和 Personal Projection；
- 学习失败不阻塞正常任务执行；
- 能够按 Group/Bot 关闭、降级和回滚；
- 通过模块契约、架构依赖和独立运行测试，证明 Memory 可替换、可复用且不与 Worker/API/存储实现耦合；
- 不绕过 ToolRouter、HIL、Shell Guard、输出脱敏和 Collector-only MCP；
- 每个功能批次独立提交。

最终定义：

> 当前代码是 Memory & Learning 基础设施基线。本文件列出的算法节点完成、通过对照验证并满足安全与许可证要求后，才能将系统定义为工业级 Memory Learning 产品实现。
