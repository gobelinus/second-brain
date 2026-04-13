"""
cleaner.py — Phase 7b: brain clean.

Safely removes processed inputs/YYYY-MM-DD/ directories after
enrichment and ingestion are confirmed complete.

Safety checks (in order):
  1. Date must be ingested in SQLite — proof compose + ingest ran
  2. inputs/archive/YYYY-MM-DD/ must exist — proof compose archived inputs
  3. Confirmation prompt (unless --yes) — destructive ops need speed bumps

What gets removed vs kept:
  REMOVES : inputs/YYYY-MM-DD/           (raw + enriched inputs)
  KEEPS   : inputs/archive/YYYY-MM-DD/   (archived source files)
  KEEPS   : daily/sensitive/YYYY-MM-DD.md (composed output)
  KEEPS   : store/                        (SQLite + ChromaDB)

Usage:
  cleaner = Cleaner(config)

  # Single date
  result = cleaner.clean(date="2026-04-01")
  result = cleaner.clean(date="2026-04-01", dry_run=True)
  result = cleaner.clean(date="2026-04-01", yes=True)

  # All cleanable dates
  dates = cleaner.cleanable_dates()
  results = cleaner.clean_all(dry_run=True)

  # Status
  cleaner.status()
"""

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from brain.config import BrainConfig

# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class CleanResult:
    """
    Result of a single Cleaner.clean() call.

    Fields
    ──────
    date         : ISO date string YYYY-MM-DD
    cleaned      : True if inputs/YYYY-MM-DD/ was removed
    skipped      : True if date was skipped (safety check failed)
    skip_reason  : human-readable reason if skipped
    files_removed: count of files removed
    bytes_removed: total bytes freed
    dry_run      : True if this was a dry run
    """

    date: str
    cleaned: bool = False
    skipped: bool = False
    skip_reason: str = ""
    files_removed: int = 0
    bytes_removed: int = 0
    dry_run: bool = False


@dataclass
class CleanStatus:
    """
    A single cleanable date entry for --status output.

    Fields
    ──────
    date         : ISO date string YYYY-MM-DD
    inputs_path  : full path to inputs/YYYY-MM-DD/
    file_count   : number of files in inputs/YYYY-MM-DD/
    total_bytes  : total size of inputs/YYYY-MM-DD/
    """

    date: str
    inputs_path: Path
    file_count: int
    total_bytes: int

    @property
    def total_mb(self) -> float:
        return round(self.total_bytes / (1024 * 1024), 2)


# ── Cleaner ───────────────────────────────────────────────────────────────────


class Cleaner:
    """
    Safely removes processed inputs/ directories after ingestion.

    All safety checks run before any deletion.
    Dry run always available — non-negotiable for destructive commands.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    # ── Public API ─────────────────────────────────────────────────────────────

    def clean(
        self,
        date: str,
        dry_run: bool = False,
        yes: bool = False,
    ) -> CleanResult:
        """
        Clean inputs/YYYY-MM-DD/ for a single date.

        Runs safety checks, prompts for confirmation, then removes directory.

        Args:
            date    : ISO date string YYYY-MM-DD
            dry_run : show what would be removed without removing
            yes     : skip confirmation prompt

        Returns:
            CleanResult with outcome details.
        """
        result = CleanResult(date=date, dry_run=dry_run)
        inputs_path = self.config.inputs / date

        print(f"\n🧹 Brain Clean — {date}")

        # ── Safety Check 1: inputs/ directory exists ──────────────────────────
        if not inputs_path.exists():
            result.skipped = True
            result.skip_reason = f"inputs/{date}/ not found — nothing to clean"
            print(f"   ℹ️  {result.skip_reason}")
            return result

        # ── Safety Check 2: date ingested in SQLite ───────────────────────────
        print("   [1/2] Checking SQLite ingest status...")
        if not self._is_ingested(date):
            result.skipped = True
            result.skip_reason = (
                f"Not ingested in SQLite — run: "
                f"brain compose --date {date} --ingest first"
            )
            print(f"   ❌ BLOCKED: {result.skip_reason}")
            return result
        print("        ✅ Ingested in SQLite")

        # ── Safety Check 3: archive directory exists ──────────────────────────
        print("   [2/2] Checking archive status...")
        archive_path = self.config.inputs_archive / date
        if not archive_path.exists():
            result.skipped = True
            result.skip_reason = (
                f"inputs/archive/{date}/ not found — "
                f"compose may not have run. "
                f"Run: brain compose --date {date} --ingest first"
            )
            print(f"   ❌ BLOCKED: {result.skip_reason}")
            return result
        print(f"        ✅ Archive exists: {archive_path}")

        # ── Inventory what would be removed ───────────────────────────────────
        all_files = list(inputs_path.rglob("*"))
        files_only = [f for f in all_files if f.is_file()]
        total_bytes = sum(f.stat().st_size for f in files_only)
        total_mb = round(total_bytes / (1024 * 1024), 2)

        result.files_removed = len(files_only)
        result.bytes_removed = total_bytes

        print()
        print(f"   📁 Would remove: {inputs_path}")
        print(f"      Files : {len(files_only)}")
        print(f"      Size  : {total_mb} MB")
        print()
        print(f"   📦 Kept : inputs/archive/{date}/")
        print(f"   📄 Kept : daily/sensitive/{date}.md")
        print("   🗃️  Kept : store/ (SQLite + ChromaDB)")

        if dry_run:
            print()
            print("   [DRY RUN] Nothing removed")
            return result

        # ── Confirmation prompt ───────────────────────────────────────────────
        if not yes:
            print()
            confirm = input(f"   Remove inputs/{date}/? [y/N] ").strip().lower()
            if confirm != "y":
                result.skipped = True
                result.skip_reason = "Cancelled by user"
                print("   Cancelled.")
                return result

        # ── Remove ────────────────────────────────────────────────────────────
        shutil.rmtree(inputs_path)
        result.cleaned = True
        print(f"\n   ✅ Removed: {inputs_path}")

        return result

    def clean_all(
        self,
        dry_run: bool = False,
        yes: bool = False,
    ) -> list[CleanResult]:
        """
        Clean all cleanable dates in one pass.

        Finds all dates that are:
          - Ingested in SQLite
          - Have inputs/archive/YYYY-MM-DD/ present
          - Have inputs/YYYY-MM-DD/ still on disk

        Prompts once for all dates combined (unless --yes).

        Args:
            dry_run : show what would be removed without removing
            yes     : skip confirmation prompt

        Returns:
            List of CleanResult — one per cleanable date.
        """
        dates = self.cleanable_dates()

        if not dates:
            print("\n   ✅ Nothing to clean — no cleanable dates found")
            return []

        # ── Show summary of what would be cleaned ─────────────────────────────
        print(f"\n🧹 Brain Clean — All ({len(dates)} date(s))")
        print()

        total_files = 0
        total_bytes = 0

        print(f"   {'DATE':<14} {'FILES':<8} SIZE")
        print("   " + "─" * 35)

        for status in dates:
            print(f"   {status.date:<14} {status.file_count:<8} {status.total_mb} MB")
            total_files += status.file_count
            total_bytes += status.total_bytes

        total_mb = round(total_bytes / (1024 * 1024), 2)
        print("   " + "─" * 35)
        print(f"   {'TOTAL':<14} {total_files:<8} {total_mb} MB")
        print()

        if dry_run:
            print("   [DRY RUN] Nothing removed")
            return [CleanResult(date=s.date, dry_run=True) for s in dates]

        # ── Single confirmation for all dates ─────────────────────────────────
        if not yes:
            confirm = (
                input(f"   Remove {len(dates)} inputs/ director(ies)? [y/N] ")
                .strip()
                .lower()
            )
            if confirm != "y":
                print("   Cancelled.")
                return [
                    CleanResult(
                        date=s.date,
                        skipped=True,
                        skip_reason="Cancelled by user",
                    )
                    for s in dates
                ]

        # ── Clean each date (yes=True — already confirmed) ────────────────────
        results = []
        for status in dates:
            result = self.clean(date=status.date, dry_run=False, yes=True)
            results.append(result)

        return results

    def cleanable_dates(self) -> list[CleanStatus]:
        """
        Find all dates that are safe to clean.

        A date is cleanable when ALL of these are true:
          1. inputs/YYYY-MM-DD/ exists on disk
          2. Ingested in SQLite
          3. inputs/archive/YYYY-MM-DD/ exists

        Returns list of CleanStatus sorted by date ascending.
        """
        ingested = self._ingested_dates()
        cleanable: list[CleanStatus] = []

        # Scan inputs/ for date directories
        if not self.config.inputs.exists():
            return []

        for entry in sorted(self.config.inputs.iterdir()):
            # Skip non-directories and archive/
            if not entry.is_dir():
                continue
            if entry.name == "archive":
                continue

            date = entry.name

            # Must look like a date
            if not _is_date(date):
                continue

            # Must be ingested
            if date not in ingested:
                continue

            # Archive must exist
            archive_path = self.config.inputs_archive / date
            if not archive_path.exists():
                continue

            # Count files and size
            all_files = [f for f in entry.rglob("*") if f.is_file()]
            total_bytes = sum(f.stat().st_size for f in all_files)

            cleanable.append(
                CleanStatus(
                    date=date,
                    inputs_path=entry,
                    file_count=len(all_files),
                    total_bytes=total_bytes,
                )
            )

        return cleanable

    def status(self) -> None:
        """
        Print all cleanable dates — dates safe to clean right now.
        """
        print("\n🧹 Brain Clean — Status")
        print()

        cleanable = self.cleanable_dates()

        if not cleanable:
            print("   ✅ Nothing to clean")
            print("      Either inputs/ directories are already removed,")
            print("      or dates are not yet ingested + archived.")
            print()
            return

        total_files = 0
        total_bytes = 0

        print(f"   {'DATE':<14} {'FILES':<8} {'SIZE':<10} PATH")
        print("   " + "─" * 60)

        for s in cleanable:
            print(
                f"   {s.date:<14} "
                f"{s.file_count:<8} "
                f"{s.total_mb} MB{'':<4} "
                f"{s.inputs_path}"
            )
            total_files += s.file_count
            total_bytes += s.total_bytes

        total_mb = round(total_bytes / (1024 * 1024), 2)
        print("   " + "─" * 60)
        print(f"   {'TOTAL':<14} {total_files:<8} {total_mb} MB")
        print()
        print("   To clean all:")
        print("     brain clean --all --dry-run")
        print("     brain clean --all")
        print()

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _is_ingested(self, date: str) -> bool:
        """Check if a date has any entry in SQLite daily_summaries."""
        db_path = self.config.sqlite_db
        if not db_path.exists():
            return False
        try:
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT 1 FROM daily_summaries WHERE date = ? LIMIT 1",
                (date,),
            ).fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    def _ingested_dates(self) -> set[str]:
        """Return all dates present in SQLite daily_summaries."""
        db_path = self.config.sqlite_db
        if not db_path.exists():
            return set()
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute("SELECT DISTINCT date FROM daily_summaries").fetchall()
            conn.close()
            return {r[0] for r in rows}
        except Exception:
            return set()


# ── Module Helpers ────────────────────────────────────────────────────────────


def _is_date(name: str) -> bool:
    """Check if a string looks like YYYY-MM-DD."""
    import re

    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", name))
