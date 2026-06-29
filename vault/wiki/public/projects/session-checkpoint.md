---
title: session-checkpoint
type: project
sensitivity: public
entity: session-checkpoint
sources: 11 claims across 1 dates (2026-06-27 → 2026-06-27)
last_rebuilt: 2026-06-27
---

# session-checkpoint

## Current Understanding

* Session-checkpoint is the single immutable source of truth for knowledge management.
* The project involves merging checkpoint capture, Brain RAG, and wiki into one private git repo named `second-brain/` with subdirectories `vault/`, `engine/`, and `runtime/`.
* Commit everything, including sensitive data, in the private repo; sensitivity is a content classification for routing, not a git-tracking gate.
* Wiki freshness is achieved through hybrid approach: daily topic-context application (mutate/compound) + weekly rebuild against immutable source.
* Provenance key file `<journal>#<anchor>` is reused across todo, CMD, delta, and wiki for idempotency.

## Decisions

- session-checkpoint is the single immutable source of truth; Brain vectors and wiki pages are derived and rebuildable.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- Merge checkpoint capture + Brain RAG + wiki into one private git repo second-brain/ with vault/ (durable), engine/ (Brain code), runtime/ (regenerable, gitignored).  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- Durable-knowledge directory is named vault/; corpus/data rejected as too generic.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- Commit everything including sensitive/ in the private repo; sensitivity is a content classification for routing, not a git-tracking gate; secret scanner still guards credentials.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- Wiki freshness is hybrid: topic-context apply daily (mutate/compound) + rebuild weekly (reconcile against immutable source).  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- Provenance key file:<journal>#<anchor> is reused across todo, CMD, delta, and wiki for idempotency.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- Recommended (pending confirm) merge path is to evolve the existing Brain repo in place into second-brain/, preserving its git history and pre-commit security tooling.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)

## Learnings

- Karpathy's LLM-Wiki pattern = immutable timestamped capture at the bottom, synthesized views derived on top; an alternative to re-retrieving raw docs (RAG).  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- session-checkpoint and the Brain project share the same append-only dated architecture; topic-context/checkpoint-index already form a partial regenerate-on-demand version of the wiki layer.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)

## Facts / Artifacts

- The delta entities block maps almost 1:1 onto Brain's SQLite structurer schema, so Brain can eventually trust the delta and skip re-structuring.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- Phase 1 shipped: Step 6 delta emission added to session-checkpoint SKILL.md and journals moved to a journal/ subdir.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)

## Open Problems

- Phase 1 was unvalidated until this run; this checkpoint is the first exercise of the new journal/ + delta paths.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- Merge strategy needs explicit confirm: Option A (evolve in place) vs Option B (fresh repo via git subtree), plus whether to rename the dir to second-brain/.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)
- Parked: journals/deltas are not directory-split public/sensitive (only wiki + Brain collections are); the delta sensitivity field does the routing.  ^[2026-06-27](../../../journal/2026-06-27.md#second-brain-architecture)

## Related

**Technologies:** `ChromaDB` · `Karpathy LLM-Wiki` · `Ollama` · `RAG` · `SQLite` · `detect-secrets` · `git subtree` · `launchd` · `pre-commit`
