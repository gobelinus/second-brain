# Brain — Daily AI Session Summary Prompts

Use these at the end of each day in the respective tool.
Copy the output, save as a `.md` file, drop into `inputs/YYYY-MM-DD/`.
Then run: `brain compose --date YYYY-MM-DD --ingest`

The composer merges all files in that folder — one file per tool is fine,
multiple files from the same tool are also fine. Just drop and compose.

---

## How the workflow fits together

```
End of day
  ├── Claude.ai  → paste prompt → copy output → inputs/YYYY-MM-DD/claude.md
  ├── Gemini     → paste prompt → copy output → inputs/YYYY-MM-DD/gemini.md
  └── [other]    → paste prompt → copy output → inputs/YYYY-MM-DD/[tool].md
                                                          ↓
                                         brain compose --date YYYY-MM-DD --ingest
                                                          ↓
                                         local LLM merges all files
                                                          ↓
                                         daily/sensitive/YYYY-MM-DD.md
                                                          ↓
                                         ingested into SQLite + ChromaDB
```

Nothing new is sent to the AI tools — they already have this session.
You are pulling value out of interactions that already happened.

---

## CLAUDE.AI — End of Session Prompt

Paste this at the end of your Claude conversation for the day:

```
Before this conversation ends, I need you to summarise our entire session
today for my personal knowledge system.

Review everything we discussed in this conversation and produce a structured
markdown summary. Be specific — include actual technical details, decisions
made, problems solved, and things learned. Do not be vague or generic.

Output ONLY the markdown below, starting immediately with the heading.
No preamble. No "here is your summary". Just the content.

---

# Claude Session Notes — [TODAY'S DATE]

## What We Worked On
[Bullet list — specific tasks, problems, questions we tackled together]

## Key Decisions Made
[Bullet list — technical or design choices made during this session,
with the reasoning if it came up]

## Things I Learned / Insights
[Bullet list — new concepts, approaches, or perspectives from this session]

## Code / Solutions Produced
[Brief description of any code written, commands run, or concrete outputs.
Include language/technology. No need to paste the full code — describe what it does.]

## Problems Still Open
[Bullet list — anything unresolved, deferred, or that needs follow-up]

## Action Items
[Bullet list — concrete things I should do next, in todo.txt style if possible:
(A) = urgent, (B) = important, (C) = nice to have]

## Topics / Technologies Referenced
[Bullet list — tools, frameworks, services, concepts that came up]

## Mood / Energy
[One word: productive / energized / neutral / frustrated / overwhelmed]
[One sentence: why]
```

---

## GEMINI — End of Session Prompt

Paste this at the end of your Gemini conversation for the day:

```
Before we finish, please summarise our entire conversation today for my
personal knowledge system.

Go back through everything we discussed and produce a structured markdown
summary. Be specific and detailed — include actual technical content,
decisions, problems solved, and things learned. Avoid generic descriptions.

Output ONLY the markdown, starting immediately with the heading.
No introduction. No commentary. Just the content.

---

# Gemini Session Notes — [TODAY'S DATE]

## What We Worked On
[Bullet list — specific tasks, problems, questions we tackled together]

## Key Decisions Made
[Bullet list — technical or design choices made during this session,
with the reasoning if it came up]

## Things I Learned / Insights
[Bullet list — new concepts, approaches, or perspectives from this session]

## Code / Solutions Produced
[Brief description of any code written, commands run, or concrete outputs.
Include language/technology. No need to paste the full code — describe what it does.]

## Problems Still Open
[Bullet list — anything unresolved, deferred, or that needs follow-up]

## Action Items
[Bullet list — concrete things I should do next, in todo.txt style if possible:
(A) = urgent, (B) = important, (C) = nice to have]

## Topics / Technologies Referenced
[Bullet list — tools, frameworks, services, concepts that came up]

## Mood / Energy
[One word: productive / energized / neutral / frustrated / overwhelmed]
[One sentence: why]
```

---

## GENERIC — Works with any AI tool

For ChatGPT, Copilot, Perplexity, or any other tool you use:

```
Before this conversation ends, please summarise our entire session today
for my personal notes.

Review everything we discussed and produce a structured markdown summary.
Be specific — include actual technical details, real decisions made, concrete
problems solved, and genuine insights. Not vague descriptions.

Output ONLY the markdown below. Start immediately with the heading.
No preamble. No "sure, here is your summary". Just the content.

---

# [TOOL NAME] Session Notes — [TODAY'S DATE]

## What We Worked On
[Bullet list — specific tasks and problems]

## Key Decisions Made
[Bullet list — choices and reasoning]

## Things I Learned / Insights
[Bullet list — new knowledge from this session]

## Code / Solutions Produced
[What was built or solved — language, purpose, approach]

## Problems Still Open
[Unresolved questions or deferred items]

## Action Items
[Next steps — (A) urgent / (B) important / (C) nice to have]

## Topics / Technologies Referenced
[Tools, frameworks, concepts that came up]

## Mood / Energy
[One word from: productive / energized / neutral / frustrated / overwhelmed]
[One sentence: why]
```

---

## Tips

**Be honest in the Mood field.** The whole point of the brain project is
that it reflects what actually happened, not a sanitised version. The
sentiment trending over time is only useful if it's accurate.

**Don't clean up Action Items before saving.** Leave them rough — the
composer's todo extraction prompt will format them into todo.txt syntax.
If they're already clean that's fine too.

**One file per tool is the right granularity.** Don't try to merge Claude
and Gemini outputs yourself — drop both files and let the composer LLM do it.
That's what it's for.

**If a session was trivial, say so.** A session about asking for a recipe
or debugging a trivial typo doesn't need a summary. Skip it. The value is
in capturing the sessions where you actually thought through something.

**The date in the filename matters.** The composer reads all files from
`inputs/YYYY-MM-DD/` — whatever date you use in the folder name is the
date the summary gets stored under. Use today's date.

---

## Example daily inputs folder

```
inputs/2026-03-19/
  ├── claude.md      ← pasted from Claude session summary
  ├── gemini.md      ← pasted from Gemini session summary
  └── standup.md     ← manually written meeting notes (optional)
```

Then:
```bash
brain compose --date 2026-03-19 --ingest
```

The composer reads all three, merges them via local LLM, writes
`daily/sensitive/2026-03-19.md`, extracts action items to `todo.txt`,
archives the inputs, and ingests into SQLite + ChromaDB.
