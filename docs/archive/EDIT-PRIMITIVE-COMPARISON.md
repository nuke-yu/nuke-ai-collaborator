# Edit 原语横向对比（edit_file 匹配/替换）

> 对比对象：**Nuke（本项目）** · **Claude Code**(haha 泄露源码) · **opencode** · **gsd-2** · **openclaw**
> 结论全部来自一手源码阅读，非文档转述。聚焦单一问题：**模型发 `(old_string → new_string)`，系统如何在文件里定位并安全替换**。

---

## 0. 这个原语为什么重要

`edit_file` 是 agent 改文件的**主原语**，替代「让模型重吐整个文件」——后者一撞 `max_tokens` 就物理截断。模型只发 `(old_string, new_string)`，系统负责在文件里**定位** `old_string` 再**替换**。

难点在于：模型给的 `old_string` 几乎总和文件实际字节有细微出入（缩进、空白、弯引号、行尾符——它是凭上下文重建，不是逐字节复制）。所以成熟实现无一例外都在「定位」这步做**容错**，同时在「替换」这步保**精确**。各家的差异，本质就是**容错策略的哲学差异**。

我们自己的实现：
- 匹配级联 [`backend/editing/replacers.py`](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/editing/replacers.py)
- 替换主函数 [`backend/editing/edit.py`](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/editing/edit.py) `apply_replacement`
- 工具层 [`backend/workspace/__init__.py`](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/workspace/__init__.py) `edit_file`（读→调→写，无字符归一）

---

## 1. 核心范式与匹配策略

| 维度 | **Nuke（我们）** | **Claude Code**(haha) | **opencode** | **gsd-2** | **openclaw** |
|---|---|---|---|---|---|
| 核心范式 | 文本宽容级联 | 精确 + 字符归一 | 文本宽容级联 | 双工具：fuzzy + **行哈希锚点** | 引擎复用 + **host 恢复层** |
| 引擎血统 | 自研（port 自 opencode） | 自研 | 自研 | `@gsd/pi-agent-core` + Rust native | `@earendil-works/pi-agent-core`（与 gsd 同源） |
| 匹配层数 | **3** | 2 | **9** | 2(fuzzy) + 锚点 | 1(精确) + 恢复 |
| 具体层（严格→宽容） | simple → line-trimmed → ws-normalized | exact → 弯引号归一 | simple / line-trimmed / **block-anchor** / ws-norm / **indent-flex** / **escape-norm** / trimmed-boundary / **context-aware(levenshtein)** / multi-occurrence | exact → fuzzy-normalize | exact（仅 LF 归一） |
| 结构/语义能力 | ❌ | ❌ | ❌（context-aware 用编辑距离做近似） | ✅ **hashline 行锚点**（行移动仍稳定）；doc 称含 tree-sitter astEdit | ❌ |

**源码锚点**
- Claude Code：`src/tools/FileEditTool/utils.ts` `normalizeQuotes`(L31) / `findActualString`(L73) / `preserveQuoteStyle`；`FileEditTool.ts` 计数+notUnique(L331-336)
- opencode：`packages/opencode/src/tool/edit.ts` 九个 Replacer + `replace()` 驱动(L674)
- gsd-2：`packages/pi-coding-agent/src/core/tools/edit-diff.ts` `fuzzyFindText`(L72) / `normalizeForFuzzyMatch`(L37)；`hashline-edit.ts`（行哈希锚点）
- openclaw：`src/agents/pi-tools.host-edit.ts` `wrapEditToolWithRecovery`(L150) / `EDIT_MISMATCH_MESSAGE`

---

## 2. 归一化、安全与工程形态

| 维度 | **Nuke** | **Claude Code** | **opencode** | **gsd-2** | **openclaw** |
|---|---|---|---|---|---|
| 行尾归一(CRLF/CR→LF) | ❌ **无** | ✅ | ✅(detect+restore) | ✅ | ✅ |
| BOM 处理 | ❌ | ✅ | ✅(split) | ✅(strip+restore) | —(仅 LF) |
| 弯引号归一 | ❌ | ✅ 双向 + `preserveQuoteStyle` 保排版 | ✅(escape-norm) | ✅ | ❌ |
| Unicode 空格/破折号 | ❌ | ❌ | 部分 | ✅ **最激进**（unicode 空格 / `—‑‒–—−`） | ❌ |
| 替换安全性 | ✅ candidate 拼真实子串 | ✅ 取真实子串 | ✅ indexOf 真实子串 | ✅ exact 用原文 / fuzzy 用归一文 | ✅ exact |
| 唯一性保证 | ⚠️ **精确字节** count（等价类盲区，见 §4） | count>1 报错 | `index≠lastIndex→continue`→notUnique | fuzzy 归一文计数 | 引擎级 |
| 未命中反馈 | notFound / notUnique 两分 | 基础 notFound | **notFound vs notUnique 富文本** | "must match exactly…" | ✅ **回吐当前内容 hint(≤800字)** |
| 编辑粒度 | 单条 + replace_all | 单条 + replace_all | 单条 + replaceAll | 单条 | ✅ **batch `edits[]`** |
| 幂等/恢复 | ❌ | ❌ | ❌ | ❌ | ✅ **检测已应用**（newText 已存在则视为已改） |
| 语言/依赖 | Python，零依赖 | TS | TS/Bun | TS + **Rust native** | TS |

---

## 3. 三种哲学

四家表面上五花八门，本质归三类：

1. **「堆宽容层」** — opencode(9) / 我们(3) / Claude Code(2)。同一思路，只差**回退层数**。我们是 opencode 的最小子集。
2. **「修字符漂移」** — gsd fuzzy / Claude Code 引号。不加层，而是把弯引号、CRLF、unicode 空格/破折号**归一**掉。便宜，一次干掉一整类失配。
3. **「换范式」** — gsd **hashline 行锚点** / openclaw **batch + 恢复 + 幂等**。**不再赌「文本匹配必中」**：要么用稳定行哈希 ID 锚定（行移动也不丢），要么匹配失败时优雅恢复（回吐内容 hint / 检测已应用）。最高级的一档。

> 关键观察：哲学①是力气活，边际递减；哲学②是性价比之王；哲学③是真正的代差，但投入也最大。

---

## 4. 我们实现的两个真实缺陷

### 🔴 缺陷一（false-positive，会改错地方）——唯一性是「精确字节」而非「等价类」

[`edit.py`](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/editing/edit.py) 的 `content.count(match)` 数的是**被选中 candidate 的逐字节重复**，不是「有多少块在该 replacer 的等价意义下都匹配」。

```
if x:
    return a        # 块A：缩进 4
if x:
        return a    # 块B：缩进 8
```

模型发 `find = "if x:\nreturn a"`：simple 不中 → `line_trimmed` 扫到**块A**先 yield → `count(块A)==1` → **静默改块A**。但**块B 同样 trim-匹配**——意图歧义，系统却没报 `不唯一`。这是 edit 原语最坏的失败模式（改错位置 > 改不了）。

**修法**：唯一性改成**等价类感知**——replacer 报告它在自身等价意义下命中几块，>1 即抛 `不唯一`。这是任何「再加宽容层」的前提，否则越宽越危险。

### 🟠 缺陷二（false-negative，安全）——`whitespace_normalized_replacer` 尾换行 miscount

`line_trimmed_replacer` 显式处理了 `find` 末尾空行（[`replacers.py`](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/editing/replacers.py) L25-26），但 `whitespace_normalized_replacer` 没有：窗口 `n = len(find.split("\n"))` 在 `find` 以 `\n` 结尾时多算一行，窗口对不齐 → 第三阶段对带尾换行的 find 几乎永不中。后果仅是少一层兜底（模型重试），不致命。

---

## 5. 演进优先级（solution-level）

在「≤30 人、内部、主要 Python/Markdown、DeepSeek/Claude」这个档位，**别追 opencode 九重 / gsd AST**（native-Rust 成本曲线，错配本栈）。按序：

| 优先级 | 动作 | 哲学 | 理由 |
|---|---|---|---|
| **P0** | 补**字符归一**（CRLF/BOM/弯引号） | ② | 唯一一项**全员都有、我们没有**的能力；~20 行干掉一整类失配 |
| **P1** | 唯一性**等价类**修复（缺陷一） | — | correctness；加宽容层的前提 |
| P2 | `whitespace_normalized` 尾换行修复（缺陷二） | — | 顺手 |
| P3 | 加 **block-anchor** 第 4 层 | ① | opencode 性价比最高那层；**必须在 P1 之后** |
| 不做 | hashline / AST / Rust Myers | ③ | 范式级投入，不匹配规模与栈 |

**一句话**：我们缺的不是「更宽」，是「更稳」——先补字符归一（P0）和唯一性（P1），比加六个 replacer 都值。

> **后续（2026-06-14）**：目标升级为**工业级完整度**后，上表「先稳后宽」的取舍被超越——
> P0–P3 + opencode 全部文本层 replacer（escape / indentation-flexible / trimmed-boundary /
> context-aware）+ 位置映射归一器 + §6 引号风格保留**已全部落地**。仅 hashline / AST / Rust
> Myers 三个**范式级**项留作架构外。完整状态见 [`EDIT-PRIMITIVE-UPGRADE-DESIGN.md`](EDIT-PRIMITIVE-UPGRADE-DESIGN.md) §12。

---

*文档日期：2026-06-14 · 范围：5 项目 edit 原语一手源码对比*
