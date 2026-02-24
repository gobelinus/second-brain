"""
structurer.py — Extract structured metadata from raw markdown summaries.

Calls local Ollama/Mistral to parse free-text daily summaries into
a validated JSON metadata object. This metadata feeds both SQLite
(for trend queries) and ChromaDB (as document metadata).

Output schema:
{
    "date": "YYYY-MM-DD",
    "sensitivity": "public" | "sensitive",
    "projects": [...],
    "blockers": [...],
    "people": [...],
    "sentiment": "productive" | "frustrated" | "neutral" | "overwhelmed" | "energized",
    "focus_areas": [...],
    "raw_file": "relative/path/to/file.md"
}
"""

import json
import re
from pathlib import Path

import ollama

from brain.config import BrainConfig

# ── Constants ─────────────────────────────────────────────────────────────────

METADATA_PROMPT = """
You are a precise metadata extractor. Analyze the daily work summary below
and extract structured metadata. Respond ONLY with a valid JSON object —
no explanation, no markdown fences, no extra text.

Required JSON schema:
{{
    "projects": ["list of project names mentioned"],
    "blockers": ["list of blockers or impediments mentioned"],
    "people": ["list of people or teams interacted with, format: Name (context)"],
    "sentiment": "one of: productive, frustrated, neutral, overwhelmed, energized",
    "focus_areas": ["list of technical or functional areas focused on"]
}}

Rules:
- projects: extract proper project/initiative names only
- blockers: extract concrete blockers, not vague statements
- people: include context in brackets e.g. "Alice (design review)"
- sentiment: pick the SINGLE best match from the allowed values only
- focus_areas: extract technologies, domains, or disciplines (e.g. AWS, SRE, mentoring)
- If a field has no data, use an empty list []
- Sentiment must always be filled — infer from tone if not explicit

Daily Summary:
{content}
"""


# ── Core Extractor ────────────────────────────────────────────────────────────


class Structurer:
    """
    Extracts structured metadata from a markdown daily summary
    using a local Ollama LLM call.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    def extract(self, file_path: Path, sensitivity: str) -> dict:
        """
        Main entry point. Given a markdown file path and sensitivity level,
        return a fully populated metadata dict ready for storage.

        Args:
            file_path:   Absolute or relative path to the .md file
            sensitivity: "public" or "sensitive"

        Returns:
            dict with all metadata fields populated
        """
        file_path = Path(file_path)
        self._validate_inputs(file_path, sensitivity)

        content = self._read_file(file_path)
        date = self._extract_date(file_path)
        raw_metadata = self._call_llm(content)
        validated = self._validate_metadata(raw_metadata)

        return {
            "date": date,
            "sensitivity": sensitivity,
            "projects": validated["projects"],
            "blockers": validated["blockers"],
            "people": validated["people"],
            "sentiment": validated["sentiment"],
            "focus_areas": validated["focus_areas"],
            "raw_file": str(file_path),
        }

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _validate_inputs(self, file_path: Path, sensitivity: str) -> None:
        """Fail fast on bad inputs before any LLM call."""
        if not file_path.exists():
            raise FileNotFoundError(f"Summary file not found: {file_path}")
        if file_path.suffix != ".md":
            raise ValueError(f"Expected a .md file, got: {file_path.suffix}")
        if sensitivity not in ("public", "sensitive"):
            raise ValueError(
                f"Invalid sensitivity '{sensitivity}'. Use 'public' or 'sensitive'."
            )

    def _read_file(self, file_path: Path) -> str:
        """Read markdown content, strip excessive whitespace."""
        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"File is empty: {file_path}")
        return content

    def _extract_date(self, file_path: Path) -> str:
        """
        Extract date from filename. Expects YYYY-MM-DD.md format.
        Falls back to today's date with a warning if format doesn't match.
        """
        stem = file_path.stem  # filename without extension
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")

        if date_pattern.match(stem):
            return stem

        # Fallback — warn but don't crash
        from datetime import date

        today = date.today().isoformat()
        print(
            f"  ⚠️  Filename '{file_path.name}' is not in YYYY-MM-DD format. "
            f"Falling back to today: {today}"
        )
        return today

    def _call_llm(self, content: str) -> dict:
        """
        Call local Ollama LLM with the extraction prompt.
        Returns parsed JSON dict or raises on failure.
        """
        prompt = METADATA_PROMPT.format(content=content)

        response = ollama.chat(
            model=self.config.ollama_llm_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0},  # deterministic extraction
        )

        raw_text = response["message"]["content"].strip()
        return self._parse_json(raw_text)

    def _parse_json(self, raw_text: str) -> dict:
        """
        Safely parse LLM JSON response.
        Handles cases where the model wraps output in markdown fences.
        """
        # Strip markdown code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"LLM returned invalid JSON.\nError: {e}\nRaw response:\n{raw_text}"
            ) from e

    def _validate_metadata(self, raw: dict) -> dict:
        """
        Validate and sanitize LLM output against expected schema.
        Fills missing fields with safe defaults rather than crashing.
        """
        allowed_sentiments = self.config.valid_sentiments

        # Ensure all list fields are actually lists
        projects = raw.get("projects", [])
        blockers = raw.get("blockers", [])
        people = raw.get("people", [])
        focus_areas = raw.get("focus_areas", [])

        for field_name, field_val in [
            ("projects", projects),
            ("blockers", blockers),
            ("people", people),
            ("focus_areas", focus_areas),
        ]:
            if not isinstance(field_val, list):
                print(f"  ⚠️  Field '{field_name}' was not a list — resetting to []")
                locals()[field_name] = []

        # Validate sentiment
        sentiment = raw.get("sentiment", "neutral").lower().strip()
        if sentiment not in allowed_sentiments:
            print(
                f"  ⚠️  Sentiment '{sentiment}' not in allowed values. "
                f"Defaulting to 'neutral'."
            )
            sentiment = "neutral"

        return {
            "projects": projects,
            "blockers": blockers,
            "people": people,
            "sentiment": sentiment,
            "focus_areas": focus_areas,
        }
