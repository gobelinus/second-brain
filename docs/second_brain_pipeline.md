# 🧠 Second-Brain Pipeline — Reference

> How the checkpoint → delta → Brain RAG → wiki pipeline fits together, and the
> exact commands to drive it. Quick reference for daily use + onboarding for
> future sessions.
>
> Design rationale lives in `~/.efcode/doodle/checkpoint-brain-wiki-{design,backfill-plan,phase0-plan}.md`.

---

## The idea in one line

**Immutable timestamped capture at the bottom; derived, rebuildable views on top.**
`session-checkpoint` journals are the single source of truth. Everything else
(deltas, Brain vectors, wiki pages) is *derived* and can be regenerated.

```
session-checkpoint (live)  ─┐
                            │  writes
                            ▼
              vault/journal/YYYY-MM-DD.md          ← SOURCE OF TRUTH (durable)
                            │
        brain backfill-deltas │ (deterministic, no LLM)   [+ live skill emits same schema]
                            ▼
              vault/deltas/DATE--slug.json          ← one per session block (durable)
                ┌───────────┴────────────┐
   brain ingest-delta              brain wiki-rebuild / wiki-apply  (LLM synthesis)
   (per date+sensitivity)                       │
                ▼                                ▼
   runtime/store/  (Chroma + SQLite)    vault/wiki/<sens>/<type>/<slug>.md
   "what happened when"  [regenerable]  "what's true now"  [durable]
                │                                │
          brain query                      open in Obsidian / read
```

---

## Layout (where things live)

| Tier | Path | Committed? | Notes |
|---|---|---|---|
| **Durable** | `vault/` | ✅ yes (incl. `sensitive/`) | `journal/`, `deltas/`, `wiki/`, `career/`, `daily/`, `blogs/`. `$CLAUDE_CHECKPOINTS_DIR` → here. |
| **Code** | `src/brain/` | ✅ yes | Brain engine (moves to `engine/` in a later Phase-0 tier). |
| **Regenerable** | `runtime/` | ❌ gitignored | `store/` (Chroma + `brain.db`) + `inputs/`. Rebuild from `vault/` + code. |

`CLAUDE_CHECKPOINTS_DIR` = `/Users/tarun.arora/Forge/brain/vault` (set in
`~/.claude/settings.json`). `~/Forge/toolkit/checkpoints` is a symlink → `vault/`.

---

## Commands

### Quick reference

| Command | Does | LLM? |
|---|---|---|
| `brain backfill-deltas` | journals → `vault/deltas/*.json` (one per block) | no |
| `brain ingest-delta` | deltas + journal text → SQLite + Chroma | embeds |
| `brain wiki-rebuild` | deltas → wiki entity pages (full) | synth |
| `brain wiki-apply --date D` | refresh pages for one date's entities | synth |
| `brain backfill` | all three stages in order (idempotent/resumable) | embeds + synth |
| `brain query "..."` | semantic query over the RAG store | yes |
| `brain status` | store stats | no |

All stages are **idempotent** — safe to re-run. Common flags: `--dry-run`,
`--date YYYY-MM-DD`, `--no-synth` (wiki, skip LLM), `--overwrite` /
`--no-overwrite`.

### 1. `brain backfill-deltas` — journals → deltas (deterministic)

```bash
brain backfill-deltas --dry-run            # count blocks, write nothing
brain backfill-deltas                      # emit missing deltas (skips existing)
brain backfill-deltas --date 2026-06-24 --overwrite
```
Maps each session block's sections → delta fields (entities, claims by type,
open_problems, mood). Conservative sensitivity (default `sensitive`).
**Never overwrites an existing delta** unless `--overwrite` (caution: that also
rewrites irreproducible *live* deltas — safe provenance-aware overwrite is a
pending follow-up).

### 2. `brain ingest-delta` — deltas → Brain RAG

```bash
brain ingest-delta --dry-run
brain ingest-delta                         # all dates
brain ingest-delta --date 2026-06-24
```
Aggregates a date's deltas **per sensitivity** into one entry (Brain is
day-keyed on `(date, sensitivity)`), trusts the delta's entities (skips the LLM
structurer), embeds the **raw journal block text**. Replaces on re-run.

### 3. `brain wiki-rebuild` / `wiki-apply` — wiki synthesis

```bash
brain wiki-rebuild --entity ip_whitelist --no-synth   # fast sample, no LLM
brain wiki-rebuild --type jira --limit 5              # sample 5 jira pages
brain wiki-rebuild                                    # full (~136 pages, LLM-heavy)
brain wiki-rebuild --no-overwrite                     # resume — skip existing
brain wiki-apply --date 2026-06-24                    # daily incremental fold
```
Pages for **projects, jira, people** (technologies = inline tags). Each claim
carries a provenance backlink `^[date](../../../journal/DATE.md#anchor)`. Page
routes to `sensitive/` if any contributing delta is sensitive. `--no-synth`
skips the "Current Understanding" LLM pass (evidence-only, fast).

### 4. `brain backfill` — the whole pipeline

```bash
brain backfill --dry-run                   # plan for all 3 stages
brain backfill                             # deltas → ingest → wiki (full)
brain backfill --skip-deltas --skip-ingest # wiki only
```

---

## Typical flows

**After a checkpoint session** (journal + live delta already written):
```bash
brain ingest-delta --date <today>
brain wiki-apply   --date <today>
```

**Full backfill from scratch** (or after restoring `vault/`):
```bash
ollama serve &                 # ensure Ollama is up (see below)
brain backfill                 # ~25-30 min, mostly the wiki LLM stage
```

**Weekly drift reconcile** (kill wiki drift against the immutable source):
```bash
brain wiki-rebuild
```

**On-demand per-topic context** (ephemeral, not the persistent wiki):
```bash
python3 ~/Forge/shared/toolkit/scripts/topic_context.py show <topic>
python3 ~/Forge/shared/toolkit/scripts/topic_context.py list
```

---

## Dependencies

- **Ollama** must be running for `ingest-delta` (embeddings via
  `nomic-embed-text`) and wiki synthesis (`llama3.1:8b`):
  ```bash
  ollama serve            # then: curl -s localhost:11434/api/tags
  ```
- Deterministic stages (`backfill-deltas`, `--no-synth` wiki, `--dry-run`) need
  no Ollama.

---

## Invariants

1. **One write of truth** — only `session-checkpoint` records raw content.
   Deltas, vectors, wiki are derived.
2. **Provenance everywhere** — `file:<journal>#<anchor>` ties every derived fact
   back to its source block.
3. **Sensitivity propagates** — rides the delta; routes Chroma collection
   (`brain_public`/`brain_sensitive`) and wiki page location. Never down-classified.
4. **Idempotent** — re-running any stage adds no duplicates.
5. **`runtime/` is disposable** — rebuildable from `vault/` + code; never committed.

---

## Known follow-ups

- `index.md` / `log.md` auto-maintenance for the wiki (not yet built).
- Jira pages use *delta-level* claim association → mild over-attribution
  (claims from a session that merely mentioned a ticket).
- Safe, provenance-aware `--overwrite` for `backfill-deltas` (don't clobber live
  deltas; stamp `source: backfill|live`).
- `technologies` field is noisy (verbose journal prose); cleaned at wiki display
  time, but the deltas still carry fragments.
- **LLM backend is Ollama-only, by design** (decided 2026-06-27). Embeddings
  must stay local regardless (Anthropic has no embeddings API). A *pluggable*
  generative backend (Claude for `public` content only, sensitive + embeddings
  staying local) is a possible future option — deferred for privacy
  (sensitive content must not leave the machine).

---

*Components: `journal_parser.py`, `delta_backfill.py`, `delta_ingest.py`,
`wiki.py`, wired in `cli.py`. Built 2026-06.*
