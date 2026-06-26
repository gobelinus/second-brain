---
title: topic-context
type: project
sensitivity: public
applied_provenances:
  - file:/Users/tarun.arora/Forge/toolkit/checkpoints/journal/2026-06-27.md#second-brain-architecture
updated: 2026-06-27
---

# topic-context

Cross-links: [[second-brain]] · [[session-checkpoint]] · [[llm-wiki-pattern]]

## Current State
The skill being extended into the synthesis layer ("what's true now") of [[second-brain]]. Today it implements `show`/`list`/`update`; Phase 3 adds the wiki modes `apply`/`show`/`lint`/`rebuild`. (This very page was produced by a first manual `apply` run, since the operation is not yet coded into the skill.)

## Decisions
- Wiki freshness is hybrid: `topic-context apply` daily (mutate/compound) + `rebuild` weekly (reconcile against the immutable source to kill drift). ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))

## Learnings
- `topic-context`/`checkpoint-index` already form a partial, regenerate-on-demand version of the wiki layer that Phase 3 makes persistent and compounding. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
