"""editing/eol.py — 行尾 / BOM 归一（纯字符串变换，无 IO）。

edit 匹配统一在 LF 平面进行：IO 层（workspace.edit_file）读入时用 strip_bom + to_lf
把内容降到「无 BOM 的 LF」，匹配/替换完再用 restore_eol 还原用户原本的行尾、补回 BOM。

为什么不放进 replacers：CRLF→LF / BOM 剥离会**改变长度**，一旦进入匹配器就会污染
「归一偏移 → 原文真实子串」的映射（char_normalized 能拼回原字节正是因为它 1→1）。
所以改长度的归一只在 IO 边界做一次，全程整文件处理，最安全。
"""

_BOM = "﻿"


def strip_bom(s: str) -> tuple[str, str]:
    """返回 (bom, 去 BOM 文本)；bom 为 '' 或 '\\ufeff'。"""
    return (_BOM, s[1:]) if s.startswith(_BOM) else ("", s)


def detect_eol(s: str) -> str:
    """探测主行尾，返回 '\\r\\n' / '\\r' / '\\n'（无明显 CRLF/CR 时默认 LF）。"""
    crlf = s.count("\r\n")
    cr = s.count("\r") - crlf
    lf = s.count("\n") - crlf
    if crlf and crlf >= lf and crlf >= cr:
        return "\r\n"
    if cr and cr > lf:
        return "\r"
    return "\n"


def to_lf(s: str) -> str:
    """CRLF / CR → LF。"""
    return s.replace("\r\n", "\n").replace("\r", "\n")


def restore_eol(s: str, eol: str) -> str:
    """把 LF 文本还原成原行尾；eol == '\\n' 时原样返回。"""
    return s if eol == "\n" else s.replace("\n", eol)
