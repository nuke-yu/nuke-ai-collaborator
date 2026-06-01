# Nuke AI Collaborator · Defect List（第二轮 · 静态扫描 + 架构复查）

> 生成：2026-05-31
> 方法：`pyflakes`（后端 NameError 类）+ `eslint`（前端）+ `py_compile` + 逐文件精读 + 真实执行路径追踪。
> 编号续接 `defect_list.md`（历史到 DFT-057），本轮新增 **DFT-058 ~ DFT-086**。
> 分两部分：**Part A 运行时缺陷**（已核实到行，可复现，**测试前必修**）；**Part B 架构级问题**（结构 / 可扩展性 / 可维护性，源自 `docs/ARCHITECTURE-REVIEW.md`）。

---

## 进度总览（Progress Dashboard）

| 批次 | 范围 | 总数 | 🔴 | 🟠 | 🟡 |
| :--- | :--- | :---: | :---: | :---: | :---: |
| Part A · 运行时缺陷 | DFT-058 ~ 070 | 13 | 5 | 5 | 3 |
| Part B · 架构级问题 | DFT-071 ~ 086 | 16 | 0 | 7 | 6 |
| **合计** | — | **29** | **7** | **13** | **9** |

> **更新 2026-05-31 · 已修 15 项**（Part A 运行时缺陷仅剩 DFT-069 未修；Part B 架构 16 项待排期），均带回归测试，全量 **527 passed in 10s**。剩 14 项未修（运行时 1 · 架构 13）。
>
> ✅ **测试前阻断项已全部清除**：DFT-058~062（曾让主流程直接崩、没有任何 bot 能跑完一轮）+ DFT-066（测试套件卡死跑不出绿色基线）已修复并推送到 main，主流程跑通，可正常测试。

### 已修映射（commit）

| DFT | commit | 验证 |
| :--- | :--- | :--- |
| DFT-058 | `fc8c598` | `tests/test_interaction_fallback.py` + 冒烟 `tests/test_smoke_dispatch.py`（`77c7e5e`） |
| DFT-066 | `fc8c598` | 全量套件 9~10s 跑完并干净退出 |
| DFT-059 | `123d7a7` | `tests/test_workspace_redirect.py` |
| DFT-060 | `a5e1ee2` | `tests/test_fork_skill_usage.py` + token 套件 |
| DFT-061 | `035ed0e` | eslint no-undef 清零 + `npm run build` |
| DFT-062 | `d9e1719` | eslint rules-of-hooks 12→0 + `npm run build` |
| DFT-063 | `f0f331d` | `tests/test_bg_spawn_finalize.py`（spy `core.bg.spawn`） |
| DFT-064 | `8ad5a32` | `tests/test_compact_overflow_pairing.py`（配对边界） |
| DFT-065 | `fdeca39` + `297e046`(test) | `tests/test_port_allocator.py` 词边界用例（子串不误匹配 / 8080 不双替换 / 标准端口仍拦截） |
| DFT-067 | `fdeca39` | registry/base 清除 `simple_v1`/`react_v1` 引用 |
| DFT-068 | `fdeca39` | 删除死赋值 `messages`（recovery）/`sections`（rd_manager） |
| DFT-070 | `fdeca39` | CORS `allow_origins` 限定 localhost 白名单 |
| DFT-072 | b679774 | CELL-ISOLATION-V3 成功落地，实现 Supervisor + Worker 多进程分片 |
| DFT-073 | b679774 | 引入 UDS 隧道长度前缀 JSON 帧协议，确立了前后端共享契约的服务端基石 |
| DFT-013 | b679774 | APScheduler 上移至 Supervisor，解决多进程下重复调度问题 |

### 状态索引

| ID | 严重度 | 模块 | 一句话 | 核实 |
| :--- | :---: | :--- | :--- | :---: |
| DFT-058 ✅已修 | 🔴 | 编排/执行 | DI `interaction` 未接入任何生产构造点 → 全路径崩 | `fc8c598` |
| DFT-059 ✅已修 | 🔴 | 工作区 | `group_ws` 未定义（应 `group_workspace`） | `123d7a7` |
| DFT-060 ✅已修 | 🔴 | 执行引擎 | `_total_*_tokens` 未初始化即 `+=` | `a5e1ee2` |
| DFT-061 ✅已修 | 🔴 | 前端 | `resumeSession`/`cancelSessionRecovery` 未 import | `035ed0e` |
| DFT-062 ✅已修 | 🔴 | 前端 | `WorkspacePanel` hooks 条件调用 | `d9e1719` |
| DFT-063 ✅已修 | 🟠 | 后台任务 | DFT-025 未完成：多处裸 `create_task` | `f0f331d` |
| DFT-064 ✅已修 | 🟠 | 上下文压缩 | 溢出恢复守卫死代码（DFT-035 保护仍生效） | `8ad5a32` |
| DFT-065 ✅已修 | 🟠 | 沙箱 | run_shell 端口拦截子串误匹配改坏命令 | 正则边界匹配 |
| DFT-066 ✅已修 | 🟠 | 测试 | 全量 `pytest` 跑不完（hang），无绿色基线 | `fc8c598` |
| DFT-067 ✅已修 | 🟠 | 执行注册 | `simple_v1`/`react_v1` 漂移 + 静默降级 | 已清理引用 |
| DFT-068 ✅已修 | 🟡 | 多处 | 死赋值 `messages`/`sections` | 已删除 |
| DFT-069 | 🟡 | 前端 | set-state-in-effect ×8 / render 内 Date.now / 闭包陈旧 | ✅ eslint |
| DFT-070 ✅已修 | 🟡 | 入口 | CORS `allow_origins=["*"]` | 限 localhost |
| DFT-071 ✅已修 | 🟠 | 编排 | 两套并行编排系统并存、角色 taxonomy 中英不一致 | 7216 |
| DFT-072 ✅已修 | 🔴 | 全局 | 进程级内存状态锁死单 worker（无横向扩展） | b679774 |
| DFT-073 ✅已修 | 🟠 | 协议 | 前后端 WS 事件无共享契约 | b679774 |
| DFT-074 ✅已修 | 🟠 | 前端 | `ChatWindow` god component（~40 useState / 25 分支） | f1af309 |
| DFT-075 | 🟠 | 执行引擎 | `tool_loop_v1.run` 802 行 god method（DFT-036 名义已修） | 架构分析 |
| DFT-076 ✅已修 | 🟠 | 编排 | RDManager 用 BOARD.md 当真相源（三方对账/正则/非原子 RMW） | 7b8ee63 |
| DFT-077 | 🟠 | 执行引擎 | DI 半迁移（broadcaster+interaction 并存）；AIService 泄漏 Jira 领域 | 架构分析 |
| DFT-078 | 🟠 | 前端 | WS 断线重连无事件补偿 → 抖动即永久丢事件 | 架构分析 |
| DFT-079 | 🟡 | 前端 | `handleWsMessage` 闭包陈旧（靠函数式 setState 侥幸） | eslint+分析 |
| DFT-080 | 🟡 | 事件总线 | EventBus 队列无 maxsize / 无背压 | 架构分析 |
| DFT-081 | 🟡 | 前端 | 硬编码 `ws://localhost:8000` / group 1 | 精读 |
| DFT-082 | 🟡 | 全局 | 端到端无鉴权 / 多租户 | 架构分析 |
| DFT-083 | 🟡 | 分层 | 函数内 import 泛滥 = 循环依赖 | 精读 |
| DFT-084 | 🟡 | 编排 | 防失控阀值魔法数 + 触顶静默 return | 精读 |
| DFT-085 | 🟡 | 前端 | 无 ErrorBoundary + 零测试 | 精读 |
| DFT-086 | 🟡 | 流程 | defect 清单"已修"与代码现实漂移（DFT-048 等） | 核实 |

---

# Part A · 运行时缺陷（测试前必修）

## DFT-058 🔴 DI `interaction` 未接入任何生产构造点 —— 当前没有任何 bot 能跑完

- **模块/文件**：`core/orchestrator.py:359,393` · `core/runner.py:62` · `sessions/recovery.py:219` · `executors/plugins/workspace_tools.py:187` · `executors/plugins/tool_loop_v1.py:328`
- **问题**：Stage 3 DI 把 `StandardInteraction` 加进 `ExecutionContext` 契约，`tool_loop_v1` 全程依赖 `ctx.interaction.*`（`:328` 起即 `update_session_status`，**全程无 None 守卫**）。但 **5 个生产构造点没有一个正确传入**：

  | 构造点 | 路径 | 现状 | 崩溃 |
  | :--- | :--- | :--- | :--- |
  | `orchestrator.py:359` | @单 bot 正常回复（主路径） | 写了 `interaction=interaction.StandardInteraction()` 但 `interaction` **未 import** | `NameError` |
  | `orchestrator.py:393` | followup 续跑 | 同上 | `NameError` |
  | `runner.py:62` | 工作流阶段执行 | **没传** | `AttributeError: NoneType` |
  | `recovery.py:219` | 崩溃恢复 resume | **没传** | `AttributeError: NoneType` |
  | `workspace_tools.py:187` | spawn 子 agent | **没传** | `AttributeError: NoneType` |

- **影响**：用户 @bot → `main.py:212` 的 `bg.spawn_group(dispatch_bots(...))` 内 NameError 被 done_callback 吞进日志，**前端毫无反应**。工作流 / 恢复 / 子 agent 同理全崩。**测试桩（`tests/*` 均传 mock interaction）使全套测试假绿，完全掩盖了此问题**——与 DFT-017 同源。
- **修复**：① `orchestrator.py` 顶部加 `from core.orchestration import interaction`；② `runner.py:62` / `recovery.py:219` / `workspace_tools.py:187` 补 `interaction=interaction.StandardInteraction()`。**根治（推荐）**：`tool_loop_v1` 在 `ctx.interaction is None` 时回退 `StandardInteraction()`，或设为 `ExecutionContext` 的 `default_factory`，避免"新增构造点又忘接"。
- **状态**：✅ 已修复（`fc8c598`）—— 采用**根治方案**：`tool_loop_v1.run` 在 `ctx.interaction is None` 时回退 `StandardInteraction()`（单一真相源），并删除 orchestrator 两处未定义 `interaction` 引用；runner/recovery/spawn 经回退自然修好。单测 `tests/test_interaction_fallback.py` + 端到端冒烟 `tests/test_smoke_dispatch.py`（`77c7e5e`，不 mock interaction）。

## DFT-059 🔴 `group_ws` 未定义 → 共享文件路径 500

- **文件**：`workspace/__init__.py:70`
- **问题**：`return group_ws(row[0])`，但本模块该作用域无 `group_ws`（模块级函数是 `group_workspace`，`:40`；`group_ws` 只是 `:242` 另一函数的局部变量）。
- **影响**：真实 bot 读/写 `BOARD.md`/`SPEC.md`/`API_CONTRACT.md`/`deliverables/`（§协作主路径 + 前端 WorkspacePanel）→ `NameError → 500`。
- **修复**：`group_ws(row[0])` → `group_workspace(row[0])` + 回归测试覆盖共享文件重定向。
- **状态**：✅ 已修复（`123d7a7`）—— 单测 `tests/test_workspace_redirect.py`（共享文件重定向 / 私有文件 / 缺成员行回退）。

## DFT-060 🔴 `_total_*_tokens` 未初始化即 `+=` → fork skill 崩

- **文件**：`executors/plugins/tool_loop_v1.py:649-652`
- **问题**：`_total_input_tokens / _total_output_tokens / _total_cache_read_tokens / _total_cache_creation_tokens` 在 fork skill 路径用 `+=`，但全文件**从未初始化**（grep 仅这 4 行）。README 声称走 `_acc_usage`，实际代码用的是这几个未定义变量。
- **影响**：bot fork 子技能时 → `UnboundLocalError`，fork 路径崩。
- **修复**：循环前初始化四个累加器为 0，或统一改用 `_acc_usage`/`AIUsage`。
- **状态**：✅ 已修复（`a5e1ee2`）—— **实际比清单更深**：调用处还漏传必需的 `ai_service`、多传不存在的 `usage_out`（两个 TypeError）。改为传入父 `ai_service`，fork 子调用 token 自动汇入 `ai_service.usage`（最终落库的累加器），删除 `_fork_usage`/`_total_*`。单测 `tests/test_fork_skill_usage.py`，token 套件 39 passed。

## DFT-061 🔴 前端 `resumeSession`/`cancelSessionRecovery` 未 import → 恢复 UI 崩

- **文件**：`frontend/src/components/ChatWindow.jsx:296,305`（定义在 `api.js:129,134`）
- **问题**：`handleResume`/`handleCancelRecovery` 调用这两个函数，但组件顶部 import（`:2`）未包含它们。
- **影响**：崩溃恢复弹窗的"恢复 / 取消"按钮一点 → `ReferenceError`。
- **修复**：在 `ChatWindow.jsx:2` 的 import 里补 `resumeSession, cancelSessionRecovery`。
- **状态**：✅ 已修复（`035ed0e`）—— eslint no-undef 清零 + `npm run build` 通过。

## DFT-062 🔴 前端 `WorkspacePanel` hooks 条件调用 → 切换面板白屏

- **文件**：`frontend/src/components/WorkspacePanel.jsx:5-26`
- **问题**：`const [showSkills] = useState(...)` 后立即 `if (showSkills) return <SkillPanel/>`，早于其余 ~13 个 hooks（`:10-26`）。违反 Rules of Hooks（hooks 数量随渲染变化）。
- **影响**：用户打开技能面板再关闭（切换 `showSkills`）→ React "rendered fewer/more hooks" 崩溃白屏。叠加 DFT-059，此面板前后端双断。
- **修复**：把 `if (showSkills) return ...` 移到所有 hooks **之后**；或把 SkillPanel 切换提到父组件。
- **状态**：✅ 已修复（`d9e1719`）—— early return 移到所有 hooks 之后。eslint rules-of-hooks 12→0 + `npm run build` 通过。

## DFT-063 🟠 DFT-025 未真正完成：多处裸 `create_task`

- **文件**：`executors/plugins/tool_loop_v1.py:776-790`（chroma/summarize/compact/log/archive 5 个）· `sessions/recovery.py:173` · `executors/plugins/workspace_tools.py:481`
- **问题**：DFT-025 声称"所有 fire-and-forget 改走 `bg.spawn`"，但上述仍是裸 `asyncio.create_task`——无引用持有（可被 GC 中途杀）、异常被吞。
- **影响**：finalize 副作用（记忆/摘要/压缩/审计/归档）可能静默丢失或半执行；恢复派发任务可能被 GC。
- **修复**：全部改走 `bg.spawn` / `bg.spawn_group`。
- **状态**：✅ 已修复（`f0f331d`）—— 7 处转 `bg.spawn`（tool_loop finalize 5 个 + recovery `_dispatch_recovery` + workspace_tools `save_rule`，后者用 `bg_spawn` 别名避开局部 `bg` 变量）。`workspace_tools:205` 的局部 `bg`（子 agent，已入 `_bg_tasks` 字典）保留。单测 `tests/test_bg_spawn_finalize.py`。

## DFT-064 🟠 溢出恢复守卫死代码，DFT-035 在此路径可能失效

- **文件**：`executors/plugins/tool_loop_v1.py:400,416`（`nonlocal messages` 声明后从未赋值）· `:463`（`_overflow_recovered` 赋值后从未使用）
- **问题**：overflow 逻辑迁到 `ai_service.stream`（内部 `messages[:]=...`）后，`tool_loop` 这里留了死声明/死变量。`_overflow_recovered` 守卫已不参与控制流。
- **影响**：DFT-035 的"配对保护 + 二次溢出杀 run"在 ai_service 路径是否仍生效需复核；存在孤儿 `tool_use_id` → Claude 400 复发风险。
- **修复**：复核 ai_service overflow 分支是否复用 `_safe_truncate_boundary` 配对保护；删死代码或重新接上守卫。
- **状态**：✅ 已修复（`8ad5a32`）—— **复核结论：配对保护未失效**，它位于 `compact_conversation` 的 split-boundary walk（步过 `tool` 消息，保留段不以孤儿 tool 开头），非独立 `_safe_truncate_boundary`；第二次溢出经 `AIContextOverflowError`（`AIError` 子类）被 `_stream_final` 优雅捕获。故本条为纯死代码清理：删 `_overflow_recovered` + 两处多余 `nonlocal messages`。单测 `tests/test_compact_overflow_pairing.py` 锁住边界配对。

## DFT-065 🟠 run_shell 端口拦截子串误匹配 → 命令被改坏

- **文件**：`executors/plugins/workspace_tools.py:550-558`（`_INTERCEPT_PORTS={"8000","8080","3000","5000","5173","80"}`）
- **问题**：`if p in cmd` 子串匹配 + `cmd.replace(p, port)`。`"80" in cmd` 会命中 `head -80`/`sleep 80`/`1980`/文件名含 80 等，length 降序排只挡了 `8080`-先于-`80`。
- **影响**：bot 命令里任何含端口数字子串被悄悄替换成随机端口 → 命令语义被改坏，难排查。
- **修复**：按 token / 正则边界匹配（`\b(8000|8080|3000|5000|5173|80)\b`）而非子串。
- **状态**：⛔ 未修复

## DFT-066 🟠 全量 pytest 跑不完（hang），无绿色基线

- **现象**：`python3 -m pytest` 多次需强杀（exit 144）；带 `--timeout=20` 仍有进程残留。514 用例可正常 collect（无导入错误），但全量跑不完。疑似 WS 集成 / 子进程沙箱测试 hang（defect_list 自述"WS 集成测试在本机会挂"）。
- **影响**：今天要测试，但回归网拿不到红/绿基线；CI 不可用。**且绿色子集掩盖了 DFT-058 全崩**。
- **修复**：给 hang 用例加 `pytest-timeout` 硬超时并定位根因；隔离/标记需真实 socket 的集成测试；**补一个"真实 dispatch_bots 端到端、不 mock interaction"的冒烟测试**堵住 DFT-058 类漂移。
- **状态**：✅ 已修复（`fc8c598`）—— **真因不是某个 test 卡，是进程退不出**：`aiosqlite` 每个连接跑在独立**非 daemon** 线程，被弃用的连接（如 DFT-063 孤儿任务里 `db.connect()` 的 `finally` 没执行）线程不退 → pytest 打完 `passed` 仍挂死。读（`db.connect`）+ 写（`db.writer`）连接线程在启动前改 `daemon=True`。全量 **521 passed in ~10s** 干净退出。冒烟测试见 `tests/test_smoke_dispatch.py`。

## DFT-067 🟠 `simple_v1`/`react_v1` 漂移 + 静默降级

- **文件**：`executors/registry.py:73`（fallback `simple_v1`）· `executors/base.py:19`（`WorkUnit.executor_id` 默认 `"simple_v1"`）· `core/orchestrator.py:375,408`（映射到 `tool_loop_v1`）· README/defect_list（DFT-037）仍称"三个 executor"
- **问题**：`simple_v1`/`react_v1` 已从磁盘删除，但仍被多处引用；配成这俩 id 的 bot 静默降级为 `tool_loop_v1`，用户无感。
- **影响**：行为静默偏离配置；`registry.get` 未知 id 不告警。
- **修复**：彻底清理引用 + 存量 bot executor_id 数据迁移；`registry.get` 未知 id 显式告警；加 CI grep 防漂移。
- **状态**：⛔ 未修复

## DFT-068 🟡 死赋值

- **文件**：`sessions/recovery.py:121`（`messages = reconstruct_messages(...)` 后从未使用，恢复改两段式后白做）· `core/orchestration/rd_manager.py:151`（`sections` 死字典）
- **影响**：浪费计算 / 可读性差，非崩溃。
- **修复**：删除或接回使用。
- **状态**：⛔ 未修复

## DFT-069 🟡 前端 eslint 异常（29 errors / 4 warns）

- **要点**：`set-state-in-effect` ×8（级联渲染）· `ChatWindow.jsx:692` render 内 `Date.now()`（purity）· `useWebSocket.js:41` `onMessage` 缺依赖（见 DFT-079）· `api.js:107`/`MessageBubble.jsx:94`/`SearchPanel.jsx:53` 等 `no-unused-vars` · `AutoReplyModal.jsx:1` 未用 import。
- **修复**：按 eslint 逐条清理；`set-state-in-effect` 多数可通过派生状态/事件回调消除。
- **状态**：⛔ 未修复

## DFT-070 🟡 CORS 全开

- **文件**：`main.py:73-78` · `allow_origins=["*"]`
- **影响**：对外化前的安全隐患。
- **修复**：收紧到白名单来源。
- **状态**：⛔ 未修复

---

# Part B · 架构级问题（来自 docs/ARCHITECTURE-REVIEW.md）

## DFT-071 🟠 两套并行编排系统并存，角色 taxonomy 中英不一致

- **文件**：旧 `core/orchestrator.py`（`dispatch_bots`/`check_handoff`，`"开发" in role`@`:240`、`"测试" in role`@`:288`）vs 新 `core/orchestration/`（`Orchestrator` ABC + `stages.py` + `RDManager`，`role=="dev"/"qa"`@`orchestrator.py:32,69`）。
- **问题**：两条协调路径同时活、语义重叠、互不知情；角色一处中文子串匹配、一处英文精确相等。
- **影响**：同一完成动作可能各触发一次或都不触发；加一种角色要在两套约定对齐——扩展性杀手。
- **修复**：收敛到声明式 `Orchestrator`（已有 `register_stage_type` + `serialize/restore`），handoff 下沉为 `StageType`；角色改能力标签集中定义。
- **状态**：⛔ 未修复

## DFT-072 🔴 进程级内存状态锁死单 worker（最高可扩展性天花板）

- **文件**：`bg._bg_tasks/_group_tasks` · `permissions._pending/_once_grants` · `orchestrator._steer_queues` · `workflow._group_orch` · `bus._typed/_wildcard` · `ws_manager` 连接表 · `registry` · `tool_executor._handlers` · `rd_manager._last_tickets` · `compact._db_compaction_locks`（均模块全局，无 redis/celery/多 worker）
- **影响**：永远只能跑单 uvicorn worker；`--workers 2` 即 WS/执行跨进程、bus/权限 future/steer/abort 全失效。`database is locked`（DFT-029/053）只是症状。
- **修复**：抽象 `StateStore` / `Broker` 接口（内存=现状，未来 Redis）；业务代码停止直接读写全局 dict。
- **状态**：⛔ 未修复

## DFT-073 🟠 前后端 WS 事件无共享契约

- **文件**：后端 `bus/events.py`（28 种 typed + 注册表）vs 前端 `ChatWindow.jsx:170-265`（`data.type` 字符串 + 25 分支 if-else）
- **影响**：加事件要两端人脑同步，漏了静默无响应；DFT-006（`chunk` vs `delta`）类 bug 的结构来源，仅运行时暴雷。
- **修复**：从 `events.py` 生成共享事件契约；前端 handler 表化；上 TypeScript。
- **状态**：⛔ 未修复

## DFT-074 🟠 前端 `ChatWindow` god component

- **文件**：`ChatWindow.jsx`（793 行，~40 个 useState `:20-58`，25 分支 `handleWsMessage`）；`MemberList.jsx`（755 行）同样膨胀。
- **影响**：可测试性≈0，改一处 blast radius 覆盖整个聊天界面。
- **修复**：WS 流收进 `useReducer`/store，`handleWsMessage` → `dispatch(event)`；按域拆 hook。
- **状态**：⛔ 未修复

## DFT-075 🟠 `tool_loop_v1.run` 802 行 god method（DFT-036 名义已修但未拆）

- **文件**：`executors/plugins/tool_loop_v1.py`
- **问题**：DFT-036 标记"已通过 DI 重构彻底解决"，但 `run` 仍是巨型方法、深层嵌套闭包改 `nonlocal`（并由此产生 DFT-060/064 的未定义/死变量）。
- **修复**：抽 run scaffold 到基类/helper，`run` 只做编排。
- **状态**：⛔ 未修复（DFT-036 状态存疑，见 DFT-086）

## DFT-076 🟠 RDManager 用 BOARD.md 当协调真相源

- **文件**：`core/orchestration/rd_manager.py`（`TICKET_RE`@`:13`、`_listen`@`:57-61`、读-改-写@`:99,147`、`_last_tickets`@`:23`）
- **问题**：三方真相源（文件 / `tickets` 表 / 内存）best-effort 对账；正则解析 LLM markdown（DFT-016/041 同类脆弱）；`_perform_archiving` 跨 await 的读-改-写非原子（VFS 锁保护不了）；`bot_id=0` 魔法数；每次 write_file 全量重扫 + 0.5s 串行。
- **修复**：ticket 状态走显式工具/事件，BOARD.md 降为 DB 单向渲染产物；`SYSTEM_ACTOR_ID` 常量。
- **状态**：⛔ 未修复

## DFT-077 🟠 DI 半迁移 + AIService 泄漏领域概念

- **文件**：`executors/base.py:105-106`（deprecated `broadcaster` 与 `interaction` 并存）· `ai_service.py:168-176` + `interaction.py:42-49`（`active_ticket_id` 把成本挂 `tickets` 表）
- **问题**：抽象层只有一个实现且纯转发，测试替身收益未兑现却付双广播维护成本；通用 AI 层硬编码 Jira 成本归集。（DFT-058 是其运行时后果。）
- **修复**：补 `FakeInteraction` 兑现脱钩或删 deprecated `broadcaster`；成本归属经通用回调上交，AIService 不认识 ticket。
- **状态**：⛔ 未修复

## DFT-078 🟠 前端 WS 断线重连无事件补偿

- **文件**：`useWebSocket.js:33`（3s 重连，无 catch-up）；后端 bus fire-and-forget 不持久化。
- **影响**：一次 WS 抖动 = 该客户端永久丢失这段时间的消息/状态，直到手动切群 refetch。
- **修复**：消息引入单调 seq，重连带 `last_seq` 回放；或重连后强制增量 refetch。
- **状态**：⛔ 未修复

## DFT-079 🟡 `handleWsMessage` 闭包陈旧

- **文件**：`useWebSocket.js:19-21,41`（`socket.onmessage` 闭包捕获上次 connect 的 `onMessage`，eslint exhaustive-deps 实锤）
- **问题**：当前靠"几乎全用函数式 `setState`"侥幸不出错；谁直接读 state 变量就会读到切群那刻的陈旧值。
- **修复**：`useRef` 持有最新 handler，`onmessage` 调 `ref.current(data)`。
- **状态**：⛔ 未修复

## DFT-080 🟡 EventBus 无背压 / 无 maxsize

- **文件**：`bus/engine.py:80,94`（订阅队列 `asyncio.Queue()` 无上限）
- **问题**：慢 typed 订阅者（如 RDManager 带 `sleep(0.5)`+DB）队列可无界增长（DFT-030 只在 WSManager 侧补了发送超时）。
- **修复**：订阅队列设上限 + 满时丢弃/降级策略。
- **状态**：⛔ 未修复

## DFT-081 🟡 前端硬编码配置

- **文件**：`useWebSocket.js:11`（`ws://localhost:8000`，非 wss/非 env）· `App.jsx:20`（`addMember(1, ...)` 硬编码 group 1）
- **影响**：生产/HTTPS 直接挂；单一硬编码入口群。
- **修复**：走环境变量 + 动态 host/协议。
- **状态**：⛔ 未修复

## DFT-082 🟡 端到端无鉴权 / 多租户

- **文件**：`main.py`（WS 无 auth）· `App.jsx:18-23`（名字 + localStorage 即身份）· `permissions/routes.py`（仅边界校验）
- **影响**：对外化前的地基缺失；任何人可冒任何身份。
- **修复**：规划身份 / 鉴权层（当前单机档可暂缓，但需先划界）。
- **状态**：⛔ 未修复（当前档位可接受）

## DFT-083 🟡 函数内 import 泛滥 = 循环依赖

- **文件**：`rd_manager.py:29,107` · `ai_service.py:160` · `traits.py:11` 等（`from db import connect` / `calculate_cost` 散在函数体）
- **影响**：分层有环（core ↔ db ↔ executors），靠延迟 import 掩盖。
- **修复**：画依赖图，用依赖倒置打断环。
- **状态**：⛔ 未修复

## DFT-084 🟡 防失控阀值魔法数 + 触顶静默 return

- **文件**：`orchestrator.py`（`check_handoff` `_depth>5`、`auto_continue` `max_iter=5`、`_MAX_FOLLOWUP_DEPTH=5`）
- **影响**：agent 安全阀散落硬编码；触顶静默 `return`，用户看不到"为什么 bot 停了"。
- **修复**：集中配置 + 触顶发可观测事件。
- **状态**：⛔ 未修复

## DFT-085 🟡 前端无 ErrorBoundary + 零测试

- **问题**：render 一处 throw 白屏整个应用；前端无任何测试文件（DFT-001/002/012/013/014 全是前端 bug）。
- **修复**：加顶层 ErrorBoundary；为 `handleWsMessage`/`useWebSocket` 等有状态核心补测试。
- **状态**：⛔ 未修复

## DFT-086 🟡 defect 清单"已修"与代码现实漂移

- **问题**：DFT-048 标"✅已修"但 `orchestrator.py:452-461` 注释自陈 race loser token 未入账；DFT-036 标已修但 god method 仍在（DFT-075）；DFT-025 标已修但裸 create_task 仍在（DFT-063）；simple_v1/react_v1 删除未全链路清理（DFT-067）。
- **影响**：清单可信度受损，是团队协作基础。
- **修复**：上述各项据实回填状态；建立"删除即全链路清理"纪律 + CI 防漂移检查。
- **状态**：⛔ 未修复

---

## 复现命令

```bash
# 后端 NameError 类（DFT-058/059/060）
python3 -m venv /tmp/lintenv && /tmp/lintenv/bin/pip install pyflakes
cd backend && /tmp/lintenv/bin/python -m pyflakes **/*.py | grep "undefined name"

# 前端崩溃类（DFT-061/062）
cd frontend && npx eslint . | grep -E "no-undef|rules-of-hooks"

# 测试基线（DFT-066：会 hang，需 pytest-timeout）
cd backend && python3 -m pytest --timeout=20 -q
```

## 建议修复顺序

1. ✅ **测试前阻断**（已完成）：DFT-058 → 059 → 060 → 061 → 062 全修，主流程跑通。
2. ✅ **稳健性**（已完成）：~~DFT-063~~（fire-and-forget 收口）· ~~DFT-064~~（死代码清理，保护已复核）· ~~DFT-065~~（端口词边界匹配）· ~~DFT-066~~（测试网 + 冒烟测试）全修。
3. ✅ **杂项清理**（已完成）：~~DFT-067~~（legacy executor 引用清理）· ~~DFT-068~~（死赋值）· ~~DFT-070~~（CORS 收紧）。
4. **剩余运行时**：DFT-069（前端 eslint：set-state-in-effect / Date.now / 闭包陈旧）。
5. **架构 P0**：DFT-072（状态抽象划界）→ DFT-071（编排收敛）。
6. **架构 P1**：DFT-073 / 076 / 077 / 078。
7. **其余 P2/架构** 渐进清理。
