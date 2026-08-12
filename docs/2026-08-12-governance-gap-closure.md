# 2026-08-12 治理缺口闭环记录

本轮针对代码核验中列出的缺口完成复核和实现。

## MCP

- `backend/executors/mcp_bridge.py` 的异步 Future 创建使用 `asyncio.get_running_loop()`；生产代码已无 `get_event_loop()` 调用。
- `McpProxyProvider` 对无 `__` 命名空间的工具强制进入 HIL，缺少 ruleset 时 fail-closed；Collector 下发的 `needs_approval` 缺失时同样 fail-closed。
- `MCPCollector` 对每个 OAuth server 使用独立 `asyncio.Lock`，并在取消、失败和完成路径清理锁与 inflight 状态。

相关测试：`test_mcp_proxy.py`、`test_mcp_collector.py`（其中包含 bridge round-trip 覆盖）。

## Memory 质量与成本评估

新增 `backend/memory/evaluation.py`，提供低基数、可跨进程快照的评估指标：

- Graphiti 实体解析准确率（需要标注样本）；
- 记忆检索 recall（需要相关记忆集合）；
- Skill 真实复用成功率；
- Memory 操作延迟和成本估算；
- heuristic token estimate 与真实 tokenizer 的平均绝对误差。

Worker 将快照通过结构化 `memory_evaluation` 字段发送给 Supervisor，并暴露为 Prometheus 指标。Letta 在使用真实 tokenizer 时自动记录校准误差。

## Artifact 物理回收

新增 `purge_deleted_artifacts()`：

1. 只处理超过 retention 窗口的 `deleted` tombstone；
2. 只允许删除 Group workspace 内的本地文件，远程或越界 locator fail-safe 跳过；
3. 物理删除成功后才删除数据库 tombstone；
4. 支持 `dry_run`，并返回 `purged/skipped` 审计结果。

## Store Registry 执行能力

`StoreRegistry` 新增：

- `operation_plan(store_id, operation)`：统一生成 migrate/backup/delete 治理计划；
- `bind_executor(...)`：绑定 host-specific side effect handler；
- `execute(...)`：只允许执行已登记且已绑定的操作；audit-hold store 拒绝 delete。

这保持了 Registry 的元数据职责，同时避免在 Registry 内硬编码具体数据库或文件系统实现。

## 验证

Artifact、Store Registry、Memory evaluation、MCP proxy/collector 和 Prometheus metrics 相关测试已运行。MCP Collector 的真实 Unix socket round-trip 在受限沙箱中可能因系统禁止创建 `/var/folders/...sock` 而失败；该失败属于测试环境权限，不是业务断言失败。

## 本轮架构 Review 结论

- **WeChat 多段消息**：风险成立。现在 `WechatIlinkAmbiguousDelivery` 会携带 `completed_chunks` 和 `total_chunks`，并写入 Outbox 审计详情；仍然禁止自动重试，后续管理面板可以据此实现从指定 chunk 恢复。
- **Webhook 重放**：风险成立。Webhook replay key 已从“时间戳+body hash”改为 `channel:tenant:event_id`，`ChannelStore.claim_webhook_replay()` 提供 24 小时 durable uniqueness；时间窗口仍用于拒绝过期签名，二者形成双层防线。
- **Graphiti BFS 参数膨胀**：风险成立。关系 recall 按每批最多 50 个 frontier 节点分批执行，避免 SQLite bind-variable 上限，同时保留完整遍历结果。
- **上下文超限**：风险成立。最终模型调用前增加同步 emergency compaction：先丢弃最早历史，再限制工具 Schema 和 system prompt；只有重新计算预算后才设置 generation tokens，避免明知窗口已满仍以 256 token 盲调 Provider。

## 2026-08-12 第二轮分层审查修复

- `SkillExecutionPlan` 已迁移到 `memory.contracts`，Voyager adapter 负责生成，application sandbox 只消费契约。
- Letta runtime 改为通过注入的 `MemoryDatabasePort` 访问 Group DB，不再引用 `ai.memory`；默认实现由 composition root 提供。
- Personal Memory API 不再直接导入 `ai.personal_vault`，App/ACL 操作统一经 `memory.bootstrap` facade。
- `AuthorizedPersonalKnowledgeService` 的 ABAC 查询和审计也已抽象为 `PersonalVaultPolicyPort`；Legacy Vault 只存在于 runtime adapter。
- Voyager 验证失败时支持显式 `rollback_fn` 补偿副作用；沙箱不会假设任意 Python callable 可以自动回滚。
- Emergency context pruning 改为按完整 assistant tool-call + tool-result 组删除，避免产生孤立 `role=tool` 消息。
- Graphiti hybrid search 的 RRF `k` 改为构造参数，默认仍为 60，可按召回分布校准。

契约测试 `test_memory_module_contracts.py` 现已通过；报告中提到的两项分层违例在当前代码中已关闭。Personal app 当前真实状态值是 `active/inactive`，不是 `disabled/archived`，后者不应写入能力说明。
