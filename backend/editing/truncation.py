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
        f"\n\n[系统提示]「{tool_name}」的输出被模型单次长度限制截断，"
        f"{where}只写入了前 {written_chars} 个字符，内容不完整。\n"
        f"请不要重新发送整个文件（同样会再次被截断）。补全方式：\n"
        f"1) 用 edit_file 续写：取当前文件末尾一段唯一文本作为 old_string，"
        f"替换为「该段 + 剩余未写入的内容」，从断点处接着写；\n"
        f"2) 若文件本身过大，改为拆成多个较小文件，分别用 write_file 写入。"
    )
