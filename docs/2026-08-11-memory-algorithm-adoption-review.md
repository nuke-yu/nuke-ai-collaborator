# Memory 算法吸收与实现边界复核（2026-08-11）

本文记录 Nuke AI Collaborator 对外部 Memory 项目的实际吸收情况。结论基于本地论文、参考项目源码/设计文档，以及 Nuke 当前代码；不把“存在算法 Adapter”误写成“生产链路已经完整接入”。

## 证据范围与引用规则

- 论文证据：`/Users/Nuke/agent_memory_design/papers/` 下的本地 PDF，以 PDF 页码和章节定位。
- 参考源码证据：`/Users/Nuke/agent_memory_design/code/` 下的项目源码和设计文档。
- Nuke 代码证据：本仓库相对路径和行号。
- 本地未发现独立论文 PDF 的项目（EverOS、AutoGen Task-Centric Memory、LangGraph Checkpoint、OpenMemory），只引用其源码或技术文档，不虚构论文结论。
- 论文中的 benchmark 数字只代表论文实验环境，不代表 Nuke 的性能指标。

## 1. Mem0：事实抽取与 ADD / UPDATE / DELETE / NOOP

参考论文：[Mem0](</Users/Nuke/agent_memory_design/papers/2504.19413-Mem0.pdf>)，PDF p.3 §2、p.4 Figure 2/§2.1、p.21 Appendix B Algorithm 1。

论文描述的流程是：从最近对话中抽取候选事实，检索相似的已有记忆，再由 LLM 决定 `ADD`、`UPDATE`、`DELETE` 或 `NOOP`。其优势是只保留对未来有用的事实，避免每轮重放完整历史，并可对冲突事实进行增量修正。论文在 LOCOMO 上报告了 Mem0/Mem0g 的准确率和延迟优势（PDF p.13–14 §4.3–§4.5），这些数字不应写成 Nuke 指标。

参考源码与 Nuke 实现：

- `backend/memory/adapters/algorithms/mem0_fact_engine.py:16`：四种动作类型。
- `backend/memory/adapters/algorithms/mem0_fact_engine.py:33`：对应的决策 Prompt。
- `backend/memory/adapters/algorithms/mem0_fact_engine.py:88`：事实冲突协调。

当前状态：生产 durable observation pipeline 的 fact stage 会进入 `add_to_chroma()`；该函数通过 `ConflictResolver.resolve_batch()` 调用 Mem0 冲突决策，并把 ADD/UPDATE/DELETE/NOOP 和原始 fact index 写入 canonical metadata（`backend/ai/memory.py:306-445,558-730`）。因此 Mem0 已在线于事实冲突与双写路径；`legacy.py` 仍只是 durable pipeline 的入口，不应被误读为算法未接线。

## 2. EverOS：Run → Case → Experience → Skill

参考文档：[EverOS How Memory Works](</Users/Nuke/agent_memory_design/code/EverOS/docs/how-memory-works.md>)。

本地没有发现 EverOS 独立论文 PDF。文档明确描述：Markdown 是事实源，SQLite/LanceDB 是可重建索引（`docs/how-memory-works.md:24-39`）；记忆按 `app_id/project_id` 隔离（`47-51`）；消息经过 buffer、边界检测、MemCell 抽取，再同步写 Episode、异步生成 atomic facts、foresight、profile、agent case 和 agent skill（`102-146`、`148-162`）。

工程优势是原始记忆与索引分离、写入和高级蒸馏解耦、支持多级抽象和空间隔离。这些是根据源码/架构推导的工程优势，不是论文实验结论。

Nuke 对应代码：

- `backend/ai/cases.py:130`：从运行轨迹组装 Case。
- `backend/ai/experiences.py:59`：有纠正证据且验证成功时才蒸馏 Experience。
- `backend/ai/pipeline.py:251`：评估 Case 并触发蒸馏。

当前边界：Nuke 的 Case → Experience → Skill 主链路已在线，`compile_candidate()` 会执行 Case 聚类、蒸馏资格门控并保存 cluster provenance 与 induction artifact。没有完整复刻 EverOS 的 OME/Markdown 源事实体系和独立反思调度；当前实现是 Nuke 原生 durable pipeline 吸收其分层思想。

## 3. AutoGen Task-Centric Memory：失败 → Insight → 复用

参考源码：

- `code/autogen/python/packages/autogen-ext/src/autogen_ext/experimental/task_centric_memory/memory_controller.py:135-189`。
- `code/autogen/python/packages/autogen-ext/src/autogen_ext/experimental/task_centric_memory/memory_controller.py:191-230`。
- `code/autogen/python/packages/autogen-ext/src/autogen_ext/experimental/task_centric_memory/_prompter.py:100-154`。
- `code/autogen/python/packages/autogen-ext/src/autogen_ext/experimental/task_centric_memory/_prompter.py:228-252`。

本地没有发现该模块的独立论文 PDF。源码明确实现：分析预期答案、错误答案和工作历史；提炼错误背后的 misconception；压缩为简短通用 Insight；按主题索引；在后续任务中检索；并可单独判断 Insight 是否有用。

Nuke 对应代码：

- `backend/ai/cases.py:130-155`：保存错误、工具轨迹和纠正证据。
- `backend/ai/experiences.py:59-80`：仅对完成且验证成功、存在纠正信号的 Case 蒸馏。
- `backend/memory/adapters/algorithms/autogen_failure_engine.py:57`：失败分类和纠正建议。

当前状态：失败工具调用会在 Tool Loop 中经脱敏、去重后注入 AutoGen 风格 corrective insight；失败 Case 仍要求真实纠正证据且验证成功才可蒸馏 Experience。`AutoGenFailureEngine.run_with_retry()` 也提供可复用的 `Retry → Validate → Store` 前置闭环，但 Nuke 不会对所有工具失败无条件自动重试。

## 4. Graphiti / Zep：时序知识图谱与事实失效

参考论文：[ZEP: A Temporal Knowledge Graph Architecture for Agent Memory](</Users/Nuke/agent_memory_design/papers/2501.13956-Graphiti.pdf>)，PDF p.1 Abstract、p.2 时序图谱、p.3 §2.2.3、p.4–5 §3 Search、p.7 LongMemEval。

论文描述：事实和关系带有有效时间；新关系出现时使旧关系失效而不破坏历史；搜索阶段结合事实、实体、社区信息，并执行召回、重排和上下文构造。论文报告的 DMR、LongMemEval 和延迟结果是 Zep 实验系统结果，不是 Nuke 指标。

参考源码：`code/graphiti/graphiti_core/nodes.py`、`edges.py`、`utils/maintenance/combined_extraction.py`、`search/search.py`。

Nuke 对应代码：

- `backend/memory/adapters/algorithms/graphiti_temporal_engine.py:34,55,86,107`：轻量时序节点/关系和冲突边失效。
- `backend/memory/application/bot_facts.py:146,205`：事实 supersede 与 `valid_to`。

当前边界：已实现实体 alias 规范化、时序失效、历史保留和带 `as_of`/`max_hops` 的有界图遍历。尚未实现 Graphiti 同等规模的 LLM 实体抽取、社区发现和完整 hybrid graph search。

## 5. RRF / MMR：多路召回融合与去冗余

Graphiti/Zep 论文在 PDF p.5 §3.1 讨论召回与重排，并在 PDF p.11 引用 RRF 和 MMR 的原始论文。RRF 的工程优势是融合 lexical/vector/graph 等多路排序而不要求分数同尺度；MMR 的优势是在相关性和结果多样性之间平衡，减少重复记忆。

Nuke 实现：`backend/memory/adapters/algorithms/hybrid_rerank_engine.py:16,23,49,109`。

当前线上 Experience Recall 已使用 RRF/MMR；线性分数只用于候选阈值过滤：

```text
0.45 × lexical + 0.35 × vector + 0.20 × cluster
```

位置：`backend/ai/experiences.py:467-530`。关键词、向量、cluster 三路排名进入 `HybridRerankEngine`，再执行 RRF 与 MMR。当前没有加载 cross-encoder 模型，不能宣称拥有 cross-encoder rerank。

## 6. Voyager：成功轨迹 → Skill 与 Critic 门控

参考论文：[Voyager](</Users/Nuke/agent_memory_design/papers/2305.16291-Voyager.pdf>)，PDF p.1 Abstract、p.2 Figure 2、p.3–6 Skill generation/verification、p.7 实验结果。

论文的三个核心组件是 Automatic Curriculum、持续增长的可执行 Skill Library、以及利用环境反馈/执行错误/自我验证的迭代 Prompting。只有任务完成并通过验证后，程序才进入 Skill Library（PDF p.3–6）。论文的 Minecraft 结果不能直接外推到 Nuke。

Nuke 对应代码：

- `backend/memory/adapters/algorithms/voyager_critic_engine.py:36,84`：独立 Critic Engine。
- `backend/ai/skill_learning.py:81,165-219`：候选生成、试用和晋升。
- `backend/ai/usage_tracking.py:89-180`：成功/失败复用反馈和暂停逻辑。

当前边界：Nuke 已吸收成功门控、Skill 生命周期、真实复用反馈，并在 Skill 编译前执行 Critic gate；`VoyagerCriticEngine.build_curriculum()` 还提供依赖拓扑与难度排序。Skill 仍是声明式经验策略，不是 Voyager 式可执行代码 Skill Library。

## 7. LangGraph Checkpoint：可恢复状态与 Durable Execution

本地没有 LangGraph 独立论文 PDF，以下引用 Checkpoint 源码：

- `code/langgraph/libs/checkpoint/langgraph/checkpoint/base/__init__.py:146,300,374,429,468,491,560`。
- `code/langgraph/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/aio.py:38,346,509,561,602`。

源码明确提供 `get_tuple`、`put`、`put_writes`、`pending_writes`、异步 SQLite Saver、线程删除和 checkpoint prune。这些机制的工程优势是流程中断可恢复、状态写入与工具写入可分离，并支持失败重试和历史裁剪。

Nuke 对应代码：`backend/memory/adapters/runtime/learning_legacy.py:186-207`（durable job、lease、幂等和失败恢复）。

当前状态：Nuke 的 durable pipeline 已持久化 checkpoint、parent、state hash/state JSON，并提供 latest、prune、thread delete；新增 `memory_checkpoint_pending_writes`、pending write acknowledge 和 checkpoint fork API。它仍不是 LangGraph 官方 SQLite Saver，resume 由 Nuke 的 lease/job worker 负责。

## 8. Letta / MemGPT：分层记忆与上下文预算

参考论文：[MemGPT](</Users/Nuke/agent_memory_design/papers/2310.08560-MemGPT.pdf>)，PDF p.1 Abstract、p.2 Main/External Context、p.3 Figure 3、p.4 Control Flow、p.6–7 Archival Memory。

论文将 Agent 上下文类比为操作系统内存：工作上下文是有限的，长期记忆位于外部存储，模型通过函数主动读取、写入、驱逐和检索。优势是突破固定上下文窗口，并支持长时间任务。

Nuke 对应代码：

- `backend/memory/adapters/algorithms/letta_acl_engine.py:39`：预算和 ACL 相关逻辑。
- `backend/ai/experiences.py:491-495`：字符预算截断。
- `backend/ai/personal_vault.py:373`：Personal Vault 上下文预算。

当前边界：Nuke 已实现分层注入、工具 schema 后的最终预算裁剪、超限输出 token clamp，以及可选 provider tokenizer。仍没有完整复刻 MemGPT/Letta 的主动 memory function、paging/eviction 和独立 Archival Memory runtime。

## 9. OpenMemory：个人记忆隔离、ACL 和访问审计

参考源码：

- `code/mem0/openmemory/api/app/utils/permissions.py:8-53`。
- `code/mem0/openmemory/api/app/models.py:132-188`。

本地没有发现 OpenMemory 独立论文 PDF。源码明确实现：Memory 必须 active、App 必须 active、访问必须通过 App-specific access control；数据模型包含 `AccessControl(subject/object/effect)`、`MemoryStatusHistory` 和 `MemoryAccessLog`。

Nuke 对应代码：

- `backend/memory/bootstrap.py:121-125`：ACL Adapter 接入 composition root。
- `backend/memory/application/authorized_personal.py:88`：授权个人记忆边界。
- `backend/ai/personal_vault.py:386`：访问/使用记录。

当前边界：Nuke 已吸收 Personal Vault、隔离、导出/删除/重建、持久化 subject/object/effect 规则、通配匹配、显式 ABAC deny 和无内容审计。仍未复刻 OpenMemory 的 App active 状态机及完整 ORM/HTTP 层。

## 10. 补充论文：Reflexion 与 Generative Agents

### Reflexion

参考论文：[Reflexion](</Users/Nuke/agent_memory_design/papers/2303.11366-Reflexion.pdf>)，PDF p.2、p.4 Algorithm 1、p.5、p.9 Limitations。

论文把环境反馈转成语言化反思，并将反思写入下一轮可读取的经验记忆。它支持 Nuke 的“失败必须变成可复用纠正证据”的设计，但论文也指出长期记忆窗口和局部最优等限制。

### Generative Agents

参考论文：[Generative Agents](</Users/Nuke/agent_memory_design/papers/2304.03442-GenerativeAgents.pdf>)，PDF p.9 Memory Retrieval、p.10 Reflection。

论文的召回分数结合 recency、importance、relevance，并支持周期性 reflection。Nuke 当前 `lexical + vector + cluster + confidence` 的召回与其思想相近，但不是同一实现；Nuke 也没有实现 Generative Agents 的完整 reflection tree。

## 最终状态摘要

| 算法 | 已吸收的主要能力 | 当前未完成部分 |
|---|---|---|
| Mem0 | 事实级 ADD/UPDATE/DELETE/NOOP 决策、canonical 双写 | 原始 Mem0 LLM extractor/prompt 不是唯一事实抽取器 |
| EverOS | Case → Experience → Skill、cluster provenance、induction artifact | OME/Markdown 源事实和独立反思调度 |
| AutoGen | 失败分析、工具循环 corrective insight、验证门控 | 不对所有失败自动重试，需显式 retry policy |
| Graphiti/Zep | 时序事实、alias、关系失效、有界图遍历 | LLM 实体抽取、社区发现、完整 Hybrid Search |
| RRF/MMR | 关键词/向量/cluster 三路融合与去冗余 | 没有 cross-encoder |
| Voyager | Critic 成功门控、Skill 生命周期、复用反馈、依赖式 Curriculum 排序 | 可执行代码 Skill Library |
| LangGraph | Durable job、lease、checkpoint、pending writes、fork/prune | 不是官方 Saver，resume 由 Nuke worker 恢复 |
| Letta/MemGPT | 分层记忆、按需注入、上下文预算 | 主动 paging、function memory、真实 tokenizer |
| OpenMemory | Personal Vault、ACL、隔离、审计 | 完整 ABAC 数据模型和访问日志 |

### 2026-08-11 实现进展补充

本次代码落地后，以下边界已经从“独立 Adapter”进入生产或持久化链路：

- RRF/MMR：关键词、向量、cluster 三路已接入 Experience Recall；
- Letta/MemGPT：记忆注入前和工具 Schema 确定后均执行上下文预算；
- Mem0：canonical fact metadata 保存 `mem0_action`；
- AutoGen：失败 Insight 已接入 Tool Loop，失败 Case 持久化，并提供异步 retry→validate 闭环；
- EverOS：Case cluster provenance 和 Skill induction artifact 写入 Skill declaration；
- Voyager：Skill 编译前执行 Critic gate；
- Graphiti：时间点关系查询、有限深度关系遍历、实体 alias 规范化；
- LangGraph：durable pipeline job 保存 checkpoint、parent、state hash/state JSON，并支持 pending writes、acknowledge、fork、prune；
- OpenMemory：个人 ACL 的允许/拒绝决策均进入无内容审计表。

仍需继续增强的部分主要是：LLM 级实体抽取/解析、跨多跳图的混合向量检索、MemGPT 主动 memory function/paging、OpenMemory App 状态机，以及按策略选择性自动 retry。

### 2026-08-12 继续实现与验证

- AutoGen：Tool Loop 失败结果经过 `redact_secrets()` 后生成一次去重的 corrective insight，作为历史证据注入下一轮模型上下文；不会把工具错误当成新的用户指令。
- LangGraph：新增 `memory_checkpoint_pending_writes` 表，以及 `put_pending_write()`、`list_pending_writes()`、`acknowledge_pending_writes()`、`fork_checkpoint()`。
- RRF/MMR：Experience Recall 的 keyword、vector、semantic-cluster 三路结果分别参与 RRF，再由 MMR 去重。
- Mem0：补充 NOOP 回归测试，确认整批事实均为 NOOP 时 canonical 和 Chroma 均不写入。
- 验证：本轮 memory 算法与接线回归测试 93 项通过；Tool Loop 失败洞察新增测试 2 项，LangGraph pending write/fork 新增测试 2 项。

### 后续继续实现

- Letta/MemGPT：预算引擎接受 provider tokenizer 或 `encode()` 对象；可用时按真实 token 数计算并二分截断，失败自动回退保守估算。
- OpenMemory：`personal_access_controls` 支持 subject/object 通配符；精确规则优先，同等 specificity 下显式 deny 优先，并保留无内容审计。
- Graphiti：增加确定性的实体候选抽取和 alias/entity 解析。候选只用于后续关系验证，不会未经证据直接写入知识图谱。
- Voyager：增加无副作用的依赖拓扑 Curriculum 排序；检测到循环依赖时 fail-closed。
- OpenMemory：增加用户隔离的 App 注册、active/inactive 生命周期查询。
- Graphiti：关系邻居已作为第四路进入 Experience Recall 的 RRF/MMR。
- Letta/MemGPT：增加按 importance/recency 分页 archival records 的纯函数选择器。
- AutoGen：retry helper 支持按 FailureCategory 配置可重试范围，避免权限类错误自动重试。
- Letta/MemGPT：增加显式 `memory_read()` / `memory_write()` 控制器，提供有界 lexical archival read 与去重 working-memory write。
- Graphiti：增加近似实体消歧和 active temporal graph connected-component 社区发现。
- Voyager：Skill declaration 拒绝 code/python/shell 等执行字段及 subprocess/eval/curl 等隐式执行指令。
- Voyager：新增 `SkillExecutionPlan`，只生成可审计的步骤、工具白名单、验证步骤和 HIL 标记；计划本身不执行代码。
- Skill 编译链路会将该 plan 持久化到 `execution_plan`，执行器可据此做二次授权与验证。
- Graphiti：增加可选 LLM entity candidate extractor；解析失败回退确定性抽取，仍不直接创建关系。
- OpenMemory：增加无内容 ACL 审计查询接口，支持分页上限并保持用户隔离。
- OpenMemory：`CreatePersonalProjection` / `FormatProjectedContext` 支持 `app_id`，projection 与读取均对 inactive/unregistered app fail-closed。
- OpenMemory：增加用户隔离的 App 列表查询，可筛选 active 状态供管理与审计界面使用。
- OpenMemory：新增 `/api/personal/memory/apps` 注册、列表、状态管理和 `/api/personal/memory/audit` 审计查询接口。
- EverOS：Skill 编译时持久化 Experience/Case source snapshot，支持后续索引重建和蒸馏恢复。
- Letta/MemGPT：Tool Loop 上下文超限时先对 learned contexts 执行 paging，再进行 tokenizer/估算器截断。
- Graphiti Adapter：正式暴露 entity candidate extraction、entity disambiguation、community discovery 能力。

这份文档的核心原则是：可以说“吸收了某算法的设计思想或局部机制”，但只有在存在生产 composition root 接线和端到端调用证据时，才可以说“该算法已在线”。

---

## 11. 先看总结：每个算法到底要解决什么问题

如果只先记住一句话，可以按下面理解：

| 算法/项目 | 核心目标 | 它主要回答的问题 | 主要产物 |
|---|---|---|---|
| Mem0 | 把对话转成可维护的原子事实 | 这条新信息应该新增、修改、删除，还是忽略？ | Fact record + ADD/UPDATE/DELETE/NOOP |
| EverOS | 把一次运行逐层沉淀成长期能力 | 一次任务经历如何逐渐变成经验和技能？ | Episode → Case → Experience → Skill |
| AutoGen | 从失败中提炼下次可复用的建议 | 这次为什么失败，下次应该记住什么？ | Insight / corrective advice |
| Graphiti/Zep | 保存事实的变化过程 | 这个事实什么时候成立，什么时候失效？ | 带有效期的实体关系和历史 |
| RRF | 融合多种召回结果 | 关键词、向量、图检索的结果如何合并？ | 综合排序列表 |
| MMR | 避免返回重复记忆 | 如何在相关的同时让上下文内容不重复？ | 多样化的 Top-K 结果 |
| Voyager | 把验证成功的行为变成可复用 Skill | 一个成功动作如何被保存、检索和组合？ | 可执行 Skill library |
| LangGraph Checkpoint | 让长任务可恢复 | 进程崩溃后从哪里继续，而不是重新开始？ | Checkpoint + pending writes |
| Letta/MemGPT | 管理有限上下文和长期记忆 | 什么放在当前上下文，什么放到外部记忆？ | Working context + archival memory |
| OpenMemory | 控制个人记忆的访问边界 | 谁可以读取哪条记忆，访问是否可审计？ | ACL decision + access log |
| Reflexion | 把失败反馈写成下一轮能读懂的反思 | 失败结果如何影响下一次尝试？ | Verbal reflection memory |
| Generative Agents | 按多种因素选择记忆并周期性反思 | 当前最值得注入上下文的记忆是哪几条？ | Ranked memories + reflections |

### 这些目标如何拼成一个完整 Memory 系统

可以把它们看成四层，而不是十三个互相独立的功能：

```text
形成层：Mem0 / EverOS / AutoGen / Voyager / Reflexion
  决定什么值得保存，以及如何从经历中学习

组织层：Graphiti / OpenMemory
  决定记忆如何保留时间、关系、权限和审计信息

检索层：RRF / MMR / Generative Agents
  决定从大量记忆中找哪些，并如何去重排序

运行层：Letta/MemGPT / LangGraph Checkpoint
  决定记忆如何进入有限上下文，以及任务如何中断恢复
```

Nuke 的 Memory 不是某一个算法的复制品，而是这四层的组合：

```text
运行轨迹
 → Mem0/事实与 Case 抽取
 → AutoGen/Reflexion 风格失败总结
 → EverOS 风格 Experience/Skill 蒸馏
 → Graphiti 风格历史保留
 → RRF/MMR 风格召回排序
 → Letta 风格预算注入
 → OpenMemory 风格权限检查
 → LangGraph 风格 Durable 学习任务
```

下面再进入每个算法的具体输入、判断步骤和代码实现。

---

## 11.1 论文算法摘要：先看论文到底提出了什么算法

这一节只总结论文中能直接定位到的算法流程。它和后面的“Nuke 当前实现”是两件事：前者回答“原论文怎么做”，后者回答“Nuke 做到了哪一步”。

### Mem0 论文 Algorithm 1：Memory Update Algorithm

位置：`2504.19413-Mem0.pdf`，PDF p.4 Figure 2、p.21 Appendix B Algorithm 1。

论文算法可以还原成下面的伪代码：

```text
输入：conversation C，已有记忆集合 M

1. 从 C 中抽取候选事实 F
2. 对每个事实 f ∈ F：
   a. 用 embedding(f) 检索 M 中最相似的旧记忆 R
   b. 将 f、R、对话摘要和最近消息交给 LLM
   c. LLM 选择一个操作：
      ADD    → 新建记忆
      UPDATE → 修改 R 的内容
      DELETE → 删除/失效 R
      NOOP   → 不改变记忆
3. 执行所有操作，得到新的 M
4. 返回更新后的记忆集合和操作历史
```

关键点有两个：

- 检索只是为了找到可能冲突或相似的旧记忆，最终动作由 LLM 决定；
- `UPDATE` 和 `DELETE` 是事实维护能力，区别于只追加文本的向量数据库。

论文中的“记忆”不是一次完整对话，而是被抽取、比较和合并后的短事实。例如：

```text
旧记忆：用户使用 Python 3.11
新事实：用户升级到 Python 3.12
论文算法输出：UPDATE(旧记忆 → 新事实)
```

Nuke 的 `FactActionType` 和 Prompt 对应这一算法，但生产观察链路目前没有直接调用该 Engine，详见第 1 节。

### Graphiti/Zep 论文算法：时序事实更新与混合检索

位置：`2501.13956-Graphiti.pdf`，PDF p.2–5 §2.2.3、§3，p.10 Appendix prompts。

论文的时序更新过程可以概括为：

```text
输入：新 Episode E，当前实体/关系图 G

1. 从 E 中抽取实体节点和关系边
2. 为新事实提取 valid_at / invalid_at
3. 查找与新边语义冲突的 active 旧边
4. 将冲突旧边设置为 invalid_at，不物理删除
5. 写入新边，并保留 created/expired 等交易时间
6. 搜索时：
   a. 高召回检索事实、实体和社区候选
   b. 使用 reranker 重排
   c. 按有效时间和当前查询构造上下文
```

论文的核心不是“使用图数据库”本身，而是让答案能够区分：

```text
现在成立的事实
某个历史时点成立的事实
已经被新事实取代的事实
```

### Voyager 论文算法：自动课程 + 代码 Skill 迭代

位置：`2305.16291-Voyager.pdf`，PDF p.2 Figure 2、p.3–6。

论文流程：

```text
输入：当前环境状态 S，目标/里程碑，已有 Skill Library

1. Curriculum 选择下一个可探索任务 T
2. 检索与 T 相关的已有 Skill
3. LLM 根据任务、状态、反馈和 Skill 生成代码动作 A
4. 执行 A，获得环境反馈和执行错误
5. Critic 检查任务是否完成，并生成修改意见
6. 若未完成：带着错误和 Critic 反馈重新生成 A
7. 若完成并通过 self-verification：
   将可执行代码和描述写入 Skill Library
8. 后续任务通过 embedding 检索并组合这些 Skill
```

论文因此把 Skill 定义为“经过环境验证的可执行程序”，而不是普通文字总结。这也是 Nuke 当前 Skill 与 Voyager Skill 的主要差异。

### MemGPT 论文算法：虚拟上下文管理

位置：`2310.08560-MemGPT.pdf`，PDF p.2–4、p.6–7。

论文的核心控制循环是：

```text
输入：有限大小的 working context W，外部 recall/archival storage A

1. 将系统指令、当前任务和最近消息放入 W
2. LLM 判断是否需要读取、写入或驱逐记忆
3. 通过 function call：
   - 从 A 检索历史记忆
   - 将 W 中的内容写入 A
   - 清理 W 中不再需要的内容
4. 若上下文仍不足，通过 heartbeat/function chaining 继续处理
5. 将与当前任务相关的内容重新放回 W
```

论文的“算法”重点是分页和主动上下文管理：模型不是被动接受截断，而是通过函数调用参与决定哪些内容留在工作区。

### Reflexion 论文 Algorithm 1：语言化反思记忆

位置：`2303.11366-Reflexion.pdf`，PDF p.2、p.4 Algorithm 1、p.5。

```text
for episode = 1 ... N:
    trajectory = agent 执行任务
    feedback = evaluator(trajectory)
    if feedback 表明成功:
        返回成功
    reflection = self_reflection(trajectory, feedback)
    memory = memory ∪ {reflection}
    下一轮执行时，将 memory 注入 prompt
```

Reflexion 的关键转变是：把二值/标量反馈转换成语言化经验，供下一轮模型读取。它不是通过梯度更新参数，而是通过外部文字记忆改变下一次推理。

### Generative Agents 论文算法：记忆召回与周期性 Reflection

位置：`2304.03442-GenerativeAgents.pdf`，PDF p.8–10。

单条记忆的召回分数由三个因素组成：

```text
retrieval_score(m, q)
  = w_r × recency(m)
  + w_i × importance(m)
  + w_v × relevance(m, q)
```

其中：

- `recency`：近期记忆衰减更慢；
- `importance`：由 LLM 评估这条事件的重要程度；
- `relevance`：记忆与当前查询的 embedding 相似度。

周期性 Reflection 流程是：

```text
累计重要性达到阈值
 → 生成需要思考的问题
 → 检索相关记忆作为证据
 → LLM 生成带引用的高层 Insight
 → 将 Insight 再写回记忆流
```

Nuke 当前的 lexical/vector/cluster/confidence 排序与这种多因素召回思想相近，但没有完整实现论文中的 importance 评分和 reflection tree。

### EverOS 源码算法摘要（本地无独立论文 PDF）

来源：`code/EverOS/docs/how-memory-works.md:102-146`、`148-162`。

```text
POST /add 或 /flush
  → 按 session/app/project 放入 SQLite buffer
  → boundary detector 判断是否形成一个记忆单元
  → 一次 LLM extraction 生成 MemCell
  → UserMemoryPipeline 同步写 Episode Markdown
  → AgentMemoryPipeline 异步运行 OME strategies
       ├─ atomic facts
       ├─ foresight
       ├─ profile
       ├─ agent case
       └─ agent skill
  → cascade daemon 监听 Markdown 变化
  → 更新 SQLite 状态和 LanceDB 检索索引
```

源码实现的关键不是单个分类器，而是“同步事实落盘 + 异步高级蒸馏 + 可重建索引”的流水线。Markdown 是 source of truth，SQLite/LanceDB 是可重建派生状态（`docs/how-memory-works.md:24-39`）。

### AutoGen Task-Centric Memory 源码算法摘要（本地无独立论文 PDF）

来源：`code/autogen/.../memory_controller.py:135-189,191-230`，`_prompter.py:100-154,228-252`。

```text
train_on_task(task, expected_answer)
  → 调用 task_assignment_callback 执行任务
  → grader 判断答案是否正确
  → 失败时把 task、expected answer、错误 response、work history 交给 LLM
  → 分析 misconception
  → 压缩成一两句通用 insight
  → generalize task + extract topics
  → memory_bank.add_memo(insight, topics, task)

test_on_task(task)
  → 按 task 主题检索相关 memo
  → 将 memo 拼入 task prompt
  → 执行任务并再次 grader 验证
```

源码还提供 `validate_insight()`：要求模型只返回 `1/0`，判断 Insight 对给定任务是否可能有用。它的核心产物不是错误日志，而是带主题索引、可以在后续任务中检索的通用建议。

### LangGraph Checkpoint 源码算法摘要（本地无独立论文 PDF）

来源：`code/langgraph/libs/checkpoint/langgraph/checkpoint/base/__init__.py` 和 `code/langgraph/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/aio.py`。

```text
任务执行产生 checkpoint C
  → aput(C) 保存线程状态、channel versions 和父 checkpoint
工具/节点产生中间写入 W
  → aput_writes(W) 保存 pending writes
进程恢复
  → aget_tuple(config) 找到最新 checkpoint
  → 将 pending_writes 合并/继续执行
任务完成
  → aput(新 checkpoint)
历史过长或线程删除
  → aprune()/adelete_thread()
```

这里的“记忆”是可恢复的工作流状态，不是用户事实。`pending_writes` 解决了“工具已经写入但最终 checkpoint 尚未完成”这一崩溃窗口；`prune` 负责清理旧检查点和分支。

### OpenMemory 源码算法摘要（本地无独立论文 PDF）

来源：`code/mem0/openmemory/api/app/utils/permissions.py:8-53`、`app/models.py:132-188`。

```text
请求访问 memory(app_id, memory_id)
  → memory.state == active？否则拒绝
  → app 是否存在？不存在则拒绝
  → app.is_active？暂停则拒绝
  → 计算 app 可访问的 memory id 集合
  → memory 是否在集合中？
      ├─ 是：允许读取并记录 MemoryAccessLog
      └─ 否：拒绝
```

数据模型还保存 `AccessControl(subject/object/effect)`、`MemoryStatusHistory` 和 `MemoryAccessLog`。因此它的重点不是召回质量，而是“记忆读取前的授权”和“读取后的可审计性”。

### RRF/MMR 的代码算法摘要

虽然 RRF/MMR 有论文来源，但 Nuke 当前最需要理解的是它们在代码中的行为：

- `hybrid_rerank_engine.py` 将多路结果转换为排名，再按 RRF 公式累加各路排名贡献；
- MMR 先选择相关结果，再对与已选结果相似的候选施加惩罚；
- 最终返回受 `limit` 约束的排序结果。

这两个算法目前存在于独立 Engine，`backend/ai/experiences.py:467-481` 的生产召回仍使用 lexical/vector/cluster 线性加权。

---

## 12. 实现细节导读：把抽象算法还原成具体动作

下面用“输入 → 中间判断 → 输出”的方式解释每个算法。例子是为了帮助理解算法动作，不代表论文 benchmark，也不代表 Nuke 一定已经执行了全部步骤。

### 11.1 Mem0：它不是“存一句话”，而是做一次事实合并

假设已有记忆：

```text
M1: 用户使用 Python 3.11
```

新消息：

```text
我已经把 Python 升级到 3.12 了。
```

Mem0 的具体步骤：

1. 把新消息拆成候选事实：`用户使用 Python 3.12`。
2. 用候选事实检索相似记忆，找到 M1。
3. 让决策模型比较新旧事实。
4. 输出：

```json
{
  "action": "UPDATE",
  "target_record_id": "M1",
  "old_content": "用户使用 Python 3.11",
  "fact": "用户使用 Python 3.12"
}
```

如果消息是“我不再使用 Python”，则可能是 `DELETE`；如果是“谢谢”，则不产生事实，输出 `NOOP`；如果是“用户在 Windows 上使用 Python 3.12”，则可能是 `ADD`。

Nuke 的 `Mem0FactEngine.reconcile_fact()` 做的就是这类比较：先规范化文本，再检查显式否定、相似记录和冲突记录（`backend/memory/adapters/algorithms/mem0_fact_engine.py:88-115`）。它不是直接把整段对话放进向量库，而是先把对话转换成可更新的事实记录。

### 11.2 EverOS：它把一次运行逐层压缩，而不是立刻生成 Skill

可以把 EverOS 理解为一个异步编译器：

```text
原始消息
  → Episode（发生了什么）
  → Atomic Fact（稳定事实）
  → Case（一次任务如何完成）
  → Experience（从案例中学到什么）
  → Skill（以后可以复用的做法）
```

具体例子：用户让 Bot 修改配置，第一次失败，第二次通过。

```text
Episode:
  用户要求修改配置，第一次路径写错，第二次修正后成功。

Case:
  task = 修改配置
  errors = 路径不存在
  correction = 先检查文件是否存在
  verification = 测试通过

Experience:
  修改配置前先确认目标文件存在，并读取当前格式。

Skill candidate:
  配置文件修改前置检查流程。
```

EverOS 文档中的关键点是：Episode 先同步写入，Atomic Fact/Profile/Case/Skill 后续异步生成（`code/EverOS/docs/how-memory-works.md:102-146`）。这样主请求不必等待所有高级记忆完成。

Nuke 的对应实现是：`assemble_case()` 收集工具、文件、错误和结果（`backend/ai/cases.py:130-155`），`distill_case()` 只处理有纠正信号的已完成 Case（`backend/ai/experiences.py:59-80`），然后由 pipeline 继续生成 Experience/Skill（`backend/ai/pipeline.py:251-270`）。

### 11.3 AutoGen：它把“错误日志”改写成“下一次可执行的提醒”

普通错误记录通常是：

```text
第 1 次失败：答案错误。
```

AutoGen Task-Centric Memory 想得到的是：

```text
遇到这类题时，先区分题目要求的统计口径，再进行计算，不要直接套用上一个公式。
```

其具体步骤：

1. 输入任务、标准答案、错误答案和完整工作历史。
2. LLM 先分析哪里做对、哪里做错。
3. 再问“导致错误的 misconception 是什么”。
4. 把结果压缩成一两句通用建议。
5. 对 Insight 做可用性判断，只返回 `1` 或 `0`。
6. 将 Insight 按任务主题索引，后续相似任务检索出来。

源码位置：`_prompter.py:100-154`、`_prompter.py:228-252`、`memory_controller.py:135-189`、`memory_controller.py:191-230`。

Nuke 中的对应物不是“保存所有错误”，而是 `correction_evidence_json`、错误列表、尝试记录和验证结果；Tool Loop 还会把脱敏后的失败洞察注入下一轮。只有验证成功后才蒸馏 Experience。Nuke 提供 retry helper，但是否自动重试由执行策略决定。

### 11.4 Graphiti：重点不是“有一张图”，而是“旧关系什么时候失效”

假设用户先说：

```text
用户住在上海。
```

后来又说：

```text
用户搬到了杭州。
```

普通 KV 存储可能直接覆盖 `city = 杭州`，历史丢失。Graphiti 的关系思路是保存两条带时间的边：

```text
用户 --住在--> 上海
  valid_at = t1
  invalid_at = t2

用户 --住在--> 杭州
  valid_at = t2
  invalid_at = null
```

查询“用户现在住哪里”只取 active 边；查询“去年住哪里”则按时间过滤。

论文中的时间字段和边失效规则见 PDF p.2–3 §2.2.3。Nuke 的 `GraphitiTemporalEngine` 也会在新冲突边加入时失效旧 active 边（`backend/memory/adapters/algorithms/graphiti_temporal_engine.py:55-107`），Bot Fact 层则通过 supersede/`valid_to` 保存事实历史（`backend/memory/application/bot_facts.py:146,205`）。

目前缺少的是前面的“实体理解”：Nuke 还没有完整实现“上海”和“上海市”是否是同一实体、两个提及是否指向同一用户，以及从多跳关系中遍历答案。

### 11.5 RRF 与 MMR：一个解决“多路排序”，一个解决“结果重复”

假设三路检索分别返回：

```text
关键词检索：A, B, C
向量检索：  B, C, D
图检索：    C, E, A
```

RRF 不直接比较三路原始分数，而是按排名累加：

```text
score(item) = Σ 1 / (k + rank_in_each_list)
```

所以同时出现在多路结果中的 C、A、B 会获得较高综合排名。

MMR 则在选出第一个相关结果后，惩罚与已选结果过于相似的候选：

```text
MMR = λ × relevance - (1-λ) × similarity_to_selected
```

这样不会把“同一事实的五种改写”全部塞进有限上下文。

Nuke 已在 `hybrid_rerank_engine.py` 中实现这两个算法，但 `experiences.py:467-481` 当前线上使用的是 lexical/vector/cluster 线性加权，因此应把它们视为“可用但未接线的检索组件”。

### 11.6 Voyager：Skill 不是总结，而是经过验证的可复用动作程序

Voyager 的 Skill 不是一句“以后注意路径”，而是类似：

```python
def inspect_then_edit_config(path, key, value):
    assert file_exists(path)
    data = read_config(path)
    data[key] = value
    write_config(path, data)
    return verify_config(path, key, value)
```

论文流程是：

1. 根据当前环境状态选择任务（curriculum）。
2. 生成代码动作。
3. 执行代码并读取环境反馈/错误。
4. Critic 判断任务是否完成，并提出修改意见。
5. 迭代直到验证成功。
6. 将成功代码按描述 embedding 存入 Skill Library。
7. 下次检索相似 Skill 并组合使用。

位置：Voyager PDF p.3–6。

Nuke 的 Skill 当前更接近“经过验证的经验策略”：`skill_learning.py:81` 生成候选，`165-219` 管理试用/晋升，`usage_tracking.py:89-180` 根据真实复用结果暂停 Skill。它还没有 Voyager 的可执行代码库和自动课程生成。

### 11.7 LangGraph Checkpoint：保存的不是聊天记录，而是可恢复的流程状态

一个可恢复任务至少需要保存：

```text
thread_id
checkpoint_id
父 checkpoint
节点状态
channel versions
pending writes（已合并到 Nuke 的 durable checkpoint 表）
```

例如工具调用成功但进程在写最终结果前崩溃：

1. `put_writes()` 先保存工具写入。
2. 进程重启后 `aget_tuple()` 读取最近 checkpoint。
3. 根据 pending writes 继续执行，而不是重新调用工具。
4. 完成后 `aput()` 写入新的 checkpoint。
5. 历史过长时使用 `prune()` 清理旧分支。

这些能力可在 `langgraph/checkpoint/base/__init__.py:146,300,374,429,468,491,560` 和 SQLite 实现 `sqlite/aio.py:346,509,561,602` 中直接看到。

Nuke 的 durable job/lease 解决的是“学习任务可靠执行”；`langgraph_dag_engine.py` 目前只是 DAG 描述和 hash，不是上述完整 checkpoint 状态机。

### 11.8 Letta/MemGPT：上下文像 RAM，长期记忆像磁盘

假设模型上下文最多容纳 8,000 token：

```text
System prompt        1,500
当前任务              2,000
最近消息              2,500
工具结果              1,000
剩余预算              1,000
```

MemGPT 的做法不是继续截断，而是让模型执行类似：

```text
把旧消息写入 archival memory
从 recall storage 检索与当前任务最相关的三条
把无关内容驱逐出 working context
```

论文的核心是有限 working context 与外部 archival storage 之间的 paging，见 MemGPT PDF p.2–7。

Nuke 当前做的是更简单的预算控制：先按 lexical/vector/cluster 排名，再按 `char_budget` 截断（`backend/ai/experiences.py:491-495`）。这能控制上下文大小，但不会由模型主动管理 memory page，也没有精确 tokenizer 预算。

### 11.9 OpenMemory：权限检查发生在“读记忆之前”

OpenMemory 的访问流程可以具体写成：

```text
请求 app_id + memory_id
  → memory.state 是否 active？
  → app 是否存在且 active？
  → app 是否拥有该 memory 的访问权？
  → 允许读取并记录 MemoryAccessLog
```

源码位置：`utils/permissions.py:8-53`。权限数据模型在 `models.py:132-188`，包括 subject/object/effect、状态历史和访问日志。

Nuke 的 `AuthorizedPersonalKnowledgeService` 在应用边界做 fail-closed 检查（`backend/memory/application/authorized_personal.py:88`），ACL Adapter 在 composition root 装配（`backend/memory/bootstrap.py:121-125`）。区别是 Nuke 当前规则主要存在本地 ACL 矩阵中，而不是 OpenMemory 的完整 ABAC ORM 表结构。

## 13. 阅读这些算法时最容易混淆的三件事

### 算法存在 ≠ 生产接入

`backend/memory/adapters/algorithms/` 下有 Engine，只能证明算法被实现或移植。要证明上线，还要继续检查：

1. composition root 是否创建它；
2. 主流程是否调用它；
3. 是否有端到端测试证明结果进入真实记忆存储和召回。

### 记忆“保存” ≠ 记忆“学习”

- 保存：把消息、事实或向量写入数据库。
- 学习：从失败/成功轨迹中抽象出下一次可以复用的规则或 Skill。

EverOS、AutoGen、Voyager 的价值主要在第二层，而不是简单的向量存储。

### 检索算法 ≠ 记忆形成算法

- Mem0、EverOS、AutoGen、Voyager 主要解决“什么值得留下、如何更新、如何蒸馏”。
- Graphiti、RRF、MMR、Letta 主要解决“如何组织、检索、重排和注入上下文”。
- OpenMemory 解决“谁可以读取哪一类记忆”。

Nuke 的完整 Memory 能力，实际上是这些层叠加后的结果，而不是某一个项目的单一实现。
