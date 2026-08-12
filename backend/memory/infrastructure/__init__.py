"""Infrastructure owned by the Memory bounded context."""

from .projection_outbox import DrainResult, ProjectionOutbox
from .safety import safe_memory_mapping, safe_memory_text
from .sqlite_database import SQLiteMemoryDatabase
from .personal_database import PersonalVaultDatabase
from .schema import (MEMORY_GROUP_DDL, MEMORY_GROUP_TABLES, MEMORY_SCHEMA_VERSION,
                     MemorySchemaManager)

__all__ = ["DrainResult", "ProjectionOutbox", "MEMORY_GROUP_DDL",
           "MEMORY_GROUP_TABLES", "MEMORY_SCHEMA_VERSION", "MemorySchemaManager",
           "safe_memory_mapping", "safe_memory_text", "SQLiteMemoryDatabase",
           "PersonalVaultDatabase"]
