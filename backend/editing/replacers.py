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
    content_lines = content.split("\n")
    n = len(find.split("\n"))
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


# 顺序即优先级：严格 → 宽容。
REPLACERS = [
    simple_replacer,
    line_trimmed_replacer,
    whitespace_normalized_replacer,
]
