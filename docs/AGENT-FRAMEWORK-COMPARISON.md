# 智能体框架功能与编辑原语横向对比报告 (AGENT-FRAMEWORK-COMPARISON)

本报告对业界主流的终端/编码 Agent 框架（**Claude Code / Claude-haha**、**opencode**、**gsd-2**）与我们当前的项目（**nuke-ai-collaborator**）进行了全功能模块的深度对比，并重点解构了核心的**代码编辑与匹配回退原语（Edit Primitive）**，最后为我们项目的演进提供了落地方案建议。

---

## 一、 全功能模块横向对比表

| 功能模块 / 原语 | Claude Code / Claude-haha (Node/TS) | opencode (Bun/TS) | gsd-2 (Node/Rust) | 我们的项目 (Python/SQLite) |
| :--- | :--- | :--- | :--- | :--- |
| **1. 核心 Agent 架构 & 多角色协作** | **单兵 ReAct 模式**：通过主循环决策工具调用；支持 `runAgent` / `forkSubagent` 动态派生临时子 Agent。 | **单兵 ReAct 模式**：基于 `Effect-TS` 副作用管道控制，侧重于与 CLI 及外部服务（如 Slack）的直接单向整合。 | **RPC 守护进程模式**：使用 `gsd-orchestrator` 控制中心，支持通过守护进程在本地或远程执行多通道任务。 | **多智能体流水线模式**：独特的 `BA 需求` $\rightarrow$ `Dev 开发` $\rightarrow$ `QA 测试` 流水线，自带人工确认卡片（Gate）的审批状态流。 |
| **2. 上下文自动压缩 (Compaction)** | **双重压缩**：基于 Token 预算。包括清除历史旧工具输出的 `microCompact`，和调用 LLM 生成 9 段结构化摘要的 `compact`。 | **纯文本截断**：`tool/truncate.ts`，如果工具输出超长（行/字节数限制），则截断并输出 preview，将全量存盘供查询，无 AI 压缩。 | **模型感知压缩**：`core/compaction/`，分析并存储文件操作（读取/修改列表），提炼为 `CompactionEntry`，重构上下文。 | **5 阶段压缩管线**：完美移植了 Claude Code 机制，实现了微压缩清除、Snip剪枝、AI 9段摘要、软删除与会话重构。 |
| **3. 会话备忘录 (Session Memory)** | **文件级更新**：静默状态下由 LLM 使用 Edit 工具增量更新工作区 `.claude/session-memory/notes.md`，用于对齐进度。 | **无独立模块**：主要依赖 REPL 缓存与上下文剪枝，无单独备忘录写回操作。 | **计划驱动**：通过 `.plans/` 目录和持久化的 Session 状态来同步规划进度。 | **消息级内存**：以 `【历史摘要】` 内存消息的形式挂载到对话流中，尚未在本地工作区生成独立的 Markdown 文件。 |
| **4. 主动记忆提炼 (Auto Memory)** | **后台提炼**：对话结束后，自动运行 extraction Agent 将偏好/规范提炼为独立 md，并更新 `MEMORY.md` 索引。 | **无此机制**：依靠 CLI 历史与 DB 简单记录。 | **无此机制**：依靠 Session 数据库还原历史。 | **无此机制**。 |
| **5. 记忆固化与融合 (Consolidation)** | **离线 Dream 整理**：异步扫描 transcripts，合并同类记忆，将相对时间（“昨天”）替换为绝对时间以防失效。 | **无此机制**。 | **无此机制**。 | **无此机制**。 |
| **6. 动态文档维护 (Magic Docs)** | **自更新文档**：后台跟踪对话，自动使用 LLM 更新标有 `# MAGIC DOC:` 的 Markdown 说明文档。 | **无此机制**。 | **无此机制**。 | **无此机制**。 |
| **7. 输入预测建议 (Prompt Suggestion)** | **后台 Fork 预测**：LLM 在后台根据最近轮次，预测用户最可能输入的 2-12 字命令并渲染在 REPL 底部。 | **历史回溯**：基于 CLI 历史命令。 | **命令补全**：基于 TUI 补全与 slash 命令。 | **无此机制**。 |
| **8. 推测性预执行 (Speculative Execution)** | **影子执行**：在后台 `/tmp/speculation` 下创建目录 Overlay，提前隐式运行预测出的写工具以实现秒级响应。 | **无此机制** | **无此机制** | **无此机制** |
| **9. 缺席重回 Recap (Away Summary)** | **返场总结**：用户挂起会话重新切回时，LLM 自动为用户生成 1-3 句的“刚才进度 Recap”。 | **无** | **启动状态提示**：启动时展示最近 Session 的极简状态。 | **无此机制** |
| **10. 主力代码变更原语 (File Edit)** | **三阶段模糊匹配**：<br>1. Exact 精确替换<br>2. 行首尾空格 Trim<br>3. 连续空白归一化（Whitespace Normalized）。 | **九重 Replacer 回退链**：从精确匹配、行首尾空格、锚点定位、缩进宽容等 9 个降级 Replacer 链式匹配，极度鲁棒。 | **Fuzzy Unicode + Rust Myers**：JS 侧进行 Unicode 全角、引号、BOM 规整；Rust 侧 Myers 算法或 AST-grep 级语义编辑。 | **三阶段回退匹配**（本项目正集成）：`backend/editing/` 已实现 Python 版的 Exact $\rightarrow$ LineTrimmed $\rightarrow$ WhitespaceNormalized。 |

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

## 三、 对我们项目（nuke-ai-collaborator）的演进建议

我们项目目前的痛点在于**只有整文件覆盖的 `write_file`，缺乏 `edit_file` 差分修改原语**，逼着 LLM 为任何微小的变动都重吐几百行的整文件，经常在 Dev 开发阶段撞上输出截断。

为了根治此架构级缺陷，建议按以下 4 步落实集成：

### 1. 补齐 `edit_file` 工作区工具与 Handler
* **方案**：在 `backend/executors/plugins/workspace_tools.py` 的注册表里完全激活 `edit_file`，并将 `_handle_edit_file` 接入 `backend/editing/edit.py` 中的 `apply_replacement` 文本替换引擎。
* **匹配健壮性**：复用我们已在 `backend/editing/replacers.py` 中实现的级联回退：
  ```
  REPLACERS = [
      simple_replacer,                 # 精确查找
      line_trimmed_replacer,           # 忽略行首尾空格
      whitespace_normalized_replacer,  # 空白压缩归一
  ]
  ```
* **VFS 机制复用**：`_handle_edit_file` 内部的读写文件应复用 `workspace` 模块现有的 VFS 路径校验、文件锁和历史快照备份机制。

### 2. 修掉并升级截断协议（Truncation Warning）
* **方案**：全面弃用早已被遗弃的 `replace_file_content` 坏提示。
* **改进文案**（在 `backend/editing/truncation.py`）：
  当 `write_file` 被截断时，不要让模型重发整个文件，提示它：
  > "该文件只写入了前 N 字符不完整，请用 edit_file 以当前文件末尾的一小段唯一文本作为 old_string，替换为「该段 + 剩余未写入的代码内容」进行锚点续写，或者将大文件拆分为较小模块分别写入。"

### 3. 抬高 Dev 阶段默认 `max_tokens`
* **方案**：由于我们项目目前的 SQLite 数据库在新建成员时，`max_tokens` 被硬编码为了 `4096`。对于复杂开发任务，这严重限制了模型的表达。
* **建议**：在 `db/schema.py`、`db/migrations.py` 和 Bot 实例化地方，将 Dev/QA 的默认 `max_tokens` 升级至 `8192`。由于 `resolve_max_tokens` 已经有了按模型 provider 智能 clamp 上限的机制（DeepSeek 上限为 8192，Claude 为 64000），抬高初值是安全的，能从源头预防截断。

### 4. Dev 阶段指令导向 “edit-first”
* **方案**：更新 `backend/core/orchestration/pipeline.py` 中 Dev 开发阶段的系统 `instruction` 指引：
  * 明确要求：**“新建文件时使用 `write_file`，修改已有文件必须使用 `edit_file`”**。
  * 严禁重发整文件，以大文件拆分和差分更新为主。
  * 维持 BA 的 Allowed Tools 白名单（不给 BA 分配写类工具 `write_file` / `edit_file`），物理保证 BA 不在澄清需求阶段落盘代码。
