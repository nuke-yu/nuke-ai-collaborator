"""Compatibility export for the pure Memory safety contract."""
from memory.domain.safety import (
    MAX_MEMORY_DEPTH, MAX_MEMORY_ITEMS, MAX_MEMORY_JSON, MAX_MEMORY_TEXT,
    safe_memory_mapping, safe_memory_text,
)

__all__ = ["MAX_MEMORY_TEXT", "MAX_MEMORY_JSON", "MAX_MEMORY_DEPTH",
           "MAX_MEMORY_ITEMS", "safe_memory_text", "safe_memory_mapping"]
