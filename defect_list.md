# Nuke AI Collaborator · Defect List (缺陷清单)

## 进度总览 (Progress Dashboard)

> 更新：2026-05-30 · 全量 **57** 项，已修 **29**，待修 **28**

| 批次 | 范围 | 总数 | 已修 | 待修 |
| :--- | :--- | :---: | :---: | :---: |
| 历史缺陷 | DFT-001 ~ 016 | 16 | 16 ✅ | 0 |
| 架构师 Review | DFT-017 ~ 057 | 41 | 13 ✅ | 28 |
| **合计** | — | **57** | **29** | **28** |

架构师 Review 按严重度：🔴 Critical 7（已修 7）· 🟠 High 12（已修 6）· 🟡 Medium 17 · 🟢 Low 5。

### 状态索引（DFT-017 ~ 052，点 ID 可跳转下方明细）

| ID | 严重度 | 状态 | 一句话 |
| :--- | :---: | :---: | :--- |
| DFT-017 | 🔴 | ✅已修复 | 会话恢复 `dispatch_bots` 签名错误致 TypeError |
| DFT-018 | 🔴 | ✅已修复 | 恢复改走专用入口续跑重建 WAL，不再从头重跑副作用工具 |
| DFT-019 | 🔴 | ✅已修复 | 恢复复用原 session_id，完成/失败回写自然迁出 `recovering` |
| DFT-020 | 🔴 | ✅已修复 | skill `allowed-tools` 白名单 fail-open |
| DFT-021 | 🔴 | ✅已修复 | skill 名路径穿越 / 任意文件读取 |
| DFT-022 | 🔴 | ✅已修复 | 全禁 skill `!` 块执行（选项 A），脚本须走 run_shell 进权限管线 |
| DFT-023 | 🔴 | ✅已修复 | run_shell 档1（cwd 限工作区+env 白名单）+档2（无 ruleset fail-closed）；`*_local_file` 改走需审批工具 + 强化凭据黑名单 |
| DFT-024 | 🟠 | ✅已修复 | 权限钩子无 ruleset 时对需审批工具 fail-closed；react_v1 接齐 ruleset/steer/rewake |
| DFT-025 | 🟠 | ✅已修复 | 新增 `core/bg.py` 登记处：`spawn()` 持有引用+异常落日志，工作流链与各 fire-and-forget 副作用全部改走它 |
| DFT-026 | 🟠 | ✅已修复（缩小范围） | 重构后阶段 handler 已是同步纯函数，stage-dict RMW / active_bot 在单线程 asyncio 下原子，**非真 race**；真正残留的是钩子表并发 `execute()` 边迭代边 remove → 已改快照迭代 + `once` 钩子 claim-before-fire |
| DFT-027 | 🟠 | ✅已修复 | `bg.spawn_group(group_id, …)` 把 dispatch 与 runner 派生的工作流单元都登记到同群任务集；abort 改 `bg.abort_group(group_id)` 取消整组 |
| DFT-028 | 🟠 | ✅已修复 | 统一 `db.connect()` helper 连接即 `PRAGMA foreign_keys=ON`，所有 store 走该 helper |
| DFT-029 | 🟠 | ✅已修复 | 同一 helper 连接即 `journal_mode=WAL`+`busy_timeout=5000`，缓解并发 `database is locked` |
| DFT-030 | 🟠 | ⛔未修复 | 全局单消费队列队头阻塞 |
| DFT-031 | 🟠 | ⛔未修复 | 权限 future 无 timeout、resolve 无鉴权 |
| DFT-032 | 🟠 | ⛔未修复 | once 规则进程级全局永久放行 |
| DFT-033 | 🟠 | ⛔未修复 | httpx client per-call、流未关闭 |
| DFT-034 | 🟡 | ⛔未修复 | tool_result `is_error` 硬编码 False |
| DFT-035 | 🟡 | ⛔未修复 | 溢出恢复拆散 tool 配对（DFT-003 同源） |
| DFT-036 | 🟡 | ⛔未修复 | `run()` 600 行 god method |
| DFT-037 | 🟡 | ⛔未修复 | 三 executor 复制生命周期已漂移 |
| DFT-038 | 🟡 | ⛔未修复 | 迁移 `except` 吞所有异常仍记成功 |
| DFT-039 | 🟡 | ⛔未修复 | 调度器重启 >1min 静默丢任务 |
| DFT-040 | 🟡 | ⛔未修复 | 调度器无 timezone，DST 平移 |
| DFT-041 | 🟡 | ⛔未修复 | 解析失败伪造单 ticket 静默推进 |
| DFT-042 | 🟡 | ⛔未修复 | `_trigger_pool_stage` 无幂等守卫 |
| DFT-043 | 🟡 | ⛔未修复 | 记忆 `try/except: pass` 静默失效 |
| DFT-044 | 🟡 | ⛔未修复 | 权限 `_matches` 嵌套参数失配可绕 |
| DFT-045 | 🟡 | ⛔未修复 | 手写 YAML 解析器丢字段 |
| DFT-046 | 🟡 | ⛔未修复 | skill 发现同步 IO 阻塞事件循环 |
| DFT-047 | 🟡 | ⛔未修复 | ws broadcast 递归改 dict 无锁 |
| DFT-048 | 🟡 | ⛔未修复 | 竞速 loser token 不计入成本 |
| DFT-049 | 🟢 | ⛔未修复 | 插件 import 错误静默吞 |
| DFT-050 | 🟢 | ⛔未修复 | 权限路由无鉴权 |
| DFT-051 | 🟢 | ⛔未修复 | AIError 回显原始异常 |
| DFT-052 | 🟢 | ⛔未修复 | `estimate_tokens` 全量 json.dumps |
| **DFT-053** | **数据库** | 🟠 High | **SQLite 锁竞争与写入并发性**：随着用户和 Bot 并发增加，频繁的消息写入和状态更新会导致 `database is locked` 异常。 | 实时对话中断，Bot 响应丢失，用户体验因数据库阻塞而显著下降。 | **已优化（DFT-029）**：开启 WAL 模式。后续应引入**异步写入队列**。 |
| **DFT-054** | **文件 I/O** | 🟠 High | **阻塞性磁盘 I/O**：工作区大文件读写目前主要使用同步操作，阻塞事件循环。 | 导致所有用户的 WebSocket 连接出现可感知的卡顿。 | 使用 `aiofiles` 或 `asyncio.to_thread` 隔离文件操作。 |
| **DFT-055** | **状态管理** | 🟡 Medium | **缺乏长时任务断点（Checkpoint）**：Agent 的 Tool Loop 迭代状态仅存在于内存，重启无法恢复。 | 服务重启后，正在进行的复杂任务无法恢复，用户只能看到任务消失。 | 在数据库中增加 `step_checkpoint` 记录。 |
| **DFT-056** | **安全性** | 🟡 Medium | **API Key 明文存储**：API 密钥明文保存在配置文件中，存在泄露风险。 | 不符合安全最佳实践，容器化部署不便。 | 增加密钥混淆，优先从环境变量加载并支持热加载。 |
| **DFT-057** | **可观测性** | 🟢 Low | **单机运行指标缺失**：无法直观监控活跃任务数和内存队列堆积情况。 | 运维人员无法及时发现潜在的性能瓶颈。 | 增加 `/api/system/status` 接口暴露运行指标。 |

### 缺陷关联链与建议修复顺序

- **安全 RCE 链**：DFT-021（路径穿越，✅ 已断第一环）→ DFT-022（`!` 块执行，✅ 已全禁，断第二环）→ DFT-023（run_shell 沙箱档1+档2 + `*_local_file` 收口，✅）→ DFT-024（权限 fail-open + react_v1 零检查，✅）。**整链已闭环**：`run_shell/write_file/*_local_file/spawn_agent` 在无 ruleset 时一律 fail-closed，react_v1 与 tool_loop_v1 权限对齐，local_file 走需审批工具 + 强化凭据黑名单兜底。
- **会话恢复链**：DFT-017（签名错误，✅ 已止崩）→ DFT-018（resume 入口，✅ 根因已修）→ DFT-019（`recovering` 泄漏，✅ 随 018 用同一 session_id 续写自然解决）。017 只是让它不崩，018 让恢复真正可用，019 随 018 收口。整链 ✅ 已闭环。
- **并发 / 后台任务链**：DFT-025（task 生命周期）+ DFT-026（无锁共享状态）+ DFT-027（abort 失效）✅ 已一并治理。核心是新增 `core/bg.py` 后台任务登记处（只依赖标准库，无项目 import 避免环）：`spawn()` 持有强引用防 GC + `add_done_callback` 落异常日志（DFT-025）；`spawn_group(group_id, coro)` 再按群登记，`abort_group(group_id)` 一次取消整条工作流链（DFT-027，旧实现只 cancel 最初 dispatch、且 done_callback 会误删后到任务的登记）。**复核发现 DFT-026 大部分已被编排重构消解**——`PoolStage.observe`/`_advance`/`enter` 等全是同步纯函数，单线程 asyncio 下 RMW 原子，不会串票/跳阶段；真正残留只有 `tool_executor` 全局钩子表在并发 `execute()` 下边 `await` 边 `list.remove()`（`ValueError: x not in list`），已改快照迭代 + `once` 钩子 claim-before-fire（移除即占用，保证恰好一次且不崩）。**附带修复测试隔离泄漏**：`test_tool_executor_hooks.py` 在 `setup_method` 向全局 `tool_executor._defs` 注册了名为 `run_shell` 的 ToolDef（与 workspace 真实工具同名）却从不清理，泄漏到后续 `test_abort_signal.py`——后者用 `get_schemas()` 构造 `tool_schemas` 时被这条残留撑成非空，使 `tool_loop_v1` 偏离纯流式分支（走未被 patch 的 `call_ai_once`）→ abort 测试拿到 `stream_error` 而非 `stream_aborted`。已加 module 级 autouse teardown fixture，在每个用例后 `clear_before_hooks/clear_after_hooks` + 清空 `_defs/_handlers`。
- **持久化链**：DFT-028（外键）+ DFT-029（WAL / busy_timeout）✅ 已合并进同一个 `db.connect()` helper——连接建立即套用 `foreign_keys=ON`+`journal_mode=WAL`+`busy_timeout=5000`；sessions/scheduler/permissions store 及 schema init 全部改走该 helper。FK 启用后顺带暴露并修正了若干测试夹具的悬空引用（需先 seed 父行）。
- **上下文配对**：DFT-035 与已修的 DFT-003 同源不同代码路径，应抽 `_safe_truncate_boundary` helper 复用。

### 参考项目对标结论（安全组，来自 gsd-2 / opencode 源码比对）

- **DFT-021**：opencode（发现后按 name 查表）/ gsd-2（`readdir` + `entry.name===expanded` 精确匹配）都不把模型给的 name 拼进路径。本项目已采用「发现式查找 + 纵深防御」对齐。
- **DFT-022**：opencode 根本不在 skill 正文内执行 shell（markdown 仅作指令）→ 印证「全禁 `!` 块」是更稳的主流做法。
- **DFT-023**：两者都把执行收进权限规则集 / 独立 sandbox（gsd-2 `exec-sandbox.ts` 的 `env_allowlist`）→ run_shell 应走 ruleset 审批 + env 白名单，子串黑名单只能当辅助。

---

以下是本项目当前版本的完整缺陷清单（按严重程度由高到低排序）：

| 缺陷 ID | 模块 / 文件 | 严重程度 | 问题描述 (Defect Description) | 影响表现 (Impact) | 修复建议 (Remediation) |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **DFT-001** | **前端聊天输入**<br>[MessageInput.jsx](frontend/src/components/MessageInput.jsx#L30-L41) | ✅ Fixed | 切换群组时 `useEffect` 依赖项数组为空导致清理函数在 prop 变更时不执行；且未监听 `defaultValue` 重设 `text` 状态。 | 切换群组后，旧草稿无法保存，且输入框内容依然维持原样，草稿自动恢复机制完全失效。 | **已修复**：监听 `groupId` 变化，触发保存并将 `text` 同步重置为新群组的 `defaultValue`。 |
| **DFT-002** | **WebSocket 客户端**<br>[useWebSocket.js](frontend/src/hooks/useWebSocket.js#L43-L50) | ✅ Fixed | Effect 清理函数调用 `ws.close()` 关闭旧连接时，会再次触发 WebSocket 的 `onclose` 回调，进而重新触发 `setTimeout` 重连。 | 用户快速切换群组或频繁断网后，后台会遗留并泄漏大量并行的自动重连 WebSocket 线程，造成连接风暴和状态不同步。 | **已修复**：在 Effect 清理函数执行 `close()` 前，务必先将 `onclose` 回调清空（`ws.current.onclose = null`）。 |
| **DFT-003** | **工具执行器**<br>[tool_loop_v1.py](backend/executors/plugins/tool_loop_v1.py#L141-L153) | ✅ Fixed | 上下文压缩截断时，未保护“Tool 调用配对结构”，直接把前导的 `assistant` message 压缩删除，保留孤立的 `tool` message。 | 再次发送请求时引发 OpenAI API 验证异常（`400 Bad Request`），导致 Bot 聊天序列永久损坏。 | **已修复**：压缩回溯判断，确保匹配的 `assistant` 消息与 `tool` 结果消息在同一侧，避免被割裂。 |
| **DFT-004** | **大模型客户端**<br>[ai_client.py](backend/ai_client.py#L131-L180) | ✅ Fixed | 转换 Claude 格式时，将连续的多个工具 `tool` 响应分别映射为独立的 `user` 角色消息，导致非交替消息排布。 | 违反 Anthropic 严格交替角色要求，Claude 接口直接抛出 `400 Invalid Message Sequence` 报错。 | **已修复**：重写了 `_to_claude_messages`，将连续多个 `tool_result` 合并入同一个 `user` 消息中，合并连续的同角色消息（如 user-user, assistant-assistant），并在 `call_ai_stream` 统一转换为 Claude 消息格式。 |
| **DFT-005** | **成员创建接口**<br>[MemberList.jsx](frontend/src/components/MemberList.jsx#L192) | ✅ Fixed | 创建 Bot 成员时，API 传参与数据库 Insert 语句均漏掉性格、模型参数、执行器设置等大批 Form 表单字段。 | 用户创建 Bot 时的自定义高级设置（性格、温度、`tool_loop` 选项）静默丢失，被降级为纯文本默认值。 | **已修复**：前端 `ChatWindow.jsx` 和 `api.js` 已改为支持传递完整表单对象；后端 `models.py` 补齐属性；`group_routes.py` 的 `add_member` 接口在 SQL INSERT 时保存所有属性。且构建了单元测试验证。 |
| **DFT-006** | **工作流引擎**<br>[workflow.py](backend/workflow.py#L233) | ✅ Fixed | 工作流实时广播 chunk 时使用了 `"chunk": chunk` 字段，而前端接收期待的是 `data.delta`。 | 工作流运行中，Bot 流式打字输出内容在前端完全呈现为字面量 `undefined`。 | **已修复**：修改了 `backend/workflow.py` 中的两处 WebSocket 广播逻辑，将 `"chunk": chunk` 变更为 `"delta": chunk`；新建了单元测试以验证广播负载格式。 |
| **DFT-007** | **数据库/摘要**<br>[database.py](backend/database.py#L43-L52) | ✅ Fixed | `role_summaries` 表中定义了 `group_id INTEGER NOT NULL` 字段，但 `maybe_summarize` 写入时未传此字段。 | SQLite 报错 `NOT NULL constraint failed` 被沉默吞掉，导致**Bot 历史摘要功能彻底失效**。 | **已修复**：修改 `maybe_summarize` 的签名与各调用方，在 `INSERT` 写入时显式传入 `group_id`。 |
| **DFT-008** | **大模型记忆组件**<br>[memory.py](backend/memory.py#L65-L71) | ✅ Fixed | `maybe_summarize` 执行时在 SQL 端未通过 `id > last_id` 进行约束，而是直接抓取 Bot 历史的所有发言再在 Python 端进行过滤。 | 随着 Bot 对话轮数增多，每次大模型响应均会全量拉取全表所有行进内存，造成极高的 SQLite I/O 损耗与内存 spike。 | **已修复**：重写 SQL 查询语句，在 WHERE 条件中引入 `AND id > ?` 直接进行过滤，避免抓取无关历史发言。 |
| **DFT-009** | **连接管理器**<br>[ws_manager.py](backend/ws_manager.py#L30-L50) | ✅ Fixed | 在 `broadcast` 时对 dead 连接清理（disconnect）后，未向群组广播 Presence 状态信息。 | 用户异常掉线后其他用户的侧边栏绿点状态无法实时同步，造成假在线显示。 | **已修复**：在 `broadcast` 检测并断开 dead 连接后，增加向群组广播其 Presence 下线消息。并设计了单元测试进行验证。 |
| **DFT-010** | **聊天历史渲染**<br>[MessageBubble.jsx](frontend/src/components/MessageBubble.jsx#L199) | ✅ Fixed | SQLite 使用 `CURRENT_TIMESTAMP` 存入无时区字尾字符串，前端 `new Date()` 默认当本地时区时间解析。 | 造成客户端的时间转换偏差，聊天消息显示时间比实际发送的中国时间（GMT+8）滞后 8 小时。 | **已修复**：后端在 `_row_to_msg`（获取消息/广播）和 `search_group_messages`（搜索）中，自动将无时区字尾的 `created_at` 字段格式化为 ISO 8601 UTC 格式（包含 `T` 和 `Z` 尾缀），使前端能正确解析。 |
| **DFT-011** | **角色路由上下文**<br>[tool_loop_v1.py](backend/executors/plugins/tool_loop_v1.py#L324) | ✅ Fixed | 构建消息上下文时，已包含最新已入库的用户消息，但随后再次强行在尾部 append 该 `user_message`。 | 导致最新消息在大模型上下文尾部被重复发送了两次，增加 Token 消耗且可能干扰推理。 | **已修复**：修正了 `build_context_message` 中的 `recent_messages` 截取，如果最后一条已存在于 `recent_messages` 则自动切片滤除，防止重复拼入。新增单元测试测试该行为。 |
| **DFT-012** | **前端搜索面板**<br>[SearchPanel.jsx](frontend/src/components/SearchPanel.jsx#L31-L41) | ✅ Fixed | 切换群组时未清理或防备之前的异步搜索 Promise，造成竞态条件（Race Condition）。 | 慢速网络请求返回后，之前群组的搜索结果覆盖当前群组的空白结果或新结果，内容混乱。 | **已修复**：在 `SearchPanel.jsx` 内部的搜索 `useEffect` 引入了 `active` 局部信号量以忽略被覆盖的过期请求；并在群组切换时重置 search state 和 query。 |
| **DFT-013** | **前端群组侧栏**<br>[GroupList.jsx](frontend/src/components/GroupList.jsx#L165-L168) | ✅ Fixed | 自动回复设置的回调中直接通过 `autoReplyTarget.auto_reply = text` 变异了 React 的 State 引用属性。 | 这种 Shallow Update 机制不会触发 React 的 UI 渲染，导致图标 `↩` 在前端列表无法实时更新显示。 | **已修复**：将 `autoReply` 变更抛出给父组件 `ChatWindow.jsx` 提供的 `onAutoReplySaved` 回调，并通过 immutable 状态更新更新 state `members` 和 `membersCache`。 |
| **DFT-014** | **表情选择组件**<br>[EmojiPicker.jsx](frontend/src/components/EmojiPicker.jsx#L35) | ✅ Fixed | `mousedown` 关闭处理与 `😊` 按钮自身的状态取反触发链冲突。 | 点击 `😊` 按钮试图收起表情 picker 时无法收起（会先关闭然后瞬间又被触发打开）。 | **已修复**：在 `MessageInput.jsx` 和 `MessageBubble.jsx` 中的表情触发按钮上增加了 `onMouseDown={(e) => e.stopPropagation()}`，防止 mousedown 事件传播到 document 导致的关闭与重新打开冲突。 |
| **DFT-015** | **并发广播**<br>[ws_manager.py](backend/ws_manager.py#L30-L50) | ✅ Fixed | 遍历连接列表并在循环中 `await ws.send_json`。 | 存在协程并发修改原列表的安全隐患，可能引发 `RuntimeError: list size changed during iteration`。 | **已修复**：改用 `list(self.connections[group_id])` 对连接列表进行浅拷贝遍历，从根本上防止迭代期间发生列表长度改变。并设计了单元测试进行验证。 |
| **DFT-016** | **工作流解析**<br>[workflow.py](backend/workflow.py#L74) | ✅ Fixed | 正则 `\d+\.` 强行匹配开发任务列表。 | 无法匹配大模型用 Markdown 的 `-` 或 `*` 列表格式输出，导致合并成一个“本次迭代任务”看板。 | **已修复**：重写了 `_parse_tickets` 中的正则逻辑以兼容 `\d+\.`、`-`、`*`、`+` 等各种 Markdown 列表前缀，并增加了完整的单元测试验证。 |

---

## 架构师深度 Review 新增缺陷（2026-05-30，状态：已修 13/36 — DFT-017 / 018 / 019 / 020 / 021 / 022 / 023 / 024 / 025 / 026 / 027 / 028 / 029 已修）

来源：对 backend 全量 ~8900 行源码的子系统级 review（执行引擎 / 编排·总线 / 持久化·会话·调度 / 技能·权限·AI 客户端）。严重度：🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low。

| 缺陷 ID | 模块 / 文件 | 严重度 | 问题描述 | 影响表现 | 解决方案 |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **DFT-017** | **会话恢复**<br>[recovery.py](backend/sessions/recovery.py#L166) | 🔴 ✅已修 | `_dispatch_recovery` 用 `bots=/user_message=/history=` 调 `dispatch_bots`，但真实签名是 `(group_id, triggered, content, sender, recent, all_bots, all_members)`，参数名不存在且缺必需位置参数。 | 每次恢复都抛 `TypeError`，被 fire-and-forget task 静默吞掉 → **恢复功能生产中完全跑不起来**。单测用 `dispatch=list.append` 桩从未覆盖真实路径。 | 先修签名为正确位置参数：`dispatch_bots(group_id, [bot], content, system_sender, recent, all_bots, members)`，`all_bots` 从 members 过滤 `type=='bot'`。根因见 DFT-018：恢复应走专用入口而非复用 `dispatch_bots`。 |
| **DFT-018** | **会话恢复**<br>[recovery.py](backend/sessions/recovery.py#L134) · [tool_loop_v1.py](backend/executors/plugins/tool_loop_v1.py#L311) · [base.py](backend/executors/base.py#L73) | 🔴 ✅已修 | `tool_loop_v1` 无参数接收重建的 `messages`/已有 `session_id`，每次无条件 `uuid4()` 新建并从群历史重建，重建出的 WAL 消息被丢弃。 | "恢复"=把原任务从头重跑，**重复执行所有已完成的副作用工具**，`needs_review`/幂等判断在下游被废。 | **已修**：① `ExecutionContext` 增 `resume_session_id`+`resume_messages`；② `tool_loop_v1.run` 检测到 resume 时跳过新建 session、跳过历史重建，剥离前导 system 后直接续跑重建的 WAL messages（含已完成 tool_result）；③ 恢复改走专用入口 `_dispatch_recovery`（不再经 `dispatch_bots` 的历史重建），把重建消息经 `ExecutionContext.resume_*` 交给 executor。单测 `test_recovery_resume.py` 验证已完成工具不重跑。 |
| **DFT-019** | **会话恢复**<br>[recovery.py](backend/sessions/recovery.py#L175) · [tool_loop_v1.py](backend/executors/plugins/tool_loop_v1.py#L311) | 🔴 ✅已修 | `_recover_one` 把孤儿置 `recovering` 后再没有任何地方迁出；`get_orphaned_sessions` 只查 `running`。 | `recovering` 行永久泄漏，token/cost 悬空，重启也不再处理。 | **已修**：随 DFT-018 用同一 `session_id` 续写——`_dispatch_recovery` 传入 `resume_session_id=sid`，`tool_loop_v1` resume 分支复用该 session，正常 completed/failed 回写自然把 `recovering` 迁出（无新行泄漏）；executor 异常时 `_dispatch_recovery` 兜底回写 `failed`。单测验证恢复后仅 1 行 session 且状态 `completed`。 |
| **DFT-020** | **执行引擎**<br>[tool_loop_v1.py](backend/executors/plugins/tool_loop_v1.py#L496) | 🔴 ✅已修 | `[s for s in tool_schemas if s.get("name") in _skill_allowed]` —— schema 的 name 在 `s["function"]["name"]`，`s.get("name")` 永远 `None` → `[] or tool_schemas` 退回全量。 | Skill 的 `allowed-tools` 工具白名单形同虚设（fail-open）；fork 白名单（:659）反而拿到空 schema。 | 两处（:496、:659）改为 `s["function"]["name"]`；补单测验证 allowed-tools 真正收窄。 |
| **DFT-021** | **技能系统**<br>[metadata.py](backend/skills/metadata.py#L4) · [loader.py](backend/skills/loader.py#L47) | 🔴 ✅已修 | `run_skill(name)` 的 `name` 直接来自模型，`skill_path` 无 `is_relative_to` 校验。 | `name="../../../../etc/hosts"` 可读取进程权限内任意文件返回模型 → 沙箱逃逸 / 任意文件读取。 | `skill_path` 内 `(skills_dir/name).resolve()` 后校验 `is_relative_to(skills_dir.resolve())`，越界拒绝；`name` 加白名单正则 `^[\w-]+$`。 |
| **DFT-022** | **技能系统**<br>[processor.py](backend/skills/processor.py) | 🔴 ✅已修 | skill 正文中的 ```!cmd``` / ``!`inline` `` 块直接走 `/bin/sh -c` 执行，绕过 `run_shell` guard 与权限钩子。 | 配合 DFT-021：bot 可 `write_file` 含 ```!rm -rf ~``` 的草稿 skill 再 `run_skill` → 自写 skill = 直接 RCE。 | **已修（选项 A：全禁）**：删除 `execute_shell_in_prompt` / `_run_shell_cmd` / `!` 块正则与 shell 常量，`process_skill_content` 只保留参数替换 + `${SKILL_DIR}`；`!` 标记作为惰性文本原样透传，**不再起任何子进程**。skill 需要 shell 时由 AI 主动调 `run_shell`（经 `tool_executor` → denylist + 权限管线 + cwd/env 沙箱）；`.py` 伴随脚本本就被 `run_skill` 推回 `run_shell`。对标 opencode（skill 正文从不执行 shell）。单测 `tests/test_skill_no_shell_exec.py`（5 例）验证 block/inline 命令不执行、参数替换/`${SKILL_DIR}` 仍生效、helper 已移除。 |
| **DFT-023** | **执行引擎**<br>[workspace_tools.py](backend/executors/plugins/workspace_tools.py#L438) | 🔴 ✅已修 | `run_shell` 用 `cwd=$HOME`+全量 `os.environ` 执行任意命令，唯一防线是子串黑名单（`rm -rf  /` 双空格/`find / -delete`/`$(echo rm)` 均可绕）；`read_local_file/write_local_file` 给整盘访问，`_is_sensitive_path` 是不全的前缀黑名单。 | 主机级 RCE + 凭据外泄（`~/.git-credentials`/`~/.npmrc`/cookie 库不在黑名单内）。 | **已修（档1+档2+local_file 收口）**：① `_resolve_shell_cwd` 把 `cwd` 限制在 `bot_{id}` 工作区内（默认即工作区根，绝对路径/`..` 越界拒绝）；② `_sandbox_env` 改为白名单（仅 PATH/HOME/LANG/LC_*/TERM 等），剥离所有 `*_KEY/*_TOKEN/*_SECRET/AWS_*` 等密钥；③ `_default_shell_guard` 档2 兜底：`ruleset is None` 时 run_shell **fail-closed**；④ `read_local_file/write_local_file` 纳入 `_APPROVAL_REQUIRED_TOOLS`（无 ruleset fail-closed，见 DFT-024），并强化 `_is_sensitive_path` 黑名单（`.git-credentials`/`.npmrc`/`.pypirc`/`.dockercfg`/`*.keystore`/`*.jks`/`cookies.sqlite` + `~/.docker`/`~/.config/gh`/`~/.password-store` 等前缀）作纵深兜底。单测 `test_p1_safety.py::TestSensitivePathExtended`（8 例）。 |
| **DFT-024** | **权限引擎**<br>[workspace_tools.py](backend/executors/plugins/workspace_tools.py#L379) · [react_v1.py](backend/executors/plugins/react_v1.py#L241) | 🟠 ✅已修 | `_permission_check_hook` 在 `ruleset is None` 时放行；`react_v1` 的 `execution_ctx` 根本不带 `ruleset`。 | 默认 fail-open；ReAct bot 跑 `run_shell/write_file/spawn_agent` **零权限检查**——同一 bot 换 executor 即可绕权限。 | **已修**：① `_permission_check_hook` 改 fail-closed——`ruleset is None` 时对 `_APPROVAL_REQUIRED_TOOLS`（run_shell/write_file/read_local_file/write_local_file/spawn_agent）默认拒绝，只读工具（read_file/list_workspace）仍放行；② `react_v1` 在无 ctx.ruleset 时按 bot `permission_mode` 自建 `Ruleset`，`execution_ctx` 补齐 `ruleset/steer_channel/rewake_queue` 并在循环顶部 drain rewake，与 `tool_loop_v1` 对齐。单测 `test_p1_safety.py::TestPermissionHookFailClosed`（7 例）+ `::TestReactV1RulesetWiring`。 |
| **DFT-025** | **编排/工作流**<br>[orchestrator.py](backend/core/orchestrator.py#L157) · [workflow.py](backend/core/workflow.py#L296) | 🟠 | 几十处 `asyncio.create_task(...)` 无引用持有、无 done_callback、无异常处理（含整个 workflow 推进链）。 | 任务可能被 GC 提前杀掉；异常只在 GC 时 warning；一个推进任务死掉 → 工作流静默卡死，用户无感知。 | 建全局 `_bg_tasks: set`，`create_task` 后 `add()` + `add_done_callback`（discard + 记录异常）；workflow 推进任务注册进可 abort 的集合（见 DFT-027）。 |
| **DFT-026** | **编排/工作流/执行器**<br>[workflow.py](backend/core/workflow.py#L97) · [orchestrator.py](backend/core/orchestrator.py#L18) · [tool_executor.py](backend/executors/tool_executor.py#L9) | 🟠 | 共享可变状态无锁：`check_and_advance` 对同一 stage dict 做 RMW（并发抢同一 ticket / 重复 advance 跳阶段）；`active_bot[group_id]` 多路竞写；全局钩子表 `once` 钩子边迭代边 `remove`。 | 工作流串票/跳阶段；会话锁不原子，消息路由到错 bot；并发下 `ValueError: x not in list`/串钩子。 | 每群一把 `asyncio.Lock` 包住 `check_and_advance` 的 RMW 与 `active_bot` 写；钩子表改执行时快照（copy-on-iterate）或加锁，`once` 钩子线程安全移除。 |
| **DFT-027** | **编排/工作流**<br>[main.py](backend/main.py#L135) · [workflow.py](backend/core/workflow.py#L188) | 🟠 | abort 只 cancel `_running_tasks[group_id]`（最初的 dispatch）；workflow 推进跑在 `advance()` 派生的游离任务上，未注册。 | 用户 abort 后整条工作流链继续跑、继续流式输出 → **abort 对工作流基本失效**。 | `_running_tasks` 改为 `dict[int, set[Task]]`，`advance/_trigger_*` 派生的任务全部注册进对应群，abort 时 cancel 整组。 |
| **DFT-028** | **持久化**<br>[db/__init__.py](backend/db/__init__.py#L7) | 🟠 ✅已修 | 全代码无任何 `PRAGMA foreign_keys=ON`（SQLite 默认 OFF）。 | 所有 FK（`session_events.session_id`/`cron_jobs.bot_id` 等）不生效，可插入悬空引用、删 bot 留孤儿。 | **已修**：`db/__init__.py` 新增 `@asynccontextmanager connect(path=None)`，连接建立后立即 `PRAGMA foreign_keys=ON`（同时 WAL+busy_timeout，见 DFT-029）；`get_db()` 委托给它；`sessions/store.py`（7 处）/`scheduler/store.py`（6 处）/`permissions/db.py`（3 处，保留自身 `_DB_PATH`）/`db/schema.py` init 全部改走 `_db.connect()`。FK 启用后顺带修正 `test_sessions.py`/`test_recovery_resume.py` 夹具——先 seed `groups`/`members` 父行再插 session。单测 `tests/test_db_pragmas.py::test_foreign_keys_enforced_on_insert`。 |
| **DFT-029** | **持久化**<br>[sessions/store.py](backend/sessions/store.py) · [scheduler/store.py](backend/scheduler/store.py) | 🟠 ✅已修 | 连接-per-调用，无 WAL、无 `busy_timeout`，默认 rollback journal 写互斥阻塞读。 | 多 bot 并发写 `session_events/add_tokens/save_message` 时超 5s 默认超时 → `OperationalError: database is locked` → 会话被标 `failed` 丢弃。 | **已修（与 DFT-028 同一 helper）**：`db.connect()` 连接即 `PRAGMA journal_mode=WAL`（写不再阻塞读）+ `PRAGMA busy_timeout=5000`（并发写等待而非立即报错）；所有 store 共用该 helper。单测 `tests/test_db_pragmas.py`（5 例）验证 WAL/busy_timeout/foreign_keys 均生效。 |
| **DFT-030** | **事件总线**<br>[adapter.py](backend/bus/adapter.py#L24) · [engine.py](backend/bus/engine.py#L80) | 🟠 | 所有 group 所有事件汇入一个 `subscribe_all()` 队列、单任务串行消费，`send_json` 无写超时；队列无 `maxsize`。 | 一个半开连接的慢客户端卡住整个 app 事件投递（全局队头阻塞）；消费者落后则内存无界增长。 | 每个 WS 连接独立 subscribe + 独立消费任务；`send_json` 加超时，慢客户端单独断开；队列设 `maxsize`，满则丢弃/断开。 |
| **DFT-031** | **权限引擎**<br>[engine.py](backend/permissions/engine.py#L81) | 🟠 | `await future` 无 timeout；`resolve()` 仅按 `request_id` 匹配无鉴权。 | 用户关页面 → 协程永久挂起占着 tool loop，`_pending` 无界增长；任何拿到 `request_id` 的客户端可批准他人 bot 的工具调用。 | `asyncio.wait_for(future, timeout)` 超时默认拒绝；WS 断开时 cancel 该连接所有 pending；`resolve` 校验 request 属于该 group/调用者。 |
| **DFT-032** | **权限引擎**<br>[engine.py](backend/permissions/engine.py#L21) | 🟠 | `_once_rules[bot_id]` 进程级全局，"once" 实际是"重启前、所有群、永久放行"。 | 用户"仅此一次"的意图被放大为跨群永久授权。 | key 改为 `(bot_id, group_id, tool_name, args_hash)`，并设单次消费（用后即删）或 TTL。 |
| **DFT-033** | **AI 客户端**<br>[ai/client.py](backend/ai/client.py) | 🟠 | 每次 AI 调用新建 `httpx.AsyncClient`，无连接池；流式生成器在消费方提前中断时 response/socket 到 GC 才释放。 | 每次请求/重试都做 TLS 握手增加延迟；高负载下泄漏文件描述符。 | app 生命周期内共享单个 `AsyncClient`（lifespan 创建、依赖注入）；流式路径 try/finally 显式 `aclose()` 或确保生成器关闭。 |
| **DFT-034** | **执行引擎**<br>[tool_loop_v1.py](backend/executors/plugins/tool_loop_v1.py#L582) | 🟡 | `tool_result` 事件 `is_error` 串/并行路径都硬编码 `False`，即便工具返回 `[执行错误]`/`[安全拒绝]`。 | WAL/审计无法区分成功与失败/被拦截；恢复时把错误当成功结果回放。 | `tool_executor.execute` 返回 `(result, is_error)` 或抛特定异常；loop 据此写 WAL `is_error`。 |
| **DFT-035** | **上下文压缩**<br>[tool_loop_v1.py](backend/executors/plugins/tool_loop_v1.py#L508) · [compact.py](backend/executors/compact.py#L276) | 🟡 | 溢出恢复与 `snip_if_needed` 仍会拆散 `assistant(tool_calls)↔tool` 配对（DFT-003 同类问题、不同代码路径）。 | 产生孤儿 `tool_use_id` → Claude 400，`_overflow_recovered` 已 True 则直接 `AIError` 杀掉整个 run。 | 把 DFT-003 的配对保护抽成 `_safe_truncate_boundary` helper，复用到 overflow recovery 与 `snip_if_needed`；删 `assistant(tool_calls)` 必须连带其 tool 结果。 |
| **DFT-036** | **执行引擎**<br>[tool_loop_v1.py](backend/executors/plugins/tool_loop_v1.py#L222) | 🟡 | `run` 是 ~600 行 god method，混 8+ 职责、深层嵌套闭包改 `nonlocal` token 计数。 | 无法单元测试（仅 `_tool_loop_core` 可测且与真实循环漂移），维护风险高。 | 抽出 run scaffold（prompt 组装/skill 快照/session/compaction/dispatch/persist）到基类或 helper，`run` 只做编排。 |
| **DFT-037** | **执行引擎**<br>[react_v1.py](backend/executors/plugins/react_v1.py) vs [tool_loop_v1.py](backend/executors/plugins/tool_loop_v1.py) | 🟡 | 三个 executor 复制 ~150 行生命周期且已漂移（react 缺 WAL/archive/before_finalize、doom-loop 策略不同、skill 快照构建不同）。 | 任一修复需手工镜像到其它 executor，易遗漏。 | 同 DFT-036，三 executor 共享生命周期脚手架，差异点用 hook/manifest 表达。 |
| **DFT-038** | **DB 迁移**<br>[migrations.py](backend/db/migrations.py#L46) | 🟡 | `except Exception: pass` 既吞"duplicate column"也吞 `database is locked`/磁盘满/语法错误，且仍记为已应用。 | schema 残缺而 `_schema_version` 谎报成功，永不重试。 | 只捕获 `sqlite3.OperationalError` 且 message 含 "duplicate column"；其它异常上抛，失败不记版本。 |
| **DFT-039** | **调度器**<br>[scheduler/engine.py](backend/scheduler/engine.py#L69) | 🟡 | `misfire_grace_time=60`+`coalesce=True`，重启超 1 分钟即静默丢失定时任务，无 last_run/next_run 审计。 | 部署/重启 >1min 静默跳过定时任务，无记录。 | 持久化 last_run/next_run；启动时对错过的 job 做 catch-up 或至少告警；按需调大 misfire_grace_time。 |
| **DFT-040** | **调度器**<br>[scheduler/engine.py](backend/scheduler/engine.py#L45) | 🟡 | `AsyncIOScheduler()` 无 timezone，用宿主本地时区解释 cron，但 `created_at` 存 UTC，混用。 | DST/服务器迁移会整体平移所有任务触发时间。 | `AsyncIOScheduler(timezone="UTC")`，cron 统一 UTC 解释，前端展示再转本地。 |
| **DFT-041** | **工作流**<br>[workflow.py](backend/core/workflow.py#L75) | 🟡 | `_parse_tickets` 解析失败时伪造单个 `["本次迭代任务"]`。 | 工作流"成功"却几乎啥都没干，无失败信号（开发团队其余 bot 全 idle）。 | 解析失败不伪造，发系统消息提示上游重发或标 stage 失败，不静默推进。 |
| **DFT-042** | **工作流**<br>[workflow.py](backend/core/workflow.py#L267) | 🟡 | `_trigger_pool_stage` 无幂等守卫，重入会 `random.shuffle`+清空 `in_progress/ticket_queue/completed`。 | 重复 advance/重试时抹掉进行中的工作与已完成票据历史。 | 开头加 `if stage.get("in_progress"): return` 幂等守卫。 |
| **DFT-043** | **AI 记忆**<br>[ai/memory.py](backend/ai/memory.py#L99) | 🟡 | `maybe_summarize`/`get_memory_context` 整体 `try/except: pass`。 | 记忆压缩静默失效无任何日志，生产几乎无法排障。 | except 改为 `log.exception`，记录但不阻断主流程。 |
| **DFT-044** | **权限引擎**<br>[engine.py](backend/permissions/engine.py#L28) | 🟡 | `_matches` 只对顶层 `arguments.values()` 的 `str(v)` 做 fnmatch；dict/list 被转成 Python repr。 | 针对命令原文写的 deny 规则对嵌套参数静默失配 → 可被参数结构绕过。 | 对 dict/list 递归展开或匹配其 JSON 序列化串；`run_shell` 专门匹配 `command` 字段原文。 |
| **DFT-045** | **技能系统**<br>[metadata.py](backend/skills/metadata.py#L18) | 🟡 | 手写非 YAML 解析器（按首个 `:` 切分），不支持 block scalar/含冒号值/flow list。 | 标准 YAML 的 frontmatter 字段被静默丢弃或截断，`allowed-tools` 解析可能误判。 | 改用 `yaml.safe_load`（无 RCE 风险），删手写解析器。 |
| **DFT-046** | **技能系统**<br>[discovery.py](backend/skills/discovery.py) | 🟡 | 每条消息构建 skill 列表时在事件循环上同步 `iterdir/exists/read_text`（五层）。 | skill 多/文件大时阻塞整个事件循环。 | 文件 IO 用 `asyncio.to_thread` 包裹；或 watcher 维护内存缓存，请求路径只读缓存。 |
| **DFT-047** | **连接管理**<br>[ws_manager.py](backend/ws_manager.py#L30) | 🟡 | `broadcast` 内调 `disconnect`（改 dict）并递归调 `broadcast` 做 presence，无锁。 | 可能深递归与重复变更；并发 connect/broadcast 丢失刚加入的连接。 | presence 改非递归（先收集 dead，断开后单次广播）；`connections` 加 `asyncio.Lock`。 |
| **DFT-048** | **编排（竞速）**<br>[orchestrator.py](backend/core/orchestrator.py#L303) | 🟡 | 竞速路径只记 winner 的 token，被 cancel 的 loser 已消耗的 provider token 永不计入。 | 成本按竞速并发数低估。 | 取消 loser 前累加其已用 token；或在 client 层按请求记账。 |
| **DFT-049** | **插件注册**<br>[registry.py](backend/executors/registry.py#L16) | 🟢 | 插件 import 错误被静默吞只留日志；全失败时 `next(iter(_registry.values()))` 抛 `StopIteration`。 | 整个 bot 系统可零 executor 启动，运行时才暴雷。 | `_load_file` 记 error 并经 `/api/plugins` 暴露健康状态；`get()` 空时抛明确错误或返回 `None` 由调用方处理。 |
| **DFT-050** | **权限路由**<br>[permissions/routes.py](backend/permissions/routes.py#L16) | 🟢 | `POST /members/{id}/permissions` 无鉴权/属主校验。 | 若路由未在 auth 中间件后，任何调用方可给任意 bot 加 `allow *` → 权限引擎被绕过。 | 整体引入 auth 后加鉴权/属主校验；至少校验调用者非 bot。 |
| **DFT-051** | **AI 客户端**<br>[ai/client.py](backend/ai/client.py#L92) | 🟢 | `AIError(f"...{str(e)}")` 把原始异常（可能含 URL/header）回显进聊天/日志。 | 潜在敏感信息泄漏到用户侧。 | 用户侧只给通用文案，详细 `str(e)` 仅入日志。 |
| **DFT-052** | **上下文压缩**<br>[compact.py](backend/executors/compact.py#L77) | 🟢 | `estimate_tokens` 每次全量 `json.dumps` 整个消息数组，每轮调多次。 | 大历史下重复 O(n) 序列化，性能损耗。 | 缓存上轮估算，仅对增量消息计算；或用长度近似避免全量 `json.dumps`。 |
