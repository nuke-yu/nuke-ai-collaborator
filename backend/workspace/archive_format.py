"""Pure Markdown rendering for archived workspace runs."""
from __future__ import annotations

import json


def build_archive_markdown(group_id: int, run_id: str, bot: dict, user_message: str,
                           sender_name: str, tool_records: list[dict] | None,
                           reply: str, iterations: int, model: str, executor: str,
                           now) -> str:
    preview_limit = 500
    lines = [
        f"# Run · {bot.get('name', '')} · {now.strftime('%Y-%m-%d %H:%M:%S')}", "",
        f"- **Group:** {group_id}", f"- **Bot:** {bot.get('name', '')} (id={bot.get('id', '')})",
        f"- **Executor:** {executor}", f"- **Model:** {model}", f"- **Run ID:** {run_id}", "",
        "---", "", "## Input", "", f"**From:** @{sender_name}" if sender_name else "**From:** (unknown)", "",
        f"> {user_message[:500].strip()}" if user_message else "> (no message)", "", "---", "", "## Execution", "",
        f"**Iterations:** {iterations}", f"**Tools called:** {len(tool_records or [])}", "",
    ]
    for i, rec in enumerate(tool_records or [], 1):
        result = rec.get("result", "")
        preview = result[:preview_limit] + ("…" if len(result) > preview_limit else "")
        lines += [f"### Tool {i} — {rec.get('name', '')}", "", f"**Args:** `{json.dumps(rec.get('args', {}), ensure_ascii=False)}`", "",
                  "**Result:**", "```", preview, "```", ""]
    lines += ["---", "", "## Output", "", reply.strip()[:2000] + ("…" if len(reply.strip()) > 2000 else ""), ""]
    return "\n".join(lines)
