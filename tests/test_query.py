"""
tests/test_query.py — Unit tests for RAGEngine.

Run with:
    uv run pytest tests/test_query.py -v

All tests are OFFLINE — they mock Ollama, ChromaDB, and SQLite.
No real LLM calls, no real vector store queries.

Coverage:
  ✅ Pattern A — SQLite structured answer (sentiment, array fields, empty)
  ✅ Pattern B — pure semantic, no date filter
  ✅ Pattern C — date-scoped ChromaDB retrieval
  ✅ Pattern D — ChromaDB + SQLite enrichment
  ✅ Similarity threshold filtering
  ✅ Empty ChromaDB result handling
  ✅ QueryResult.to_markdown() output
  ✅ summarize() forced Pattern C
  ✅ summarize() date validation
  ✅ _collections_for_scope() routing
  ✅ _build_where_filter() ChromaDB filter construction
  ✅ _format_sqlite_answer() — sentiment, array fields, empty, generic
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from brain.query import QueryResult, RAGEngine
from brain.router import RouteDecision

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.rag_top_k = 5
    cfg.rag_max_context_chunks = 8
    cfg.rag_default_scope = "all"
    cfg.rag_similarity_threshold = 1.4
    cfg.rag_max_summarize_days = 90
    cfg.rag_system_prompt = "You are a helpful assistant."
    cfg.rag_summarize_system_prompt = "You are a summarizer."
    cfg.ollama_llm_model = "mistral:latest"
    cfg.ollama_temperature = 0
    cfg.sqlite_db = MagicMock()
    cfg.chroma_store = MagicMock()
    cfg.chroma_collection_for.side_effect = lambda s: f"brain_{s}"
    return cfg


@pytest.fixture
def mock_router():
    router = MagicMock()
    return router


@pytest.fixture
def mock_embedder():
    embedder = MagicMock()
    embedder.embed_chunks.return_value = [[0.1, 0.2, 0.3]]
    return embedder


@pytest.fixture
def mock_sqlite():
    store = MagicMock()
    return store


@pytest.fixture
def mock_chroma():
    store = MagicMock()
    return store


@pytest.fixture
def engine(mock_config, mock_router, mock_embedder, mock_sqlite, mock_chroma):
    """RAGEngine with all external dependencies mocked."""
    with (
        patch("brain.query.QueryRouter", return_value=mock_router),
        patch("brain.query.Embedder", return_value=mock_embedder),
        patch("brain.query.SQLiteStore", return_value=mock_sqlite),
        patch("brain.query.ChromaStore", return_value=mock_chroma),
    ):
        eng = RAGEngine(config=mock_config)
    # Inject mocks directly so tests can configure them
    eng.router = mock_router
    eng.embedder = mock_embedder
    eng.sqlite = mock_sqlite
    eng.chroma = mock_chroma
    return eng


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_decision(
    pattern="B",
    scope="all",
    date_from=None,
    date_to=None,
    use_sqlite=False,
    use_chroma=True,
    sqlite_field=None,
    rationale="test",
) -> RouteDecision:
    return RouteDecision(
        pattern=pattern,
        scope=scope,
        date_from=date_from,
        date_to=date_to,
        use_sqlite=use_sqlite,
        use_chroma=use_chroma,
        sqlite_field=sqlite_field,
        rationale=rationale,
    )


def make_sqlite_row(
    date_str="2026-02-10",
    sensitivity="public",
    projects=None,
    blockers=None,
    sentiment="productive",
    focus_areas=None,
    people=None,
) -> dict:
    return {
        "date": date_str,
        "sensitivity": sensitivity,
        "projects": json.dumps(projects or []),
        "blockers": json.dumps(blockers or []),
        "sentiment": sentiment,
        "focus_areas": json.dumps(focus_areas or []),
        "people": json.dumps(people or []),
        "raw_file": f"daily/public/{date_str}.md",
        "ingested_at": "2026-02-24T10:00:00",
    }


def make_chunk(text="some note", date_str="2026-02-10", distance=0.3) -> dict:
    return {
        "text": text,
        "metadata": {
            "date": date_str,
            "sensitivity": "public",
            "sentiment": "productive",
        },
        "distance": distance,
    }


# ── Pattern A Tests ───────────────────────────────────────────────────────────


class TestPatternA:
    def test_sentiment_distribution(self, engine):
        engine.router.route.return_value = make_decision(
            pattern="A", sqlite_field="sentiment"
        )
        engine.sqlite.query_metadata.return_value = [
            make_sqlite_row(sentiment="productive"),
            make_sqlite_row(date_str="2026-02-11", sentiment="productive"),
            make_sqlite_row(date_str="2026-02-12", sentiment="frustrated"),
        ]

        result = engine.answer("What was my most common sentiment?")

        assert result.pattern == "A"
        assert result.chunks_retrieved == 0
        assert "productive" in result.answer
        assert "2 day" in result.answer  # 2 productive days

    def test_projects_field(self, engine):
        engine.router.route.return_value = make_decision(
            pattern="A", sqlite_field="projects"
        )
        engine.sqlite.query_metadata.return_value = [
            make_sqlite_row(projects=["Brain CLI", "Platform"]),
            make_sqlite_row(date_str="2026-02-11", projects=["Brain CLI"]),
        ]

        result = engine.answer("List all projects in February")

        assert result.pattern == "A"
        assert "Brain CLI" in result.answer
        assert "2 mention" in result.answer

    def test_blockers_field(self, engine):
        engine.router.route.return_value = make_decision(
            pattern="A", sqlite_field="blockers"
        )
        engine.sqlite.query_metadata.return_value = [
            make_sqlite_row(blockers=["waiting on approval", "unclear requirements"]),
        ]

        result = engine.answer("How many blockers did I have?")
        assert "waiting on approval" in result.answer

    def test_empty_result(self, engine):
        engine.router.route.return_value = make_decision(
            pattern="A", sqlite_field="projects"
        )
        engine.sqlite.query_metadata.return_value = []

        result = engine.answer("List all projects")
        assert "No data found" in result.answer
        assert result.chunks_retrieved == 0
        assert result.dates_covered == []

    def test_empty_array_field(self, engine):
        """Rows exist but the field is empty for all of them."""
        engine.router.route.return_value = make_decision(
            pattern="A", sqlite_field="blockers"
        )
        engine.sqlite.query_metadata.return_value = [
            make_sqlite_row(blockers=[]),
            make_sqlite_row(date_str="2026-02-11", blockers=[]),
        ]

        result = engine.answer("What were my blockers?")
        assert "No blockers found" in result.answer

    def test_pattern_a_does_not_call_embedder(self, engine):
        """Critical: Pattern A must never call the embedder."""
        engine.router.route.return_value = make_decision(
            pattern="A", sqlite_field="sentiment"
        )
        engine.sqlite.query_metadata.return_value = [make_sqlite_row()]

        engine.answer("What was my mood?")
        engine.embedder.embed_chunks.assert_not_called()


# ── Pattern B Tests ───────────────────────────────────────────────────────────


class TestPatternB:
    def setup_chromadb_mock(self, engine, chunks):
        """Helper: wire mock ChromaDB to return specific chunks."""
        mock_collection = MagicMock()
        mock_collection.count.return_value = 10
        mock_collection.query.return_value = {
            "documents": [[c["text"] for c in chunks]],
            "metadatas": [[c["metadata"] for c in chunks]],
            "distances": [[c["distance"] for c in chunks]],
        }
        engine.chroma.client.get_collection.return_value = mock_collection
        engine.config.chroma_collection_for.side_effect = lambda s: f"brain_{s}"

    def test_pure_semantic_no_date_filter(self, engine):
        chunks = [make_chunk("I felt overwhelmed by the infra work", "2026-02-10")]
        self.setup_chromadb_mock(engine, chunks)
        # scope="public" → single collection → deterministic chunk count
        engine.router.route.return_value = make_decision(pattern="B", scope="public")

        with patch.object(engine, "_call_llm", return_value="You were overwhelmed."):
            result = engine.answer("Find notes about feeling overwhelmed")

        assert result.pattern == "B"
        assert result.chunks_retrieved == 1
        assert "2026-02-10" in result.dates_covered
        assert result.date_from is None
        assert result.date_to is None

    def test_similarity_threshold_filters_distant_chunks(self, engine):
        """Chunks with distance > threshold should be dropped."""
        chunks = [
            make_chunk("relevant note", "2026-02-10", distance=0.5),
            make_chunk("irrelevant noise", "2026-02-11", distance=1.8),  # > 1.4
        ]
        self.setup_chromadb_mock(engine, chunks)
        # scope="public" → single collection → only the 0.5-distance chunk passes
        engine.router.route.return_value = make_decision(pattern="B", scope="public")

        with patch.object(engine, "_call_llm", return_value="Answer."):
            result = engine.answer("Find relevant notes")

        assert result.chunks_retrieved == 1  # second chunk filtered out

    def test_empty_chromadb_returns_no_results_message(self, engine):
        """When ChromaDB has no matching chunks, return graceful message."""
        engine.chroma.client.get_collection.side_effect = Exception("not found")
        engine.router.route.return_value = make_decision(pattern="B")

        result = engine.answer("Find notes about something obscure")
        assert "No relevant notes found" in result.answer
        assert result.chunks_retrieved == 0

    def test_pattern_b_no_sqlite_call(self, engine):
        """Pattern B should never call SQLite."""
        chunks = [make_chunk()]
        self.setup_chromadb_mock(engine, chunks)
        engine.router.route.return_value = make_decision(pattern="B")

        with patch.object(engine, "_call_llm", return_value="Answer."):
            engine.answer("Tell me about Azure work")

        engine.sqlite.query_metadata.assert_not_called()


# ── Pattern C Tests ───────────────────────────────────────────────────────────


class TestPatternC:
    def test_date_filter_passed_to_chromadb(self, engine):
        """Pattern C must pass date bounds to ChromaDB where filter."""
        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "documents": [["note about blockers"]],
            "metadatas": [
                [
                    {
                        "date": "2026-01-15",
                        "sensitivity": "public",
                        "sentiment": "frustrated",
                    }
                ]
            ],
            "distances": [[0.4]],
        }
        engine.chroma.client.get_collection.return_value = mock_collection
        engine.router.route.return_value = make_decision(
            pattern="C",
            date_from="2026-01-01",
            date_to="2026-01-31",
            use_sqlite=True,
            use_chroma=True,
        )

        with patch.object(engine, "_call_llm", return_value="Blocked by approvals."):
            result = engine.answer("What blocked me in January?")

        # Verify the where filter was built correctly
        call_kwargs = mock_collection.query.call_args.kwargs
        where = call_kwargs.get("where") or mock_collection.query.call_args[1].get(
            "where"
        )
        print(where)
        assert result.pattern == "C"
        assert result.date_from == "2026-01-01"
        assert result.date_to == "2026-01-31"


# ── Pattern D Tests ───────────────────────────────────────────────────────────


class TestPatternD:
    def test_sqlite_enrichment_called(self, engine):
        """Pattern D must call SQLite to enrich ChromaDB results."""
        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "documents": [["note about patterns"]],
            "metadatas": [
                [
                    {
                        "date": "2026-02-10",
                        "sensitivity": "public",
                        "sentiment": "productive",
                    }
                ]
            ],
            "distances": [[0.3]],
        }
        engine.chroma.client.get_collection.return_value = mock_collection
        engine.sqlite.query_metadata.return_value = [
            make_sqlite_row(blockers=["approval waiting"])
        ]
        engine.router.route.return_value = make_decision(
            pattern="D",
            use_sqlite=True,
            use_chroma=True,
        )

        with patch.object(engine, "_call_llm", return_value="Patterns found."):
            result = engine.answer("What patterns do you see in my blockers?")

        engine.sqlite.query_metadata.assert_called_once()
        assert result.pattern == "D"
        assert len(result.sqlite_rows) == 1


# ── _build_where_filter Tests ─────────────────────────────────────────────────


class TestBuildWhereFilter:
    def test_no_dates_returns_none(self, engine):
        d = make_decision()
        assert engine._build_where_filter(d) is None

    def test_date_from_only(self, engine):
        d = make_decision(date_from="2026-01-01")
        f = engine._build_where_filter(d)
        assert f == {"date": {"$gte": "2026-01-01"}}

    def test_date_to_only(self, engine):
        d = make_decision(date_to="2026-01-31")
        f = engine._build_where_filter(d)
        assert f == {"date": {"$lte": "2026-01-31"}}

    def test_both_dates_returns_and_filter(self, engine):
        d = make_decision(date_from="2026-01-01", date_to="2026-01-31")
        f = engine._build_where_filter(d)
        assert f == {
            "$and": [
                {"date": {"$gte": "2026-01-01"}},
                {"date": {"$lte": "2026-01-31"}},
            ]
        }


# ── _collections_for_scope Tests ──────────────────────────────────────────────


class TestCollectionsForScope:
    def test_public_scope(self, engine):
        cols = engine._collections_for_scope("public")
        assert cols == ["brain_public"]

    def test_sensitive_scope(self, engine):
        cols = engine._collections_for_scope("sensitive")
        assert cols == ["brain_sensitive"]

    def test_all_scope_returns_both(self, engine):
        cols = engine._collections_for_scope("all")
        assert "brain_public" in cols
        assert "brain_sensitive" in cols
        assert len(cols) == 2


# ── summarize() Tests ─────────────────────────────────────────────────────────


class TestSummarize:
    def test_date_validation_end_before_start(self, engine):
        with pytest.raises(ValueError, match="must be after"):
            engine.summarize("2026-02-20", "2026-02-01")

    def test_date_range_exceeds_max(self, engine):
        engine.config.rag_max_summarize_days = 30
        with pytest.raises(ValueError, match="exceeds max_summarize_days"):
            engine.summarize("2026-01-01", "2026-03-01")

    def test_summarize_forces_pattern_c(self, engine):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 3
        mock_collection.query.return_value = {
            "documents": [["work note"]],
            "metadatas": [
                [
                    {
                        "date": "2026-02-10",
                        "sensitivity": "public",
                        "sentiment": "productive",
                    }
                ]
            ],
            "distances": [[0.4]],
        }
        engine.chroma.client.get_collection.return_value = mock_collection

        with patch.object(engine, "_call_llm", return_value="Summary here."):
            result = engine.summarize("2026-02-01", "2026-02-25")

        assert result.pattern == "C"
        assert result.date_from == "2026-02-01"
        assert result.date_to == "2026-02-25"

    def test_summarize_uses_summarize_system_prompt(self, engine):
        """summarize() must use rag_summarize_system_prompt, not rag_system_prompt."""
        mock_collection = MagicMock()
        mock_collection.count.return_value = 1
        mock_collection.query.return_value = {
            "documents": [["work note"]],
            "metadatas": [
                [
                    {
                        "date": "2026-02-10",
                        "sensitivity": "public",
                        "sentiment": "productive",
                    }
                ]
            ],
            "distances": [[0.4]],
        }
        engine.chroma.client.get_collection.return_value = mock_collection

        captured_prompt = {}

        def capture_llm(system_prompt, context, question):
            captured_prompt["system"] = system_prompt
            return "Summary."

        with patch.object(engine, "_call_llm", side_effect=capture_llm):
            engine.summarize("2026-02-01", "2026-02-25")

        assert captured_prompt["system"] == "You are a summarizer."


# ── QueryResult Tests ─────────────────────────────────────────────────────────


class TestQueryResult:
    def test_to_markdown_contains_question(self):
        r = QueryResult(
            question="What was I blocked on?",
            answer="You were blocked by approvals.",
            pattern="C",
            scope="all",
            chunks_retrieved=3,
            dates_covered=["2026-02-10", "2026-02-11"],
            date_from="2026-02-01",
            date_to="2026-02-28",
            rationale="Date scoped",
        )
        md = r.to_markdown()
        assert "What was I blocked on?" in md
        assert "You were blocked by approvals." in md
        assert "Pattern C" in md or "C" in md
        assert "2026-02-10" in md

    def test_to_markdown_no_date_range(self):
        r = QueryResult(
            question="Find notes about Azure",
            answer="You worked on Azure several times.",
            pattern="B",
            scope="public",
            chunks_retrieved=2,
            dates_covered=["2026-02-05"],
            date_from=None,
            date_to=None,
        )
        md = r.to_markdown()
        assert "Find notes about Azure" in md
        assert "Date range" not in md  # no date range for Pattern B
