# Edit 原语统一升级设计（Solution-Level）

> 目标：**综合 opencode / Claude Code / gsd-2 / openclaw 四家全部技术，做最大兼容**，落成一个比任何单一框架都更全、且更干净的统一原语。
> 配套对比见 [`EDIT-PRIMITIVE-COMPARISON.md`](EDIT-PRIMITIVE-COMPARISON.md)。

---

## 1. 设计原则

1. **替换永远精确，容错只在定位**——保留我们现有的 candidate 间接层优点：无论匹配多宽容，最终都拼接**原文真实字节**，绝不改写归一后的内容（gsd 在 fuzzy 时写归一文，丢原字节，我们要做得更好）。
2. **正交分解，矩阵替代平铺**——opencode 的 9 个 replacer 是「字符归一」和「结构容错」两件事的随意组合。把这两条轴拆开，得到一个**参数化引擎**，覆盖面是矩阵（含四家都没单独做过的组合），代码量却更小。
3. **越宽越险 → 唯一性是前提而非补丁**——每加一层容错都扩大「静默改错」面。唯一性必须按**等价类**判定，且是整个设计的承重墙。
4. **分层可交付**——不是一次性重写；Layer 0 先修缺陷+补字符归一（向后兼容），后续层叠加。

---

## 2. 关键洞察：两条正交轴

四家的容错本质是两条独立的轴：

| 轴 | 含义 | 各家技术 |
|---|---|---|
| **A. 字符归一** | 把*字符*规范化 | 行尾(CRLF/CR→LF)、BOM、弯引号、unicode 空格、unicode 破折号、转义符 |
| **B. 结构容错** | 把*空白/结构*放宽 | exact、line-trimmed、whitespace-collapse、indentation-flexible、block-anchor(首尾行锚)、context-aware(编辑距离) |

opencode 把两轴混在 9 个手写 replacer 里。**拆开后**：B 轴里「归一后做子串查找」的那些（line-trim、ws-collapse、indent-strip…）其实**只是 A 轴换了一组 transform**——它们能塌进**同一个引擎**。只有 block-anchor / levenshtein 这种「跨行带重排容忍」的需要独立算法。

> 结论：`exact / line-trim / ws-collapse / indent-flex / quote-norm / unicode-norm` 全部 = 同一个「归一+子串」引擎的不同参数；真正需要单独写的只有 1–2 个算法型匹配器。**9 个 replacer 收敛成 1 个引擎 + 2 个算法匹配器。**

---

## 3. 核心抽象：位置映射归一器（the enabling primitive）

要在归一文里定位、却拼回原文真实字节，需要一个**带反向位置映射**的归一器。这是整个设计的地基，gsd 没做到（它直接在归一文上操作）。

```python
@dataclass
class Normalized:
    text: str           # 归一后的字符串
    src:  list[int]     # src[i] = 归一文第 i 个字符来自原文的下标；len == len(text)+1（末尾哨兵）

def normalize(s: str, transforms) -> Normalized:
    """逐段消费原文：每个 transform 把 k 个原文字符 → m 个归一字符，
    所有 m 个归一字符的 src 都指向这段原文的起点。"""
    out, src, i = [], [], 0
    while i < len(s):
        consumed, emitted = apply_first(transforms, s, i)   # k → m
        for ch in emitted:
            out.append(ch); src.append(i)
        i += consumed
    src.append(len(s))     # 末尾哨兵，供 span 右端映射
    return Normalized("".join(out), src)
```

**反向映射**：归一文里匹配到 span `[a, b)` → 原文真实 span `[src[a], src[b])` → 拼接 `orig[:src[a]] + new + orig[src[b]:]`。**原字节零损耗。**

这套机器**统一表达**了：
- 字符归一（引号/破折号/unicode 空格，1→1）
- 空白折叠（一段空白 → 单空格，k→1，src 全指向段起点）
- 行内 trim（行首尾空白 run → 空，k→0）
- 缩进剥离（行首空白 → 空）

也就是说 B 轴里所有「子串型」策略都退化成「选一组 transform 跑这个归一器」。

> 行尾/BOM 是例外（改长度且全局），放 IO 边界处理（§5），不进归一器——否则 CRLF 的偏移会污染映射。

---

## 4. 统一定位算法 `locate()`

```python
# 严格 → 宽容，curated（非 35 种组合全跑），顺序即安全旋钮
TIERS = [
    Substr({}),                                   # exact
    Substr({trailing_ws_per_line}),               # 行尾空白
    Substr({quotes}),                             # 弯引号
    Substr({quotes, dashes, uspaces}),            # 全 unicode 归一（gsd 同款）
    Substr({line_trim}),                          # 逐行 trim
    Substr({ws_collapse}),                        # 连续空白折叠
    Substr({indent_strip}),                       # 缩进宽容
    Substr({quotes, dashes, uspaces, ws_collapse}),
    BlockAnchor(),                                # 首尾行锚 + 中间模糊（opencode）
    # Levenshtein(threshold)  ← 默认关，§10 风险
]

def locate(content, find, replace_all):
    for tier in TIERS:
        spans = tier.match(content, find)          # 已映射回原文坐标
        distinct = dedupe_by_bytes(content, spans) # 按真实字节去重 = 等价类
        if not distinct:
            continue
        if len(distinct) > 1:
            if replace_all:
                return distinct                    # 调用方全替换
            raise EditError(notUnique(len(distinct), tier))   # 富反馈：哪一层、命中几块
        return [distinct[0]]
    raise EditError(notFound(diagnostics(content, find)))     # 近邻 hint（§7）
```

**唯一性按等价类判**（修我们的 🔴 缺陷一，也强于所有四家）：收集该层产出的全部 span、按真实字节去重，`>1` 即 `不唯一`。opencode 只查 `index≠lastIndex`（字节重复），gsd 在归一文上 count，**无人查「同一层等价类里有几个不同块」**。我们做到。

---

## 5. IO 边界：行尾 / BOM（全局、可逆）

```python
async def edit_file(path, old, new, replace_all):
    raw = await read(path)
    bom, raw = strip_bom(raw)            # 记下 BOM
    eol = detect_eol(raw)                # 记下主行尾 CRLF/LF/CR
    content = to_lf(raw)                 # 整个操作在 LF 平面
    old, new = to_lf(old), to_lf(new)

    spans = locate(content, old, replace_all)
    result = splice(content, spans, new)

    result = restore_eol(result, eol)    # 写回保留用户原行尾（不静默改）
    await write(path, bom + result)
```

对齐 Claude Code（整文件 LF）+ gsd（detect+restore），但**行尾选择写回原样**，不制造 diff 噪音。

---

## 6. new_string 协调（typography reconciliation）

匹配越宽容，`new_string` 越可能与命中块的格式不一致。两项高价值协调：

| 协调 | 触发 | 动作 | 来源 |
|---|---|---|---|
| **缩进重对齐** | 经 `indent_strip` / `line_trim` 命中 | 把命中块的公共前导缩进重新施加到 `new_string` 各行 | 代码场景刚需（opencode IndentationFlexible 隐含） |
| **引号风格保留** | 经 `quotes` 命中（文件用弯引号，模型发直引号） | 把文件的弯引号风格回施到 `new_string` | Claude Code `preserveQuoteStyle` |

缩进重对齐是代码编辑的关键收益；引号保留可选。

---

## 7. 工具层：批量 + 幂等恢复 + 失配提示（openclaw 层）

匹配引擎之上的**工具层**能力，与匹配正交：

1. **批量编辑** `edits: [{old, new}, …]`——顺序应用，每条应用后**重新定位**（偏移已变）。openclaw 同款，省多次往返。
2. **幂等恢复**——某条 `old` 找不到时，若 `new` 已存在且 `old` 缺席 → 判为**已应用**，跳过而非报错。**保守**：仅当 `new` 恰好出现一次才认定（避免掩盖真错）。
3. **失配富提示**——`notFound` 时回吐命中失败处的**近邻上下文**（截断 ≤800 字）+ 指明用 `edit_file` 锚点续写。融合 openclaw 的 hint + 我们现有的 `build_completion_hint`。

---

## 8. 可选 Layer 3：hashline 锚点（独立工具，范式级）

gsd 的 `LINE#ID` 行哈希锚点是唯一的「换范式」能力——读文件时给每行打稳定哈希标签，模型按 ID 引用，**行移动也不丢锚**。它和文本匹配是两套世界，**作为独立 opt-in 工具** `edit_by_anchor` 提供，不混入 `locate()`。默认不开；大文件高频改场景再启用。

---

## 9. 能力溯源（每项技术吸收自谁）

| 能力 | opencode | Claude Code | gsd-2 | openclaw | 本设计落点 |
|---|:---:|:---:|:---:|:---:|---|
| 多层结构容错 | ✅(9) | (2) | (2) | — | §4 TIERS（引擎化） |
| 弯引号归一 + 排版保留 | escape | ✅ | ✅ | — | §3 + §6 |
| 激进 unicode 归一 | 部分 | — | ✅ | — | §3 transforms |
| 行尾/BOM | ✅ | ✅ | ✅ | ✅ | §5 IO 边界 |
| block-anchor | ✅ | — | — | — | §4 BlockAnchor |
| 编辑距离近似 | ✅ | — | — | — | §4（默认关） |
| 等价类唯一性 | 部分 | 部分 | 部分 | — | **§4（强于全部）** |
| 原字节零损映射 | — | 部分 | — | — | **§3（强于全部）** |
| 批量 edits | — | — | — | ✅ | §7 |
| 幂等恢复 | — | — | — | ✅ | §7 |
| 失配近邻 hint | — | — | — | ✅ | §7 |
| 行哈希锚点 | — | — | ✅ | — | §8（opt-in） |

> 本设计是四家的**并集**，且在「唯一性」「原字节映射」两项上做到**超集**。

---

## 10. 硬权衡（solution-level 必须点名）

1. **组合爆炸**：策略 × 归一 = 矩阵，但**不全跑**——§4 是 curated 的 ~9 级 ladder，按「strict→lenient」排序，顺序就是安全旋钮。
2. **越宽越险**：每多一层都放大静默改错面。**等价类唯一性（§4）是开启激进容错的硬前提**，不是事后补丁。先有它，才敢加 block-anchor / levenshtein。
3. **levenshtein 默认关**：编辑距离近似匹配命中率高但误伤也高，仅在显式 `fuzzy=true` 或低阈值下启用。
4. **幂等是双刃**：「new 已存在就跳过」会掩盖改错目标，故 §7 限定「new 恰好一次」才认定。
5. **协调的边界**：缩进重对齐对结构化代码有效，对混合缩进/制表符文件可能误施——经 `ws_collapse` 等更激进层命中时，禁用自动重对齐，转为报 hint 让模型确认。

---

## 11. 分层交付

| Layer | 内容 | 交付价值 | 兼容性 |
|---|---|---|---|
| **L0 地基** | 位置映射归一器(§3) + IO 行尾/BOM(§5) + 等价类唯一性(§4) + 引号/unicode 归一 | 修两个缺陷 + 补全员标配的字符归一 | 现有 3 replacer 平移为 3 个 tier，向后兼容 |
| **L1 增宽** | block-anchor + indent-flex + 缩进重对齐(§6) | 接住长块内部写歪、缩进漂移 | 纯增量，受 L0 唯一性保护 |
| **L2 工效** | 批量 edits + 幂等恢复 + 失配 hint(§7) | 减往返、抗重试、自恢复 | 工具层，匹配引擎不动 |
| **L3 范式(opt-in)** | hashline `edit_by_anchor` 独立工具(§8) | 大文件稳定锚定 | 独立工具，默认关 |

**落地顺序**：L0 一次成型（地基，含两缺陷修复）→ L1 → L2 → L3 视需要。每层按 `backend/CLAUDE.md` 节奏：写完→只跑 `editing/tests/test_edit.py`；L0 因动核心，先写失配/唯一性失败测试钉死再改。

---

*设计日期：2026-06-14 · 范围：四框架技术并集 + 超集（唯一性 / 原字节映射）*
