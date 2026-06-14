"""editing/replacers.py — 在文件内容里定位「待替换文本」的匹配器级联（纯函数）。

模型给的 old_string 常和文件里的实际文本有细微出入（行首尾空白、缩进、内部空白、弯引号、
转义写法…）。每个 replacer 接收 (content, find)，**产出 content 里真实存在的、与 find 等价
的子串**（candidate）——调用方再用这个 candidate 去做精确替换（改写永远精确，宽容只在定位）。

排在前面的越严格、越优先；越往后越宽容。第一个产出可用 candidate 的 replacer 胜出。
字符/转义层经位置映射归一器（normalize.py）拼回原文真实字节；结构层按行窗口产出真实块。
port 自 opencode 九重 replacer 思路，并补齐位置映射 + 等价类唯一性（见 edit.py）。
"""
import difflib
import re

from editing.normalize import iter_spans, CHAR_TRANSFORMS, ESCAPE_TRANSFORMS

Replacer = "callable(content: str, find: str) -> Iterator[str]"

_CONTEXT_THRESHOLD = 0.9     # context_aware 模糊匹配的最低相似度（保守）


def _line_starts(content_lines):
    """每行在原文中的起始下标（含行间 \\n）。"""
    starts, pos = [], 0
    for ln in content_lines:
        starts.append(pos)
        pos += len(ln) + 1
    return starts


def _strip_trailing_empty(find_lines):
    return find_lines[:-1] if find_lines and find_lines[-1] == "" else find_lines


def _remove_common_indent(lines):
    """去掉所有非空行的公共最小前导缩进，保留相对缩进结构。"""
    widths = [len(ln) - len(ln.lstrip(" \t")) for ln in lines if ln.strip()]
    if not widths:
        return list(lines)
    m = min(widths)
    return [ln[m:] if ln.strip() else ln for ln in lines]


def simple_replacer(content: str, find: str):
    """精确子串：find 原样出现在 content 里。"""
    if find and find in content:
        yield find


def char_normalized_replacer(content: str, find: str):
    """字符归一后子串匹配：弯引号/unicode 空格/破折号归一成 ASCII 等价物再找。
    经位置映射归一器拼回 content 里的**原始真实子串**（保留文件原本的弯引号等排版）。"""
    if not find:
        return
    for a, b in iter_spans(content, find, CHAR_TRANSFORMS):
        yield content[a:b]


def escape_normalized_replacer(content: str, find: str):
    r"""转义归一后子串匹配：把 `\n`/`\t`/`\"` 等反斜杠转义解成实际字符再找——接住模型把
    多行/特殊字符写成转义串的情形。转义改长度（2→1），靠归一器的 src 映射拼回原字节。"""
    if not find:
        return
    for a, b in iter_spans(content, find, ESCAPE_TRANSFORMS):
        yield content[a:b]


def indentation_flexible_replacer(content: str, find: str):
    """忽略**公共**基线缩进、但保留**相对**缩进结构的逐行匹配——接住「整块被改了基准缩进
    层级，但内部相对缩进对」的情形。比 line_trimmed 严格（后者连相对缩进也抹平）。"""
    find_lines = _strip_trailing_empty(find.split("\n"))
    if not find_lines:
        return
    nf = _remove_common_indent(find_lines)
    content_lines = content.split("\n")
    starts = _line_starts(content_lines)
    n = len(find_lines)
    for i in range(len(content_lines) - n + 1):
        if _remove_common_indent(content_lines[i:i + n]) == nf:
            last = i + n - 1
            yield content[starts[i]: starts[last] + len(content_lines[last])]


def line_trimmed_replacer(content: str, find: str):
    """逐行匹配，但忽略每行的首尾空白差异。产出 content 里对应的真实块。"""
    content_lines = content.split("\n")
    find_lines = _strip_trailing_empty(find.split("\n"))
    if not find_lines:
        return
    starts = _line_starts(content_lines)
    n = len(find_lines)
    for i in range(len(content_lines) - n + 1):
        if all(content_lines[i + j].strip() == find_lines[j].strip() for j in range(n)):
            last = i + n - 1
            yield content[starts[i]: starts[last] + len(content_lines[last])]


def trimmed_boundary_replacer(content: str, find: str):
    """剥掉 find **首尾的整空行**后再逐行 trim 匹配——接住模型在块前后多/少了空行的情形。"""
    find_lines = find.split("\n")
    while find_lines and not find_lines[-1].strip():
        find_lines.pop()
    while find_lines and not find_lines[0].strip():
        find_lines.pop(0)
    if not find_lines:
        return
    content_lines = content.split("\n")
    starts = _line_starts(content_lines)
    n = len(find_lines)
    for i in range(len(content_lines) - n + 1):
        if all(content_lines[i + j].strip() == find_lines[j].strip() for j in range(n)):
            last = i + n - 1
            yield content[starts[i]: starts[last] + len(content_lines[last])]


def whitespace_normalized_replacer(content: str, find: str):
    """把连续空白归一成单个空格后整块比较，命中则产出 content 里的真实块。"""
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    target = norm(find)
    if not target:
        return
    find_lines = _strip_trailing_empty(find.split("\n"))
    n = len(find_lines)
    content_lines = content.split("\n")
    if n <= 0 or n > len(content_lines):
        return
    for i in range(len(content_lines) - n + 1):
        block = "\n".join(content_lines[i:i + n])
        if norm(block) == target:
            yield block


def context_aware_replacer(content: str, find: str):
    """模糊匹配（≥2 行）：逐行 trim 后按相似度（difflib）找 ≥阈值 的等行数窗口——接住内部
    个别行被模型写歪、但整体高度相似的情形。最危险层之一，阈值保守 + 靠等价类唯一性兜底。"""
    find_lines = _strip_trailing_empty(find.split("\n"))
    if len(find_lines) < 2:          # 单行不做模糊（收益/风险不划算）
        return
    target = "\n".join(ln.strip() for ln in find_lines)
    content_lines = content.split("\n")
    starts = _line_starts(content_lines)
    n = len(find_lines)
    for i in range(len(content_lines) - n + 1):
        cand = "\n".join(ln.strip() for ln in content_lines[i:i + n])
        if difflib.SequenceMatcher(None, target, cand).ratio() >= _CONTEXT_THRESHOLD:
            last = i + n - 1
            yield content[starts[i]: starts[last] + len(content_lines[last])]


def block_anchor_replacer(content: str, find: str):
    """首尾行锚定（find ≥3 行）：只要 find 的首行与尾行（去空白）能在 content 里框定一段块，
    中间行**不比对、行数也可不同**。接住「长块内部被模型写歪、但首尾对」的情况。

    最宽容、最危险的一层，放在最后；安全完全依赖等价类唯一性——首/尾若框定出多段不同块，
    apply_replacement 会因「不唯一」拒绝，不会乱改。"""
    find_lines = _strip_trailing_empty(find.split("\n"))
    if len(find_lines) < 3:          # 不足 3 行：首尾即全部，交给前面更严格的层
        return
    first, last = find_lines[0].strip(), find_lines[-1].strip()
    if not first or not last:
        return
    content_lines = content.split("\n")
    starts = _line_starts(content_lines)
    for i in range(len(content_lines)):
        if content_lines[i].strip() != first:
            continue
        for j in range(i + 1, len(content_lines)):   # 最近的尾行锚
            if content_lines[j].strip() == last:
                yield content[starts[i]: starts[j] + len(content_lines[j])]
                break


# 顺序即优先级：严格 → 宽容。字符层（char/escape，结构不变、拼回原字节）紧随精确；
# 再到保相对缩进 → 抹平缩进 → 剥边缘空行 → 折叠空白 → 模糊相似 → 首尾锚（最宽容垫底）。
REPLACERS = [
    simple_replacer,
    char_normalized_replacer,
    escape_normalized_replacer,
    indentation_flexible_replacer,
    line_trimmed_replacer,
    trimmed_boundary_replacer,
    whitespace_normalized_replacer,
    context_aware_replacer,
    block_anchor_replacer,
]
