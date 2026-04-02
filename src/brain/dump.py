"""
dump.py — Daily raw note parser for Brain.

Reads all daily_*.md files from inputs/YYYY-MM-DD/, splits them into
H2 sections, classifies each section by type (Task / PR / Meeting / Other),
and writes structured output files ready for brain compose.

Flow:
  inputs/YYYY-MM-DD/daily_dump.md  (+ daily_tasks.md, daily_meets.md, etc.)
      │
      ▼  DumpParser
      ├── task-ef-route53-txt-record.md    ← Task sections
      ├── task-cs-15011-hotfix.md          ← Task with Jira ID detected
      ├── pr-reviews.md                    ← All PR sections merged
      ├── meet-platform-sync.md            ← Meeting skeleton (you fill before compose)
      └── misc-random-notes.md             ← Catch-all (nothing is ever lost)

Idempotency:
  - Original daily_*.md files are NEVER modified
  - Existing output files are SKIPPED with a warning (protects filled meet skeletons)
  - Safe to re-run at any point

Jira Bridge:
  - Task sections are scanned for Jira ticket IDs ([A-Z]{2,9}-\\d+)
  - Detected IDs are written as <!-- jira:TICKET-ID --> tags in the output
  - brain compose → todo extraction picks these up automatically
  - This is the Phase 5b bridge — no extra work needed at compose time

All settings (keywords, templates, glob pattern) are config-driven.
No hardcoded values here.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from brain.config import BrainConfig

# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class ParsedSection:
    """
    A single H2 section extracted from a daily_*.md file.

    Fields
    ──────
    title         : raw section title (after stripping "## ")
    body          : section body text (everything until next H2 or EOF)
    section_type  : classified type — "task" | "pr" | "meeting" | "other"
    source_file   : filename of the daily_*.md this came from
    jira_ids      : Jira ticket IDs found in title + body (Task sections only)
    slug          : URL-safe slug derived from title — used in output filename
    """

    title: str
    body: str
    section_type: str
    source_file: str
    jira_ids: list[str] = field(default_factory=list)
    slug: str = ""

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = _slugify(self.title)


@dataclass
class DumpResult:
    """
    Summary of a single brain dump run.

    Fields
    ──────
    date          : ISO date string — YYYY-MM-DD
    input_files   : daily_*.md files read
    sections      : all ParsedSection objects extracted
    written       : paths of files successfully written
    skipped       : paths skipped because they already existed
    warnings      : non-fatal messages (skipped files, empty sections, etc.)
    """

    date: str
    input_files: list[Path] = field(default_factory=list)
    sections: list[ParsedSection] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def task_count(self) -> int:
        return sum(1 for s in self.sections if s.section_type == "task")

    @property
    def pr_count(self) -> int:
        return sum(1 for s in self.sections if s.section_type == "pr")

    @property
    def meeting_count(self) -> int:
        return sum(1 for s in self.sections if s.section_type == "meeting")

    @property
    def other_count(self) -> int:
        return sum(1 for s in self.sections if s.section_type == "other")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    """
    Convert a section title into a filesystem-safe slug.

    Examples:
      "EF Route 53 TXT Record"  → "ef-route53-txt-record"
      "1:1 with Alice"          → "1-1-with-alice"
      "CS-15011: hotfix"        → "cs-15011-hotfix"
    """
    # Lowercase
    slug = text.lower()
    # Replace common separators with hyphen
    slug = re.sub(r"[\s:/_|]+", "-", slug)
    # Remove anything that's not alphanumeric or hyphen
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    # Collapse multiple hyphens
    slug = re.sub(r"-{2,}", "-", slug)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    return slug or "untitled"


def _extract_jira_ids(text: str, pattern: str) -> list[str]:
    """
    Extract all Jira ticket IDs from a block of text.

    Uses the compiled pattern from config (e.g. [A-Z]{2,9}-\\d+).
    Deduplicates — same ticket mentioned twice appears once.

    Returns sorted list for deterministic output.
    """
    try:
        found = re.findall(pattern, text)
        return sorted(set(found))
    except re.error:
        # Bad pattern in config — fail soft, no IDs extracted
        return []


# ── DumpParser ────────────────────────────────────────────────────────────────


class DumpParser:
    """
    Parses daily_*.md files into structured output files.

    One instance per run — call parse() with a date string.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

        # Pre-compile Jira pattern once — used on every task section
        raw_pattern = (
            self.config._cfg.get("mcp", {})
            .get("jira", {})
            .get("ticket_id_pattern", r"[A-Z]{2,9}-\d+")
        )
        try:
            self._jira_pattern = re.compile(raw_pattern)
        except re.error:
            # Bad pattern in config — disable Jira detection gracefully
            self._jira_pattern = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def parse(self, date: str, dry_run: bool = False) -> DumpResult:
        """
        Main entry point. Parse all daily_*.md files for the given date.

        Args:
            date    : ISO date string — YYYY-MM-DD
            dry_run : If True, print what would be written but write nothing.

        Returns:
            DumpResult with full summary of the run.

        Raises:
            ValueError       : Invalid date format.
            FileNotFoundError: inputs/YYYY-MM-DD/ directory doesn't exist.
        """
        self._validate_date(date)

        inputs_path = self.config.dump_inputs_path(date)

        print("\n🧠 Brain Dump Parser")
        print(f"   Date      : {date}")
        print(f"   Input dir : {inputs_path}")
        print()

        result = DumpResult(date=date)

        # ── Step 1: Collect daily_*.md input files ────────────────────────────
        print("  [1/4] Collecting input files...")
        input_files = self._collect_inputs(inputs_path)
        result.input_files = input_files
        for f in input_files:
            print(f"        + {f.name}")

        # ── Step 2: Parse all sections from all input files ───────────────────
        print("  [2/4] Parsing H2 sections...")
        all_sections: list[ParsedSection] = []
        for f in input_files:
            sections = self._parse_file(f)
            all_sections.extend(sections)
            print(f"        {f.name} → {len(sections)} section(s)")

        if not all_sections:
            print("        ⚠️  No H2 sections found in any input file.")
            print("           Make sure your notes use ## headings.")
            return result

        result.sections = all_sections

        # ── Step 3: Classify sections ─────────────────────────────────────────
        print("  [3/4] Classifying sections...")
        for s in all_sections:
            symbol = _type_symbol(s.section_type)
            jira_note = f"  [jira: {', '.join(s.jira_ids)}]" if s.jira_ids else ""
            print(f"        {symbol} [{s.section_type:8s}] {s.title}{jira_note}")

        # ── Step 4: Write output files ────────────────────────────────────────
        print(f"  [4/4] Writing output files{' (DRY RUN)' if dry_run else ''}...")
        self._write_all(
            sections=all_sections,
            inputs_path=inputs_path,
            date=date,
            dry_run=dry_run,
            result=result,
        )

        # ── Summary ───────────────────────────────────────────────────────────
        print()
        self._print_summary(result, dry_run)

        return result

    # ── Section Parsing ────────────────────────────────────────────────────────

    def _collect_inputs(self, inputs_path: Path) -> list[Path]:
        """
        Collect all daily_*.md files from the inputs directory.

        Raises:
            FileNotFoundError: Directory doesn't exist.
            ValueError:        No daily_*.md files found.
        """
        if not inputs_path.exists():
            raise FileNotFoundError(
                f"Inputs directory not found: {inputs_path}\n"
                f"Create it and drop your daily_*.md files there:\n"
                f"  mkdir -p {inputs_path}"
            )

        glob_pattern = self.config.dump_input_glob
        files = sorted(inputs_path.glob(glob_pattern))

        if not files:
            raise ValueError(
                f"No files matching '{glob_pattern}' found in {inputs_path}\n"
                f"Expected files like: daily_dump.md, daily_tasks.md, daily_meets.md"
            )

        return files

    def _parse_file(self, file_path: Path) -> list[ParsedSection]:
        """
        Split a single markdown file into ParsedSection objects at H2 boundaries.

        Strategy:
          - Split on lines starting with "## "
          - First split yields any content BEFORE the first H2 (preamble)
          - Preamble is captured as an "other" section titled "Preamble"
            so nothing is ever lost
          - Each subsequent split is a proper section

        Empty section bodies are kept — the user may have left a heading
        with no notes yet. We write the template anyway.
        """
        content = file_path.read_text(encoding="utf-8")
        source_name = file_path.name
        sections: list[ParsedSection] = []

        # Split on H2 headings — keep the heading text
        # Pattern: line starting with "## " (exactly two hashes + space)
        parts = re.split(r"^(## .+)$", content, flags=re.MULTILINE)

        # parts layout after split:
        #   [preamble_text, "## Title1", body1, "## Title2", body2, ...]
        # If no H2 exists: parts = [full_content]

        preamble = parts[0].strip()
        if preamble:
            # Content before first H2 — capture as Other so nothing is lost
            sections.append(
                ParsedSection(
                    title="Preamble",
                    body=preamble,
                    section_type="other",
                    source_file=source_name,
                )
            )

        # Walk heading + body pairs
        i = 1
        while i < len(parts) - 1:
            raw_heading = parts[i]  # e.g. "## Task: EF Route 53"
            body = parts[i + 1].strip()  # everything until next H2

            # Strip "## " prefix and any trailing colon from title
            title = raw_heading.lstrip("#").strip()
            # Remove trailing colon — "Task: Fix X" → "Task: Fix X" (keep)
            # but "PR Review:" → "PR Review" (strip bare trailing colon)
            if title.endswith(":"):
                title = title[:-1].strip()

            section_type = self._classify(title)
            jira_ids = self._detect_jira(title, body, section_type)

            sections.append(
                ParsedSection(
                    title=title,
                    body=body,
                    section_type=section_type,
                    source_file=source_name,
                    jira_ids=jira_ids,
                )
            )
            i += 2

        return sections

    def _classify(self, title: str) -> str:
        """
        Classify a section title into one of: task | pr | meeting | other.

        Evaluation order matters:
          1. task    — checked first (most common, most important)
          2. pr      — before meeting (a "PR review" must not be a "meeting")
          3. meeting — broad keywords, so evaluated last of the named types
          4. other   — catch-all

        All matching is case-insensitive substring search.
        Keyword lists are loaded from config — no hardcoding here.
        """
        lower = title.lower()

        for keyword in self.config.dump_task_keywords:
            if keyword.lower() in lower:
                return "task"

        for keyword in self.config.dump_pr_keywords:
            if keyword.lower() in lower:
                return "pr"

        for keyword in self.config.dump_meeting_keywords:
            if keyword.lower() in lower:
                return "meeting"

        return "other"

    def _detect_jira(self, title: str, body: str, section_type: str) -> list[str]:
        """
        Scan title + body for Jira ticket IDs.

        Only runs for Task sections — PR/Meeting/Other sections don't get
        Jira tags. A meeting mentioning a ticket is not the same as working
        on that ticket.

        Returns empty list if:
          - section_type is not "task"
          - Jira pattern is misconfigured (fails soft)
          - No IDs found
        """
        if section_type != "task":
            return []
        if self._jira_pattern is None:
            return []

        combined = f"{title}\n{body}"
        found = self._jira_pattern.findall(combined)
        return sorted(set(found))

    # ── Output Writing ─────────────────────────────────────────────────────────

    def _write_all(
        self,
        sections: list[ParsedSection],
        inputs_path: Path,
        date: str,
        dry_run: bool,
        result: DumpResult,
    ) -> None:
        """
        Write all classified sections to output files.

        Routing:
          task    → individual file per section
          pr      → all sections merged into pr-reviews.md
          meeting → individual skeleton file per section
          other   → individual catch-all file per section

        PR sections accumulate in a buffer and are written once at the end
        to avoid opening/closing the file repeatedly.
        """
        pr_sections: list[ParsedSection] = []

        for section in sections:
            if section.section_type == "pr":
                pr_sections.append(section)
                continue

            if section.section_type == "task":
                self._write_task(section, inputs_path, date, dry_run, result)

            elif section.section_type == "meeting":
                self._write_meeting(section, inputs_path, date, dry_run, result)

            else:  # "other"
                self._write_other(section, inputs_path, date, dry_run, result)

        # Write all PR sections as one merged file
        if pr_sections:
            self._write_pr_merged(pr_sections, inputs_path, date, dry_run, result)

    def _write_task(
        self,
        section: ParsedSection,
        inputs_path: Path,
        date: str,
        dry_run: bool,
        result: DumpResult,
    ) -> None:
        """Write a task section to task-<slug>.md."""
        prefixes = self.config.dump_output_prefixes
        prefix = prefixes.get("task", "task-")
        filename = f"{prefix}{section.slug}.md"
        output_path = inputs_path / filename

        if self._should_skip(output_path, result):
            return

        # Build jira tag block — empty string if no IDs found
        jira_tags = ""
        if section.jira_ids:
            jira_tags = "\n".join(f"<!-- jira:{jid} -->" for jid in section.jira_ids)
            jira_tags = f"\n{jira_tags}\n"

        template = self.config.dump_task_file_template
        content = template.format(
            title=_strip_type_prefix(section.title),
            date=date,
            source_file=section.source_file,
            jira_tags=jira_tags,
            original_notes=section.body if section.body else "<!-- add notes -->",
        )

        self._write_file(output_path, content, dry_run, result)

    def _write_meeting(
        self,
        section: ParsedSection,
        inputs_path: Path,
        date: str,
        dry_run: bool,
        result: DumpResult,
    ) -> None:
        """Write a meeting section to meet-<slug>.md skeleton."""
        prefixes = self.config.dump_output_prefixes
        prefix = prefixes.get("meeting", "meet-")
        filename = f"{prefix}{section.slug}.md"
        output_path = inputs_path / filename

        if self._should_skip(output_path, result):
            return

        template = self.config.dump_meeting_skeleton_template
        content = template.format(
            title=_strip_type_prefix(section.title),
            date=date,
            source_file=section.source_file,
            original_notes=section.body
            if section.body
            else "<!-- no notes captured yet -->",
        )

        self._write_file(output_path, content, dry_run, result)

    def _write_pr_merged(
        self,
        sections: list[ParsedSection],
        inputs_path: Path,
        date: str,
        dry_run: bool,
        result: DumpResult,
    ) -> None:
        """
        Merge all PR sections into a single pr-reviews.md file.

        Each section becomes a subsection (### heading) inside the merged file.
        This avoids cluttering the inputs directory with multiple tiny files.
        """
        filename = self.config.dump_pr_merge_file
        output_path = inputs_path / filename

        if self._should_skip(output_path, result):
            return

        lines = [f"# PR Reviews — {date}\n"]
        for s in sections:
            clean_title = _strip_type_prefix(s.title)
            lines.append(f"\n## {clean_title}\n")
            lines.append(
                s.body if s.body else "_No notes captured — add links/comments here._"
            )
            lines.append("\n")

        content = "\n".join(lines)
        self._write_file(output_path, content, dry_run, result)

    def _write_other(
        self,
        section: ParsedSection,
        inputs_path: Path,
        date: str,
        dry_run: bool,
        result: DumpResult,
    ) -> None:
        """Write a catch-all section to misc-<slug>.md."""
        prefixes = self.config.dump_output_prefixes
        prefix = prefixes.get("other", "misc-")

        # Special case: preamble gets its own fixed filename
        if section.title == "Preamble":
            filename = "misc-preamble.md"
        else:
            filename = f"{prefix}{section.slug}.md"

        output_path = inputs_path / filename

        if self._should_skip(output_path, result):
            return

        template = self.config.dump_other_file_template
        content = template.format(
            title=section.title,
            date=date,
            source_file=section.source_file,
            original_notes=section.body if section.body else "<!-- no content -->",
        )

        self._write_file(output_path, content, dry_run, result)

    # ── File I/O Helpers ───────────────────────────────────────────────────────

    def _should_skip(self, output_path: Path, result: DumpResult) -> bool:
        """
        Check if an output file already exists.

        If it does: record in result.skipped, print warning, return True.
        This protects meeting skeletons the user has already filled in.
        """
        if output_path.exists():
            warning = f"⚠️  Skipping (already exists): {output_path.name}"
            result.skipped.append(output_path)
            result.warnings.append(warning)
            print(f"        {warning}")
            return True
        return False

    def _write_file(
        self,
        output_path: Path,
        content: str,
        dry_run: bool,
        result: DumpResult,
    ) -> None:
        """
        Write content to output_path (or print preview on dry_run).
        Records written path in result.
        """
        if dry_run:
            print(f"        [DRY RUN] Would write: {output_path.name}")
            print(f"        {'─' * 54}")
            # Print first 8 lines as preview
            preview_lines = content.splitlines()[:8]
            for line in preview_lines:
                print(f"        {line}")
            if len(content.splitlines()) > 8:
                print(f"        ... ({len(content.splitlines())} lines total)")
            print(f"        {'─' * 54}")
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            print(f"        ✅ Written: {output_path.name}")

        result.written.append(output_path)

    # ── Validation ─────────────────────────────────────────────────────────────

    def _validate_date(self, date: str) -> None:
        """Fail fast if date is not in YYYY-MM-DD format."""
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            raise ValueError(f"Invalid date format: '{date}'. Expected YYYY-MM-DD.")

    # ── Summary ────────────────────────────────────────────────────────────────

    def _print_summary(self, result: DumpResult, dry_run: bool) -> None:
        """Print a clean run summary with next-step instructions."""
        label = " (DRY RUN)" if dry_run else ""
        print(f"  ✅ Dump parse complete{label}")
        print()
        print(f"     Input files : {len(result.input_files)}")
        print(f"     Sections    : {len(result.sections)} total")
        print(f"       📋 Tasks   : {result.task_count}")
        print(f"       🔀 PRs     : {result.pr_count}")
        print(f"       🤝 Meetings: {result.meeting_count}")
        print(f"       📎 Other   : {result.other_count}")
        print()

        if result.written and not dry_run:
            print(f"     Files written ({len(result.written)}):")
            for p in result.written:
                print(f"       → {p.name}")

        if result.skipped:
            print(f"\n     ⚠️  Skipped ({len(result.skipped)}) — already exist:")
            for p in result.skipped:
                print(f"       → {p.name}")

        # ── Next steps ────────────────────────────────────────────────────────
        if not dry_run:
            date = result.date
            meeting_files = [
                p
                for p in result.written
                if p.name.startswith(
                    self.config.dump_output_prefixes.get("meeting", "meet-")
                )
            ]

            print()
            print("  ─── Next Steps ───────────────────────────────────────")

            if meeting_files:
                print(f"  1️⃣  Fill meeting skeletons ({len(meeting_files)} file(s)):")
                for mf in meeting_files:
                    print(f"       open {mf}")

            step = 2 if meeting_files else 1
            print(f"  {step}️⃣  Run: brain compose --date {date} --ingest")
            print("  ──────────────────────────────────────────────────────")


# ── Private Module Helpers ────────────────────────────────────────────────────


def _strip_type_prefix(title: str) -> str:
    """
    Remove the type label prefix from a section title for cleaner output.

    Examples:
      "Task: EF Route 53 TXT Record"  → "EF Route 53 TXT Record"
      "Meet: Platform Sync"            → "Platform Sync"
      "PR Review: CS-15011"            → "CS-15011"
      "EF Route 53 TXT Record"         → "EF Route 53 TXT Record"  (no prefix, unchanged)
    """
    # Match "Word:" or "Two Words:" at the start — case insensitive
    return re.sub(r"^[\w\s]{1,20}:\s*", "", title).strip() or title


def _type_symbol(section_type: str) -> str:
    """Return an emoji symbol for the section type — used in console output."""
    return {
        "task": "📋",
        "pr": "🔀",
        "meeting": "🤝",
        "other": "📎",
    }.get(section_type, "•")
