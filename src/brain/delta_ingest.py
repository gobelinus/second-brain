"""delta_ingest.py — ingest session deltas into Brain's RAG stores.

The native "Phase 5" ingest path: trusts a delta's `entities` block (so it
skips the LLM structurer entirely) and embeds the **raw block text** from the
journal for recall. This is the design-faithful counterpart to `brain ingest`,
which LLM-structures arbitrary markdown.

Day-keyed by construction — Brain's SQLite PK and ChromaDB dedup are
`(date, sensitivity)`, but a journal has many per-block deltas. So we
**aggregate all of a date's same-sensitivity blocks into one entry**: union
their entities, concatenate their block text, pick a representative sentiment.
Per-block ingest would clobber (each `delete_by_date` wipes the prior block).

Idempotent: re-ingesting a date replaces its entry (INSERT OR REPLACE +
delete_by_date), never duplicates.
"""

from __future__ import annotations

import json
from collections import Counter

from brain.chunker import Embedder, TextChunker
from brain.config import BrainConfig
from brain.ingest import ChromaStore, SQLiteStore
from brain.journal_parser import SessionBlock, parse_checkpoint_file


class DeltaIngestPipeline:
    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()
        self.chunker = TextChunker(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        self.embedder = Embedder(self.config)
        self.sqlite = SQLiteStore(self.config.sqlite_db)
        self.chroma = ChromaStore(self.config.chroma_store, self.config)
        self._valid_sentiments = set(self.config.valid_sentiments)

    # ── discovery ──────────────────────────────────────────────────────────────

    def dates(self) -> list[str]:
        """All distinct dates that have at least one delta, sorted."""
        seen = {p.name.split("--", 1)[0] for p in self.config.deltas_dir.glob("*.json")}
        return sorted(seen)

    # ── ingest ───────────────────────────────────────────────────────────────────

    def ingest_all(self, verbose: bool = True) -> dict:
        stats = {"dates": 0, "entries": 0, "chunks": 0}
        for date in self.dates():
            results = self.ingest_date(date)
            stats["dates"] += 1
            for r in results:
                stats["entries"] += 1
                stats["chunks"] += r["chunks"]
            if verbose:
                summary = ", ".join(
                    f"{r['sensitivity']}:{r['chunks']}ch" for r in results
                )
                print(f"   {date}  →  {summary}")
        return stats

    def ingest_date(self, date: str) -> list[dict]:
        """Ingest one date: one Brain entry per sensitivity present."""
        delta_files = sorted(self.config.deltas_dir.glob(f"{date}--*.json"))
        if not delta_files:
            return []
        deltas = [json.loads(f.read_text()) for f in delta_files]

        journal = self.config.journal_dir / f"{date}.md"
        blocks_by_slug: dict[str, SessionBlock] = {}
        if journal.exists():
            blocks_by_slug = {b.slug: b for b in parse_checkpoint_file(journal)}

        groups: dict[str, list[dict]] = {}
        for d in deltas:
            groups.setdefault(d.get("sensitivity", "sensitive"), []).append(d)

        results = []
        for sensitivity, group in groups.items():
            metadata, content = self._aggregate(
                date, sensitivity, group, blocks_by_slug
            )
            self.sqlite.upsert(metadata)
            self.chroma.delete_by_date(date, sensitivity)
            chunks = self.chunker.chunk(content) if content.strip() else []
            if chunks:
                embeddings = self.embedder.embed_chunks(chunks)
                self.chroma.upsert_chunks(chunks, embeddings, metadata)
            results.append({"sensitivity": sensitivity, "chunks": len(chunks)})
        return results

    # ── aggregation ────────────────────────────────────────────────────────────

    def _aggregate(
        self,
        date: str,
        sensitivity: str,
        group: list[dict],
        blocks_by_slug: dict[str, SessionBlock],
    ) -> tuple[dict, str]:
        projects: set[str] = set()
        people: set[str] = set()
        technologies: set[str] = set()
        blockers: list[str] = []
        moods: list[str] = []
        content_parts: list[str] = []

        for d in group:
            ent = d.get("entities", {})
            projects.update(ent.get("projects", []))
            people.update(ent.get("people", []))
            technologies.update(ent.get("technologies", []))
            # jira/prs are kept out of SQLite (delta/wiki only) by design, but
            # fold them into focus_areas-free text via the embedded block body.
            blockers.extend(d.get("open_problems", []))
            if d.get("mood"):
                moods.append(d["mood"])

            block = blocks_by_slug.get(d.get("session_anchor", ""))
            if block is not None:
                content_parts.append(self._block_text(block))
            else:
                # Journal block missing — fall back to the claim text so the
                # entry is never empty.
                content_parts.append("\n".join(c["text"] for c in d.get("claims", [])))

        metadata = {
            "date": date,
            "sensitivity": sensitivity,
            "projects": sorted(projects),
            "people": sorted(people),
            "focus_areas": sorted(technologies),
            "blockers": list(dict.fromkeys(blockers)),  # dedup, keep order
            "sentiment": self._pick_sentiment(moods),
            "raw_file": str(self.config.journal_dir / f"{date}.md"),
        }
        return metadata, "\n\n".join(content_parts)

    @staticmethod
    def _block_text(block: SessionBlock) -> str:
        parts = [f"## {block.label}"]
        for name, body in block.sections.items():
            if body:
                parts.append(f"### {name}\n{body}")
        return "\n\n".join(parts)

    def _pick_sentiment(self, moods: list[str]) -> str:
        for mood, _ in Counter(moods).most_common():
            if mood in self._valid_sentiments:
                return mood
        return "neutral"
