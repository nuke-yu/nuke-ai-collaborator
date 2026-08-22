# StoragePort Architecture

Updated: 2026-08-22

## Current boundary

The storage bounded context is under `backend/storage`:

```text
storage/
├── ports.py       # Connection, transaction, migration, health, lifecycle ports
├── contracts.py   # Runtime capability validation
├── composition.py # Context-scoped backend binding
└── adapters/
    └── sqlite.py  # Built-in SQLite implementation
```

`db/` remains a compatibility facade for existing application code. It routes
connections through the active `StorageComposition` and delegates the default
SQLite implementation to `storage.adapters.sqlite`.

Memory uses the same boundary at its infrastructure edge. `SQLiteMemoryDatabase`
accepts an explicit `StoragePort`, otherwise resolving the adapter from the
current storage scope. Pipeline learning services receive one shared
`PipelineJobRepositoryPort` from the composition root; they do not construct a
second repository per use case. Legacy constructors retain a compatibility
fallback for existing embedded callers.

## Required adapter capabilities

Every replaceable adapter must provide:

- `connect()` and `connect_sync()` for read access;
- `write_connect()` for serialized write transactions;
- `migrate()` for schema changes;
- `health_check()` for readiness/liveness checks;
- `close()` for lifecycle shutdown.

Registration and composition fail fast when any capability is missing. Storage
composition is scoped with `storage_scope()`, so nested hosts can use different
backends without mutating one another's ContextVar state.

## Deliberate limitation

This is now a complete capability contract, not yet a PostgreSQL implementation.
The query and migration layers still contain SQLite-specific SQL. A future
PostgreSQL adapter must provide its own dialect-aware repositories/migrations;
it must not be registered as a compatibility wrapper around SQLite.

## Remaining extraction work

The canonical pipeline repository implementation is still located in the
legacy application module for compatibility with existing imports. Its runtime
instance is now composition-injected and application services depend on the
`PipelineJobRepositoryPort`. Moving the concrete implementation into
`memory.infrastructure` is the next mechanical extraction and must preserve
the compatibility import until callers are migrated.
