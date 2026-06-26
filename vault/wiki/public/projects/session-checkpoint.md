---
title: session-checkpoint
type: project
sensitivity: public
applied_provenances:
  - file:/Users/tarun.arora/Forge/toolkit/checkpoints/journal/2026-06-27.md#second-brain-architecture
updated: 2026-06-27
---

# session-checkpoint

Cross-links: [[second-brain]] · [[brain]] · [[topic-context]]

## Current State
The capture layer of [[second-brain]] and its single source of truth: an append-only dated journal skill. Phase 1 extended it to also emit a machine-readable session delta and to write journals under a `journal/` subdir.

## Decisions
- `session-checkpoint` is the single immutable source of truth; downstream Brain + wiki are derived. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
- The provenance key `file:<journal>#<anchor>` is reused across todo, CMD, delta, and wiki for idempotency. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))

## Learnings
- `session-checkpoint` and the [[brain]] project share the same append-only dated architecture; [[topic-context]]/`checkpoint-index` already form a partial, regenerate-on-demand version of the wiki layer. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))

## Facts
- Phase 1 shipped: Step 6 "Emit the session delta" added, and journals moved to a `journal/` subdir so journal/deltas/career/wiki are mergeable siblings under one corpus root. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
