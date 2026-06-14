"""editing/edit.py — 精确字符串替换的核心逻辑（纯函数，无 IO）。

这是文件编辑子系统的主原语：给定文件内容 + (old_string → new_string)，返回替换后的新
内容；定位不到 / 不唯一 时抛 EditError。匹配走 replacers.REPLACERS 级联，对模型给的
old_string 的细微空白/缩进偏差有容错。

工具层（workspace_tools._handle_edit_file）只负责：读文件 → 调本函数 → 写回，
本模块不碰文件系统，便于独立测试。
"""
from editing.replacers import (
    REPLACERS, line_trimmed_replacer, block_anchor_replacer,
)


class EditError(Exception):
    """替换无法安全完成（未找到 / 不唯一 / 参数非法）。"""


# 经这些层命中时，old_string 丢了缩进信息 → new_string 须按命中块的公共缩进重对齐，
# 否则多行块会被拍平（de-indent）。精确/字符归一/空白折叠层不重对齐：
# simple/char_norm 的缩进是模型有意给的；ws_normalized 折叠了内部空白、缩进语义已不清。
_RECONCILE_INDENT = {line_trimmed_replacer, block_anchor_replacer}


def _equivalence_class(content: str, old_string: str):
    """取**第一个有命中的** replacer，返回 (replacer, 全部不同真实子串)。
    无命中返回 (None, [])。

    关键：唯一性按「该层等价类」判，而非精确字节 count——line-trimmed 等宽容层会
    yield 多个字节不同但等价的块，必须全部收集，否则会静默改错第一块。
    """
    for replacer in REPLACERS:
        distinct: list[str] = []
        seen: set[str] = set()
        for cand in replacer(content, old_string):
            if cand and cand in content and cand not in seen:
                seen.add(cand)
                distinct.append(cand)
        if distinct:
            return replacer, distinct
    return None, []


def _common_indent(s: str) -> int:
    """块内非空行的最小前导缩进宽度（空格/制表各记 1）。"""
    widths = [len(ln) - len(ln.lstrip(" \t")) for ln in s.split("\n") if ln.strip()]
    return min(widths) if widths else 0


def _reindent(text: str, delta: int) -> str:
    """整体平移 text 各非空行的前导缩进 delta 列（正=加、负=减，最多减到内容起点）。"""
    if delta == 0:
        return text
    out = []
    for ln in text.split("\n"):
        if not ln.strip():
            out.append(ln)
        elif delta > 0:
            out.append(" " * delta + ln)
        else:
            k = 0
            while k < len(ln) and k < -delta and ln[k] in " \t":
                k += 1
            out.append(ln[k:])
    return "\n".join(out)


def apply_replacement(content: str, old_string: str, new_string: str,
                      replace_all: bool = False) -> str:
    """把 content 中的 old_string 替换为 new_string，返回新内容。

    - old_string 为空 → EditError（用 write_file 建新文件，别用 edit）。
    - old_string == new_string → EditError（无意义替换）。
    - 定位不到 → EditError。
    - 非 replace_all 且命中等价类含多处 → EditError（要求加上下文或用 replace_all）。
    """
    if old_string == "":
        raise EditError("old_string 不能为空（新建文件请用 write_file）")
    if old_string == new_string:
        raise EditError("old_string 与 new_string 相同，无需替换")

    replacer, matches = _equivalence_class(content, old_string)
    if not matches:
        raise EditError("old_string 在文件中未找到")

    # 总命中处数 = 等价类内每个不同块的字节出现次数之和。
    total = sum(content.count(m) for m in matches)
    if not replace_all and total > 1:
        raise EditError(
            f"old_string 在文件中匹配到 {total} 处（{len(matches)} 个等价块）、不唯一；"
            f"请加入更多上下文使其唯一，或用 replace_all=true 全部替换"
        )

    reconcile = replacer in _RECONCILE_INDENT
    old_base = _common_indent(old_string) if reconcile else 0

    def _repl_for(match: str) -> str:
        # 按「命中块比 old_string 多出的公共缩进」平移 new_string，保住块缩进。
        return _reindent(new_string, _common_indent(match) - old_base) if reconcile else new_string

    if replace_all:
        out = content
        for m in matches:
            out = out.replace(m, _repl_for(m))
        return out
    return content.replace(matches[0], _repl_for(matches[0]), 1)
