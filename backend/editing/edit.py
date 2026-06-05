"""editing/edit.py — 精确字符串替换的核心逻辑（纯函数，无 IO）。

这是文件编辑子系统的主原语：给定文件内容 + (old_string → new_string)，返回替换后的新
内容；定位不到 / 不唯一 时抛 EditError。匹配走 replacers.REPLACERS 级联，对模型给的
old_string 的细微空白/缩进偏差有容错。

工具层（workspace_tools._handle_edit_file）只负责：读文件 → 调本函数 → 写回，
本模块不碰文件系统，便于独立测试。
"""
from editing.replacers import REPLACERS


class EditError(Exception):
    """替换无法安全完成（未找到 / 不唯一 / 参数非法）。"""


def _find_match(content: str, old_string: str) -> str | None:
    """按 replacer 优先级找出 content 里真实存在、与 old_string 等价的子串。"""
    for replacer in REPLACERS:
        for candidate in replacer(content, old_string):
            if candidate and candidate in content:
                return candidate
    return None


def apply_replacement(content: str, old_string: str, new_string: str,
                      replace_all: bool = False) -> str:
    """把 content 中的 old_string 替换为 new_string，返回新内容。

    - old_string 为空 → EditError（用 write_file 建新文件，别用 edit）。
    - old_string == new_string → EditError（无意义替换）。
    - 定位不到 → EditError。
    - 非 replace_all 且匹配到多处 → EditError（要求加上下文或用 replace_all）。
    """
    if old_string == "":
        raise EditError("old_string 不能为空（新建文件请用 write_file）")
    if old_string == new_string:
        raise EditError("old_string 与 new_string 相同，无需替换")

    match = _find_match(content, old_string)
    if match is None:
        raise EditError("old_string 在文件中未找到")

    occurrences = content.count(match)
    if not replace_all and occurrences > 1:
        raise EditError(
            f"old_string 在文件中出现 {occurrences} 次、不唯一；"
            f"请加入更多上下文使其唯一，或用 replace_all=true 全部替换"
        )

    if replace_all:
        return content.replace(match, new_string)
    return content.replace(match, new_string, 1)
