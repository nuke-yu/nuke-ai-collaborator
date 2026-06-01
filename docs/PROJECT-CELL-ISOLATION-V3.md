# 架构设计：Project-Cell Isolation V3（分片宇宙版）

> 日期：2026-05-31
> 解决：**DFT-072**（进程级内存状态锁死单 worker、无法横向扩展）；内含 **DFT-073**（UDS 隧道的事件契约）。
> 业务画像：**几百个项目群组**、多数长期沉睡、偶发唤醒；类 Slack/微信单入口 UX；要故障隔离 + 任务连续性。
> 状态：设计定稿，**已完成外部架构师交叉评审（见 §10）**，进入 Phase 0。

---

## 1. 核心决策与理由

| 决策 | 选了 | 否决了 | 为什么 |
|---|---|---|---|
| 进程模型 | **K 个常驻分片 worker**（`group→worker` 用**存储的分配表**，连接时钉死） | 一群一进程 | 几百个群 = 几百个解释器（几十 GB）→ 出局。分片池**进程数有上界** |
| 状态外置 | **不外置**（沿用进程内 EventBus 等） | 上 Redis/外部 broker | "群"恰好是**状态局部性单元**，现有状态本就按 group_id 切分，一群不跨进程 → 省掉大改造 |
| 数据库 | **1 中心库 + N 个群私有库** | 共享单库 | 共享单库多进程写 = `database is locked` 回归（DFT-053 的进程内单写锁跨进程失效） |
| 隔离手段 | 绝对路径 + `_safe_path`/`_resolve_shell_cwd` | `os.chdir` | chdir 进程级全局、是 footgun、且不提供真隔离，现有校验已够 |
| 回收 | **内存态驱逐**（不杀进程） | SIGTERM 杀进程 + 唤醒 | 固定池常驻，"回收"退化成清内存 + 关私有库连接，**零进程开销、零冷启动** |

> 核心哲学：**分片池给到"故障隔离 + 多核 + 进程数可控"；磁盘是永久真相源；内存态可随时丢弃重建。**

---

## 2. 拓扑与组件归属

```
浏览器 ──WS──> Supervisor（唯一入口进程）
                 ├─ 终止 WS，握手时按 URL 的 group_id 钉死 → worker
                 ├─ APScheduler（全局调度，到点唤醒对应 worker）
                 ├─ 中心库唯一写者
                 └─ 路由表 group→worker
                      │  下行隧道 (UDS)        ▲ 上行隧道 (UDS)
                      ▼                        │
                 Worker_0 .. Worker_{K-1}（常驻）
                      ├─ 每个 worker 懒加载它名下各群的「内存态 + 私有库连接」
                      ├─ EventBus / RDManager / tool_loop 执行（进程内，按群隔离）
                      ├─ 私有库读写 group_{id}/chat.db（单 worker 独占 → 无跨进程写竞争）
                      └─ 中心库只读挂载（读 Bot 模板/配置）
```

| 组件 | 跑在 | 职责 |
|---|---|---|
| APScheduler | Supervisor | 到点检查 worker，下发 trigger 唤醒群 |
| WS 终止 + 路由 | Supervisor | 握手钉死连接→worker，之后纯转发帧 |
| 中心库写 | Supervisor | groups/members/config/**unread_counts** 唯一写者 |
| RDManager / tool_loop / EventBus | Worker | 只管自己名下群，写各群私有库 |
| Interaction | Worker | 写私有库 save_message；广播/未读经**上行隧道**交 Supervisor |
| Process Sandbox | Worker | run_shell 子进程 + ulimit + 动态端口 |

---

## 3. 数据域归属（承重墙）

| 数据 | 归属 | 读写 |
|---|---|---|
| groups / members / app_config / templates | 中心库 | Supervisor 写，Worker 只读 |
| **unread_counts** | 中心库 | **Supervisor 写**（Worker 经上行推增量），Supervisor/UI 读 |
| messages / session_events / agent_sessions / tickets / reactions / pins / member_read | **群私有库** `group_{id}/chat.db` | 该群所属 Worker 独占读写 |
| ChromaDB | 群私有目录 | Worker 独占 |

> 关键修正：unread **不让 worker 直接写中心库**（否则跨进程写又回来），改成 worker 上行推增量、Supervisor 落库 → 中心库回到"只有 Supervisor 写"。

---

## 4. 双 UDS 隧道 = DFT-073 的事件契约

握手时连接已钉死到某 worker（group_id 在 WS URL 里、整连接生命周期不变，**不必每帧解析**）。帧用**长度前缀 JSON**。

| 方向 | 载荷 |
|---|---|
| **下行** Supervisor→Worker | `user_message` · `abort` · `permission_response` · `wake_trigger`(cron/告警) |
| **上行** Worker→Supervisor | `broadcast`（stream_chunk/typing/message/…全部 28 种事件）· `unread_delta` |

> worker 的 `bus/adapter.py` 不再调 `manager.broadcast`，改成**把 bus 事件序列化发上行 UDS**；Supervisor 收到后按连接归属扇出给浏览器。**这份上下行 schema 就是 DFT-073 要的"前后端共享事件契约"的服务端半边**，建议从 `bus/events.py` 生成。

---

## 5. 关键机制：懒水合 / 驱逐 / 沉睡唤醒

**懒水合**：事件进来 → 路由到常驻 worker → 若该群内存态不在，则开私有库、读最近上下文、按需 open chroma、RDManager 读当前 BOARD.md → 起一轮 run。**无进程派生、无 Python 冷启动**，首条多花零点几~一两秒。

**驱逐（取代回收）三铁律**：
1. **只动内存，绝不碰磁盘**——DB 文件 / workspace / chroma 永久保留，删群只能用户显式操作。
2. 判据：`无消息>60min AND bg 在跑==0 AND 无 running session` → 落 snapshot + 清内存态 + **关该群私有库连接**。
3. **每 worker 对"打开的私有库连接"设 LRU 上界**——名下几十个群，只保留活跃工作集，不同时握几十个 aiosqlite 线程。

**沉睡群唤醒（几百个群、3 个月不动、第四个月出 prod issue）**：
- 因为驱逐只动内存，沉睡群的私有库 + workspace 3 个月一直在磁盘 → "唤醒"= 在常驻 worker 里**按需水合 + 起全新 run**（**唤醒 ≠ resume session**：沉睡群无在途 session，比续跑更简单）。
- 两种入口：**人驱动**（UI 发消息，亚秒级）；**事件/调度驱动**（Supervisor 的 cron/告警 → 下发 wake_trigger → 群自己醒、自己干、结果落库 + 上行推未读 → 运维次日打开已见现场）。
- 水合**有界**：只读最近上下文 + 当前看板，不灌 3 个月历史。
- 真正的难点在**业务层**（3 个月后代码/环境漂移）：基础设施只需保证 **workspace 持久**（bot 每跳挂载最新看板重对齐）+ **sandbox 可重建**（Dev/QA 用 run_shell 重拉环境）。

---

## 6. 复用现有代码（不是推倒重来）

- EventBus / `permissions._pending` / steer / RDManager **本就按 group_id 切分** → 进程内原样可用。
- `db/writer.py` 的 keyed 单写锁模式 → key 从 `loop_id` 扩成 `(loop_id, db_path)`，天然支持多私有库各自单写。
- **影子持久化（DFT-018/055）** = 唤醒 / 续跑的同一套机器。
- `_safe_path` / `_resolve_shell_cwd` = 隔离已就绪。
- 权限 `ask` 的 future 局部性（DFT-031）在 cell 模型里**更干净**（WS 钉死同一 worker，approve 原路返回）。

---

## 7. 仍需用证据拍板的两件事

1. **工作负载并行画像**：run_shell 已 fork 子进程吃多核、LLM 是 I/O 异步——多进程真正稳拿的是**故障隔离与内存爆炸半径**，未必是吞吐并行。建议先加个 **event-loop lag 监控**，量单进程多群并发时被纯 Python CPU 阻塞多久，据此定 **K**。
2. **UDS/广播链路可行性**：这是最大未知，**先做 Phase 0 spike** 去风险，再投入 DB 大改。

---

## 8. 落地顺序（strangler-fig，每步现 app 可跑）

| Phase | 内容 | 价值/风险 |
|---|---|---|
| **0** | UDS spike：worker bus 事件→UDS→Supervisor→mock client 闭环（跨进程、真 EventBus、自带断言+超时） | **先去风险**，半天 |
| **1** | DB 参数化：`write_connect(path)`、`(loop,path)` keying、`GroupDB`/`global_db()` 句柄；单进程内先验证 | 大但机械 |
| **2** | schema 拆全局/群内 + 一次性数据迁移 splitter | — |
| **3** | 切 Supervisor/Worker 边界（先同进程函数调用，再换 UDS） | 用 Phase 0 成果 |
| **4** | 起 K 个 worker、分配表路由、群内存态懒加载/LRU 驱逐 | 收口 |

> 排序铁律：**DB 拆分价值要等多进程才兑现，别先搭 DB 地基再验 IPC**。Phase 0 不通，DB 大改就是沉没成本。

---

## 9. 主要风险

- 每 worker 的并发私有库连接数（aiosqlite 线程）→ 靠 LRU 驱逐封顶。
- 跨群聚合（未读）→ 靠 worker 上行 + Supervisor 投影，不扇出查询。
- Supervisor 成为 WS 单点 → 需扛大量并发流不被单个慢连接队头阻塞（复用 DFT-030 发送超时思路，但现在在 Supervisor 层）。
- 进程管理 / 可观测性多进程化（`/api/system/status` 要跨 worker 聚合）。

---

## 10. 评审细化（已 align：外部架构师 + 交叉评审）

外部架构师提了 3 条，全部采纳，并各补一条"他没点透、但决定成败"的细节：

### 10.1 Schema 迁移 = 水合的一部分，不是启动的一部分

- **决策**：中心库在 Supervisor 启动时**急切迁移**（仅一个，必须 ready）；**私有库的迁移挂进「水合」**——worker 打开某群私有库时先跑该库 migrations 再用。
- **理由**：几百个库 + 多数沉睡，启动时 `migrate_all()` 全量迁移会让部署变慢、中途失败留下**混版本舰队**、且吵醒本不该醒的沉睡库。沉睡库应在**被唤醒那一刻才迁移**。
- **可行性**：`db/migrations.py` 已是 **per-DB `_schema_version`**，加上 DFT-038「迁移失败不谎报成功」，天然幂等、可续跑。
- `migrate_all()` **保留为可选管理命令**（想在放流量前前置验证某个昂贵迁移时用），不是默认路径。

### 10.2 日志聚合：真正的杠杆是 trace_id，且日志要走「独立通道」

- **决策**：①跨进程调试靠 **`trace_id` 贯穿**——一个 `trace_id`（+`group_id`+`worker_id`）从下行 `user_message` 一路带到上行 `broadcast`，**结构化（JSON）** 打日志，`grep trace_id` 即可把一个请求在 Supervisor 与某 worker 间串起来。②**日志不走业务隧道**——单开一条 UDS 日志通道，或每 worker 直接写 `logs/worker-N.log` 读时聚合。
- **硬约束**：`LOG_RECORD` 绝不能和 `stream_chunk` 挤同一条 UDS——否则日志暴风会把**用户可见广播堵在日志后面**（队头阻塞）。

### 10.3 路由：显式分配表为主；rebalance 必须是「租约 + 干净交接」

- **决策**：`groups` 表加 **`assigned_worker_id`** 显式分配（单机、K 基本稳定，显式分配长期够用，还能手动把"重群"钉到专属 worker）。一致性哈希**留到真上多机时再说**，单机上是过度设计。
- **硬约束（架构师漏点，最危险）**：rebalance 把群从 A 换到 B 时，**绝不允许 A、B 同时持有该群私有库写连接的窗口**——否则刚消除的跨进程写竞争在交接瞬间复活。分配必须是**租约 + 干净交接**：
  ```
  A: 停接新消息 → drain 在途 → 落 snapshot → 关私有库连接 → ack
   → Supervisor 改 assigned_worker_id → B: open
  ```
  **任何时刻一个群的私有库只有一个 worker 持写连接**——这是 rebalance 的正确性底线，比一致性哈希本身重要。私有库在共享磁盘上，**重分配不搬数据**，群在新 worker 上从磁盘水合即可。

### 10.4 平台分流与传输抽象（IPC）

IPC **按平台用各自最原生的实现**，藏在一个薄接口后；平台差异锁死在 `transport_*.py`，业务侧无感（与现有 `_IS_WINDOWS` 分 `_DEFAULT_SHELL` 同一套路）。

| 平台 | 传输 | asyncio 原语 |
|---|---|---|
| **Mac / Linux** | **UDS** `/tmp/nuke_{name}.sock` | `start_unix_server` / `open_unix_connection` |
| **Windows** | **Named Pipe** `\\.\pipe\nuke_{name}` | Proactor：`start_serving_pipe` / `create_pipe_connection` |

包布局：

```
runtime/ipc/
  __init__.py        # 按 sys.platform 选后端，导出 serve/connect/make_addr + framing
  framing.py         # 长度前缀(u32) + JSON 收发 —— 跨平台，测一次
  protocol.py        # 上/下行消息 schema —— 跨平台
  transport_unix.py  # UDS
  transport_win.py   # Named Pipe（StreamReaderProtocol 包装成 stream）
```

- **铁律**：`framing` / `protocol` / 业务全共享；只有 `serve` / `connect` / `make_addr` 三个函数按平台分叉（各 ~30 行）。
- **逃生口**：Windows named pipe 若难搞，只改 `transport_win.py` 换 loopback TCP（`127.0.0.1` + 启动 token），业务零改动。
- **Windows 沙箱**（独立于 IPC）：`run_shell` 的 `ulimit` 内存限额在 Windows 无等效（既有问题）→ 用 Job Objects 或部署走 WSL/容器兜底。

---

## 11. 仍待用证据验证（Spike / 度量）

> §10 已把"单库 vs 每群一库"与"K/路由方式"敲定，下列为仍需实测的开放项。

1. **UDS / 广播链路（Phase 0 spike）**：worker bus 事件→UDS→Supervisor→模拟前端，**单次往返 < 100ms**。已写 `backend/spike_uds_bridge.py`（真·跨进程 + 真 EventBus + 自带断言/超时），待运行取数。
2. **工作负载并行画像**：run_shell 已 fork、LLM 是 I/O——多进程买的究竟是"故障隔离"还是"吞吐并行"？加 **event-loop lag 监控**，量单进程多群并发被纯 Python CPU 阻塞多久，据此定 **K**。
3. **Supervisor 作为 WS 单点**的扇出能力与队头阻塞——几百群高并发流下是否扛得住（复用 DFT-030 发送超时思路，但现在在 Supervisor 层）。

---

## 12. 执行 Backlog（CELL-xx · 单一可跟踪真相源）

> 每完成一个 CELL 就更新此表（状态 + commit）。strangler-fig：每项落地后现有单进程 app 仍可跑，直到 CELL-22 才翻入口。
> **进度（2026-05-31）**：已完成 13 · 未做 10。**cell 已能端到端跑一个 bot**（同进程），但**真多进程 + 浏览器 WS 终止 + 调度尚未接**（CELL-13/16/22）。

### Epic 0 · 去风险（门禁）

| CELL | 内容 / 交付物 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|
| 🚦 01 | UDS / 广播链路 spike：worker bus 事件→IPC→Supervisor→模拟前端闭环；**两套传输各测**（UDS + Windows 命名管道） | — | 闭环 PASS 且**单次往返 < 100ms**（实测记录） | ⛔ 待办（`spike_uds_bridge.py` 在工作树，未正式测延迟/未提交；CELL-08 单测已覆盖 UDS 功能正确性） |
| 🚦 02 | event-loop lag 度量 + 负载场景 → 定 **K** | — | 拿到单进程多群并发的 p99 loop lag；据此判"隔离 vs 并行"并定 K | ⛔ 待办 |

### Epic 1 · 数据层（Phase 1-2）

| CELL | 内容 / 交付物 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|
| 03 | `db.writer` 按 `(loop_id, db_path)` 参数化：`write_connect(path)` / `aclose_writer(path)` | — | 不同库独立锁/连接；同库串行；向后兼容 | ✅ `675c006` |
| 04 | contextvar 路由：`db/context.py`（`bind_db`/`current_db_path`）+ `connect`/`write_connect` 解析 + `global_db()` 绕过绑定 | 03 | 绑定后读写落群库、`global_db` 走中心、子任务继承、退出重置 | ✅ `a1a0f46` |
| 05 | schema 分域：`db/schema_split.py`（`CENTRAL_TABLES`/`GROUP_TABLES` + `init_central_db`/`init_group_db`，群表去跨域 FK） | 04 | 分区正确、跨域 FK 丢弃、域内 FK 保留、群库独立 | ✅ `2fc25ab` |
| 06 | 一次性数据 splitter `db/split_tool.py`：legacy → central + 各 group_{id} | 05 | 分区/隔离/id 保留/行数守恒 | ✅ `4666f66` |
| 14b | 查询层分域：sender 字段反规范化进 messages（migration_014 回填 + save_message 快照 + `_MSG_SQL` 去 members JOIN） | 05 | get_messages 在群库无 members 表下可用；bot 端到端跑通 | ✅ `1e5adcb` |

### Epic 2 · IPC 传输

| CELL | 内容 / 交付物 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|
| 08 | `runtime/ipc/`：framing（长度前缀 JSON，依赖纯净）+ transport_unix（UDS）+ transport_win（命名管道）+ `__init__` 按平台选 | 01 | 分帧往返 / 超大帧拒绝 / 原生传输端到端 | ✅ `01d7953`（`transport_win` 待 Windows runner 验收 ⚠️） |
| 09 | 隧道协议 schema：上/下行消息类型 + `envelope`（group_id/trace_id 头）；HELLO 控制帧 | 08 | 通道划分 / envelope；*待补：从 `bus/events.py` 自动生成 + 28 事件契约测试* | 🟡 基本完成 `01d7953`/`d13c216`（自动生成未做） |

### Epic 3 · Supervisor / Worker 切分

| CELL | 内容 / 交付物 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|
| 10 | `runtime/worker.py`：下行循环（user_message→bind 群库→dispatch；abort）+ 上行 pump（bus→IPC broadcast）；`runtime/dbpaths.py` | 04,09 | user_message→上行 broadcast；abort 路由 | ✅ `acb89a8` |
| 11 | Worker 端 bus adapter（上行发 IPC 而非 WSManager） | 09,10 | — | ✅ 并入 CELL-10 上行 pump |
| 12 | `runtime/supervisor.py`：IPC server（HELLO 注册）+ 下行 `send_to_worker`（route）+ 上行扇出到浏览器客户端 + unread 钩子 | 09,11 | 浏览器→Worker→浏览器端到端、群隔离、无 worker 报错 | ✅ `d13c216` |
| 13 | APScheduler 上移 Supervisor，到点下发 `wake_trigger` | 12 | cron → worker 唤醒 → 跑一轮 | ⛔ 待办 |
| 14 | 瘦启动器 `runtime/entry.py --role`（build/run factory）+ worker 真实 dispatch `runtime/dispatch.py`（中心读 members + 群库写） | 04,09,12 | 两 role 可构造；dispatch 跑通 bot（见 14b） | ✅ `986c4d1`（+14b 解锁 e2e）。**WS 终止壳 + 调度仍是 TODO（run_supervisor）** |

### Epic 4 · 分片池 / 路由 / 生命周期

| CELL | 内容 / 交付物 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|
| 15 | `runtime/router.py`：`groups.assigned_worker_id` 显式分配 + 查询 | 12 | 新群分配；查询稳定 | ✅ `3804e51` |
| 16 | 多进程派生：Supervisor 起 K 个 worker 进程，按分配表路由 | 14,15 | 2 worker/2 群跨进程消息正确落位 | ✅ \`8c9de02\` |
| 17 | `runtime/lifecycle.py`：群懒水合 + 空闲驱逐（snapshot+清内存+关私有库）+ **私有库连接 LRU 上界** | 16 | 沉睡群唤醒；空闲驱逐；LRU 封顶 | ⛔ 待办 |
| 18 | 租约 + 干净交接（drain→snapshot→关→ack→改派→open） | 15,17 | **不变量：任何时刻一群私有库仅一个 worker 持写连接** | ⛔ 待办 |

### Epic 5 · 横切 / 运维

| CELL | 内容 / 交付物 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|
| 19 | trace_id 贯穿下行→worker→上行 + 结构化日志（JSON，**独立日志通道**，不挤业务隧道） | 09 | `grep trace_id` 跨进程串一个请求 | ⛔ 待办 |
| 20 | `/api/system/status`（DFT-057）跨 worker 聚合 bg/ws/权限指标 | 16 | status 返回每 worker + 汇总 | ⛔ 待办 |
| 21 | Supervisor 扇出复用 DFT-030 发送超时，慢客户端不拖垮其他 | 12 | 慢客户端不阻塞他人 | ⛔ 待办 |

### Epic 6 · 切换收口

| CELL | 内容 / 交付物 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|
| 22 | Cutover：FastAPI WS 终止壳接 Supervisor + 默认入口切 runtime + 删旧单进程路径/spike | 全部 | 全系统跑在 cell 架构上，旧路径移除 | ⛔ 待办（**"真正能对外起服务"在这一步**） |
| 23 | Windows 沙箱策略（run_shell 内存限额：Job Objects 或部署走 WSL/容器）— 可选，独立于 cell 主线 | — | — | ⛔ 待办（低优先） |

### 关键路径

```
01,02（门禁）→ 数据层(03→04→05→06→14b) ∥ IPC(08→09)
   汇合 → S/W 切分(10→12→14 ✅ · 13) → 分片池(15→16→17→18)
        → 横切(19,20,21 可并行) → Cutover(22)
```

> **当前位置**：数据层 + IPC + S/W 切分（同进程）已通，**bot 能跑**。下一关键跃迁是 **CELL-22（WS 终止壳，可对外起服务）**；CELL-13（调度）相对独立可穿插。
