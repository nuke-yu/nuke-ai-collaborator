# Memory Capability Wiring Status

> Updated: 2026-08-22
>
> An adapter existing in `memory/adapters` is not evidence that the default
> recall or learning path invokes it.

## Status vocabulary

| Status | Meaning |
|---|---|
| `implemented` | Algorithm or adapter has implementation and unit tests. |
| `composed` | Adapter can be constructed by the Memory composition root. |
| `wired` | A production application use case invokes it on the normal path. |
| `enabled` | Default runtime configuration selects that path. |
| `verified` | Production-shaped integration test proves the complete path. |

## Current inventory

| Capability | Implemented | Composed | Wired by default | Honest claim |
|---|---:|---:|---:|---|
| Case → Experience → Skill learning | Yes | Yes | Yes | Nuke-native durable learning pipeline; not a complete EverOS runtime |
| Voyager critic and gated skill plan | Yes | Yes | Partial | Constrained declarative skill plan; not an executable Voyager code library |
| Temporal Graph / Graphiti adapter | Yes | Yes | No for default hot recall | Optional temporal relation capability |
| RRF/MMR reranker | Yes | Yes | Partial | Optional reranking component |
| Lexical/vector/cluster recall | Yes | Yes | Yes | Current default Experience recall path |
| Projection rollout gate | Yes | Yes | Yes | Active migration gate protecting retirement of legacy direct writes |

## Explicit non-claims

- Graphiti support does not mean every memory query performs graph retrieval.
- RRF/MMR support does not mean every result is reranked by those algorithms.
- Voyager integration does not mean arbitrary executable skills are generated.
- `direct_write_enabled` is not dead code today. Runtime lifecycle still
  records projection audits and consults the rollout gate. It is transitional
  code with a retirement condition, not a second canonical write path.

## Retirement condition for the projection gate

The rollout gate may be removed only after every supported deployment has:

1. canonical writes enabled for every group;
2. no runtime caller depending on legacy direct Chroma writes;
3. a completed projection audit for the migration window; and
4. a schema migration that removes the rollout state table and metrics together.

