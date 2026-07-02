# 并发执行模型 — 微观审计标准

> 本文是审计运行时"一件事跑在哪一层"的**基准清单**。任何新增的后台活儿、长循环、
> 阻塞调用、子任务，都应能对号入座到下面某一层；对不上号 = 需要 review。
>
> 三层心智模型：**进程(OS 隔离)→ 协程(架构级并发)→ 线程(仅卸载阻塞调用)**。

---

## 0. 一句话判据（审计第一问）

判断一件事该在哪一层，只问一句：**它是「等 I/O」还是「调同步阻塞库」？**

- **等 I/O**（等模型 / 等 IPC 帧 / 等工具返回 / 等锁）→ **协程 `asyncio.Task`**。
- **调同步阻塞库 / 阻塞 syscall**（Chroma、jedi、文件读写、glob+unlink）→ **丢线程**
  （`asyncio.to_thread` / `loop.run_in_executor`），否则卡死整个事件循环。
- **需要崩溃隔离 / 独立内存 / 独立生命周期** → **独立进程**（`create_subprocess_exec`）。

违反这条判据的典型反模式（审计要抓的）：
- 在协程里直接调同步阻塞库（卡事件循环）。
- 把纯 I/O 等待丢进线程池（浪费线程、无意义）。
- 把本该常驻、需被取消的活儿用裸 `create_task` 发出去而不登记（GC 提前回收 / 无法 abort）。

---

## 1. 进程层（OS 进程，`create_subprocess_exec`）

由 Supervisor 用 `asyncio.create_subprocess_exec(sys.executable, "-m", "runtime.entry", ...)`
拉起（`runtime/supervisor.py:93`，命令见 `:69`/`:80`）。各自独立解释器 / PID / 内存 /
GIL / 事件循环；崩溃由 `_run_process_loop` 指数退避自动重启（`supervisor.py:85`）。

| 进程 | 职责 |
|---|---|
| `main.py` | FastAPI / WS 入口 |
| **Supervisor** | 路由 WS、中继 MCP_CALL/MCP_RESULT、监管 Worker/Collector 生死 |
| **Worker × N** | 每个 pin 住若干 group，跑 AI 循环（`run_unit`） |
| **MCP Collector** | 唯一持有真实 MCP stdio/remote 连接 |

**为什么必须是进程而不是线程**（审计时不要质疑这三条，是硬约束）：
1. **MCP 单进程原则**：MCP 连接的 anyio cancel scope 绑定创建它的 task，跨进程/跨 task
   用会 RuntimeError → MCP 必须独占 Collector 进程，Worker 只能拿 `McpProxyProvider` 透传。
2. **group 隔离 + 爆炸半径**：一个 Worker 崩了，Supervisor 单独重启，不影响别的 Worker。
3. **GIL**：多进程才能真正并行；进程内部是 I/O 密集的单线程 asyncio。

---

## 2. 协程层（`asyncio.Task`：架构级并发主力）

每个进程内 = **1 个事件循环（单线程）**。并发单元是 `asyncio.Task`，三种产生方式：
- `asyncio.create_task(...)` —— 常驻基础设施循环。
- `bg.spawn(coro)` —— fire-and-forget 副作用，持引用 + 异常落日志（`core/bg.py:56`）。
- `bg.spawn_group(gid, coro)` —— 在 spawn 之上按 group 登记，使 `abort_group` 能整链取消
  （`core/bg.py:65`）。**需要被群级取消的活儿必须走这个，不能用裸 create_task。**

> 同群串行：`group_run_lock`（`bg.py:28`）让同一 group 的 `run_unit` FIFO 串行，
> 避免连发消息时并发互踩、输出交错。不同群各自一把锁，互不阻塞。

### 2.1 Supervisor 进程
- `_run_process_loop` × (每 Worker + Collector) —— 监管 + 崩溃重启（`supervisor.py:67,78`）
- 关停时 `client.close()`（`supervisor.py:279`）

### 2.2 Worker 进程（并发主战场）
**常驻 pump 循环**（`worker.py:74-78`）：
- `_pump_upstream` —— 读 IPC 帧
- `_report_stats_loop` —— 每 30s 上报指标
- `_pump_recap` —— recap 生成
- `_pump_compaction` —— DB 历史压缩
- `_hydrate_assigned_groups` —— 群组 DB 预热

**业务 Task**：
- ⭐ **`run_unit`**（`bg.spawn_group`，`runner.py:118,321`）—— **一个 Bot 响应一条消息**，
  同群 FIFO 串行。这是核心执行单元。
- ⭐ **后台 subagent**（`_run_bg_agent`，`workspace_tools.py:276`）—— 同事件循环的兄弟 Task。
  ⚠️ 见 §4 已知 gap。
- 工作流推进 `wf.apply`（`dispatch.py:123,282`、`rd_automation.py:59,82`）
- fire-and-forget 副作用：`clear_recap`、`send_auto_reply`、`save_rule`、`_retro_on_done`、
  `_recap_on_paused`（`worker.py:176-279`、`dispatch.py:94-101`）
- 记忆/日志：`memory.observe`、`maybe_reflect`、`append_log`、`archive_run`、
  `CompactionTriggered`（`tool_loop_v1_helpers.py:537-562`）
- 崩溃恢复 dispatch（`sessions/recovery.py:135,233`）

### 2.3 MCP Collector 进程
- `_repush_loop` —— 周期把 schema 重推给所有 Worker（`mcp_collector.py:310`）
- `_handle_call` —— **每个 MCP 工具调用一个 Task**（`mcp_collector.py:318`）
- `_handle_auth_start` / `_handle_reload` —— OAuth 启动 / 配置热重载（`:322,328`）
- `_reinit_with_auth` —— 拿到 token 后重连 server（`:282`）
- MCP 会话：`_session_task` + `ready_wait`（`mcp_client.py:426,430`）

### 2.4 跨进程通用基础设施
- `lifecycle._background_loop` —— 群组淘汰/驱逐循环（`lifecycle.py:174`）
- `bus` 事件投递 `_run` + ws adapter（`bus/engine.py:147`、`bus/adapter.py`）
- LSP `_read_loop` + `_reaper_loop`（`lsp_engine.py:59,221`）
- 容器沙箱 reaper（`container_sandbox.py:212`）
- `_media_reaper_loop` —— **循环本身是协程**，每 6h 把真正清理活儿 `to_thread` 卸载
  （`main.py:92` + `:45`）

---

## 3. 线程层（`to_thread` / `run_in_executor` / `threading`：仅卸载阻塞）

线程**不承载业务流程、不互相通信**，唯一用途是把同步阻塞调用挪出事件循环。
唯一例外是一个常驻的文件 watcher 计时器。

| 类别 | 干什么 | 位置 |
|---|---|---|
| **ChromaDB 记忆** | query / add / delete / prune_expired / get_by_ids / update_batch / get_all（Chroma 同步库全卸载） | `ai/memory.py`（十几处） |
| **工具事件落库** | tool_event 写入 | `ai/tool_events.py:381` |
| **文件系统读写** | read_text / write / 构建文件树 / mkdir / delete / skill 状态 / 预览路径 / 列目录 | `workspace/__init__.py`、`workspace_tools.py:1261,1278`、`api/workspace.py` |
| **code intel (jedi)** | definition / references / hover / document_symbols | `executors/code_intel/jedi_engine.py` |
| **skills 扫描** | list_skills 同步文件扫描 | `skills/discovery.py:74,122` |
| **media reaper 清理** | reap_screenshots（glob + unlink） | `main.py:45` |
| **文件变更 watcher（真·常驻线程）** | `threading.Timer` debounce skill 文件改动 | `skills/watcher.py:63-72` |
| **线程锁（配套）** | `threading.Lock` 保护被线程池触碰的共享态 | `bus/engine.py:57`、`workspace/__init__.py:21`、`workspace_tools.py:1037`、`skills/metadata.py:234`、`skills/cache.py:9`、`git_worktree.py:50` |

---

## 4. subagent 的定级（特别说明，常被问到）

subagent **不构成新的进程/Worker 级别**，它寄生在父 Bot 那趟 `run_unit` 之下、同一个
Worker 进程/事件循环内：

- **前台 spawn**（`await ...run(sub_ctx)`，`workspace_tools.py:281`）：**没有新 Task**，
  只是父 `run_unit` 协程里的嵌套 `await`，同一个 `asyncio.Task` 更深的栈帧。
- **后台 spawn**（`asyncio.create_task(_run_bg_agent)`，`workspace_tools.py:276`）：新建一个
  与父 run_unit 同循环并发的兄弟 Task。

⚠️ **已知 gap（审计重点关注）**：后台 subagent
1. 登记在 `workspace_tools._bg_tasks`，**不走 `bg.spawn_group`** → 不在群级可取消链上，
   `abort_group` / evict 群时可能成为漏网游离 Task；
2. **不取 `group_run_lock`** → 与父 run_unit 及同群其他 run 真正并发，绕过同群 FIFO 不变量。
   评估 evict-group-while-bg-subagent-running 的安全性时必须考虑这两点。

---

## 5. 审计 checklist（用本文做 review 时逐条对）

- [ ] 新增的常驻循环：是不是协程 Task？是否被持引用（`bg.spawn` 或显式保存），避免 GC？
- [ ] 新增的群级活儿：是否走 `bg.spawn_group` 以便 `abort_group` 能取消？
- [ ] 协程里有没有直接调同步阻塞库（Chroma/jedi/FS/glob）？应 `to_thread` 卸载。
- [ ] 丢进线程的活儿，是不是真的阻塞？纯 I/O 等待不该进线程池。
- [ ] 跨进程共享态：有没有误以为线程锁能跨进程同步？（不能，进程间靠 IPC/DB）
- [ ] 新 MCP 相关代码：MCP 连接是否仍只活在 Collector 进程？Worker 只透传？
- [ ] 任何能并发写同一 group 上下文的新 Task：是否考虑了 `group_run_lock`？
