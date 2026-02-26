"""
router.py — Query Router for Brain Phase 3.

Decides HOW a user question should be answered before any LLM call is made.
Choosing the right retrieval strategy up front is what makes the system fast
and accurate — throwing every question at ChromaDB is wasteful and often wrong.

Four Routing Patterns
─────────────────────
Pattern A — SQLite only
    Structured/aggregate questions that have a deterministic answer in the
    metadata store. No embeddings needed.
    Examples: "how many days blocked in Jan?", "list all projects in Feb"

Pattern B — ChromaDB only
    Pure semantic/contextual questions with no implied date filter.
    Examples: "find notes about feeling overwhelmed", "what did I say about AWS?"

Pattern C — SQLite first → ChromaDB
    Date range is known or implied → scope the vector search to those dates.
    This is the most efficient pattern for time-bound semantic queries.
    Examples: "what blocked me in January?", "summarise my Azure work last week"

Pattern D — ChromaDB first → SQLite
    Meaning leads, facts enrich. Find semantically similar chunks first,
    then use their dates to pull structured metadata for a richer answer.
    Examples: "what patterns in my blockers?", "how has my sentiment changed?"

Usage
─────
    router = QueryRouter(config)

    decision = router.route("What was I blocked on last month?")
    # → RouteDecision(
    #       pattern="C",
    #       scope="all",
    #       date_from="2026-01-26",
    #       date_to="2026-02-25",
    #       use_sqlite=True,
    #       use_chroma=True,
    #       sqlite_field=None,
    #       rationale="Date range implied by 'last month' — SQLite scoped ChromaDB search"
    #   )
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from brain.config import BrainConfig

# ── Route Decision ─────────────────────────────────────────────────────────────


@dataclass
class RouteDecision:
    """
    The router's verdict — consumed by RAGEngine to drive retrieval.

    Fields
    ──────
    pattern        : "A" | "B" | "C" | "D"
    scope          : "public" | "sensitive" | "all"
    date_from      : ISO date string or None  (inclusive lower bound)
    date_to        : ISO date string or None  (inclusive upper bound)
    use_sqlite     : whether SQLite should be queried
    use_chroma     : whether ChromaDB should be queried
    sqlite_field   : for Pattern A — which metadata field to aggregate
                     e.g. "projects", "blockers", "sentiment", "people"
    rationale      : human-readable explanation of the routing decision
                     (shown in verbose mode, useful for debugging)
    """

    pattern: str
    scope: str
    date_from: str | None
    date_to: str | None
    use_sqlite: bool
    use_chroma: bool
    sqlite_field: str | None
    rationale: str
    # Internal — populated by RAGEngine after retrieval, not by router
    retrieved_dates: list[str] = field(default_factory=list)


# ── Query Router ───────────────────────────────────────────────────────────────


class QueryRouter:
    """
    Analyses a natural language question and returns a RouteDecision.

    Design principle: be fast and conservative.
    - Pattern A is checked first (cheapest — no embeddings at all)
    - Pattern C is preferred over B when any date signal is present
      (scoped vector search is always faster than a full collection scan)
    - Pattern D is only chosen when the question asks for patterns/trends
      across content that isn't date-bounded

    The router does NOT call any external services — it is pure Python
    string analysis. All LLM + ChromaDB calls happen in RAGEngine.
    """

    # ── Metadata fields the router recognises for Pattern A ──────────────────
    _SQLITE_FIELDS = {
        "projects": ["project", "projects", "working on", "worked on", "built"],
        "blockers": [
            "blocker",
            "blockers",
            "blocked",
            "blocking",
            "impediment",
            "stuck",
        ],
        "sentiment": [
            "sentiment",
            "mood",
            "feeling",
            "felt",
            "energy",
            "productive",
            "frustrated",
        ],
        "focus_areas": [
            "focus",
            "focus area",
            "focus areas",
            "topic",
            "topics",
            "area",
            "areas",
        ],
        "people": [
            "people",
            "person",
            "team",
            "teams",
            "met with",
            "talked to",
            "colleague",
        ],
    }

    # ── Relative date phrases → (days_back_from, days_back_to) ───────────────
    # "last week" → from 7 days ago to yesterday
    _RELATIVE_DATE_PATTERNS: list[tuple[str, int, int]] = [
        (r"\byesterday\b", 1, 1),
        (r"\blast\s+week\b", 7, 1),
        (r"\bpast\s+week\b", 7, 1),
        (r"\blast\s+7\s+days?\b", 7, 1),
        (r"\blast\s+two\s+weeks?\b", 14, 1),
        (r"\blast\s+2\s+weeks?\b", 14, 1),
        (r"\blast\s+fortnight\b", 14, 1),
        (r"\blast\s+month\b", 30, 1),
        (r"\bpast\s+month\b", 30, 1),
        (r"\blast\s+30\s+days?\b", 30, 1),
        (r"\blast\s+quarter\b", 90, 1),
        (r"\blast\s+3\s+months?\b", 90, 1),
        (r"\bthis\s+week\b", 7, 0),
        (r"\bthis\s+month\b", 31, 0),
    ]

    # ── Named month → (month_number, year_offset) — assumes current year ─────
    _MONTH_NAMES = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    # ── Pattern D trigger phrases (cross-cutting, analytical questions) ───────
    _PATTERN_D_PHRASES = [
        r"\bpattern",
        r"\btrend",
        r"\bover\s+time\b",
        r"\bchange[ds]?\b.*\bover\b",
        r"\bevol",
        r"\bcompare\b",
        r"\bprogress\b",
    ]

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()
        self._today = date.today()

    def route(self, question: str, scope: str = "all") -> RouteDecision:
        """
        Analyse the question and return the optimal RouteDecision.

        Args:
            question : The raw user question string
            scope    : "public" | "sensitive" | "all" (from CLI --scope flag)

        Returns:
            RouteDecision with pattern, date bounds, and retrieval flags set
        """
        q = question.lower().strip()

        # ── Pattern A check — structured/aggregate questions ─────────────────
        pattern_a = self._check_pattern_a(q)
        if pattern_a is not None:
            sqlite_field, rationale = pattern_a
            return RouteDecision(
                pattern="A",
                scope=scope,
                date_from=self._extract_date_from(q),
                date_to=self._extract_date_to(q),
                use_sqlite=True,
                use_chroma=False,
                sqlite_field=sqlite_field,
                rationale=rationale,
            )

        # ── Extract any date signal from the question ─────────────────────────
        date_from = self._extract_date_from(q)
        date_to = self._extract_date_to(q)
        has_date_signal = date_from is not None or date_to is not None

        # ── Pattern D check — cross-cutting analytical questions ──────────────
        if self._is_pattern_d(q) and not has_date_signal:
            return RouteDecision(
                pattern="D",
                scope=scope,
                date_from=None,
                date_to=None,
                use_sqlite=True,
                use_chroma=True,
                sqlite_field=None,
                rationale=(
                    "Analytical/pattern question detected — "
                    "ChromaDB semantic search first, SQLite enrichment second"
                ),
            )

        # ── Pattern C — date signal present → scoped ChromaDB search ─────────
        if has_date_signal:
            return RouteDecision(
                pattern="C",
                scope=scope,
                date_from=date_from,
                date_to=date_to,
                use_sqlite=True,
                use_chroma=True,
                sqlite_field=None,
                rationale=(
                    f"Date signal detected (from={date_from}, to={date_to}) — "
                    f"SQLite date filter scopes ChromaDB vector search"
                ),
            )

        # ── Pattern B — default: pure semantic search ─────────────────────────
        return RouteDecision(
            pattern="B",
            scope=scope,
            date_from=None,
            date_to=None,
            use_sqlite=False,
            use_chroma=True,
            sqlite_field=None,
            rationale=(
                "No date signal, no aggregate trigger — "
                "pure ChromaDB semantic search across all dates"
            ),
        )

    # ── Pattern A Detection ───────────────────────────────────────────────────

    def _check_pattern_a(self, q: str) -> tuple[str, str] | None:
        """
        Check if the question is a structured/aggregate query best answered
        by SQLite alone.

        Returns (sqlite_field, rationale) if Pattern A, else None.

        Detection logic:
          1. Does the question contain a known aggregate trigger phrase?
             (from config.yaml query.sqlite_trigger_phrases)
          2. Can we identify WHICH metadata field it's asking about?

        Both conditions must be true for Pattern A — otherwise we fall through
        to semantic search which is more forgiving.
        """
        trigger_phrases = getattr(self.config, "query_sqlite_trigger_phrases", [])

        has_aggregate_trigger = any(phrase in q for phrase in trigger_phrases)
        if not has_aggregate_trigger:
            return None

        # Identify the target field
        for field_name, keywords in self._SQLITE_FIELDS.items():
            if any(kw in q for kw in keywords):
                return (
                    field_name,
                    f"Aggregate trigger + field '{field_name}' detected — SQLite only",
                )

        # Aggregate trigger present but no clear field — still Pattern A
        # but with no specific field (RAGEngine will do a general count)
        return (
            None,
            "Aggregate trigger detected (no specific field) — SQLite only",
        )

    # ── Pattern D Detection ───────────────────────────────────────────────────

    def _is_pattern_d(self, q: str) -> bool:
        """
        Check if question is asking for cross-cutting patterns or trends.
        These need both ChromaDB (content) and SQLite (facts) to answer well.
        """
        return any(re.search(pattern, q) for pattern in self._PATTERN_D_PHRASES)

    # ── Date Extraction ───────────────────────────────────────────────────────

    def _extract_date_from(self, q: str) -> str | None:
        """
        Extract the start date (inclusive lower bound) from natural language.

        Handles:
          - Relative phrases: "last week", "last month", "past 7 days"
          - Named months:     "in January", "in February 2026"
          - ISO dates:        "from 2026-01-01", "since 2026-02-01"

        Returns ISO date string (YYYY-MM-DD) or None if no signal found.
        """
        # Relative phrases
        for pattern, days_back_from, _ in self._RELATIVE_DATE_PATTERNS:
            if re.search(pattern, q):
                return (self._today - timedelta(days=days_back_from)).isoformat()

        # Named month (e.g. "in january", "during february")
        month_match = re.search(
            r"\b(?:in|during|for)\s+(" + "|".join(self._MONTH_NAMES.keys()) + r")"
            r"(?:\s+(\d{4}))?",
            q,
        )
        if month_match:
            month_num = self._MONTH_NAMES[month_match.group(1)]
            year = (
                int(month_match.group(2)) if month_match.group(2) else self._today.year
            )
            # If the named month is in the future for current year, use last year
            if year == self._today.year and month_num > self._today.month:
                year -= 1
            return f"{year}-{month_num:02d}-01"

        # Explicit ISO date after "from" / "since" / "after"
        iso_match = re.search(r"(?:from|since|after|starting)\s+(\d{4}-\d{2}-\d{2})", q)
        if iso_match:
            return iso_match.group(1)

        return None

    def _extract_date_to(self, q: str) -> str | None:
        """
        Extract the end date (inclusive upper bound) from natural language.

        Handles:
          - Relative phrases: "last week" → yesterday, "this week" → today
          - Named months:     "in January" → last day of January
          - ISO dates:        "to 2026-01-31", "until 2026-02-20"

        Returns ISO date string (YYYY-MM-DD) or None if no signal found.
        """
        # Relative phrases → end date
        for pattern, _, days_back_to in self._RELATIVE_DATE_PATTERNS:
            if re.search(pattern, q):
                if days_back_to == 0:
                    return self._today.isoformat()
                return (self._today - timedelta(days=days_back_to)).isoformat()

        # Named month → last day of that month
        month_match = re.search(
            r"\b(?:in|during|for)\s+(" + "|".join(self._MONTH_NAMES.keys()) + r")"
            r"(?:\s+(\d{4}))?",
            q,
        )
        if month_match:
            month_num = self._MONTH_NAMES[month_match.group(1)]
            year = (
                int(month_match.group(2)) if month_match.group(2) else self._today.year
            )
            if year == self._today.year and month_num > self._today.month:
                year -= 1
            # Last day of month: first day of next month minus one day
            if month_num == 12:
                last_day = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                last_day = date(year, month_num + 1, 1) - timedelta(days=1)
            return last_day.isoformat()

        # Explicit ISO date after "to" / "until" / "through" / "before"
        iso_match = re.search(
            r"(?:to|until|through|before|ending)\s+(\d{4}-\d{2}-\d{2})", q
        )
        if iso_match:
            return iso_match.group(1)

        return None

    # ── Debug / Introspection ─────────────────────────────────────────────────

    def explain(self, question: str, scope: str = "all") -> str:
        """
        Return a human-readable explanation of the routing decision.
        Useful for debugging why a question was routed a certain way.

        Usage:
            print(router.explain("What was I blocked on last month?"))
        """
        decision = self.route(question, scope)
        lines = [
            "🔀 Query Router — Decision",
            f"   Question : {question}",
            f"   Pattern  : {decision.pattern}",
            f"   Scope    : {decision.scope}",
            f"   Date from: {decision.date_from or '(none)'}",
            f"   Date to  : {decision.date_to or '(none)'}",
            f"   SQLite   : {'✅' if decision.use_sqlite else '❌'}",
            f"   ChromaDB : {'✅' if decision.use_chroma else '❌'}",
            f"   Field    : {decision.sqlite_field or '(none)'}",
            f"   Rationale: {decision.rationale}",
        ]
        return "\n".join(lines)
