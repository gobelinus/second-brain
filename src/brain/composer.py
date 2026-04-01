"""
composer.py — Combine multiple daily input files into a single structured summary.

Reads all markdown/text files from inputs/YYYY-MM-DD/, combines them via a
local Ollama LLM call, writes the result to daily/sensitive/YYYY-MM-DD.md,
and optionally archives the input files.

Approach — Structured Output (Pydantic):
  The LLM is asked to return JSON only, matching DailySummary schema.
  Python assembles the markdown from the validated object.
  The model never touches dates, headers, or formatting — it only
  categorises and summarises content. This eliminates all header
  format failures (## vs **, wrong dates, missing sections).

Flow:
  inputs/YYYY-MM-DD/
    ├── ai_summary.md
    ├── meeting_standup.md
    └── notes.md
          │
          ▼  (LLM returns JSON → DailySummary)
          │
          ▼  (Python renders markdown)
  daily/sensitive/YYYY-MM-DD.md
          │
          ▼  (archive)
  inputs/archive/YYYY-MM-DD/
"""

import json
import re
import shutil
from pathlib import Path
from typing import Literal

import ollama
from pydantic import BaseModel, Field, field_validator

from brain.config import BrainConfig

# ── Pydantic Schema ───────────────────────────────────────────────────────────

SentimentType = Literal[
    "productive", "frustrated", "neutral", "overwhelmed", "energized"
]


class DailySummary(BaseModel):
    """
    Structured daily summary extracted from raw input files by the LLM.

    The LLM populates every field via JSON.
    Python owns all structural elements: date, headers, markdown formatting.
    """

    focus_areas: list[str] = Field(
        description="Main focus areas for the day — technologies, themes, disciplines."
    )
    projects: list[str] = Field(description="Projects worked on today.")
    key_takeaways: list[str] = Field(
        description="Most important insights, decisions, and observations from today."
    )
    meetings: list[str] = Field(
        description="Meetings attended with brief context. Empty list if none."
    )
    blockers: list[str] = Field(
        description="Concrete blockers encountered. Empty list if none."
    )
    people: list[str] = Field(
        description="People or teams interacted with. Empty list if none."
    )
    mood: SentimentType = Field(
        description=(
            "Overall mood/sentiment for the day. "
            "Must be one of: productive, frustrated, neutral, overwhelmed, energized."
        )
    )
    mood_reason: str = Field(description="One sentence explaining the mood.")
    references: list[str] = Field(
        description=(
            "Real URLs and ticket numbers only — Jira, Confluence, PRs, Slack threads. "
            "Do NOT include file names or tool notices. Empty list if none."
        )
    )

    @field_validator("mood", mode="before")
    @classmethod
    def normalise_mood(cls, v: str) -> str:
        """
        Normalise mood to a valid sentinel value.
        Maps common variants (e.g. 'very productive', 'PRODUCTIVE') to the
        canonical form. Falls back to 'neutral' if no match found.
        """
        valid = {"productive", "frustrated", "neutral", "overwhelmed", "energized"}
        cleaned = str(v).strip().lower()
        if cleaned in valid:
            return cleaned
        for sentiment in valid:
            if sentiment in cleaned:
                return sentiment
        return "neutral"

    def to_markdown(self, date: str) -> str:
        """
        Render the structured summary as a markdown document.

        Date and all structural elements are injected here — never by the LLM.
        """

        def _bullets(items: list[str], empty: str = "None") -> str:
            if not items:
                return empty
            return "\n".join(f"- {item}" for item in items)

        return f"""\
# Daily Summary — {date} (Sensitive)

## Focus Areas
{_bullets(self.focus_areas)}

## Projects
{_bullets(self.projects)}

## Key Takeaways
{_bullets(self.key_takeaways)}

## Meetings
{_bullets(self.meetings)}

## Blockers
{_bullets(self.blockers)}

## People / Teams
{_bullets(self.people)}

## Mood
{self.mood}
{self.mood_reason}

## References
{_bullets(self.references)}
"""


# ── Composer ──────────────────────────────────────────────────────────────────


class Composer:
    """
    Combines multiple daily input files into a single structured
    sensitive summary using a local Ollama LLM call.

    The LLM returns JSON. Python validates it via Pydantic and renders
    the final markdown. The model never touches dates or formatting.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    def compose(self, date: str, dry_run: bool = False) -> Path:
        """
        Main entry point. Compose a structured summary for the given date.

        Args:
            date:    ISO date string — YYYY-MM-DD
            dry_run: If True, print output but do not write or archive files.

        Returns:
            Path to the written output file (or would-be path on dry_run).

        Raises:
            FileNotFoundError: If inputs directory or files do not exist.
            ValueError:        If date format is invalid or no input files found.
        """
        self._validate_date(date)

        inputs_path = self.config.composer_inputs_path(date)
        output_path = self.config.composer_output_path(date)

        print("\n🧠 Brain Composer")
        print(f"   Date       : {date}")
        print(f"   Inputs dir : {inputs_path}")
        print(f"   Output     : {output_path}")
        print()

        # ── Step 1: Collect input files ───────────────────────────────────────
        print("  [1/6] Collecting input files...")
        input_files = self._collect_inputs(inputs_path)
        for f in input_files:
            print(f"        + {f.name}")

        # ── Step 2: Read files individually (kept for todo extraction) ─────────
        print("  [2/6] Reading input content...")
        combined_content = self._read_inputs(input_files)
        word_count = len(combined_content.split())
        print(f"        {len(input_files)} file(s), ~{word_count} words total")

        # ── Step 3: Two-pass LLM extraction ───────────────────────────────────
        # Pass A: extract per-file (small focused context per call)
        # Pass B: merge per-file summaries into one (tiny context, trivial task)
        print("  [3/6] Composing summary via LLM (two-pass)...")
        summary, composed = self._call_llm_two_pass(input_files, date)
        print(f"        Mood      : {summary.mood}")
        print(f"        Projects  : {len(summary.projects)}")
        print(f"        Blockers  : {len(summary.blockers)}")
        print(f"        Takeaways : {len(summary.key_takeaways)}")

        # ── Step 4: Extract action items into todo.txt ────────────────────────
        print("  [4/6] Extracting action items...")
        todo_items = self._extract_todos(date, combined_content)
        if todo_items:
            print(f"        {len(todo_items)} action item(s) found")
        else:
            print("        No action items found")

        if dry_run:
            print()
            print("  [DRY RUN] Summary preview:")
            print("  " + "─" * 58)
            print(composed)
            print("  " + "─" * 58)
            if todo_items:
                print()
                print("  [DRY RUN] Action items preview:")
                print("  " + "─" * 58)
                for item in todo_items:
                    print(f"  {item}")
                print("  " + "─" * 58)
            print("  [DRY RUN] No files written or archived.")
            return output_path

        # ── Step 5: Write output + todos + optionally archive ─────────────────
        print("  [5/6] Writing output files...")
        self._write_output(output_path, composed)
        print(f"        Summary  : {output_path}")

        if todo_items:
            todo_path = self._append_todos(date, todo_items)
            print(f"        Todos    : {todo_path} (+{len(todo_items)} items)")

        if self.config.composer_archive_after_compose:
            archive_path = self.config.composer_archive_path(date)
            self._archive_inputs(inputs_path, archive_path)
            print(f"        Archived : {archive_path}")

        # ── Step 6: Suggest todo completions (non-blocking) ───────────────────
        print("  [6/6] Checking open todos against today's summary...")
        self._suggest_completions(output_path, date)

        print()
        print("  ✅ Compose complete")
        print(f"     Next: brain ingest --file {output_path} --sensitivity sensitive")
        return output_path

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _validate_date(self, date: str) -> None:
        """Fail fast if date is not in YYYY-MM-DD format."""
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            raise ValueError(f"Invalid date format: '{date}'. Expected YYYY-MM-DD.")

    def _collect_inputs(self, inputs_path: Path) -> list[Path]:
        """
        Collect all input files from the inputs directory.
        Filters by configured extensions. Sorts for deterministic ordering.
        """
        if not inputs_path.exists():
            raise FileNotFoundError(
                f"Inputs directory not found: {inputs_path}\n"
                f"Create it and drop your notes there first:\n"
                f"  mkdir -p {inputs_path}"
            )

        allowed_extensions = self.config.composer_input_extensions
        files = sorted(
            f
            for f in inputs_path.iterdir()
            if f.is_file() and f.suffix in allowed_extensions
        )

        if not files:
            raise ValueError(
                f"No input files found in {inputs_path}\n"
                f"Accepted extensions: {allowed_extensions}"
            )

        return files

    def _read_inputs(self, input_files: list[Path]) -> str:
        """
        Read and concatenate all input files.
        Each file is labelled with its filename so the LLM has provenance context.
        """
        sections = []
        for f in input_files:
            content = f.read_text(encoding="utf-8").strip()
            if content:
                sections.append(f"### Source: {f.name}\n\n{content}")

        if not sections:
            raise ValueError("All input files are empty — nothing to compose.")

        return "\n\n---\n\n".join(sections)

    # ── Two-pass LLM pipeline ─────────────────────────────────────────────────

    # ── Cache directory for per-file extractions ─────────────────────────────
    # Written alongside the inputs so you can inspect and reuse them.
    # Filename: <stem>.extracted.json  e.g. ai_summary.extracted.json
    # Delete a cache file to force re-extraction of that file only.

    def _cache_path(self, input_file: Path) -> Path:
        """Return the .extracted.json cache path for an input file."""
        return input_file.parent / f"{input_file.stem}.extracted.json"

    def _load_cache(self, input_file: Path) -> dict | None:
        """
        Load a cached extraction if it exists and is newer than the source file.
        Returns None if cache is missing or stale.
        """
        cache = self._cache_path(input_file)
        if not cache.exists():
            return None
        if cache.stat().st_mtime < input_file.stat().st_mtime:
            return None  # source was modified after cache — stale
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            print(f"        Cache hit : {input_file.name} → {cache.name}")
            return data
        except Exception:
            return None

    def _save_cache(self, input_file: Path, data: dict) -> None:
        """Write extraction result to .extracted.json alongside the source file."""
        cache = self._cache_path(input_file)
        try:
            cache.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"        ⚠️  Could not write cache for {input_file.name}: {e}")

    def _call_llm_two_pass(
        self, input_files: list[Path], date: str
    ) -> tuple["DailySummary", str]:
        """
        Two-pass extraction to work within llama3.1:8b context limits.

        Pass A — per-file extraction (one LLM call per file):
            Each file is processed individually. Small, focused context means
            the model can read the content properly instead of losing it under
            a 3,000+ word prompt.

            Results are cached as <stem>.extracted.json in the inputs directory.
            On re-run, cached files are loaded directly — no LLM call needed
            unless the source file has changed since the cache was written.
            Delete a .extracted.json file to force re-extraction.

        Pass B — merge (one LLM call, tiny context):
            Per-file JSON summaries merged into a single DailySummary.
            Falls back to Python merge if the LLM call fails.
        """
        # ── Pass A: extract per file (with cache) ─────────────────────────────
        per_file_summaries: list[dict] = []
        for f in input_files:
            content = f.read_text(encoding="utf-8").strip()
            if not content:
                continue

            cached = self._load_cache(f)
            if cached is not None:
                per_file_summaries.append(cached)
                continue

            print(f"        Extracting: {f.name} (~{len(content.split())} words)")
            raw_dict = self._extract_single_file(f, content)
            self._save_cache(f, raw_dict)
            print(f"        Cached    : {self._cache_path(f).name}")
            per_file_summaries.append(raw_dict)

        if not per_file_summaries:
            raise ValueError("All input files were empty after reading.")

        # ── Pass B: merge per-file summaries ──────────────────────────────────
        print(f"        Merging {len(per_file_summaries)} file extraction(s)...")
        try:
            merged = self._merge_summaries(per_file_summaries)
        except Exception as e:
            print(f"        ⚠️  LLM merge failed ({e}) — using Python merge")
            merged = self._python_merge(per_file_summaries)

        merged = self._rescue_nested(merged)

        # Ensure mood_reason always present — model sometimes omits it
        if not merged.get("mood_reason"):
            merged["mood_reason"] = f"Overall mood: {merged.get('mood', 'neutral')}."

        try:
            summary = DailySummary(**merged)
        except Exception as e:
            raise ValueError(
                f"Merged data does not match DailySummary schema: {e}\n"
                f"Merged keys: {list(merged.keys())}\n"
                f"Tip: delete .extracted.json files in the inputs directory "
                f"and re-run to force fresh extraction."
            ) from e

        return summary, summary.to_markdown(date)

    def _extract_single_file(self, input_file: Path, content: str) -> dict:
        """
        Pass A: extract structured data from a single file.

        Context is small (one file at a time) so the model can focus entirely
        on reading the content rather than juggling multiple sources.
        Returns a raw dict — validation happens after merging in Pass B.
        """
        filename = input_file.name

        example = {
            "focus_areas": ["main theme or technology"],
            "projects": ["project name"],
            "key_takeaways": ["concrete insight, decision, or outcome"],
            "meetings": ["meeting name — attendees — time if known"],
            "blockers": ["specific blocker"],
            "people": ["Name — context"],
            "mood": "productive",
            "mood_reason": "One sentence explaining the mood.",
            "references": ["URL or ticket number"],
        }

        # Safe defaults returned on extraction failure — never blocks compose
        empty = {
            k: (
                []
                if k not in ("mood", "mood_reason")
                else ("neutral" if k == "mood" else "No mood recorded.")
            )
            for k in example
        }

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a JSON extraction API. "
                    "Output only a single valid JSON object. "
                    "Never write prose, markdown, or explanations."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Extract structured data from the work notes below.\n\n"
                    f"Output a JSON object with EXACTLY these keys:\n"
                    f"{json.dumps(list(example.keys()))}\n\n"
                    f"Rules:\n"
                    f"- All list fields must be JSON arrays — use [] if nothing found\n"
                    f"- mood: exactly one of: productive, frustrated, neutral, overwhelmed, energized\n"
                    f"- mood_reason: always include — one sentence explaining the mood\n"
                    f"- references: only real URLs and ticket/PR numbers — not file names\n"
                    f"- Extract only what is in the notes — do not invent content\n\n"
                    f"Example output:\n{json.dumps(example, indent=2)}\n\n"
                    f"SOURCE: {filename}\n\n{content}"
                ),
            },
        ]

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            response = ollama.chat(
                model=self.config.ollama_llm_model,
                messages=messages,
                format="json",
                options={"temperature": 0},
            )
            raw = response["message"]["content"].strip()
            try:
                data = self._parse_raw_json(raw)
                # Ensure mood_reason present — model sometimes omits it
                if not data.get("mood_reason"):
                    data["mood_reason"] = f"Mood: {data.get('mood', 'neutral')}."
                # Coerce any dict items in list fields to strings
                # llama3 sometimes returns {"Name": "Rahul", "context": "..."}
                # instead of "Rahul — context" in people/meetings/etc.
                data = self._coerce_list_fields(data)
                return data
            except ValueError as e:
                if attempt == max_attempts:
                    print(
                        f"        ⚠️  {filename}: extraction failed ({e}) — using empty"
                    )
                    return empty

        return empty

    def _coerce_list_fields(self, data: dict) -> dict:
        """
        Coerce dict items in list fields to strings.

        llama3 sometimes returns structured dicts in list fields despite the
        schema requiring plain strings. Examples seen in production:

          people:    [{"Name": "Rahul", "context": "green build"}]
          meetings:  [{"title": "Sprint Planning", "time": "10am"}]
          blockers:  [{"issue": "infra approval", "severity": "high"}]

        This method flattens them to readable strings before caching or
        validation, so the error never reaches DailySummary Pydantic validation.

        Called on every extraction result before _save_cache() writes to disk —
        so corrupt data is never cached in the first place.
        """
        LIST_FIELDS = [
            "focus_areas",
            "projects",
            "key_takeaways",
            "meetings",
            "blockers",
            "people",
            "references",
        ]
        for field in LIST_FIELDS:
            items = data.get(field)
            if not isinstance(items, list):
                continue
            fixed = []
            for item in items:
                if isinstance(item, dict):
                    fixed.append(self._dict_to_str(item))
                elif isinstance(item, str):
                    fixed.append(item)
                else:
                    fixed.append(str(item))
            data[field] = fixed
        return data

    @staticmethod
    def _dict_to_str(d: dict) -> str:
        """
        Flatten a dict to a readable string.

        Tries common name/title keys first, then appends context/role value.
        Falls back to joining all values if no recognised keys found.

        Examples:
          {"Name": "Rahul", "context": "green build"} → "Rahul — green build"
          {"title": "Sprint Planning", "time": "10am"} → "Sprint Planning — 10am"
          {"issue": "infra approval", "severity": "high"} → "infra approval — high"
        """
        name_keys = [
            "name",
            "Name",
            "person",
            "Person",
            "title",
            "Title",
            "meeting",
            "issue",
            "Issue",
        ]
        value_keys = [
            "context",
            "Context",
            "role",
            "Role",
            "note",
            "Note",
            "description",
            "Description",
            "time",
            "Time",
            "severity",
            "detail",
            "Detail",
        ]

        name = next((str(d[k]) for k in name_keys if k in d), None)
        value = next((str(d[k]) for k in value_keys if k in d), None)

        if name and value:
            return f"{name} — {value}"
        elif name:
            return name
        elif len(d) == 1:
            return str(next(iter(d.values())))
        else:
            return " — ".join(str(v) for v in d.values() if v)

    def _merge_summaries(self, summaries: list[dict]) -> dict:
        """
        Pass B: merge per-file summaries into one via LLM.

        The context is tiny — just the per-file JSON objects — so the model
        reliably produces a merged result without losing content.
        """
        summaries_json = json.dumps(summaries, indent=2)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a JSON merge API. "
                    "Output only a single valid JSON object. "
                    "Never write prose, markdown, or explanations."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Merge the following per-file JSON summaries into ONE combined summary.\n\n"
                    f"Rules:\n"
                    f"1. Combine all list fields — deduplicate, preserve all unique items\n"
                    f"2. For mood: use the most prominent mood across all files\n"
                    f"3. For mood_reason: write one sentence summarising the overall day\n"
                    f"4. Output keys: focus_areas, projects, key_takeaways, meetings, "
                    f"blockers, people, mood, mood_reason, references\n"
                    f"5. All list fields must be arrays — use [] if empty\n\n"
                    f"INPUT SUMMARIES:\n{summaries_json}"
                ),
            },
        ]

        response = ollama.chat(
            model=self.config.ollama_llm_model,
            messages=messages,
            format="json",
            options={"temperature": 0},
        )
        raw = response["message"]["content"].strip()
        return self._parse_raw_json(raw)

    def _python_merge(self, summaries: list[dict]) -> dict:
        """
        Fallback: pure Python merge of per-file dicts.
        Used when the Pass B LLM call fails. No LLM required.
        Deduplicates list fields, takes last non-empty value for scalars.
        """
        merged: dict = {
            "focus_areas": [],
            "projects": [],
            "key_takeaways": [],
            "meetings": [],
            "blockers": [],
            "people": [],
            "references": [],
            "mood": "neutral",
            "mood_reason": "",
        }
        for s in summaries:
            for field, value in s.items():
                fn = field.lower().replace(" ", "_").replace("-", "_")
                if fn not in merged:
                    continue
                if isinstance(merged[fn], list) and isinstance(value, list):
                    seen = set(merged[fn])
                    for item in value:
                        if item not in seen:
                            merged[fn].append(item)
                            seen.add(item)
                elif isinstance(value, str) and value:
                    merged[fn] = value
        return merged

    def _parse_raw_json(self, raw: str) -> dict:
        """
        Parse a raw LLM response string into a dict.
        Strips markdown fences if present. Raises ValueError on failure.
        """
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"Not valid JSON: {e}") from e

    def _parse_response(self, raw: str) -> "DailySummary":
        """
        Parse the LLM raw response into a validated DailySummary.

        Handles two known failure modes from llama3:latest:

        1. Markdown fences — model wraps JSON in ```json ... ``` despite instructions.
           Fix: strip fences before parsing.

        2. Nested-by-source structure — model keys output by source file name
           instead of following the flat schema. Looks like:
             {"standup": {"key_takeaways": [...], ...}, "ai_summary": {...}}
           Fix: _rescue_nested() detects this and merges all nested dicts
           into a single flat object before Pydantic validation.
        """
        # Strip markdown fences
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"Response is not valid JSON: {e}") from e

        # Rescue nested structure if the model ignored the flat schema
        data = self._rescue_nested(data)

        try:
            return DailySummary(**data)
        except Exception as e:
            raise ValueError(f"JSON does not match DailySummary schema: {e}") from e

    # Fields we expect at the top level of the JSON response.
    _EXPECTED_FIELDS = frozenset(
        {
            "focus_areas",
            "projects",
            "key_takeaways",
            "meetings",
            "blockers",
            "people",
            "mood",
            "mood_reason",
            "references",
        }
    )

    def _rescue_nested(self, data: dict) -> dict:
        """
        Detect and flatten a nested-by-source JSON structure.

        llama3 sometimes returns:
          {
            "standup":    {"key_takeaways": [...], "blockers": [...], ...},
            "ai_summary": {"projects": [...], "focus_areas": [...], ...},
          }

        This method detects that none of the top-level keys are schema fields,
        then merges all nested dicts into a single flat object — combining
        list fields via extend, taking the last non-empty value for scalars.

        If the top level already looks correct, data is returned unchanged.
        """
        # Case 1: single-wrapper key e.g. {"summary": {...all fields...}}
        # Unwrap it before further checks.
        if len(data) == 1:
            only_value = next(iter(data.values()))
            if isinstance(only_value, dict) and self._EXPECTED_FIELDS & set(
                only_value.keys()
            ):
                print("  \u2139\ufe0f  Detected single-wrapper JSON — unwrapping")
                return only_value

        # Case 2: flat and correct — any expected field at the top level
        if self._EXPECTED_FIELDS & set(data.keys()):
            return data

        # Case 3: nested-by-source — no expected fields at top level
        print(
            "  \u2139\ufe0f  Detected nested JSON — flattening sources into flat schema"
        )

        merged: dict = {
            "focus_areas": [],
            "projects": [],
            "key_takeaways": [],
            "meetings": [],
            "blockers": [],
            "people": [],
            "references": [],
            "mood": "neutral",
            "mood_reason": "",
        }

        for _source_key, source_data in data.items():
            if not isinstance(source_data, dict):
                continue
            for field, value in source_data.items():
                field_norm = field.lower().replace(" ", "_").replace("-", "_")
                if field_norm not in merged:
                    continue
                if isinstance(merged[field_norm], list) and isinstance(value, list):
                    # Merge lists, deduplicate while preserving order
                    seen = set(merged[field_norm])
                    for item in value:
                        if item not in seen:
                            merged[field_norm].append(item)
                            seen.add(item)
                elif isinstance(value, str) and value:
                    # Scalar — take last non-empty value
                    merged[field_norm] = value

        return merged

    def _write_output(self, output_path: Path, content: str) -> None:
        """Write composed content to the output path."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

    def _extract_todos(self, date: str, content: str) -> list[str]:
        """
        Call LLM to extract action items from input content in todo.txt format.
        Returns a list of todo.txt lines. Empty list if none found or no template.
        Fails softly — a todo extraction failure never blocks the summary.
        """
        prompt_template = self.config.composer_todo_prompt_template
        if not prompt_template:
            return []

        prompt = prompt_template.format(date=date, content=content)

        try:
            response = ollama.chat(
                model=self.config.ollama_llm_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0},
            )
            raw = response["message"]["content"].strip()

            return [
                line.strip()
                for line in raw.splitlines()
                if line.strip() and line.strip().startswith("(")
            ]

        except Exception as e:
            print(f"  ⚠️  Todo extraction failed (non-blocking): {e}")
            return []

    def _append_todos(self, date: str, todo_items: list[str]) -> Path:
        """
        Append extracted todo items to the rolling todo.txt file.
        Adds a date separator comment before each day's items.

        Format:
            # 2026-03-10
            (A) 2026-03-10 Fix hotfix approval +Anvil @review
        """
        todo_path = self.config.todo_txt
        todo_path.parent.mkdir(parents=True, exist_ok=True)

        block = f"\n# {date}\n" + "\n".join(todo_items) + "\n"

        with open(todo_path, "a", encoding="utf-8") as f:
            f.write(block)

        return todo_path

    def _archive_inputs(self, inputs_path: Path, archive_path: Path) -> None:
        """
        Move all files from inputs_path into archive_path.
        Skips files that already exist in the archive with a warning.
        """
        archive_path.mkdir(parents=True, exist_ok=True)

        for f in inputs_path.iterdir():
            if not f.is_file():
                continue

            dest = archive_path / f.name

            if dest.exists():
                print(
                    f"  ⚠️  Archive target already exists, skipping: {dest.name}\n"
                    f"      Manually move or remove: {dest}"
                )
                continue

            shutil.move(str(f), str(dest))

    def _suggest_completions(self, summary_path: Path, date: str) -> None:
        """
        Run TodoMatcher against today's composed summary and print suggestions.

        Non-blocking — any failure is caught and printed as a warning.
        Only called when not in dry_run mode.
        """
        try:
            from brain.todo_matcher import TodoMatcher

            matcher = TodoMatcher(self.config)
            open_count = len(matcher.list_open())

            if open_count == 0:
                print("        No open todos to check.")
                return

            print(f"        Scanning {open_count} open todo(s)...")
            content = summary_path.read_text(encoding="utf-8")
            matches = matcher.match(content, date)

            if not matches:
                print("        No open todos appear addressed today.")
                return

            print(f"        {len(matches)} item(s) may have been addressed:\n")
            for m in matches:
                # Show id: and jira: prominently if present
                tag_hint = ""
                if m.todo_id:
                    tag_hint += f"  id:{m.todo_id}"
                if m.jira_ref:
                    tag_hint += f"  jira:{m.jira_ref}"
                print(f"        {m.raw_line}")
                print(f"          ↳ {m.evidence}")
                print()

            print("        To close an item:")
            print("          brain todo --close <id>")
            print(
                f"          e.g. brain todo --close {matches[0].todo_id or 'XXXXXXXX'}"
            )

        except Exception as e:
            print(f"        ⚠️  Todo matching failed (non-blocking): {e}")
