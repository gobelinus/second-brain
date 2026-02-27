"""
tests/test_cli_phase3.py — CLI tests for Phase 3 query + summarize commands.

Run with:
    uv run pytest tests/test_cli_phase3.py -v

Uses Typer's CliRunner to invoke commands as the user would.
All LLM, ChromaDB, and SQLite calls are mocked — no Ollama needed.

Coverage:
  ✅ brain query — success, renders answer + metadata footer
  ✅ brain query --export — exports result.to_markdown()
  ✅ brain query invalid scope — exits with code 1
  ✅ brain query RAGEngine error — exits with code 1
  ✅ brain query no results — shows graceful message
  ✅ brain summarize — success, renders answer + metadata footer
  ✅ brain summarize --export — exports result.to_markdown()
  ✅ brain summarize invalid date format — exits with code 1
  ✅ brain summarize end before start — exits with code 1
  ✅ brain summarize invalid scope — exits with code 1
  ✅ brain summarize RAGEngine error — exits with code 1
"""

from unittest.mock import patch

from typer.testing import CliRunner

from brain.cli import app
from brain.query import QueryResult

# ── Runner ────────────────────────────────────────────────────────────────────

runner = CliRunner()

# ── Fixtures ──────────────────────────────────────────────────────────────────


def make_query_result(
    question="What was I blocked on?",
    answer="You were blocked by infra approvals.",
    pattern="C",
    scope="all",
    chunks_retrieved=3,
    dates_covered=None,
    date_from="2026-01-26",
    date_to="2026-02-24",
    rationale="Date scoped",
) -> QueryResult:
    return QueryResult(
        question=question,
        answer=answer,
        pattern=pattern,
        scope=scope,
        chunks_retrieved=chunks_retrieved,
        dates_covered=dates_covered or ["2026-02-10", "2026-02-14"],
        date_from=date_from,
        date_to=date_to,
        rationale=rationale,
    )


def make_summarize_result() -> QueryResult:
    return QueryResult(
        question="Summarise all activity from 2026-02-01 to 2026-02-20.",
        answer="## Summary\n\nYou focused on Brain CLI and Platform work.",
        pattern="C",
        scope="all",
        chunks_retrieved=5,
        dates_covered=["2026-02-10", "2026-02-14", "2026-02-18"],
        date_from="2026-02-01",
        date_to="2026-02-20",
        rationale="Forced Pattern C for summarize",
    )


# ── brain query tests ─────────────────────────────────────────────────────────


class TestQueryCommand:
    def test_query_success_renders_answer(self):
        result = make_query_result()
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.answer.return_value = result

            output = runner.invoke(app, ["query", "What was I blocked on?"])

        assert output.exit_code == 0
        assert "You were blocked by infra approvals." in output.output

    def test_query_renders_metadata_footer(self):
        result = make_query_result()
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.answer.return_value = result

            output = runner.invoke(app, ["query", "What was I blocked on?"])

        assert "Pattern" in output.output
        assert result.pattern in output.output
        assert "2026-02-10" in output.output  # dates_covered

    def test_query_invalid_scope_exits_1(self):
        output = runner.invoke(app, ["query", "anything", "--scope", "banana"])
        assert output.exit_code == 1
        assert "Invalid scope" in output.output

    def test_query_engine_value_error_exits_1(self):
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.answer.side_effect = ValueError("bad query")

            output = runner.invoke(app, ["query", "bad question"])

        assert output.exit_code == 1
        assert "Query error" in output.output

    def test_query_engine_unexpected_error_exits_1(self):
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.answer.side_effect = RuntimeError("ollama down")

            output = runner.invoke(app, ["query", "something"])

        assert output.exit_code == 1
        assert "Unexpected error" in output.output

    def test_query_no_chunks_shows_graceful_message(self):
        """When RAGEngine returns 'No relevant notes found', CLI shows it cleanly."""
        result = make_query_result(
            answer="No relevant notes found for this query.",
            chunks_retrieved=0,
            dates_covered=[],
        )
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.answer.return_value = result

            output = runner.invoke(app, ["query", "something obscure"])

        assert output.exit_code == 0
        assert "No relevant notes found" in output.output

    def test_query_export_calls_handle_export(self):
        """--export should invoke the export handler with to_markdown() content."""
        result = make_query_result()
        with (
            patch("brain.cli.RAGEngine") as MockEngine,
            patch("brain.cli.BrainConfig"),
            patch("brain.cli._handle_export") as mock_export,
            patch("brain.cli.Exporter"),
        ):
            MockEngine.return_value.answer.return_value = result

            output = runner.invoke(app, ["query", "What blocked me?", "--export"])

        assert output.exit_code == 0
        mock_export.assert_called_once()
        # Verify to_markdown() content was passed, not a raw string
        call_kwargs = mock_export.call_args.kwargs
        assert "# 🧠 Brain Query Result" in call_kwargs["content"]

    def test_query_scope_passed_to_engine(self):
        """--scope value must be forwarded to RAGEngine.answer()."""
        result = make_query_result(scope="sensitive")
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.answer.return_value = result

            runner.invoke(
                app, ["query", "find sensitive notes", "--scope", "sensitive"]
            )

        MockEngine.return_value.answer.assert_called_once_with(
            question="find sensitive notes",
            scope="sensitive",
        )


# ── brain summarize tests ─────────────────────────────────────────────────────


class TestSummarizeCommand:
    def test_summarize_success_renders_answer(self):
        result = make_summarize_result()
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.summarize.return_value = result

            output = runner.invoke(
                app, ["summarize", "--from", "2026-02-01", "--to", "2026-02-20"]
            )

        assert output.exit_code == 0
        assert "Brain CLI" in output.output

    def test_summarize_renders_metadata_footer(self):
        result = make_summarize_result()
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.summarize.return_value = result

            output = runner.invoke(
                app, ["summarize", "--from", "2026-02-01", "--to", "2026-02-20"]
            )

        assert "Pattern" in output.output
        assert "C" in output.output
        assert "5" in output.output  # chunks_retrieved

    def test_summarize_invalid_date_format_exits_1(self):
        output = runner.invoke(
            app, ["summarize", "--from", "01-02-2026", "--to", "2026-02-20"]
        )
        assert output.exit_code == 1
        assert "Invalid date format" in output.output

    def test_summarize_end_before_start_exits_1(self):
        output = runner.invoke(
            app, ["summarize", "--from", "2026-02-20", "--to", "2026-02-01"]
        )
        assert output.exit_code == 1
        assert "--to date must be after" in output.output

    def test_summarize_invalid_scope_exits_1(self):
        output = runner.invoke(
            app,
            [
                "summarize",
                "--from",
                "2026-02-01",
                "--to",
                "2026-02-20",
                "--scope",
                "banana",
            ],
        )
        assert output.exit_code == 1
        assert "Invalid scope" in output.output

    def test_summarize_engine_error_exits_1(self):
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.summarize.side_effect = ValueError(
                "range too large"
            )

            output = runner.invoke(
                app, ["summarize", "--from", "2026-01-01", "--to", "2026-02-20"]
            )

        assert output.exit_code == 1
        assert "Summarize error" in output.output

    def test_summarize_export_calls_handle_export(self):
        result = make_summarize_result()
        with (
            patch("brain.cli.RAGEngine") as MockEngine,
            patch("brain.cli.BrainConfig"),
            patch("brain.cli._handle_export") as mock_export,
            patch("brain.cli.Exporter"),
        ):
            MockEngine.return_value.summarize.return_value = result

            output = runner.invoke(
                app,
                ["summarize", "--from", "2026-02-01", "--to", "2026-02-20", "--export"],
            )

        assert output.exit_code == 0
        mock_export.assert_called_once()
        call_kwargs = mock_export.call_args.kwargs
        assert "# 🧠 Brain Query Result" in call_kwargs["content"]

    def test_summarize_passes_correct_args_to_engine(self):
        """date_from, date_to, scope must all be forwarded correctly."""
        result = make_summarize_result()
        with patch("brain.cli.RAGEngine") as MockEngine, patch("brain.cli.BrainConfig"):
            MockEngine.return_value.summarize.return_value = result

            runner.invoke(
                app,
                [
                    "summarize",
                    "--from",
                    "2026-02-01",
                    "--to",
                    "2026-02-20",
                    "--scope",
                    "sensitive",
                ],
            )

        MockEngine.return_value.summarize.assert_called_once_with(
            date_from="2026-02-01",
            date_to="2026-02-20",
            scope="sensitive",
        )
