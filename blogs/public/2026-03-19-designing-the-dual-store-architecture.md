# Designing the Dual-Store Architecture

*Part 2 of [Building a Private Second Brain with Local LLMs](#)*

*Previously: [Why I Stopped Trusting SaaS with My Work Notes](#)*

---

Most RAG tutorials use one store. You embed your documents, put the vectors in ChromaDB or Pinecone, and query semantically. It's clean, it's easy to explain, and it works well for the use cases those tutorials demonstrate.

It doesn't work well for what I was trying to build.

The moment I tried to answer "how many days in January did I have a blocker?" I had a problem. Vector search doesn't count. It doesn't group by date. It doesn't tell you that "waiting on infra approval" showed up eleven times in Q1. For those questions, you need SQL — and SQL needs a relational store.

So I use two stores. Here's why, how the schema is designed, and the routing logic that decides which store handles each query.

---

## Why Two Stores

The two stores aren't redundant — they're complementary. Each answers a different class of question.

```
SQLite (brain.db)       → structured metadata, filters, aggregation
ChromaDB                → embeddings, semantic similarity, full text
```

**SQLite answers structured questions:**

- How many days did I work on X?
- What's my sentiment distribution for the last quarter?
- Which projects appear most in my blockers?
- List every day I mentioned a specific person.

These are counting and filtering problems. They have exact answers. `GROUP BY`, `COUNT`, `json_each()` — SQL handles them in milliseconds with zero model calls.

**ChromaDB answers semantic questions:**

- Find notes about feeling overwhelmed.
- What was I working on when I mentioned the incident?
- Surface anything related to cost optimisation, even if I didn't use those exact words.

These are similarity problems. The answer isn't a row in a table — it's the closest chunk of text to a query embedding. Only a vector store can answer them.

The thing that makes this architecture interesting is what falls between those two extremes — questions that need both. "What blocked me in January?" is a semantic question (what counts as a blocker?) scoped by a date range (January). You need SQL to establish the date bounds, then ChromaDB to find the semantically relevant content within those bounds. More on this when I get to the query router.

---

## The Schema Decision: Why json_each()

Every daily summary produces a metadata record. Here's what that record looks like:

```json
{
  "date": "2026-03-15",
  "sensitivity": "public",
  "projects": ["Platform Migration", "Autoscaling"],
  "blockers": ["Waiting on infra approval", "Unclear requirements from PM"],
  "people": ["Alice", "Bob"],
  "sentiment": "productive",
  "focus_areas": ["SRE", "AWS", "Incident Response"],
  "raw_file": "daily/public/2026-03-15.md"
}
```

The array fields — `projects`, `blockers`, `people`, `focus_areas` — are the interesting design decision. The normalised relational approach would be separate tables: a `projects` table, a `blockers` table, junction tables to link them to daily entries. That's the right answer for a production database serving multiple applications.

It's the wrong answer here. Separate tables mean schema migrations when fields change. They mean JOIN complexity for every query. They mean more surface area to maintain.

Instead, those fields are stored as JSON arrays in SQLite text columns:

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

SQLite's `json_each()` function lets you query into those arrays without normalising them:

```sql
-- Which dates mention the Autoscaling project?
SELECT date FROM daily_summaries, json_each(projects)
WHERE json_each.value LIKE '%Autoscaling%'
ORDER BY date DESC

-- How often does each blocker appear?
SELECT json_each.value AS blocker, COUNT(*) AS occurrences
FROM daily_summaries, json_each(blockers)
GROUP BY json_each.value
ORDER BY occurrences DESC
```

You get the query power of normalisation without the schema overhead. For a single-user system where the schema is stable and the data volume is modest, this is the right trade-off.

---

## The Composite Primary Key

The most important line in that schema is the last one:

```sql
PRIMARY KEY (date, sensitivity)
```

Here's why that's not obvious.

My system has two sensitivity levels: `public` (git-tracked, shareable) and `sensitive` (git-ignored, local only). On any given day, I might ingest both — a public summary I'm comfortable committing, and a sensitive one with meeting context and opinions I'd rather keep private.

If the primary key were just `date`, the second ingest would silently overwrite the first. You'd never get an error. The public record would disappear and be replaced by the sensitive one, or vice versa. A bug that would only surface when you noticed missing data weeks later.

The composite key `(date, sensitivity)` makes public and sensitive records for the same date independent rows that coexist cleanly:

```
date        sensitivity   sentiment   projects
──────────  ───────────   ─────────   ────────────────────────
2026-03-15  public        productive  ["Autoscaling", "SRE"]
2026-03-15  sensitive     productive  ["Autoscaling", "SRE", "Incident Review"]
```

The same principle applies in ChromaDB. Every chunk ID is prefixed with sensitivity:

```python
ids = [
    f"{metadata['sensitivity']}_{metadata['date']}_chunk_{i}"
    for i in range(len(chunks))
]
# e.g. public_2026-03-15_chunk_0
#      sensitive_2026-03-15_chunk_0
```

Without that prefix, re-ingesting the sensitive summary would overwrite the public chunks for the same date. Same silent corruption, different store.

---

## The Sensitivity Model

Public and sensitive aren't security classifications — they're workflow classifications.

```
public    → git tracked, scanner enforced, safe to commit and share
sensitive → git ignored, stays on this machine, never committed
```

The scanner enforces this at the git boundary. Before any commit, it checks public summary files against a blocklist: phone numbers, email addresses, internal domains, compensation terms, HR language. Anything that looks like it shouldn't be in a public git repo blocks the commit:

```python
# Evaluation order matters:
# 1. allowlist  → if match → SKIP (technical terms that look sensitive but aren't)
# 2. private_domains → if match → BLOCK (always critical)
# 3. blocklist  → severity-aware: critical/high block, warning logs
```

The allowlist runs first. Without it, `localhost` and `example.com` would be flagged as internal domains. Technical notes are full of strings that pattern-match to things that look sensitive but aren't.

The practical effect of the model: I write my sensitive summaries without filtering myself — real names, real opinions, internal ticket numbers, meeting context. The public summaries are the ones I'd be comfortable with anyone reading. Both get ingested, both are queryable, and the sensitivity boundary is enforced structurally rather than by discipline.

---

## The Four Query Patterns

Once you have two stores, you need a router — something that decides which store handles each query. My router has four patterns:

```
Pattern A: SQLite only
  → aggregate / counting questions
  → "how many days did I have a blocker in January?"
  → "what's my sentiment distribution for Q1?"

Pattern B: ChromaDB only
  → pure semantic questions with no date constraint
  → "find notes about feeling overwhelmed"
  → "surface anything related to cost optimisation"

Pattern C: SQLite first → ChromaDB
  → date range known, semantic content needed
  → "what blocked me last month?"
  → SQLite extracts the date bounds, ChromaDB searches within them

Pattern D: ChromaDB first → SQLite
  → semantic pattern first, then structured enrichment
  → "what patterns do you see in my blockers?"
  → ChromaDB finds relevant chunks, SQLite enriches with metadata
```

The router uses pure string analysis — no model call, no embedding, no latency. It looks for aggregate trigger phrases ("how many", "count", "which days"), date signals ("last month", "in January", "this week"), and analytical signals ("patterns", "trend", "over time"):

```python
# Pattern A wins if aggregate phrases present — cheapest, check first
if any(phrase in question for phrase in sqlite_trigger_phrases):
    return PatternA

# Pattern D if date signal AND analytical signal
if date_signal and analytical_signal:
    return PatternD

# Pattern C if date signal alone
if date_signal:
    return PatternC

# Default: pure semantic search
return PatternB
```

The ordering is a deliberate cost decision. Pattern A is checked first because it's the cheapest — no embedding, no ChromaDB query, just SQL. Pattern D is checked last because it's the most expensive — embedding the question, querying ChromaDB, then enriching with SQLite. On tie ("how many days last month?" contains both aggregate and date signals), the cheaper pattern wins.

One subtle trap: month names like "in January" initially ended up in the aggregate trigger phrase list. That caused questions like "what blocked me in January?" to route to Pattern A instead of Pattern C, because "January" was triggering the SQLite-only path. The fix was to handle month names in the date extractor only, never in trigger phrases. The two systems must not overlap.

---

## The date_int Problem

There's one thing about the ChromaDB integration that bit me and is worth documenting explicitly.

ChromaDB's `$gte` and `$lte` metadata filter operators only accept numeric types. If you pass an ISO date string, the query runs without error and returns zero results. No exception, no warning — just silence.

```python
# WRONG — runs without error, returns nothing
where={"date": {"$gte": "2026-01-01"}}

# CORRECT — YYYYMMDD integer preserves sort order
where={"date_int": {"$gte": 20260101}}
```

Every chunk stores both fields:

```python
chunk_metadata = {
    "date":     "2026-03-15",       # for display
    "date_int": 20260315,           # for range filters
    ...
}
```

I found this by querying a date range I knew had data, getting zero results, and spending longer than I'd like to admit ruling out every other possible cause before isolating the filter type. The ChromaDB documentation mentions it, but not prominently enough that I caught it before building.

If you're adding date range filtering to ChromaDB and you're not getting results you expect, check this first.

---

## What This Looks Like End to End

To make this concrete — here's the full path from a question to an answer:

**"What blocked me in January?"**

1. Router detects "January" → date signal → no aggregate phrases → **Pattern C**
2. Router extracts date bounds: `2026-01-01` → `2026-01-31`, converts to `date_int`: `20260101` → `20260131`
3. Question is embedded via `nomic-embed-text` (local Ollama call)
4. ChromaDB queried with embedding + `date_int` range filter → top-K chunks returned
5. SQLite queried for structured metadata (blockers array) for the same date range
6. Context assembled: chunks + structured metadata
7. Ollama/Mistral generates answer from context + question
8. Result returned with source dates, pattern used, chunks retrieved

**"How many days did I have a blocker in January?"**

1. Router detects "how many" → aggregate phrase → **Pattern A**
2. SQLite query: `SELECT COUNT(DISTINCT date) FROM daily_summaries, json_each(blockers) WHERE date BETWEEN '2026-01-01' AND '2026-01-31' AND json_each.value != ''`
3. Result formatted as structured answer — no model call, instant

The two questions feel similar. The architecture treats them completely differently, and correctly so.

---

## What I'd Change

The dual-store approach is holding up well. If I were starting again, the one thing I'd do differently is storing `date_int` from day one instead of discovering it was necessary after I'd ingested several weeks of data and had to rebuild the ChromaDB collections.

The composite primary key I'd keep exactly as-is. The `json_each()` approach I'd keep. The four routing patterns have proven to cover the actual distribution of questions I ask — I haven't needed a fifth.

The sensitivity model is the decision I feel best about. Being able to write honestly without self-censoring, while still having a queryable public layer for sharing context with colleagues, is exactly the workflow I wanted. The structural enforcement — git tracking, scanner hooks, separate ChromaDB collections — means the boundary holds without requiring discipline to maintain.

---

*Next: [Post 3 — Getting Structure from Unstructured Notes](#)*

*Previously: [Post 1 — Why I Stopped Trusting SaaS with My Work Notes](#)*

*Tags: rag, chromadb, sqlite, architecture, local-llm, python, knowledge-management*
