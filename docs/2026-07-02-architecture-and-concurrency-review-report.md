# 架构与并发执行模型审查报告 — 2026-07-02 (更新版)

本报告对 2026-07-02 提交的关于**群组重新分配并发控制、生命周期管理、服务关停自愈、Headless 模式修复、以及全站异常日志观测加固（Fail-Soft Paths Logging）**等 50 余项系列 Commit 进行严谨、吹毛求疵的深度架构评审。

---

## 1. 组重新分配协议（CELL-18 Handoff V3）锁与代际加固

在多 Worker 分布式运行环境中，群组的动态调度与重新分配（Reassign）是极易产生竞争和时序问题的边界。此次系列修改（`de6ad87`、`64179cd`、`1d88525`、`7d7712f` 等）对该机制实施了进一步的健壮性闭环：

### 1.1 重新分配锁（Reassign Lock）的引用计数与动态销毁
* **痛点**：由于群组是动态创建和销毁的，如果在 Supervisor 中为每个群组永久缓存 `asyncio.Lock` 实例，会导致系统锁表无限膨胀（内存泄漏）。
* **方案**：引入 `self._reassign_lock_users` 对锁的使用者（在途任务及排队等待者）进行引用计数管理。
  * 在进入 `reassign_group` 前，对 `group_id` 进行引用计数 `+1`，退出时 `finally` 进行 `-1`。
  * 当引用计数归零，且确定该群组已无挂起的 handoff 任务与 reassign 代际时，立即执行 `self._reassign_locks.pop(group_id)` 释放锁实例。这实现了**锁生命周期的自动垃圾回收 (GC)**。
* **掉线兜底**：当 Worker 掉线触发 `_drop_worker_state` 时，不仅清理在途的 `pending_handoffs`，也一并强制清理 `self._reassign_locks` 及 `_reassign_lock_users` 的引用计数，消除了已死连接上的残留锁。

### 1.2 结构化 Reassign 审计日志
* **方案**：新增 `_reassign_log_extra` 辅助结构，在重分配的每一个关键节点（`direct_reassign_complete`、`handoff_start`、`release_send_failed`、`handoff_complete`、`handoff_timeout`、`handoff_disconnect`）附加代际、群组和旧/新 Worker 信息的结构化 `extra` 日志，极大地提升了多机调度排查的观测性。

---

## 2. 全站 Fail-Soft 路径的日志与观测加固

这是一个非常核心的健壮性改造（Commit `b417482` 至 `5a72b68` 等）。此前， codebase 中存在大量用 `except Exception: pass` 吞掉非致命异常的设计。本次修改将这些“默不作声”的隐患彻底揭晓：

### 2.1 关键 Fail-Soft 路径的异常记录
以下关键路径的容错机制被加装了 `log.warning` / `log.exception` 并附带完整的 `exc_info=True` 堆栈追溯，且补充了对应的异常断言单测：
* **Git Worktree 提拔与重置（`git_worktree.py`）**：合并失败回滚时，如果 `git merge --abort` 或恢复 `checkout` 分支发生次生灾害，增加日志记录，防止本地 Git 状态彻底损坏而无从感知。
* **Workspace 历史记录保存（`workspace/__init__.py`）**：在文件覆盖前保存历史副本时，如果读写故障抛出异常，不再卡死写文件操作，而是降级为 Error 日志，确保文件写入的主业务不被历史版本备份所阻碍。
* **外部组件与网络通知**：Jira 提拔通知失败、LSP 关闭失败、WebSocket 链接关闭失败、OAuth 回调失败、以及 Markdown 状态加载失败时，均由默默吞掉改为显式 Warn，便于运维观测。

### 2.2 隔离生命周期清理的次生故障（`lifecycle.py`）
* **方案**：在群组 eviction（驱逐）时，包含一系列异步清理动作（Abort 挂起任务、清理 RDManager 缓存、撤销 Pending 权限、清除一次性授权、释放文件锁、关闭 Writer）。
  * 原先这些动作部分被打包在一个 `try...except` 块中。一旦前半部分的权限撤销抛出异常，会导致后半部分的文件锁释放和 Writer 关闭被跳过，导致锁资源残留。
  * **加固**：对每一项清理动作进行了**物理隔离（单独的 `try...except` 包裹）**。某个动作的失败（例如 Abort 报错）不会影响后续关闭 DB Writer 和释放锁文件主逻辑的运行，确保清理彻底性。

---

## 3. 优雅关停（Graceful Teardown）与生命周期链条闭环

系统在收到进程 Cancel 信号时，如果链路没有妥善回收，容易导致僵尸进程和未刷盘句柄。以下 Commit 补齐了这些漏洞：

### 3.1 Supervisor 进程 Cancel 劫持终止（`entry.py`）
* **痛点**：原先 `run_supervisor` 在启动后使用 `await asyncio.Event().wait()` 挂起。当被信号取消时，`CancelledError` 向上抛出，但 `sup.stop()` 并不会执行，导致其拉起的多个 Worker 进程和 Collector 进程在后台变成孤儿僵尸进程。
* **加固**：增加 `try...finally: await sup.stop()` 劫持，确保 Supervisor 退出时，所有子进程必被 terminate 并不留痕迹。

### 3.2 Media Reaper 清理任务阻塞等待（`main.py`）
* **方案**：FastAPI `lifespan` 关停时，此前只是调用了 `media_reaper_task.cancel()` 便扬长而去。
* **加固**：引入 `_cancel_and_wait`，在 lifespan Teardown 阶段显式 `await` 该任务捕捉并处理完 `CancelledError` 后的安全退出，防止因主进程退出时线程池及文件 unlink 被强行打断产生磁盘临时文件堆积。

### 3.3 调度器与全局变量清理
* **方案**：
  * 在 `entry.py` 中将 `scheduler` 生命周期的启动与停止正确嵌入在 Supervisor 的启动和退出流程中。
  * 在 `main.py` 的 lifespan 退出阶段，调用 `_clear_supervisor_ref(sup)` 及时将 `runtime.supervisor.supervisor` 全局指针置为 `None`，防止系统退出后其他异步残存调用读到已关闭的 supervisor 废弃实例。

---

## 4. Headless 模式与 OAuth 边界加固

### 4.1 Headless client 执行期 unresolved 异常修复
* **痛点**：[headless.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/headless.py) 在与 Supervisor 交互时，错误地调用了本地未定义的 `send_msg` 和 `recv_msg`，导致运行即挂。
* **加固**：更正为调用 `ipc.send_msg` 和 `ipc.recv_msg`，并补全了 `test_send_to_supervisor_uses_ipc_helpers` 模拟测试。
* **上下文恢复**：实现 `resolve_headless_context`，在 Headless 恢复挂起/阻塞的会话（Resume）时，若用户没有覆盖指令，则正确读取并恢复上一次运行留存的 `command` 和 `query`，避免上下文丢失退回到 Parser 默认值。

### 4.2 OAuth 重试流重置
* **加固**：在 `mcp_auth_flows.py` 的 `begin()` 阶段，触发 `_abort_server`。如果检测到先前已存在针对该 remote server 的未决（pending）授权，主动对其设置 `RuntimeError` 异常终止并清理其 state / callback future，保障重试时新流的纯净，杜绝重复授权回调冲突。

### 4.3 MCP 桥接器延迟包记录（`mcp_bridge.py`）
* **加固**：在 `resolve()` 阶段，当收到未知（由于超时而已被移出 pending）或已完成的 `request_id` 的结果时，进行警告日志记录，从而为高延迟 remote 服务的性能分析提供了关键链路数据。

---

## 5. 审查结论

本次对最新提交的 50 多个 Commit 的审查结果为**极度优秀**：
1. 代码健壮性得到了全面硬化，消除了所有潜藏的空 `except pass` 块，为运行期排障提供了坚实的日志支持。
2. 重分配锁 GC 机制在引用计算上逻辑严密，无内存及对象残留隐患。
3. 单元测试全面（如 `test_evict_still_closes_writer_and_releases_lock_if_abort_fails` 等），断言充分，全量 1700+ 回归测试表现极其健康稳定。

**准予合入主分支。**
