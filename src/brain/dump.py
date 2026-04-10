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
    pr_numbers    : PR numbers detected in pr-reviews.md (for comment fetching)
    pr_copied     : PR comment files copied from src_dir (--copy-pr-comments run)
    """

    date: str
    input_files: list[Path] = field(default_factory=list)
    sections: list[ParsedSection] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pr_numbers: list[str] = field(default_factory=list)
    pr_copied: list[Path] = field(default_factory=list)
    jira_ids_detected: list[str] = field(default_factory=list)  # ← NEW after pr_numbers

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
        print("  [1/6] Collecting input files...")
        input_files = self._collect_inputs(inputs_path)
        result.input_files = input_files
        for f in input_files:
            print(f"        + {f.name}")

        # ── Step 2: Parse all sections from all input files ───────────────────
        print("  [2/6] Parsing H2 sections...")
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
        print("  [3/6] Classifying sections...")
        for s in all_sections:
            symbol = _type_symbol(s.section_type)
            jira_note = f"  [jira: {', '.join(s.jira_ids)}]" if s.jira_ids else ""
            print(f"        {symbol} [{s.section_type:8s}] {s.title}{jira_note}")

        # ── Step 4: Write output files ────────────────────────────────────────
        print(f"  [4/6] Writing output files{' (DRY RUN)' if dry_run else ''}...")
        self._write_all(
            sections=all_sections,
            inputs_path=inputs_path,
            date=date,
            dry_run=dry_run,
            result=result,
        )

        # ── Step 5: Detect PR numbers in written pr-reviews.md ────────────────
        print("  [5/6] Scanning for PR numbers...")
        pr_file = inputs_path / self.config.dump_pr_merge_file
        if pr_file.exists():
            result.pr_numbers = _extract_pr_numbers(pr_file.read_text(encoding="utf-8"))
            if result.pr_numbers:
                print(
                    f"        Found {len(result.pr_numbers)} PR(s): {', '.join('#' + n for n in result.pr_numbers)}"
                )
            else:
                print("        No PR numbers detected")
        else:
            print("        No pr-reviews.md found — skipping")

        # ── Step 6: Collect all Jira IDs from written task files ─────────────
        print("  [6/6] Collecting Jira ticket IDs from task files...")
        all_jira_ids: set[str] = set()
        for section in all_sections:
            if section.jira_ids:
                all_jira_ids.update(section.jira_ids)
        result.jira_ids_detected = sorted(all_jira_ids)
        if result.jira_ids_detected:
            print(f"        Found: {', '.join(result.jira_ids_detected)}")
        else:
            print("        No Jira ticket IDs detected")

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

        if result.pr_copied:
            print(f"\n     📋 PR comment files copied ({len(result.pr_copied)}):")
            for p in result.pr_copied:
                print(f"       → {p.name}")

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
            print("  ─── Next Steps ───────────────────────────────────────────")

            step = 1

            if meeting_files:
                print(
                    f"  {step} Fill meeting skeletons ({len(meeting_files)} file(s)):"
                )
                for mf in meeting_files:
                    print(f"       open {mf}")
                step += 1

            # ── PR comment instructions ───────────────────────────────────────────
            if result.pr_numbers:
                print(
                    f"{step}  Get PR comments ({len(result.pr_numbers)} PR(s) detected):"
                )
                cmd_template = self.config.enricher_pr_external_command
                for pr_num in result.pr_numbers:
                    cmd = cmd_template.format(pr_number=pr_num)
                    print(f"       {cmd}")

                step += 1
                print(f"  {step}   Copy PR comments to inputs:")
                print(f"       brain dump --date {date} --copy-pr-comments")
                step += 1

            # ── Jira context instructions ─────────────────────────────────────────
            if result.jira_ids_detected:
                print(
                    f"  {step}   Get Jira context ({len(result.jira_ids_detected)} ticket(s) detected):"
                )
                cmd_template = self.config.enricher_jira_external_command
                for ticket_id in result.jira_ids_detected:
                    cmd = cmd_template.format(ticket_id=ticket_id)
                    print(f"       {cmd}")
                step += 1
                print(f"  {step}   Copy Jira context to inputs:")
                print(f"       brain dump --date {date} --copy-jira-context")
                step += 1

            print(f"  {step} scp inputs/{date}/ to remote server")
            step += 1
            print(f"  {step}  python enricher.py --date {date}  (on remote)")
            step += 1
            print(
                f"  {step}  scp enriched/ back → brain compose --date {date} --ingest"
            )
            print("  ─────────────────────────────────────────────────────────")


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


# ── PR Comment Fetcher ────────────────────────────────────────────────────────


class PRCommentFetcher:
    """
    Copies PR comment files from the external script output directory
    into the inputs/YYYY-MM-DD/ folder for a given date.

    The external PR comment script is a black box — it writes files to a
    configured src_dir. This class picks up those files and copies them
    to the right place for enricher.py to consume.

    Flow:
      External script writes: ~/.brain/pr-comments/pr-103018-comments.md
      PRCommentFetcher copies: inputs/2026-04-02/pr-103018-comments.md

    Idempotency:
      Existing destination files are SKIPPED with a warning.
      Source files are never deleted — copy, not move.

    Usage:
      fetcher = PRCommentFetcher(config)
      result = fetcher.copy(pr_numbers=["103018", "4521"], date="2026-04-02")
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    def copy(
        self,
        pr_numbers: list[str],
        date: str,
        dry_run: bool = False,
    ) -> list[Path]:
        """
        Copy PR comment files for the given PR numbers into inputs/YYYY-MM-DD/.

        Args:
            pr_numbers : list of PR number strings e.g. ["103018", "4521"]
            date       : ISO date string YYYY-MM-DD
            dry_run    : if True, print what would be copied without copying

        Returns:
            List of destination paths successfully copied (or would-be on dry_run).

        Raises:
            FileNotFoundError: src_dir doesn't exist or isn't configured.
        """
        src_dir = self.config.enricher_pr_comments_src_dir
        dest_dir = self.config.dump_inputs_path(date)
        pattern = self.config.enricher_pr_comments_filename_pattern
        copied: list[Path] = []

        print("\n🧠 Brain Dump — PR Comment Copy")
        print(f"   Date    : {date}")
        print(f"   Source  : {src_dir}")
        print(f"   Dest    : {dest_dir}")
        print()

        if not src_dir.exists():
            raise FileNotFoundError(
                f"PR comments source directory not found: {src_dir}\n"
                f"Run your external PR comment script first, or update\n"
                f"enricher.pr_comments.src_dir in config.local.yaml"
            )

        if not pr_numbers:
            print("  ℹ️  No PR numbers provided — nothing to copy.")
            return []

        dest_dir.mkdir(parents=True, exist_ok=True)

        for pr_num in pr_numbers:
            filename = pattern.format(pr_number=pr_num)
            src_path = src_dir / filename
            dest_path = dest_dir / filename

            # ── Source file missing ───────────────────────────────────────────
            if not src_path.exists():
                print(
                    f"  ⚠️  Not found in src_dir: {filename}\n"
                    f"       Run: {self.config.enricher_pr_external_command.format(pr_number=pr_num)}"
                )
                continue

            # ── Destination already exists — skip ─────────────────────────────
            if dest_path.exists():
                print(f"  ⚠️  Already exists, skipping: {filename}")
                continue

            # ── Copy ──────────────────────────────────────────────────────────
            if dry_run:
                print(f"  [DRY RUN] Would copy: {filename}")
                print(f"            {src_path} → {dest_path}")
            else:
                import shutil

                shutil.copy2(str(src_path), str(dest_path))
                print(f"  ✅ Copied: {filename}")

            copied.append(dest_path)

        # ── Summary ───────────────────────────────────────────────────────────
        print()
        if copied:
            print(f"  ✅ {len(copied)} file(s) copied to inputs/{date}/")
            print("     Ready to scp to remote server for enrichment.")
        else:
            print("  ⚠️  No files copied — check warnings above.")
            print(f"     Source dir: {src_dir}")

        return copied


def _type_symbol(section_type: str) -> str:
    """Return an emoji symbol for the section type — used in console output."""
    return {
        "task": "📋",
        "pr": "🔀",
        "meeting": "🤝",
        "other": "📎",
    }.get(section_type, "•")


def _extract_pr_numbers(content: str) -> list[str]:
    """
    Extract PR numbers from markdown content.

    Detects two formats:
      Full URL : https://github.com/org/repo/pull/103018
      Hash ref : #103018  or  PR #103018  or  pull/103018

    Returns sorted deduplicated list of PR number strings.
    Strings not ints — PR numbers are identifiers, not quantities.

    Examples:
      "* https://github.com/org/repo/pull/103018"  → ["103018"]
      "* #4521 approved"                            → ["4521"]
      "reviewed PR #4521 and #4489 today"           → ["4489", "4521"]
    """
    found: set[str] = set()

    # Full GitHub URL pattern
    url_matches = re.findall(
        r"https?://github\.com/[^/]+/[^/]+/pull/(\d+)",
        content,
    )
    found.update(url_matches)

    # Hash ref pattern — #103018 or PR #103018 or pull/103018
    # Require word boundary before # to avoid matching mid-word
    hash_matches = re.findall(
        r"(?:^|[\s,(])#(\d{3,})",  # min 3 digits — avoids matching #1 etc
        content,
        flags=re.MULTILINE,
    )
    found.update(hash_matches)

    # plain pull/N pattern (without full URL)
    pull_matches = re.findall(
        r"\bpull/(\d+)\b",
        content,
    )
    found.update(pull_matches)

    return sorted(found)


# ── Jira Context Fetcher ──────────────────────────────────────────────────────


class JiraContextFetcher:
    """
    Copies Jira JSON context files from the external script output directory
    into inputs/YYYY-MM-DD/context/ for a given date.

    The external Jira script is a black box — it writes jira-TICKET.json
    files to a configured src_dir. This class copies them to the right
    place so enricher.py can read them without any credentials.

    Flow:
      External script writes: ~/.brain/jira-context/jira-ENG-161845.json
      JiraContextFetcher copies: inputs/2026-04-02/context/jira-ENG-161845.json

    Idempotent: existing files are SKIPPED with a warning.
    Source files are never deleted — copy, not move.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    def copy(
        self,
        ticket_ids: list[str],
        date: str,
        dry_run: bool = False,
    ) -> list[Path]:
        """
        Copy Jira JSON files for the given ticket IDs into
        inputs/YYYY-MM-DD/context/.

        Args:
            ticket_ids : list of ticket ID strings e.g. ["ENG-161845", "CS-15011"]
            date       : ISO date string YYYY-MM-DD
            dry_run    : if True, print what would be copied without copying

        Returns:
            List of destination paths successfully copied.

        Raises:
            FileNotFoundError: src_dir doesn't exist or isn't configured.
        """
        import shutil

        src_dir = self.config.enricher_jira_src_dir
        dest_dir = self.config.enricher_context_path(date)
        pattern = self.config.enricher_jira_filename_pattern
        copied: list[Path] = []

        print("\n🧠 Brain Dump — Jira Context Copy")
        print(f"   Date   : {date}")
        print(f"   Source : {src_dir}")
        print(f"   Dest   : {dest_dir}")
        print()

        if not src_dir.exists():
            raise FileNotFoundError(
                f"Jira context source directory not found: {src_dir}\n"
                f"Run your external Jira script first, or update\n"
                f"enricher.jira_script.src_dir in config.local.yaml"
            )

        if not ticket_ids:
            print("  ℹ️  No Jira ticket IDs provided — nothing to copy.")
            return []

        dest_dir.mkdir(parents=True, exist_ok=True)

        for ticket_id in ticket_ids:
            filename = pattern.format(ticket_id=ticket_id)
            src_path = src_dir / filename
            dest_path = dest_dir / filename

            if not src_path.exists():
                cmd = self.config.enricher_jira_external_command.format(
                    ticket_id=ticket_id
                )
                print(f"  ⚠️  Not found in src_dir: {filename}\n       Run: {cmd}")
                continue

            if dest_path.exists():
                print(f"  ⚠️  Already exists, skipping: {filename}")
                continue

            if dry_run:
                print(f"  [DRY RUN] Would copy: {filename}")
            else:
                shutil.copy2(str(src_path), str(dest_path))
                print(f"  ✅ Copied: {filename}")

            copied.append(dest_path)

        print()
        if copied and not dry_run:
            print(f"  ✅ {len(copied)} Jira file(s) copied to {dest_dir}")
        elif not copied:
            print("  ⚠️  No files copied — check warnings above.")

        return copied
