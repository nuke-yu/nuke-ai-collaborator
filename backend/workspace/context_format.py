"""Formatting helpers for workspace context injection."""


def format_context_blocks(blocks: list[dict]) -> str:
    parts = []
    for block in blocks:
        label = block["name"] if block["source"] == "bot" else f"{block['name']} (群组)"
        parts.append(f"=== {label} ===\n{block['content']}")
    return "\n\n".join(parts)
