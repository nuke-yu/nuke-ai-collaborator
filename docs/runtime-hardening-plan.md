# Runtime Hardening Plan

## Summary

This document tracks the runtime hardening work for the `Lifecycle`, `Worker`,
`Supervisor`, and MCP runtime paths.

The current focus is not feature work. The goal is to close state-leak,
cleanup, and shutdown gaps so the runtime behaves like a durable multi-process
agent platform instead of a best-effort internal tool.

## Current Status

The highest-risk `Lifecycle + handoff` boundaries have already been hardened.

Completed areas:

- `Lifecycle`
  - Concurrent `hydrate / evict / shutdown / sweep / LRU` boundaries are
    covered by implementation fixes and tests.
  - Eviction cleanup no longer stops early when intermediate cleanup steps fail.
  - Group writer close and group file-lock release are now decoupled correctly.

- `Supervisor / handoff`
  - `reassign_group()` now has generation tracking, per-group serialization,
    stale ACK protection, stale disconnect cleanup, and lock/version cleanup.
  - Handoff cleanup state now converges back to empty after direct reassign,
    timeout, disconnect, and stop paths.
  - Structured reassign logging is in place for key handoff outcomes.

- Test stability
  - `tests/test_cell_17_lifecycle.py` is green.
  - `tests/test_cell_18_handoff.py` is green.
  - Cross-run of both suites is green.

## Completed Work

Recently completed hardening changes include:

- `Log lifecycle cleanup failures without skipping eviction`
- `Add structured reassign logging to supervisor`
- `Track supervisor cleanup tasks during stop`
- `Clear stale route reassign locks on disconnect`
- `Clear stale handoff reassign locks on disconnect`
- `Release supervisor reassign locks after completion`
- `Keep lifecycle permission cleanup running after partial failures`
- `Keep lifecycle eviction cleanup running after abort errors`
- `Release lifecycle locks after writer close failures`

These commits closed the main shutdown, handoff, and cleanup consistency gaps in
the `Lifecycle` and `Supervisor` paths.

## Remaining Work

### 1. Worker shutdown and disconnect cleanup

Target:

- `backend/runtime/worker.py`

Changes:

- Log `mcp_bridge.reset()` failures during `Worker.close()`
- Log `writer.wait_closed()` failures during `Worker.close()`
- Preserve current best-effort close semantics; do not turn close into a hard
  failure path

Tests:

- Extend `backend/tests/test_worker_loop.py`
- Cover bridge reset failure logging
- Cover writer `wait_closed()` failure logging
- Assert `close()` still completes and `_writer` is cleared

### 2. Supervisor stop/disconnect fail-soft audit

Target:

- `backend/runtime/supervisor.py`

Changes:

- Audit remaining shutdown/disconnect `except: pass` sites
- Convert important close/wait failures into log-and-continue behavior
- Keep current stop semantics best-effort and non-fatal

Priority areas:

- old worker `close()`
- server `wait_closed()`
- subprocess terminate/wait cleanup

### 3. MCP cleanup and concurrency hardening

Target:

- `backend/executors/mcp_bridge.py`
- `backend/runtime/mcp_collector.py`
- related MCP tests

Changes:

- Audit collector shutdown cleanup symmetry
- Recheck pending-request cleanup on reset, late result, and collector exit
- Normalize failure-path logging for bridge/proxy/collector cleanup

Goal:

- Bring MCP cleanup behavior up to the same standard now used in
  `Lifecycle/Supervisor`

## Validation Strategy

Every hardening task should follow the same cadence:

1. Make one narrow runtime change
2. Run the directly related tests
3. Commit only after the related tests pass

Core validation suites:

- `python3 -m pytest tests/test_cell_17_lifecycle.py -q`
- `python3 -m pytest tests/test_cell_18_handoff.py -q`
- `python3 -m pytest tests/test_worker_loop.py -q`
- MCP-specific suites once MCP cleanup work resumes

Cross-check suites:

- `python3 -m pytest tests/test_cell_17_lifecycle.py tests/test_cell_18_handoff.py -q`

## Working Rules

- Do not mix feature work into runtime hardening commits
- Prefer behavior-preserving cleanup fixes over structural rewrites
- Any new fail-soft path should log, not silently pass
- Any state introduced for coordination must have an explicit cleanup path
- Keep `docs/CONCURRENCY-EXECUTION-MODEL.md` unchanged unless the runtime model
  itself changes
