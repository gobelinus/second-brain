# 🧠 Brain Project — Context Document
> Paste this at the start of any new chat to continue building.
> Last updated: 2026-03-18

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
| LLM | Ollama + llama3.1:8b (local) — override in config.local.yaml |
| Embeddings | Ollama + `nomic-embed-text` (local) |
| Vector store | ChromaDB (persistent, file-based) |
| Structured store | SQLite (stdlib, zero-ops) |
| RAG orchestration | LlamaIndex |
| CLI framework | Typer (`brain` command) |
| Dashboard | Streamlit (local only, `brain dashboard`) |
| Data validation | Pydantic v2 (composer structured output) |
| Code quality | ruff (format + lint) |
| Security | pre-commit + detect-secrets + custom scanner |

---

## Project Structure

```
brain/
├── .git/
├── .gitignore                     ← sensitive/, store/, inputs/, launchd/ ignored
├── .pre-commit-config.yaml        ← ruff + detect-secrets + custom scanner
├── .secrets.baseline              ← detect-secrets baseline
├── .env.example                   ← safe template, .env is git ignored
├── .streamlit/
│   └── config.toml                ← ✅ telemetry off, localhost only, light theme
├── pyproject.toml                 ← uv managed, brain CLI entrypoint
├── uv.lock                        ← tracked, reproducible installs
├── config.yaml                    ← ALL settings here, no hardcoded values
├── config.local.yaml              ← git ignored, machine-specific overrides
├── dashboard.py                   ← ✅ BUILT — Streamlit 4-tab dashboard
├── todo.txt                       ← rolling todo.txt (appended by composer)
├── README.md                      ← ✅ BUILT — full project docs
├── DAILY_WORKFLOW.md              ← ✅ BUILT — human-facing daily ops guide
│
├── daily/
│   ├── public/                    ← ✅ git tracked
│   │   └── YYYY-MM-DD.md
│   └── sensitive/                 ← 🚫 git ignored
│       └── YYYY-MM-DD.md
│
├── blogs/
│   ├── public/                    ← ✅ git tracked — published posts
│   │   └── YYYY-MM-DD-slug.md
│   └── sensitive/                 ← 🚫 git ignored — drafts
│       └── YYYY-MM-DD-slug-draft.md
│
├── inputs/                        ← 🚫 git ignored (raw daily inputs)
│   ├── YYYY-MM-DD/                ← drop files here for composer
│   │   ├── ai_summary.md
│   │   ├── ai_summary.extracted.json   ← per-file LLM cache (auto-generated)
│   │   ├── meetings.md
│   │   ├── meetings.extracted.json
│   │   └── notes.md
│   └── archive/                   ← processed inputs moved here after compose
│
├── exports/
│   ├── public/                    ← ✅ git tracked
│   ├── sensitive/                 ← 🚫 git ignored
│   ├── reminders/                 ← daily reminder markdown files
│   └── todos/                     ← dated per-day todo backups (future)
│
├── store/                         ← 🚫 git ignored (generated)
│   ├── chroma/
│   └── brain.db
│
├── scripts/
│   ├── __init__.py
│   ├── scan_public_summaries.py   ← ✅ pre-commit hook, config-driven
│   ├── daily_reminder.py          ← ✅ launchd entry point
│   ├── reindex.py                 ← ✅ BUILT — full corpus rebuild script
│   └── anonymize.py               ← ✅ BUILT — redact sensitive data for sharing
│
├── launchd/                       ← 🚫 git ignored — macOS launchd plist
│   └── com.brain.reminder.plist
│
└── src/brain/
    ├── __init__.py
    ├── config.py                  ← ✅ BUILT — BrainConfig typed accessors
    ├── structurer.py              ← ✅ BUILT — LLM metadata extraction
    ├── chunker.py                 ← ✅ BUILT — TextChunker + Embedder
    ├── ingest.py                  ← ✅ BUILT — SQLite + ChromaDB pipeline
    ├── blog_ingest.py             ← ✅ BUILT — blog-specific ingest (ChromaDB only)
    ├── exporter.py                ← ✅ BUILT — markdown export utility
    ├── composer.py                ← ✅ BUILT — two-pass Pydantic structured output
    ├── reminder.py                ← ✅ BUILT — notification + launchd management
    ├── trend.py                   ← ✅ BUILT — SQLite aggregation queries
    ├── router.py                  ← ✅ BUILT — QueryRouter, four patterns
    ├── query.py                   ← ✅ BUILT — RAGEngine, ChromaDB + Ollama
    └── cli.py                     ← ✅ BUILT — all commands wired
```

---

## CLI Commands

```bash
# ── Daily workflow ────────────────────────────────────────────────────────────
brain compose --date 2026-03-18                    # LLM combine inputs → sensitive summary
brain compose --date 2026-03-18 --ingest           # compose + auto-ingest
brain compose --date 2026-03-18 --dry-run          # preview without writing

brain ingest --file daily/public/2026-03-18.md --sensitivity public
brain ingest --file daily/sensitive/2026-03-18.md --sensitivity sensitive
brain ingest --file blogs/public/2026-03-18-slug.md --sensitivity public

# ── Query ─────────────────────────────────────────────────────────────────────
brain query "What was I blocked on last month?"
brain query "What patterns do you see in my blockers?" --scope sensitive
brain query "Summarise my Azure research" --scope public --export

brain summarize --from 2026-02-01 --to 2026-02-25
brain summarize --from 2026-01-01 --to 2026-01-31 --scope public --export

# ── Trend ─────────────────────────────────────────────────────────────────────
brain trend --field projects
brain trend --field sentiment --from 2026-02-01
brain trend --field blockers --from 2026-01-01 --to 2026-01-31 --export
brain trend --field focus_areas --export

# ── Anonymize ─────────────────────────────────────────────────────────────────
brain anonymize daily/sensitive/2026-03-18.md         # redact → stdout
brain anonymize daily/sensitive/2026-03-18.md | pbcopy # redact → clipboard
brain anonymize daily/sensitive/2026-03-18.md --diff   # show changed lines
brain anonymize daily/sensitive/2026-03-18.md --output /tmp/safe.md

# ── Status + maintenance ──────────────────────────────────────────────────────
brain status                    # SQLite + ChromaDB store stats
brain status --detailed         # per-date on-disk vs ingested comparison

uv run python scripts/reindex.py               # incremental reindex
uv run python scripts/reindex.py --rebuild     # wipe + full re-ingest (prompts confirmation)
uv run python scripts/reindex.py --dry-run     # preview only
uv run python scripts/reindex.py --path daily/public  # single directory

# ── Dashboard ─────────────────────────────────────────────────────────────────
brain dashboard                 # open Streamlit UI in browser (port 8501)
brain dashboard --port 8502 --no-browser

# ── Reminder ─────────────────────────────────────────────────────────────────
brain remind                    # send reminder now (test mode)
brain remind --install          # generate + load launchd plist
brain remind --uninstall        # unload + remove plist
brain remind --status           # show schedule + last run info
```

---

## Key Design Decisions

### Storage — Dual Store with Composite Key

```
SQLite (brain.db)                    ChromaDB
─────────────────                    ────────
Structured metadata                  Embeddings + chunks
Fast filter / aggregate              Semantic similarity search
PRIMARY KEY (date, sensitivity)      IDs: {sensitivity}_{date}_chunk_{N}
```

**Why composite PK:** Public and sensitive summaries for the same date
must coexist as independent records. Single `date` PK silently overwrites.

**`date_int` in ChromaDB metadata:** ChromaDB `$gte`/`$lte` operators only
accept numeric types — ISO date strings fail silently with 0 results.
Every chunk stores both:
- `date` — display string `"2026-03-18"`
- `date_int` — integer `20260318` — used for all range filters

### Query Router — Four Patterns

```
Pattern A: SQLite only            → "how many days blocked in Jan?"
Pattern B: ChromaDB only          → "find notes about feeling overwhelmed"
Pattern C: SQLite first→ChromaDB  → "what blocked me in January?"
Pattern D: ChromaDB first→SQLite  → "what patterns in my blockers?"
```

Router uses pure string analysis — no LLM call, instant, deterministic.
Pattern A is checked first (cheapest). Pattern D last (most expensive).
Month names are handled by the date extractor, not trigger phrases —
they must not overlap or routing breaks.

### Composer — Two-Pass Pydantic Pipeline

```
Pass A: one LLM call per input file (small context, focused extraction)
        → writes <stem>.extracted.json cache alongside each input file
        → cache is reused on re-run unless source file is newer than cache
        → delete a .extracted.json to force re-extraction of that file

Pass B: one LLM call to merge per-file JSON summaries (tiny context)
        → falls back to _python_merge() if LLM call fails

Python renders final markdown from validated DailySummary Pydantic object.
Model never touches dates, headers, or formatting.
```

Why two-pass: llama3.1:8b with 3,000+ word mixed input loses content
reliably. Individual files (500–1,200 words) stay within the model's
effective attention range.

### Sensitivity Model

```
public    → git tracked, scanner enforced, safe to share
sensitive → git ignored, local only, never leaves machine
```

Scanner evaluation order: allowlist → private_domains → blocklist.
Allowlist checked first to prevent false positives on technical terms.
`critical`/`high` severity blocks commit. `warning` logs only.

### Config Layering

```
config.yaml        ← base, git tracked, no secrets
config.local.yaml  ← machine overrides, git ignored (e.g. llm_model)
.env               ← secrets only, git ignored
```

---

## Metadata Schema

Extracted by local LLM from every daily summary:

```json
{
  "date": "2026-03-18",
  "sensitivity": "public",
  "projects": ["Brain Project", "Secrets Migration"],
  "blockers": ["PR #101236 pending review"],
  "people": ["Naveen Kumar M"],
  "sentiment": "productive",
  "focus_areas": ["RAG", "ChromaDB", "Pydantic"],
  "raw_file": "daily/public/2026-03-18.md"
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

Query array fields with `json_each()`:
```sql
SELECT date FROM daily_summaries, json_each(projects)
WHERE json_each.value LIKE '%autoscaling%'
ORDER BY date DESC
```

---

## Phase Delivery Status

```
Phase 1   ✅ COMPLETE
──────────────────────────────────────────────────────────
  config.py       — BrainConfig typed accessors, deep merge
  structurer.py   — Ollama/Mistral metadata extraction
  chunker.py      — TextChunker + Embedder
  ingest.py       — SQLiteStore + ChromaStore + IngestPipeline
  blog_ingest.py  — BlogIngestPipeline (ChromaDB only, no SQLite)
  exporter.py     — dated markdown export, sensitivity routing
  cli.py          — ingest, status commands
  pre-commit      — ruff, detect-secrets, public scanner
  Seed data       — initial public + sensitive entries ingested

Phase 1b  ✅ COMPLETE
──────────────────────────────────────────────────────────
  composer.py     — two-pass Pydantic structured output
                    DailySummary schema + mood normalisation validator
                    per-file .extracted.json cache (stale-aware)
                    _rescue_nested() for nested-by-source JSON
                    _python_merge() fallback if LLM merge fails
  reminder.py     — macOS notification, launchd plist management
  scripts/daily_reminder.py — launchd entry point
  cli.py update   — brain compose, brain remind commands
  config.yaml     — scanner, composer, reminder, blog, anonymizer sections

Phase 2   ✅ COMPLETE
──────────────────────────────────────────────────────────
  trend.py        — TrendEngine (SQLite aggregations, json_each)
                    TrendFormatter (markdown output)
  cli.py update   — brain trend command wired

Phase 3   ✅ COMPLETE
──────────────────────────────────────────────────────────
  router.py       — QueryRouter, four patterns (A/B/C/D)
                    pure string analysis, relative date extraction
                    month name trap avoided (date extractor vs trigger phrases)
  query.py        — RAGEngine, ChromaDB + Ollama/Mistral
                    QueryResult.to_markdown() for --export
                    date_int filter, no similarity threshold
  cli.py update   — brain query + brain summarize wired
  scripts/reindex.py — incremental / --rebuild / --dry-run
                       exit code 1 on any failure, confirmation prompt on rebuild

Phase 4   ✅ COMPLETE
──────────────────────────────────────────────────────────
  dashboard.py    — Streamlit 4-tab dashboard:
                    Tab 1: Daily Entries — date cards, sentiment emoji, scope filter
                    Tab 2: Blockers — Plotly horizontal bar + timeline table
                    Tab 3: Action Items — todo.txt parser, priority/project filters
                    Tab 4: Query — RAGEngine wired, 5-item session history
                    @st.cache_data on all SQLite queries
  .streamlit/config.toml — telemetry off, localhost only, light theme
  cli.py update   — brain dashboard, brain status --detailed
  README.md       — full project documentation
  DAILY_WORKFLOW.md — daily operations guide with 7-step checklist

Post Phase 4  ✅ COMPLETE
──────────────────────────────────────────────────────────
  scripts/anonymize.py — Anonymizer class, _print_stats, _print_diff
                         Three bugs fixed vs first draft:
                           - _redaction_stats() no longer creates second BrainConfig()
                           - people replacement uses \b word boundary (not str.replace)
                           - people count uses re.findall with word boundary
                         Enhancements added:
                           - --diff flag shows changed lines to stderr
                           - redaction_stats() is instance method returning dict
                           - .json added to supported file extensions
  cli.py update   — brain anonymize command wired
                    --diff flag, --output flag for file writing
```

---

## ⚠️ Hard-Won Lessons

### ChromaDB — Date Filtering Requires Integers

`$gte`/`$lte` operators only accept numeric types. ISO strings fail silently —
query returns 0 results with no exception.

```python
# WRONG — silently returns 0 results
where={"date": {"$gte": "2026-02-01"}}

# CORRECT — YYYYMMDD integer preserves sort order
where={"date_int": {"$gte": 20260201}}
```

After adding `date_int` to chunk metadata, run `reindex --rebuild` to backfill.

### ChromaDB — L2 Distances Are NOT in 0.0–2.0 Range

`nomic-embed-text` (768-dim) returns raw L2 distances in the **300–600 range**.
A similarity threshold of 1.4 silently drops every result.

```python
# WRONG — kills all results
if dist <= 1.4:
    all_chunks.append(chunk)

# CORRECT — trust ChromaDB top-K, no threshold
all_chunks.append(chunk)
```

### Composer — LLMs Ignore Format Instructions Under Long Context

`llama3` and `llama3.1:8b` with 3,000+ word prompts reliably fail in
one of four ways:

| Failure | Symptom | Fix |
|---|---|---|
| Prose output | "Here is a summary..." despite `format="json"` | system role + `format="json"` + concrete filled-in example |
| Nested-by-source JSON | `{"standup": {"blockers": [...]}}` | `_rescue_nested()` — merge all nested dicts |
| Empty fields | Valid JSON with all `[]` | Two-pass: one LLM call per file (~800 words), then merge |
| `mood_reason` missing | Pydantic validation error | Explicit fallback in both extract + merge steps |

### Typer CliRunner

`mix_stderr=False` is not supported. Always use `result.output` not
`result.stdout` in tests.

---

## Pending Items

### Backlog (prioritised)

| Priority | Item | Effort | Notes |
|---|---|---|---|
| Medium | Sentiment timeline chart | Small | Data in SQLite. Line chart of mood over time. ~30 lines Plotly in `dashboard.py`. |
| Medium | Query history persistence | Medium | `query_history` SQLite table. Expose in dashboard Tab 4 alongside session history. |
| Medium | Global date filter in dashboard | Small | Sidebar `date_from`/`date_to` picker driving all four tabs simultaneously. |
| Low | Blog posts 2–7 | Per post | All outlined in `BRAIN_BLOG_CONTEXT.md`. Post 2 (dual-store) closest to ready. |
| Low | CLI tests for query/summarize | Medium | Typer CliRunner. Note: `mix_stderr=False` unsupported, use `result.output`. |

---

## Blog Series Status

Series: **Building a Private Second Brain with Local LLMs**
Brief for every post in `BRAIN_BLOG_CONTEXT.md`.

| # | Title | Status |
|---|---|---|
| 1 | Why I Stopped Trusting SaaS with My Work Notes | ✅ Written |
| 2 | Designing the Dual-Store Architecture | ⏳ Ready to draft |
| 3 | The Metadata Extractor: Getting Structure from Unstructured Notes | ⏳ Ready to draft |
| 4 | Two ChromaDB Bugs That Took Hours to Find | ⏳ Ready to draft |
| 5 | Building the Query Router: Four Patterns, Zero LLM Calls | ⏳ Ready to draft |
| 6 | The Reindex Script: Operational Tooling That Scales | ⏳ Ready to draft |
| 7 | What I'd Do Differently (Retrospective) | ⏳ After 6 months use |

Post 1 location: `blogs/public/2026-03-18-why-i-stopped-trusting-saas-with-my-work-notes.md`

---

## Git Log

```
feat: brain anonymize CLI command — --diff, --output flags
feat: scripts/anonymize.py — Anonymizer class, word-boundary people redaction
fix: anonymize _redaction_stats — eliminate second BrainConfig(), use self._cfg
fix: anonymize people replacement — re.sub word boundary replaces str.replace
feat: update BRAIN_PROJECT_CONTEXT.md — reflect true state as of 2026-03-18
feat: blog post 1 — Why I Stopped Trusting SaaS with My Work Notes
fix: composer _json NameError — all _json. replaced with json. (module-level import)
feat: composer two-pass pipeline with per-file .extracted.json cache
feat: composer _rescue_nested — handles nested-by-source and single-wrapper JSON
refactor: composer — Pydantic DailySummary structured output replaces markdown prompting
fix: composer mood_reason missing field — fallback in _extract_single_file + _call_llm_two_pass
feat: Phase 4 complete — Streamlit dashboard (4 tabs), brain dashboard, brain status --detailed
feat: README.md + DAILY_WORKFLOW.md
feat: Phase 3 complete — RAG query engine, reindex script, brain query + summarize wired
fix: ChromaDB date filter — requires date_int (YYYYMMDD int), not ISO date strings
fix: remove similarity threshold — nomic-embed-text L2 distances are 300–600 not 0–2
feat: Phase 2 complete — trend.py SQLite aggregations, brain trend wired
feat: Phase 1b complete — composer.py, reminder.py, brain compose + remind commands
feat: Phase 1 complete — ingest, structurer, config, exporter, CLI, pre-commit hooks
```

Tags: `v0.1.0-phase1` · `v0.2.0-phase2` · `v0.3.0-phase3` · `v0.4.0-phase4`

---

## How to Continue in a New Chat

Paste this document, then say:

```
"Add sentiment timeline to dashboard"  ← Plotly line chart, ~30 lines
"Add query history persistence"        ← SQLite table + dashboard Tab 4
"Add global date filter to dashboard"  ← sidebar picker, all tabs
"Write blog post 2"                    ← dual-store architecture
"Write blog post 3"                    ← metadata extractor deep-dive
"Write blog post 4"                    ← ChromaDB bugs war story
```

Build one thing at a time. Commit at each checkpoint.
