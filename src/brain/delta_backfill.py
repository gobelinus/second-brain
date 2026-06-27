"""delta_backfill.py — deterministically derive session deltas from journals.

One delta JSON per session block, written to `vault/deltas/DATE--slug.json`,
using the deterministic section→field mapping from the design (no LLM). This
is the *backfill* path for historical journals; the live `session-checkpoint`
skill emits the same schema during a session.

Schema (matches the hand-emitted vault/deltas/2026-06-27--*.json):
    date, session_anchor, topic_label, provenance, sensitivity,
    entities{projects, people, technologies, jira, prs},
    claims[{type, text, entities}], open_problems[], mood

Invariants:
  * Idempotent — never overwrites an existing delta file (the live skill's
    deltas and prior backfills are authoritative).
  * Faithful — claims are lifted verbatim from the journal; nothing invented.
  * Conservative sensitivity — defaults to `sensitive`; only blocks with no
    sensitive markers at all are classified `public`.
"""

from __future__ import annotations

import json
import re

from brain.config import BrainConfig
from brain.journal_parser import (
    SessionBlock,
    extract_bullets,
    iter_journals,
    parse_checkpoint_file,
)

JIRA_RE = re.compile(r"\b[A-Z]{2,}-\d{3,}\b")
PR_RE = re.compile(r"\bPR[ #-]?(\d{3,})\b", re.IGNORECASE)
PROJECT_TAG_RE = re.compile(r"(?<!\w)\+([A-Za-z][\w-]{2,})")

# Words that mark a block as sensitive regardless of named entities.
SENSITIVE_KEYWORDS = {
    "customer",
    "client",
    "credential",
    "credentials",
    "password",
    "secret",
    "secrets",
    "token",
    "apikey",
    "api key",
    "security",
    "vulnerability",
    "cve",
    "internal",
    "incident",
    "outage",
    "pii",
    "production",
    "prod",
}

# Anonymizer-map keys that are organizations, not people — excluded from
# people[] but still counted as sensitivity markers.
NON_PERSON_KEYS = {
    "eightfold",
    "eightfold.ai",
    "eightfoldai",
    "lumen technologies",
    "vscode",
    "tarun.arora",
}


class DeltaBackfiller:
    def __init__(self, cfg: BrainConfig | None = None):
        self.cfg = cfg or BrainConfig()
        people_map = self.cfg._cfg.get("anonymizer", {}).get("people", {}) or {}
        # Gazetteers, lowercased for matching.
        self._gazetteer = {k.lower(): k for k in people_map}  # all named entities
        self._people = {
            k.lower(): k for k in people_map if k.lower() not in NON_PERSON_KEYS
        }

    # ── public API ──────────────────────────────────────────────────────────

    def backfill(self, only_date: str | None = None, overwrite: bool = False) -> dict:
        """Emit deltas for all journals (or a single date). Returns stats."""
        deltas_dir = self.cfg.deltas_dir
        deltas_dir.mkdir(parents=True, exist_ok=True)

        stats = {"files": 0, "blocks": 0, "written": 0, "skipped": 0, "paths": []}
        for journal in iter_journals(self.cfg.journal_dir):
            if only_date and not journal.name.startswith(only_date):
                continue
            stats["files"] += 1
            for block in parse_checkpoint_file(journal):
                stats["blocks"] += 1
                delta = self._block_to_delta(block, journal)
                out = deltas_dir / f"{block.date}--{block.slug}.json"
                if out.exists() and not overwrite:
                    stats["skipped"] += 1
                    continue
                out.write_text(json.dumps(delta, indent=2, ensure_ascii=False) + "\n")
                stats["written"] += 1
                stats["paths"].append(out)
        return stats

    # ── per-block mapping ─────────────────────────────────────────────────────

    def _block_to_delta(self, block: SessionBlock, journal_path) -> dict:
        full_text = "\n".join(block.sections.values())

        technologies = self._list_items(
            block.section(
                "topics / technologies referenced", "topics/technologies referenced"
            )
        )
        jira = sorted(set(JIRA_RE.findall(full_text)))
        prs = sorted({f"PR {n}" for n in PR_RE.findall(full_text)})
        projects = sorted(set(PROJECT_TAG_RE.findall(full_text)))
        people = self._match_gazetteer(full_text, self._people)

        entities = {
            "projects": projects,
            "people": people,
            "technologies": technologies,
            "jira": jira,
            "prs": prs,
        }

        claims: list[dict] = []
        claims += self._claims(
            block.section("key decisions made"), "decision", entities
        )
        claims += self._claims(
            block.section("things i learned / insights", "things i learned"),
            "learning",
            entities,
        )
        claims += self._claims(
            block.section("code / solutions produced", "code/solutions produced"),
            "fact",
            entities,
        )

        open_problems = extract_bullets(block.section("problems still open"))
        mood = self._mood(block.section("mood / energy", "mood/energy"))

        label = block.label
        topic_label = (
            label if not block.is_implicit else f"First session of {block.date}"
        )

        return {
            "date": block.date,
            "session_anchor": block.slug,
            "topic_label": topic_label,
            "provenance": f"file:{journal_path}#{block.slug}",
            "sensitivity": self._sensitivity(full_text, entities),
            "entities": entities,
            "claims": claims,
            "open_problems": open_problems,
            "mood": mood,
        }

    # ── helpers ────────────────────────────────────────────────────────────────

    def _claims(self, section_text: str, claim_type: str, entities: dict) -> list[dict]:
        out = []
        for bullet in extract_bullets(section_text):
            out.append(
                {
                    "type": claim_type,
                    "text": bullet,
                    "entities": self._claim_entities(bullet, entities),
                }
            )
        return out

    @staticmethod
    def _claim_entities(text: str, entities: dict) -> list[str]:
        low = text.lower()
        hits = []
        for group in entities.values():
            for ent in group:
                if ent.lower() in low and ent not in hits:
                    hits.append(ent)
        return hits

    @staticmethod
    def _list_items(section_text: str) -> list[str]:
        """Tech/topic lists are sometimes bullets, sometimes comma-joined."""
        if not section_text:
            return []
        bullets = extract_bullets(section_text)
        if bullets:
            items = bullets
        else:
            items = re.split(r"[,\n;]", section_text)
        seen, out = set(), []
        for it in items:
            t = re.sub(r"\s+", " ", it).strip().strip("`*_ ")
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out

    def _match_gazetteer(self, text: str, gazetteer: dict[str, str]) -> list[str]:
        low = text.lower()
        return sorted({orig for k, orig in gazetteer.items() if k in low})

    @staticmethod
    def _mood(section_text: str) -> str:
        if not section_text:
            return ""
        first = section_text.strip().splitlines()[0]
        first = re.sub(r"^[\*\-\s]+", "", first)
        m = re.match(r"([A-Za-z]+)", first)
        return m.group(1).lower() if m else ""

    def _sensitivity(self, text: str, entities: dict) -> str:
        low = text.lower()
        if entities["jira"] or entities["prs"]:
            return "sensitive"
        if any(k in low for k in self._gazetteer):
            return "sensitive"
        if any(kw in low for kw in SENSITIVE_KEYWORDS):
            return "sensitive"
        return "public"
