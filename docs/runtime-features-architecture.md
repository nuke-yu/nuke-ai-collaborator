# Runtime Features Architecture

Implemented on 2026-08-21.

The runtime feature refactor is split into independently check-out-able commits:

1. `9b144a0` — route Code Mode bash through the existing tool executor chain.
2. `4eba601` — split Code Mode into domain, application, ports, adapters, and composition.
3. `949fb25` — execute Code Mode in a disposable subprocess with parent-mediated SDK calls.
4. `7e2d280` — scope plugin disposer ownership with `ContextVar` registration scopes.
5. `2d1df83` — scope plugin dependency bindings with `PluginComposition`.
6. `ebad9ac` — complete the storage port with transaction, migration, health, and lifecycle capabilities.

The application/domain layer of a runtime feature is host-independent. Concrete
workspace, shell, process, database, and plugin implementations are assembled
only in composition or adapter modules. The old `executors.code_mode` import is
kept as a compatibility facade for existing callers.

Validation is provided by `backend/tests/test_runtime_features_boundaries.py`
and the feature-specific test suites. The next architectural extension should
add a new bounded context under `backend/runtime_features/<feature>` rather
than adding feature logic to `executors` or `main.py`.
