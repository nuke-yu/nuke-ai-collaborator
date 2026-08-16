# Memory 论文借鉴与实现总表

> 目的：把 Memory 当前实现按“解决的问题”重新组织，并明确论文思想、工程落点、实现证据和未完成边界。\
> 状态口径：`已在线` 表示 composition root 有生产接线；`部分在线` 表示存在持久化/测试链路但仍有迁移或能力边界；`适配器` 不等于生产能力。

## 总体架构

```text
Observe
  → Normalize / Redact
  → Fact / Episode / Failure / Reflection extraction
  → Canonical SQLite transaction
  → Durable Outbox
  → Vector / FTS / Graph projections

Recall
  → Scope + ACL filter
  → Lexical / Vector / Graph / Cluster lanes
  → RRF + MMR + budget
  → Context injection

Outcome
  → Verify evidence
  → Case / Experience / Skill promotion
  → Usage feedback and decay
```

## 论文—实现矩阵

| 生命周期/能力 | 参考论文或项目 | 论文/项目解决的问题 | Nuke 当前代码落点 | 当前状态 | 架构边界与下一步 |
|---|---|---|---|---|---|
| 事实抽取与冲突决策 | **Mem0**，`2504.19413-Mem0.pdf` §2、Appendix B Algorithm 1 | 新信息对已有记忆执行 ADD / UPDATE / DELETE / NOOP，避免重复和冲突事实累积 | `memory/adapters/algorithms/mem0_fact_engine.py`；`memory/application/bot_facts.py`；canonical `memory_records` | **已在线** | Mem0 只负责决策，不拥有事务、权限或存储；模型失败必须继续走确定性 fallback，并持续观测 ADD/UPDATE/NOOP 比例和成本 |
| 原始事件到长期能力 | **EverOS**，`EverOS/docs/how-memory-works.md` | Run → Episode/Case → Experience → Skill 的逐级抽象，以及原始事实与索引分离 | `memory/application/case_service.py`、`experience_service.py`、`skill_service.py`、`jobs.py` | **已在线** | 当前是 Nuke 原生 durable pipeline，不是 EverOS OME/Markdown 的完整复刻；需要继续保证 source snapshot 可重建、蒸馏幂等 |
| 失败洞察与纠正复用 | **AutoGen Task-Centric Memory**（源码实现，无独立论文） | 从错误答案、工作轨迹和预期答案中提炼短小、可复用的 corrective insight | `memory/adapters/algorithms/autogen_failure_engine.py`；Tool Loop failure insight；`case_service.py` | **已在线** | 失败不能自动等同于可重试；retry 必须受 FailureCategory、工具白名单和副作用策略约束 |
| 语言化反思 | **Reflexion**，`2303.11366-Reflexion.pdf` Algorithm 1 | 将环境反馈写成下一轮可读取的 verbal reflection | `memory/application/reflections.py`、`reflexion_service.py`、durable reflection stage | **部分在线** | 已有结构化反思和 watermark，但仍需评估反思污染、长期累积和收益；反思不是事实，不应提升为 authoritative source |
| 时序事实与关系失效 | **ZEP/Graphiti**，`2501.13956-Graphiti.pdf` §2–§3 | 保留事实历史、有效时间和 supersede 关系，支持 as-of 查询 | `graphiti_temporal_engine.py`；`CanonicalRelationService`；`memory_relations_archive`；`MemoryComposition.temporal_graph` | **核心能力已在线** | 已接入索引化实体消歧、多跳 BFS、社区图 materialization、热/冷边归档；后续重点是线上规模与召回质量评估 |
| 多路召回融合 | **RRF**（Graphiti/ZEP 引用） | lexical、vector、graph、cluster 分数不在同一尺度时仍能稳定融合排序 | `memory/adapters/algorithms/hybrid_rerank_engine.py`；Experience Recall | **已在线** | RRF 的 `k`、各 lane 权重必须配置化并可观测；不能把 RRF 描述为 cross-encoder |
| 结果去冗余 | **MMR**（Graphiti/ZEP 引用） | 在相关性和多样性之间取平衡，减少重复记忆占用上下文 | `memory/adapters/algorithms/hybrid_rerank_engine.py` | **已在线** | MMR 只负责排序，不负责权限、事实有效性或最终预算；需按 query 类型评估 lambda |
| 成功轨迹到技能 | **Voyager**，`2305.16291-Voyager.pdf` §2–§3 | Automatic Curriculum、Skill Library、执行反馈和验证门控 | `memory/application/skill_compilation.py`、`skill_projection.py`；`voyager_sandbox.py`；Voyager critic adapter | **部分在线** | 当前 Skill 是受限声明式策略/执行计划，不是 Minecraft 式可执行代码库；任何 code/shell/eval 路径必须 fail-closed |
| 可恢复长任务 | **LangGraph Checkpoint**（源码实现，无独立论文） | 保存 checkpoint、pending writes、fork/prune，进程失败后恢复而非重跑 | `memory/application/jobs.py`、`execution_runs.py`；`memory_checkpoint_pending_writes` | **已在线** | 兼容 Saver API 不代表使用官方 Saver；lease、幂等键、状态 hash 和删除 thread 仍是 Nuke 自有不变量 |
| 有限上下文与外部记忆 | **MemGPT/Letta**，`2310.08560-MemGPT.pdf` §2–§4 | 将 working context 与 archival memory 分层，由 paging/eviction 控制上下文窗口 | `memory/adapters/algorithms/letta_acl_engine.py`；tool-loop budget/compaction；durable memory blocks | **部分在线** | 已有 tokenizer-aware budget、paging、eviction 和显式 memory read/write；尚未成为完整的主动 function-memory runtime |
| 个人记忆权限与审计 | **OpenMemory**（源码实现，无独立论文） | App 生命周期、subject/object/effect ACL、访问审计和用户隔离 | `memory/application/authorized_personal.py`、`personal_vault.py`；`infrastructure/personal_policy.py`；central deletion audit | **已在线** | API 必须只经过 authorized application service；删除审计是 outbox/sweeper 问题，不能因中央服务失败而回滚物理删除 |
| 记忆重要性与周期反思 | **Generative Agents**，`2304.03442-GenerativeAgents.pdf` §Memory Retrieval/Reflection | 结合 recency、importance、relevance 选择注入记忆，并周期性生成 reflection | `memory/application/experience_service.py` 的 recency/importance/semantic ranking；reflection pipeline | **部分在线** | 当前排序是工程化近似，不是论文算法复现；需要单独评估 importance 标注质量和反思树缺失的影响 |
| 记忆安全与信息边界 | **跨论文工程约束**（非单一论文） | 防止历史记忆中的 prompt injection、secret、跨租户数据进入模型上下文 | `memory/domain/safety.py`；`MemoryScope`/`Principal`；ACL；projection outbox | **已在线** | 所有 canonical 写入、projection、recall 和 context injection 都必须保持脱敏、长度/深度限制、Group/用户隔离；算法适配器不得绕过这些边界 |

## 统一分层归属

| 层 | 负责什么 | 允许依赖 | 禁止承担 |
|---|---|---|---|
| Domain | `MemoryScope`、owner、relation、safety、状态不变量 | 标准库和纯领域类型 | LLM、SQLite、Chroma、HTTP、全局 singleton |
| Contracts / Ports | command/query/result、repository、algorithm、projection、ACL 契约 | Domain | 具体算法和具体存储实现 |
| Application | observe、recall、forget、case、experience、skill、personal authorization | Ports、Domain | 直接 import adapters、legacy host 或具体数据库 |
| Adapters | Mem0、Graphiti、RRF/MMR、Voyager、Letta、AutoGen 的可替换算法实现 | Ports、第三方 SDK/模型 | 决定租户权限、事务边界、canonical schema |
| Infrastructure | SQLite、Chroma projection、outbox、schema、sweeper、secret provider | Ports、外部资源 | 解释业务策略或直接替代 Application authorization |
| Bootstrap / Composition | 选择具体实现、组装依赖、管理生命周期 | 所有外层实现 | 被业务代码反向依赖 |

## 结论

1. **Canonical SQLite 是事实源**；Chroma、FTS、Graph 都是可重建 projection。
2. **论文算法是能力插件，不是系统边界**；权限、隔离、事务、脱敏和预算必须由 Nuke 自己的 Domain/Application 层强制执行。
3. 当前最值得继续投入的不是再增加算法，而是：Graph 规模化检索、reflection 质量评估、projection 全量对账、legacy fallback 使用量归零，以及基于真实线上数据的 Recall/Reuse/Conflict 指标。
4. “已在线”只表示代码已接入生产组合根，不代表达到对应论文的实验规模、数据集或 benchmark 结果。
