# 🧠 Brain — Daily Workflow Guide

> What is expected from you each day, why it matters, and exactly how to do it.
> This is the human-facing companion to the automated morning reminder.

---

## The Daily Loop in 60 Seconds

```
Morning reminder fires at 09:00 (or any time based on your settings)
    ↓
Check for docs/ai-session-summary-prompts.md and get ai summaries
from other ai tools
    ↓
Drop raw notes into inputs/YYYY-MM-DD/
    ↓
brain compose --date YYYY-MM-DD --ingest    ← sensitive summary + todos
    ↓
Write daily/public/YYYY-MM-DD.md            ← sanitised version
brain ingest --file ... --sensitivity public
    ↓
(optional) Ingest blog post if written today
    ↓
brain status --detailed                     ← verify all clean
    ↓
git add + git commit                        ← public files only
    ↓
Review todo.txt action items

```

Total active time: ~10–15 minutes.
Most of the work is done by the LLM — your job is to drop the notes and review the output.

---

## Step 1 — Drop Your Notes

**Folder:** `inputs/YYYY-MM-DD/`

```bash
mkdir -p inputs/2026-03-11
```

The composer reads every `.md` and `.txt` file in this folder and merges them into
a single structured sensitive summary. You can drop as many or as few files as you have.

### What files to drop

| File | What goes in it | Required? |
|---|---|---|
| `ai_summary.md` | AI-generated summary from your internal tool | Recommended |
| `standup.md` | Standup / team sync notes | If applicable |
| `meetings.md` | Meeting notes, key decisions, action items | If applicable |
| `notes.md` | Ad-hoc thoughts, links, references, observations | Optional |
| anything else | Any `.md` or `.txt` file you want included | Optional |

**The more context you drop, the richer the composed summary.** The LLM is instructed
to merge — not duplicate — so overlapping content across files gets consolidated cleanly.

### What the AI summary should contain

If your internal AI tool generates a daily summary, configure it to produce output in
this format for best results with the composer:

```
# Daily Summary — YYYY-MM-DD (Sensitive)
## Focus Areas
## Projects
## Key Takeaways
## Meetings
## Blockers
## People / Teams
## Mood
## References
```

The composer uses this as the base and enriches it with your other input files.

---

## Step 2 — Compose + Ingest Sensitive Summary

```bash
brain compose --date 2026-03-11 --ingest
```

**What this does:**
1. Reads all files from `inputs/2026-03-11/`
2. Calls local Mistral via Ollama to merge them into a structured summary
3. Validates the output has all required sections (fails loudly if the LLM drifted)
4. Writes `daily/sensitive/2026-03-11.md`
5. Extracts action items → appends to `todo.txt` in `todo.txt` format
6. Archives `inputs/2026-03-11/` → `inputs/archive/2026-03-11/`
7. Ingests into SQLite (metadata) + ChromaDB (embeddings)

**The sensitive summary is for you alone.** It stays git-ignored and never leaves your
machine. Write freely — internal names, ticket numbers, salary discussions, HR topics,
honest opinions about meetings — all fine here.

### If something looks wrong

Preview without writing or archiving:
```bash
brain compose --date 2026-03-11 --dry-run
```

Ingest separately after reviewing:
```bash
brain compose --date 2026-03-11           # writes file, no ingest
brain ingest --file daily/sensitive/2026-03-11.md --sensitivity sensitive
```

---

## Step 3 — Write + Ingest Public Summary

The public summary is a **sanitised, shareable version** of your day.
It lives in `daily/public/` and is git-tracked — treat it as content
you'd be comfortable sharing with your team or publishing on your blog.

**File:** `daily/public/2026-03-11.md`

### What to include / exclude

| Include | Exclude |
|---|---|
| Technologies you worked with | Internal team names (use roles: "a PM", "the infra team") |
| Decisions and rationale | Internal Jira/Confluence URLs (anonymise ticket refs) |
| Technical blockers and how you resolved them | Salary, compensation, HR discussions |
| Learning and observations | Proprietary system names or architecture details |
| Open source libraries, tools, patterns | Private domains, internal API endpoints |

### Run the scanner before committing

The pre-commit hook runs automatically on `git commit`, but you can run it early:
```bash
pre-commit run scan-public-summaries --files daily/public/2026-03-11.md
```

Severity levels:
- `critical` / `high` → will block the commit, must fix
- `warning` → logged but commit allowed — review manually

### Ingest the public summary

```bash
brain ingest --file daily/public/2026-03-11.md --sensitivity public
```

This stores it in the `brain_public` ChromaDB collection — queryable via
`brain query --scope public` and visible in the dashboard's Daily Entries tab.

---

## Step 4 — Blog Post (optional)

If you wrote or updated a blog post today, ingest it so it's queryable alongside
your daily notes.

### New public post

```bash
# Save to
blogs/public/2026-03-11-my-post-slug.md

# Scanner check
pre-commit run scan-public-summaries --files blogs/public/2026-03-11-my-post-slug.md

# Ingest
brain ingest --file blogs/public/2026-03-11-my-post-slug.md --sensitivity public
```

### Draft / sensitive post

```bash
# Save to (git-ignored)
blogs/sensitive/2026-03-11-my-draft.md

# Ingest (no scanner check needed — sensitive)
brain ingest --file blogs/sensitive/2026-03-11-my-draft.md --sensitivity sensitive
```

Blog posts are auto-detected by their path and routed to separate ChromaDB
collections (`blog_public`, `blog_sensitive`) — they don't mix with your daily summaries.

### Blog metadata extracted automatically

The ingest pipeline calls the local LLM to extract:
```json
{
  "title": "My Post Title",
  "slug": "my-post-slug",
  "date": "2026-03-11",
  "tags": ["rag", "chromadb", "observability"],
  "status": "draft",
  "url": null
}
```

---

## Step 5 — Verify Everything

```bash
brain status --detailed
```

Expected output for a clean day:
```
DATE           ON DISK                  INGESTED                 STATUS
──────────────────────────────────────────────────────────────────────────
2026-03-11     public + sensitive       public + sensitive       ✅ up to date
2026-03-10     public + sensitive       public + sensitive       ✅ up to date
```

If you see `⚠️ needs ingest`, the command prints the exact `brain ingest` command to fix it.

---

## Step 6 — Commit Public Files

Only public files get committed. Sensitive files, store/, and inputs/ are all git-ignored.

```bash
git add daily/public/2026-03-11.md
git add blogs/public/                   # if you wrote a blog post today
git add exports/public/                 # if you used --export on any command
git status                              # double-check nothing sensitive is staged
git commit -m "data: daily summary 2026-03-11"
```

The pre-commit hooks run automatically:
- **ruff** — format + lint check on any Python files staged
- **detect-secrets** — credential pattern scan
- **scan-public-summaries** — your custom scanner on any `.md` files in `daily/public/` or `blogs/public/`

---

## Step 7 — Review Action Items

The composer extracted action items from your input files and appended them to `todo.txt`:

```bash
# See today's items
grep -A 30 "^# 2026-03-11" todo.txt
```

Or open the dashboard:
```bash
brain dashboard
# → Action Items tab → filter by date: 2026-03-11
```

Priority guide:
- **(A) Urgent** — do today or first thing tomorrow
- **(B) Important** — schedule this week
- **(C) Low priority** — backlog, review weekly

---

## Catching Up on Missed Days

If you missed a day or several:

```bash
# 1. Check what's missing
brain status --detailed

# 2. Write your summaries retroactively
#    daily/public/2026-03-08.md
#    daily/sensitive/2026-03-08.md

# 3. Ingest each one
brain ingest --file daily/public/2026-03-08.md --sensitivity public
brain ingest --file daily/sensitive/2026-03-08.md --sensitivity sensitive

# 4. Or use reindex to pick up everything at once
uv run python scripts/reindex.py
```

You don't need to use `brain compose` for missed days if you're writing the
summary directly — compose is for the multi-file input workflow only.

---

## Weekly Habits

These aren't daily but worth doing once a week:

```bash
# Trend review — what did you work on this week?
brain trend --field projects --from 2026-03-04 --to 2026-03-11

# Sentiment check — how was your week emotionally?
brain trend --field sentiment --from 2026-03-04 --to 2026-03-11

# Blockers — what kept coming up?
brain trend --field blockers --from 2026-03-04 --to 2026-03-11

# Week summary via RAG
brain summarize --from 2026-03-04 --to 2026-03-11 --export

# Review and close out (A) action items from todo.txt
```

---

## Querying Your Brain

Any time — not just during the daily workflow:

```bash
# Natural language — semantic + structured
brain query "What was I blocked on last month?"
brain query "When did I last work on cost optimisation?"
brain query "Summarise my Azure work in February"
brain query "What patterns do you see in my blockers?" --scope sensitive

# Date range summary
brain summarize --from 2026-02-01 --to 2026-02-28

# Structured counts
brain trend --field people --from 2026-01-01    # who did I work with most?
brain trend --field focus_areas                  # what technologies dominated?
```

---

## Reminder System

The morning reminder fires at 09:00 daily via macOS launchd.

```bash
brain remind --install    # set up the 09:00 schedule
brain remind --status     # check it's loaded + see recent reminder files
brain remind              # trigger manually (test mode)
brain remind --uninstall  # remove the schedule
```

Each reminder writes a checklist to `exports/reminders/YYYY-MM-DD_reminder.md`
with all steps pre-filled with today's date and yesterday's ingest status.

---

## File Ownership Summary

| Location | Sensitivity | Git | Purpose |
|---|---|---|---|
| `daily/public/` | Public | ✅ tracked | Sanitised daily summaries — shareable |
| `daily/sensitive/` | Sensitive | 🚫 ignored | Full daily summaries — local only |
| `blogs/public/` | Public | ✅ tracked | Published / ready-to-publish posts |
| `blogs/sensitive/` | Sensitive | 🚫 ignored | Drafts with internal context |
| `inputs/YYYY-MM-DD/` | Sensitive | 🚫 ignored | Raw daily inputs for composer |
| `inputs/archive/` | Sensitive | 🚫 ignored | Processed inputs (auto-archived) |
| `exports/public/` | Public | ✅ tracked | `--export` outputs from public queries |
| `exports/sensitive/` | Sensitive | 🚫 ignored | `--export` outputs from sensitive queries |
| `exports/reminders/` | Sensitive | 🚫 ignored | Daily reminder checklists |
| `todo.txt` | Sensitive | 🚫 ignored | Rolling action items |
| `store/` | Sensitive | 🚫 ignored | ChromaDB + SQLite (auto-generated) |

---

## Troubleshooting

**`brain compose` fails with "LLM output does not follow required structure"**
The model ignored the format template. Options:
1. Try `--dry-run` to see what it actually returned
2. Switch to a larger model in `config.local.yaml`: `ollama.llm_model: llama3`
3. Simplify your input files — very long inputs sometimes cause drift

**Query returns "No relevant notes found"**
- Check `brain status --detailed` — is the date actually ingested?
- Run `uv run python scripts/reindex.py` to ensure nothing was missed
- If you recently added `date_int` to the schema, run `reindex --rebuild` to backfill

**Scanner blocking a commit with a false positive**
Add the pattern to `scanner.allowlist` in `config.yaml`:
```yaml
scanner:
  allowlist:
    - "my-safe-pattern"
```

**Ollama is slow (>60 seconds per compose)**
Normal for Mistral 7B on CPU. If it's consistently timing out, increase the timeout:
```yaml
# config.local.yaml
ollama:
  timeout_seconds: 300
```
Or switch to a faster model locally: `ollama pull llama3.2:3b`
