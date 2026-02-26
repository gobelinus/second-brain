"""
tests/test_router.py — Unit tests for QueryRouter.

Run with:
    uv run pytest tests/test_router.py -v

These tests are OFFLINE — they do not call Ollama, ChromaDB, or SQLite.
The router is pure Python string analysis, so tests run instantly.

Coverage:
  ✅ Pattern A — aggregate/structured questions
  ✅ Pattern B — pure semantic questions
  ✅ Pattern C — date-scoped semantic questions
  ✅ Pattern D — cross-cutting pattern/trend questions
  ✅ Date extraction — relative phrases, named months, ISO dates
  ✅ Scope passthrough
  ✅ explain() output format
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from brain.router import QueryRouter

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_config():
    """
    Minimal BrainConfig mock — only the fields router.py reads.
    Keeps tests isolated from config file I/O.
    """
    cfg = MagicMock()
    cfg.query_sqlite_trigger_phrases = [
        "how many",
        "count",
        "total",
        "which days",
        "list all",
        "on what dates",
        "how often",
        "most common",
        "least common",
        "average",
    ]
    return cfg


@pytest.fixture
def router(mock_config):
    """QueryRouter with mocked config."""
    r = QueryRouter(config=mock_config)
    # Pin today's date so relative date assertions are deterministic
    r._today = date(2026, 2, 25)
    return r


# ── Pattern A Tests ───────────────────────────────────────────────────────────


class TestPatternA:
    """Aggregate/count/filter questions → SQLite only."""

    def test_how_many_blocked_days(self, router):
        d = router.route("How many days was I blocked last month?")
        assert d.pattern == "A"
        assert d.use_sqlite is True
        assert d.use_chroma is False
        assert d.sqlite_field == "blockers"

    def test_count_projects(self, router):
        d = router.route("Count how many projects I worked on in January")
        assert d.pattern == "A"
        assert d.sqlite_field == "projects"

    def test_list_all_people(self, router):
        d = router.route("List all people I met with this month")
        assert d.pattern == "A"
        assert d.sqlite_field == "people"

    def test_most_common_sentiment(self, router):
        d = router.route("What was my most common sentiment in February?")
        assert d.pattern == "A"
        assert d.sqlite_field == "sentiment"

    def test_how_often_focus_area(self, router):
        d = router.route("How often did I focus on AWS topics?")
        assert d.pattern == "A"
        assert d.sqlite_field == "focus_areas"

    def test_pattern_a_includes_date_bounds(self, router):
        """Pattern A should still extract date bounds for SQLite WHERE clause."""
        d = router.route("How many days was I blocked in January?")
        assert d.pattern == "A"
        assert d.date_from == "2026-01-01"
        assert d.date_to == "2026-01-31"


# ── Pattern B Tests ───────────────────────────────────────────────────────────


class TestPatternB:
    """Pure semantic questions with no date signal → ChromaDB only."""

    def test_feeling_overwhelmed(self, router):
        d = router.route("Find notes about feeling overwhelmed")
        assert d.pattern == "B"
        assert d.use_chroma is True
        assert d.use_sqlite is False
        assert d.date_from is None
        assert d.date_to is None

    def test_azure_work(self, router):
        d = router.route("What did I say about Azure architecture?")
        assert d.pattern == "B"
        assert d.use_chroma is True

    def test_what_was_i_blocked_on(self, router):
        """No date signal → Pattern B (not Pattern A — no aggregate trigger)."""
        d = router.route("What was I blocked on?")
        assert d.pattern == "B"

    def test_scope_passed_through(self, router):
        d = router.route("Find notes about cost optimization", scope="sensitive")
        assert d.scope == "sensitive"

    def test_scope_default_all(self, router):
        d = router.route("Find notes about cost optimization")
        assert d.scope == "all"


# ── Pattern C Tests ───────────────────────────────────────────────────────────


class TestPatternC:
    """Date signal present → SQLite scoped ChromaDB search."""

    def test_last_month(self, router):
        d = router.route("What was I blocked on last month?")
        assert d.pattern == "C"
        assert d.use_sqlite is True
        assert d.use_chroma is True
        # "last month" from 2026-02-25 → from=2026-01-26, to=2026-02-24
        assert d.date_from == "2026-01-26"
        assert d.date_to == "2026-02-24"

    def test_last_week(self, router):
        d = router.route("Summarise my work last week")
        assert d.pattern == "C"
        assert d.date_from == "2026-02-18"
        assert d.date_to == "2026-02-24"

    def test_named_month_january(self, router):
        d = router.route("What were my blockers in January?")
        assert d.pattern == "C"
        assert d.date_from == "2026-01-01"
        assert d.date_to == "2026-01-31"

    def test_named_month_december(self, router):
        """December is before current month — should use current year."""
        d = router.route("What did I work on in December?")
        assert d.pattern == "C"
        assert d.date_from == "2025-12-01"
        assert d.date_to == "2025-12-31"

    def test_iso_date_from_to(self, router):
        d = router.route("Summarise from 2026-01-01 to 2026-01-31")
        assert d.pattern == "C"
        assert d.date_from == "2026-01-01"
        assert d.date_to == "2026-01-31"

    def test_yesterday(self, router):
        d = router.route("What did I do yesterday?")
        assert d.pattern == "C"
        assert d.date_from == "2026-02-24"
        assert d.date_to == "2026-02-24"

    def test_past_week(self, router):
        d = router.route("Show me notes from the past week")
        assert d.pattern == "C"
        assert d.date_from == "2026-02-18"

    def test_last_30_days(self, router):
        d = router.route("What projects did I work on over the last 30 days?")
        assert d.pattern == "C"
        assert d.date_from == "2026-01-26"


# ── Pattern D Tests ───────────────────────────────────────────────────────────


class TestPatternD:
    """Cross-cutting analytical questions → ChromaDB first, SQLite enrichment."""

    def test_patterns_in_blockers(self, router):
        d = router.route("What patterns do you see in my blockers?")
        assert d.pattern == "D"
        assert d.use_sqlite is True
        assert d.use_chroma is True
        assert d.date_from is None

    def test_sentiment_trend(self, router):
        d = router.route("How has my sentiment evolved over time?")
        assert d.pattern == "D"

    def test_progress_question(self, router):
        d = router.route("Show me my progress on infrastructure projects")
        assert d.pattern == "D"

    def test_compare_projects(self, router):
        d = router.route("Compare my focus between AWS and Azure work")
        assert d.pattern == "D"

    def test_pattern_d_overridden_by_date_signal(self, router):
        """
        Pattern D should NOT trigger when a date range is present —
        Pattern C is more specific and should win.
        """
        d = router.route("What trends do I see in my blockers last month?")
        # "last month" gives a date signal → Pattern C wins over Pattern D
        assert d.pattern == "C"


# ── Date Extraction Tests ─────────────────────────────────────────────────────


class TestDateExtraction:
    """Direct tests of _extract_date_from and _extract_date_to."""

    def test_relative_this_week(self, router):
        from_date = router._extract_date_from("this week")
        to_date = router._extract_date_to("this week")
        # "this week" from 2026-02-25 → from = 7 days ago, to = today
        assert from_date == "2026-02-18"
        assert to_date == "2026-02-25"

    def test_relative_last_quarter(self, router):
        from_date = router._extract_date_from("last quarter")
        assert from_date == "2025-11-27"

    def test_iso_since(self, router):
        from_date = router._extract_date_from("what happened since 2026-02-01?")
        assert from_date == "2026-02-01"

    def test_iso_until(self, router):
        to_date = router._extract_date_to("summarise until 2026-02-20")
        assert to_date == "2026-02-20"

    def test_no_date_signal(self, router):
        assert router._extract_date_from("what blocked me?") is None
        assert router._extract_date_to("what blocked me?") is None

    def test_february_last_day(self, router):
        """February 2026 ends on 28th (non-leap year)."""
        to_date = router._extract_date_to("in february 2026")
        assert to_date == "2026-02-28"

    def test_january_last_day(self, router):
        to_date = router._extract_date_to("in january")
        assert to_date == "2026-01-31"

    def test_future_month_uses_last_year(self, router):
        """March is after February (current month), so use 2025."""
        from_date = router._extract_date_from("in march")
        assert from_date == "2025-03-01"


# ── Explain Tests ─────────────────────────────────────────────────────────────


class TestExplain:
    def test_explain_returns_string(self, router):
        output = router.explain("What was I blocked on last month?")
        assert isinstance(output, str)
        assert "Pattern" in output
        assert "Rationale" in output

    def test_explain_shows_dates(self, router):
        output = router.explain("What blocked me in January?")
        assert "2026-01-01" in output
        assert "2026-01-31" in output
