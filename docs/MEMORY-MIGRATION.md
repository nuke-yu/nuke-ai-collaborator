# Memory convergence record

Updated: 2026-08-13

`backend/memory` is the only Memory business implementation. The former
`backend/ai` Memory modules and the compatibility facade have been removed;
there is no supported dual-write or fallback mode.

## Runtime boundary

```text
API / Worker / Scheduler
        |
        v
memory.application
        |
        v
memory.ports -> memory.infrastructure
        |
        +--> canonical group/personal SQLite records
        +--> transactional projection outbox
        +--> derived Chroma/vector projections
```

The process-level `MemoryComposition` is the single production wiring point
for the canonical database, schema manager, projection outbox, and reconciler.
Application services receive ports or those composed dependencies; they do not
select a second storage implementation.

## Completed migration

- Conversation memory, Personal Vault, ACL policy evaluation, usage audit,
  group facts, reflections, relations, tool events, cases, experiences,
  skills, learning usage, reflexion, execution runs, metrics, and projection
  outbox services now live under `memory.application`.
- `memory.canonical` and `memory.bootstrap` inject the concrete Personal Vault
  policy. Explicit deny rules are enforced and every authorization decision is
  audited.
- Learning state transitions enforce adoption, execution, and verification
  evidence in the application service. Callers cannot self-strengthen a
  memory by skipping the evidence contract.
- Case upsert retries first resolve the authoritative `case_id` by `run_id`,
  so replays of pre-migration rows cannot return an ID that is not persisted.
- Observation summaries and tool episodes pass through bounded redaction
  before canonical storage. Summary watermarks and message reads are scoped by
  `thread_id`; Personal Vault usage events are emitted only for records that
  actually fit the rendered context.
- `safe_memory_mapping()` bounds the data structure before serialization and
  always returns round-trippable JSON. It never slices serialized JSON text.
- Memory schema v12 and central DB migration 063 scope `agent_cases` identity
  by `(group_id, run_id)`, preserving isolation when a run identifier is reused
  across Groups.
- Observation summaries read the production `meta.memory_observation.thread_id`
  path and retain a top-level fallback for older records.
- Personal Vault schema v3 validates the physical schema, rebuilds legacy
  projection and habit-evidence tables with cascading foreign keys, removes
  orphan rows, and runs `foreign_key_check`; this repair also runs when an old
  database was incorrectly labelled v2. Vault access uses WAL, busy-timeouts,
  and a cross-process file lock. Full Vault deletion removes records,
  projections, usage/audit/governance data, and the physical database file.
  Impact analysis reports actual usage sessions.
- Schema v3 includes source-system identity in Habit evidence keys, merges
  historical duplicate source records deterministically, and stores
  content-free record/projection deletion audit events.
- Habit observations use a stable `habit_key` record, aggregate evidence by
  sample/context/time-span, reject contradictions, and only promote a habit
  after the canonical maturity thresholds are met. Export includes all Vault
  tables, including habit evidence, usage provenance, apps, ACL rules, and
  ACL audit events.
- Personal Vault administration is port-driven: application services receive a
  `PersonalVaultDatabasePort`, while database construction remains in the
  canonical composition roots. ABAC rules have supported set/delete commands.
- Personal sensitivity is monotonic (`private < restricted < secret`); an
  escalation to `secret` revokes active projections in the same transaction.
  Habit evidence accepts only `support` or `contradict`, and all habit fields
  pass through the canonical safety boundary.
- Personal Vault deletion holds the cross-process lock through WAL checkpoint
  and physical unlink. Vault opening validates every core table's final column
  shape and rejects unsupported future schema versions.
- Pipeline job completion and its terminal checkpoint are committed by one
  repository transaction, so a checkpoint failure cannot leave a completed job
  without its durable completion node.
- Run completion commands persist telemetry only; they never overwrite the
  evidence state machine's terminal `verified_success`/`verified_failure`
  states with a non-existent `completed` state. Long-running pipeline handlers
  renew their fenced lease and are cancelled when renewal fails.
- Stable Personal Vault source identities are based on
  `(user, source_type, source_id, kind)` when a source ID exists, so corrected
  content updates the same record and cannot leave an older projected version
  active. Record deletion removes its usage provenance in the same transaction.
- Safety redaction runs before length bounding, including nested mappings and
  all Vault metadata fields such as app names, projection purposes, session
  IDs, and usage purposes.
- `MemoryAuthorizationError` is translated to HTTP 403 centrally, and an
  unavailable authorization audit fails closed.
- Experience aggregation reads and writes under one writer transaction and
  matches task, failure, and environment signatures before combining evidence.
- Projection writes remain transactional outbox intents; Chroma is derived and
  retryable, never authoritative.

## Removed surfaces

The following old Memory modules no longer exist:

- `backend/ai/memory.py`
- `backend/ai/personal_vault.py`
- `backend/ai/cases.py`
- `backend/ai/experiences.py`
- `backend/ai/skill_learning.py`
- `backend/ai/pipeline.py`
- `backend/ai/tool_events.py`
- `backend/ai/execution_runs.py`
- `backend/ai/reflexion.py`
- `backend/ai/usage_tracking.py`
- `backend/ai/learning_metrics.py`
- `backend/ai/projection_outbox.py`
- `backend/memory/compatibility.py`

Historical data backfill scripts remain only where they convert existing data
into canonical records or projections. They are not runtime compatibility
entrypoints and do not define a second Memory model.

## Verification

The migration gate is:

1. `python3 -m compileall -q backend/memory`
2. `git diff --check`
3. canonical contract, authorization, safety, observation, learning, pipeline,
   and projection tests
4. the complete backend regression suite

No commit is releasable while any old Memory import, compatibility facade,
unsafe storage path, or unverified canonical transition remains.
