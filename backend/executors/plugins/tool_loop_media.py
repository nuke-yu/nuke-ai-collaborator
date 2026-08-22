"""MCP media-marker promotion at the group-aware worker boundary."""
from __future__ import annotations

import logging
import re
import shutil

MCPSHOT_RE = re.compile(r"__mcpshot__:([A-Za-z0-9._-]+)")


def check_and_attach_file(runner, tool_result: str) -> tuple[str, bool]:
    """Promote staged screenshot markers into the owning group's media dir."""
    if not isinstance(tool_result, str) or "__mcpshot__:" not in tool_result:
        return tool_result, False
    from core import media
    from workspace import layout

    group_id = runner.ctx.group_id
    if not group_id:
        return tool_result, False
    staging = layout.media_staging_dir()
    dest_dir = layout.group_media_dir(group_id, "screenshots")
    modified = False
    for filename in MCPSHOT_RE.findall(tool_result):
        if not media.is_safe_filename(filename):
            continue
        src = staging / filename
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
        mime_type = "image/jpeg" if ext == "jpg" else f"image/{ext}"
        try:
            if src.exists() and src.is_file():
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest_dir / filename))
            ref = media.canonical_ref(group_id, "screenshots", filename)
            if "attached_file" not in runner.execution_ctx:
                runner.execution_ctx["attached_file"] = {
                    "url": ref, "name": filename, "type": mime_type,
                }
            modified = True
        except Exception as exc:
            logging.getLogger(__name__).error(
                "Failed to promote MCP screenshot %s: %s", filename, exc
            )
    return MCPSHOT_RE.sub("screenshot attached", tool_result), modified
