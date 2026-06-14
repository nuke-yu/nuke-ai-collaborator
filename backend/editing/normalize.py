"""editing/normalize.py — 位置映射归一器（纯逻辑，无 IO）。

匹配的核心难题：在「归一后的文本」里定位，却要把改动拼回**原文真实字节**（保留弯引号、
转义写法等原貌）。本模块用一个带反向位置映射的归一器解决：

    normalize(s, transforms) → Normalized(text, src)
        text: 归一后的字符串
        src : src[i] = 归一文第 i 个字符来自原文的下标（末尾多一个哨兵 = len(s)）

于是归一文里匹配到的 span [a, b) 可经 src 映射回原文 span [src[a], src[b])，再从原文切片。
这让「改长度的归一」（如 `\\n` 两字符 → 1 个换行）也能精确拼回原字节——gsd 在归一文上直接
操作、丢了原字节，我们靠 src 做到零损耗。

transform 协议：callable(s, i) -> (consumed, emitted) | None
  命中则消费原文 consumed 个字符、产出归一串 emitted；不命中返回 None。所有 emitted 字符
  的 src 都指向这段原文的起点 i。
"""
from dataclasses import dataclass


@dataclass
class Normalized:
    text: str
    src: list[int]      # len == len(text) + 1（末尾哨兵）


def normalize(s: str, transforms) -> Normalized:
    out: list[str] = []
    src: list[int] = []
    i, n = 0, len(s)
    while i < n:
        consumed, emitted = 1, None
        for t in transforms:
            r = t(s, i)
            if r is not None:
                consumed, emitted = r
                break
        if emitted is None:
            emitted = s[i]
        for ch in emitted:
            out.append(ch)
            src.append(i)
        i += consumed
    src.append(n)
    return Normalized("".join(out), src)


def iter_spans(content: str, find: str, transforms):
    """产出 content 里每一处与 find 等价（在 transforms 归一下）的**原文** span (start, end)。"""
    nf = normalize(find, transforms).text
    if not nf:
        return
    nc = normalize(content, transforms)
    L = len(nf)
    start = 0
    while True:
        idx = nc.text.find(nf, start)
        if idx == -1:
            break
        yield nc.src[idx], nc.src[idx + L]
        start = idx + 1


# --------------------------------------------------------------------------- #
# transform 原语
# --------------------------------------------------------------------------- #
_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
}
_DASHES = {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-"}
_USPACES = {
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", "　": " ",
}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\", "`": "`", "0": "\0"}


def t_quotes(s, i):
    c = s[i]
    return (1, _QUOTES[c]) if c in _QUOTES else None


def t_dashes(s, i):
    c = s[i]
    return (1, "-") if c in _DASHES else None


def t_uspace(s, i):
    c = s[i]
    return (1, " ") if c in _USPACES else None


def t_unescape(s, i):
    """反斜杠转义 → 实际字符（`\\n` → 换行 等），长度 2→1。"""
    if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in _ESCAPES:
        return (2, _ESCAPES[s[i + 1]])
    return None


CHAR_TRANSFORMS = (t_quotes, t_dashes, t_uspace)     # 1→1，长度保持
ESCAPE_TRANSFORMS = (t_unescape,)                    # 2→1，改长度
