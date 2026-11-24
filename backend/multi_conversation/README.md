# Multi Conversation

`multi_conversation` is the application boundary for ordinary, free-form group
conversation turns. The group message dispatcher and Orchestrator remain the
entry and routing points; the Orchestrator marks the resulting `WorkUnit` with
`execution_mode="multi_conversation"`, and the Worker Runner calls
`plugin.run_turn()`.

```text
multi_conversation/
├── plugin.py   # ordinary conversation execution entry
├── service.py  # per-turn setup coordination
├── context.py  # typed context request/result and Memory port calls
└── session.py  # Session start/resume coordination and evidence
```

This mode is distinct from `online_task`. Dashboard story work enters through
`START_WORKFLOW` and `coding_agent_v1`; it does not pass through
`plugin.run_turn()`. Both paths currently share the Agent executor, so that
executor can use this module's context assembly and Session lifecycle services.
`executor_id` continues to select the underlying Agent execution engine and is
not used to infer the application path.

## Ownership

- This module owns the multi-conversation execution entry, recalled context
  assembly, and Session start/resume coordination used by the shared Agent
  execution loop.
- `sessions/` remains responsible for session persistence and recovery.
- `memory/` remains responsible for memory APIs, storage, and retrieval.
- `executors/` remains responsible for model/tool execution.
- `core/orchestration/` remains responsible for choosing participants and
  producing the next work unit.

Multi-Agent collaboration remains in `core/orchestration/`: its strategies
continue to choose and sequence Bots, and their WorkUnits still use the shared
executor. They reuse the context and Session setup services without entering
the ordinary-chat `plugin.run_turn()` route.

The Agent loop itself remains in `executors/`. The module composes its
conversation setup operations through `ConversationSetupPorts`, keeping the
runtime reusable while giving multi-turn context and lifecycle policy a
separate home.
