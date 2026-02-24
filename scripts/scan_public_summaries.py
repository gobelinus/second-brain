"""
scan_public_summaries.py — Pre-commit hook for public daily summaries.

Scans staged public markdown files for sensitive content that should
only appear in daily/sensitive/ files.

Checks:
  - Internal/corporate URLs (Jira, Confluence, internal tools)
  - Email addresses
  - AWS account IDs and ARNs
  - API keys, tokens, secrets patterns
  - Internal IP addresses
  - PR/issue references to private repositories
  - Common secret key patterns

Exit codes:
  0 — clean, commit proceeds
  1 — sensitive content found, commit blocked
"""

import re
import sys
from pathlib import Path

# ── Detection Patterns ────────────────────────────────────────────────────────

PATTERNS = [
    # Internal / corporate URLs
    (
        r"https?://[a-zA-Z0-9\-]+\.atlassian\.net",
        "Internal Jira/Confluence URL",
    ),
    (
        r"https?://[a-zA-Z0-9\-]+\.internal\b",
        "Internal domain URL",
    ),
    (
        r"https?://[a-zA-Z0-9\-]*stage\.[a-zA-Z0-9\-]+\.[a-z]+",
        "Staging environment URL",
    ),
    (
        r"https?://[a-zA-Z0-9\-]*prod[0-9]*[\.\-][a-zA-Z0-9\-]+\.[a-z]+",
        "Production environment URL",
    ),
    # Email addresses
    (
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        "Email address",
    ),
    # AWS sensitive patterns
    (
        r"arn:aws:[a-zA-Z0-9\-]+:[a-zA-Z0-9\-]*:[0-9]{12}",
        "AWS ARN with account ID",
    ),
    (
        r"\b[0-9]{12}\b",
        "Possible AWS account ID (12-digit number)",
    ),
    (
        r"AKIA[0-9A-Z]{16}",
        "AWS Access Key ID",
    ),
    (
        r"(?i)(aws_secret|secret_key|aws_key)\s*[=:]\s*\S+",
        "AWS secret key assignment",
    ),
    # Generic secret patterns
    (
        r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+",
        "Password assignment",
    ),
    (
        r"(?i)(api_key|apikey|api-key)\s*[=:]\s*\S+",
        "API key assignment",
    ),
    (
        r"(?i)(token|secret|credential)\s*[=:]\s*['\"]?\w{8,}['\"]?",
        "Token or secret assignment",
    ),
    (
        r"(?i)bearer\s+[a-zA-Z0-9\-_\.]{20,}",
        "Bearer token",
    ),
    # Private GitHub repos (org-specific)
    (
        r"https?://github\.com/[A-Z][a-zA-Z0-9]+/[a-zA-Z0-9\-]+/pull/[0-9]+",
        "Private GitHub PR link (check if repo is public)",
    ),
    # Internal IP addresses
    (
        r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        "Internal IP address (10.x.x.x)",
    ),
    (
        r"\b172\.(1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}\b",
        "Internal IP address (172.16-31.x.x)",
    ),
    (
        r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
        "Internal IP address (192.168.x.x)",
    ),
    # CloudWatch / internal AWS console URLs
    (
        r"https?://[a-z0-9\-]+\.console\.aws\.amazon\.com",
        "AWS console URL (may contain account context)",
    ),
]

# ── Allowlist — known safe patterns to ignore ─────────────────────────────────
# Add regex patterns here for things that look sensitive but are safe to commit

ALLOWLIST = [
    r"example@example\.com",
    r"user@company\.com",
    r"ollama",  # local tool references
    r"localhost",  # local URLs are fine
    r"127\.0\.0\.1",  # loopback
]


# ── Scanner ───────────────────────────────────────────────────────────────────


def is_allowlisted(match: str) -> bool:
    """Return True if the match is in the known-safe allowlist."""
    return any(re.search(pattern, match, re.IGNORECASE) for pattern in ALLOWLIST)


def scan_file(file_path: Path) -> list[tuple[int, str, str]]:
    """
    Scan a single file for sensitive patterns.

    Returns list of (line_number, matched_text, description) tuples.
    """
    findings = []
    lines = file_path.read_text(encoding="utf-8").splitlines()

    for line_num, line in enumerate(lines, start=1):
        # Skip markdown comments and code block markers
        stripped = line.strip()
        if stripped.startswith("<!--") or stripped == "```":
            continue

        for pattern, description in PATTERNS:
            for match in re.finditer(pattern, line):
                matched_text = match.group(0)
                if not is_allowlisted(matched_text):
                    findings.append((line_num, matched_text, description))

    return findings


def main(files: list[str]) -> int:
    """
    Main entry point — scan all provided files.
    Returns 0 if clean, 1 if any findings.
    """
    all_clean = True

    for file_str in files:
        file_path = Path(file_str)

        if not file_path.exists():
            continue

        findings = scan_file(file_path)

        if findings:
            all_clean = False
            print(f"\n🚨 Sensitive content detected in: {file_path}")
            print("─" * 60)
            for line_num, matched_text, description in findings:
                print(f"  Line {line_num:>4}: [{description}]")
                print(f"            {matched_text}")
            print()
            print("  ⚠️  Move this content to daily/sensitive/ or")
            print("      add to ALLOWLIST in scripts/scan_public_summaries.py")
            print("      if this match is a false positive.")
            print()

    if all_clean:
        print("✅ Public summaries scan: clean")

    return 0 if all_clean else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
