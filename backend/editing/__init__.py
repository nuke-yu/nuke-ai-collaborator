"""editing/ — 解耦的文件编辑子系统（纯逻辑，不碰文件系统、不依赖执行器/工具注册）。

对外公开：
- apply_replacement / EditError —— 精确字符串替换原语（edit_file 工具的内核）
- build_completion_hint        —— 写入被截断时给模型的续写指引文案

文件系统读写由 workspace/ 负责，工具注册由 executors 负责；本包只做纯字符串变换，
因此可以脱离 DB/执行器单独跑测试。
"""
from editing.edit import apply_replacement, apply_batch, EditError
from editing.truncation import build_completion_hint
from editing.eol import strip_bom, detect_eol, to_lf, restore_eol
from editing.recovery import idempotent_skip, mismatch_hint

__all__ = [
    "apply_replacement", "apply_batch", "EditError", "build_completion_hint",
    "strip_bom", "detect_eol", "to_lf", "restore_eol",
    "idempotent_skip", "mismatch_hint",
]
