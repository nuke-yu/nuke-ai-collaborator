from __future__ import annotations

import json

from memory.infrastructure import safe_memory_mapping, safe_memory_text


def test_memory_text_redacts_bearer_token_and_bounds_payload() -> None:
    secret = "Authorization: Bearer " + "A" * 48
    safe = safe_memory_text(secret, limit=128)

    assert "Bearer " not in safe or "[REDACTED]" in safe
    assert len(safe) <= 128


def test_memory_mapping_redacts_nested_secret_and_limits_depth() -> None:
    payload: dict[str, object] = {
        "headers": {"Authorization": "Bearer " + "B" * 48},
        "nested": {"level": {"next": {"deeper": {"value": {"last": {"x": "y"}}}}}},
    }

    encoded = safe_memory_mapping(payload)
    decoded = json.loads(encoded)

    assert "[REDACTED]" in encoded
    assert "[nested payload truncated]" in encoded
    assert decoded["headers"]["Authorization"] != payload["headers"]["Authorization"]


def test_memory_mapping_budget_never_cuts_json_in_half() -> None:
    payload = {f"field_{index}": "value-" + "x" * 4_000 for index in range(8)}

    encoded = safe_memory_mapping(payload)

    assert len(encoded) <= 16_000
    assert json.loads(encoded)["_truncated"] is True


def test_memory_mapping_honors_tiny_budgets() -> None:
    assert safe_memory_mapping({"secret": "value"}, limit=0) == ""
    assert safe_memory_mapping({"secret": "value"}, limit=1) == ""
    assert safe_memory_mapping({"secret": "value"}, limit=2) == "{}"
    assert len(safe_memory_mapping({"secret": "value"}, limit=8)) <= 8
