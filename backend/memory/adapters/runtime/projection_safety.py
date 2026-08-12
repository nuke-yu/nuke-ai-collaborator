"""Host redaction hooks for projection errors and imported content."""
from __future__ import annotations


def redact_projection_error(message: str) -> str:
    from executors.redaction import redact_secrets
    redacted, _ = redact_secrets(message)
    return redacted


def redact_projection_content(content: str) -> str:
    from executors.redaction import redact_secrets
    redacted, _ = redact_secrets(content)
    return redacted
