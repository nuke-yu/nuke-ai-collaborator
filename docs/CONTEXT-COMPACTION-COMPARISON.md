# 🔄 上下文压缩设计：与三大主流框架的工程横向对比

本篇文档对 **Nuke AI Collaborator 的上下文压缩（Context Compaction）系统** 与业内三个优秀 AI Agent 框架（**OpenCode**、**gsd-2**、**Claude Code**）的底层设计、触发机制、安全防御及持久化策略进行深度横向对比，以明确我们系统的工程抉择与定制化落地成果。

---

## 一、 四大框架横向对比表

| 维度 / 特征 | OpenCode (Rust) | gsd-2 (TypeScript) | Claude Code (TypeScript) | 我们的框架 (Nuke AI - Python) |
| :--- | :--- | :--- | :--- | :--- |
| **Token 估算** | JSON 序列化（精确 `stringify // 4`，但存在大数组序列化性能开销） | 字符数折算（`chars/4`） | 字符数折算（`chars/4`） | **优化版字符数折算**（按消息结构与 Content 字符数快速估算，免去每次 `json.dumps` 全数组序列化开销） |
| **触发机制** | Token 阈值（默认 10,000）且可压缩消息数 > 4时自动触发。 | **双重触发 + 手动**：<br>1. Token 阈值；<br>2. 拦截 LLM `context_overflow` 报错重试；<br>3. 手动命令 `/compact`。 | Token 阈值 + 溢出检测 `isOverflow`。<br>**支持自动回滚**到上一次未溢出的 User 消息并重放。 | **对齐 gsd-2 / Claude Code**：<br>1. Pre-run 前置检查自适应 Token 阈值；<br>2. **API 溢出恢复**：三个核心执行点拦截 `AIContextOverflowError` 自动压缩重试。 |
| **摘要生成方式** | **本地规则程序化提取 (零延迟)**：<br>由 Rust 在 `compact.rs` 中程序化提取，不调用 LLM。优点：0 延迟、0 费用、100% 稳定，防幻觉。 | **LLM 异步生成** (`compaction.ts`)：<br>分为初次总结和更新总结。支持长消息分块迭代。 | **专属 Subagent + UPDATE 迭代合并**：<br>当已有摘要时，构建 `UPDATE` 提示词对已有摘要进行迭代合并。 | **混合通道 (Strategy 3 + 4)**：<br>1. 优先采用增量追加模式（对齐 Claude Code 的迭代合并思想，只对新 Delta 消息生成摘要追加）；<br>2. 降级为 9 段式 LLM 全量结构化压缩。 |
| **摘要结构与格式** | **XML 结构化** `<summary>`：<br>包含压缩统计、使用工具、最近 3 条请求、Pending 任务、文件路径、截断 Timeline。 | **Markdown 结构化看板**：<br>`## Goal` / `## Constraints` / `## Progress` / `## Key Decisions` / `## Next Steps` / `## Critical Context`。 | **Markdown 严格模板**：<br>比 gsd-2 多一个 `## Relevant Files` 字段（相关文件列表）。 | **对齐 Claude Code 模板**：<br>采用 **9 段式严格 Markdown 模板**（带 `<analysis>` 思考草稿区，压缩后由程序过滤剥离），保留 Pending 任务与文件列表。 |
| **长文本/工具输出防御** | **输入截断 + 工具去重**：<br>首尾截断 stdout，限制 Paste 长度，自动精简重复运行的 Tool 节点。 | **分块总结 + 局部截断**：<br>单条消息 > 2,000 字符强制截断。采用 Markdown 标题边界截断防止格式破碎。 | **文件缓存 + 命令行索引 Hint (`truncate.ts`)**：<br>输出 > 2000 行/50KB 时，自动存入本地临时文件，仅返回 Preview 以及引导 LLM 使用 `grep` 读取的 Hint。 | **双端截断 (Head + Tail 各 10K)**：<br>防止因单侧截断丢失命令行退出码（exit code）或关键 pass/fail 状态。 |
| **旧数据剪枝 (Pruning)** | 直接按历史消息条数进行 Compaction 压缩。 | 无。 | **逆序扫描擦除 (`compaction.ts`)**：<br>若累积工具返回超过 40,000 tokens，**直接在 DB 中擦除较早工具的 Output**。 | **Strategy 1 工具结果微压缩**：<br>对齐 Claude Code 剪枝思路，对 `run_shell`、`read_file` 等高频工具的旧返回内容直接在内存中替换为 `[旧工具结果已清除]` 占位符。 |
| **断点恢复与持久化** | **Continuation 提示词**：<br>使用 `COMPACT_DIRECT_RESUME_INSTRUCTION` 告知 LLM 续接历史，不要寒暄复述。 | **Markdown 快照**：<br>压缩前将不超 2KB 的快照写入 `.gsd/last-snapshot.md`，重启通过 `gsd_resume` 重建状态。 | **会话回滚与重放 (`overflow.ts`)**：<br>溢出时撤销引发溢出的 Assistant 输出，执行 Compaction 后自动重新提交。 | **1. DB 归档持久化**：超 30K 时在 SQLite 软删除老消息并归档 9 段快照，激活时自动拉起；<br>**2. Continuation 续接**：对齐 OpenCode 注入续接提示词。 |
| **防退化/防幻觉保护** | **天然免疫**（由于采用本地 Rust 规则提取，摘要绝对不会幻觉或劣化）。 | **Degenerate Summary Guard**：<br>若生成的摘要 < 100 字符或结构缺失，触发重写；二次失败则拒绝更新。 | **Plugin 钩子拦截**：<br>通过 `experimental.session.compacting` 等 Plugin 钩子拦截干预或注入特定的 Anchored 摘要数据。 | **1. 熔断器机制**：连续 3 次 AI 压缩失败则自动熔断，退化为无 AI 剪枝模式防死锁；<br>**2. VFS 跨压缩跟踪**：记录修改文件在压缩后自动重注入。 |

---

## 二、 核心设计差异与工程权衡

### 1. 为什么我们没有采用 OpenCode 的「纯代码规则提取」？
* **权衡分析**：OpenCode 通过 Rust 本地解析代码提取摘要，确实达到了零延迟、零费用和绝对的防幻觉稳定性。但其弊端在于**灵活性极低**，无法处理复杂的排障过程、架构决策和临时产生的设计变更。
* **我们的做法**：我们最终落地了 **gsd-2 / Claude Code 的 LLM 异步压缩路线**。为了规避幻觉和退化，我们通过 Strategy 3（基于上一版本摘要的 Delta 增量追加）和 Strategy 4（带 `<analysis>` 思考区的严格 9 段模板）来强约束 LLM 的输出结构，保证其记忆精度。

### 2. 对齐 Claude Code 的「剪枝」与 gsd-2 的「双端防御」
* **工具结果剪枝 (Pruning)**：Claude Code 逆序扫描并擦除超过 40,000 tokens 的旧工具返回。我们在 `compact.py` 中实现了 **Strategy 1 (微压缩)**，采用轻量化 Python 内存扫描，将过期的工具执行结果直接擦除并替换为 `[旧工具结果已清除]`，无需调用 LLM 即可清理 80% 的冗余上下文。
* **工具输出截断**：对于超长输出，我们没有采用 OpenCode 的单侧截断（会导致日志尾部的 Exit Code、Pass/Fail 等关键运行结果丢失），而是实现了 **Head + Tail 各 10K 双端截断**，既保留了日志的头部调用环境，又保证了尾部的核心结果信号完好。

### 3. Web 常驻服务模式下的「DB 层持久化归档」（Nuke AI 独创）
* **背景差异**：OpenCode, gsd-2 以及 Claude Code 都是 **CLI 单次运行工具 (Ephemeral)**。会话在本地终端退出后，内存状态随之销毁，不需要考虑历史记录无限增长带来的系统性负担。
* **工程挑战**：Nuke AI Collaborator 是一款 **常驻式 Web 服务**，用户的聊天记录和机器人的执行历史必须永久存入 SQLite 数据库。如果不做处理，随着时间推移，单组的 DB 加载和 Session 反序列化将会严重阻碍 Event Loop，造成严重的网络延迟和内存泄露。
* **创新机制**：我们设计了 **SQLite DB Compaction 异步后台归档**：
  - 在每次 `run` 结束后，异步评估数据库历史，若超过 30,000 tokens，将调用 AI 压缩成 9 段式快照，存入独立的 `compaction_summaries` 归档表。
  - 将所有归档前的冷消息状态标记为 `is_deleted = 1`。
  - 下次 Session 激活或重新加载（Hydration）时，系统默认忽略已标记删除的消息，直接读取最新的 9 段摘要快照并拼接最近的 10 条消息，从根本上消除了数据库读取与内存解析的性能瓶颈。
