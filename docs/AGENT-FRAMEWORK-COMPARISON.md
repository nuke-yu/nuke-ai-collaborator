# 智能体框架功能与编辑原语横向对比报告 (AGENT-FRAMEWORK-COMPARISON)

本报告对业界主流的终端/编码 Agent 框架（**Claude Code / Claude-haha**、**opencode**、**gsd-2**）与我们当前的项目（**nuke-ai-collaborator**）进行了全功能模块的深度对比，并重点解构了核心的**代码编辑与匹配回退原语（Edit Primitive）**，最后为我们项目的演进提供了落地方案建议。

---

## 一、 全功能模块横向对比表

| 功能模块 / 原语 | Claude Code / Claude-haha (Node/TS) | opencode (Bun/TS) | gsd-2 (Node/Rust) | 我们的项目 (Python/SQLite) |
| :--- | :--- | :--- | :--- | :--- |
| **1. 核心 Agent 架构 & 多角色协作** | **单兵 ReAct 模式**：通过主循环决策工具调用；支持 `runAgent` / `forkSubagent` 动态派生临时子 Agent。 | **单兵 ReAct 模式**：基于 `Effect-TS` 副作用管道控制，侧重于与 CLI 及外部服务（如 Slack）的直接单向整合。 | **RPC 守护进程模式**：使用 `gsd-orchestrator` 控制中心，支持通过守护进程在本地或远程执行多通道任务。 | **多智能体流水线模式**：独特的 BA 需求 $\rightarrow$ Dev 开发 $\rightarrow$ QA 测试流水线，支持人工确认卡片（Gate）的审批状态流。通过全局事件总线（EventBus）订阅/发布，实现了 Workflow 运行阶段与执行器的彻底解耦。 |
| **2. 上下文自动压缩 (Compaction)** | **双重压缩**：基于 Token 预算。包括清除历史旧工具输出的 `microCompact`，和调用 LLM 生成 9 段结构化摘要的 `compact`。 | **纯文本截断**：`tool/truncate.ts`，如果工具输出超长（行/字节数限制），则截断并输出 preview，将全量存盘供查询，无 AI 压缩。 | **模型感知压缩**：`core/compaction/`，分析并存储文件操作（读取/修改列表），提炼为 `CompactionEntry`，重构上下文。 | **5 阶段异步压缩管线**：完美移植了 Claude Code 机制，实现了微压缩清除旧工具结果、Snip剪枝、AI 增量会话摘要、AI 9段全量摘要。<br><br>**异步后台 Worker 架构**：DB 历史压缩完全异步移至后台 Worker（通过 `CompactionTriggered` 事件泵触发），消除了同步工具循环中的阻塞卡顿，彻底消除了响应延迟。 |
| **3. 会话备忘录 (Session Memory)** | **文件级更新**：静默状态下由 LLM 使用 Edit 工具增量更新工作区 `.claude/session-memory/notes.md`，用于对齐进度。 | **无独立模块**：主要依赖 REPL 缓存与上下文剪枝，无单独备忘录写回操作。 | **计划驱动**：通过 `.plans/` 目录和持久化的 Session 状态来同步规划进度。 | **消息级内存 + 项目级文档**：以 `【历史摘要】` 内存消息的形式挂载到对话流中。并且在任务完结时，将复盘持久化到本地工作区的 `retros/run_{anchor_msg_id}.md` 与 `RETRO_LATEST.md` 中，作为项目级文档供人和 Agent 阅读。 |
| **4. 主动记忆提炼 (Auto Memory)** | **后台提炼**：对话结束后，自动运行 extraction Agent 将偏好/规范提炼为独立 md，并更新 `MEMORY.md` 索引。 | **无此机制**：依靠 CLI 历史与 DB 简单记录。 | **无此机制**：依靠 Session 数据库还原历史。 | **逻辑触发复盘提炼**：工作流完结时（`WorkflowPaused` reason="done"）触发。通过专用去噪 Retro Prompt（排除了无架构影响的正常工具执行过程、寒暄与冗余语法纠错等过程噪音）提炼出 4 段 Markdown 格式的复盘（含最终成果摘要、核心技术决策与动因、避坑教训、技术债与后续待办），写入工作区归档。 |
| **5. 记忆固化与融合 (Consolidation)** | **离线 Dream 整理**：异步扫描 transcripts，合并同类记忆，将相对时间（“昨天”）替换为绝对时间以防失效。 | **无此机制**。 | **无此机制**。 | **双重自学技能审批流**：支持 Bot 通过自写规则写入 `skills/learned/draft/`，并重定向写操作，由人类进行 Approve/Reject 审批移动到 `active/` 激活为注入式 Skill，兼顾了智能体自主学习与安全性。复盘产物默认保留为描述性文档，待人工策展后续提升。 |
| **6. 动态文档维护 (Magic Docs)** | **自更新文档**：后台跟踪对话，自动使用 LLM 更新标有 `# MAGIC DOC:` 的 Markdown 说明文档。 | **无此机制**。 | **无此机制**。 | **无此机制**。 |
| **7. 输入预测建议 (Prompt Suggestion)** | **后台 Fork 预测**：LLM 在后台根据最近轮次，预测用户最可能输入的 2-12 字命令并渲染在 REPL 底部。 | **历史回溯**：基于 CLI 历史命令。 | **命令补全**：基于 TUI 补全与 slash 命令。 | **三层建议架构 (Tier 1+2+3)**：<br>1. **Tier 1 (前端状态投影)**：实时从 workflow 卡片状态中投影出 `确认并继续 / 我想修改 / 开始流水线 / 中止` 等动作 Chip，填充输入框或触发操作，绝不自动执行以维护 HIL 安全底线。<br>2. **Tier 2 (复盘文档联动)**：识别工作流 Done 后的 `RETRO_LATEST.md`，推荐 Bot 查看复盘的快捷 Chip。<br>3. **Tier 3 (AI 智能草稿)**：前端触发 `✨ AI 建议`，在后台按需调用轻量 LLM 异步生成沟通消息。注入群组 Bots名册防止 @mention 幻觉，并支持 **in-flight 并发锁**（锁定期间直接返回旧缓存 fallback）以及**空结果 30秒缓存冷却机制（Cooldown TTL）**，物理隔绝爆刷 LLM 风险。 |
| **8. 推测性预执行 (Speculative Execution)** | **影子执行**：在后台 `/tmp/speculation` 下创建目录 Overlay，提前隐式运行预测出的写工具以实现秒级响应。 | **无此机制** | **无此机制** | **无此机制** |
| **9. 缺席重回 Recap (Away Summary)** | **返场总结**：用户挂起会话重新切回时，LLM 自动为用户生成 1-3 句的“刚才进度 Recap”。 | **无** | **启动状态提示**：启动时展示最近 Session 的极简状态。 | **双轨双重重回复盘**：<br>1. **慢轨 (Workflow done)**：复盘提炼出的 Executive Summary 自动覆写到 central DB 的 `groups.away_summary` 字段中。<br>2. **快轨 (Workflow paused/gate)**：异步生成 1-3 句针对当前任务状态和 Bot 完成情况的重回摘要。支持 WebSocket 广播更新，且包含面向具体用户的 unread 个性化 Recap生成 (`generate_personal_recap`)。 |
| **10. 主力代码变更原语 (File Edit)** | **三阶段模糊匹配**：<br>1. Exact 精确替换<br>2. 行首尾空格 Trim<br>3. 连续空白归一化（Whitespace Normalized）。 | **九重 Replacer 回退链**：从精确匹配、行首尾空格、锚点定位、缩进宽容等 9 个降级 Replacer 链式匹配，极度鲁棒。 | **Fuzzy Unicode + Rust Myers**：JS 侧进行 Unicode 全角、引号、BOM 规整；Rust 侧 Myers 算法或 AST-grep 级语义编辑。 | **三阶段回退匹配**：Python 版的 `edit_file` 原语，使用严格 $\rightarrow$ 宽容的级联匹配算法：<br>1. `simple_replacer`（精确子串匹配）<br>2. `line_trimmed_replacer`（忽略行首尾空白）<br>3. `whitespace_normalized_replacer`（连续空白归一化比较）<br>并支持在输出截断时，输出包含偏移量和读取范围的精准续写指引 (`build_completion_hint`) |

---

## 二、 核心编辑原语（Edit）的实现解构

对已存在文件的修改是 LLM 编码中最频繁的操作。如果强迫 LLM 重吐整个大文件，一旦撞上单次输出 Token 上限（通常为 4096），代码就会发生物理截断。因此，成熟框架**无一例外都剥离了对 `write_file`（重写整个文件）的依赖，将 `edit_file`（精确文本替换）作为主力原语**。

### 1. Claude Code 的匹配回退
* **实现**：`src/tools/FileEditTool/`
* **思路**：依次进行 Exact Match $\rightarrow$ Trimmed Lines Match $\rightarrow$ Whitespace Normalized Match。在匹配前会探测文件的换行符（CRLF / LF）并进行统规，同时自动剥离 invisible BOM，极大增强了对模型由于缩进或空格偏差产生的小毛病的容错度。

### 2. opencode 的九重级联回退（TS/JS）
* **实现**：`packages/opencode/src/tool/edit.ts` 中的 `replace()` 函数。
* **思路**：设计了极为详尽的 9 层级联 Replacer 链：
  1. **SimpleReplacer**：直接子串查找。
  2. **LineTrimmedReplacer**：逐行去除首尾空格比对。
  3. **BlockAnchorReplacer**：只对齐首尾两行（中间模糊比对）。
  4. **WhitespaceNormalizedReplacer**：多空格、制表符归一。
  5. **IndentationFlexibleReplacer**：容忍缩进格式不同（对齐空格与 Tab）。
  6. **EscapeNormalizedReplacer**：转义处理。
  7. **TrimmedBoundaryReplacer**：忽略边缘空行。
  8. **ContextAwareReplacer**：上下文宽容匹配。
  9. **MultiOccurrenceReplacer**：处理 replaceAll 多处替换。
* **反馈**：精确回馈 `notFound` 或 `notUnique`（出现多次），如果匹配多处会要求 LLM 补充更多上下文以确保修改唯一，绝不乱改。

### 3. gsd-2 的高性能 Rust Myers 算法 + AST 语义级替换
* **实现**：`packages/native/` (Crate `similar` 绑定) 与 `packages/pi-coding-agent/src/core/tools/edit.ts`
* **思路**：
  * **AST 语法匹配**：`astEdit` 结合 `tree-sitter`，跳过单纯的字面值比对，直接对抽象语法树节点进行精确的语义重构。
  * **Myers Diff 高速生成**：使用 Rust 底层 Meyers 算法产生 unified diff，并实现了一个 JS 纯代码 Myers diff 生成器作为 Fallback，防止本地原生动态库挂起（Native Hang）破坏 CLI 会话。

---

## 三、 我们的项目演进与落地实现状态

我们已成功落实上述集成建议，彻底解决了大文件修改易截断、Token 限制过低等痛点，落地细节如下：

### 1. 补齐与激活 `edit_file` 工具
* **落地实现**：在 [workspace_tools.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/workspace_tools.py) 中注册并完全激活了 `edit_file` 工具，对应的核心引擎位于 [edit.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/editing/edit.py) 中的 `apply_replacement` 替换器。
* **匹配健壮性**：完整落地了三阶段级联回退逻辑，包含 `simple_replacer`、`line_trimmed_replacer` 以及 `whitespace_normalized_replacer`，对模型产生的微小空格及缩进偏差具有极高的容错性。
* **VFS 机制复用**：`_handle_edit_file` 严格复用了 VFS 路径校验、文件锁以及历史快照备份机制，实现了编辑操作的安全隔离。

### 2. 升级文件写入截断协议（Truncation Warning）
* **落地实现**：完全废弃了过时的 `replace_file_content` 提示，统一迁移到 [truncation.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/editing/truncation.py)。
* **指引提示**：当 `write_file` 被截断时，输出包含偏移量和读取范围的精准续写指引 (`build_completion_hint`)，引导模型使用 `edit_file` 以当前文件末尾的唯一文本为锚点进行差分续写，或建议其拆分大文件。

### 3. 提升 Dev/QA 阶段默认 `max_tokens`
* **落地实现**：在 [schema.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/db/schema.py) 和 [migrations.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/db/migrations.py) 中，将 members 表的 `max_tokens` 默认值提升至 `8192`。
* **动态调节**：基于 [model_limits.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/ai/model_limits.py) 的 `resolve_max_tokens`，根据不同模型供应商（例如 DeepSeek 为 8192，Claude 为 64000）进行动态 Clamp 上限保护，安全防范截断同时充分发挥长上下文优势。

### 4. 全面贯彻 "edit-first" 指导原则
* **落地实现**：更新了 [pipeline.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/pipeline.py) 中 Dev 开发阶段的系统 `instruction` 提示词。
* **明确约束**：强制要求模型“新建文件使用 `write_file`，修改已有文件必须使用 `edit_file`”，严禁重发整文件。同时，BA 依然保持 Allowed Tools 白名单（无写类工具），防止需求分析阶段产生意外的代码落盘。
