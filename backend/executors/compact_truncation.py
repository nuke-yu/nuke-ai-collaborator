"""Workspace-backed truncation for oversized tool results and user messages."""
from __future__ import annotations

import os
import uuid


def _cap(model_name, *, default_cap: int, context_windows: dict[str, int], default_window: int) -> int:
    if not model_name:
        return default_cap
    dynamic = int(context_windows.get(model_name, default_window) * 0.15 * 4)
    return max(20_000, min(160_000, dynamic))


def truncate_tool_result(tool_name: str, tool_result: str, group_id: int, model_name: str = None,
                         *, default_cap: int, context_windows: dict[str, int], default_window: int,
                         group_workspace) -> tuple[str, str | None]:
    if not isinstance(tool_result, str):
        return tool_result, None
    char_cap = _cap(model_name, default_cap=default_cap, context_windows=context_windows, default_window=default_window)
    if len(tool_result) <= char_cap:
        return tool_result, None
    ws_path = group_workspace(group_id)
    directory = os.path.join(ws_path, "truncated_outputs")
    os.makedirs(directory, exist_ok=True)
    filename = f"tool_result_{uuid.uuid4()}.log"
    with open(os.path.join(directory, filename), "w", encoding="utf-8") as handle:
        handle.write(tool_result)
    half = char_cap // 2
    rel_path = os.path.join("truncated_outputs", filename)
    removed = len(tool_result) - 2 * half
    hint = (f"\n\n[系统提示] 该工具「{tool_name}」输出超长（{len(tool_result):,} 字符），已被自动截断。\n"
            f"完整输出已保存至当前工作区路径：{rel_path}\n"
            "你可以使用 search 工具按关键词检索该文件，或 read_file 工具并设置 offset/limit 参数局部读取（需要 head/tail 时再用 run_shell），"
            "请勿尝试直接读取整份日志以节省上下文空间。")
    return f"{tool_result[:half]}\n\n[... 已自动截断 {removed:,} 字符 ...]\n\n{tool_result[-half:]}{hint}", rel_path


def truncate_user_message(content, group_id: int, model_name: str = None,
                          *, default_cap: int, context_windows: dict[str, int], default_window: int,
                          group_workspace) -> tuple[str, str | None]:
    if not isinstance(content, str):
        return content, None
    char_cap = _cap(model_name, default_cap=default_cap, context_windows=context_windows, default_window=default_window)
    if len(content) <= char_cap:
        return content, None
    directory = os.path.join(group_workspace(group_id), "truncated_outputs")
    os.makedirs(directory, exist_ok=True)
    filename = f"user_message_{uuid.uuid4()}.txt"
    with open(os.path.join(directory, filename), "w", encoding="utf-8") as handle:
        handle.write(content)
    half = char_cap // 2
    rel_path = os.path.join("truncated_outputs", filename)
    removed = len(content) - 2 * half
    hint = (f"\n\n[系统提示] 该用户消息内容超长（{len(content):,} 字符），已自动截断以保护上下文。\n"
            f"完整内容已保存至当前工作区路径：{rel_path}\n"
            "你可以使用 search 工具按关键词检索该文件，或 read_file 工具并设置 offset/limit 参数局部读取该文件。")
    return f"{content[:half]}\n\n[... 已自动截断 {removed:,} 字符 ...]\n\n{content[-half:]}{hint}", rel_path
