"""
update_suggester.py — Phase 6b: External Update Suggestions.

Scans the composed daily summary for Jira ticket IDs, drafts a
progress comment per ticket using the local LLM, and manages the
draft lifecycle (pending → approved / rejected).

All state is file-based — no SQLite involvement.

Draft lifecycle:
  exports/updates/YYYY-MM-DD-CS-15011-draft.md     ← after --scan
  exports/updates/YYYY-MM-DD-CS-15011-approved.md  ← after --approve
  exports/updates/YYYY-MM-DD-CS-15011-rejected.md  ← after --reject

MCP posting is deliberately deferred — --approve prints the comment
to screen only. The human copies and posts manually for now.

Usage:
  suggester = UpdateSuggester(config)

  # Scan composed summary → write draft files
  result = suggester.scan(date="2026-04-10")

  # List all drafts (pending / approved / rejected)
  drafts = suggester.list_drafts()

  # Show a specific draft
  suggester.show(ticket_id="CS-15011")

  # Approve — prints comment to screen, renames file
  suggester.approve(ticket_id="CS-15011")

  # Reject — renames file, no posting
  suggester.reject(ticket_id="CS-15011")
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import requests

from brain.config import BrainConfig

# ── Data Structures ───────────────────────────────────────────────────────────


class DraftStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class DraftFile:
    """
    Represents a single update draft on disk.

    Fields
    ──────
    ticket_id : Jira ticket ID e.g. CS-15011
    date      : date the draft was generated for (YYYY-MM-DD)
    status    : pending | approved | rejected
    path      : full path to the file on disk
    """

    ticket_id: str
    date: str
    status: DraftStatus
    path: Path


@dataclass
class ScanResult:
    """
    Result of a single UpdateSuggester.scan() call.

    Fields
    ──────
    date            : ISO date string YYYY-MM-DD
    tickets_found   : Jira IDs detected in the summary
    drafts_written  : ticket IDs for which a draft was written
    drafts_skipped  : ticket IDs skipped (draft already exists)
    warnings        : non-fatal messages
    """

    date: str
    tickets_found: list[str] = field(default_factory=list)
    drafts_written: list[str] = field(default_factory=list)
    drafts_skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── UpdateSuggester ───────────────────────────────────────────────────────────


class UpdateSuggester:
    """
    Scans daily summaries, drafts Jira comments, manages draft lifecycle.

    Instantiate once, call methods per date or ticket.
    All config comes from BrainConfig — no hardcoded values.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    # ── Public API ─────────────────────────────────────────────────────────────

    def scan(self, date: str, dry_run: bool = False) -> ScanResult:
        """
        Scan composed daily summary for Jira IDs and draft comments.

        Args:
            date    : ISO date string YYYY-MM-DD
            dry_run : print what would be written without writing files

        Returns:
            ScanResult with ticket counts and draft paths.
        """
        result = ScanResult(date=date)

        # ── Step 1: Load composed summary ─────────────────────────────────────
        summary_path = self.config.daily_sensitive / f"{date}.md"
        if not summary_path.exists():
            raise FileNotFoundError(
                f"No composed summary found for {date}.\n"
                f"Expected: {summary_path}\n"
                f"Run: brain compose --date {date} --ingest first."
            )

        summary_content = summary_path.read_text(encoding="utf-8")
        print(f"\n🔍 Scanning: {summary_path.name}")

        # ── Step 1b: Verify Ollama is reachable ──────────────────────────────────
        try:
            health = requests.get(
                f"{self.config.ollama_base_url}",
                timeout=5,
            )
            health.raise_for_status()
        except Exception:
            raise RuntimeError(
                f"Ollama not reachable at {self.config.ollama_base_url}\n"
                f"Start it with: ollama serve"
            ) from None
        # ── Step 2: Detect Jira IDs ───────────────────────────────────────────
        ticket_ids = _extract_ticket_ids(
            summary_content, self.config.updates_ticket_pattern
        )
        result.tickets_found = ticket_ids

        if not ticket_ids:
            print("   No Jira ticket IDs found in summary.")
            return result

        print(f"   Found {len(ticket_ids)} ticket(s): {', '.join(ticket_ids)}")

        # ── Step 3: Ensure output directory exists ────────────────────────────
        if not dry_run:
            self.config.updates_output_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 4: Draft comment per ticket ──────────────────────────────────
        for ticket_id in ticket_ids:
            draft_path = self.config.updates_draft_path(date, ticket_id)
            approved_path = self.config.updates_approved_path(date, ticket_id)
            rejected_path = self.config.updates_rejected_path(date, ticket_id)

            # Skip if any state file already exists — idempotent
            if draft_path.exists() or approved_path.exists() or rejected_path.exists():
                print(f"   ⏭️  {ticket_id} — draft already exists, skipping")
                result.drafts_skipped.append(ticket_id)
                continue

            print(f"   ✍️  {ticket_id} — drafting comment via LLM...")

            # Extract ticket-specific context from summary
            context = _extract_ticket_context(summary_content, ticket_id)

            # Call local LLM
            comment_body = self._draft_comment(
                ticket_id=ticket_id,
                date=date,
                summary_context=context,
            )

            if not comment_body:
                msg = f"{ticket_id} — LLM returned empty response, skipping"
                print(f"   ⚠️  {msg}")
                result.warnings.append(msg)
                continue

            # Build draft file content
            draft_content = _build_draft_file(
                ticket_id=ticket_id,
                date=date,
                comment_body=comment_body,
                summary_path=summary_path,
            )

            if dry_run:
                print(f"   [DRY RUN] Would write: {draft_path}")
                print(f"   {'─' * 54}")
                for line in draft_content.splitlines()[:10]:
                    print(f"   {line}")
                if len(draft_content.splitlines()) > 10:
                    print(f"   ... ({len(draft_content.splitlines())} lines total)")
                print(f"   {'─' * 54}")
                result.drafts_written.append(ticket_id)  # ← count it in dry run too
            else:
                draft_path.write_text(draft_content, encoding="utf-8")
                print(f"   ✅ Written: {draft_path.name}")
                result.drafts_written.append(ticket_id)

        return result

    def list_drafts(self) -> list[DraftFile]:
        """
        List all draft files in exports/updates/ sorted by date descending.

        Returns list of DraftFile objects regardless of status.
        """
        output_dir = self.config.updates_output_dir
        if not output_dir.exists():
            return []

        drafts: list[DraftFile] = []

        for path in sorted(output_dir.glob("*.md"), reverse=True):
            draft = _parse_draft_filename(path)
            if draft:
                drafts.append(draft)

        return drafts

    def show(self, ticket_id: str) -> str:
        """
        Find and return the content of the most recent draft for a ticket.

        Raises FileNotFoundError if no draft exists for this ticket.
        """
        ticket_id = ticket_id.upper()
        draft = self._find_latest(ticket_id)

        if not draft:
            raise FileNotFoundError(
                f"No draft found for ticket {ticket_id}.\n"
                f"Run: brain update --scan --date YYYY-MM-DD first."
            )

        return draft.path.read_text(encoding="utf-8")

    def approve(self, ticket_id: str) -> DraftFile:
        """
        Approve a draft — print comment to screen, rename file to approved.

        MCP posting is deferred — prints the comment so the human can
        copy and post manually.

        Raises FileNotFoundError if no pending draft exists.
        Raises ValueError if draft is already approved or rejected.
        """
        ticket_id = ticket_id.upper()
        draft = self._find_latest(ticket_id, status=DraftStatus.PENDING)

        if not draft:
            # Check if already processed
            approved = self._find_latest(ticket_id, status=DraftStatus.APPROVED)
            rejected = self._find_latest(ticket_id, status=DraftStatus.REJECTED)
            if approved:
                raise ValueError(f"{ticket_id} is already approved.")
            if rejected:
                raise ValueError(
                    f"{ticket_id} is already rejected — use --scan to regenerate."
                )
            raise FileNotFoundError(
                f"No pending draft found for {ticket_id}.\n"
                f"Run: brain update --scan --date YYYY-MM-DD first."
            )

        # Extract just the comment body from the draft file
        comment_body = _extract_comment_from_draft(
            draft.path.read_text(encoding="utf-8")
        )

        # Print comment to screen — human copies and posts
        print(f"\n📋 Jira Comment — {ticket_id}")
        print("─" * 60)
        print(comment_body)
        print("─" * 60)
        print(f"\n👆 Copy the above and post to: {ticket_id}")
        print("   (MCP auto-posting coming in a future phase)")

        # Rename draft → approved
        approved_path = self.config.updates_approved_path(draft.date, ticket_id)
        draft.path.rename(approved_path)

        return DraftFile(
            ticket_id=ticket_id,
            date=draft.date,
            status=DraftStatus.APPROVED,
            path=approved_path,
        )

    def reject(self, ticket_id: str) -> DraftFile:
        """
        Reject a draft — rename file to rejected, no posting.

        Raises FileNotFoundError if no pending draft exists.
        Raises ValueError if draft is already approved or rejected.
        """
        ticket_id = ticket_id.upper()
        draft = self._find_latest(ticket_id, status=DraftStatus.PENDING)

        if not draft:
            approved = self._find_latest(ticket_id, status=DraftStatus.APPROVED)
            rejected = self._find_latest(ticket_id, status=DraftStatus.REJECTED)
            if approved:
                raise ValueError(f"{ticket_id} is already approved.")
            if rejected:
                raise ValueError(f"{ticket_id} is already rejected.")
            raise FileNotFoundError(
                f"No pending draft found for {ticket_id}.\n"
                f"Run: brain update --scan --date YYYY-MM-DD first."
            )

        rejected_path = self.config.updates_rejected_path(draft.date, ticket_id)
        draft.path.rename(rejected_path)

        print(f"\n🗑️  {ticket_id} — draft rejected and moved to: {rejected_path.name}")

        return DraftFile(
            ticket_id=ticket_id,
            date=draft.date,
            status=DraftStatus.REJECTED,
            path=rejected_path,
        )

    # ── LLM Integration ───────────────────────────────────────────────────────
    def _draft_comment(
        self,
        ticket_id: str,
        date: str,
        summary_context: str,
    ) -> str:
        prompt = self.config.updates_comment_prompt_template.format(
            ticket_id=ticket_id,
            date=date,
            summary_context=summary_context,
        )

        payload = {
            "model": self.config.ollama_llm_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0,
            },
        }

        try:
            response = requests.post(
                f"{self.config.ollama_base_url}/api/generate",
                json=payload,
                timeout=self.config.ollama_timeout,
            )
            response.raise_for_status()
            result = response.json().get("response", "").strip()
            if not result:
                print(f"   ⚠️  LLM returned empty response for {ticket_id}")
                print(f"        Model: {self.config.ollama_llm_model}")
                print(f"        URL  : {self.config.ollama_base_url}")
            return result

        except requests.exceptions.ConnectionError:
            print(f"   ⚠️  Ollama not reachable at {self.config.ollama_base_url}")
            return ""
        except requests.exceptions.Timeout:
            print(f"   ⚠️  Ollama timed out after {self.config.ollama_timeout}s")
            return ""
        except Exception as e:
            print(f"   ⚠️  LLM error: {type(e).__name__}: {e}")
            return ""

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _find_latest(
        self,
        ticket_id: str,
        status: DraftStatus | None = None,
    ) -> DraftFile | None:
        """
        Find the most recent draft file for a ticket ID.

        Args:
            ticket_id : Jira ticket ID (uppercase)
            status    : filter by status, or None to match any status
        """
        all_drafts = self.list_drafts()
        matches = [
            d
            for d in all_drafts
            if d.ticket_id == ticket_id and (status is None or d.status == status)
        ]
        return matches[0] if matches else None


# ── Module-Level Helpers ──────────────────────────────────────────────────────


def _extract_ticket_ids(content: str, pattern: str) -> list[str]:
    r"""
    Extract unique Jira ticket IDs from text, sorted and deduplicated.

    Normalises to uppercase. Filters out common false positives
    (e.g. time strings like 10:30 won't match [A-Z]{2,9}-\d+).
    """
    found = re.findall(pattern, content)
    # Deduplicate preserving first-seen order
    seen: set[str] = set()
    result: list[str] = []
    for ticket in found:
        upper = ticket.upper()
        if upper not in seen:
            seen.add(upper)
            result.append(upper)
    return result


def _extract_ticket_context(content: str, ticket_id: str) -> str:
    """
    Extract the paragraph(s) from the summary that mention a specific ticket.

    Strategy:
      1. Find all paragraphs/bullet lines mentioning the ticket ID
      2. Return up to 50 lines of surrounding context
      3. Fall back to full summary if nothing specific found

    This gives the LLM focused context rather than the entire summary.
    """
    lines = content.splitlines()
    relevant: list[str] = []
    ticket_upper = ticket_id.upper()

    for i, line in enumerate(lines):
        if ticket_upper in line.upper():
            # Grab a window of lines around each mention
            start = max(0, i - 3)
            end = min(len(lines), i + 8)
            relevant.extend(lines[start:end])
            relevant.append("")  # separator between windows

    if relevant:
        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for line in relevant:
            if line not in seen:
                seen.add(line)
                deduped.append(line)
        return "\n".join(deduped[:50])

    # Fallback — return full summary (LLM will do its best)
    return content


def _build_draft_file(
    ticket_id: str,
    date: str,
    comment_body: str,
    summary_path: Path,
) -> str:
    """
    Build the full content of a draft file.

    Format is human-readable markdown so you can review it easily.
    The comment body is delimited so _extract_comment_from_draft()
    can reliably pull it back out.
    """
    return f"""\
# Update Draft — {ticket_id} — {date}

> **Status:** pending
> **Source:** {summary_path.name}
> **Ticket:** {ticket_id}

---

<!-- COMMENT_START -->
{comment_body}
<!-- COMMENT_END -->

---

## Review

- Approve: `brain update --approve {ticket_id}`
- Reject:  `brain update --reject {ticket_id}`

*Draft generated by brain update --scan. Review before approving.*
"""


def _extract_comment_from_draft(content: str) -> str:
    """
    Extract the comment body from a draft file.

    Looks for content between <!-- COMMENT_START --> and <!-- COMMENT_END -->
    delimiters. Falls back to full content if delimiters are missing.
    """
    match = re.search(
        r"<!-- COMMENT_START -->\n(.*?)\n<!-- COMMENT_END -->",
        content,
        re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return content.strip()


def _parse_draft_filename(path: Path) -> DraftFile | None:
    """
    Parse a draft filename into a DraftFile.

    Expected patterns:
      2026-04-10-CS-15011-draft.md
      2026-04-10-CS-15011-approved.md
      2026-04-10-CS-15011-rejected.md
    """
    name = path.stem  # strip .md

    # Match: YYYY-MM-DD-TICKET-status
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})-([A-Z]{2,9}-\d+)-(draft|approved|rejected)$",
        name,
        re.IGNORECASE,
    )
    if not match:
        return None

    date_str = match.group(1)
    ticket_id = match.group(2).upper()
    status_str = match.group(3).lower()

    status_map = {
        "draft": DraftStatus.PENDING,
        "approved": DraftStatus.APPROVED,
        "rejected": DraftStatus.REJECTED,
    }

    return DraftFile(
        ticket_id=ticket_id,
        date=date_str,
        status=status_map[status_str],
        path=path,
    )
