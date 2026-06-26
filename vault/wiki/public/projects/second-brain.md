---
title: Second Brain
type: project
sensitivity: public
applied_provenances:
  - file:/Users/tarun.arora/Forge/toolkit/checkpoints/journal/2026-06-27.md#second-brain-architecture
updated: 2026-06-27
---

# Second Brain

Cross-links: [[session-checkpoint]] · [[brain]] · [[topic-context]] · [[llm-wiki-pattern]]

## Current State
A personal knowledge system being built by wiring three existing pieces into one private git repo: `session-checkpoint` (capture), the Brain RAG project (retrieval), and a Karpathy-style wiki built by `topic-context` (synthesis). Phase 1 is shipped; the repo merge (Phase 0) and the wiki engine (Phase 3) are the next steps.

## Decisions
- `session-checkpoint` is the single immutable source of truth; Brain vectors and wiki pages are derived and rebuildable, never sources. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
- Merge checkpoint capture + Brain RAG + wiki into one private git repo `second-brain/` with three top-levels: `vault/` (durable), `engine/` (Brain code), `runtime/` (regenerable, gitignored). ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
- The durable-knowledge directory is named `vault/`; `corpus`/`data` rejected as too generic. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
- Commit everything including `sensitive/` in the private repo; sensitivity is a content classification for routing, not a git-tracking gate; the secret scanner still guards raw credentials. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
- Recommended merge path (pending confirm) is to evolve the existing [[brain]] repo in place into `second-brain/`, preserving its git history and pre-commit security tooling. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))

## Open Problems
- Phase 1 validated as of 2026-06-27; merge strategy (evolve-in-place vs fresh repo via git subtree) and repo rename still need explicit confirm. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
- Journals/deltas are not directory-split public/sensitive (only the wiki + Brain collections are); the delta `sensitivity` field does the routing. Parked. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
