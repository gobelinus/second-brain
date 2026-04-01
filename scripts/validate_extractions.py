"""
validate_extractions.py — Validate all .extracted.json cache files.

Finds every *.extracted.json file under the inputs/ directory (including
archive/) and validates each one against the DailySummary Pydantic schema.

Reports:
  - Which files are valid
  - Which files have schema errors (field name, error type, bad value)
  - A delete command for each invalid file so you can force re-extraction

Usage:
  python scripts/validate_extractions.py
  python scripts/validate_extractions.py --fix        # delete invalid files
  python scripts/validate_extractions.py --dir inputs/2026-03-31
  python scripts/validate_extractions.py --verbose    # show full content of bad files

Exit code:
  0 — all valid
  1 — one or more invalid files found
"""

import json
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ── Schema ────────────────────────────────────────────────────────────────────
# Inline here so the validator works standalone without importing composer.py
# (which pulls in ollama and other heavy deps). Kept in sync manually.
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from brain.config import BrainConfig

SentimentType = Literal[
    "productive", "frustrated", "neutral", "overwhelmed", "energized"
]


class DailySummarySchema(BaseModel):
    """Mirror of composer.DailySummary — kept in sync manually."""

    focus_areas: list[str]
    projects: list[str]
    key_takeaways: list[str]
    meetings: list[str]
    blockers: list[str]
    people: list[str]
    mood: SentimentType
    mood_reason: str
    references: list[str]

    @field_validator("mood", mode="before")
    @classmethod
    def normalise_mood(cls, v: str) -> str:
        valid = {"productive", "frustrated", "neutral", "overwhelmed", "energized"}
        cleaned = str(v).strip().lower()
        if cleaned in valid:
            return cleaned
        for sentiment in valid:
            if sentiment in cleaned:
                return sentiment
        return "neutral"


# ── Validation logic ──────────────────────────────────────────────────────────


def _describe_value(value) -> str:
    """Compact representation of a bad value for display."""
    s = repr(value)
    return s[:80] + "…" if len(s) > 80 else s


def _friendly_errors(e: ValidationError) -> list[str]:
    """
    Convert Pydantic ValidationError into clean human-readable lines.

    For each error, shows:
      field.subfield  type_of_error  bad_value_preview
    """
    lines = []
    for err in e.errors():
        loc = " → ".join(str(p) for p in err["loc"])
        etype = err["type"]
        msg = err["msg"]
        val = _describe_value(err.get("input", "?"))

        # Friendly type label
        if etype == "string_type":
            label = f"expected string, got {type(err.get('input', '')).__name__}"
        elif etype == "missing":
            label = "field is missing"
        elif etype == "value_error":
            label = msg
        else:
            label = f"{etype}: {msg}"

        lines.append(f"  {loc:<30}  {label}")
        if etype == "string_type":
            lines.append(f"  {'':30}  value: {val}")

    return lines


def validate_file(path: Path) -> tuple[bool, list[str]]:
    """
    Validate a single .extracted.json file.

    Returns:
        (is_valid, error_lines)
        error_lines is empty if valid.
    """
    # ── Parse JSON ────────────────────────────────────────────────────────────
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, [f"  Invalid JSON: {e}"]

    if not isinstance(raw, dict):
        return False, [f"  Expected JSON object, got {type(raw).__name__}"]

    # ── Validate schema ───────────────────────────────────────────────────────
    try:
        DailySummarySchema(**raw)
        return True, []
    except ValidationError as e:
        return False, _friendly_errors(e)


def find_extracted_files(root: Path) -> list[Path]:
    """Find all *.extracted.json files under root, sorted by path."""
    return sorted(root.rglob("*.extracted.json"))


def _coerce_preview(path: Path) -> list[str]:
    """
    Preview what auto-coercion would do to a file's list fields.
    Shows fields where dicts would be flattened to strings.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    LIST_FIELDS = [
        "focus_areas",
        "projects",
        "key_takeaways",
        "meetings",
        "blockers",
        "people",
        "references",
    ]
    lines = []
    for field in LIST_FIELDS:
        items = raw.get(field, [])
        for i, item in enumerate(items):
            if isinstance(item, dict):
                # Show what coercion would produce
                coerced = _coerce_dict_to_str(item)
                lines.append(f'  {field}[{i}]  dict → "{coerced}"')
    return lines


def _coerce_dict_to_str(d: dict) -> str:
    """
    Flatten a dict item in a list field to a readable string.

    Common patterns from llama3:
      {"Name": "Rahul", "context": "green build"}  → "Rahul — green build"
      {"name": "Alice", "role": "PM"}               → "Alice — PM"
      {"title": "Sprint Planning", "time": "10am"}  → "Sprint Planning — 10am"
    """
    # Try common name-like keys first
    name_keys = ["name", "Name", "person", "Person", "title", "Title", "meeting"]
    value_keys = [
        "context",
        "Context",
        "role",
        "Role",
        "note",
        "Note",
        "description",
        "time",
        "Time",
        "detail",
    ]

    name = next((str(d[k]) for k in name_keys if k in d), None)
    value = next((str(d[k]) for k in value_keys if k in d), None)

    if name and value:
        return f"{name} — {value}"
    elif name:
        return name
    elif value:
        return value
    else:
        # Fallback: join all values
        return " — ".join(str(v) for v in d.values() if v)


def coerce_and_fix(path: Path) -> bool:
    """
    Auto-coerce dict items in list fields to strings and overwrite the file.
    Returns True if the file was modified, False if already valid.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    LIST_FIELDS = [
        "focus_areas",
        "projects",
        "key_takeaways",
        "meetings",
        "blockers",
        "people",
        "references",
    ]

    modified = False
    for field in LIST_FIELDS:
        items = raw.get(field, [])
        fixed = []
        for item in items:
            if isinstance(item, dict):
                fixed.append(_coerce_dict_to_str(item))
                modified = True
            else:
                fixed.append(item)
        raw[field] = fixed

    if modified:
        path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    return modified


# ── Main ──────────────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    fix = "--fix" in args
    verbose = "--verbose" in args
    coerce = "--coerce" in args  # fix dicts-in-lists instead of deleting

    # --dir overrides the default inputs path
    dir_arg = next(
        (args[i + 1] for i, a in enumerate(args) if a == "--dir" and i + 1 < len(args)),
        None,
    )

    cfg = BrainConfig()
    root = Path(dir_arg) if dir_arg else cfg.inputs

    if not root.exists():
        print(f"❌ Directory not found: {root}", file=sys.stderr)
        return 1

    files = find_extracted_files(root)

    if not files:
        print(f"✅ No .extracted.json files found under {root}")
        return 0

    print("\n🧠 Brain — Extraction Cache Validator")
    print(f"   Directory : {root}")
    print(f"   Files     : {len(files)} .extracted.json file(s)")
    print()

    valid_count = 0
    invalid_count = 0
    invalid_files: list[Path] = []

    for path in files:
        is_valid, errors = validate_file(path)
        rel = path.relative_to(root) if path.is_relative_to(root) else path

        if is_valid:
            valid_count += 1
            print(f"  ✅  {rel}")
        else:
            invalid_count += 1
            invalid_files.append(path)
            print(f"  ❌  {rel}")
            for line in errors:
                print(line)

            if coerce:
                # Show what coercion would do
                preview = _coerce_preview(path)
                if preview:
                    print("       Coercion preview:")
                    for line in preview:
                        print(f"       {line}")

            if verbose:
                # Print full file content
                try:
                    content = json.loads(path.read_text(encoding="utf-8"))
                    print("       Full content:")
                    for line in json.dumps(content, indent=2).splitlines():
                        print(f"       {line}")
                except Exception:
                    pass

            print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("─" * 60)
    print(f"  Valid   : {valid_count}")
    print(f"  Invalid : {invalid_count}")

    if not invalid_files:
        print()
        print("✅ All cache files are valid.")
        return 0

    # ── Fix actions ───────────────────────────────────────────────────────────
    print()

    if coerce:
        # Try to fix dicts-in-lists in place
        fixed = 0
        still_broken = []
        for path in invalid_files:
            was_modified = coerce_and_fix(path)
            is_valid_now, _ = validate_file(path)
            if is_valid_now:
                rel = path.relative_to(root) if path.is_relative_to(root) else path
                action = "coerced + valid" if was_modified else "already valid"
                print(f"  ✅  Fixed: {rel} ({action})")
                fixed += 1
            else:
                still_broken.append(path)

        if still_broken:
            print()
            print(f"  ⚠️  {len(still_broken)} file(s) still invalid after coercion.")
            print("     Delete these to force fresh extraction:\n")
            for path in still_broken:
                print(f"     rm {path}")
        else:
            print(f"\n✅ All {fixed} file(s) fixed via coercion.")

        return 1 if still_broken else 0

    elif fix:
        # Delete invalid files so compose re-extracts them
        print(
            "  Deleting invalid cache files (will be re-extracted on next compose):\n"
        )
        for path in invalid_files:
            path.unlink()
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            print(f"  🗑️   Deleted: {rel}")
        print()
        print(f"  ✅ {len(invalid_files)} file(s) deleted.")
        print("     Run: brain compose --date YYYY-MM-DD")
        print("     The affected files will be re-extracted automatically.")
        return 0

    else:
        # Just report — suggest what to do
        print("  To fix:")
        print()
        print("  Option A — coerce dicts to strings (faster, no LLM call):")
        print("    python scripts/validate_extractions.py --coerce")
        print()
        print("  Option B — delete and re-extract via LLM:")
        print("    python scripts/validate_extractions.py --fix")
        print()
        print("  To delete specific files manually:")
        for path in invalid_files:
            print(f"    rm {path}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
