# 工具记忆系统设计（无 observer 的四层方案）

> 2026-06-27 · 作者 nuke
> 相关 commit：`19aeda6`(L1) · `4b426ca`(L3) · `05a4322`(L4) · `2e24245`(L3-FTS5)

---

## 1. 背景与触发

起因是调研开源项目 **[thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)**——一个给 AI agent 做"跨 session 持久记忆"的系统。它的核心做法是：

- 用**生命周期 hooks** 捕获 agent 每次工具调用
- 雇一个**独立的 observer LLM**（常驻 SDK session，靠 `resume` + prompt cache 累积上下文）旁观主 session，把每次工具调用压成结构化 XML `<observation>`
- 存进 SQLite(FTS5) + Chroma 向量库
- 检索走 **3-layer workflow**：`search`(只返回 index+ID) → `timeline`(anchor 周围) → `fetch`(仅对筛过的 ID 取全文)，号称省 ~10x token
- session 开始时语义注入相关记忆

### 关键否决：不要 observer

claude-mem 最贵、最重的部分就是那个**为压缩记忆而常驻的第二个 LLM**。在本项目里这是重复付费：

- 本项目是**群组式 AI 协作平台**，群里的 bot 本身就是那个常驻 LLM、是"最懂项目上下文的成员"
- 再挂一个旁观 LLM = 多一份模型成本 + 多一个进程，与本项目"≤30 人 + 100 项目、单机进程隔离"的规模取向冲突
- 用户明确要求：**不额外烧模型**

于是改走 **"零模型确定性捕获 + 复用 bot 已有推理"** 的路线。

---

## 2. 讨论与决策过程

权衡过的"无 observer 怎么有记忆"的几条路：

| 方案 | 结论 |
|---|---|
| 复刻 observer LLM | ❌ 否决——正是要去掉的东西 |
| 确定性代码抓事件（零模型） | ✅ 采纳为 L1：工具调用的"发生了什么"用纯代码提取，不需要模型来叙述 |
| bot 自驱写记忆（复用已有推理） | ✅ 已被既有 `observe`/`recall`（Chroma 消息记忆）覆盖，即 L2 |
| 三层检索协议（与模型无关） | ✅ 采纳为 L3：claude-mem 唯一值得无条件抄的部分，省 token 的关键是 index→fetch 分离 |
| 批量压缩成持久记忆（1 次/触发，不常驻） | ✅ 采纳为 L4：用现有 group 模型，复用既有 `maybe_summarize`/`maybe_reflect` 的 turn 后门控范式 |
| Chroma 逐事件向量化 | ❌ 不做——L4 已把蒸馏 episode 塞进 Chroma，语义召回那档已覆盖；逐事件 embedding 是重复劳动 + 烧 embedding |

核心分工：**原始事件**走确定性日志 + FTS 关键词检索（快、零模型）；**蒸馏结论**走 Chroma 语义召回（L4 产出，自动接入既有 recall / session-init 注入）。

---

## 3. 架构总览

```
工具调用 (builtin/skill/shell/MCP)
   │
   ▼  executors/tool_dispatch.dispatch_tool   ← 唯一收口
 ┌─────────────────────────────────────────────┐
 │ L1  fire-and-forget 写 tool_events 行         │  零模型
 └─────────────────────────────────────────────┘
                    │ (turn 后 observe)
                    ▼
 ┌─────────────────────────────────────────────┐
 │ L4  条数门控 → 1 次 call_ai 压缩成持久记忆     │  1 次模型/触发
 │     → 写 Chroma(tool_episode) → 标记 compressed│
 └─────────────────────────────────────────────┘
                    │
 检索：              ▼
 ┌──────────────┐  ┌──────────────────────────┐
 │ L3 三层检索   │  │ 既有 recall / session 注入 │
 │ search/      │  │ （含 L4 蒸馏 episode）      │
 │ timeline/    │  └──────────────────────────┘
 │ fetch        │
 │ (FTS5+bm25)  │
 └──────────────┘
```

| 层 | 做什么 | 模型成本 | 落点 |
|---|---|---|---|
| **L1** | 确定性事件日志 | 0 | `tool_dispatch.dispatch_tool` → `ai/tool_events.record_event` → 群库 `tool_events` 表 |
| **L2** | bot 自驱记结论 | 复用既有推理 | 既有 `ChromaMemoryProvider.observe`（无新增） |
| **L3** | 三层检索（省 token） | 0 | `ai/tool_events.{search,timeline,fetch}_events` + builtin 工具 `search_memory/memory_timeline/memory_fetch` |
| **L4** | 批量压缩成持久记忆 | ~1 次/触发 | `ai/tool_events.maybe_compress_tool_events`，挂在 `observe` 的 gather |

---

## 4. 各层详解

### L1 — 确定性工具事件日志（commit `19aeda6`）

- **唯一收口**：`executors/tool_dispatch.dispatch_tool` —— serial 和 parallel 两条执行路径都汇到这里，且能拿到 `(result, is_error)`，builtin 与 MCP 全覆盖。
  - ⚠️ **没用 `tool_executor` 的 `_after_hooks`**：那套只覆盖走 tool_executor 的工具，**MCP 工具走 ToolRouter 会绕过它**，漏一半事件。
- **fire-and-forget**：`asyncio.create_task` + 强引用集合防 GC；`group_id` 缺失即跳过；失败一律吞掉（只有"缺表=迁移缺口"才响亮上抛，对齐 `ai.memory` 约定）。**绝不阻塞主 tool loop**。
- **零模型提取**：`args_summary`/`result_summary` = `redact_secrets` 脱敏 + head/tail 截断（2k 字符，巨型 Read 的轻量版防护）；`files_touched` 从 path 类入参抠；`command` 从 `run_shell` 的 cmd 抠。
- **表**（群库 `tool_events`，schema_split + migration_025）：`id, ts(ms), group_id, bot_id, thread_id, tool, args_summary, result_summary, is_error, files_touched(JSON), command, compressed`。

### L3 — 三层检索（commit `4b426ca`，FTS5 升级 `2e24245`）

借鉴 claude-mem 的 `__IMPORTANT` 三层工作流：

```
search(query)         → 只返回 index+ID（便宜，~50 tok/条）
timeline(anchor=ID)   → 看某条周围时序
fetch(ids=[...])      → 仅对筛过的 ID 取全文（贵）
```

- 查询函数：`ai/tool_events.search_events / timeline_events / fetch_events`，全部 `group_id` 强制过滤，**绝不跨群**。
- builtin 工具：`search_memory` / `memory_timeline` / `memory_fetch`，description 抄了 claude-mem 的"先 search 拿 ID 再 fetch，省 10x"。
- **暴露方式**：进 `tool_loop_v1` 的 manifest（`_WORKSPACE_TOOLS + RD_TOOLS + MEMORY_TOOLS`）——**manifest 才是模型可见工具的唯一来源**。
  - ⚠️ 注意 `search`/`code_intel`/`mcp_authenticate` 是"注册但不进 manifest"的 register-only 特例，别照它们的样板。记忆召回设为默认能力（像 read_file/run_shell）。

#### FTS5 升级（`2e24245`）

`search_events` 从 LIKE 子串升级为 **FTS5 排序检索**：

- external-content 虚表 `tool_events_fts`（按 `rowid=id` 镜像文本列，**不复制数据**）+ bm25 相关性排序 + 分词。
- AFTER INSERT/DELETE 触发器保持同步。
- 三路 dispatch：空 query → `_search_recency`；非空 → `_search_fts`（MATCH + bm25）；FTS5 不可用或 query 被拒 → `_search_like` 降级。
- 用户 query 净化成带引号的 FTS phrase，特殊字符不会引发语法错误。
- **鲁棒铁律**：虚表与触发器**要么一起建、要么一起跳，绝不半残**——否则"留着触发器删了表"会让 `record_event` 的 INSERT 崩（这是测试逮到的真实坑）。FTS5 不可用时 migration_027 早退、`init_group_db` 的 try 整体失败，二者皆不创建。

### L4 — 批量压缩成持久记忆（commit `05a4322`）

- **触发点**：`ChromaMemoryProvider.observe` 的 `gather` 第 4 条子 pipeline（与 `add_to_chroma`/`maybe_summarize`/`maybe_reflect` 并列，继承 fail-soft + schema-gap 处理）。
  - 用既有 turn 后 `observe` 门控，**没接真正的 session-end hook**——与 `maybe_summarize` 一致、风险更低。
- **门控**：纯条数。某 bot 在某群 `compressed=0` 的事件达到 `TOOL_EVENT_COMPRESS_THRESHOLD`（默认 20）才触发，否则早退**不调模型**。
- **压缩**：一次 `call_ai_once` 把这批（最多 `MAX_BATCH`=40）总结成 1–3 条持久结论，写进 Chroma（`mem_type=tool_episode`，timestamp 用事件 ts ms→s 对齐 `time.time()`），再把这批 `UPDATE compressed=1`。`NO_INSIGHT` 也推进，避免重复压同批。
- **接入既有召回**：写进 Chroma 后，自动被既有 `recall` / session-init 语义注入取到——零新检索管线。
- **保留**：`_prune_compressed` 低概率后台删除 `compressed=1` 且超 `TOOL_EVENT_RETENTION_DAYS`(默认 30) 的原始行，防表无限增长。

---

## 5. 不变量与安全属性

- **群组隔离**（项目铁律）：所有数据落 **per-group DB**（不进 central），所有查询 `group_id` 强制过滤。
- **fail-open / fail-soft**：L1 记录失败不阻塞主 loop；L4 是 observe gather 的隔离子 pipeline，单条失败不影响其余。唯一会响亮上抛的是 `is_missing_schema_error`（迁移缺口必须被发现）。
- **脱敏**：进库前过 `redaction.redact_secrets`。
- **零额外常驻进程/模型**：L1/L3 零模型；L4 复用群模型、1 次/触发，无 observer。

---

## 6. 配置旋钮（`core/config.py`）

| 常量 | 默认 | 作用 |
|---|---|---|
| `TOOL_EVENT_COMPRESS_THRESHOLD` | 20 | L4 触发的未压缩事件条数门槛 |
| `TOOL_EVENT_COMPRESS_MAX_BATCH` | 40 | 单次压缩最多吃多少条 |
| `TOOL_EVENT_COMPRESS_MAX_INSIGHTS` | 3 | 单次最多产出几条持久结论 |
| `TOOL_EVENT_RETENTION_DAYS` | 30 | 已压缩原始行的保留天数 |

均可用 `NUKE_*` 环境变量覆盖。

## 7. 数据迁移

`migration_025/026/027` 均为 **schema 迁移**，由 `db/migrations.py` 的 `run_migrations` 在启动时**自动幂等执行**（群库经 `ensure_group_db_ready` 补齐），**无需手动 `--apply`**。

- 025：建 `tool_events` 表 + 索引
- 026：加 `compressed` 列 + 未压缩查找索引
- 027：FTS5 虚表 + 同步触发器 + 回填（best-effort，FTS5 不可用则降级 LIKE）

---

## 8. 与 claude-mem 对照

| 维度 | claude-mem | 本方案 |
|---|---|---|
| 记忆生成 | 独立 observer LLM 旁观、每次工具调用都压 | 代码抓事件（L1）+ bot 自驱（L2）+ 批量压缩（L4，1 次/触发） |
| 常驻模型/进程 | 是（observer SDK session） | 否 |
| 隔离单位 | project（cwd 派生） | group（per-group DB + 权限 + Chroma） |
| 检索 | 3-layer + FTS5 + Chroma | 3-layer + FTS5（原始）+ Chroma（蒸馏 episode） |
| 注入 | session 开始自动注入 | 既有 recall / session-init 注入（含 L4 episode） |

砍掉的：observer 模型、observer 进程、per-tool 模型成本、resume/prompt-cache 复杂度。
保留的：确定性召回、三层省 token 检索、自驱持久记忆、群组隔离。

---

## 9. 刻意未做

- **Chroma 逐事件向量化**：L4 已把蒸馏 episode 入 Chroma，语义召回已覆盖；逐事件 embedding 是重复 + 烧 embedding 成本。如确有"对未蒸馏原始事件做语义检索"的需求再议。

---

## 10. 涉及文件

- `executors/tool_dispatch.py` — L1 收口
- `ai/tool_events.py` — record / search / timeline / fetch / compress / prune
- `executors/plugins/memory_search_tool.py` — 三个 builtin 检索工具
- `executors/plugins/tool_loop_v1.py`、`workspace_tools.py` — manifest 暴露 + 注册
- `ai/memory_provider.py` — observe 接入 L4
- `core/config.py` — L4 旋钮
- `db/schema_split.py`、`db/migrations.py` — 表/列/FTS schema + migration 025/026/027
- `tests/test_tool_events.py` — L1/L3/L4/FTS 全覆盖（32 用例）
