# Nuke AI Collaborator · 架构 Review（合并版）

> 日期：2026-05-31
> 范围：backend（Python · FastAPI，~7200 行核心）+ frontend（React 19 · Vite · Tailwind v4，~4270 行）
> 衡量标准：**长期演进 / 生产档**（耦合、抽象边界、可测试性、可扩展性、可靠性）
> 本文合并两份独立 review：
> - **A · 系统性结构 Review**（跨栈架构主线、可扩展性、协议契约、god-object）
> - **B · 具体缺陷 + 业务 Gap + UX Review**（当前运行时 bug、业务应然、视觉重塑）
> 两份几乎不重叠——A 看"结构与演进上限"，B 看"此刻的 bug 与业务/观感"。合并后按优先级去重排列。
>
> 说明：本文**只收录 `defect_list.md` 之外的新洞察**；已记录条目（DFT-xxx）仅在"状态可信度"一节按需引用，不重复。

---

## 0. 总体评价

一个**功能密度极高、后端工程自觉性很强的单进程单租户 monolith**。它早已越过"玩具"——EventBus 解耦、可插拔 executor/orchestrator、声明式 stage、权限管线、崩溃恢复（影子持久化）、token 全链路记账、trait 原子挂载，加上 `defect_list.md` 这套自我审查纪律本身，成熟度超出一般项目。

问题不在功能，而在它正卡在"演进为平台"的门槛上：**已经积累了两套并行协调机制 + 大量进程级内存状态 + 一处断在主路径上的具体 bug**。骨架的"形"是对的，债集中在"还没收口"和"假设单机"。

---

## 1. 业务背景与架构对齐（来自 Review B，作为全文前提）

协作模型围绕**群组项目**展开，多角色 Bot 流水线：

```
人类 → BA Bot（分析需求，写 BOARD.md / SPEC.md）
      → 架构 Bot（写方案 → 群组共享工作区）
      → 发现新 Ticket → Dev Bots（竞速 / 经验领卡）
      → 自测 & 写 deliverables/ → 自动触发 CodeCommitted
      → QA Bot（本地拉环境验证）→ 反馈人类
```

四个与之对齐的**有意设计**（重要：以下不是事故，是刻意架构，评审建议是"加固"而非"推翻"）：
1. **工作区重定向**：`BOARD.md` / `SPEC.md` / `API_CONTRACT.md` / `deliverables/` 重定向到**群组共享区**，Bot 私有日志/记忆留在私有目录，防上下文污染。
2. **VFS 读写锁**：并发读写看板/代码引入 asyncio 细粒度文件锁。
3. **动态端口分配**：Dev/QA 自测时拦截硬编码 8000/3000，动态分配并注入环境变量。
4. **影子持久化 + 每跳挂载最新看板**：WAL 支持断点续跑；推理大循环每一跳动态挂载最新 BOARD，使 Bot 感知最新全局状态。

> 这段业务背景是 Review A 开场缺失的上下文。它**重新定性**了下文 §4.3 对 RDManager/BOARD.md 的批评：markdown 当协调面是刻意为之，建议方向是"加固解析与并发"，而非"它是错的"。

---

## 2. 贯穿全栈的 5 条系统性主线（Review A）

这是全文的骨架。下面所有单点缺陷，几乎都能归到这五条之一。

| # | 主线 | 后端表现 | 前端表现 |
|---|---|---|---|
| ① | **每层中心都坐着一个 god-object** | `tool_loop_v1.run`（802 行，DFT-036）、`dispatch_bots` | `ChatWindow`（近 40 个 useState）+ `handleWsMessage`（25 分支） |
| ② | **同一份契约两端严谨度不对称** | WS 事件 typed + 注册（28 种） + 重度测试 | stringly-typed if-else 消费 + **零测试** |
| ③ | **协调状态全在内存 / 单进程** | bus、权限 pending、bg、steer、workflow 状态、各 registry 全是模块全局；零横向扩展原语 | localStorage 身份；重连无事件补偿 |
| ④ | **用 LLM 自由文本当控制平面** | RDManager 正则解析 BOARD.md、`check_handoff` 扫 @mention、角色字符串匹配 | — |
| ⑤ | **端到端无身份 / 多租户** | 无 auth，权限路由仅边界校验 | 名字 + localStorage 即身份 |

---

## 3. 🔴 当前运行时 Bug（Review B + 核实）

### 3.1 🔴 `group_ws` NameError —— 共享文件路径直接崩溃

- **文件**：`backend/workspace/__init__.py:70`
- **问题**：`return group_ws(row[0])`，但本模块 `group_ws` **在该作用域未定义**——模块级函数名是 `group_workspace`（`:40`），`group_ws` 只是另一个函数里的局部变量（`:242`）。
- **影响**：任何真实 bot 读/写 `BOARD.md` / `SPEC.md` / `API_CONTRACT.md` / `deliverables/`（即 §1 的整条协作主路径 + 前端 WorkspacePanel 加载/保存）都会 `NameError → 500`。
- **核实**：✅ 已确认属实。讽刺的是它正坐在 Review A 大篇幅批评的 BOARD.md 协调路径上——结构层说它"脆弱"，但它**此刻根本是断的**。
- **修复**：`group_ws(row[0])` → `group_workspace(row[0])`；补一个覆盖共享文件重定向的回归测试。
- **教训（给 Review A）**：系统性 review 对"逐文件运行时 bug"的扫描密度不足（未逐读 `workspace/`、`api/`、`workspace_tools.py`）。建议补一轮文件级扫描。

---

## 4. 后端 · 设计层（Review A）

### 4.1 🔴 两套编排系统并存，职责重叠、无单一真相源

同时存在两条独立的多 Bot 协调路径，**同时活着、语义重叠、互不知情**：

| | 旧路径 | 新路径 |
|---|---|---|
| 入口 | `core/orchestrator.py` · `dispatch_bots` / `check_handoff` / `auto_continue_if_needed` | `core/orchestration/` · `Orchestrator` ABC + `stages.py` + `RDManager` |
| 协调机制 | 硬编码字符串：`"开发" in role`（`:240`）、`"测试" in role`（`:288,343`）、扫消息 `@mention` | 事件驱动：`TicketCreated → dev bot`（`:32`）、`CodeCommitted → qa bot`（`:69`） |
| 角色 taxonomy | 中文子串模糊匹配 | 英文精确相等（`role=="dev"/"qa"`） |

- `check_handoff`（看 @mention 文本）与 RDManager+事件（看 BOARD.md 表格）都在做"谁完成→通知下一个"，同一动作可能各触发一次或都不触发。
- **角色约定中英不一致**：role 写"测试工程师"在旧路径命中、新路径漏判；写"qa"则相反。加一种角色要在两套约定里都对齐——扩展性隐形杀手。
- **建议**：收敛到声明式 `Orchestrator`（已有 `register_stage_type` 插件点 + `serialize/restore`），把 handoff/续写下沉为 `StageType` 或 orchestrator 插件；角色改**能力标签（capability tags）**集中定义。

### 4.2 🟠 Dev Bot 派单：经验匹配未实现（Review B 业务 Gap + Review A 结构）

- **文件**：`core/orchestrator.py:39` · `target_bot = dev_bots[0]`（注释自陈 "select the first one for now"）
- **业务应然（B）**：忽略了"多卡主动抢占"、"单卡最优经验分配（Expertise Match）"、"无匹配随机分派"的认领规则。
- **结构（A）**：这正是 §4.1"硬编码角色逻辑"的一个实例。
- **建议**：基于 Dev Bot 的 role/traits 关键字对 Ticket 描述做 `_expertise_score` 打分派单；该逻辑应落在收敛后的编排器/StageType 内，而非散在事件 handler。

### 4.3 🟠 RDManager 用 Markdown 文件（BOARD.md）作为协调真相源

> 前提：§1 已说明文件重定向是有意设计。以下是对其**健壮性**的加固建议，非否定。

- **三处真相源 best-effort 对账**：`BOARD.md`（文件）↔ `tickets` 表（DB）↔ `_last_tickets`（内存，`rd_manager.py:23`）。任一步异常即漂移。
- **解析 LLM 生成的 Markdown 表格**（`TICKET_RE`，`:13`）——DFT-016/041 同类脆弱性：少一列 / 状态带空格 / 全角竖线 → ticket 静默丢失。
- **read-modify-write 非原子**：`_perform_archiving` 先 `to_thread(read_text)` 读、改完 `write_file(0,...)` 写回（`:99,147`），其间任意 bot 写 BOARD → 丢失更新。VFS 锁只保护单次 write，保护不了这段跨 await 的读-改-写。
- **每次 write_file 全量重扫**：`_listen` 对任意 write_file ToolResult `sleep(0.5)` 后整板重解析+DB 同步（`:57-61`），O(写次数) 全量重算且 0.5s 串在单订阅者里。
- **`bot_id=0` 魔法数**散落多处当"系统"。应命名常量 `SYSTEM_ACTOR_ID`。
- **建议**：ticket 状态变更走**显式工具/事件**（`update_ticket(id, status)`）而非"写文件→正则猜意图"；BOARD.md 降为 **DB 的单向渲染产物**，消除三方对账 + 把 LLM 自由文本挡在结构化状态外。

### 4.4 🟠 DI 重构（InteractionAdapter）半迁移 + AIService 泄漏领域概念

- **`broadcaster` 与 `interaction` 并存**：`ExecutionContext` 同时带 deprecated 的 `broadcaster`（`base.py:105`）和 `interaction`（`:106`），orchestrator 两个都塞（`:363-364`）；`StandardInteraction.broadcast` 只是转调 `bus.broadcast`（`interaction.py:18`）。抽象层目前**只有一个实现且纯转发**——测试替身收益没兑现，却付出"两条广播路径要同步维护"的成本。
- **通用 AI 层泄漏 Jira 概念**：`AIService._sync_tokens` 直接 `getattr(ctx, "active_ticket_id")` 把成本挂到 `tickets` 表（`ai_service.py:168-176`、`interaction.py:42-49`）。一个本应"只管推理/流控/token"的服务层硬编码了特定工作流的成本归集规则。
- **建议**：补 `FakeInteraction` 兑现单测脱钩（或删 deprecated `broadcaster`）；成本归属通过通用回调上交上层决定，AIService 不认识 ticket。

---

## 5. 后端 · 实现层（Review A）

- 🟠 **`simple_v1` / `react_v1` 漂移 + 静默降级**：两 executor 已从磁盘删除，但仍被引用——`registry.get()` fallback 到不存在的 `simple_v1`（`registry.py:73`）、`orchestrator.py:375,408` 把它们映射到 `tool_loop_v1`、`WorkUnit.executor_id` 默认仍是 `"simple_v1"`（`base.py:19`）、README/defect_list（DFT-037）还在讲"三个 executor"。配成这俩 id 的 bot **静默降级**，用户无感。**建议**：彻底清理引用 + 存量 bot executor_id 数据迁移；`registry.get` 对未知 id 显式告警而非静默兜底；加 CI grep 防漂移。
- 🟡 **`core/orchestrator.py` 文件结构紊乱**：顶部先定义 `init_event_handlers()`（`:14-112`），**之后**才出现模块级 import（`:113-122`），中间夹一个字面量 `...`（`:12`）。merge 疤，import 顺序反直觉。建议 import 全提到文件头。
- 🟡 **函数内 import 泛滥**：`from db import connect`、`from ai.pricing import calculate_cost`、`from .constants import TRAITS_ROOT` 散在函数体（`rd_manager.py:29,107`、`ai_service.py:160`、`traits.py:11`）。量大 = 分层有环（core ↔ db ↔ executors）。建议画依赖图、用依赖倒置打断环，而非延迟 import 掩盖。
- 🟡 **防失控阀值用魔法数**：`_depth>5`、`max_iter=5`、`_MAX_FOLLOWUP_DEPTH=5`。这些是 agent 安全阀，应集中配置，触顶时发可观测事件（现在静默 `return`，用户看不到"为什么 bot 不接着干了"）。
- 🟡 **EventBus 无背压**：订阅队列无 `maxsize`（`engine.py:80,94`）。DFT-030 只在 WSManager 侧补了发送超时；慢 typed 订阅者（如 RDManager 带 `sleep(0.5)`+DB）队列仍可无界增长。建议队列设上限 + 满时降级策略。

---

## 6. 前端 · 架构层（Review A）

- 🔴 **`ChatWindow` 是 god component**：793 行、近 40 个 `useState`（`:20-58`）、一个 25 分支 `handleWsMessage`（`:170-265`）直接驱动十几个 state slice。前端版 DFT-036。`MemberList`（755 行）同样在膨胀。**建议**：WS 事件流收进 `useReducer`/store，`handleWsMessage` 退化为 `dispatch(event)`；按域拆 `useGroups/useMessages/usePresence/useWorkflow`。
- 🔴 **同一 WS 协议两端无共享契约**：后端 28 种 typed event + 注册表，前端 `data.type` 字符串 + if-else 链消费，中间无共享 schema。加事件要改 `events.py` 又要记得加前端分支——DFT-006（`chunk` vs `delta`）类 bug 的结构来源，且只能运行时暴雷。**建议**：从 `events.py` 生成共享事件契约；前端 handler 表化（`const handlers = {stream_chunk: ...}`）；上 TypeScript（本项目"有状态 WS + 25 变体协议"正是 TS 收益最大场景）。
- 🟠 **断线重连无事件补偿**：`useWebSocket` 3s 重连（`:33`）但无 catch-up；后端 bus fire-and-forget 不持久化。**一次 WS 抖动 = 该客户端永久丢失这段时间的消息/状态**，直到手动切群 refetch。**建议**：消息引入单调 seq，重连带 `last_seq` 回放缺口，或重连后对 active group 强制增量 refetch。
- 🟠 **`handleWsMessage` 闭包陈旧陷阱**：`socket.onmessage` 闭包捕获上次 connect 时的 `onMessage`（`useWebSocket.js:19-21,41`）。目前靠"几乎全用函数式 `setState`"绕开，是"靠纪律维持的隐患"——将来谁直接读一个 state 变量就会读到切群那刻的陈旧值。**建议**：`useRef` 持有最新 handler，`onmessage` 调 `ref.current(data)`。
- 🟡 **缓存一致性手工且不齐**：`messages`/`messagesCache`/`reactionCache`/`membersCache` + `syncCache`，每次变更要记得两边都更。`stream_chunk`（`:189`）只更 `messages` 不更 cache → 流式中途切群再回半截消息丢失；`message_edited` 两边都更。25 分支纪律不统一。建议同样收进 reducer 统一处理。
- 🟡 **硬编码配置 / 无 ErrorBoundary**：`ws://localhost:8000`（`useWebSocket.js:11`，非 wss/非 env）生产即挂；`addMember(1,...)` 硬编码 group 1（`App.jsx:20`）；render 一处 throw 白屏整个应用（无 ErrorBoundary）。
- 🟡 **前端零测试**：后端成体系 pytest，前端**一个测试文件都没有**——而 DFT-001/002/012/013/014 全是前端 bug。复杂度最高、最缺安全网的一侧。

---

## 7. 前端 · UX / 视觉重塑（Review B · Wow Factor）

代码质量高、组件隔离良好，但默认风格偏多，科技感欠缺。基于 Tailwind v4 的五项重塑：

| 模块 | 现状 | 重塑方案 |
|---|---|---|
| 字形 | 浏览器默认 Sans-serif | 加载 **Inter / Outfit**（Google Fonts），排版更高端 |
| Bot 头像 | 与人类无区分 | **AI 身份角标**：微渐变呼吸边框 + 右下角 `🤖` |
| 浮层弹窗 | 普通暗色块 | **毛玻璃拟态**：`bg-gray-900/75 backdrop-blur-md` + 极细金属灰渐变边 |
| 滚动条 | 系统默认（Win 端突兀） | 暗色无边界 5px 极窄、半透明灰紫、滚动时隐现 |
| shell 输出 | 普通 Markdown 块 | **终端盒**：JetBrains Mono + 深黑底 + 高亮绿提示符 |

> 此轴与 Review A 的架构关注点正交，可作为独立的前端体验迭代并行推进。

---

## 8. 可扩展性专章（Review A · 最高天花板）

你最关心的"未来可扩展性"，**第一约束在主线 ③**。所有协调状态都是模块级 Python 全局，且无任何横向扩展原语（确认无 redis/celery/多 worker）：

```
bg._bg_tasks / _group_tasks        permissions._pending / _once_grants
orchestrator._steer_queues         workflow._group_orch
bus._typed / _wildcard 队列         ws_manager 连接表
executors registry / tool handlers RDManager._last_tickets
compact._db_compaction_locks
```

含义：**永远只能跑单个 uvicorn worker**。一旦 `--workers 2`：WS 连接落 worker A、bot 执行落 worker B，EventBus 跨不过进程、权限 ask 的 future 在别的进程永远 resolve 不了、steer/abort 全失效。`database is locked`（DFT-029/053）只是这个根因的症状——真正天花板是**所有运行时协调都假设单事件循环**。

> Review B 通篇未提 scaling——这是两份 review 互补的关键一块。

**建议（现在就划界，不必马上实现）**：
- 把进程级状态抽象成 `StateStore` 接口（内存实现=现状，未来 Redis 实现=可扩展）；
- EventBus 抽象成 `Broker` 接口（内存=现状，未来 Redis Pub/Sub / NATS）；
- **关键：现在就别让业务代码直接 `dict[group_id]` 读写全局**，否则迁移成本随调用点数量线性爆炸。这比修任何单个 DFT 都更决定演进上限。

---

## 9. defect 流程的元观察（Review A）

`defect_list.md` 的纪律很可贵，但发现**"已修"状态与代码现实漂移**，影响清单可信度：

- **DFT-048 标"✅已修"但代码自陈未修**：`orchestrator.py:452-461` 留着坦白注释——race 路径 loser token "doesn't use sessions yet … we just ensure the winner's usage is correct"，loser 成本依旧没入账。git `d51f95a` 说 resolve 了，代码说没有。**Review B 也照抄了"048 已修"**——同一个陷阱。**建议**：要么真修，要么把状态改回未修。
- **Review B §2.4「12 个测试失败」已过时**：其建议的修复（mock 目标改指 `core.orchestration.ai_service.call_ai_stream_messages`）现已在代码（`test_recovery_resume.py:98`、`test_abort_signal.py:80`）。✅ 核实：`pytest tests/test_abort_signal.py tests/test_recovery_resume.py` → **2 passed**。该条不再成立。
- **建议**：加 CI 检查——grep 已删除的 executor_id/类名（防 §5 的 simple_v1 漂移）；"删除即全链路清理"作为纪律。

---

## 10. ✅ 值得肯定（公允）

- 后端 EventBus typed+wildcard 双通道解耦，adapter 是唯一知道 WSManager 的地方——边界清晰。
- 声明式 stage 插件（`register_stage_type`，核心无 `if stage_type==`）是真正可扩展的设计。
- `Orchestrator` ABC 带 `serialize/restore/resume_units`——把崩溃恢复做进契约层，少见的成熟度。
- 权限管线语义周到（deny→allow→once→ask；子 agent 拒交互；once-grant 按 `(bot,group,tool,args_hash)` 精确消费）。
- token 全链路记账 + trait 原子挂载 + 影子持久化断点续传。
- 前端组件隔离良好、交互完成度高。

---

## 11. 跨栈优先级矩阵

| 优先级 | 主线 | 动作 | 来源 |
|---|---|---|---|
| **P0** | 3.1 | 修 `group_ws` NameError（一行）+ 回归测试 | B |
| **P0** | ③ | 进程级状态抽象成 `StateStore`/`Broker` 接口；业务代码停止直接读写全局 dict | A |
| **P0** | ① | 后端收敛单一声明式编排 + 统一角色标签；前端 WS 流收进 reducer/store | A |
| **P1** | ② | 从 `events.py` 生成共享事件契约；前端 handler 表化 + 上 TS；补前端测试网 | A |
| **P1** | ④ / 4.3 | ticket 状态走显式事件、BOARD.md 降为渲染产物；角色用能力标签 | A |
| **P1** | 4.2 | Dev Bot Expertise-Match 派单算法 | B |
| **P1** | 5 / 9 | 清理 simple_v1/react_v1 漂移 + 修正 DFT-048「已修」虚标 + CI 防漂移 | A |
| **P2** | 4.4 | DI 兑现测试收益 / 去 deprecated broadcaster；AIService 去 Jira 耦合 | A |
| **P2** | ③(前端) | WS 重连 seq 补偿；硬编码 `ws://localhost` 走 env | A |
| **P2** | ⑤ | 规划身份 / 鉴权层 | A |
| **P2** | 5/6 | 循环依赖、魔法数集中、EventBus 背压、前端 ErrorBoundary、闭包陷阱 | A |
| **P3** | §7 | 前端 UX 视觉重塑（字体 / 毛玻璃 / 滚动条 / 终端盒） | B |

---

## 附录 · 两份 review 的分工

> **Review B 强在"此刻的具体 bug + 业务应然 + UX 观感"**（NameError、派单算法、视觉重塑），**Review A 强在"系统性结构 + 可扩展性 + 跨栈模式"**（单进程天花板、双编排、协议契约、god-object）。两者几乎不重叠——合起来才是完整覆盖。

**仍建议补的一轮**：文件级运行时 bug 扫描（`workspace/`、`api/`、`workspace_tools.py`），弥补系统性 review 对具体 bug 的扫描密度不足（§3.1 的 NameError 即来自此盲区）。
