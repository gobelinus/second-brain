"""
trend.py — SQLite aggregation queries for Brain.

Provides structured trend analysis across daily summary metadata.
All queries run against SQLite only — no LLM, no ChromaDB.

Supported fields:
  projects    — frequency + chronological breakdown
  blockers    — frequency + chronological breakdown
  focus_areas — frequency + chronological breakdown
  people      — frequency + chronological breakdown
  sentiment   — distribution + per-date breakdown

Query patterns:
  - Date range filtering (--from, --to)
  - Sensitivity scoping (public, sensitive, all)
  - All array fields use json_each() for proper unnesting
"""

import json
import sqlite3
from dataclasses import dataclass, field

from brain.config import BrainConfig

# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class FrequencyRow:
    value: str
    count: int


@dataclass
class ChronologicalRow:
    date: str
    sensitivity: str
    values: list[str]


@dataclass
class SentimentDistribution:
    sentiment: str
    count: int
    percentage: float


@dataclass
class TrendResult:
    """Unified result container for all trend queries."""

    field: str
    date_from: str | None
    date_to: str | None
    sensitivity: str
    total_days: int

    # Array field results
    frequency: list[FrequencyRow] = field(default_factory=list)
    chronological: list[ChronologicalRow] = field(default_factory=list)

    # Sentiment-specific results
    distribution: list[SentimentDistribution] = field(default_factory=list)
    sentiment_by_date: list[tuple[str, str, str]] = field(default_factory=list)
    # (date, sensitivity, sentiment)


# ── Trend Engine ──────────────────────────────────────────────────────────────


class TrendEngine:
    """
    Runs SQLite aggregation queries against daily_summaries.
    All public methods return TrendResult objects.
    """

    ARRAY_FIELDS = ("projects", "blockers", "focus_areas", "people")
    ALL_FIELDS = ARRAY_FIELDS + ("sentiment",)

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    def query(
        self,
        field: str,
        date_from: str | None = None,
        date_to: str | None = None,
        sensitivity: str = "all",
    ) -> TrendResult:
        """
        Main entry point. Run a trend query for the given field.

        Args:
            field:       One of: projects, blockers, focus_areas, people, sentiment
            date_from:   Start date inclusive (YYYY-MM-DD), or None for all
            date_to:     End date inclusive (YYYY-MM-DD), or None for all
            sensitivity: 'public', 'sensitive', or 'all'

        Returns:
            TrendResult with frequency + chronological data populated.

        Raises:
            ValueError: If field is invalid or database doesn't exist.
        """
        self._validate_field(field)
        self._validate_db()

        result = TrendResult(
            field=field,
            date_from=date_from,
            date_to=date_to,
            sensitivity=sensitivity,
            total_days=self._count_days(date_from, date_to, sensitivity),
        )

        if field == "sentiment":
            result.distribution = self._sentiment_distribution(
                date_from, date_to, sensitivity
            )
            result.sentiment_by_date = self._sentiment_by_date(
                date_from, date_to, sensitivity
            )
        else:
            result.frequency = self._array_frequency(
                field, date_from, date_to, sensitivity
            )
            result.chronological = self._array_chronological(
                field, date_from, date_to, sensitivity
            )

        return result

    # ── Query methods ─────────────────────────────────────────────────────────

    def _array_frequency(
        self,
        field: str,
        date_from: str | None,
        date_to: str | None,
        sensitivity: str,
    ) -> list[FrequencyRow]:
        """
        Count occurrences of each unique value in a JSON array field.
        Uses json_each() to properly unnest array values.
        Returns sorted by count descending.
        """
        params, where = self._build_where(date_from, date_to, sensitivity)

        # json_each unnests the JSON array column into individual rows
        sql = f"""
            SELECT json_each.value AS item, COUNT(*) AS cnt
            FROM daily_summaries, json_each(daily_summaries.{field})
            {where}
            GROUP BY json_each.value
            ORDER BY cnt DESC, json_each.value ASC
        """

        rows = self._execute(sql, params)
        return [FrequencyRow(value=row[0], count=row[1]) for row in rows]

    def _array_chronological(
        self,
        field: str,
        date_from: str | None,
        date_to: str | None,
        sensitivity: str,
    ) -> list[ChronologicalRow]:
        """
        Return per-date values for a JSON array field.
        Returns sorted by date ascending.
        """
        params, where = self._build_where(date_from, date_to, sensitivity)

        sql = f"""
            SELECT date, sensitivity, {field}
            FROM daily_summaries
            {where}
            ORDER BY date ASC, sensitivity ASC
        """

        rows = self._execute(sql, params)
        return [
            ChronologicalRow(
                date=row[0],
                sensitivity=row[1],
                values=json.loads(row[2]),
            )
            for row in rows
        ]

    def _sentiment_distribution(
        self,
        date_from: str | None,
        date_to: str | None,
        sensitivity: str,
    ) -> list[SentimentDistribution]:
        """
        Count and percentage breakdown of each sentiment value.
        Returns sorted by count descending.
        """
        params, where = self._build_where(date_from, date_to, sensitivity)

        sql = f"""
            SELECT sentiment, COUNT(*) AS cnt
            FROM daily_summaries
            {where}
            GROUP BY sentiment
            ORDER BY cnt DESC
        """

        rows = self._execute(sql, params)
        total = sum(row[1] for row in rows)

        return [
            SentimentDistribution(
                sentiment=row[0],
                count=row[1],
                percentage=round((row[1] / total * 100), 1) if total > 0 else 0.0,
            )
            for row in rows
        ]

    def _sentiment_by_date(
        self,
        date_from: str | None,
        date_to: str | None,
        sensitivity: str,
    ) -> list[tuple[str, str, str]]:
        """
        Return (date, sensitivity, sentiment) for each entry.
        Sorted by date ascending.
        """
        params, where = self._build_where(date_from, date_to, sensitivity)

        sql = f"""
            SELECT date, sensitivity, sentiment
            FROM daily_summaries
            {where}
            ORDER BY date ASC, sensitivity ASC
        """

        return self._execute(sql, params)

    def _count_days(
        self,
        date_from: str | None,
        date_to: str | None,
        sensitivity: str,
    ) -> int:
        """Count total matching entries for context in output."""
        params, where = self._build_where(date_from, date_to, sensitivity)
        sql = f"SELECT COUNT(*) FROM daily_summaries {where}"
        result = self._execute(sql, params)
        return result[0][0] if result else 0

    # ── SQL helpers ───────────────────────────────────────────────────────────

    def _build_where(
        self,
        date_from: str | None,
        date_to: str | None,
        sensitivity: str,
    ) -> tuple[list, str]:
        """
        Build WHERE clause and params list from filter arguments.
        Returns (params, where_clause_string).
        """
        conditions = []
        params = []

        if date_from:
            conditions.append("date >= ?")
            params.append(date_from)

        if date_to:
            conditions.append("date <= ?")
            params.append(date_to)

        if sensitivity != "all":
            conditions.append("sensitivity = ?")
            params.append(sensitivity)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        return params, where

    def _execute(self, sql: str, params: list) -> list:
        """Execute a query and return all rows."""
        conn = sqlite3.connect(self.config.sqlite_db)
        try:
            rows = conn.execute(sql, params).fetchall()
            return rows
        finally:
            conn.close()

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_field(self, field: str) -> None:
        if field not in self.ALL_FIELDS:
            raise ValueError(
                f"Invalid field '{field}'. Choose from: {', '.join(self.ALL_FIELDS)}"
            )

    def _validate_db(self) -> None:
        if not self.config.sqlite_db.exists():
            raise ValueError(
                "No database found. Run 'brain ingest' first to populate data."
            )


# ── Formatter ─────────────────────────────────────────────────────────────────


class TrendFormatter:
    """
    Formats TrendResult into human-readable markdown output.
    Used by both CLI display and --export.
    """

    def format(self, result: TrendResult) -> str:
        """Format a TrendResult as a markdown string."""
        lines = []

        # ── Header ────────────────────────────────────────────────────────────
        lines.append(f"# Trend: {result.field.replace('_', ' ').title()}")
        lines.append("")

        date_range = self._format_date_range(result.date_from, result.date_to)
        lines.append(f"**Date range:** {date_range}")
        lines.append(f"**Sensitivity:** {result.sensitivity}")
        lines.append(f"**Entries analysed:** {result.total_days}")
        lines.append("")

        if result.field == "sentiment":
            lines.extend(self._format_sentiment(result))
        else:
            lines.extend(self._format_array_field(result))

        return "\n".join(lines)

    def _format_array_field(self, result: TrendResult) -> list[str]:
        lines = []

        # ── Frequency summary ─────────────────────────────────────────────────
        lines.append("## Frequency Summary")
        lines.append("")
        if result.frequency:
            for row in result.frequency:
                bar = "█" * min(row.count, 20)  # simple bar, max 20 chars
                lines.append(f"  {bar} {row.count:>3}x  {row.value}")
        else:
            lines.append("  No data found for this field and date range.")
        lines.append("")

        # ── Chronological breakdown ───────────────────────────────────────────
        lines.append("## Chronological Breakdown")
        lines.append("")
        if result.chronological:
            for row in result.chronological:
                lines.append(f"**{row.date}** ({row.sensitivity})")
                if row.values:
                    for v in row.values:
                        lines.append(f"  - {v}")
                else:
                    lines.append("  - none")
                lines.append("")
        else:
            lines.append("  No data found.")

        return lines

    def _format_sentiment(self, result: TrendResult) -> list[str]:
        lines = []

        # ── Distribution ──────────────────────────────────────────────────────
        lines.append("## Sentiment Distribution")
        lines.append("")
        if result.distribution:
            for row in result.distribution:
                bar = "█" * min(row.count, 20)
                lines.append(
                    f"  {bar} {row.count:>3}x  {row.percentage:>5.1f}%  {row.sentiment}"
                )
        else:
            lines.append("  No data found.")
        lines.append("")

        # ── Per-date breakdown ────────────────────────────────────────────────
        lines.append("## Per-Date Breakdown")
        lines.append("")

        # Emoji map for quick visual scan
        sentiment_emoji = {
            "productive": "🟢",
            "energized": "⚡",
            "neutral": "⚪",
            "frustrated": "🟡",
            "overwhelmed": "🔴",
        }

        if result.sentiment_by_date:
            for date, sensitivity, sentiment in result.sentiment_by_date:
                emoji = sentiment_emoji.get(sentiment, "⚪")
                lines.append(f"  {emoji} {date} ({sensitivity}) — {sentiment}")
        else:
            lines.append("  No data found.")
        lines.append("")

        return lines

    def _format_date_range(self, date_from: str | None, date_to: str | None) -> str:
        if date_from and date_to:
            return f"{date_from} → {date_to}"
        elif date_from:
            return f"{date_from} → present"
        elif date_to:
            return f"beginning → {date_to}"
        else:
            return "all time"
