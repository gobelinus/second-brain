"""
query.py — RAG Query Engine for Brain Phase 3.

Answers natural language questions against your daily summaries using a
four-pattern retrieval strategy decided by QueryRouter.

The engine never decides HOW to retrieve — that's the router's job.
The engine only knows HOW to execute each retrieval pattern efficiently
and how to assemble the LLM prompt from whatever comes back.

Entry Points
────────────
  engine = RAGEngine(config)

  # Ask a question (brain query)
  result = engine.answer(
      question="What was I blocked on last month?",
      scope="all",
  )

  # Summarise a date range (brain summarize)
  result = engine.summarize(
      date_from="2026-02-01",
      date_to="2026-02-25",
      scope="all",
  )

Both return a QueryResult — structured output the CLI renders and
optionally exports to markdown.

Design Decisions
────────────────
- Embedder is called with [question] (single item) — same interface as
  ingest, no new embedding code needed.
- ChromaDB returns raw L2 distances which scale with embedding dimension
  (nomic-embed-text 768-dim produces distances in the 300–600 range).
  Filtering is done by top-K ranking only — no distance threshold applied.
- SQLite metadata is always fetched for Pattern C/D to enrich the LLM
  context with structured facts (projects, blockers, sentiment) alongside
  the raw text chunks.
- Streaming is NOT used — Mistral 7B on a laptop is fast enough for
  single-query responses and streaming adds CLI complexity for little gain.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

import ollama

from brain.chunker import Embedder
from brain.config import BrainConfig
from brain.ingest import ChromaStore, SQLiteStore
from brain.router import QueryRouter, RouteDecision

# ── Helpers ───────────────────────────────────────────────────────────────────


def _date_to_int(date_str: str) -> int:
    """
    Convert ISO date string to integer for ChromaDB numeric range filtering.

    ChromaDB's $gte/$lte operators only accept numeric values — ISO strings
    like "2026-02-01" cause a runtime error. YYYYMMDD integer format preserves
    correct sort order while satisfying the numeric type requirement.

    Examples:
        "2026-02-01" → 20260201
        "2026-12-31" → 20261231
    """
    return int(date_str.replace("-", ""))


# ── Query Result ───────────────────────────────────────────────────────────────


@dataclass
class QueryResult:
    """
    Structured output from the RAG engine.

    Fields
    ──────
    question        : original question or "(summarize)" label
    answer          : LLM-generated answer in markdown
    pattern         : routing pattern used — A | B | C | D
    scope           : search scope — public | sensitive | all
    chunks_retrieved: number of chunks sent to LLM context
    dates_covered   : sorted list of dates represented in retrieved chunks
    date_from       : lower bound of query (None = no filter)
    date_to         : upper bound of query (None = no filter)
    sqlite_rows     : structured metadata rows (Pattern A/D only)
    rationale       : router's explanation (shown in verbose mode)
    """

    question: str
    answer: str
    pattern: str
    scope: str
    chunks_retrieved: int
    dates_covered: list[str]
    date_from: str | None
    date_to: str | None
    sqlite_rows: list[dict] = field(default_factory=list)
    rationale: str = ""

    def to_markdown(self) -> str:
        """
        Render result as a clean markdown document suitable for export.
        Used by the --export flag in cli.py.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dates_str = ", ".join(self.dates_covered) if self.dates_covered else "all dates"
        date_range = ""
        if self.date_from or self.date_to:
            date_range = (
                f"\n| Date range | {self.date_from or '—'} → {self.date_to or '—'} |"
            )

        return f"""\
# 🧠 Brain Query Result

**Generated:** {now}

## Question

> {self.question}

## Answer

{self.answer}

---

## Query Metadata

| Field | Value |
|---|---|
| Pattern | {self.pattern} |
| Scope | {self.scope} |
| Chunks retrieved | {self.chunks_retrieved} |
| Dates covered | {dates_str} |{date_range}
| Rationale | {self.rationale} |
"""


# ── RAG Engine ─────────────────────────────────────────────────────────────────


class RAGEngine:
    """
    Executes retrieval-augmented generation using the routing decision
    produced by QueryRouter.

    Wires together: ChromaDB → context assembly → Ollama/Mistral → answer.

    For Pattern A (SQLite-only), no LLM call is made — the answer is
    assembled directly from structured metadata rows.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()
        self.router = QueryRouter(self.config)
        self.embedder = Embedder(self.config)
        self.sqlite = SQLiteStore(self.config.sqlite_db)
        self.chroma = ChromaStore(self.config.chroma_store, self.config)

    # ── Public API ─────────────────────────────────────────────────────────────

    def answer(self, question: str, scope: str = "all") -> QueryResult:
        """
        Answer a natural language question against the brain store.

        Flow:
          1. Route the question → RouteDecision
          2. Execute the appropriate retrieval pattern
          3. Assemble LLM context from retrieved chunks
          4. Call Ollama/Mistral with system prompt + context + question
          5. Return structured QueryResult

        Args:
            question : raw user question
            scope    : "public" | "sensitive" | "all"

        Returns:
            QueryResult with answer, source dates, and metadata
        """
        scope = scope or self.config.rag_default_scope
        decision = self.router.route(question, scope)

        print(f"\n🔀 Router: Pattern {decision.pattern}")
        print(f"   {decision.rationale}")
        if decision.date_from or decision.date_to:
            print(
                f"   Date range: {decision.date_from or '—'} → {decision.date_to or '—'}"
            )
        print()

        # ── Pattern A — SQLite only ───────────────────────────────────────────
        if decision.pattern == "A":
            return self._execute_pattern_a(question, decision)

        # ── Patterns B / C / D — involve ChromaDB ────────────────────────────
        return self._execute_semantic(question, decision)

    def summarize(
        self,
        date_from: str,
        date_to: str,
        scope: str = "all",
    ) -> QueryResult:
        """
        Summarise activity across a date range.

        Uses a forced Pattern C decision — date range is always known.
        Retrieves more chunks than a normal query (uses max_context_chunks
        directly) to get broad coverage across the period.

        Args:
            date_from : start date inclusive (YYYY-MM-DD)
            date_to   : end date inclusive (YYYY-MM-DD)
            scope     : "public" | "sensitive" | "all"

        Returns:
            QueryResult with structured markdown summary as the answer
        """
        scope = scope or self.config.rag_default_scope

        # Validate date range
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d")
            end = datetime.strptime(date_to, "%Y-%m-%d")
            if end < start:
                raise ValueError(
                    f"date_to ({date_to}) must be after date_from ({date_from})"
                )
            days = (end - start).days
            if days > self.config.rag_max_summarize_days:
                raise ValueError(
                    f"Date range spans {days} days — exceeds max_summarize_days "
                    f"({self.config.rag_max_summarize_days}). "
                    f"Narrow the range or increase max_summarize_days in config.yaml."
                )
        except ValueError as e:
            raise ValueError(str(e)) from e

        # Force Pattern C — date range always known for summarize
        decision = RouteDecision(
            pattern="C",
            scope=scope,
            date_from=date_from,
            date_to=date_to,
            use_sqlite=True,
            use_chroma=True,
            sqlite_field=None,
            rationale=f"Summarize command — forced Pattern C for {date_from} → {date_to}",
        )

        print("\n🔀 Router: Pattern C (summarize)")
        print(f"   Date range: {date_from} → {date_to}")
        print()

        question = f"Summarise all activity from {date_from} to {date_to}."
        return self._execute_semantic(
            question=question,
            decision=decision,
            system_prompt=self.config.rag_summarize_system_prompt,
            # Use more chunks for summarize — we want breadth over precision
            top_k_override=self.config.rag_max_context_chunks,
        )

    # ── Pattern A Execution ────────────────────────────────────────────────────

    def _execute_pattern_a(self, question: str, decision: RouteDecision) -> QueryResult:
        """
        Execute a SQLite-only structured query (Pattern A).

        Queries the metadata store directly and assembles a formatted
        answer without calling the LLM — fast, deterministic, offline.
        """
        print("  [1/2] Querying SQLite metadata store...")
        rows = self.sqlite.query_metadata(
            field=decision.sqlite_field,
            date_from=decision.date_from,
            date_to=decision.date_to,
            sensitivity=decision.scope,
        )
        print(f"        {len(rows)} row(s) found")

        print("  [2/2] Assembling structured answer...")
        answer = self._format_sqlite_answer(question, rows, decision)

        dates_covered = sorted({r["date"] for r in rows})
        return QueryResult(
            question=question,
            answer=answer,
            pattern="A",
            scope=decision.scope,
            chunks_retrieved=0,
            dates_covered=dates_covered,
            date_from=decision.date_from,
            date_to=decision.date_to,
            sqlite_rows=rows,
            rationale=decision.rationale,
        )

    # ── Semantic Execution (Patterns B / C / D) ────────────────────────────────

    def _execute_semantic(
        self,
        question: str,
        decision: RouteDecision,
        system_prompt: str | None = None,
        top_k_override: int | None = None,
    ) -> QueryResult:
        """
        Execute a semantic (ChromaDB + LLM) query.
        Handles Patterns B, C, and D.

        Steps:
          1. Embed the question
          2. Query ChromaDB (with date filter for C/D)
          3. Filter low-similarity chunks
          4. For Pattern D: enrich with SQLite metadata
          5. Assemble LLM context
          6. Call Ollama/Mistral
          7. Return QueryResult
        """
        top_k = top_k_override or self.config.rag_top_k
        sys_prompt = system_prompt or self.config.rag_system_prompt

        # ── Step 1: Embed the question ─────────────────────────────────────────
        print("  [1/4] Embedding question...")
        query_embedding = self.embedder.embed_chunks([question])[0]

        # ── Step 2: Retrieve chunks from ChromaDB ─────────────────────────────
        print("  [2/4] Retrieving chunks from ChromaDB...")
        raw_chunks = self._retrieve_chunks(
            query_embedding=query_embedding,
            decision=decision,
            top_k=top_k,
        )
        print(f"        {len(raw_chunks)} chunk(s) retrieved")

        if not raw_chunks:
            return QueryResult(
                question=question,
                answer=(
                    "No relevant notes found for this query.\n\n"
                    "Try broadening the date range, changing the scope, "
                    "or ingesting more daily summaries."
                ),
                pattern=decision.pattern,
                scope=decision.scope,
                chunks_retrieved=0,
                dates_covered=[],
                date_from=decision.date_from,
                date_to=decision.date_to,
                rationale=decision.rationale,
            )

        # ── Step 3: For Pattern D — enrich with SQLite metadata ───────────────
        sqlite_rows: list[dict] = []
        if decision.pattern == "D":
            print("  [3/4] Enriching with SQLite metadata (Pattern D)...")
            chunk_dates = list({c["metadata"]["date"] for c in raw_chunks})
            sqlite_rows = self.sqlite.query_metadata(
                field=None,
                date_from=min(chunk_dates),
                date_to=max(chunk_dates),
                sensitivity=decision.scope,
            )
            print(f"        {len(sqlite_rows)} metadata row(s) fetched")
        else:
            print("  [3/4] Skipping SQLite enrichment (not Pattern D)...")

        # ── Step 4: Assemble LLM context ──────────────────────────────────────
        print("  [4/4] Calling LLM...")
        context = self._assemble_context(raw_chunks, sqlite_rows)
        answer = self._call_llm(
            system_prompt=sys_prompt,
            context=context,
            question=question,
        )

        dates_covered = sorted({c["metadata"]["date"] for c in raw_chunks})
        return QueryResult(
            question=question,
            answer=answer,
            pattern=decision.pattern,
            scope=decision.scope,
            chunks_retrieved=len(raw_chunks),
            dates_covered=dates_covered,
            date_from=decision.date_from,
            date_to=decision.date_to,
            sqlite_rows=sqlite_rows,
            rationale=decision.rationale,
        )

    # ── ChromaDB Retrieval ─────────────────────────────────────────────────────

    def _retrieve_chunks(
        self,
        query_embedding: list[float],
        decision: RouteDecision,
        top_k: int,
    ) -> list[dict]:
        """
        Query ChromaDB for the most relevant chunks.

        For Pattern C/D: builds a ChromaDB `where` filter to scope results
        to the date range from the routing decision.

        Ranks chunks by L2 distance (lower = more similar) and returns
        the top max_context_chunks. No threshold filtering — raw L2 distances
        from nomic-embed-text are in the 300–600 range, not 0.0–2.0.

        Returns list of dicts:
            [{"text": str, "metadata": dict, "distance": float}, ...]
        sorted by distance ascending (most relevant first).
        """
        collections = self._collections_for_scope(decision.scope)
        max_chunks = self.config.rag_max_context_chunks
        all_chunks: list[dict] = []

        for collection_name in collections:
            try:
                collection = self.chroma.client.get_collection(name=collection_name)
            except Exception:
                # Collection doesn't exist yet — no data ingested for this scope
                continue

            # Build date filter for Pattern C and D
            where_filter = self._build_where_filter(decision)

            try:
                results = collection.query(
                    query_embeddings=[query_embedding],
                    n_results=min(top_k, collection.count() or 1),
                    where=where_filter,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as e:
                err = str(e)
                # ChromaDB raises when a where filter finds zero matching
                # documents — this is expected (empty date range) and safe.
                # All other errors are surfaced so they don't hide bugs like
                # missing metadata fields (e.g. date_int not yet written).
                if "no documents" in err.lower() or "does not exist" in err.lower():
                    continue
                print(f"        ⚠️  ChromaDB query issue on '{collection_name}': {e}")
                continue

            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for doc, meta, dist in zip(docs, metas, distances, strict=False):
                all_chunks.append(
                    {
                        "text": doc,
                        "metadata": meta,
                        "distance": dist,
                    }
                )

        # Sort by distance (most relevant first), cap at max_context_chunks
        all_chunks.sort(key=lambda c: c["distance"])

        # Deduplicate — same text appearing in both public+sensitive collections
        # (shouldn't happen, but scope="all" iterates both, so guard it)
        seen: set[tuple] = set()
        unique_chunks: list[dict] = []
        for chunk in all_chunks:
            key = (chunk["metadata"].get("date"), chunk["text"][:80])
            if key not in seen:
                seen.add(key)
                unique_chunks.append(chunk)

        return unique_chunks[:max_chunks]

    def _build_where_filter(self, decision: RouteDecision) -> dict | None:
        """
        Build a ChromaDB metadata `where` filter from the route decision.

        ⚠️  ChromaDB's $gte/$lte operators require NUMERIC values — they refuse
        string operands even for ISO date strings. We store dates as integers
        in the `date_int` metadata field (YYYYMMDD, e.g. 20260201) so range
        comparisons work correctly via numeric operators.

        Returns None if no date bounds → full collection scan.
        Returns a single condition if only one bound present.
        Returns a $and filter if both bounds present.
        """
        conditions = []

        if decision.date_from:
            conditions.append({"date_int": {"$gte": _date_to_int(decision.date_from)}})
        if decision.date_to:
            conditions.append({"date_int": {"$lte": _date_to_int(decision.date_to)}})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _collections_for_scope(self, scope: str) -> list[str]:
        """
        Resolve which ChromaDB collection(s) to search based on scope.

        scope="all"       → search both public + sensitive collections
        scope="public"    → public collection only
        scope="sensitive" → sensitive collection only
        """
        if scope == "public":
            return [self.config.chroma_collection_for("public")]
        if scope == "sensitive":
            return [self.config.chroma_collection_for("sensitive")]
        # "all" — search both
        return [
            self.config.chroma_collection_for("public"),
            self.config.chroma_collection_for("sensitive"),
        ]

    # ── Context Assembly ───────────────────────────────────────────────────────

    def _assemble_context(
        self,
        chunks: list[dict],
        sqlite_rows: list[dict] | None = None,
    ) -> str:
        """
        Format retrieved chunks (and optional SQLite rows) into a clean
        context block for the LLM prompt.

        Each chunk is labelled with its source date and sensitivity so the
        LLM can reference them in its answer ("On 2026-02-14, you noted...").

        SQLite rows (Pattern D only) are appended as a structured metadata
        section — gives the LLM hard facts to anchor pattern analysis.
        """
        parts: list[str] = []

        for i, chunk in enumerate(chunks, 1):
            meta = chunk["metadata"]
            date_str = meta.get("date", "unknown date")
            sensitivity = meta.get("sensitivity", "")
            label = f"[{i}] {date_str}"
            if sensitivity:
                label += f" ({sensitivity})"
            parts.append(f"{label}\n{chunk['text'].strip()}")

        context = "\n\n---\n\n".join(parts)

        # Append structured metadata for Pattern D enrichment
        if sqlite_rows:
            meta_lines = ["## Structured Metadata\n"]
            for row in sqlite_rows:
                projects = json.loads(row.get("projects", "[]"))
                blockers = json.loads(row.get("blockers", "[]"))
                sentiment = row.get("sentiment", "neutral")
                date_str = row.get("date", "")
                meta_lines.append(
                    f"- **{date_str}** | sentiment: {sentiment} | "
                    f"projects: {', '.join(projects) or 'none'} | "
                    f"blockers: {', '.join(blockers) or 'none'}"
                )
            context += "\n\n" + "\n".join(meta_lines)

        return context

    # ── LLM Call ───────────────────────────────────────────────────────────────

    def _call_llm(
        self,
        system_prompt: str,
        context: str,
        question: str,
    ) -> str:
        """
        Call local Ollama/Mistral with the assembled RAG prompt.

        Prompt structure:
            [System]  — role + rules from config.yaml
            [User]    — context chunks + question

        Uses temperature from config (default 0 for determinism).
        Raises ValueError if LLM returns empty response.
        """
        user_message = f"## Context\n\n{context}\n\n---\n\n## Question\n\n{question}"

        response = ollama.chat(
            model=self.config.ollama_llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            options={"temperature": self.config.ollama_temperature},
        )

        result = response["message"]["content"].strip()

        if not result:
            raise ValueError(
                "LLM returned an empty response.\n"
                f"Check that Ollama is running: ollama run {self.config.ollama_llm_model}"
            )

        return result

    # ── Pattern A Answer Formatting ────────────────────────────────────────────

    def _format_sqlite_answer(
        self,
        question: str,
        rows: list[dict],
        decision: RouteDecision,
    ) -> str:
        """
        Format SQLite query results into a clean markdown answer.
        No LLM involved — pure structured data formatting.

        For Pattern A the answer is deterministic and fast.
        The format adapts based on which field was queried.
        """
        if not rows:
            date_hint = ""
            if decision.date_from or decision.date_to:
                date_hint = (
                    f" between {decision.date_from or '—'} "
                    f"and {decision.date_to or '—'}"
                )
            return f"No data found{date_hint}. Try ingesting more daily summaries."

        field = decision.sqlite_field
        date_range_str = ""
        if decision.date_from or decision.date_to:
            date_range_str = (
                f" ({decision.date_from or 'start'} → {decision.date_to or 'today'})"
            )

        # ── Sentiment field — show distribution ───────────────────────────────
        if field == "sentiment":
            from collections import Counter

            sentiments = [r.get("sentiment", "neutral") for r in rows]
            counts = Counter(sentiments)
            total = len(sentiments)
            lines = [f"**Sentiment distribution{date_range_str}** ({total} days)\n"]
            for sentiment, count in counts.most_common():
                pct = round(count / total * 100)
                bar = "█" * (count * 20 // total) if total > 0 else ""
                lines.append(f"- **{sentiment}**: {count} day(s) ({pct}%) {bar}")
            return "\n".join(lines)

        # ── Array fields — explode and count ──────────────────────────────────
        if field in ("projects", "blockers", "focus_areas", "people"):
            from collections import Counter

            all_values: list[str] = []
            for row in rows:
                values = json.loads(row.get(field, "[]"))
                all_values.extend(values)

            if not all_values:
                return f"No {field} found{date_range_str}."

            counts = Counter(all_values)
            total_days = len({r["date"] for r in rows})
            lines = [
                f"**{field.replace('_', ' ').title()}{date_range_str}** "
                f"({total_days} day(s), {len(all_values)} total mention(s))\n"
            ]
            for value, count in counts.most_common(20):
                lines.append(f"- **{value}**: {count} mention(s)")
            return "\n".join(lines)

        # ── Generic fallback — list dates with all fields ─────────────────────
        lines = [f"**Results{date_range_str}** ({len(rows)} row(s))\n"]
        for row in rows:
            lines.append(
                f"- **{row['date']}** | "
                f"sentiment: {row.get('sentiment', '—')} | "
                f"projects: {', '.join(json.loads(row.get('projects', '[]'))) or 'none'}"
            )
        return "\n".join(lines)
