# 🧠 Brain Project — Blog Series Context
> Living document. Update this as new decisions are made.
> Each entry feeds directly into a blog post draft.
> Last updated: 2026-03-31

---

## Series Overview

**Title:** Building a Private Second Brain with Local LLMs
**Audience:** Senior engineers and architects who are privacy-conscious,
curious about RAG, and skeptical of SaaS knowledge tools.
**Tone:** Practitioner-to-practitioner. Show the decisions and the dead ends,
not just the happy path. "Here's what we built, here's what broke, here's why."
**Format:** Each post is standalone but references the series.
Code snippets are real — taken directly from the project.

---

## Post Roster

### Post 1 — Why I Stopped Trusting SaaS with My Work Notes
**Status:** ✅ Written — `blogs/public/2026-03-18-why-i-stopped-trusting-saas-with-my-work-notes.md`
**Type:** Problem framing / opinion
**Hook:** Every tool that promises to help you think requires you to
hand over your thoughts. Notion, Obsidian Sync, Roam — the moment
your notes leave your machine, they're someone else's training data.

---

### Post 2 — Designing the Dual-Store Architecture
**Status:** ✅ Written — `blogs/public/2026-03-19-designing-the-dual-store-architecture.md`
**Type:** Architecture deep-dive
**Hook:** Most RAG tutorials use one store. We use two — and the reason
tells you something important about the limits of vector search alone.

**Key decisions covered:**
- Composite PK (date + sensitivity) — silent overwrite problem
- Why two stores: SQLite for structured, ChromaDB for semantic
- json_each() for array fields — pragmatic over normalised
- Four query patterns and the router cost ordering
- date_int bug — $gte/$lte only accepts numeric types
- Sensitivity model as workflow classification, not security

---

### Post 3 — The Metadata Extractor: Getting Structure from Unstructured Notes
**Status:** ⏳ Ready to draft
**Type:** Implementation deep-dive
**Hook:** Daily notes are chaos. Meetings, decisions, blockers, half-finished
thoughts — all mixed together. We use a local LLM to extract structure
from this chaos before storing it. Here's what we learned.

**Key decisions to cover:**

#### Temperature = 0 for Extraction
Why: Deterministic JSON. Non-zero risks reformatted fields, renamed keys,
or commentary that breaks parsing.

#### Allowlist Before Blocklist in the Scanner
Why: Without allowlist, "localhost" and "example.com" get flagged.
Short-circuit eliminates false positives for technical terms.

#### Enforced Sentiment Vocabulary
Five fixed values: productive / frustrated / neutral / overwhelmed / energized.
Why: Free-text sentiment can't be GROUP BY'd.

#### LLM Output Validation Before Storage
Why: Fail loud at the boundary, not quietly at query time.

#### Composer Two-Pass Pipeline (add to this post)
Why single-pass failed: llama3.1:8b loses content under 3,000+ word context.
Two-pass: one LLM call per file, then merge. Per-file .extracted.json cache.
_rescue_nested() handles nested-by-source JSON.

---

### Post 4 — Two ChromaDB Bugs That Took Hours to Find
**Status:** ⏳ Ready to draft
**Type:** War story / debugging post
**Hook:** The documentation says $gte works. It does — just not with strings.
And nomic-embed-text distances look nothing like cosine similarity scores.
Here's how our RAG pipeline silently dropped every result, and how we found it.

**Bug 1:** $gte/$lte only accepts numeric types — ISO strings return 0 results silently.
Fix: store date_int (YYYYMMDD int) alongside date string.

**Bug 2:** nomic-embed-text L2 distances are 300–600, not 0–2.
A threshold of 1.4 silently drops everything. Fix: remove threshold, trust top-K.

**Meta-lesson:** Silent failures are the hardest bugs.
Make exception handlers less broad. Build diagnostic tooling early.

---

### Post 5 — Building the Query Router: Four Patterns, Zero LLM Calls
**Status:** ⏳ Ready to draft
**Type:** Implementation deep-dive
**Hook:** Most RAG systems embed the question and search. We do that too —
but only for some questions. For others, SQL is faster, cheaper, and more
accurate. Here's how we route between them without calling an LLM.

**Key decisions to cover:**
- Pure string analysis, no LLM — deterministic, instant, testable
- Pattern A checked first — cheapest wins on tie (deliberate cost decision)
- Relative date extraction without a model call
- The month name trap — date extractor and trigger phrases must not overlap
- Why a heuristic router beats an LLM router for this task

---

### Post 6 — The Reindex Script: Operational Tooling That Scales
**Status:** ⏳ Ready to draft
**Type:** Platform/SRE perspective post
**Hook:** The ingest pipeline handles one file. The reindex script handles
everything. It's the difference between a tool and a system.

**Key decisions to cover:**
- Three modes: incremental / rebuild / dry-run
- Confirmation prompt on destructive operations — speed bump, not security
- Exit code 1 on any failure — composable with cron/CI
- Failed files don't abort the run — fail the file, not the run

---

### Post 7 — Turning Your AI Chat Sessions into a Knowledge Base
**Status:** ⏳ Ready to draft
**Type:** Workflow / practical guide
**Hook:** You already use Claude, Gemini, and ChatGPT every day. Every
session contains decisions made, problems solved, things learned. Most of
it evaporates when you close the tab. Here's how to capture it locally
without sending anything new to the cloud.

**Key ideas to cover:**

#### The insight: reverse data flow
Most people think of AI tools as something they send data to.
This workflow reverses it — ask the AI to summarise what already happened,
pull that summary down to your local brain. Nothing new leaves your machine.

#### The hallucination problem with templates
LLMs treat a structured template as a form to fill in — even when sections
don't apply. "CRITICAL RULE: omit sections entirely if they don't apply"
reframes sections as conditional. The distinction between imperative ("fill
this") and conditional ("include only if") changes model behaviour.

#### How it fits the composer pipeline
AI session summaries drop into inputs/YYYY-MM-DD/ alongside standup notes,
meeting notes, whatever else is there. One brain compose --ingest handles
everything. The local LLM merges all sources.

#### The one always-include section: Mood
Everything else is conditional. Mood is mandatory — it's the signal that
makes sentiment trending meaningful. An honest "frustrated" is more
valuable than a diplomatic "neutral".

---

### Post 8 — Teaching Your Brain to Notice Completed Work
**Status:** ⏳ Draft after Phase 5a complete
**Type:** Implementation deep-dive + design reasoning
**Hook:** A todo list that never gets shorter isn't a productivity tool —
it's a guilt ledger. Here's how we taught the brain to surface action items
that look done, without ever auto-closing anything.

**Key ideas to cover:**

#### Auto-suggest, not auto-close
The system never mutates todo.txt without human confirmation.
"Did I actually finish this?" is a human judgment, not a model call.
False positives (marking something done that isn't) are much worse than
false negatives. Conservative threshold by design.

#### Stable IDs on todos
id:xxxx tag appended at extraction time. Why: Layer 2 task enrichment links
back to this ID. Without it, you'd need a migration on a flat text file.

#### The LLM matching prompt shape
"Only mark as candidate if today's summary contains explicit evidence —
a project match AND description suggesting the work happened.
False negatives are fine. False positives are not."

#### What this enables next
Todo IDs are the bridge to Layer 2 task enrichment. Once a todo has an ID
and an optional jira:TICKET-123 tag, the enrichment pipeline can pull
external context and local brain notes — assembled locally.

---

### Post 9 — Connecting the Brain to Jira and Confluence via MCP
**Status:** ⏳ Draft after Phase 5b complete
**Type:** Architecture + integration deep-dive
**Hook:** The brain knows everything you've written down. Jira knows
everything your team has filed. They've never talked to each other.
Here's how we connected them — locally, without sending your private
notes to a cloud API.

**Key ideas to cover:**

#### Why MCP over bespoke API integration
One integration pattern, multiple tools. Jira MCP, Confluence MCP, Slack MCP
share the same client interface. Bespoke REST integrations mean three auth
flows, three rate limit strategies, three error handling paths. MCP
standardises all of that away.

#### The privacy architecture
External data (Jira, Confluence) is already not private — it's on Atlassian's
servers. Brain notes are private — they stay local. The assembly step is the
critical boundary: local LLM assembly means private notes and external data
never meet on a cloud server.

#### Graceful degradation
MCP server quality varies. Design: if MCP call fails, fall back to local-only
context. A partial brief is better than a broken command.

#### The enrichment brief format
```
Task: PLAT-1234 — Fix hotfix approval

External (Jira):   status, assignee, comments, linked PRs
External (Conf):   linked design doc sections
Brain context:     3 days mention this ticket
                   last worked on: 2026-03-15
                   blockers historically: "waiting on infra approval"
                   sentiment on those days: frustrated (2), neutral (1)
```

#### Ingesting the brief closes the loop
brain task --enrich PLAT-1234 --ingest
The enrichment output lands in inputs/YYYY-MM-DD/ and gets composed into
the day's summary. The brain learns about the task through explicit
enrichment pulls — future queries surface this context automatically.

---

### Post 10 — Retrospective: A Year of Building and Using a Private Second Brain
**Status:** ⏳ Draft after 6 months consistent use
**Type:** Retrospective / lessons learned
**Hook:** The best test of a knowledge system is whether you still use it
six months later. Here's what held up, what didn't, and what I'd build
differently if I started today.

**Placeholder questions to revisit:**
- Was dual-store the right call? (Probably yes)
- Was the sensitivity model worth the complexity? (Almost certainly yes)
- Did the MCP integrations hold up or become maintenance burden?
- What does the actual query pattern distribution look like after 6 months?
- ChromaDB vs alternatives — would you choose differently?
- Was LlamaIndex worth the dependency?
- What would a multi-user version look like?

---

## Living Decisions Log

| Date | Decision | Why | Alternative |
|---|---|---|---|
| Phase 1 | Ollama + Mistral 7B | 100% local, no API key | OpenAI API |
| Phase 1 | ChromaDB persistent | File-based, zero ops | Pinecone, Weaviate |
| Phase 1 | SQLite for metadata | stdlib, zero ops, json_each() for arrays | Postgres |
| Phase 1 | Composite PK (date + sensitivity) | Public + sensitive same date must coexist | Single date PK |
| Phase 1 | Temperature=0 for extraction | Deterministic JSON output | Default temperature |
| Phase 1 | Allowlist before blocklist in scanner | Eliminate false positives for technical terms | Blocklist only |
| Phase 1 | Five fixed sentiment values | Enables GROUP BY trending | Free-text sentiment |
| Phase 1 | LLM output validation before storage | Fail loud at boundary, not at query time | Trust LLM output |
| Phase 2 | json_each() for array field queries | Arrays stored as JSON, need exploding for aggregation | Normalised tables |
| Phase 3 | Four routing patterns, pure string analysis | No LLM call for routing, instant + deterministic | LLM-based router |
| Phase 3 | Pattern A checked first | Cheapest pattern wins on tie | Most specific wins |
| Phase 3 | date_int (YYYYMMDD int) in chunk metadata | ChromaDB $gte/$lte only accepts numeric types | ISO string dates |
| Phase 3 | Removed similarity threshold | nomic-embed-text L2 distances are 300-600, not 0-2 | Threshold of 1.4 |
| Phase 3 | Route() not Run() in reindex | Auto-detects blog vs daily, one entry point | Per-directory logic |
| Phase 3 | Exit code 1 on any reindex failure | Composable with cron/CI/alerting | Always exit 0 |
| Phase 3 | Confirmation prompt on --rebuild | Speed bump for destructive operation | No prompt |
| Phase 4 | Composer two-pass Pydantic pipeline | llama3.1:8b loses content under 3,000+ word context | Single-pass prompt |
| Phase 4 | Per-file .extracted.json cache | Stale-aware, avoids re-extraction on re-run | No cache |
| Phase 4 | _rescue_nested() in composer | llama3 sometimes keys output by source filename | Reject and re-prompt |
| Phase 5 | Auto-suggest only for todo completion | False positives worse than false negatives | Auto-close |
| Phase 5 | id: tags on todos from creation | Layer 2 enrichment needs stable reference, avoid migration | Add IDs later |
| Phase 5 | MCP for external integrations | One pattern for Jira/Confluence/Slack, not bespoke APIs | REST per tool |
| Phase 5 | Local LLM for brief assembly | Private notes must not meet external data on cloud server | Cloud LLM |
| Phase 5 | Enrichment brief ingested into brain | Future queries surface enriched context automatically | Brief is ephemeral |

---

## Blog Infrastructure Notes

### Where Blog Files Live in the Project
```
blogs/
├── public/      ← ✅ git tracked — published posts
│   └── YYYY-MM-DD-slug.md
└── sensitive/   ← 🚫 git ignored — drafts with internal context
    └── YYYY-MM-DD-slug-draft.md
```

### Drafting Workflow
```
1. Write draft in blogs/sensitive/YYYY-MM-DD-slug-draft.md
2. Strip sensitive details → blogs/public/YYYY-MM-DD-slug.md
3. brain ingest --file blogs/public/... --sensitivity public
4. Publish externally — update url field in post frontmatter if desired
```

### Series Status at a Glance
| # | Title | Status |
|---|---|---|
| 1 | Why I Stopped Trusting SaaS with My Work Notes | ✅ Written |
| 2 | Designing the Dual-Store Architecture | ✅ Written |
| 3 | The Metadata Extractor | ⏳ Ready to draft |
| 4 | Two ChromaDB Bugs | ⏳ Ready to draft |
| 5 | Building the Query Router | ⏳ Ready to draft |
| 6 | The Reindex Script | ⏳ Ready to draft |
| 7 | Turning AI Chat Sessions into a Knowledge Base | ⏳ Ready to draft |
| 8 | Teaching Your Brain to Notice Completed Work | ⏳ After Phase 5a |
| 9 | Connecting the Brain to Jira and Confluence via MCP | ⏳ After Phase 5b |
| 10 | Retrospective | ⏳ After 6 months use |
