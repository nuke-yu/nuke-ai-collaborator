# Nuke AI Collaborator — 架构与代码深度审查报告 (2026-06-02)

## 概要信息
- **审查日期**: 2026-06-02
- **审查视角**: 50年 Python 架构师，聚焦架构缺陷、并发安全、性能瓶颈和可维护性。
- **背景说明**: 已有缺陷清单中 53/57 项已修，本审查只关注尚未被发现的新问题。

## 审查文件范围
在本次审查中，对以下文件进行了深度代码审计：
- **入口层**:
  - [main.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/main.py)
- **事件总线 (EventBus)**:
  - [engine.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/bus/engine.py)
  - [events.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/bus/events.py)
  - [adapter.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/bus/adapter.py)
  - [__init__.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/bus/__init__.py)
- **WebSocket 管理**:
  - [ws_manager.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/ws_manager.py)
- **编排层**:
  - [base.py (orchestration)](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/base.py)
  - [declarative.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/declarative.py)
  - [stages.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/stages.py)
  - [interaction.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/interaction.py)
  - [locks.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/locks.py)
  - [registry.py (orchestration)](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/registry.py)
  - [ai_service.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/ai_service.py)
- **执行层 (Executors)**:
  - [base.py (executors)](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/base.py)
  - [tool_loop_v1.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py)
  - [registry.py (executors)](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/registry.py)
  - [tool_executor.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/tool_executor.py)
- **AI 客户端**:
  - [client.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/ai/client.py)
- **数据库层**:
  - [__init__.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/db/__init__.py)
  - [queries.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/db/queries.py)
  - [schema.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/db/schema.py)
  - [context.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/db/context.py)
  - [writer.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/db/writer.py)
- **运行时 (Runtime)**:
  - [supervisor.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/supervisor.py)
  - [worker.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/worker.py)
  - [dispatch.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/dispatch.py)
  - [lifecycle.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/lifecycle.py)
  - [protocol.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/ipc/protocol.py)
  - [framing.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/ipc/framing.py)
- **会话与恢复**:
  - [store.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/store.py)
  - [recovery.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/recovery.py)
- **工作流与后台**:
  - [workflow.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/workflow.py)
  - [bg.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/bg.py)
  - [runner.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/runner.py)
- **权限与工作区**:
  - [engine.py (permissions)](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/permissions/engine.py)
  - [__init__.py (workspace)](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/workspace/__init__.py)
- **API 与调度层**:
  - [messages.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/api/messages.py)
  - [runner.py (scheduler)](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/scheduler/runner.py)
- **历史缺陷与架构清单**:
  - [defect_list.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/defect_list.md)
  - [architecture_review_summary.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/architecture_review_summary.md)

---

## 🔴 Critical (线上崩溃/功能失效级缺陷)

### C-1：Worker 上游泵 ([_pump_upstream](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/worker.py#L104-L112)) 无异常处理——单条 IPC 发送失败即杀死整个事件流

> [!WARNING]
> **严重影响**: Worker 存活但"哑火"——看起来一切正常，实际无事件投递。这是一个生产级静默失败。

- **源码位置**: [worker.py:L104-112](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/worker.py#L104-L112)
- **问题分析**: `ipc.send_msg` 若因 Supervisor 端暂时不可用（如 TCP 写缓冲区满、连接半关闭）而抛出异常，整个 `async for` 循环崩溃退出。此后该 Worker 产生的所有 bus 事件（`stream_chunk`、`message`、`presence` 等）将全部被静默丢弃，前端将收不到任何 AI 输出。
- **推荐修复方案**:
  ```python
  async def _pump_upstream(self) -> None:
      async with self._sub as sub:
          async for payload in sub:
              gid = payload.get("group_id")
              if gid is None:
                  continue
              try:
                  await ipc.send_msg(self._writer, ipc.protocol.envelope(
                      ipc.protocol.BROADCAST, group_id=gid, payload=payload,
                  ))  
              except Exception:
                  log.exception("worker %s: upstream send failed, event dropped", self.worker_id)
  ```

---

### C-2：EventBus wildcard 订阅者列表迭代无快照保护——并发 subscribe/unsubscribe 可致 RuntimeError

> [!NOTE]
> **严重影响**: 纯单线程 asyncio 下暂时安全，但为预防未来架构演变，建议做快照保护防御。

- **源码位置**: [engine.py:L78](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/bus/engine.py#L78) (`list(self._wildcard)`) 与 [engine.py:L73](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/bus/engine.py#L73) (`list(self._typed.get(event_type, []))`)
- **问题分析**: 目前 `dispatch` 迭代均使用了 `list(...)` 快照保护，这非常正确。然而，[subscribe](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/bus/engine.py#L81) 和 [subscribe_all](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/bus/engine.py#L96) 对列表的 `append`/`remove` 没有任何同步保护。在单线程 asyncio 下虽然没有产生 yield point 冲突，但在 Supervisor/Worker 多进程架构中，为了防止未来的多事件循环并发重构引起 `RuntimeError`，建议添加快照防御或同步锁。

---

## 🟠 High (架构级设计缺陷)

### H-1：[_post_system_msg](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/runner.py#L25-L33) 使用读连接执行写操作——绕过 SQLite 写序列化器

> [!WARNING]
> **严重影响**: 绕过了 SQLite 的写入排他锁，可能在高并发写入时导致 `database is locked` 异常。

- **源码位置**: [runner.py:L25-33](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/runner.py#L25-L33)
- **问题分析**: `_post_system_msg` 内部使用 `get_db()` 获取数据库连接。`get_db()` 返回的是普通的只读或未受序列化锁保护的 `aiosqlite` 连接。但内部调用的 `save_message` 会执行 `INSERT` 写操作。这违背了项目设计中写操作必须通过 [writer.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/db/writer.py) 提供的统一单锁连接 (write-connect) 契约。
- **推荐修复方案**: 将 `async with get_db() as db:` 变更为 `async with write_connect() as db:`。

---

### H-2：[store.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/store.py) 使用读连接执行写操作

> [!WARNING]
> **严重影响**: 相比 H-1 频率更高，因为每次 AI 调用、工具调用都会触发。极易在高并发下导致 SQLite 锁争抢失败。

- **源码位置**:
  - [create_session](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/store.py#L8-L26)
  - [append_event](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/store.py#L29-L39)
  - [update_session_status](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/store.py#L88-L94)
  - [save_snapshot](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/store.py#L113-L119)
  - [add_tokens](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/store.py#L122-L141)
- **问题分析**: 上述方法全部使用了 `async with _db.connect() as conn:` 这种读连接，而非 `_db.write_connect()`，然而执行的全是 `INSERT`/`UPDATE` 等写操作。
- **推荐修复方案**: 统一将所有写操作块修改为 `async with _db.write_connect() as conn:`。

---

### H-3：[AIService._sync_tokens](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/ai_service.py#L156-L173) 的累加语义在多并发 AI 调用下语义正确，但架构脆弱

- **源码位置**: [ai_service.py:L156-173](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/ai_service.py#L156-L173)
- **问题分析**: [AIService.usage](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/ai_service.py#L46) 是实例级累加器，每次 AI 调用后将累加总量写入数据库。因为当前的 `ToolLoopRunner` 对 AI 的调用是串行执行的，故没有并发覆盖的风险。但若后续架构演进，将 AI 调用放到 `asyncio.gather` 并发执行时，实例级累加器的累加值可能会在写入 DB 时发生错乱。
- **架构建议**: 应重构为基于单次 Request 的 Token 记账模式，而非全局实例级累加写入，消除潜在隐患。

---

### H-4：[Supervisor._fanout](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/supervisor.py#L156-L174) 超时后只 unregister_browser 但不广播 presence

- **源码位置**: [supervisor.py:L156-174](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/supervisor.py#L156-L174)
- **问题分析**: 当移除超时或写入失败的 client 时，[Supervisor._fanout](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/supervisor.py#L156-L174) 会调用 `self.unregister_browser`，但与 [WSManager.broadcast](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/ws_manager.py#L57) 不同，这里并不会广播 `presence: offline` 状态。
- **影响**: 导致前端绿点状态不同步，超时断开的浏览器客户端在系统里依然显示为 online。
- **推荐修复方案**: 超时卸载后，提取其 `member_id` 并广播 offline 状态。这需要扩展 WSClientProxy 接口以暴露 `member_id`。

---

## 🟡 Medium (代码质量/潜在风险)

### M-1：[main.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/main.py) WebSocket endpoint 中 unreachable dead code

- **源码位置**: [main.py:L188-203](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/main.py#L188-L203)
- **问题分析**: 在 line 188 处的 `continue` 使得 190-203 行的代码在任何情况下都是不可达的死代码。这可能是一个被遗忘的早期逻辑残留。
- **建议**: 清理冗余代码，若 172-176 行已正确处理 `permission_response`，则应安全地删除这一段死代码。

---

### M-2：[get_orphaned_sessions](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/store.py#L97-L103) 使用 f-string 拼接 SQL

- **源码位置**: [store.py:L101-103](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/sessions/store.py#L101-L103)
- **问题分析**: 使用了 `f" AND group_id = {group_id}"` 来拼接 SQL 查询，破坏了项目其他地方使用的参数化查询（`?` 占位符）标准模式。虽然 `group_id` 是 int，没有注入风险，但属于不良实践。
- **推荐修复方案**:
  ```python
  query = "SELECT * FROM agent_sessions WHERE status = 'running'"
  params: tuple = ()
  if group_id:
      query += " AND group_id = ?"
      params = (group_id,)
  query += " ORDER BY created_at ASC"
  async with conn.execute(query, params) as cur:
  ```

---

### M-3：WorkflowUpdate 事件已注册但未在文档中列出

- **源码位置**: [events.py:L196](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/bus/events.py#L196) (`WorkflowUpdate`)
- **问题分析**: `WorkflowUpdate` 通过 `@event("workflow_update")` 正常注册，但在 `README.md` 中说明的事件列表只列出了 28 种，遗漏了这一种。实际上共有 29 种事件。
- **建议**: 更新 `README.md` 以保证文档的严谨性和同步更新。

---

### M-4：[LifecycleManager.stats](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/lifecycle.py#L119-L123) 方法定义在 global 变量之后

- **源码位置**: [lifecycle.py:L119-123](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/lifecycle.py#L119-L123)
- **问题分析**: `manager = LifecycleManager()` 实例定义在了类定义文件末尾，但 `stats` 方法被写在其后或缩进有异。虽然语法没有报错，但由于书写顺序，容易给维护者造成“stats是游离函数”的视觉误导。
- **建议**: 将实例初始化代码移动至类内方法的最终声明之后。

---

### M-5：VFS path lock registry 可能随时间无限增长

- **源码位置**: [__init__.py (workspace)](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/workspace/__init__.py#L14)
- **问题分析**: `_LOOP_REGISTRIES` 按照 `(loop_id, resolved_path)` 存储了所有的路径锁，且这些锁在路径访问完毕后从未被释放或清理。在长周期的 Worker 中，访问的文件越多，字典会持续膨胀。
- **建议**: 在进行 [_do_evict](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/lifecycle.py#L78) 时清理相关 group 占用的路径锁，或对字典采用 LRU 淘汰机制。

---

### M-6：[Supervisor._routing_cache](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/supervisor.py#L38) 永不失效

- **源码位置**: [supervisor.py:L38](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/supervisor.py#L38)
- **问题分析**: 缓存 `_routing_cache` 存储了 `group_id -> worker_id`。如果是在运行时之外（例如直接在外部使用 SQL 修改了 DB 中的 `assigned_worker_id`），缓存无法感知这一变更。
- **建议**: 虽然目前运行时所有的重新分配都通过 [reassign_group](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/supervisor.py#L202-L239) 更新缓存，但作为缓存，增加合理的 TTL 校验或版本号验证会更健壮。

---

### M-7：ToolLoopRunner.execute 中 steer_channel 和 rewake_queue 的 empty() + get_nowait() 模式在 asyncio 下安全但有竞态窗口

- **源码位置**: [tool_loop_v1.py:L658-678](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py#L658-L678)
- **问题分析**:
  ```python
  if self.ctx.steer_channel and not self.ctx.steer_channel.empty():
      steers = []
      while not self.ctx.steer_channel.empty():
          steers.append(self.ctx.steer_channel.get_nowait())
  ```
  在单线程 asyncio 事件循环下，`empty()` -> `get_nowait()` 之间不会产生 yield point (即没有 `await`)，因此是安全的。然而，在其他并发框架中这种模式存在竞态。
- **建议**: 在代码注释中明确说明“单线程 asyncio 模型下 `empty` -> `get_nowait` 是原子的”，以防止未来进行架构迁移或多线程/多进程适配时引入 bug。

---

## 📊 架构设计总评

### 值得肯定与推崇的亮点
1. **事件总线 PubSub 双通道设计**: `typed` 与 `wildcard` 订阅独立管理，采用 `put_nowait` "drop newest" 背压机制并配合 Subscription 上下文管理器自动退订，逻辑严密优美。
2. **编排与执行高度解耦**: 契约基于 `OrchestratorStep` 数据类，通过 `InteractionAdapter` 隔离副作用，层级结构清晰合理。
3. **可插拔的阶段设计 (StageType)**: `single`/`pool`/`discussion`/`verification` 均采用模块注册挂载，是策略模式的典范实践。
4. **后台任务安全生命周期**: `bg.py` 的 `spawn` 带有强引用保护及 done_callback，极佳地规避了 asyncio 长期运行任务被 GC 回收的常见陷阱。
5. **SQLite 写序列化安全机制**: [writer.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/db/writer.py) 实现了单文件单锁独占 + WAL 机制，是解决 SQLite 写入并发冲突的上乘设计。
6. **IPC 通讯可靠性**: 帧长度前缀、HELLO 握手与租约转移 (lease handoff) 设计达到了生产级工业水准。

### 亟待优化的架构缺陷
1. **读写连接混用 (H-1, H-2)**: 最需优先治理的问题。写入操作必须统一通过 `write_connect` 串行化，防止 SQLite 频繁触发 `database is locked`。
2. **Worker 上游事件泵缺乏容错 (C-1)**: 单条消息超时或异常直接使得整个 Worker 静默哑火，属于线上灾难级隐患，应立即修复。
3. **全局错误边界缺失**: `_pump_upstream` 与监控循环等后台任务一旦发生未捕获异常即悄然中止，缺乏统一的重启与监控管理。
4. **内存常驻字典未清理 (M-5, M-6)**: `_once_grants`、`_pending`、`_LOOP_REGISTRIES`、`_routing_cache` 等结构缺乏清理和过期淘汰机制，在长期运行中存在内存泄漏隐患。

### 性能调优建议
- [runner.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/runner.py) 中的 `_post_system_msg` 每次执行都会调用 `get_messages(limit=3)`，如果仅是为了获取最新消息以同步，可在写入成功后直接在内存中构造对应的字典对象，省去一次数据库读开销。
- [ToolLoopRunner._get_fresh_context_prefix](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py#L236) 每次工具迭代均会从物理磁盘读取工作区文件。对于极少变动的文件应做内存缓存。
- [_acc_usage](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py#L32) 函数中累积到一个临时列表再取 `[0]` 属于反模式，可直接进行字典的 inplace 修改，减少高频分配列表的 GC 压力。
