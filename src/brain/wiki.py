"""wiki.py — synthesize the Karpathy-style wiki from session deltas.

"What's true now" layer: fold the per-block deltas into compounding,
interlinked entity pages. Pages are generated for **projects, jira tickets,
and people** (technologies are surfaced as inline tags, not their own pages —
they're too numerous/noisy). Every claim keeps a provenance backlink to its
source journal block.

Two entry points, mirroring the design:
  * rebuild — regenerate pages from the full delta corpus (the backfill path,
    and the weekly drift-killer). Idempotent.
  * apply(date) — regenerate only the pages touched by one date's deltas
    (the daily incremental fold), implemented as a scoped rebuild so it can
    never drift from the immutable source.

Routing: a page lands in vault/wiki/<sensitivity>/<type>/<slug>.md. An entity's
page is `sensitive` if ANY contributing delta is sensitive (conservative — we
never down-classify), else `public`.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime

import ollama

from brain.config import BrainConfig

# Entity groups we make pages for → on-disk subdir + frontmatter type.
PAGE_TYPES = OrderedDict(
    [("projects", "project"), ("jira", "jira"), ("people", "person")]
)

# The user themselves — never gets a "person" page.
SELF_NAMES = {"tarun", "tarun arora", "tarun.arora"}

# Cap how many claims feed the LLM synthesis prompt (evidence keeps them all).
SYNTH_CLAIM_CAP = 50


def slugify(name: str) -> str:
    s = re.sub(r"\s+", "-", name.lower().strip())
    return re.sub(r"[^\w\-]", "", s) or "untitled"


def clean_tech_tags(techs) -> list[str]:
    """Salvage readable tags from the noisy 'Topics/Technologies' field.

    The journal section is free prose, so the delta's technologies list carries
    sentence fragments (split mid-clause on commas/backticks). Display-time
    filter: drop fragments rather than re-deriving the committed deltas.
    """
    out, seen = [], set()
    for t in techs:
        t = re.sub(r"\s+", " ", t).strip().strip("`*_-.,;:() ").strip()
        if not t or len(t) < 2 or len(t) > 60:
            continue
        if t.count("`") % 2 != 0:  # unbalanced backtick → fragment
            continue
        if not re.search(r"[A-Za-z]", t):  # no letters → junk like "=1"
            continue
        if not re.match(r"^[A-Za-z0-9`/]", t):  # must start sane
            continue
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return sorted(out)


class WikiBuilder:
    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()
        self.wiki_dir = self.config.wiki_dir

    # ── public API ──────────────────────────────────────────────────────────────

    def rebuild(
        self,
        only_types: list[str] | None = None,
        only_entity: str | None = None,
        limit: int | None = None,
        synth: bool = True,
        overwrite: bool = True,
    ) -> dict:
        """Regenerate pages from the full corpus. Returns stats."""
        index = self._collect()
        types = only_types or list(PAGE_TYPES)
        stats = {"written": 0, "skipped": 0, "pages": []}

        count = 0
        for etype in types:
            for name, data in sorted(index[etype].items()):
                if only_entity and name != only_entity:
                    continue
                if limit is not None and count >= limit:
                    break
                path = self._page_path(etype, name, data["sensitivity"])
                if path.exists() and not overwrite:
                    stats["skipped"] += 1
                    continue
                content = self._render(etype, name, data, synth)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                stats["written"] += 1
                stats["pages"].append(path)
                count += 1
        return stats

    def apply(self, date: str, synth: bool = True) -> dict:
        """Regenerate only the entities touched by one date's deltas."""
        touched = self._entities_for_date(date)
        index = self._collect()
        stats = {"written": 0, "skipped": 0, "pages": []}
        for etype, names in touched.items():
            for name in names:
                data = index[etype].get(name)
                if not data:
                    continue
                path = self._page_path(etype, name, data["sensitivity"])
                content = self._render(etype, name, data, synth)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                stats["written"] += 1
                stats["pages"].append(path)
        return stats

    # ── corpus collection ────────────────────────────────────────────────────────

    def _load_deltas(self) -> list[dict]:
        out = []
        for f in sorted(self.config.deltas_dir.glob("*.json")):
            try:
                out.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                continue
        return out

    def _collect(self) -> dict:
        """Build per-entity aggregates keyed by type → name → data."""
        index: dict[str, dict] = {t: {} for t in PAGE_TYPES}
        for d in self._load_deltas():
            ents = d.get("entities", {})
            date = d.get("date", "")
            anchor = d.get("session_anchor", "")
            sensitivity = d.get("sensitivity", "sensitive")
            techs = ents.get("technologies", [])
            for etype in PAGE_TYPES:
                for name in ents.get(etype, []):
                    if etype == "people" and name.lower() in SELF_NAMES:
                        continue
                    bucket = index[etype].setdefault(name, self._empty_bucket())
                    bucket["dates"].add(date)
                    bucket["sensitivities"].add(sensitivity)
                    bucket["technologies"].update(techs)
                    bucket["people"].update(ents.get("people", []))
                    bucket["jira"].update(ents.get("jira", []))
                    for c in d.get("claims", []):
                        bucket["claims"].append(
                            {
                                "type": c["type"],
                                "text": c["text"],
                                "date": date,
                                "anchor": anchor,
                            }
                        )
                    for p in d.get("open_problems", []):
                        bucket["open_problems"].append(
                            {"text": p, "date": date, "anchor": anchor}
                        )
        # finalize sensitivity + dedup claims
        for etype in PAGE_TYPES:
            for data in index[etype].values():
                data["sensitivity"] = (
                    "sensitive" if "sensitive" in data["sensitivities"] else "public"
                )
                data["claims"] = self._dedup(data["claims"])
                data["open_problems"] = self._dedup(data["open_problems"])
        return index

    @staticmethod
    def _empty_bucket() -> dict:
        return {
            "claims": [],
            "open_problems": [],
            "dates": set(),
            "sensitivities": set(),
            "technologies": set(),
            "people": set(),
            "jira": set(),
        }

    @staticmethod
    def _dedup(items: list[dict]) -> list[dict]:
        """Dedup by normalized text (keep earliest date), sort by date."""
        seen: dict[str, dict] = {}
        for it in sorted(items, key=lambda x: x["date"]):
            key = re.sub(r"\s+", " ", it["text"].lower())[:80]
            if key not in seen:
                seen[key] = it
        return sorted(seen.values(), key=lambda x: x["date"])

    def _entities_for_date(self, date: str) -> dict:
        touched: dict[str, set] = {t: set() for t in PAGE_TYPES}
        for f in sorted(self.config.deltas_dir.glob(f"{date}--*.json")):
            d = json.loads(f.read_text())
            ents = d.get("entities", {})
            for etype in PAGE_TYPES:
                for name in ents.get(etype, []):
                    if etype == "people" and name.lower() in SELF_NAMES:
                        continue
                    touched[etype].add(name)
        return touched

    # ── rendering ────────────────────────────────────────────────────────────────

    def _page_path(self, etype: str, name: str, sensitivity: str):
        return self.wiki_dir / sensitivity / etype / f"{slugify(name)}.md"

    def _backlink(self, etype: str, sensitivity: str, date: str, anchor: str) -> str:
        # page at wiki/<sens>/<etype>/x.md  →  vault/journal/<date>.md
        # up 3 (etype → sens → wiki → vault), then journal/
        return f"../../../journal/{date}.md#{anchor}"

    def _render(self, etype: str, name: str, data: dict, synth: bool) -> str:
        ptype = PAGE_TYPES[etype]
        sensitivity = data["sensitivity"]
        dates = sorted(data["dates"])
        by_type = {"decision": [], "learning": [], "fact": []}
        for c in data["claims"]:
            by_type.get(c["type"], by_type["fact"]).append(c)

        lines = [
            "---",
            f"title: {name}",
            f"type: {ptype}",
            f"sensitivity: {sensitivity}",
            f"entity: {slugify(name)}",
            f"sources: {len(data['claims'])} claims across {len(dates)} dates "
            f"({dates[0] if dates else '?'} → {dates[-1] if dates else '?'})",
            f"last_rebuilt: {datetime.now().strftime('%Y-%m-%d')}",
            "---",
            "",
            f"# {name}",
            "",
        ]

        if synth:
            lines += [
                "## Current Understanding",
                "",
                self._synthesize(name, ptype, by_type),
                "",
            ]

        section_titles = [
            ("decision", "Decisions"),
            ("learning", "Learnings"),
            ("fact", "Facts / Artifacts"),
        ]
        for ckey, title in section_titles:
            items = by_type[ckey]
            if not items:
                continue
            lines += [f"## {title}", ""]
            for c in items:
                bl = self._backlink(etype, sensitivity, c["date"], c["anchor"])
                lines.append(f"- {c['text']}  ^[{c['date']}]({bl})")
            lines.append("")

        if data["open_problems"]:
            lines += ["## Open Problems", ""]
            for p in data["open_problems"]:
                bl = self._backlink(etype, sensitivity, p["date"], p["anchor"])
                lines.append(f"- {p['text']}  ^[{p['date']}]({bl})")
            lines.append("")

        # Related — technologies as inline tags, plus cross-entity links.
        related = []
        techs = clean_tech_tags(data["technologies"])
        if techs:
            shown = " · ".join(f"`{t}`" for t in techs[:25])
            more = f" _(+{len(techs) - 25} more)_" if len(techs) > 25 else ""
            related.append(f"**Technologies:** {shown}{more}")
        co_people = sorted(p for p in data["people"] if p.lower() not in SELF_NAMES)
        if etype != "people" and co_people:
            related.append("**People:** " + ", ".join(co_people))
        if etype != "jira" and data["jira"]:
            related.append("**Jira:** " + ", ".join(sorted(data["jira"])))
        if related:
            lines += ["## Related", ""] + related + [""]

        return "\n".join(lines).rstrip() + "\n"

    def _synthesize(self, name: str, ptype: str, by_type: dict) -> str:
        """One LLM pass to synthesize a coherent 'current state' from claims."""
        claims = (by_type["decision"] + by_type["learning"] + by_type["fact"])[
            :SYNTH_CLAIM_CAP
        ]
        if not claims:
            return "_No claims yet._"
        bullets = "\n".join(f"- ({c['type']}) {c['text']}" for c in claims)
        prompt = (
            f"You are maintaining a personal knowledge wiki. Synthesize a concise, "
            f"faithful 'current understanding' of the {ptype} \"{name}\" from the "
            f"claims below. Rules: 3-6 bullet points; merge duplicates; do NOT invent "
            f"anything not in the claims; no preamble; markdown bullets only.\n\n"
            f"CLAIMS:\n{bullets}\n"
        )
        try:
            resp = ollama.chat(
                model=self.config.ollama_llm_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0},
            )
            return resp["message"]["content"].strip()
        except Exception as e:
            return f"_Synthesis unavailable ({e})._"
