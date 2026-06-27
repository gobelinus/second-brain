# 🧠 Brain Project — Phase 5 Build Plan
> Todo Completion Suggestions + Task Enrichment via MCP
> Created: 2026-03-31

---

## Overview

Phase 5 has two sub-phases with a clean dependency boundary.

```
Phase 5a — Todo Matcher (self-contained, build first)
  Open todos + today's summary → LLM → suggested completions
  Wired into brain compose — no new command needed

Phase 5b — Task Enrichment via MCP (depends on 5a for todo IDs)
  Any task ID → Jira/Confluence MCP + local RAG → task brief
  Local LLM assembles — private notes never leave the machine
```

5a can ship and be used daily while 5b is being built.
5b requires todo IDs from 5a to link enrichment back to tracked items.

---

## Phase 5a — Todo Matcher

### What it does

After every successful `brain compose --ingest`, scan open todos against
today's summary and surface candidates that look addressed:

```
🧠 Brain Composer — compose complete

✅ Compose complete
   Next: brain ingest --file daily/sensitive/2026-03-31.md --sensitivity sensitive

🔍 Checking open todos against today's summary...
   3 items may have been addressed today:

   (A) id:a3f2 2026-03-15 Fix hotfix approval for kkumar +Anvil @review
   (B) id:b7c1 2026-03-17 Write RFC for autoscaling limits +Autoscaling @docs
   (C) id:c2d8 2026-03-20 Send weekly summary to team +Comms @team

   Review and close with: brain todo --close a3f2
   Or edit todo.txt directly: x 2026-03-31 (was: your item text)
```

Nothing is auto-closed. The human decides.

---

### Step 1 — Add id: tags to todo extraction

**File:** `config.yaml` — `composer.todo_prompt_template`

Add to the extraction prompt instructions:

```
- Append a unique 4-character alphanumeric ID tag to each item: id:xxxx
  Generate the ID as a short hash of the item text (first 8 chars of
  sha1 of the description text, lowercased). This makes IDs stable —
  the same item re-extracted gets the same ID.
- If a Jira ticket number is mentioned anywhere in the source notes
  (e.g. PLAT-1234, ENG-42), append it as a tag: jira:PLAT-1234
- If a Confluence page is referenced, append: conf:PAGE-TITLE-SLUG

Example output:
(A) 2026-03-31 Fix hotfix approval for kkumar +Anvil @review id:a3f291 jira:PLAT-1234
(B) 2026-03-31 Write RFC for autoscaling +Autoscaling @docs id:b7c142 conf:autoscaling-rfc
```

**Why sha1-based IDs:** deterministic — re-running extraction on the same
text produces the same ID. No UUID randomness means duplicate detection
works and no id:xxxx drift if you re-compose a day.

**Why id: at creation, not later:**
- Layer 2 enrichment links back to this ID
- Avoids a migration on a flat text file later
- todo.txt spec allows freeform `key:value` tags — fully compatible

---

### Step 2 — Build TodoMatcher class

**File:** `src/brain/todo_matcher.py`

```python
class TodoMatcher:
    """
    Compares open action items in todo.txt against a daily summary
    and returns items that appear to have been addressed today.

    Conservative by design — false negatives preferred over false positives.
    Never mutates todo.txt. Returns suggestions only.
    """

    def __init__(self, config: BrainConfig | None = None):
        ...

    def match(self, summary_content: str, date: str) -> list[TodoMatch]:
        """
        Load open todos, compare against summary, return candidates.

        Returns list of TodoMatch(item, confidence, evidence_snippet).
        Only items with HIGH confidence are returned — conservative threshold.
        """
        open_todos = self._load_open_todos()
        if not open_todos:
            return []
        return self._call_llm(open_todos, summary_content, date)

    def _load_open_todos(self) -> list[str]:
        """Read todo.txt, return lines that are not completed (no 'x ' prefix)."""
        ...

    def _call_llm(self, todos, summary, date) -> list[TodoMatch]:
        """
        Single Ollama call. Conservative prompt — explicit evidence required.
        Returns empty list on LLM failure (non-blocking).
        """
        ...
```

**TodoMatch dataclass:**
```python
@dataclass
class TodoMatch:
    raw_line: str        # full todo.txt line
    todo_id: str         # extracted id:xxxx value, or ""
    jira_ref: str        # extracted jira:TICKET, or ""
    evidence: str        # LLM's one-sentence reason
    confidence: str      # "high" | "medium" — only "high" shown by default
```

**LLM prompt shape (conservative):**
```
You are reviewing open action items against today's work summary.
For each action item, decide: does today's summary provide EXPLICIT
evidence this was completed or addressed?

Rules:
- Only mark HIGH confidence if the summary explicitly describes work
  matching the action item — a matching project AND completion language
- Mark MEDIUM if there is indirect evidence (project mentioned but
  no clear completion)
- Mark NONE if there is no evidence
- When uncertain, choose the lower confidence level
- False negatives are fine. False positives are not.

Return ONLY a JSON array. No preamble. No commentary.
[{"line": "...", "confidence": "high|medium|none", "evidence": "..."}]
```

---

### Step 3 — Wire into composer.py

**File:** `src/brain/composer.py`

After successful compose (and optional ingest), call TodoMatcher:

```python
# Step 6: Check open todos against today's summary (non-blocking)
if not dry_run:
    print("  [6/6] Checking open todos against today's summary...")
    self._suggest_completions(output_path, date)
```

```python
def _suggest_completions(self, summary_path: Path, date: str) -> None:
    """
    Run TodoMatcher against today's composed summary.
    Prints suggestions to stdout. Never blocks compose on failure.
    """
    try:
        from brain.todo_matcher import TodoMatcher
        matcher = TodoMatcher(self.config)
        content = summary_path.read_text(encoding="utf-8")
        matches = matcher.match(content, date)

        if not matches:
            print("        No open todos appear addressed today.")
            return

        print(f"        {len(matches)} item(s) may have been addressed:")
        for m in matches:
            print(f"        {m.raw_line}")
            print(f"          → {m.evidence}")
        print()
        print("        Review and close: brain todo --close <id>")
        print("        Or edit todo.txt: x {date} (was: description)")

    except Exception as e:
        print(f"        ⚠️  Todo matching failed (non-blocking): {e}")
```

**Key design:** wrapped in try/except — a failed todo match never blocks
the compose workflow. Same pattern as `_extract_todos()`.

---

### Step 4 — Add brain todo command to cli.py

```bash
brain todo --list              # show all open todos
brain todo --list --all        # show open + completed
brain todo --close a3f291      # mark item as done: x YYYY-MM-DD ...
brain todo --suggest           # run matcher against most recent summary
```

`--close` writes `x YYYY-MM-DD ` prefix per todo.txt spec.
`--suggest` is a standalone way to re-run matching without composing.

---

### Phase 5a Deliverables

```
src/brain/todo_matcher.py      ← TodoMatcher class, TodoMatch dataclass
src/brain/composer.py update   ← _suggest_completions() wired in step 6
src/brain/cli.py update        ← brain todo command
config.yaml update             ← id:/jira:/conf: tags in todo extraction prompt
```

---

## Phase 5b — Task Enrichment via MCP

### What it does

```bash
brain task --enrich PLAT-1234
brain task --enrich id:a3f291
brain task --enrich PLAT-1234 --ingest   # save brief to inputs/today/
```

Output:
```
🧠 Brain — Task Enrichment

Task: PLAT-1234 — Fix hotfix approval for kkumar

── External Context ──────────────────────────────────
Jira PLAT-1234
  Status    : In Progress
  Assignee  : kkumar
  Priority  : High
  Created   : 2026-03-10
  Comments  : 3 (last: "approval blocked on security review")
  Linked PRs: #4521 (open), #4489 (merged)

Confluence: Platform Hotfix Process (linked from ticket)
  Summary   : [2-3 sentence extract of relevant section]

── Brain Context ─────────────────────────────────────
Mentions in ingested notes: 4 days
Last worked on            : 2026-03-28
Related blockers          : "waiting on infra approval" (×2),
                            "security review pending" (×1)
Sentiment on those days   : frustrated (3), neutral (1)
People involved           : kkumar, Alice (security), Bob (infra)
Related chunks            : [top 3 semantic matches]

── Brief ─────────────────────────────────────────────
[Local LLM synthesises external + brain context into 1-page brief]
```

---

### Architecture

```
brain task --enrich PLAT-1234
    │
    ├── Detect source type
    │     PLAT-1234  → Jira
    │     ENG-42     → Jira
    │     id:a3f291  → look up in todo.txt → extract jira: tag if present
    │
    ├── Pull external context (MCP)
    │     Jira MCP  → ticket details, comments, linked PRs
    │     Conf MCP  → linked pages (graceful degradation if unavailable)
    │
    ├── Pull brain context (local)
    │     SQLite → dates mentioning ticket ID, people, blockers, sentiment
    │     ChromaDB → top-K semantic chunks for ticket ID + title
    │
    ├── Assemble brief (local Ollama)
    │     System: "You are assembling a task brief from external + personal context"
    │     User:   [external context] + [brain context] + "Produce a brief"
    │     → markdown brief
    │
    └── Optional --ingest
          Write to inputs/YYYY-MM-DD/task-PLAT-1234.md
          brain compose --date YYYY-MM-DD picks it up in next run
```

**Privacy boundary:** local LLM handles assembly. External data and private
notes never meet on a cloud server.

---

### Step 1 — MCP client wrapper

**File:** `src/brain/mcp_client.py`

```python
class MCPClient:
    """
    Thin wrapper around MCP tool calls for Jira and Confluence.
    Returns structured dicts — callers don't need to know MCP details.
    Fails gracefully — all methods return None on any MCP error.
    """

    def get_jira_ticket(self, ticket_id: str) -> dict | None:
        """Return ticket details, comments, linked PRs. None on failure."""
        ...

    def get_confluence_page(self, page_id_or_url: str) -> dict | None:
        """Return page title + relevant sections. None on failure."""
        ...
```

**MCP servers to wire (config.yaml):**
```yaml
mcp:
  jira:
    server_url: "https://jira.mcp.yourorg.com/mcp"   # or Atlassian hosted
    enabled: true
  confluence:
    server_url: "https://confluence.mcp.yourorg.com/mcp"
    enabled: true
```

**Graceful degradation pattern:**
```python
jira_data = self.mcp_client.get_jira_ticket(ticket_id)
if jira_data is None:
    print("  ⚠️  Jira MCP unavailable — using local context only")
    # continue with brain context only
```

---

### Step 2 — TaskEnricher class

**File:** `src/brain/task_enricher.py`

```python
class TaskEnricher:
    """
    Assembles a task brief from external MCP sources + local brain context.
    Assembly runs via local Ollama — private notes never leave the machine.
    """

    def enrich(self, task_ref: str) -> TaskBrief:
        """
        task_ref: Jira ticket ID, todo id:xxxx, or free text task description.

        Returns TaskBrief with external context, brain context, and
        LLM-assembled markdown brief.
        """
        ...

    def _resolve_task_ref(self, task_ref: str) -> ResolvedTask:
        """Detect type: jira ticket ID / todo id / free text."""
        ...

    def _pull_brain_context(self, task_ref: str, title: str) -> BrainContext:
        """SQLite metadata + ChromaDB semantic search for this task."""
        ...

    def _assemble_brief(self, external, brain) -> str:
        """Local Ollama call. Returns markdown brief."""
        ...
```

---

### Step 3 — brain task command in cli.py

```python
@app.command()
def task(
    ref: str,              # PLAT-1234, id:a3f291, or free text
    ingest: bool = False,  # --ingest: save brief to inputs/today/
    export: bool = False,  # --export: save to exports/
) -> None:
    """
    Enrich a task with external context (Jira/Confluence) + local brain notes.
    Assembly runs locally — private notes never leave this machine.

    Examples:
        brain task --enrich PLAT-1234
        brain task --enrich PLAT-1234 --ingest
        brain task --enrich id:a3f291
    """
```

---

### Step 4 — Dashboard: Task tab (Tab 6)

Add a new tab to the Streamlit dashboard:

```
🎯  Tasks
  ├── Search: [ticket ID or keyword]  [Enrich →]
  ├── Recent enrichments (from query_history equivalent for tasks)
  └── Brief display with Jira/Confluence/brain sections clearly separated
```

---

### Phase 5b Deliverables

```
src/brain/mcp_client.py        ← MCPClient, Jira + Confluence wrappers
src/brain/task_enricher.py     ← TaskEnricher, TaskBrief dataclass
src/brain/cli.py update        ← brain task --enrich command
config.yaml update             ← mcp: section with server URLs + enabled flags
dashboard.py update            ← Tab 6: Tasks
```

---

## Full Phase 5 Sequence

```
Phase 5a
──────────────────────────────────────────────────────────
  Step 1: config.yaml — id:/jira:/conf: tags in todo prompt
  Step 2: src/brain/todo_matcher.py — TodoMatcher class
  Step 3: src/brain/composer.py — _suggest_completions() wired
  Step 4: src/brain/cli.py — brain todo command
  Checkpoint: test with real todo.txt + real daily summary
  Commit: v0.5a-todo-matcher

Phase 5b
──────────────────────────────────────────────────────────
  Step 1: config.yaml — mcp: section
  Step 2: src/brain/mcp_client.py — MCPClient
  Step 3: src/brain/task_enricher.py — TaskEnricher
  Step 4: src/brain/cli.py — brain task command
  Step 5: dashboard.py — Tab 6 Tasks
  Checkpoint: test with real Jira ticket end-to-end
  Commit: v0.5b-task-enrichment
```

---

## Key Design Decisions (carry to context doc)

| Decision | Choice | Rationale |
|---|---|---|
| Todo completion | Auto-suggest only, never auto-close | False positives are worse than false negatives |
| Todo IDs | sha1-based id:xxxx at extraction time | Deterministic, stable, Layer 2 links back cleanly |
| External ref tags | jira:/conf: in todo.txt | Free field in todo.txt spec, no format change needed |
| MCP vs REST | MCP for all external integrations | One pattern, multiple tools, no bespoke auth per tool |
| Brief assembly | Local Ollama | Private notes must not meet external data on cloud server |
| MCP failure | Graceful degradation to local-only | Partial brief > broken command |
| Brief ingestion | Optional --ingest flag | Brief becomes part of brain, future queries surface it |
| Todo matching failure | Non-blocking (try/except in composer) | Match failure must never block daily compose workflow |
