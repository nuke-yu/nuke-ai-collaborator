# 用隐式会话键给「无 topic」记忆作用域（增强提案）

> 状态：**增强提案，未实现**（前置的一致性修复已全部落地，本提案是可选的进一步增强）
> 日期：2026-06-16（2026-06-17 对齐现状重写）
> 关联：[`MEMORY-SYSTEM-DESIGN.md`](MEMORY-SYSTEM-DESIGN.md)

## 这份文档的来历

它最初写于一轮记忆系统 review 之中，当时三条记忆通道对「无 topic（自由聊天）」各自给了互相矛盾的处理。**那批不一致此后已被逐条修掉**（见下），所以本文从「修 bug 的方案」**降格重写为「一个可选增强」**：现状已自洽，但自由聊天内部仍有残余串味，且自由聊天反思被一刀切抑制——隐式会话键能更优雅地解这两点。

## 现状（前置修复已落地）

`thread_id` 是记忆作用域键。只有 `discussion` 编排器在人给出 topic 后铸出 `disc:{group}:{uuid}`（随机、非内容寻址）；自由聊天 / round_robin / 默认编排器返回 `None`（`core/orchestration/base.py`）。「无 topic」统一落 `""`（Chroma 不接受 `None`，故 `""` 是规范空值）。

三条通道当前对「无 topic」的处理**已统一并自洽**：

| 通道 | 现状 | 落地 |
|---|---|---|
| 摘要 `role_summaries` | 按 `thread_id` 精确召回；自由聊天作 `""`，只取自己的；遗留 `NULL` 永不匹配（不串入） | `7a45665` / `95a1050` / 空值统一 `81fff47` |
| RAG 事实 `retrieve_relevant` | **不**硬过滤；`TimeDecayRanker` 给「同 thread」**软加成**，跨话题仍可召回 | `05a1462`（硬过滤→软加成） |
| 反思 `maybe_reflect` | 按 `thread_id` 分区、各话题独立 per-thread 水位线；**自由聊天事实（`""`）整体不反思** | `ff2ef31`（分区 + per-thread 水位线 + 抑制自由聊天） |

> 历史背景：早先反思共享单一水位线会让非主导话题「饿死」、且自由聊天 `""` 桶会产生跨话题「桥梁洞察」。前者已由 per-thread 水位线（`reflection_state` 主键 `(bot,group,thread)`）+ 按 thread 精确抓取（`53cd74f`）根治；后者由「自由聊天不反思」直接压掉。

## 仍存在的残余缺口（本提案要解的）

「无 topic」现在是**一个常量 `""` 大桶**——所有自由聊天内容共享同一个键。于是：

1. **自由聊天内部仍串味**：上午聊「芒果坏肚子」、下午聊「选股」，两者的摘要都挂 `""`、事实都挂 `""` → 聊选股时芒果的摘要/事实照样作为「同 thread」上浮。讨论之间不串了，但**自由聊天内部不同话题之间还串**。
2. **自由聊天反思被一刀切**：为了不桥接，干脆「`""` 不反思」→ 自由聊天里再有价值的积累也永远不沉淀洞察。

根因都是：自由聊天**事实上有话题段**（按时间自然分段），但被当成一个无差别的 `""`。

## 决策（增强）

给自由聊天一个**隐式会话键**替代常量 `""`：系统从消息流（v1：时间间隔）自己切出「会话段」，每段一个键 `chat:{group}:{anchor}`。`disc:` 不变；`""` 这个大桶被细分成若干会话段键。

这样：
- 摘要 / RAG 的通道逻辑**一行不改**（它们本就按 `thread_id` 作用域/加成）——只是自由聊天的键从常量 `""` 变成 `chat:...`，残余串味自然消失。
- 反思可以从「自由聊天一律不反思」改为「**按会话段反思**」——每段独立归纳，既不跨段桥接，又不再浪费自由聊天的积累。

## 键设计

| 场景 | 键 | 产生方 |
|---|---|---|
| 显式讨论 | `disc:{group}:{uuid}` | `DiscussionOrchestrator`（不变） |
| 隐式自由聊天 | `chat:{group}:{anchor}` | 消息写入路径（新增） |

`anchor` 取该会话段**首条消息写入时**的 `int(time.time()*1000)`——一个 id 无关、天然唯一的不透明值（不需要等 INSERT 拿到行 id，避开鸡生蛋）。它只是个键，不与任何时间戳列的表示绑定。

## 边界规则（v1 = 时间窗）

连续消息间出现 > `CHAT_SESSION_IDLE_GAP`（默认 1800s）的静默 → 结束当前段，下一条消息开新段。

- 新增 config：`NUKE_CHAT_SESSION_IDLE_GAP_SECONDS = 1800`。
- 人和 bot 消息都参与连续性判断；系统 / recap 消息排除在边界检测外（非对话）。

## 存储与计算：落在消息行上，不在内存

给 `messages` 加一列 `session_id`，**写入消息时一次算好**。理由 vs 读时现算：O(1) 读、确定性、天生抗 worker 崩溃（落群库、不依赖内存态）、历史消息自带作用域。

写入逻辑（在所有群消息汇聚的唯一写点 `db/queries.save_message`）：

```
prev = SELECT created_at, session_id FROM messages
       WHERE group_id=? ORDER BY id DESC LIMIT 1
# messages.created_at 是 SQLite TIMESTAMP（UTC 文本，非 epoch）——间隔判定用
# julianday() 之差或在 Python 侧解析，别按数值相减。
if prev is None or gap(prev.created_at, now) > IDLE_GAP:
    session_id = f"chat:{group}:{int(time.time()*1000)}"
else:
    session_id = prev.session_id
```

> 讨论期间的消息也会带 `session_id`，但记忆作用域**优先用显式 `disc:` 键**，`session_id` 仅自由聊天兜底，两者不冲突。

## 注入点（编排器不动）

不改各编排器的 `current_thread_id`（会把同步方法变成带 I/O 的异步、牵动所有实现）。在**两个装配 `MemoryContext` / `MemoryEvent` 的地方**（`executors/plugins/tool_loop_v1_helpers.py` 的 recall ≈:213 与 observe ≈:483）加兜底：

```python
thread_id = _wf.current_thread_id(group_id)              # 显式（讨论）
if thread_id is None:
    thread_id = await current_chat_session_id(group_id)  # 隐式（= 最新消息的 session_id）
```

`current_chat_session_id` = 读群库最新一条消息的 `session_id`（O(1) 索引读）。改面就这两处。

## 三条通道的变化

| 通道 | 本增强带来的变化 |
|---|---|
| 摘要 | 逻辑不变；自由聊天的键由 `""` 变 `chat:...` → 自由聊天内部不再跨话题段串摘要 |
| RAG | 逻辑不变（仍是同 thread 软加成 + 跨 thread 可召回）；自由聊天键变细 → 软加成更精准 |
| 反思 | **行为变化**：把「`""` 不反思」改为「按 `chat:` 会话段反思」。每段独立归纳，自由聊天积累重新能沉淀洞察，且段间不桥接 |

RAG 仍是**软加成而非硬过滤**：它的价值在跨时间/跨会话召回（「记得三周前那个决定」），硬过滤会废掉长期记忆。摘要/反思天生话题绑定 → 硬作用域；RAG → 软偏置。

## 迁移

```sql
ALTER TABLE messages ADD COLUMN session_id TEXT DEFAULT NULL
```

走和近期迁移一致的**群库 hydration 时 `run_migrations`** 路径（`runtime/lifecycle.py`），存量群库下次加载自动补列；主进程端点经 `ensure_group_ready` 依赖也会补（见 `21f116a`）。历史消息 `session_id=NULL` 当作一个「遗留段」，随 TTL 老化，不回填。

> Rollback：`ALTER TABLE messages DROP COLUMN session_id;`

## 行为变化与风险

1. **自由聊天反思被重新启用**（当前是完全不反思）→ 多了 LLM 归纳成本与写入。靠 `REFLECT_MIN_FACTS` 门槛兜底：很短的闲聊段（事实不够）不会触发，符合预期。
2. per-thread 水位线与按 thread 精确抓取**已落地**（`53cd74f`），所以会话段让「话题」数量变多**不再**有早先的「沉寂话题钉低水位线 / 非主导话题饿死」问题——这条旧风险已消除。
3. 自由聊天摘要按会话段作用域是行为变化（之前是整桶 `""`），需回归确认不至于过窄。

## 测试计划（TDD）

- session 铸造：间隔 > 阈值 → 新键；阈值内 → 继承。
- `current_chat_session_id` 兜底：讨论中 → `disc:`；自由聊天 → 最新消息的 `chat:`。
- 摘要：自由聊天下按当前会话段召回，不串入上一段。
- RAG：同会话段事实软加成上浮，跨段仍可召回。
- 反思：两个自由聊天会话段各自归纳、不桥接；过短的段不触发。

## 升级路径

键的形状与三通道消费代码都不依赖「边界怎么判」。要更细的话题切分时，只把边界判据从「时间窗」换成 embedding 语义漂移，**下游一行不改**。

## 落地顺序

1. `messages.session_id` + `save_message` 写入逻辑 + migration
2. `current_chat_session_id` 兜底（两处注入点）
3. 反思：把「`""` 不反思」改为「按 `chat:` 会话段反思」
4. 各自 TDD
