"""journal_parser.py — parse checkpoint journals into session blocks.

Vendored (and trimmed) from the `topic-context` skill's `topic_context.py`
so the Brain engine is self-contained — no cross-repo import of a script that
lives outside version control. Keep the block/section semantics IDENTICAL to
the skill: a session block is a `## <Label> — HH:MM AM/PM` heading followed by
the well-known H3 subsections; the first-of-day block may be implicit.

Pure functions, stdlib only. The delta backfill and the wiki engine both build
on top of this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Subsection headings we recognize (at ## or ###). Normalized (lowercased,
# leading emoji/decoration stripped, whitespace collapsed) before comparison.
EXTRACT_SECTIONS = {
    "what we worked on",
    "key decisions made",
    "things i learned / insights",
    "things i learned",
    "code / solutions produced",
    "code/solutions produced",
    "problems still open",
    "action items",
    "mood / energy",
    "mood/energy",
    "topics / technologies referenced",
    "topics/technologies referenced",
    "learning artifacts",
}


@dataclass
class SessionBlock:
    """One session within a daily checkpoint file."""

    date: str  # "2026-04-26"
    label: str  # "Worktree Migration" or "(implicit — first of day)"
    timestamp: str  # "08:11 AM" or ""
    raw_heading: str  # the original ## line, or "" for implicit blocks
    sections: dict[str, str] = field(default_factory=dict)  # normalized title → body

    IMPLICIT_LABEL = "(implicit — first of day)"

    @property
    def slug(self) -> str:
        s = re.sub(r"\s+", "-", self.label.lower().strip())
        return re.sub(r"[^\w\-]", "", s) or "untitled"

    @property
    def is_implicit(self) -> bool:
        return self.label == self.IMPLICIT_LABEL

    def section(self, *names: str) -> str:
        """First non-empty section among the given normalized names."""
        for n in names:
            v = self.sections.get(n, "")
            if v:
                return v
        return ""


SESSION_HEADING_RE = re.compile(
    r"^##\s+(?P<label>.+?)\s+—\s+(?P<ts>\d{1,2}:\d{2}\s*[AP]M)\s*$",
    re.IGNORECASE,
)
SECTION_HEADING_RE = re.compile(r"^(##|###)\s+(.+?)\s*$")


def parse_checkpoint_file(path: Path) -> list[SessionBlock]:
    """Split a daily checkpoint into session blocks.

    Handles both the canonical `## Topic — HH:MM AM/PM` boundary and the
    implicit first-of-day block (a known section heading appearing before any
    `## Topic —` line). Returns blocks in file order (oldest → newest).
    """
    text = path.read_text()
    lines = text.splitlines()

    m = re.match(r"(\d{4}-\d{2}-\d{2})\.md$", path.name)
    date = m.group(1) if m else "unknown"

    blocks: list[SessionBlock] = []
    current: SessionBlock | None = None
    current_section: str | None = None
    section_buf: list[str] = []

    def flush_section():
        nonlocal current_section, section_buf
        if current and current_section is not None:
            current.sections[current_section] = "\n".join(section_buf).strip()
        current_section = None
        section_buf = []

    def flush_block():
        nonlocal current
        flush_section()
        if current and (current.label or current.sections):
            blocks.append(current)
        current = None

    for line in lines:
        m_session = SESSION_HEADING_RE.match(line)
        if m_session:
            flush_block()
            current = SessionBlock(
                date=date,
                label=m_session.group("label").strip(),
                timestamp=m_session.group("ts").strip(),
                raw_heading=line,
            )
            continue

        m_section = SECTION_HEADING_RE.match(line)
        if m_section:
            heading_text = m_section.group(2).strip().lower()
            normalized = re.sub(r"^[^\w]+", "", heading_text).strip()
            normalized = re.sub(r"\s+", " ", normalized)
            if any(normalized.startswith(s) for s in EXTRACT_SECTIONS):
                if current is None:
                    current = SessionBlock(
                        date=date,
                        label=SessionBlock.IMPLICIT_LABEL,
                        timestamp="",
                        raw_heading="",
                    )
                flush_section()
                current_section = normalized
                continue
            # Decorative / unknown sub-heading — keep it within the open section.
            if current_section is not None:
                section_buf.append(line)
            continue

        if current_section is not None:
            section_buf.append(line)

    flush_block()
    return blocks


def extract_bullets(section_text: str) -> list[str]:
    """Pull markdown bullets out of a section (-, *, or numbered).

    Continuation lines fold into the preceding bullet. Returns bullet text with
    the marker stripped.
    """
    if not section_text:
        return []
    bullets: list[str] = []
    current: list[str] = []
    for line in section_text.splitlines():
        stripped = line.lstrip()
        if re.match(r"^[\-\*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            if current:
                bullets.append(" ".join(current).strip())
            current = [re.sub(r"^([\-\*]|\d+\.)\s+", "", stripped)]
        elif current and stripped:
            current.append(stripped)
        elif not stripped and current:
            bullets.append(" ".join(current).strip())
            current = []
    if current:
        bullets.append(" ".join(current).strip())
    return [b for b in bullets if b]


def iter_journals(journal_dir: Path):
    """Yield journal files sorted by date (filename)."""
    return sorted(journal_dir.glob("*.md"))


def parse_corpus(journal_dir: Path) -> list[SessionBlock]:
    """Parse every journal under journal_dir into a flat list of blocks."""
    blocks: list[SessionBlock] = []
    for f in iter_journals(journal_dir):
        blocks.extend(parse_checkpoint_file(f))
    return blocks
