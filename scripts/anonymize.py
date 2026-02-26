"""
anonymize.py — Redact sensitive data from markdown files for safe debugging.

Reads a file, applies all anonymization rules from config.yaml,
and prints the sanitized content to stdout. Nothing is written to disk.

Usage:
  python scripts/anonymize.py path/to/file.md
  python scripts/anonymize.py daily/sensitive/2026-02-24.md | pbcopy
  python scripts/anonymize.py daily/sensitive/2026-02-24.md > /tmp/safe.md

Redaction order (matters — more specific patterns run first):
  1. AWS ARNs              → [AWS_ARN]         (before account ID to avoid partial match)
  2. AWS key IDs           → [AWS_KEY_ID]
  3. AWS account IDs       → [AWS_ACCOUNT_ID]
  4. Generic secrets       → [REDACTED]
  5. Internal URLs         → [INTERNAL_URL]
  6. Internal IPs          → [INTERNAL_IP]
  7. Internal hostnames    → [INTERNAL_HOST]
  8. Local paths           → [LOCAL_PATH]
  9. People names          → Person_A, Person_B ... (from config map)
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
    Stateless — safe to call multiple times.
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

        # 1. AWS ARNs — must run before account ID pattern (ARNs contain 12-digit IDs)
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
        people = self._cfg.get("people", {})
        sorted_names = sorted(people.keys(), key=len, reverse=True)
        for name in sorted_names:
            alias = people[name]
            # Case-sensitive exact replacement — names are proper nouns
            text = text.replace(name, alias)

        return text

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _apply_pattern(self, text: str, pattern: str, replacement: str) -> str:
        """
        Apply a single regex pattern replacement.
        Skips silently if pattern is empty or invalid.
        """
        if not pattern:
            return text
        try:
            return re.sub(pattern, replacement, text)
        except re.error as e:
            print(f"⚠️  Invalid pattern skipped: {pattern!r} — {e}", file=sys.stderr)
            return text


# ── Summary stats ─────────────────────────────────────────────────────────────


def _redaction_stats(original: str, anonymized: str) -> None:
    """Print a brief summary of what was redacted to stderr."""
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
    found = {tag: anonymized.count(tag) for tag in tags if anonymized.count(tag) > 0}

    # Count people replacements
    cfg = BrainConfig()._cfg.get("anonymizer", {}).get("people", {})
    for name, alias in cfg.items():
        count = original.count(name)
        if count > 0:
            found[alias] = count

    if found:
        print("\n🔒 Anonymization summary:", file=sys.stderr)
        for tag, count in found.items():
            print(f"   {tag}: {count} replacement(s)", file=sys.stderr)
    else:
        print(
            "\n✅ No sensitive patterns found — content looks clean.", file=sys.stderr
        )


# ── Entry point ───────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    if not args:
        print(
            "Usage: python scripts/anonymize.py path/to/file.md",
            file=sys.stderr,
        )
        return 1

    file_path = Path(args[0])

    if not file_path.exists():
        print(f"❌ File not found: {file_path}", file=sys.stderr)
        return 1

    if file_path.suffix not in (".md", ".txt", ".log", ".py"):
        print(
            f"⚠️  Unexpected file type: {file_path.suffix} — proceeding anyway",
            file=sys.stderr,
        )

    original = file_path.read_text(encoding="utf-8")

    anonymizer = Anonymizer()
    anonymized = anonymizer.anonymize(original)

    # Stats to stderr so they don't pollute the piped output
    _redaction_stats(original, anonymized)

    # Anonymized content to stdout
    print(anonymized)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
