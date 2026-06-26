---
title: Brain
type: project
sensitivity: public
applied_provenances:
  - file:/Users/tarun.arora/Forge/toolkit/checkpoints/journal/2026-06-27.md#second-brain-architecture
updated: 2026-06-27
---

# Brain

Cross-links: [[second-brain]] · [[session-checkpoint]] · [[topic-context]]

## Current State
The RAG retrieval layer ("what happened when") of [[second-brain]] — an existing, separate local project (ChromaDB + SQLite over daily summaries). To be merged into `second-brain/` as the `engine/` package.

## Decisions
- Brain becomes the `engine/` package inside the merged `second-brain/` repo. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
- Recommended merge path is to evolve the existing Brain repo in place, preserving its git history + pre-commit security tooling; `vault/`+`runtime/` are additive dirs. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))

## Learnings
- Brain and [[session-checkpoint]] share the same append-only dated architecture; neither does the wiki's synthesis/dedup/cross-link step. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))

## Facts
- The delta `entities` block maps almost 1:1 onto Brain's SQLite structurer schema, so Brain can eventually trust the delta and skip re-structuring. ([2026-06-27](../../journal/2026-06-27.md#second-brain-architecture))
