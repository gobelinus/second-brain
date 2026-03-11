# 🧠 Brain — Local Private Second Brain

A fully local, private, queryable knowledge system built from daily markdown
summaries. No cloud APIs. No telemetry. Runs entirely on your laptop.

**Core pattern:** RAG (Retrieval Augmented Generation) — daily summaries are
ingested, chunked, embedded, and stored. A local LLM answers queries using
retrieved chunks as context.

---

## Guiding Principles

| Principle | Decision |
|---|---|
| **Privacy First** | 100% local — no cloud APIs, no telemetry |
| **Simplicity** | Minimal dependencies, runs on a laptop |
| **Incremental** | Each phase delivers standalone value |
| **Human Readable** | Source data stays in plain markdown — always inspectable |

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

---

## Project Structure

```
brain/
├── dashboard.py               ← Streamlit dashboard (brain dashboard)
├── config.yaml                ← All settings — no hardcoded values
├── config.local.yaml          ← Machine-specific overrides (git ignored)
├── todo.txt                   ← Rolling action items (todo.txt format)
│
├── daily/
│   ├── public/                ← ✅ git tracked — YYYY-MM-DD.md
│   └── sensitive/             ← 🚫 git ignored — YYYY-MM-DD.md
│
├── blogs/
│   ├── public/                ← ✅ git tracked — published posts
│   └── sensitive/             ← 🚫 git ignored — drafts
│
├── inputs/                    ← 🚫 git ignored — raw daily inputs
│   ├── YYYY-MM-DD/            ← drop files here for composer
│   └── archive/               ← processed inputs moved here
│
├── exports/
│   ├── public/                ← ✅ git tracked — exported query results
│   ├── sensitive/             ← 🚫 git ignored
│   └── reminders/             ← daily checklist files
│
├── store/                     ← 🚫 git ignored (auto-generated)
│   ├── chroma/                ← ChromaDB vector store
│   └── brain.db               ← SQLite metadata store
│
├── scripts/
│   ├── reindex.py             ← full corpus rebuild script
│   ├── daily_reminder.py      ← launchd entry point
│   └── scan_public_summaries.py ← pre-commit security scanner
│
├── .streamlit/
│   └── config.toml            ← Streamlit: light theme, telemetry off
│
└── src/brain/
    ├── config.py              ← BrainConfig typed accessors
    ├── structurer.py          ← LLM metadata extraction
    ├── ingest.py              ← SQLite + ChromaDB pipeline
    ├── composer.py            ← multi-file daily summary combiner
    ├── reminder.py            ← macOS notification + checklist
    ├── exporter.py            ← markdown export utility
    ├── router.py              ← query router (4 patterns)
    ├── query.py               ← RAG engine
    ├── trend.py               ← SQLite aggregation queries
    └── cli.py                 ← Typer CLI entrypoint
```

---

## Setup

### Prerequisites

```bash
# Install Ollama
brew install ollama        # macOS
# or: https://ollama.com/download

# Pull required models
ollama pull mistral
ollama pull nomic-embed-text

# Install uv (Python package manager)
brew install uv
```

### Install

```bash
git clone <your-repo>
cd brain

# Install dependencies
uv sync

# Verify CLI works
brain --help
```

---

## Daily Workflow

### 1. Write your daily summary

Drop notes into `inputs/YYYY-MM-DD/`:

```
inputs/2026-03-11/
  ├── ai_summary.md       ← from your AI tool
  ├── standup.md          ← meeting notes
  └── notes.md            ← ad-hoc notes
```

### 2. Compose + ingest

```bash
# Combine all input files into a structured summary via local LLM
# Writes to daily/sensitive/YYYY-MM-DD.md + extracts todos to todo.txt
brain compose --date 2026-03-11 --ingest
```

Or ingest an existing summary directly:

```bash
brain ingest --file daily/public/2026-03-11.md --sensitivity public
brain ingest --file daily/sensitive/2026-03-11.md --sensitivity sensitive
```

### 3. Check status

```bash
# Summary stats
brain status

# Full per-date breakdown — shows what's on disk vs what's ingested
# Prints ready-to-run commands for any gaps
brain status --detailed
```

### 4. Query your brain

```bash
# Natural language questions
brain query "What was I blocked on last month?"
brain query "Summarise my AWS work in February" --scope sensitive
brain query "What patterns do you see in my blockers?" --export

# Date range summary
brain summarize --from 2026-02-01 --to 2026-02-28

# Structured trend queries
brain trend --field projects --from 2026-02-01
brain trend --field sentiment --from 2026-01-01 --to 2026-01-31
brain trend --field blockers --export
```

### 5. Open the dashboard

```bash
brain dashboard
# Opens http://localhost:8501 in your browser
```

---

## Bulk Ingest / Reindex

When you have many files to ingest, or after schema changes (e.g. adding
metadata fields), use the reindex script instead of running `brain ingest`
for each file individually.

```bash
# Incremental — only ingests files not yet in the store
uv run python scripts/reindex.py

# Full rebuild — wipes SQLite + ChromaDB and re-ingests everything
# Use this after any metadata schema changes
uv run python scripts/reindex.py --rebuild

# Preview what would be ingested without touching anything
uv run python scripts/reindex.py --dry-run
uv run python scripts/reindex.py --rebuild --dry-run

# Single directory only
uv run python scripts/reindex.py --path daily/public

# Verbose — full pipeline output per file (useful for debugging)
uv run python scripts/reindex.py --verbose
```

**When to use `--rebuild`:**
- After adding new metadata fields to `ChromaStore.upsert_chunks()`
  (e.g. the `date_int` field added in Phase 3)
- After changing chunking settings in `config.yaml`
- After deleting summaries (removes ghost SQLite/ChromaDB records)

---

## Sensitivity Model

```
public    → git tracked, scanner enforced, safe to share
sensitive → git ignored, local only, never leaves machine
```

Both can exist for the same date — stored as separate records with a
composite primary key `(date, sensitivity)`.

The pre-commit scanner enforces that public summaries contain no:
- Email addresses or phone numbers
- Internal domain names or private URLs
- API keys, passwords, or credentials
- Compensation or HR-sensitive terms

---

## CLI Reference

```bash
brain ingest    --file PATH --sensitivity [public|sensitive]
brain compose   --date YYYY-MM-DD [--ingest] [--dry-run]
brain remind    [--install] [--uninstall] [--status]
brain query     QUESTION [--scope all|public|sensitive] [--export]
brain summarize --from YYYY-MM-DD --to YYYY-MM-DD [--scope all] [--export]
brain trend     --field [projects|blockers|sentiment|focus_areas|people]
                [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--export]
brain status    [--detailed]
brain dashboard [--port 8501] [--no-browser]
```

All commands that produce output support `--export`. Exported files land
in `exports/public/` or `exports/sensitive/` with a dated filename.

---

## Dashboard

```bash
brain dashboard
# or
uv run streamlit run dashboard.py
```

Four tabs:

| Tab | What it shows |
|---|---|
| 📅 Daily Entries | Two-panel browse — date list on left, full summary on right |
| 🚧 Blockers | Plotly frequency chart + timeline table, filterable by date and sensitivity |
| ✅ Action Items | todo.txt grouped by date, filtered by priority / project |
| 🔍 Query | Natural language RAG query interface with routing metadata |

The dashboard is read-only — all ingestion stays in the CLI.
Runs on `localhost:8501` only — no external access.

---

## Query Routing

The query engine automatically routes questions to the most efficient
retrieval pattern:

| Pattern | When used | Example |
|---|---|---|
| **A** — SQLite only | Counting, aggregation | "How many days was I blocked in Jan?" |
| **B** — ChromaDB only | Pure semantic search | "Find notes about feeling overwhelmed" |
| **C** — Date-scoped semantic | Date + content question | "What did I work on last month?" |
| **D** — Semantic + enriched | Pattern analysis | "What patterns do you see in my blockers?" |

The router uses pure string matching — no LLM call, instant, deterministic.

---

## Known Gotchas

### ChromaDB date filtering requires integers

ChromaDB's `$gte`/`$lte` operators only accept numeric types. ISO date
strings like `"2026-02-01"` cause a silent failure (0 results, no error).

**Fix:** a `date_int` field (`YYYYMMDD` integer) is stored alongside `date`
in every chunk's metadata. If you see 0 results from date-scoped queries,
run `reindex --rebuild` to backfill any chunks written before this field existed.

### nomic-embed-text L2 distances are not cosine similarity scores

`nomic-embed-text` (768-dim) returns raw L2 distances in the **300–600
range** for typical text pairs — not 0.0–2.0. Any similarity threshold
designed for cosine similarity will silently drop all results.

**Fix:** no threshold filtering is applied. ChromaDB's top-K ranking
handles relevance — lower distance = more similar.

---

## Adding a New Daily Summary (Catch-Up)

If you missed some days and want to add summaries retroactively:

```bash
# 1. Write your summary files
#    daily/public/2026-03-05.md
#    daily/sensitive/2026-03-05.md

# 2. Check what's missing
brain status --detailed

# 3. Ingest the specific files shown as missing
brain ingest --file daily/public/2026-03-05.md --sensitivity public
brain ingest --file daily/sensitive/2026-03-05.md --sensitivity sensitive

# 4. Or just run reindex — it picks up everything new automatically
uv run python scripts/reindex.py
```

---

## Development

```bash
# Run tests
uv run pytest tests/ -v

# Format + lint
uv run ruff format .
uv run ruff check .

# Pre-commit hooks (runs on every git commit)
pre-commit run --all-files
```

---

## Phase Status

| Phase | Status | Description |
|---|---|---|
| Phase 1 | ✅ Complete | Config, ingestion pipeline, CLI foundation |
| Phase 1b | ✅ Complete | Composer, reminder, config-driven scanner |
| Phase 2 | ✅ Complete | SQLite trend queries |
| Phase 3 | ✅ Complete | RAG query engine, router, reindex script |
| Phase 4 | ✅ Complete | Streamlit dashboard |
| Phase 5 | ⏳ Future | Query history, visual sentiment timeline |
