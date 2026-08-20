# Runtime Features

Runtime features are bounded contexts with an explicit composition boundary.

Each feature follows:

```text
domain/contracts -> application -> ports <- adapters
                              ^
                         composition
```

`code_mode` keeps policy and orchestration in `domain.py` and `application.py`.
Workspace, bash, and process execution are adapters. `executors.code_mode` is
only a compatibility facade; new callers should use `runtime_features.code_mode`.

The feature must receive host capabilities through ports. It must not import
the Worker, API, database, or plugin registry from its application layer.
