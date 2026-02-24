"""
cli.py — Brain CLI entrypoint.

Built with Typer. Exposes clean, memorable commands:
  brain ingest    — ingest a daily markdown summary
  brain query     — semantic query across your brain (Phase 3)
  brain trend     — structured trend queries via SQLite (Phase 2)
  brain summarize — summarize a date range (Phase 3)

Every output command supports --export to write a dated markdown file.
"""

from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer

from brain.config import BrainConfig
from brain.exporter import Exporter
from brain.ingest import IngestPipeline

# ── App Setup ─────────────────────────────────────────────────────────────────

app = typer.Typer(
    name="brain",
    help="🧠 Brain — your local, private, queryable second brain.",
    add_completion=False,
    rich_markup_mode="markdown",
)

# ── Shared Helpers ────────────────────────────────────────────────────────────


def _get_config() -> BrainConfig:
    """Load config — fail fast with a clean message if misconfigured."""
    try:
        return BrainConfig()
    except Exception as e:
        typer.echo(f"\n❌ Config error: {e}", err=True)
        raise typer.Exit(code=1)


def _print_header(command: str) -> None:
    """Print consistent command header."""
    typer.echo(f"\n🧠 Brain — {command}")
    typer.echo("─" * 40)


def _print_export_confirmation(path: Path) -> None:
    """Print export path confirmation."""
    typer.echo(f"\n📄 Exported to: {path}")


def _handle_export(
    content: str,
    command: str,
    sensitivity: str,
    exporter: Exporter,
    query: str | None = None,
    title: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    scope: str = "all",
) -> None:
    """Shared export handler used by all commands."""
    try:
        path = exporter.export(
            content=content,
            command=command,
            sensitivity=sensitivity,
            query=query,
            title=title,
            date_from=date_from,
            date_to=date_to,
            scope=scope,
        )
        _print_export_confirmation(path)
    except Exception as e:
        typer.echo(f"\n⚠️  Export failed: {e}", err=True)


# ── Commands ──────────────────────────────────────────────────────────────────

@app.command()
def ingest(
    file: Annotated[
        Path,
        typer.Option(
            "--file", "-f",
            help="Path to the daily markdown summary to ingest.",
            exists=True,
            readable=True,
        ),
    ],
    sensitivity: Annotated[
        str,
        typer.Option(
            "--sensitivity", "-s",
            help="Sensitivity level: 'public' or 'sensitive'.",
        ),
    ] = "public",
    export: Annotated[
        bool,
        typer.Option(
            "--export",
            help="Export ingestion report to markdown file.",
        ),
    ] = False,
) -> None:
    """
    **Ingest** a daily markdown summary into your brain.

    Extracts metadata via local LLM, stores in SQLite + ChromaDB.

    Examples:

        brain ingest --file daily/public/2026-02-20.md --sensitivity public

        brain ingest --file daily/sensitive/2026-02-20.md --sensitivity sensitive
    """
    _print_header("Ingesting daily summary")

    # ── Validate sensitivity early ────────────────────────────────────────────
    if sensitivity not in ("public", "sensitive"):
        typer.echo(
            f"\n❌ Invalid sensitivity '{sensitivity}'. "
            f"Use 'public' or 'sensitive'.",
            err=True,
        )
        raise typer.Exit(code=1)

    cfg = _get_config()

    try:
        pipeline = IngestPipeline(cfg)
        metadata = pipeline.run(file_path=file, sensitivity=sensitivity)

    except FileNotFoundError as e:
        typer.echo(f"\n❌ File not found: {e}", err=True)
        raise typer.Exit(code=1)

    except ValueError as e:
        typer.echo(f"\n❌ Ingestion error: {e}", err=True)
        raise typer.Exit(code=1)

    except Exception as e:
        typer.echo(f"\n❌ Unexpected error during ingestion: {e}", err=True)
        raise typer.Exit(code=1)

    # ── Optional export of ingestion report ───────────────────────────────────
    if export:
        report = _build_ingest_report(metadata)
        exporter = Exporter(cfg)
        _handle_export(
            content=report,
            command="ingest",
            sensitivity=sensitivity,
            exporter=exporter,
            title=f"Ingestion Report — {metadata['date']}",
        )

    typer.echo("\n✅ Done.\n")


@app.command()
def query(
    question: Annotated[
        str,
        typer.Argument(help="The question to ask your brain."),
    ],
    scope: Annotated[
        str,
        typer.Option(
            "--scope",
            help="Search scope: 'public', 'sensitive', or 'all'.",
        ),
    ] = "all",
    export: Annotated[
        bool,
        typer.Option(
            "--export",
            help="Export query result to markdown file.",
        ),
    ] = False,
) -> None:
    """
    **Query** your brain with a natural language question.

    Performs semantic search across your daily summaries.

    *(Full RAG implementation in Phase 3 — stub active)*

    Examples:

        brain query "What was I blocked on last month?"

        brain query "Summarise my Azure research" --scope public --export
    """
    _print_header("Semantic Query")
    typer.echo(f"   Question: {question}")
    typer.echo(f"   Scope   : {scope}")
    typer.echo()

    # ── Phase 3 stub ──────────────────────────────────────────────────────────
    stub_response = (
        f"🚧 Semantic query engine coming in Phase 3.\n\n"
        f"**Your question:** {question}\n\n"
        f"**Scope:** {scope}\n\n"
        f"The RAG pipeline (ChromaDB + Ollama/Mistral) will answer "
        f"this question when Phase 3 is complete."
    )

    typer.echo(stub_response)

    if export:
        # Determine sensitivity from scope for export routing
        sensitivity = scope if scope in ("public", "sensitive") else "public"
        exporter = Exporter(_get_config())
        _handle_export(
            content=stub_response,
            command="query",
            sensitivity=sensitivity,
            exporter=exporter,
            query=question,
            scope=scope,
        )

    typer.echo()


@app.command()
def trend(
    field: Annotated[
        str,
        typer.Option(
            "--field",
            help="Metadata field to trend: projects, blockers, sentiment, focus_areas, people.",
        ),
    ],
    date_from: Annotated[
        Optional[str],
        typer.Option(
            "--from",
            help="Start date (YYYY-MM-DD).",
        ),
    ] = None,
    date_to: Annotated[
        Optional[str],
        typer.Option(
            "--to",
            help="End date (YYYY-MM-DD). Defaults to today.",
        ),
    ] = None,
    sensitivity: Annotated[
        str,
        typer.Option(
            "--sensitivity",
            help="Filter by sensitivity: 'public', 'sensitive', or 'all'.",
        ),
    ] = "all",
    export: Annotated[
        bool,
        typer.Option(
            "--export",
            help="Export trend result to markdown file.",
        ),
    ] = False,
) -> None:
    """
    **Trend** analysis on structured metadata fields via SQLite.

    *(Full implementation in Phase 2 — stub active)*

    Examples:

        brain trend --field projects --from 2026-01-01 --to 2026-01-31

        brain trend --field sentiment --from 2026-02-01 --export
    """
    _print_header("Trend Analysis")

    # ── Validate field ────────────────────────────────────────────────────────
    valid_fields = ["projects", "blockers", "sentiment", "focus_areas", "people"]
    if field not in valid_fields:
        typer.echo(
            f"\n❌ Invalid field '{field}'. "
            f"Choose from: {', '.join(valid_fields)}",
            err=True,
        )
        raise typer.Exit(code=1)

    # ── Default date_to to today ──────────────────────────────────────────────
    date_to = date_to or date.today().isoformat()

    typer.echo(f"   Field      : {field}")
    typer.echo(f"   From       : {date_from or 'beginning'}")
    typer.echo(f"   To         : {date_to}")
    typer.echo(f"   Sensitivity: {sensitivity}")
    typer.echo()

    # ── Phase 2 stub ──────────────────────────────────────────────────────────
    stub_response = (
        f"🚧 Trend analysis coming in Phase 2.\n\n"
        f"**Field:** {field}\n"
        f"**From:** {date_from or 'beginning'}\n"
        f"**To:** {date_to}\n"
        f"**Sensitivity:** {sensitivity}\n\n"
        f"SQLite aggregation queries will power this command in Phase 2."
    )

    typer.echo(stub_response)

    if export:
        export_sensitivity = "public" if sensitivity in ("all", "public") else "sensitive"
        exporter = Exporter(_get_config())
        _handle_export(
            content=stub_response,
            command="trend",
            sensitivity=export_sensitivity,
            exporter=exporter,
            title=f"Trend: {field}",
            date_from=date_from,
            date_to=date_to,
        )

    typer.echo()


@app.command()
def summarize(
    date_from: Annotated[
        str,
        typer.Option(
            "--from",
            help="Start date (YYYY-MM-DD).",
        ),
    ],
    date_to: Annotated[
        str,
        typer.Option(
            "--to",
            help="End date (YYYY-MM-DD).",
        ),
    ],
    scope: Annotated[
        str,
        typer.Option(
            "--scope",
            help="Search scope: 'public', 'sensitive', or 'all'.",
        ),
    ] = "all",
    export: Annotated[
        bool,
        typer.Option(
            "--export",
            help="Export summary to markdown file.",
        ),
    ] = False,
) -> None:
    """
    **Summarize** your activity across a date range.

    *(Full RAG implementation in Phase 3 — stub active)*

    Examples:

        brain summarize --from 2026-02-01 --to 2026-02-20

        brain summarize --from 2026-01-01 --to 2026-01-31 --scope public --export
    """
    _print_header("Date Range Summary")
    typer.echo(f"   From : {date_from}")
    typer.echo(f"   To   : {date_to}")
    typer.echo(f"   Scope: {scope}")
    typer.echo()

    # ── Basic date validation ─────────────────────────────────────────────────
    try:
        from datetime import datetime
        start = datetime.strptime(date_from, "%Y-%m-%d")
        end = datetime.strptime(date_to, "%Y-%m-%d")
        if end < start:
            typer.echo(
                "\n❌ --to date must be after --from date.",
                err=True,
            )
            raise typer.Exit(code=1)
    except ValueError:
        typer.echo(
            "\n❌ Invalid date format. Use YYYY-MM-DD.",
            err=True,
        )
        raise typer.Exit(code=1)

    # ── Phase 3 stub ──────────────────────────────────────────────────────────
    stub_response = (
        f"🚧 Summarization coming in Phase 3.\n\n"
        f"**Date Range:** {date_from} → {date_to}\n"
        f"**Scope:** {scope}\n\n"
        f"ChromaDB retrieval + Ollama/Mistral summarization "
        f"will power this command in Phase 3."
    )

    typer.echo(stub_response)

    if export:
        sensitivity = scope if scope in ("public", "sensitive") else "public"
        exporter = Exporter(_get_config())
        _handle_export(
            content=stub_response,
            command="summarize",
            sensitivity=sensitivity,
            exporter=exporter,
            title=f"Summary: {date_from} to {date_to}",
            date_from=date_from,
            date_to=date_to,
            scope=scope,
        )

    typer.echo()


# ── Utility Commands ──────────────────────────────────────────────────────────

@app.command()
def status() -> None:
    """
    **Status** — show current Brain configuration and store stats.

    Examples:

        brain status
    """
    import sqlite3
    import chromadb
    from chromadb.config import Settings

    _print_header("Brain Status")
    cfg = _get_config()

    # ── Config summary ────────────────────────────────────────────────────────
    typer.echo("\n📋 Configuration")
    typer.echo(f"   LLM model   : {cfg.ollama_llm_model}")
    typer.echo(f"   Embed model : {cfg.ollama_embed_model}")
    typer.echo(f"   Ollama URL  : {cfg.ollama_base_url}")

    # ── SQLite stats ──────────────────────────────────────────────────────────
    typer.echo("\n🗃️  SQLite Store")
    if cfg.sqlite_db.exists():
        try:
            conn = sqlite3.connect(cfg.sqlite_db)
            total = conn.execute(
                "SELECT COUNT(*) FROM daily_summaries"
            ).fetchone()[0]
            public = conn.execute(
                "SELECT COUNT(*) FROM daily_summaries WHERE sensitivity='public'"
            ).fetchone()[0]
            sensitive = conn.execute(
                "SELECT COUNT(*) FROM daily_summaries WHERE sensitivity='sensitive'"
            ).fetchone()[0]
            latest = conn.execute(
                "SELECT MAX(date) FROM daily_summaries"
            ).fetchone()[0]
            conn.close()
            typer.echo(f"   Total entries  : {total}")
            typer.echo(f"   Public entries : {public}")
            typer.echo(f"   Sensitive      : {sensitive}")
            typer.echo(f"   Latest entry   : {latest or 'none'}")
        except Exception as e:
            typer.echo(f"   ⚠️  Could not read SQLite: {e}")
    else:
        typer.echo("   No database found — run 'brain ingest' first.")

    # ── ChromaDB stats ────────────────────────────────────────────────────────
    typer.echo("\n🔮 ChromaDB Store")
    if cfg.chroma_store.exists():
        try:
            client = chromadb.PersistentClient(
                path=str(cfg.chroma_store),
                settings=Settings(anonymized_telemetry=False),
            )
            for name in (cfg.public_collection, cfg.sensitive_collection):
                try:
                    col = client.get_collection(name)
                    typer.echo(f"   {name}: {col.count()} chunks")
                except Exception:
                    typer.echo(f"   {name}: not yet created")
        except Exception as e:
            typer.echo(f"   ⚠️  Could not read ChromaDB: {e}")
    else:
        typer.echo("   No vector store found — run 'brain ingest' first.")

    typer.echo()


# ── Private Helpers ───────────────────────────────────────────────────────────

def _build_ingest_report(metadata: dict) -> str:
    """Build a markdown ingestion report from metadata."""
    projects = "\n".join(f"- {p}" for p in metadata["projects"]) or "- none"
    blockers = "\n".join(f"- {b}" for b in metadata["blockers"]) or "- none"
    people = "\n".join(f"- {p}" for p in metadata["people"]) or "- none"
    focus = "\n".join(f"- {f}" for f in metadata["focus_areas"]) or "- none"

    return f"""\
## Ingestion Report — {metadata['date']}

| Field | Value |
|---|---|
| Date | {metadata['date']} |
| Sensitivity | {metadata['sensitivity']} |
| Sentiment | {metadata['sentiment']} |
| Source | {metadata['raw_file']} |

### Projects
{projects}

### Blockers
{blockers}

### People / Teams
{people}

### Focus Areas
{focus}
"""


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
