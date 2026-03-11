# 🧠 Brain Project — Context Document
> Paste this at the start of any new chat to continue building.
> Last updated: 2026-03-11

---

## What We Are Building

A **local, private, queryable personal second brain** built from daily
activity summaries in markdown. No cloud APIs. No telemetry. Runs
entirely on a laptop using Ollama + local LLMs.

Core pattern: **RAG (Retrieval Augmented Generation)** — not fine-tuning.
Daily summaries are ingested, chunked, embedded, and stored. A local LLM
answers queries using retrieved chunks as context.

---

## Guiding Principles

| Principle | Decision |
|---|---|
| Privacy First | 100% local — no cloud APIs, no telemetry |
| Simplicity | Minimal dependencies, runs on a laptop |
| Incremental | Each phase delivers standalone value |
| Extensible | Easy to add new input sources later |
| Human Readable | Source data stays in plain markdown always |
| Security | Pre-commit hooks guard against sensitive data leaking |
| Version Control | Code + public summaries tracked, sensitive data never |

---

## Tech Stack

| Component | Technology |
|---|---|
| Package manager | `uv` with `pyproject.toml` |
| Python version | 3.12 |
| LLM | Ollama + Mistral 7B (local) |
| Embeddings | Ollama + `nomic-embed-text` (local) |
| Vector store | ChromaDB (persistent, file-based) |
| Structured store | SQLite (stdlib, zero-ops) |
| CLI framework | Typer (`brain` command) |
| Dashboard | Streamlit + Plotly |
| Code quality | ruff (format + lint) |
| Security | pre-commit + detect-secrets + custom scanner |

Note: LlamaIndex was originally planned for RAG orchestration but was not
used — the RAG pipeline was implemented directly with ChromaDB + Ollama.

---

## Project Structure

```
brain/
├── .git/
├── .gitignore                      ← sensitive/, store/, inputs/ all ignored
├── .pre-commit-config.yaml         ← ruff + detect-secrets + custom scanner
├── .secrets.baseline               ← detect-secrets baseline
├── .env.example                    ← safe template, .env is git ignored
├── .streamlit/
│   └── config.toml                 ← light theme, telemetry off, localhost only
├── pyproject.toml                  ← uv managed, brain CLI entrypoint
├── uv.lock                         ← tracked, reproducible installs
├── config.yaml                     ← ALL settings here, no hardcoded values
├── config.local.yaml               ← git ignored, machine-specific overrides
├── README.md                       ← ✅ full project documentation
├── dashboard.py                    ← ✅ Streamlit dashboard (4 tabs)
├── todo.txt                        ← rolling action items (todo.txt format)
│
├── daily/
│   ├── public/                     ← ✅ git tracked
│   │   └── YYYY-MM-DD.md
│   └── sensitive/                  ← 🚫 git ignored
│       └── YYYY-MM-DD.md
│
├── blogs/
│   ├── public/                     ← ✅ git tracked — published posts
│   │   └── YYYY-MM-DD-slug.md
│   └── sensitive/                  ← 🚫 git ignored — drafts
│       └── YYYY-MM-DD-slug-draft.md
│
├── inputs/                         ← 🚫 git ignored (raw daily inputs)
│   ├── YYYY-MM-DD/                 ← drop files here for composer
│   │   ├── ai_summary.md
│   │   └── meeting_standup.md
│   └── archive/                    ← processed inputs moved here
│
├── exports/
│   ├── public/                     ← ✅ git tracked
│   ├── sensitive/                  ← 🚫 git ignored
│   └── reminders/                  ← daily reminder markdown files
│
├── store/                          ← 🚫 git ignored (generated)
│   ├── chroma/
│   └── brain.db
│
├── scripts/
│   ├── __init__.py
│   ├── scan_public_summaries.py    ← ✅ pre-commit hook, config-driven
│   ├── reindex.py                  ← ✅ full corpus rebuild script
│   └── daily_reminder.py           ← ✅ launchd entry point
│
├── launchd/                        ← macOS launchd plist (generated on install)
│   └── com.brain.reminder.plist
│
└── src/brain/
    ├── __init__.py
    ├── config.py                   ← ✅ BrainConfig typed accessors, deep merge
    ├── structurer.py               ← ✅ LLM metadata extraction (Mistral, temp=0)
    ├── ingest.py                   ← ✅ SQLite + ChromaDB pipeline (date_int fix)
    ├── chunker.py                  ← ✅ TextChunker + Embedder
    ├── exporter.py                 ← ✅ dated markdown export, sensitivity routing
    ├── composer.py                 ← ✅ multi-file LLM combiner + todo extraction
    ├── reminder.py                 ← ✅ macOS notification + launchd management
    ├── trend.py                    ← ✅ SQLite aggregation queries
    ├── router.py                   ← ✅ QueryRouter, four patterns (A/B/C/D)
    ├── query.py                    ← ✅ RAGEngine, ChromaDB + Ollama/Mistral
    ├── blog_ingest.py              ← ✅ BlogIngestPipeline (ChromaDB only)
    └── cli.py                      ← ✅ full CLI — all commands wired
```

---

## CLI Commands (all built)

```bash
# ── Daily workflow ────────────────────────────────────────────────────────────
brain ingest --file daily/public/2026-03-11.md --sensitivity public
brain ingest --file daily/sensitive/2026-03-11.md --sensitivity sensitive
brain compose --date 2026-03-11 --ingest       # combine inputs/ → summary → ingest
brain compose --date 2026-03-11 --dry-run      # preview without writing

# ── Status + visibility ───────────────────────────────────────────────────────
brain status                                   # store stats + config
brain status --detailed                        # per-date disk vs store breakdown
                                               # prints ingest commands for gaps

# ── Query ─────────────────────────────────────────────────────────────────────
brain query "What was I blocked on last month?"
brain query "What patterns do you see in my blockers?" --scope sensitive
brain query "Summarise my Azure work" --export

# ── Summarize ─────────────────────────────────────────────────────────────────
brain summarize --from 2026-02-01 --to 2026-02-28
brain summarize --from 2026-01-01 --to 2026-01-31 --scope public --export

# ── Trends ───────────────────────────────────────────────────────────────────
brain trend --field projects --from 2026-02-01
brain trend --field sentiment --from 2026-01-01 --to 2026-01-31
brain trend --field blockers --export

# ── Reminder ──────────────────────────────────────────────────────────────────
brain remind                  # send reminder now (test)
brain remind --install        # generate plist + load into launchd (09:00 daily)
brain remind --uninstall
brain remind --status

# ── Dashboard ─────────────────────────────────────────────────────────────────
brain dashboard                # opens http://localhost:8501
brain dashboard --port 8502
brain dashboard --no-browser

# ── Bulk operations ───────────────────────────────────────────────────────────
uv run python scripts/reindex.py               # incremental — skip ingested files
uv run python scripts/reindex.py --rebuild     # wipe + re-ingest everything
uv run python scripts/reindex.py --dry-run     # preview only
uv run python scripts/reindex.py --path daily/public
```

---

## Key Design Decisions

### Storage — Dual Store with Composite Key

```
SQLite (brain.db)                    ChromaDB
─────────────────                    ────────
Structured metadata                  Embeddings + chunks
Fast filter/aggregate                Semantic similarity search
PRIMARY KEY (date, sensitivity)      IDs: {sensitivity}_{date}_chunk_{N}
                                     Metadata: date, date_int, sensitivity,
                                               sentiment, projects, focus_areas
```

**Composite PK:** Public and sensitive summaries for the same date coexist
as independent records. Single `date` PK silently overwrites.

**date_int field:** ChromaDB `$gte`/`$lte` operators only accept numeric
types. `date_int` stores `YYYYMMDD` as an integer (e.g. `20260201`) for
range filtering. ISO string dates cause a silent 0-results failure.

### Query Router — Four Patterns

```
Pattern A: SQLite only          → "how many days blocked in Jan?"
Pattern B: ChromaDB only        → "find notes about feeling overwhelmed"
Pattern C: SQLite → ChromaDB    → "what blocked me in January?"
Pattern D: ChromaDB → SQLite    → "what patterns in my blockers?"
```

Router uses pure string matching — no LLM call, instant, deterministic.
Pattern A checked first (cheapest). Pattern D last (most expensive).

### Sensitivity Model

```
public    → git tracked, scanner enforced, safe to share
sensitive → git ignored, local only, never leaves machine
```

Scanner evaluation order: allowlist → private_domains → blocklist.
Severity: critical/high → block commit, warning → log only.

### Blog Posts

Separate from daily summaries. Stored in `blogs/public/` or
`blogs/sensitive/`. Auto-detected by `IngestPipeline.route()` based on
path — routes to `BlogIngestPipeline` (ChromaDB only, no SQLite).
Separate ChromaDB collections: `blog_public`, `blog_sensitive`.

### Config Layering

```
config.yaml        ← base, git tracked, no secrets
config.local.yaml  ← machine overrides, git ignored
.env               ← secrets only, git ignored
```

---

## Metadata Schema

Extracted by local LLM (Mistral, temperature=0) from every daily summary:

```json
{
  "date": "2026-03-11",
  "sensitivity": "public",
  "projects": ["Personal Second Brain", "Brain CLI"],
  "blockers": [],
  "people": [],
  "sentiment": "productive",
  "focus_areas": ["RAG", "ChromaDB", "SQLite", "Python"],
  "raw_file": "daily/public/2026-03-11.md"
}
```

Sentiment options (enforced): `productive`, `frustrated`, `neutral`,
`overwhelmed`, `energized`

---

## SQLite Schema

```sql
CREATE TABLE daily_summaries (
    date         TEXT NOT NULL,
    sensitivity  TEXT NOT NULL,
    projects     TEXT NOT NULL DEFAULT '[]',   -- JSON array
    blockers     TEXT NOT NULL DEFAULT '[]',   -- JSON array
    people       TEXT NOT NULL DEFAULT '[]',   -- JSON array
    sentiment    TEXT NOT NULL DEFAULT 'neutral',
    focus_areas  TEXT NOT NULL DEFAULT '[]',   -- JSON array
    raw_file     TEXT NOT NULL,
    ingested_at  TEXT NOT NULL,
    PRIMARY KEY (date, sensitivity)
)
```

---

## ⚠️ Hard-Won Lessons

### ChromaDB — Date Filtering Requires Integers
`$gte`/`$lte` operators only accept numeric types. ISO strings silently
return 0 results with no error. Fix: store `date_int: YYYYMMDD int`
alongside `date` string in every chunk's metadata. After adding the field,
run `reindex --rebuild` to backfill existing chunks.

### ChromaDB — L2 Distances Are Not Cosine Similarity Scores
`nomic-embed-text` (768-dim) returns raw L2 distances in the **300–600
range**. A threshold of `1.4` (designed for cosine similarity, range 0–2)
silently drops every result. Fix: remove threshold filtering entirely.
ChromaDB's top-K ranking handles relevance — lower distance = more similar.

### LLM Output Validation Before Storage
LLMs hallucinate structure. Validate that output starts with the expected
H1 header and contains all required sections before writing to disk.
Fail loud at the boundary, not quietly at query time.

### Exception Handlers Must Not Be Too Broad
The `except Exception: continue` pattern in `_retrieve_chunks` swallowed
real errors (missing `date_int` field) and returned 0 chunks with no
warning. Make exception handlers specific — only catch what you expect.

---

## Phase Delivery Status

```
Phase 1   ✅ COMPLETE
──────────────────────────────────────────────────────────
  config.py       — BrainConfig typed accessors, deep merge
  structurer.py   — Ollama/Mistral metadata extraction
  ingest.py       — SQLiteStore + ChromaStore + IngestPipeline
  chunker.py      — TextChunker + Embedder
  exporter.py     — dated markdown export, sensitivity routing
  cli.py          — ingest, status commands
  pre-commit      — ruff, detect-secrets, public scanner

Phase 1b  ✅ COMPLETE
──────────────────────────────────────────────────────────
  composer.py     — multi-file LLM combiner, todo extraction
  reminder.py     — macOS notification + launchd plist management
  scripts/daily_reminder.py — launchd entry point
  cli.py update   — compose + remind commands
  config.yaml     — scanner, composer, reminder settings
  blog_ingest.py  — BlogIngestPipeline, separate ChromaDB collections

Phase 2   ✅ COMPLETE
──────────────────────────────────────────────────────────
  trend.py        — SQLite aggregation (COUNT, GROUP BY, json_each)
  cli.py update   — brain trend command wired

Phase 3   ✅ COMPLETE
──────────────────────────────────────────────────────────
  router.py       — QueryRouter, four patterns (A/B/C/D)
  query.py        — RAGEngine, ChromaDB + Ollama/Mistral
  cli.py update   — brain query + brain summarize wired
  scripts/reindex.py — full corpus rebuild (incremental + --rebuild)
  Fix: date_int numeric field for ChromaDB range filtering
  Fix: removed similarity threshold (L2 distances are 300-600, not 0-2)

Phase 4   ✅ COMPLETE
──────────────────────────────────────────────────────────
  dashboard.py    — Streamlit, 4 tabs, light theme, Plotly charts
    Tab 1: Daily Entries — two-panel browse, sentiment + sensitivity badges
    Tab 2: Blockers — Plotly frequency bar chart + timeline table
    Tab 3: Action Items — todo.txt parser, priority/project/date filters
    Tab 4: Query — RAGEngine wired, spinner, metadata bar, 5-item history
  .streamlit/config.toml — telemetry off, localhost only, light theme
  cli.py update   — brain dashboard command
  cli.py update   — brain status --detailed (disk vs store per-date table)
  README.md       — full project documentation

Phase 5   ⏳ NOT STARTED
──────────────────────────────────────────────────────────
  Sentiment timeline chart (Plotly line chart, mood over time)
  Visual project frequency over time
  Query history persistence (SQLite)
  Dashboard date range picker (global filter across all tabs)
```

---

## What Is NOT Built Yet (Honest Gaps)

These were discussed or designed but not implemented:

| Item | Notes |
|---|---|
| Sentiment timeline in dashboard | Phase 5 — line chart of mood over time |
| Query history persistence | Session state only — lost on restart |
| Dashboard global date filter | Each tab has its own filter today |
| `brain query` + `brain summarize` tests via CLI runner | test_cli_phase3.py stubs exist |
| anonymizer.py script | Designed in config.yaml, not built |
| Multi-user support | Explicitly out of scope |
| Streamlit auth | Not needed — localhost only |

---

## Git Log (as of 2026-03-11)

```
feat: brain status --detailed + brain dashboard command + README
feat: Phase 4 Steps 1-4 — Streamlit dashboard complete
feat: Phase 3 complete — RAG query engine, reindex script, CLI wired
fix: ChromaDB date filter requires date_int (YYYYMMDD int) not ISO strings
fix: remove similarity threshold — nomic-embed-text L2 distances are 300-600
feat: add QueryRouter — Phase 3 Step 1 (4 patterns, 27 tests)
feat: add RAGEngine — Phase 3 Step 2 (ChromaDB + Ollama, 25 tests)
feat: wire brain query + brain summarize — Phase 3 Step 3
feat: add reindex script — full corpus rebuild with --rebuild/--dry-run
feat: Phase 2 complete — trend.py SQLite aggregations
feat: Phase 1b complete — composer, reminder, blog ingestion, scanner
feat: add BrainConfig loader with deep merge and typed accessors
feat: add IngestPipeline — SQLite + ChromaDB storage with idempotent upserts
feat: add Structurer — LLM metadata extraction from markdown summaries
feat: add CLI — ingest, query, trend, summarize, status commands
feat: add Exporter — dated markdown export with sensitivity routing
chore: add pre-commit hooks — ruff, detect-secrets, public scanner
chore: project bootstrap — structure, config, gitignore, deps
```

Tags: `v0.1.0-phase1` · `v0.3.0-phase3` · `v0.4.0-phase4`

---

## How to Continue in a New Chat

Paste this document + `BRAIN_BLOG_CONTEXT.md`, then say one of:

```
"Start Phase 5"                  ← sentiment timeline, query history
"Draft blog post N"              ← write a post from the blog context
"Build anonymizer.py"            ← script to redact sensitive content
"Add query history persistence"  ← persist queries to SQLite
```

Build one file at a time. Commit at each checkpoint.
