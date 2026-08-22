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
- `dialect` with parameter-placeholder and identifier-quoting capabilities.

Registration and composition fail fast when any capability is missing. Storage
composition is scoped with `storage_scope()`, so nested hosts can use different
backends without mutating one another's ContextVar state.

## Deliberate limitation

This is now a complete capability contract, not yet a PostgreSQL implementation.
The query and migration layers still contain SQLite-specific SQL. A future
PostgreSQL adapter must provide its own dialect-aware repositories/migrations
and implement the dialect contract; it must not be registered as a
compatibility wrapper around SQLite.

## Current extraction status

The canonical pipeline repository implementation now lives in
`memory.infrastructure.pipeline_jobs`. The application module retains only the
dispatcher and a temporary dynamic compatibility symbol for older imports.
The runtime instance is composition-owned and application services depend on
the `PipelineJobRepositoryPort`.

The remaining work is to remove that compatibility symbol after all callers
have migrated, and to route the remaining learning use cases that write
`pipeline_jobs` directly through the repository port.
