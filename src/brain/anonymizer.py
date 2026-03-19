"""
anonymize.py — Redact sensitive data from markdown files for safe debugging.

Reads a file, applies all anonymization rules from config.yaml,
and prints the sanitized content to stdout. Nothing is written to disk.

Usage:
  python scripts/anonymize.py path/to/file.md
  python scripts/anonymize.py daily/sensitive/2026-02-24.md | pbcopy
  python scripts/anonymize.py daily/sensitive/2026-02-24.md > /tmp/safe.md
  python scripts/anonymize.py daily/sensitive/2026-02-24.md --diff

Redaction order (matters — more specific patterns run first):
  1. AWS ARNs              → [AWS_ARN]         (before account ID — ARNs contain 12-digit IDs)
  2. AWS key IDs           → [AWS_KEY_ID]
  3. AWS account IDs       → [AWS_ACCOUNT_ID]
  4. Generic secrets       → [REDACTED]
  5. Internal URLs         → [INTERNAL_URL]    (before hostnames — URLs are more specific)
  6. Internal IPs          → [INTERNAL_IP]
  7. Internal hostnames    → [INTERNAL_HOST]
  8. Local paths           → [LOCAL_PATH]
  9. People names          → Person_A, Person_B ... (longest first, word-boundary matched)
"""

import re
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from brain.config import BrainConfig

# ── Anonymizer ────────────────────────────────────────────────────────────────


class Anonymizer:
    """
    Applies all redaction rules from config.yaml to a block of text.
    Stateless — safe to call multiple times with the same instance.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()
        self._cfg = self.config._cfg.get("anonymizer", {})

    def anonymize(self, content: str) -> str:
        """
        Apply all redaction rules in order.
        Returns the fully anonymized string.
        """
        text = content

        # 1. AWS ARNs — must run before account ID (ARNs contain 12-digit account IDs)
        text = self._apply_pattern(
            text, self._cfg.get("aws_arn_pattern", ""), "[AWS_ARN]"
        )

        # 2. AWS key IDs
        text = self._apply_pattern(
            text, self._cfg.get("aws_key_pattern", ""), "[AWS_KEY_ID]"
        )

        # 3. AWS account IDs
        text = self._apply_pattern(
            text, self._cfg.get("aws_account_id_pattern", ""), "[AWS_ACCOUNT_ID]"
        )

        # 4. Generic secrets
        for pattern in self._cfg.get("secret_patterns", []):
            text = self._apply_pattern(text, pattern, "[REDACTED]")

        # 5. Internal URLs — run before hostname patterns (URLs are more specific)
        for pattern in self._cfg.get("internal_url_patterns", []):
            text = self._apply_pattern(text, pattern, "[INTERNAL_URL]")

        # 6. Internal IPs
        for pattern in self._cfg.get("ip_patterns", []):
            text = self._apply_pattern(text, pattern, "[INTERNAL_IP]")

        # 7. Internal hostnames
        for pattern in self._cfg.get("hostname_patterns", []):
            text = self._apply_pattern(text, pattern, "[INTERNAL_HOST]")

        # 8. Local absolute paths (stacktraces, debug output)
        text = self._apply_pattern(
            text, self._cfg.get("local_path_pattern", ""), "[LOCAL_PATH]"
        )

        # 9. People names — longest names first to avoid partial replacement
        #    e.g. replace "Rahul Balani" before "Rahul"
        #    \b word boundary prevents "Rahul" matching inside "Rahulkumar"
        #    re.escape handles names with dots, hyphens, or special chars
        people = self._cfg.get("people", {})
        sorted_names = sorted(people.keys(), key=len, reverse=True)
        for name in sorted_names:
            alias = people[name]
            text = re.sub(rf"\b{re.escape(name)}\b", alias, text)

        return text

    def redaction_stats(self, original: str, anonymized: str) -> dict[str, int]:
        """
        Return a dict of {tag: count} for all redactions made.

        Tag counts come from the anonymized text (where replacements landed).
        People counts come from the original (names are gone from anonymized).
        Uses self._cfg — no second BrainConfig() load needed.

        Returns only tags/names that had at least one match — clean for display.
        """
        tags = [
            "[AWS_ARN]",
            "[AWS_KEY_ID]",
            "[AWS_ACCOUNT_ID]",
            "[REDACTED]",
            "[INTERNAL_URL]",
            "[INTERNAL_IP]",
            "[INTERNAL_HOST]",
            "[LOCAL_PATH]",
        ]
        stats: dict[str, int] = {
            tag: anonymized.count(tag) for tag in tags if anonymized.count(tag) > 0
        }

        # Count people from original — word-boundary matched, consistent with replacement
        people = self._cfg.get("people", {})
        for name, alias in people.items():
            count = len(re.findall(rf"\b{re.escape(name)}\b", original))
            if count > 0:
                stats[alias] = count

        return stats

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _apply_pattern(self, text: str, pattern: str, replacement: str) -> str:
        """
        Apply a single regex pattern replacement.
        Skips silently if pattern is empty. Prints a warning to stderr
        and returns text unchanged if the pattern is invalid regex.
        """
        if not pattern:
            return text
        try:
            return re.sub(pattern, replacement, text)
        except re.error as e:
            print(f"⚠️  Invalid pattern skipped: {pattern!r} — {e}", file=sys.stderr)
            return text


# ── CLI helpers ───────────────────────────────────────────────────────────────


def print_stats(stats: dict[str, int]) -> None:
    """Print redaction summary to stderr — doesn't pollute piped stdout output."""
    if stats:
        print("\n🔒 Anonymization summary:", file=sys.stderr)
        for tag, count in sorted(stats.items()):
            print(f"   {tag}: {count} replacement(s)", file=sys.stderr)
    else:
        print(
            "\n✅ No sensitive patterns found — content looks clean.", file=sys.stderr
        )


def print_diff(original: str, anonymized: str) -> None:
    """
    Print changed lines to stderr — shows exactly what was redacted.
    Unchanged lines are omitted. Useful for spot-checking without reading
    the entire document.
    """
    orig_lines = original.splitlines()
    anon_lines = anonymized.splitlines()

    changed = 0
    print("\n📋 Changed lines:", file=sys.stderr)
    for i, (orig, anon) in enumerate(zip(orig_lines, anon_lines, strict=False), 1):
        if orig != anon:
            print(f"  line {i:>4}  - {orig}", file=sys.stderr)
            print(f"           + {anon}", file=sys.stderr)
            changed += 1

    if changed == 0:
        print("  (no changes)", file=sys.stderr)
    else:
        print(f"\n  {changed} line(s) changed", file=sys.stderr)


# ── Entry point ───────────────────────────────────────────────────────────────


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
