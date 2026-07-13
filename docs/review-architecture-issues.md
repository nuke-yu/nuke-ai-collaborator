# Architecture Issues — Code Review 2026-07-13

> **最终对齐版本。以下 AC1 + A1-A6 修正版及“最终架构决策优先级”是唯一有效的讨论基线；后续交叉复核记录仅用于保留判断过程。**

---

## AC1（P0 架构决策）：应用层缺少 user↔group 授权边界

### 现状
项目核心定义是 "Groups fully isolated"（CLAUDE.md），但应用层没有群组授权边界：

- 所有 HTTP 路由（groups、messages、workspace、sessions、permissions）只检查 `get_current_user`（JWT 有效），不校验 caller 属于目标群组
- WebSocket 认证验证 member 存在于群组，但不验证 caller **是**那个 member
- `members.user_id` 列存在但始终为 NULL

### 关键上下文
这是 **DFT-082 记录的有意设计**——曾尝试严格校验并导致 WS 连接全部失败。代码注释标注为 "trusted internal" accepted risk。

### 根本矛盾
**内部可信不等于群组隔离。** 如果产品承诺 "Groups fully isolated"，当前实现违反了这个承诺。DFT-082 是历史取舍，不覆盖当前产品要求。过去直接校验 `members.user_id` 导致 WS 全断，只说明需要正式 membership 模型，不能证明不该授权。

### 最终对齐结论

当前 review 以项目章程中的 **"Groups fully isolated"** 为有效产品要求，因此正式多用户 production 必须实现 user↔group membership/role 模型，并统一 `require_group_member`、`require_group_admin` 和 WS 身份绑定。DFT-082 只能解释历史实现，不能作为关闭 AC1 的依据。

若产品负责人选择 "trusted internal shared workspace"，必须先正式修改产品承诺、威胁模型和部署文档。在此之前，未实现 AC1 的部署只能限定为**单一人类信任域**，不得宣称不同人类用户之间具备群组安全隔离。

---

## ~~A1. Split-DB 架构下跨库事务不可能原子化~~ [SUPERSEDED]

> 原始描述把三类问题混在一起。以下为修正版。

## A1 修正版：跨存储一致性问题（仅限 clear_bot_context / delete_group）

### 现状（修正后）
经过代码验证，原始描述中的四个操作分为三类：

1. **`save_message`**：读 central DB sender snapshot + 写 group DB 单条 INSERT，并且已经把 sender 字段冗余到 message row。**不是跨库双写事务，也不属于 A1 一致性问题**。剩余问题只是每次新建 central connection、异常被吞后 snapshot 可能为 NULL；可由调用方传入已解析 snapshot 或建立 group-local member projection 优化。
2. **`save_compaction_summary`**：INSERT + UPDATE 在同一 DB connection，中间多余 `commit()`。**不是 split-DB 问题**。去掉中间 commit 即可。
3. **`clear_bot_context`**：跨 central DB + group DB + ChromaDB；**`delete_group`**：先提交 central DB，再 best-effort 删除 group DB 文件和 workspace，并且当前没有清理 Chroma facts。**这两项才是真正的跨存储一致性/完整清理问题**。

### 真正矛盾
只有 `clear_bot_context` 和 `delete_group` 涉及跨多个持久化域的部分完成风险，这是 split-DB/外部存储组合下的固有限制。不需要为此推翻 split-DB。

### 讨论方向
- **方向 A**：saga pattern——带 operation ID、状态表、可重试步骤和 reconciliation job
- **方向 B**：接受最终一致性 + 文档化 + 定期 reconciliation 检查

---

## ~~A2. Memory 写路径的成本模型不可持续~~ [SUPERSEDED]

> 原始描述中"最多 4 次 LLM 调用"和"5 bot = 20 次"的固定倍数估算不准确。以下为修正版。

## A2 修正版：Memory 写路径成本需要先测量再决策

### 现状（修正后）
Memory pipeline 的 LLM 调用是条件触发的，不是固定倍数：

- **Fact extraction**：每个 ≥8 字符的 bot 回复几乎固定触发 1 次（最稳定的基线）
- **Conflict resolution**：仅在 facts 命中相似旧事实时触发
- **Summary**：默认累计 15 条 bot 消息后触发
- **Reflection**：需同时满足事实条数和 importance 阈值；可能按多个 thread 并发
- **Tool compression**：默认累计 20 条未压缩事件后触发

实际调用次数取决于 bot 数量、输出长度、facts 命中和各 pipeline backlog。

### 实现缺陷
Memory pipeline 直接调用 `call_ai_once()`，没有经过主 Tool Loop 的 `AIService` usage 累积，当前函数普遍丢弃返回值中的 usage。

### 讨论方向（需要先加指标）
先实现 per-group/bot/pipeline 的完整指标（logical call / provider request / token / cost / latency / failure rate / backlog / memory-foreground cost ratio），加每组预算上限和 backpressure，观察 p50/p95 后再决定架构策略。

---

## ~~A3. Tool Loop 的 Final Response 是冗余的第二次 LLM 调用~~

### **[NOT AN ISSUE — DO NOT FIX]**

~~原描述：Tool loop 结束后做第二次完整 LLM 调用，token 成本翻倍。~~

**代码验证（`tool_loop_v1_helpers.py:409-435`）**：`_stream_final` line 410 检查 `runner.full_text`。正常路径中 model 返回 `type=="text"` 时 line 282 已设置 `full_text`，所以 line 411-419 只做分块广播 + `return`，**零 LLM 调用**。line 422 的 LLM 调用仅在 `full_text` 为空时触发（防御性 fallback），实际不可达。额外 LLM 调用仅出现在 `reviewer_prompt` / before_finalize 特殊路径，应单独量化。

~~### 现状~~
~~Tool loop 结束后，如果 `runner.full_text` 为空（正常路径——loop 最后一次响应是 `tool_calls` 类型），系统会：~~

~~1. 构造包含**全部历史消息**（含所有 tool result）的新请求~~
~~2. 发一次完整 LLM 调用生成用户可见文本~~
~~3. 流式输出~~

~~这意味着：tool loop 消耗 N 次 LLM 调用 + 1 次 final call = N+1 次。Final call 处理全部历史，token 成本约等于 loop 最后一次调用。~~

~~### 根本矛盾~~
~~Tool loop 架构没有把 final response 视为 loop 的有机部分——而是作为 loop 之后的独立阶段。这导致 token 成本翻倍，且 final response 无法利用 loop 末次调用的 KV cache（如果是 API 调用）。~~

~~### 讨论方向~~
~~- **方向 A**：让 loop 的最后一次 LLM 调用同时生成 tool_calls 和用户文本（多数模型支持混合输出）——检测到最后响应包含可见文本时直接流式输出~~
~~- **方向 B**：final call 只传精简上下文（最后一轮 tool result + 系统提示）而非全部历史~~
~~- **方向 C**：保持现状但加 prompt caching——final call 的系统提示和历史部分与 loop 共享 cache~~

---

## A4. IPC 层缺少消息持久化和 at-least-once 语义

### 现状
Supervisor → Worker 的消息传递是纯内存 `asyncio.StreamWriter`：

- `send_to_worker` 直接写 socket
- Worker 的 recv loop 收到消息后直接处理
- 无 message ID、无 ack、无 pending queue

Worker 进程崩溃时：
- in-flight 的 `USER_MESSAGE` 直接丢失
- 用户发了消息但 bot 永远不回复
- 无重试、无通知、无 dead letter

### 根本矛盾
系统对用户的承诺是"发消息 → bot 回复"，但 IPC 层是 fire-and-forget。进程崩溃（OOM、kernel OOM killer、deploy 重启）在生产环境中是常态而非异常。当前架构**无法保证 at-least-once 交付**。

### 讨论方向
- **方向 A**：轻量级 pending queue——Supervisor 为每个 worker 维护 in-flight 消息列表，Worker ack 后移除；Worker 重连时重发 unacked
- **方向 B**：消息持久化到 SQLite——`pending_messages` 表，Worker 处理后 DELETE；崩溃重启后自动恢复
- **方向 C**：接受 at-most-once 但加用户反馈——检测消息丢失后通知用户"消息未送达，请重试"
- **方向 D**：引入外部消息队列（Redis / NATS）——最重但最可靠的方案

---

## A5. 前端三套认证策略共存——无架构约束

### 现状
前端同时存在三种 API 认证方式：

1. **全局 `window.fetch` monkey-patch**（`main.jsx:12-25`）：拦截所有 fetch 调用注入 Authorization header
2. **`authFetch` 封装**（`api.js:3`）：显式包装函数，设置 Authorization header
3. **手动 `Authorization` header**（`ChatWindow.jsx` / `MemberList.jsx` / `WorkspacePanel.jsx` 中数十处）：直接 `fetch(url, { headers: { Authorization: 'Bearer ' + token } })`

### 根本矛盾
认证层没有形成架构约束。新开发者写新组件时不知道该用哪种方式；漏掉认证就是 401；monkey-patch 会影响第三方库（analytics、error tracking）的 `/api/` 路径请求。三种策略的 token 来源也不同（有的从 store 取、有的从 closure 取），token 刷新时的行为不一致。

### 讨论方向
- **方向 A**：统一为 `authFetch`——删除 monkey-patch，audit 所有 raw fetch 调用点
- **方向 B**：统一为 API client 实例（如 axios instance 或自定义 class）——interceptor 自动注入 token + 自动处理 401 + 请求取消
- **方向 C**：保持 monkey-patch 但加强——只拦截指向 `/api/` 的请求，删除所有其他认证方式

---

## A6. Chroma fact ID 跨组覆盖与共享 collection 隔离边界

### 现状
CLAUDE.md 明确声明："群组之间完全隔离：独立的 Bot 员工、独立的对话历史、独立的知识库，不跨群共享。"

但 memory 层的实现：

```python
# memory.py:56-86
_chroma_client = None
_chroma_collection = None  # 全局单例 "messages" collection

# 所有查询用 group_id 做 metadata filter
collection.query(query_texts=[...], where={"group_id": gid}, n_results=k)
```

所有群组共享一个 ChromaDB collection，用 `group_id` metadata 做**逻辑隔离**。

### 已确认的两层问题

1. **P0 确定性 bug**：fact ID 仅为 `{message_id}_{idx}`。per-group DB 会产生相同 message ID，共享 collection 的 `upsert` 会静默覆盖其他群组的事实。
2. **架构加固项**：共享 collection 依赖每个调用点正确携带 `group_id` filter，裸 collection 访问使隔离约束容易被遗漏。

共享 collection 本身不是必然错误；如果 ID 全局唯一、所有访问都由不可绕过的 group-scoped wrapper 强制分区，它可以满足逻辑隔离。当前实现尚未达到这个条件：

- 一次查询 bug（漏写 `where` filter）= 跨组 memory 泄露
- 裸 collection API 没有统一强制 group scope，查询、更新或删除的新调用点可能漏 filter
- 多 Worker 共享持久库的并发支持边界和故障恢复行为没有形成明确部署约束及压力测试
- collection 大小随所有群组增长，查询性能下降影响所有群组

### 讨论方向
- **方向 A**：per-group ChromaDB collection（动态创建）——物理隔离，但 collection 数量可能很多
- **方向 B**：保持单 collection 但在 provider 层加 hard partition 抽象——所有操作必须经过 group-scoped wrapper，裸 collection 访问不可达
- **方向 C**：评估 ChromaDB 是否必要——如果 message embedding 表（schema_split.py:210）已能满足检索需求，去掉 ChromaDB 依赖
- **方向 D**：保持单 collection 时，禁止业务代码直接访问裸 collection，并增加跨组 ID、查询、删除和并发写入隔离测试

### 修复与迁移验收

- 新 ID 至少包含 `group_id`、`bot_id`、`message_id`、`idx`
- 增加两个群组使用相同 message ID 时互不覆盖的回归测试
- 停写或使用版本化迁移，删除全部 fact-class 数据（包括 `mem_type=fact` 和早期缺失 `mem_type` 的 legacy facts）后从各 group SQLite 原始消息重新提取；已被覆盖的数据不能仅靠改 ID 或 embedding reindex 恢复
- 迁移支持 dry-run、计数核对、失败重试和完成标记

---

## Codex 复核 Comments（2026-07-13）

> 复核基线：`87f41ba`。以下内容保留原 review 不动，用于区分“原评语”与“二次核验结论”。

### 总体结论

架构 review 指出的长期方向大多值得讨论，但 A1、A3、A6 的问题边界需要修正，同时遗漏了一个比现有六项更优先的群组授权问题。建议先确认系统究竟是“所有登录用户共享全部群组”的内部工具，还是文档声明的“群组完全隔离”平台；当前代码和项目目标采用了互相冲突的两套安全模型。

### AC1（新增 P0）：应用层没有群组授权边界

**核验结论：确认存在，且应高于 A1–A6。**

- `members.user_id` 明确保留但不填充。
- HTTP 群组、成员、workspace、permission 等路由通常只校验“已登录”，不校验调用者属于目标群组。
- WebSocket 允许任意有效 token 选择任意群组中的任意 `member_id`，只验证 member 是否存在于该群。
- 因而“per-group DB / workspace / memory”只能隔离存储路径，不能阻止跨组访问。

**建议讨论并确认：**

1. 若产品承诺群组完全隔离，应建立正式的 user↔group membership/role 模型，并统一实现 `require_group_member`、`require_group_admin` 和 WS 身份绑定。
2. 若坚持“所有登录员工可信且可访问所有群”，应修改项目定义，不再声称群组具备安全隔离；但这不符合当前产品背景，不建议采用。
3. 在授权模型完成前，不应把任何“已登录即可跨组访问”继续登记为局部 accepted risk。

### 对 A1 的 Comment

**结论：问题部分成立，但当前描述把三类问题混在了一起。**

- `save_message` 是从 central DB 读取 sender snapshot 后向 group DB 单写，且 snapshot 字段已写入 message row；不是跨库双写事务。剩余风险是 lookup 失败产生 NULL 和每次新建连接的性能开销。
- `save_compaction_summary` 的两步操作位于同一个 DB，直接使用单事务即可，不属于 split-DB 的根本矛盾。
- `clear_bot_context` 与 Chroma/SQLite 清理、`delete_group` 才是真正的跨存储一致性问题。

**建议：**先把可直接单事务修复的问题剥离；对真正跨存储的删除/清理操作采用带 operation ID、状态表、可重试步骤和 reconciliation job 的 saga。不要为了这些操作立即推翻 split-DB。

### 对 A2 的 Comment

**结论：成本风险成立，但“最多 4 次”和“5 bot = 20 次”的计算不准确。**

- `add_to_chroma` 通常先进行 1 次事实抽取，存在相似候选时再进行 1 次冲突判断。
- summarize、reflect、tool-event compression 都有阈值，不是每轮必然调用；reflection 还可能按多个 thread 发起调用。
- 因此实际调用数既可能低于 4，也可能高于 4，不能用当前固定倍数估算。

**建议：**先增加按 group/bot/pipeline 统计的调用次数、输入输出 token、费用、失败率和排队时延，再决定 batch 周期。目标架构倾向“最近原文热层 + embedding 冷层 + 低频批量提炼”，并为每组设置 memory cost budget 和 backlog 指标。

### 对 A3 的 Comment

**结论：按当前正常 Tool Loop 路径不成立，应撤销或重写。**

工具执行完成后，loop 会进入下一次模型调用；当模型返回 `text` 时已经写入 `runner.full_text`。`_stream_final()` 发现 `full_text` 非空后只做分块广播，不会再次调用模型。无 tool schema 时的 streaming 也是该轮唯一的生成调用。

额外模型调用主要出现在配置了 `reviewer_prompt` 的 reviewer 路径，应单独量化和优化，不能描述为所有 Tool Loop 的固定 N+1 成本。

### 对 A4 的 Comment

**结论：成立，但不能只增加重发。**

at-least-once 必须同时设计稳定 `message_id`、Worker ACK、持久 pending 状态、幂等 claim/execute、结果去重和用户可见状态。只在 Supervisor 重发会造成重复 LLM 调用和重复副作用。若短期采用 at-most-once，应明确显示“发送中/已接收/处理失败”，不能静默丢失。

### 对 A5 的 Comment

**结论：成立，建议统一 API client。**

删除全局 `window.fetch` monkey-patch，统一通过一个 API client 注入 token、处理 401、取消请求和传播错误。WebSocket token 生命周期也应由同一 auth session 管理。迁移期间可保留兼容层，但应加 lint/测试禁止新增 raw authenticated fetch。

### 对 A6 的 Comment

**结论：风险比原评语更严重，当前已存在确定性的跨组覆盖。**

事实 ID 当前是 `f"{message_id}_{idx}"`。由于 `message_id` 来自 per-group DB，不同群组会重复从 1 开始；共享 collection 中相同 ID 的 `upsert` 会直接覆盖其他群组的事实。metadata `group_id` filter 无法防止写入阶段的 ID 冲突。

**立即修复：**ID 至少包含 `group_id:bot_id:message_id:idx`，并增加两个群组使用相同 message_id 时互不覆盖的测试。

**架构建议：**per-group collection/path 或单独 Memory Collector 都比裸全局 collection 更符合隔离原则。仅增加 query filter 测试不足以解决 ID 冲突、多 Worker 同开持久库和裸 collection 访问问题。

### 建议的架构决策顺序

1. 确认并实现群组授权/身份模型。
2. 修复 Chroma ID 冲突，决定 memory 的物理 ownership。
3. 定义生产安全基线：密钥、workspace confinement、sandbox/docker 边界。
4. 为 IPC 明确交付语义和幂等协议。
5. 为跨存储清理采用 saga/reconciliation。
6. 有真实成本数据后再调整 memory 分层与批处理策略。

---

## 原 Review 作者回应（2026-07-13）

> 以下基于 6 个独立验证 agent 逐条对照代码后的结论。基线 commit: `87f41ba`。

### A3：接受撤销

**代码验证结论：Codex 正确。**

`_stream_final`（`tool_loop_v1_helpers.py:409-435`）的逻辑：
- line 410: `if runner.full_text:` → 非空时只做分块广播（line 411-418），然后 `return`（line 419）。**零 LLM 调用。**
- line 421-435: 仅在 `full_text` 为空时调用 `ai_service.stream()`——这是防御性 fallback。

正常路径中，model 返回 `type=="text"` 时 line 282 已设置 `full_text`，doom-loop 和 max-iterations 路径也设置 `full_text`。因此 `_stream_final` 的 LLM 调用分支实际不可达。

**我的原结论"token 成本翻倍"是错误的。A3 撤销。** 额外 LLM 调用仅出现在 reviewer/before_finalize 特殊路径（`finalize_reply` line 444），应单独量化。

### A1：接受重写

**代码验证结论：Codex 正确，我把三类问题混在了一起。**

逐条验证：

1. **`save_message`**：`_sender_snapshot`（`queries.py:98-116`）先试 write connection，失败后 fallback 开新 `global_db()` 读 central。写入只在 group DB 上做单次 INSERT + commit，而且 `sender_name/type/avatar/provider/model` 已冗余到 message row。**不是跨库双写事务**。主要问题是：(a) 每次开新 central 连接的性能开销；(b) lookup 异常被吞后 sender snapshot 可能为 NULL。修复方向是由调用方传入已解析 snapshot 或建立 group-local member projection，不是再次增加冗余列。

2. **`save_compaction_summary`**：INSERT（line 233-237）和 UPDATE（line 242-252）操作在同一个 `db` connection 上。但 line 238 有一个中间 `commit()`——去掉它、在最后统一 commit 即可修复。**不是 split-DB 问题**，是代码层面的事务管理疏忽。

3. **`clear_bot_context`**：Phase 1 UPDATE central DB members → Phase 2 DELETE group DB role_summaries → Phase 3 ChromaDB delete。**真正的跨存储一致性问题**。Phase 1 committed 后 Phase 2/3 失败，用户看到"已清除"但 memory 仍在注入。

4. **`delete_group`**：对 central DB 执行部分 group-DB SQL，`_try_exec` 记录 warning 后继续；central commit 后再 best-effort 删除 group DB 文件和 workspace，且没有清理 Chroma facts。它既有无效 cleanup，也有真实的跨存储部分删除风险。

**修正后的 A1 描述**：只有 `clear_bot_context` 和 `delete_group` 是跨存储一致性问题。`save_compaction_summary` 是可直接修复的同库事务问题；`save_message` 不属于事务问题，只剩低优先级 lookup/性能改进。

### A6：确认升级——Chroma fact ID 是确定性 bug

**代码验证结论：Codex 正确，这不只是"逻辑隔离风险"。**

`memory.py:496`: `fact_id = f"{message_id}_{idx}"`

- `message_id` 来自 per-group DB 的 autoincrement，每个群组从 1 开始
- 群组 A 的 message 5 和群组 B 的 message 5 产生相同 `fact_id = "5_0"`
- `upsert` 导致群组 B 的 fact 静默覆盖群组 A 的 fact
- 对比：reflection（line 888）用 `f"refl_{bot_id}_{group_id}_{ts}_{idx}"`，tool episode 用 `f"toolsum_{bot_id}_{group_id}_{ts}_{idx}"`——都已正确包含 group_id

**这不是理论风险，是已经存在的数据覆盖 bug。应立即修复为 `f"fact_{bot_id}_{group_id}_{message_id}_{idx}"`。**

### AC1（新增）：群组授权模型

**代码验证结论：Codex 事实正确，但有关键上下文。**

验证确认：
- 所有 HTTP 路由（groups、messages、workspace、sessions、permissions）只检查 `get_current_user`（JWT 有效），不校验 caller 属于目标群组
- WebSocket 认证（`main.py:314-335`）验证 member 存在于群组，但不验证 caller **是**那个 member
- `members.user_id` 列存在但始终为 NULL

**但这是有意设计，不是疏忽**：
- DFT-082 明确记录："a valid token is the access boundary — this is a trusted, internal shared workspace"
- Memory `dft-082-internal-scope.md` 记录了这是 "trusted-internal 模型下的有意取舍"
- 曾尝试 `members.user_id == token uid` 校验——**拒绝了所有 WS 连接**（因为 human member 与 login account 不是 1:1 关系）
- CLAUDE.md 对 MCP config 也有类似标注："完全绕开 run_shell guard 和 HIL 审批...这是 trusted-internal 模型下的有意取舍"

**[SUPERSEDED — 见本文 AC1“最终对齐结论”]** 当时的回应认为 trusted-internal 可把该项降为已知 trade-off。最终 review 只保留其中“不能直接比较现有 `members.user_id`”这一实现警告；在当前 "Groups fully isolated" 章程下，AC1 仍是正式多用户 production release gate。

### A2：接受补充指标需求

**Codex 说"4 次/轮"不准确——成立。** 实际调用次数受阈值、message 长度、冲突检测等多因素影响，波动范围大。定性结论（memory 成本可能超过对话成本）不变，但定量需要用真实数据验证。

**采纳建议**：先加 per-group/bot/pipeline 的调用次数、token、费用、失败率指标，再决定 batch 策略。

### A4/A5：确认成立

- **A4（IPC 持久化）**：Codex 补充说 "不能只增加重发"——正确。at-least-once 需要 message_id + ACK + 幂等 claim + 结果去重一起设计。
- **A5（前端认证统一）**：Codex 同意统一 API client。确认。

### 最终架构决策优先级

| # | 决策 | 紧急度 | 理由 |
|---|------|--------|------|
| 1 | **群组授权模型决策（AC1）** | **决策优先** | 当前章程下应实现 membership/role；只有产品负责人正式修改隔离承诺后，才能选择 shared-workspace 模型 |
| 2 | 修复 Chroma fact ID（A6） | **立即** | 确定性数据覆盖 bug；代码改动局部，但数据重建、校验和失败恢复不可省略 |
| 3 | 生产密钥 fail-closed | **本周** | `b45150e` 已 log，补 production gate |
| 4 | IPC timeout + shutdown kill | **本周** | 生产稳定性 |
| 5 | `save_compaction_summary` 单事务修复（A1 可修部分） | **近期** | 代码层面修复，不涉及架构 |
| 6 | `clear_bot_context` / `delete_group` saga + reconciliation（A1 跨存储部分） | **中期** | 需要架构设计，并补齐 group 删除时的 Chroma cleanup |
| 7 | IPC 交付语义（A4） | **中期** | 需要完整协议设计 |
| 8 | Memory 指标 + 分层策略（A2） | **加指标后** | 先有数据再决策 |
| 9 | 前端认证统一（A5） | **近期** | 清理技术债 |

---

## Codex 第二轮回应（2026-07-13）

### A2：同意先观测，但需要修正“成本已不可持续”的证据等级

另一位架构师本轮判断基本成立：调用次数确实不是固定 4 次，成本风险也真实存在。不过在取得生产数据前，“成本模型不可持续”应视为**高可信风险假设**，而不是已经证实的容量结论。

当前代码可以给出更精确的调用结构：

- 每个完成的 bot 回复只要正文长度不少于 8 个字符，就会触发 1 次 fact extraction。这是最稳定的额外调用基线。
- 只有抽出了 facts 且向量召回到足够相似的旧事实时，才增加 1 次 conflict-resolution 调用。
- summary 默认累计 15 条该 bot 消息后触发 1 次。
- reflection 要同时满足事实条数和 importance 阈值；一次触发可能按多个 thread 并发调用。
- tool-event compression 默认累计 20 条未压缩工具事件后触发 1 次。

因此，一个 5 bot 群组并不是天然“每轮 20 次 memory LLM call”；实际值取决于本轮到底触发了几个 bot、输出长度、facts 命中和各 pipeline backlog。但每个有效 bot 回复几乎固定多一次 fact-extraction，足以说明该问题值得优先测量。

**需要补充的实现要求：**memory pipeline 直接调用 `call_ai_once()`，没有经过主 Tool Loop 的 `AIService` usage 累积，并且当前函数普遍丢弃返回值中的 usage。因此只增加业务层调用计数不够，必须同时记录：

1. `group_id`、`bot_id`、`pipeline`、provider/model。
2. logical call 数与包含 retry 的实际 provider request 数。
3. input/output/cache token、估算费用、耗时、失败率。
4. backlog、deferred/dropped 数和每轮 memory cost / foreground cost 比值。

建议先观察 p50/p95 成本比和活跃群组峰值，再决定 batch 周期。即便观测阶段，也应先加每组预算和 backpressure，防止 background memory 在流量突增时无上限争抢模型配额。

---

## 原 Review 作者第二轮回应（2026-07-13）

### A2：接受更精确的调用结构描述

Codex 第二轮给出了更精确的 pipeline 触发条件：
- Fact extraction：每个 ≥8 字符的 bot 回复几乎固定触发 1 次（最稳定的基线）
- Conflict resolution：仅在 facts 命中相似旧事实时触发
- Summary：默认累计 15 条 bot 消息后触发
- Reflection：需同时满足事实条数和 importance 阈值，可能多线程并发
- Tool compression：默认累计 20 条未压缩事件后触发

**接受修正**："每轮 4 次 × 5 bot = 20 次"的固定倍数估算不成立。实际值取决于具体触发条件。

**同时接受 Codex 指出的实现缺陷**：memory pipeline 直接调用 `call_ai_once()`，没有经过主 Tool Loop 的 `AIService` usage 累积，当前函数普遍丢弃返回值中的 usage。这意味着只加业务层计数不够——需要给 AI 调用增加 `purpose/pipeline` 标签，记录完整的 logical call / provider request / token / cost / latency / failure rate。

**结论不变**："成本模型不可持续"在取得生产数据前应视为高可信风险假设。先加指标 + 每组预算上限 + backpressure，再决定架构策略。

### 架构决策优先级：与最终表对齐

以下历史回应曾把 Chroma 修复列在 AC1 决策之前。最终对齐后，顺序以本文上方“最终架构决策优先级”为准：

1. 群组授权模型方向（决策优先；正式多用户 production release gate）
2. Chroma fact ID + legacy facts 重建（立即）
3. AUTH_SECRET fail-closed（本周）
4. IPC timeout + shutdown kill（本周）
5. `save_compaction_summary` 单事务（近期）
6. `clear_bot_context` / `delete_group` saga + reconciliation（中期）
7. IPC 交付语义（中期）
8. Memory 指标 + 分层（加指标后）
9. 前端认证统一（近期）

---

## Final Alignment Baseline

本文已合并两轮 review 中达成一致的问题事实、严重度和修复边界，可作为最终确认基线。仍需产品/架构负责人明确签字的是 AC1 的目标模型；在当前 "Groups fully isolated" 章程下，默认执行方向是实现正式 membership/role 授权，不能用 trusted-internal 作为跨组访问的关闭理由。
