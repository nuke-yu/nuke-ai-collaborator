# 架构与并发执行模型审查报告 — 2026-07-02

本报告总结并评估了 2026-07-02 提交的关于**群组重新分配并发控制、生命周期管理器状态防护、以及 MCP 通信生命周期加固**的系列代码修改。

---

## 1. 组重新分配协议（CELL-18 Handoff V3）加固

在多 Worker 分布式运行环境中，群组的动态调度与重新分配（Reassign）是最容易产生竞争和时序问题的边界。本次修改对 `reassign_group` 进行了深度防御设计：

### 1.1 代际版本化（Reassign Versioning）
* **痛点**：重分配（Reassign）操作包含 10 秒的异步等待（老 Worker 释放 Lease）。如果在这期间对同个群组触发了多次重分配（如 `w1 → w2` 随后快速触发 `w2 → w3`），先发任务的超时或释放 ACK 可能会覆盖新任务的路由缓存，造成脑裂与流量错投。
* **方案**：引入 `reassign_version` 计数器。每次进入 `reassign_group` 即递增版本号并注销先前的 pending 任务。在 `finally` 阶段，只有当前任务的版本号仍与最新代际一致时，才被允许写盘和更新路由缓存。这彻底隔离了多轮并发重分配的时序竞争。
* **异常回滚**：当中央 DB 写入失败时，增加异常捕获，立即调用 `_finish_reassign_version` 提前注销未生效的版本，避免由于异常阻塞导致的代际字典内存泄漏。

### 1.2 老 Worker 掉线秒级自愈
* **痛点**：重分配过程中，如果老 Worker 突然离线（或物理崩溃），Supervisor 默认会傻等 10 秒超时。
* **方案**：在 `_drop_worker_state` 中加入感知。当发现老 Worker 离线且处于 pending handoff 状态时，立即将其关联 of Future 设置为 `False` 唤醒等待，引导重分配流程直接 fallback 到新 Worker，免去了无意义的超时等待，提升了集群自愈响应速度。

---

## 2. 生命周期管理器（Lifecycle Manager）在途状态防护

`LifecycleManager` 负责群组数据库的预热（Hydration）与 LRU 淘汰（Eviction）。

### 2.1 引入在途任务守护（In-flight Future Guards）
* **痛点**：并发流量可能对同一群组瞬间调用多次 `hydrate()`，或者在加载的同时触发 LRU 淘汰，导致数据库文件锁（GroupLock）抢占失败、任务重复创建或加载一半被强行驱逐。
* **方案**：在管理器内部维护 `_hydrating` 和 `_evicting` 映射字典。
  * 当一个群组正在预热或正在淘汰时，后续相同的操作不会开启新的协程，而是通过 `asyncio.shield` 共享已有的 Future。
  * `_pick_lru_evictable_group` 会自动过滤当前正处于 hydration/eviction 过程中的群组，防止将预热中的群组误判为冷数据淘汰。

### 2.2 关闭期间防止协程泄漏
* **方案**：在 `shutdown()` 时，将 `_shutting_down` 标志置为 `True`，并取消和等待所有的 `_hydrating` 和 `_evicting` 任务。通过将 `_ensure_evictor_task_locked` 的唤醒逻辑置于 `_shutting_down` 校验之后，彻底杜绝了关闭期间因为意外调用 `hydrate` 再次拉起后台守护循环的问题。

### 2.3 淘汰异常时文件锁安全释放
* **痛点**：在驱逐群组（`_do_evict`）时，如果关闭数据库 Writer 抛出异常，原本处于流程末端的 `glock.release()` 会被跳过，导致该群组的文件锁被永久残留霸占，使后续任何对此群组的 hydration 加载都因占锁失败而卡死。
* **方案**：将 `aclose_writer` 放在 `try` 块中，并将 `glock.release()` 与 `self._locks.pop(gid)` 放入 `finally` 块中。确保即使数据库关闭失败，文件锁也必然会被安全释放。

---

## 3. MCP 桥接与收集器生命周期及内存泄漏清理

对跨进程/跨协程的 MCP 工具调用框架进行了深度的资源安全审查。

### 3.1 `mcp_bridge.py` 中的 Pending Future 泄露清除
* **痛点**：如果 Worker 调用的 MCP 工具因为超时或被强行取消，原先的请求 ID 依然存留在桥接器的 `_pending` 字典中，长时间运行会造成内存泄漏。
* **方案**：在 `request` 和 `authenticate` 方法中引入 `_finish_pending` 辅助函数，使用 `try...finally` 与 `CancelledError` 捕获，确保无论是正常结束、发生超时还是协程被主动取消，均能在第一时间内移出 `_pending`。

### 3.2 收集器（Collector）优雅关停与僵尸进程回收
* **方案**：在 `mcp_collector.py` 中实现了显式的 `close()` 机制。系统退出时，会主动取消并等待所有在途的 MCP 工具执行 Task，对 Socket Writer 进行 `wait_closed()`，并调用 `_kill_descendants()` 强杀遗留的 `npx/node` 孙子进程。

---

## 4. 回归与修复：Recap 单元测试修复

* **问题**：在此前的重构中，`dbpaths.py` 不再直接导出 `WORKSPACE_ROOT`，而是通过 `layout.py` 的 SSOT 动态获取。这导致 `test_recap.py` 中的 `patch("runtime.dbpaths.WORKSPACE_ROOT", ...)` 抛出 `AttributeError` 阻碍测试通过。
* **修复**：在 [backend/tests/test_recap.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/tests/test_recap.py) 中，删除了针对 `runtime.dbpaths` 的冗余 patching 逻辑（保持对 `skills.constants.WORKSPACE_ROOT` 的覆盖即可）。修复后，`test_recap.py` 下的 **33 个测试用例全绿通过**。

---

## 5. 审查结论
本次审查的所有 7 个并发/健壮性提交（`8be7040`、`6dff1b9`、`8e74133`、`c20df13`、`b29b726`、`015a525`、`1986598`）在设计上完全契合 [CONCURRENCY-EXECUTION-MODEL.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/CONCURRENCY-EXECUTION-MODEL.md) 的微观审计标准，单元测试覆盖度高，性能表现稳健，**准予合入主分支**。
