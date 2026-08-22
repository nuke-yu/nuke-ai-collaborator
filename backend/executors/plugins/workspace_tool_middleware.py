"""Output middleware hooks for workspace tools."""
from __future__ import annotations

import logging
from core import config


async def default_secret_redactor(name: str, arguments: dict, result: str, context: dict) -> str | None:
    from executors.redaction import redact_secrets
    redacted, count = redact_secrets(result)
    if count:
        logging.getLogger(__name__).warning("redacted %d secret(s) from '%s' output", count, name)
        return redacted
    return None


async def default_output_truncator(name: str, arguments: dict, result: str, context: dict) -> str | None:
    from executors.spill import spill_output
    preview, locator = spill_output(
        group_id=(context or {}).get("group_id"), tool_name=name,
        text=result, limit=config.TOOL_RESULT_MAX_CHARS,
    )
    return preview if locator is not None or preview != result else None
