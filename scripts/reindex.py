#!/usr/bin/env python3
"""
scripts/reindex.py — Full corpus reindex for Brain.

Scans all known source directories and ingests every markdown file into
SQLite + ChromaDB. Idempotent by default — skips files already ingested
at the correct sensitivity. Use --rebuild to wipe and start fresh.

Usage
─────
  # Incremental — only ingest files not yet in the store
  uv run python scripts/reindex.py

  # Full rebuild — wipe SQLite + ChromaDB, re-ingest everything
  uv run python scripts/reindex.py --rebuild

  # Dry run — show what would be ingested, touch nothing
  uv run python scripts/reindex.py --dry-run
  uv run python scripts/reindex.py --rebuild --dry-run

  # Single directory only
  uv run python scripts/reindex.py --path daily/public

  # Verbose — print each file as it's processed
  uv run python scripts/reindex.py --verbose

Why This Exists
───────────────
Manual `brain ingest` works for daily additions. Reindex is for:
  - First-time setup on a new machine (clone repo → reindex)
  - After adding date_int or other ChromaDB metadata fields (requires re-ingest)
  - After deleting summaries (--rebuild removes ghost SQLite/ChromaDB records)
  - After migrating sensitivity of a file

Source Directories (from config.yaml)
──────────────────────────────────────
  daily/public/     → sensitivity: public,    pipeline: daily
  daily/sensitive/  → sensitivity: sensitive, pipeline: daily
  blogs/public/     → sensitivity: public,    pipeline: blog (auto-detected)
  blogs/sensitive/  → sensitivity: sensitive, pipeline: blog (auto-detected)

The script uses IngestPipeline.route() which auto-detects blog vs daily,
so no per-directory pipeline selection is needed here.
"""

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── Path bootstrap — make src/ importable when run directly ───────────────────
# Handles both `uv run python scripts/reindex.py` and `python scripts/reindex.py`
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from brain.config import BrainConfig  # NOQA
from brain.ingest import ChromaStore, IngestPipeline, SQLiteStore  # NOQA


# ── Source directory spec ──────────────────────────────────────────────────────


@dataclass
class SourceDir:
    """A directory to scan with its associated sensitivity label."""

    path: Path
    sensitivity: str

    def label(self) -> str:
        return f"{self.path} ({self.sensitivity})"


# ── Run statistics ─────────────────────────────────────────────────────────────


@dataclass
class ReindexStats:
    """Accumulated counts across the full reindex run."""

    found: int = 0
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)

    def record_error(self, path: Path, error: str) -> None:
        self.failed += 1
        self.errors.append((path, error))

    def summary_line(self) -> str:
        return (
            f"  Found: {self.found}  |  "
            f"Ingested: {self.ingested}  |  "
            f"Skipped: {self.skipped}  |  "
            f"Failed: {self.failed}"
        )


# ── Reindexer ─────────────────────────────────────────────────────────────────


class Reindexer:
    """
    Scans configured source directories and runs the ingest pipeline
    on each file found, with optional rebuild (wipe + re-ingest) mode.
    """

    def __init__(self, config: BrainConfig):
        self.config = config
        self.sqlite = SQLiteStore(config.sqlite_db)
        self.chroma = ChromaStore(config.chroma_store, config)

    def source_dirs(self) -> list[SourceDir]:
        """
        Resolve all source directories from config.
        Returns only directories that actually exist on disk — missing
        directories are silently skipped (e.g. blogs/ may not exist yet).
        """
        candidates = [
            SourceDir(self.config.daily_public_path, "public"),
            SourceDir(self.config.daily_sensitive_path, "sensitive"),
            SourceDir(self.config.blogs_public_path, "public"),
            SourceDir(self.config.blogs_sensitive_path, "sensitive"),
        ]
        return [s for s in candidates if s.path.exists()]

    def collect_files(self, source_dirs: list[SourceDir]) -> list[tuple[Path, str]]:
        """
        Collect all .md files from the given source directories.
        Returns list of (file_path, sensitivity) tuples, sorted by path
        for deterministic ordering.
        """
        files: list[tuple[Path, str]] = []
        for src in source_dirs:
            found = sorted(src.path.glob("*.md"))
            for f in found:
                files.append((f, src.sensitivity))
        return files

    def wipe_stores(self, dry_run: bool = False) -> None:
        """
        Wipe SQLite table and ChromaDB collections.
        Used by --rebuild before re-ingesting everything.
        """
        print("  🗑️  Wiping stores...")

        if dry_run:
            print("     [DRY RUN] Would delete all SQLite rows")
            print("     [DRY RUN] Would delete all ChromaDB collections")
            return

        # ── Wipe SQLite ───────────────────────────────────────────────────────
        try:
            db_path = self.config.sqlite_db
            if db_path.exists():
                conn = sqlite3.connect(db_path)
                deleted = conn.execute("DELETE FROM daily_summaries").rowcount
                conn.commit()
                conn.close()
                print(f"     SQLite: {deleted} row(s) deleted ✅")
            else:
                print(
                    "     SQLite: no database found — will be created on first ingest"
                )
        except Exception as e:
            print(f"     ⚠️  SQLite wipe failed: {e}")

        # ── Wipe ChromaDB — delete and recreate each collection ───────────────
        collections_to_wipe = [
            self.config.chroma_collection_for("public"),
            self.config.chroma_collection_for("sensitive"),
        ]
        # Also wipe blog collections if they exist
        for blog_col in (
            getattr(self.config, "blog_public_collection", None),
            getattr(self.config, "blog_sensitive_collection", None),
        ):
            if blog_col:
                collections_to_wipe.append(blog_col)

        for col_name in collections_to_wipe:
            try:
                self.chroma.client.delete_collection(col_name)
                print(f"     ChromaDB '{col_name}': deleted ✅")
            except Exception:
                # Collection doesn't exist yet — fine
                print(f"     ChromaDB '{col_name}': not found (skipped)")

    def run(
        self,
        rebuild: bool = False,
        dry_run: bool = False,
        path_filter: Path | None = None,
        verbose: bool = False,
    ) -> ReindexStats:
        """
        Main entry point. Orchestrates the full reindex run.

        Args:
            rebuild     : wipe stores before ingesting
            dry_run     : print plan but touch nothing
            path_filter : limit scan to a single directory
            verbose     : print each file as it's processed

        Returns:
            ReindexStats with counts of found/ingested/skipped/failed
        """
        stats = ReindexStats()

        # ── Resolve source directories ────────────────────────────────────────
        all_dirs = self.source_dirs()

        if path_filter:
            path_filter = path_filter.resolve()
            all_dirs = [s for s in all_dirs if s.path.resolve() == path_filter]
            if not all_dirs:
                # Check if the path exists at all — helps with typos
                if not path_filter.exists():
                    print(f"❌ Path not found: {path_filter}")
                else:
                    print(
                        f"❌ Path not in configured source dirs: {path_filter}\n"
                        f"   Configured dirs: "
                        + ", ".join(str(s.path) for s in self.source_dirs())
                    )
                return stats

        if not all_dirs:
            print("⚠️  No source directories found. Have you created daily/public/ yet?")
            return stats

        # ── Print plan ────────────────────────────────────────────────────────
        print(
            f"\n🧠 Brain Reindex{'  [DRY RUN]' if dry_run else ''}"
            f"{'  [REBUILD]' if rebuild else ''}"
        )
        print("─" * 50)
        print("  Source directories:")
        for src in all_dirs:
            print(f"    {src.label()}")
        print()

        # ── Collect files ─────────────────────────────────────────────────────
        files = self.collect_files(all_dirs)
        stats.found = len(files)

        if not files:
            print("  ⚠️  No .md files found in source directories.")
            return stats

        print(f"  📄 {stats.found} file(s) found")
        print()

        # ── Wipe stores if rebuild ────────────────────────────────────────────
        if rebuild:
            self.wipe_stores(dry_run=dry_run)
            print()

        # ── Ingest each file ──────────────────────────────────────────────────
        pipeline = IngestPipeline(self.config)
        start_time = time.monotonic()

        for i, (file_path, sensitivity) in enumerate(files, 1):
            prefix = f"  [{i:>3}/{stats.found}]"

            # ── Skip check (incremental mode only) ───────────────────────────
            if not rebuild:
                date_str = file_path.stem  # filename without .md = date
                already_ingested = self.sqlite.date_exists(date_str, sensitivity)
                if already_ingested:
                    stats.skipped += 1
                    if verbose:
                        print(f"{prefix} ⏭️  SKIP  {file_path.name} ({sensitivity})")
                    continue

            if dry_run:
                print(f"{prefix} 📋 WOULD INGEST  {file_path.name} ({sensitivity})")
                stats.ingested += 1
                continue

            # ── Ingest ────────────────────────────────────────────────────────
            try:
                # Suppress pipeline's own print output unless verbose
                if not verbose:
                    _suppress_pipeline_output(pipeline, file_path, sensitivity)
                else:
                    print(f"{prefix} ⚙️  Ingesting {file_path.name} ({sensitivity})")
                    pipeline.route(file_path=file_path, sensitivity=sensitivity)

                stats.ingested += 1

                if not verbose:
                    print(f"{prefix} ✅ {file_path.name} ({sensitivity})")

            except Exception as e:
                error_msg = str(e)
                stats.record_error(file_path, error_msg)
                print(f"{prefix} ❌ FAILED  {file_path.name}: {error_msg[:80]}")

        # ── Summary ───────────────────────────────────────────────────────────
        elapsed = time.monotonic() - start_time
        print()
        print("─" * 50)
        print(f"  {'[DRY RUN] ' if dry_run else ''}Reindex complete  ({elapsed:.1f}s)")
        print(stats.summary_line())

        if stats.errors:
            print()
            print(f"  ❌ {stats.failed} file(s) failed:")
            for path, err in stats.errors:
                print(f"     {path.name}: {err[:100]}")

        print()
        return stats


# ── Output suppression helper ──────────────────────────────────────────────────


def _suppress_pipeline_output(
    pipeline: IngestPipeline,
    file_path: Path,
    sensitivity: str,
) -> None:
    """
    Run pipeline.route() while suppressing its stdout progress prints.
    Used in non-verbose mode so reindex output stays clean and scannable.

    We redirect sys.stdout temporarily rather than patching print() — this
    works correctly even if the pipeline imports print from builtins.
    """
    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        pipeline.route(file_path=file_path, sensitivity=sensitivity)
    finally:
        sys.stdout = old_stdout


# ── CLI ────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reindex",
        description=(
            "🧠 Brain Reindex — scan all source directories and ingest into "
            "SQLite + ChromaDB.\n\n"
            "Default (no flags): incremental — skips already-ingested files.\n"
            "Use --rebuild to wipe stores and start completely fresh."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  uv run python scripts/reindex.py                  # incremental
  uv run python scripts/reindex.py --rebuild        # wipe + full re-ingest
  uv run python scripts/reindex.py --dry-run        # preview only
  uv run python scripts/reindex.py --rebuild --dry-run
  uv run python scripts/reindex.py --path daily/public
  uv run python scripts/reindex.py --verbose
        """,
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        default=False,
        help="Wipe SQLite + ChromaDB before ingesting. Re-ingests ALL files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would be ingested without writing anything.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        metavar="DIR",
        help="Limit scan to a single directory (e.g. daily/public).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print full pipeline output for each file (useful for debugging).",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # ── Safety prompt for destructive rebuild ─────────────────────────────────
    if args.rebuild and not args.dry_run:
        print("\n⚠️  --rebuild will WIPE all SQLite rows and ChromaDB collections.")
        print("   All files will be re-ingested from scratch.")
        answer = input("   Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("   Aborted.")
            return 0

    try:
        config = BrainConfig()
    except Exception as e:
        print(f"\n❌ Config error: {e}", file=sys.stderr)
        return 1

    reindexer = Reindexer(config)
    stats = reindexer.run(
        rebuild=args.rebuild,
        dry_run=args.dry_run,
        path_filter=args.path,
        verbose=args.verbose,
    )

    # Exit 1 if any files failed — useful for CI / cron alerting
    return 1 if stats.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
