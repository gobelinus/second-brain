"""
scan_public_summaries.py — Pre-commit hook for public daily summaries.

Scans staged public markdown files for sensitive content that should
only appear in daily/sensitive/ files.

All patterns, allowlists, and severity levels are driven by config.yaml.
Do NOT add patterns here — edit config.yaml scanner section instead.

Evaluation order (enforced per line):
  1. allowlist       -> if match, SKIP (safe, stop checking this match)
  2. private_domains -> if match, BLOCK commit (always critical)
  3. blocklist       -> severity-aware:
       critical / high  -> BLOCK commit
       warning          -> LOG only, allow commit

Exit codes:
  0 — clean or warnings only, commit proceeds
  1 — critical or high severity findings, commit blocked
"""

import re
import sys
from pathlib import Path

# Allow running as a standalone script from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from brain.config import BrainConfig

# ── Findings dataclass ────────────────────────────────────────────────────────


class Finding:
    def __init__(
        self,
        line_num: int,
        matched_text: str,
        description: str,
        severity: str,
        note: str = "",
    ):
        self.line_num = line_num
        self.matched_text = matched_text
        self.description = description
        self.severity = severity  # critical | high | warning
        self.note = note

    @property
    def blocks_commit(self) -> bool:
        return self.severity in ("critical", "high")


# ── Core scanner ──────────────────────────────────────────────────────────────


def is_allowlisted(text: str, allowlist: list[str]) -> bool:
    """
    Return True if the matched text contains any allowlisted pattern.
    Allowlist checked FIRST — if safe, skip all further checks for this match.
    """
    return any(
        re.search(re.escape(pattern), text, re.IGNORECASE) for pattern in allowlist
    )


def scan_line(
    line: str,
    line_num: int,
    allowlist: list[str],
    private_domains: list[dict],
    blocklist: list[dict],
) -> list[Finding]:
    """
    Scan a single line using the three-tier evaluation order.
    Returns list of Finding objects for this line.
    """
    findings = []

    # Skip markdown comment lines and bare code fence markers
    stripped = line.strip()
    if stripped.startswith("<!--") or stripped == "```":
        return findings

    # Collect all candidate matches from private_domains + blocklist
    # We evaluate each match independently through the three tiers

    # Tier 2: private_domains — always critical
    for domain_entry in private_domains:
        pattern = domain_entry["pattern"]
        description = domain_entry["description"]
        for match in re.finditer(pattern, line, re.IGNORECASE):
            matched_text = match.group(0)
            # Tier 1: allowlist check first
            if is_allowlisted(matched_text, allowlist):
                continue
            findings.append(
                Finding(
                    line_num=line_num,
                    matched_text=matched_text,
                    description=description,
                    severity="critical",
                )
            )

    # Tier 3: blocklist — severity-aware
    for block_entry in blocklist:
        pattern = block_entry["pattern"]
        description = block_entry["description"]
        severity = block_entry.get("severity", "warning")
        note = block_entry.get("note", "")
        for match in re.finditer(pattern, line):
            matched_text = match.group(0)
            # Tier 1: allowlist check first
            if is_allowlisted(matched_text, allowlist):
                continue
            findings.append(
                Finding(
                    line_num=line_num,
                    matched_text=matched_text,
                    description=description,
                    severity=severity,
                    note=note,
                )
            )

    return findings


def scan_file(
    file_path: Path,
    allowlist: list[str],
    private_domains: list[dict],
    blocklist: list[dict],
) -> list[Finding]:
    """
    Scan a single file for sensitive patterns.
    Returns all Finding objects across all lines.
    """
    findings = []
    lines = file_path.read_text(encoding="utf-8").splitlines()

    for line_num, line in enumerate(lines, start=1):
        findings.extend(
            scan_line(line, line_num, allowlist, private_domains, blocklist)
        )

    return findings


# ── Output formatting ─────────────────────────────────────────────────────────

SEVERITY_LABEL = {
    "critical": "🚨 CRITICAL",
    "high": "🔴 HIGH    ",
    "warning": "⚠️  WARNING ",
}


def print_findings(file_path: Path, findings: list[Finding]) -> None:
    """Print structured findings grouped by severity — blocking first."""
    blocking = [f for f in findings if f.blocks_commit]
    warnings = [f for f in findings if not f.blocks_commit]

    print(f"\n🚨 Sensitive content detected: {file_path}")
    print("  " + "─" * 58)

    for finding in blocking + warnings:
        label = SEVERITY_LABEL.get(finding.severity, finding.severity.upper())
        print(f"  [{label}] Line {finding.line_num}: {finding.description}")
        print(f"             Match : {finding.matched_text!r}")
        if finding.note:
            print(f"             Note  : {finding.note}")

    print()
    if blocking:
        print("  🛑 BLOCKED — move content to daily/sensitive/ or update")
        print("     scanner.allowlist in config.yaml if this is a false positive.")
    else:
        print("  ⚠️  Warnings only — commit proceeds. Review before sharing.")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────
def main(files: list[str]) -> int:
    cfg = BrainConfig()

    allowlist = cfg.scanner_allowlist
    private_domains = cfg.scanner_private_domains
    blocklist = cfg.scanner_blocklist

    has_blocking = False
    all_clean = True

    for file_str in files:
        file_path = Path(file_str)
        if not file_path.exists():
            continue

        findings = scan_file(file_path, allowlist, private_domains, blocklist)

        if findings:
            all_clean = False
            print_findings(file_path, findings)
            if any(f.blocks_commit for f in findings):
                has_blocking = True

    if all_clean:
        print("✅ scan_public_summaries: clean")

    return 1 if has_blocking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
