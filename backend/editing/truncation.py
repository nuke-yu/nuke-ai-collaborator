"""editing/truncation.py — 写文件被单次输出长度截断时给模型的续写指引（纯文本生成）。

旧实现把这段文案硬编码在 tool_executor 里，还引用了一个不存在的 replace_file_content
工具。这里集中产出符合真实工具集的、可执行的指引：用 edit_file 从断点续写，或拆文件。
"""


def build_completion_hint(tool_name: str, path: str, written_chars: int) -> str:
    """文件被截断写入后，追加到工具结果里的续写指引。

    tool_name    触发截断的写工具名（如 write_file）
    path         被写入的文件路径（可能为空）
    written_chars 实际写入磁盘的字符数（= 模型已生成的前缀长度）
    """
    where = f"文件「{path}」" if path else "该文件"
    return (
        f"\n\n[系统提示] {where}只写入了前 {written_chars} 字符不完整，"
        f"请用 edit_file 以末尾一段为锚点补全剩余内容，"
        f"或把超大文件拆成多个较小文件分别写——不要重发整文件"
    )
