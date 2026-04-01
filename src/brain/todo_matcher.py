"""
todo_matcher.py — Compare open todos against today's summary.

After composing a daily summary, TodoMatcher scans the open items in
todo.txt and asks a local LLM whether any appear to have been addressed
in today's work. Returns suggestions only — never mutates todo.txt.

Design principles:
  Conservative by default — false negatives are fine, false positives are not.
  Non-blocking — any failure returns an empty list, never raises.
  One LLM call — all open todos sent in a single batch, not per-item.

Usage (called by composer.py after compose):
  matcher = TodoMatcher(config)
  matches = matcher.match(summary_content, date="2026-03-31")
  for m in matches:
      print(m.raw_line)
      print(f"  → {m.evidence}")

Standalone (called by brain todo --suggest):
  matcher = TodoMatcher(config)
  matches = matcher.match_from_file(summary_path, date)
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import ollama

from brain.config import BrainConfig

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class TodoMatch:
    """
    A single todo item that may have been addressed today.

    Fields:
      raw_line   — the complete original line from todo.txt
      todo_id    — value of the id: tag, or "" if not present
      jira_ref   — value of the jira: tag, or "" if not present
      confidence — "high" or "medium"
                   Only "high" items are shown by default.
                   "medium" items are shown with --all flag on brain todo --suggest.
      evidence   — one sentence from the LLM explaining why it thinks
                   this item was addressed
    """

    raw_line: str
    todo_id: str = ""
    jira_ref: str = ""
    confidence: str = "high"
    evidence: str = ""


# ── TodoMatcher ───────────────────────────────────────────────────────────────


class TodoMatcher:
    """
    Compares open action items in todo.txt against a daily summary
    and returns items that appear to have been addressed today.

    Never mutates todo.txt. Returns suggestions only.
    Conservative threshold — only HIGH confidence matches returned by default.
    """

    # Maximum number of open todos to send to LLM in one call.
    # Beyond this, we chunk into batches to stay within context limits.
    _MAX_BATCH = 30

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    # ── Public API ────────────────────────────────────────────────────────────

    def match(
        self,
        summary_content: str,
        date: str,
        include_medium: bool = False,
    ) -> list[TodoMatch]:
        """
        Compare open todos against provided summary content.

        Args:
            summary_content: Full text of today's composed summary.
            date:            Today's date string — YYYY-MM-DD.
            include_medium:  If True, return medium confidence matches too.
                             Default False — only high confidence shown.

        Returns:
            List of TodoMatch — empty if no candidates found or on any failure.
        """
        open_todos = self._load_open_todos()
        if not open_todos:
            return []

        return self._run_matching(open_todos, summary_content, date, include_medium)

    def match_from_file(
        self,
        summary_path: Path,
        date: str,
        include_medium: bool = False,
    ) -> list[TodoMatch]:
        """
        Convenience wrapper — reads summary from file then calls match().
        Used by brain todo --suggest.
        """
        if not summary_path.exists():
            return []
        content = summary_path.read_text(encoding="utf-8")
        return self.match(content, date, include_medium)

    def close_todo(self, todo_id: str, date: str) -> bool:
        """
        Mark a todo item as completed in todo.txt by its id: tag.

        Prepends 'x YYYY-MM-DD ' per todo.txt spec.
        Returns True if item was found and closed, False if not found.

        Does not remove the id: tag — keeps the completed item traceable.
        """
        todo_path = self.config.todo_txt
        if not todo_path.exists():
            return False

        lines = todo_path.read_text(encoding="utf-8").splitlines(keepends=True)
        updated = []
        found = False

        for line in lines:
            stripped = line.rstrip("\n")
            # Skip already-completed lines
            if stripped.startswith("x "):
                updated.append(line)
                continue
            # Match on id: tag
            if f"id:{todo_id}" in stripped:
                updated.append(f"x {date} {stripped}\n")
                found = True
            else:
                updated.append(line)

        if found:
            todo_path.write_text("".join(updated), encoding="utf-8")

        return found

    def list_open(self) -> list[str]:
        """Return all open (non-completed) todo lines."""
        return self._load_open_todos()

    def list_all(self) -> tuple[list[str], list[str]]:
        """Return (open_lines, completed_lines)."""
        todo_path = self.config.todo_txt
        if not todo_path.exists():
            return [], []

        open_lines = []
        done_lines = []
        for line in todo_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("x "):
                done_lines.append(stripped)
            else:
                open_lines.append(stripped)

        return open_lines, done_lines

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_open_todos(self) -> list[str]:
        """
        Read todo.txt and return non-completed, non-comment lines.
        Completed items start with 'x ' per todo.txt spec.
        Comment lines start with '#'.
        """
        todo_path = self.config.todo_txt
        if not todo_path.exists():
            return []

        open_lines = []
        for line in todo_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("x "):
                continue
            open_lines.append(stripped)

        return open_lines

    def _run_matching(
        self,
        todos: list[str],
        summary: str,
        date: str,
        include_medium: bool,
    ) -> list[TodoMatch]:
        """
        Run LLM matching in batches if needed.
        Collects results from all batches, filters by confidence.
        Returns empty list on any failure — non-blocking.
        """
        try:
            all_matches: list[TodoMatch] = []

            # Batch to stay within context limits
            for i in range(0, len(todos), self._MAX_BATCH):
                batch = todos[i : i + self._MAX_BATCH]
                batch_matches = self._call_llm(batch, summary, date)
                all_matches.extend(batch_matches)

            # Filter by confidence threshold
            threshold = {"high", "medium"} if include_medium else {"high"}
            return [m for m in all_matches if m.confidence in threshold]

        except Exception as e:
            # Non-blocking — log and return empty
            print(f"        ⚠️  Todo matching failed (non-blocking): {e}")
            return []

    def _call_llm(
        self,
        todos: list[str],
        summary: str,
        date: str,
    ) -> list[TodoMatch]:
        """
        Single Ollama call — batch of todos against today's summary.

        Prompt is conservative: requires EXPLICIT evidence of completion,
        not just topic overlap. Returns parsed TodoMatch list.
        Falls back to empty list on parse failure.
        """
        todos_block = "\n".join(f"  {t}" for t in todos)
        # Truncate summary to keep prompt manageable — first 1,500 words
        summary_truncated = " ".join(summary.split()[:1500])

        prompt = f"""You are reviewing open action items to see if today's work summary
provides evidence that any were completed or addressed.

Today's date: {date}

OPEN ACTION ITEMS:
{todos_block}

TODAY'S WORK SUMMARY:
{summary_truncated}

For each action item, assess whether today's summary provides evidence it was
addressed. Apply these rules strictly:

- confidence "high"   : summary explicitly describes completing or delivering
                        the action item — matching project AND completion language
                        (e.g. "merged", "sent", "fixed", "deployed", "closed")
- confidence "medium" : summary mentions the project or topic but no clear
                        completion signal
- confidence "none"   : no relevant evidence in summary

When uncertain between high and medium, choose medium.
When uncertain between medium and none, choose none.
False negatives are acceptable. False positives are not.

Return ONLY a valid JSON array. No preamble. No commentary. No markdown fences.
Each element must have exactly these keys: "line", "confidence", "evidence"
"evidence" must be one sentence — quote the specific part of the summary
that supports the assessment, or "No evidence found." for confidence "none".

Example:
[
  {{"line": "(A) 2026-03-15 Fix hotfix approval +Anvil @review id:3a9f2b1c", "confidence": "high", "evidence": "Summary states 'hotfix approval merged and deployed to prod'"}},
  {{"line": "(B) 2026-03-20 Update runbook +Infra @docs id:7c4e8d2a", "confidence": "none", "evidence": "No evidence found."}}
]"""

        response = ollama.chat(
            model=self.config.ollama_llm_model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={"temperature": 0},
        )

        raw = response["message"]["content"].strip()
        return self._parse_llm_response(raw, todos)

    def _parse_llm_response(
        self,
        raw: str,
        original_todos: list[str],
    ) -> list[TodoMatch]:
        """
        Parse LLM JSON response into TodoMatch list.

        Handles:
          - Markdown fences (```json ... ```)
          - Object wrapper ({"matches": [...]})
          - Mismatched lines — falls back to fuzzy matching against originals
          - Any parse failure → empty list (non-blocking)
        """
        # Strip markdown fences
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.MULTILINE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned, flags=re.MULTILINE).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return []

        # Unwrap {"matches": [...]} or {"items": [...]} if needed
        if isinstance(data, dict):
            for key in ("matches", "items", "results", "todos"):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
            else:
                return []

        if not isinstance(data, list):
            return []

        matches = []
        for item in data:
            if not isinstance(item, dict):
                continue

            confidence = str(item.get("confidence", "none")).lower().strip()
            if confidence == "none":
                continue

            line = str(item.get("line", "")).strip()
            evidence = str(item.get("evidence", "")).strip()

            # Resolve line back to an original todo (LLM may paraphrase)
            resolved = self._resolve_line(line, original_todos)
            if not resolved:
                continue

            matches.append(
                TodoMatch(
                    raw_line=resolved,
                    todo_id=self._extract_tag(resolved, "id"),
                    jira_ref=self._extract_tag(resolved, "jira"),
                    confidence=confidence
                    if confidence in ("high", "medium")
                    else "medium",
                    evidence=evidence,
                )
            )

        return matches

    def _resolve_line(self, line: str, originals: list[str]) -> str:
        """
        Match LLM-returned line back to an original todo.txt line.

        LLMs sometimes rephrase or truncate the line. We try:
          1. Exact match
          2. Original contains the returned line (LLM truncated)
          3. id: tag match (most reliable)
          4. First 40-char prefix match
        """
        if not line:
            return ""

        # 1. Exact
        if line in originals:
            return line

        # 2. Substring — LLM returned truncated version
        for orig in originals:
            if line in orig or orig in line:
                return orig

        # 3. id: tag match
        id_val = self._extract_tag(line, "id")
        if id_val:
            for orig in originals:
                if f"id:{id_val}" in orig:
                    return orig

        # 4. First 40-char prefix
        prefix = line[:40].lower()
        for orig in originals:
            if orig.lower().startswith(prefix):
                return orig

        return ""

    @staticmethod
    def _extract_tag(line: str, tag: str) -> str:
        """
        Extract a tag value from a todo.txt line.
        e.g. _extract_tag("... id:a3f291 jira:PLAT-1", "id") → "a3f291"
        Returns "" if tag not present.
        """
        match = re.search(rf"\b{re.escape(tag)}:(\S+)", line)
        return match.group(1) if match else ""
