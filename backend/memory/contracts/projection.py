"""Stable projection event types shared by producers and delivery adapters."""

BOT_MEMORY_VECTOR_UPSERT = "bot_memory_vector_upsert"
BOT_MEMORY_VECTOR_DELETE = "bot_memory_vector_delete"
EXPERIENCE_VECTOR_UPSERT = "experience_vector_upsert"

__all__ = [
    "BOT_MEMORY_VECTOR_UPSERT",
    "BOT_MEMORY_VECTOR_DELETE",
    "EXPERIENCE_VECTOR_UPSERT",
]
