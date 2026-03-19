# Why I Stopped Trusting SaaS with My Work Notes

*Part 1 of [Building a Private Second Brain with Local LLMs](#series-roadmap)*

---

Every tool that promises to help you think requires you to hand over your thoughts.

Notion. Obsidian Sync. Roam Research. Reflect. The pitch is always the same: capture everything, find it later, let AI surface patterns you'd miss. It sounds like a senior engineer's dream — a queryable record of every decision, blocker, and breakthrough, available from any device, forever.

The small print is where it gets interesting.

Your notes leave your machine the moment you hit sync. They sit on someone else's servers, under someone else's terms of service, powering someone else's model training pipeline — unless you've read the policy carefully enough to opt out of the right checkbox in the right settings panel. And even if you have, you're one acquisition, one pricing change, or one bankruptcy away from losing access to years of accumulated context.

I decided I was done with that trade-off. Here's what I built instead, and why each decision matters.

---

## The Problem With Convenience

I used to keep my daily work notes in Notion. It was genuinely good — flexible, searchable, cross-device. Then three things happened in quick succession that changed my mind.

First, I tried to query across a year of notes in a structured way. Notion's search is full-text, not semantic. You can filter by property, but the moment you want "show me every day I was blocked on infra work in Q4" you're either manually tagging everything retroactively or you're disappointed. The data was there. The ability to ask questions of it wasn't.

Second, I noticed I was self-censoring. Certain meeting notes — the ones with real opinions about team dynamics, or blunt assessments of architectural decisions — I wasn't writing down because I didn't fully trust where they'd end up. That's a significant cost. The most useful notes are often the ones you'd only write in a private journal.

Third, I looked at what I was actually paying for and what I was getting. A SaaS subscription to store markdown files and run full-text search on them. Files I could store in git for free. Search I could replace with something far more powerful if I owned the stack.

The calculus shifted. The convenience wasn't worth the privacy compromise, the lock-in, or the capability ceiling.

---

## Privacy as an Engineering Constraint

Most engineers think about privacy as a compliance concern — something the security team worries about, addressed with checkboxes and data classification policies. That framing misses something important.

Privacy is an engineering constraint, the same way latency or cost is an engineering constraint. And like latency or cost, ignoring it early means paying for it later.

For personal knowledge tooling specifically, privacy has a concrete and underappreciated cost: **you write less honestly when you know the audience is uncertain**. The most useful thing a second brain can do is let you think out loud without filtering yourself. That only works if you fully trust where your thoughts go.

The decision I made was to treat "no data leaves this machine" as a hard requirement — not a nice-to-have. Every subsequent technical decision follows from that constraint.

---

## The Local-First Stack

Here's what running fully local actually looks like in practice, and why I picked each component.

### LLM: Ollama + Mistral 7B (not the OpenAI API)

The OpenAI API would have made this faster to prototype. The quality at GPT-4 would have been better than Mistral 7B. I chose Ollama anyway.

The reasons are layered. There's no API key, which means no credentials to rotate, leak, or have compromised. There's no telemetry — Ollama has a flag for this (`anonymized_telemetry=False`) and I verified it. There's no network call, which means the system works offline on a plane or in a building with blocked ports. And there's no usage bill that scales with how much I write.

Mistral 7B is good enough for the tasks I'm asking it to do: extract structured metadata from markdown, answer questions about a few retrieved chunks of text, summarise a week of activity. It's not GPT-4. It doesn't need to be.

The practical constraint that pushed me toward Ollama was a simpler one: I wanted to be able to demo this to a colleague on a laptop with no internet connection and have it work. That requirement eliminates every cloud API instantly.

### Vector Store: ChromaDB (not Pinecone or Weaviate)

The RAG tutorials you find online mostly use Pinecone. It's a managed vector database with a generous free tier and a clean Python client. It would have been the easy choice.

The problem with Pinecone for this use case isn't the product — it's the architecture. Your embeddings live in Pinecone's infrastructure. When you query your notes, that query (and the retrieved chunks) pass through their servers. For public notes this might be fine. For the sensitive half of the system, it's a non-starter.

ChromaDB runs as a persistent, file-based store. The entire vector database is a directory on disk. No server to run, no port to expose, no cloud account to manage. It starts when your Python process starts and persists between runs. Zero ops, truly — not "zero ops until it isn't."

### Structured Store: SQLite (not Postgres)

Vector search answers semantic questions well. It answers aggregate questions poorly.

"Find notes about feeling overwhelmed" — ChromaDB, obviously.

"How many days in January did I have a blocker? What projects show up most in my blockers? What's my sentiment trend over the last quarter?" — these are SQL questions. They need counts, GROUP BY, date range filters.

I needed a structured store alongside the vector store. The choice of which structured store was easy: SQLite is in Python's standard library, the entire database is a single file, and it handles everything I need at the scale of one person's daily notes. Postgres would have been operationally heavier for zero benefit.

The one interesting design decision was using `json_each()` to query array fields:

```sql
SELECT date, json_each.value AS project
FROM daily_summaries, json_each(projects)
WHERE json_each.value LIKE '%autoscaling%'
ORDER BY date DESC
```

Projects, blockers, people, and focus areas are stored as JSON arrays in SQLite text columns. This lets me keep the schema simple while still being able to explode and aggregate over individual array items. It's not the normalised relational approach — it's the pragmatic one.

### File Format: Markdown (not anything else)

This one isn't a technical decision — it's a bet about the future.

Markdown has been a stable, widely-supported format for fifteen years. It renders in GitHub, in VS Code, in every static site generator, in your terminal with any pager. You can read it in a text editor when every other piece of software has been deprecated. You can grep it. You can version-control it meaningfully with git diff.

Notion's export format, Roam's JSON export, Obsidian's proprietary plugins — all of these are risks. The moment a company pivots, gets acquired, or changes pricing, your ability to get your data out in a useful form becomes uncertain.

Every note in this system is a plain markdown file that I can open with any text editor today, in five years, or in twenty. That durability is worth more than any proprietary feature.

---

## What "Local-First" Actually Means

"Local-first" has become a marketing term. Apps slap it on their landing page while still syncing everything to a central server by default.

What it means in practice for this system:

**The LLM inference runs on your CPU/GPU.** No network call, no API key, no usage tracking. Ollama pulls the model once and runs it locally forever.

**The vector store is a directory on disk.** ChromaDB's persistent mode is a folder called `store/chroma/`. You can zip it, move it, back it up with rsync. It has no server process.

**The structured store is a single file.** `store/brain.db` is a SQLite database. You can open it with the `sqlite3` CLI, inspect it with any SQLite browser, or query it from any language with a SQLite driver.

**The notes themselves are in git.** Public summaries are committed and tracked. Sensitive summaries are in `.gitignore` and never leave the machine. You get full version history, meaningful diffs, and the ability to recover any previous version.

There is no sync. There is no cloud. There is nothing to subscribe to. The system works as long as your laptop works.

---

## The Trade-offs I'm Accepting

I'm not pretending this is free. Local-first has real costs.

**No mobile access.** If I want to capture a note on my phone, I use Apple Notes, then move it to the brain later. This is a workflow friction I've accepted. It forces me to batch my note-taking into a daily sit-down rather than fragmenting it across the day, which turns out to be a feature.

**Model quality ceiling.** Mistral 7B is good. GPT-4 is better. For the tasks I'm asking — structured extraction, retrieval-augmented QA, date-range summarisation — the quality difference is smaller than you'd expect. But it's there.

**Setup cost.** Getting Ollama running, pulling models, wiring together ChromaDB and SQLite, building the ingestion pipeline — this took real time. There's no installer. If you want this system, you have to build it.

I think those trade-offs are worth it for a tool that stores honest, sensitive, professionally useful information. Your calculus may differ.

---

## Series Roadmap {#series-roadmap}

This is the first post in a series documenting the complete build. Here's what's coming:

**Post 2 — Designing the Dual-Store Architecture**
Why we use two stores (SQLite + ChromaDB) instead of one, the four query patterns the router decides between, and the composite primary key that lets public and sensitive summaries for the same day coexist cleanly.

**Post 3 — Getting Structure from Unstructured Notes**
How a local LLM extracts metadata from markdown chaos: temperature=0, enforced vocabulary, output validation before storage, and what happens when the model ignores your instructions.

**Post 4 — Two ChromaDB Bugs That Took Hours to Find**
`$gte` doesn't work with strings. nomic-embed-text L2 distances are in the 300–600 range, not 0–2. Both silently returned zero results. Here's how we found them.

**Post 5 — Building the Query Router Without an LLM**
Four retrieval patterns, pure string analysis, no model call for routing. Why deterministic heuristics beat an LLM router for this task.

**Post 6 — The Reindex Script: Operational Tooling That Scales**
Incremental, rebuild, and dry-run modes. Exit codes that compose with cron. Why failed files shouldn't abort the run.

**Post 7 — Retrospective**
Six months of daily use. What held up, what didn't, what I'd change.

---

If you're building something similar, or if you've made different trade-offs and want to argue about them — I'm interested. The decisions in this series are real ones made under real constraints, not toy examples constructed to make a point.

The code is what it is because the requirements are what they are.

---

*Next: [Post 2 — Designing the Dual-Store Architecture](#)*

*Tags: local-llm, rag, privacy, knowledge-management, ollama, chromadb, sqlite, python*
