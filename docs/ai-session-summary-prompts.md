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
Before this conversation ends, summarise our entire session today for my
personal knowledge system.

CRITICAL RULE: Only include a section if there is real content from our
conversation to put in it. If a section does not apply to what we actually
discussed, OMIT IT ENTIRELY — do not write the heading, do not write
"N/A", do not write "None". A missing section is correct. A fabricated
or placeholder section is not.

Output ONLY the markdown below, starting immediately with the heading.
No preamble. No "here is your summary". Just the content.

---

# Claude Session Notes — [TODAY'S DATE]

## What We Worked On
(include only if we worked on something concrete — specific tasks,
problems, or questions we tackled together)

## Key Decisions Made
(include only if actual decisions or technical choices were made —
with brief reasoning if it came up)

## Things I Learned / Insights
(include only if genuinely new concepts, approaches, or perspectives
came up — not restatements of what I already knew)

## Code / Solutions Produced
(include only if real code was written, commands were run, or concrete
technical outputs were produced — describe what it does, language/tech used)

## Problems Still Open
(include only if something was explicitly left unresolved or deferred)

## Action Items
(include only if there are concrete follow-up tasks — use todo.txt style:
(A) = urgent, (B) = important, (C) = nice to have)

## Topics / Technologies Referenced
(include only if specific tools, frameworks, services, or technical
concepts came up that are worth capturing)

## Mood / Energy
(always include — one word: productive / energized / neutral /
frustrated / overwhelmed, then one sentence explaining why)
```

---

## GEMINI — End of Session Prompt

Paste this at the end of your Gemini conversation for the day:

```
Before we finish, summarise our entire conversation today for my
personal knowledge system.

CRITICAL RULE: Only include a section if there is real content from our
conversation to put in it. If a section does not apply to what we actually
discussed, OMIT IT ENTIRELY — do not write the heading, do not write
"N/A", do not write "None". A missing section is correct. A fabricated
or placeholder section is not.

Output ONLY the markdown, starting immediately with the heading.
No introduction. No commentary. Just the content.

---

# Gemini Session Notes — [TODAY'S DATE]

## What We Worked On
(include only if we worked on something concrete — specific tasks,
problems, or questions we tackled together)

## Key Decisions Made
(include only if actual decisions or technical choices were made —
with brief reasoning if it came up)

## Things I Learned / Insights
(include only if genuinely new concepts, approaches, or perspectives
came up — not restatements of what I already knew)

## Code / Solutions Produced
(include only if real code was written, commands were run, or concrete
technical outputs were produced — describe what it does, language/tech used)

## Problems Still Open
(include only if something was explicitly left unresolved or deferred)

## Action Items
(include only if there are concrete follow-up tasks — use todo.txt style:
(A) = urgent, (B) = important, (C) = nice to have)

## Topics / Technologies Referenced
(include only if specific tools, frameworks, services, or technical
concepts came up that are worth capturing)

## Mood / Energy
(always include — one word: productive / energized / neutral /
frustrated / overwhelmed, then one sentence explaining why)
```

---

## GENERIC — Works with any AI tool

For ChatGPT, Copilot, Perplexity, or any other tool you use:

```
Before this conversation ends, summarise our entire session today for
my personal notes.

CRITICAL RULE: Only include a section if there is real content from our
conversation to put in it. If a section does not apply to what we actually
discussed, OMIT IT ENTIRELY — do not write the heading, do not write
"N/A", do not write "None". A missing section is correct. A fabricated
or placeholder section is not.

Output ONLY the markdown. Start immediately with the heading.
No preamble. No "sure, here is your summary". Just the content.

---

# [TOOL NAME] Session Notes — [TODAY'S DATE]

## What We Worked On
(include only if we worked on something concrete)

## Key Decisions Made
(include only if actual decisions were made — with reasoning)

## Things I Learned / Insights
(include only if genuinely new knowledge came up)

## Code / Solutions Produced
(include only if real code or concrete technical output was produced)

## Problems Still Open
(include only if something was left unresolved or deferred)

## Action Items
(include only if there are concrete follow-up tasks —
(A) urgent / (B) important / (C) nice to have)

## Topics / Technologies Referenced
(include only if specific tools, frameworks, or concepts came up)

## Mood / Energy
(always include — one word: productive / energized / neutral /
frustrated / overwhelmed, then one sentence explaining why)
```

---

## Tips

**Mood / Energy is the only mandatory section.** Everything else is
omitted if it doesn't apply. A session that was purely exploratory
discussion might only have "What We Worked On" and "Mood". That's correct.

**"CRITICAL RULE" wording is intentional.** LLMs respond to strong
framing. Without it, the model interprets the section template as a form
to fill in, and hallucinates content to complete empty fields. The rule
reframes sections as conditional — only emit what actually happened.

**Don't clean up Action Items before saving.** Leave them rough — the
composer's todo extraction prompt will format them into todo.txt syntax.

**One file per tool is the right granularity.** Don't merge Claude and
Gemini outputs yourself — drop both files and let the composer LLM do it.

**If a session was trivial, skip it.** A session about asking for a recipe
or debugging a trivial typo doesn't need a summary. The value is in
capturing sessions where you actually thought through something.

**The date in the filename matters.** The composer reads all files from
`inputs/YYYY-MM-DD/` — use today's date consistently.

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
