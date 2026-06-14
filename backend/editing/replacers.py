"""editing/replacers.py — 在文件内容里定位「待替换文本」的匹配器级联（纯函数）。

模型给的 old_string 常和文件里的实际文本有细微出入（行首尾空白、缩进、内部空白多了
少了）。每个 replacer 接收 (content, find)，**产出 content 里真实存在的、与 find 等价的
子串**（candidate）——调用方再用这个 candidate 去做精确替换。

排在前面的越严格、越优先；越往后越宽容。第一个产出可用 candidate 的 replacer 胜出。
无任何 IO、无第三方依赖，可单独测试。port 自 opencode 的 edit replacer 思路（精简版）。
"""
import re

Replacer = "callable(content: str, find: str) -> Iterator[str]"


def simple_replacer(content: str, find: str):
    """精确子串：find 原样出现在 content 里。"""
    if find and find in content:
        yield find


# 长度保持的字符归一表（1 字符 → 1 字符）：弯引号、各类破折号、unicode 空格。
# 因为 1→1，归一文与原文逐位对应，匹配到的偏移可直接切回原文真实字节。
# 行尾(CRLF)/BOM 改长度，不在此处——由 IO 层（workspace.edit_file）统一处理。
_CHAR_CANON = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",   # 单弯引号
    "“": '"', "”": '"', "„": '"', "‟": '"',   # 双弯引号
    "‐": "-", "‑": "-", "‒": "-", "–": "-",   # 连字符/短破折
    "—": "-", "―": "-", "−": "-",                   # 长破折/减号
    " ": " ", " ": " ", " ": " ", " ": " ",   # 各类 unicode 空格
    " ": " ", " ": " ", " ": " ", "　": " ",
}


def _char_canon(s: str) -> str:
    return "".join(_CHAR_CANON.get(ch, ch) for ch in s)


def char_normalized_replacer(content: str, find: str):
    """字符归一后子串匹配：把弯引号/unicode 空格/破折号归一成 ASCII 等价物再找。
    归一是 1→1 长度保持，故偏移不变，yield 的是 content 里的**原始真实子串**（保留
    文件原本的弯引号等排版）。"""
    if not find:
        return
    nc = _char_canon(content)
    nf = _char_canon(find)
    if nc == content and nf == find:
        return  # 没有可归一字符；精确情形已由 simple_replacer 覆盖
    start = 0
    while True:
        idx = nc.find(nf, start)
        if idx == -1:
            break
        yield content[idx: idx + len(find)]   # len(nf)==len(find)，偏移对齐原文
        start = idx + 1


def line_trimmed_replacer(content: str, find: str):
    """逐行匹配，但忽略每行的首尾空白差异。产出 content 里对应的真实块。"""
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    if find_lines and find_lines[-1] == "":
        find_lines = find_lines[:-1]
    if not find_lines:
        return

    # 预计算每行在 content 中的起始下标（含行间的 \n）
    starts = []
    pos = 0
    for ln in content_lines:
        starts.append(pos)
        pos += len(ln) + 1  # +1 = 换行符

    n = len(find_lines)
    for i in range(len(content_lines) - n + 1):
        if all(content_lines[i + j].strip() == find_lines[j].strip() for j in range(n)):
            start = starts[i]
            last = i + n - 1
            end = starts[last] + len(content_lines[last])
            yield content[start:end]


def whitespace_normalized_replacer(content: str, find: str):
    """把连续空白归一成单个空格后整块比较，命中则产出 content 里的真实块。"""
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    target = norm(find)
    if not target:
        return
    # 与 line_trimmed 一致：算窗口大小前先剥掉 find 尾部空行，否则带尾 \n 的 find
    # 会让 n 多算一行、窗口对不齐，第三阶段对其几乎永不命中。
    find_lines = find.split("\n")
    if find_lines and find_lines[-1] == "":
        find_lines = find_lines[:-1]
    n = len(find_lines)
    content_lines = content.split("\n")
    if n <= 0 or n > len(content_lines):
        return
    starts = []
    pos = 0
    for ln in content_lines:
        starts.append(pos)
        pos += len(ln) + 1
    for i in range(len(content_lines) - n + 1):
        block = "\n".join(content_lines[i:i + n])
        if norm(block) == target:
            yield block


def block_anchor_replacer(content: str, find: str):
    """首尾行锚定（find ≥3 行）：只要 find 的首行与尾行（去空白）能在 content 里框定
    一段块，中间行**不比对、行数也可不同**。接住「长块内部被模型写歪、但首尾对」的情况。

    最宽容、最危险的一层，放在最后；安全完全依赖 L0 的等价类唯一性——首/尾若框定出
    多段不同块，apply_replacement 会因「不唯一」拒绝，不会乱改。"""
    find_lines = find.split("\n")
    if find_lines and find_lines[-1] == "":
        find_lines = find_lines[:-1]
    if len(find_lines) < 3:          # 不足 3 行：首尾即全部，交给前面更严格的层
        return
    first, last = find_lines[0].strip(), find_lines[-1].strip()
    if not first or not last:
        return
    content_lines = content.split("\n")
    starts, pos = [], 0
    for ln in content_lines:
        starts.append(pos)
        pos += len(ln) + 1
    for i in range(len(content_lines)):
        if content_lines[i].strip() != first:
            continue
        for j in range(i + 1, len(content_lines)):   # 最近的尾行锚
            if content_lines[j].strip() == last:
                yield content[starts[i]: starts[j] + len(content_lines[j])]
                break


# 顺序即优先级：严格 → 宽容。char_normalized 紧随精确之后——它只做 1→1 字符替换、
# 不放宽空白结构，风险低，应优先于行级 trim/折叠。block_anchor 最宽容，垫底。
REPLACERS = [
    simple_replacer,
    char_normalized_replacer,
    line_trimmed_replacer,
    whitespace_normalized_replacer,
    block_anchor_replacer,
]
