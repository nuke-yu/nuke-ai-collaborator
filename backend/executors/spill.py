"""Bounded spill storage for oversized tool output."""
from __future__ import annotations

import os
import re
import tempfile
import uuid

MAX_SLICE_LINES = 200
MAX_SLICE_CHARS = 20_000
_LOCATOR_RE = re.compile(r"^spill://(tool_result_[0-9a-f]{32}\.log)$")


def _preview(text: str, limit: int) -> str:
    lines = text.splitlines(keepends=True)
    if len(lines) > 100:
        preview = "".join(lines[:50])
        preview += "\n[... 中间内容已溢出，请使用 slice_read ...]\n"
        preview += "".join(lines[-50:])
    else:
        preview = text
    if len(preview) <= limit:
        return preview
    half = max(1, (limit - 80) // 2)
    return (
        preview[:half]
        + "\n[... 预览已限制，请使用 slice_read ...]\n"
        + preview[-half:]
    )


def spill_output(*, group_id: int | None, tool_name: str, text: str, limit: int):
    """Persist oversized output and return ``(preview, locator)``."""
    if len(text) <= limit:
        return text, None
    if group_id is None:
        return _preview(text, limit), None

    from workspace import group_workspace

    directory = group_workspace(group_id) / "truncated_outputs"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"tool_result_{uuid.uuid4().hex}.log"
    target = directory / filename
    fd, temporary = tempfile.mkstemp(prefix=f".{filename}.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise

    return (
        _preview(text, limit)
        + f"\n\n[系统提示] 工具「{tool_name}」输出已溢出并保存。"
        f"完整内容句柄：spill://{filename}。"
        "请使用 slice_read(locator, start_line, end_line) 按需读取。",
        f"spill://{filename}",
    )


def read_spilled_lines(*, group_id: int | None, locator: str, start_line: int, end_line: int) -> str:
    """Read a bounded, 1-based inclusive line range from a spill file."""
    if group_id is None:
        return "[错误] 缺少 group_id"
    match = _LOCATOR_RE.fullmatch(locator.strip())
    if not match:
        return "[参数错误] 非法 spill locator"
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (start_line, end_line)):
        return "[参数错误] 行号必须是整数"
    if start_line < 1 or end_line < start_line:
        return "[参数错误] 行范围无效"
    if end_line - start_line + 1 > MAX_SLICE_LINES:
        return f"[参数错误] 单次最多读取 {MAX_SLICE_LINES} 行"

    from workspace import group_workspace

    root = group_workspace(group_id).resolve()
    spill_root = (root / "truncated_outputs").resolve()
    target = (spill_root / match.group(1)).resolve()
    if not target.is_relative_to(spill_root) or not target.is_file():
        return "[错误] spill 内容不存在"
    try:
        selected: list[str] = []
        selected_chars = 0
        with target.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if line_number < start_line:
                    continue
                if line_number > end_line:
                    break
                selected.append(line)
                selected_chars += len(line)
                if selected_chars >= MAX_SLICE_CHARS:
                    break
    except (OSError, UnicodeError) as exc:
        return f"[读取错误] {exc}"
    result = "".join(selected)
    if len(result) > MAX_SLICE_CHARS:
        result = result[:MAX_SLICE_CHARS] + "\n[... slice_read 输出已限制 ...]"
    return result or "[空范围]"
