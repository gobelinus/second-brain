"""
anonymize.py — Standalone entry point for anonymizing sensitive files.

Thin wrapper around brain.anonymizer.Anonymizer — all logic lives there.
This script exists so you can run it directly without the brain CLI:

Usage:
  python scripts/anonymize.py path/to/file.md
  python scripts/anonymize.py daily/sensitive/2026-02-24.md | pbcopy
  python scripts/anonymize.py daily/sensitive/2026-02-24.md > /tmp/safe.md
  python scripts/anonymize.py daily/sensitive/2026-02-24.md --diff

For the CLI path, use:
  brain anonymize daily/sensitive/2026-02-24.md --diff
"""

import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from brain.anonymizer import Anonymizer, print_diff, print_stats


def main(args: list[str]) -> int:
    show_diff = "--diff" in args
    file_args = [a for a in args if not a.startswith("--")]

    if not file_args:
        print(
            "Usage: python scripts/anonymize.py path/to/file.md [--diff]",
            file=sys.stderr,
        )
        print(
            "       python scripts/anonymize.py daily/sensitive/2026-02-24.md | pbcopy",
            file=sys.stderr,
        )
        return 1

    file_path = Path(file_args[0])

    if not file_path.exists():
        print(f"❌ File not found: {file_path}", file=sys.stderr)
        return 1

    if file_path.suffix not in (".md", ".txt", ".log", ".py", ".json"):
        print(
            f"⚠️  Unexpected file type: {file_path.suffix} — proceeding anyway",
            file=sys.stderr,
        )

    original = file_path.read_text(encoding="utf-8")

    anonymizer = Anonymizer()
    anonymized = anonymizer.anonymize(original)

    # Stats and diff always go to stderr — never pollute the piped stdout output
    stats = anonymizer.redaction_stats(original, anonymized)
    print_stats(stats)

    if show_diff:
        print_diff(original, anonymized)

    # Anonymized content to stdout — pipe-friendly
    print(anonymized)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
