"""
cli.py — Brain CLI entrypoint.

Built with Typer. Exposes clean, memorable commands:
  brain ingest    — ingest a daily markdown summary
  brain compose   — combine input files into a structured summary
  brain remind    — manage the daily reminder system
  brain query     — semantic query across your brain (Phase 3 ✅)
  brain trend     — structured trend queries via SQLite (Phase 2)
  brain summarize — summarize a date range (Phase 3 ✅)
  brain status    — show store stats and config
  brain dashboard — launch the Streamlit dashboard

Every output command supports --export to write a dated markdown file.
"""

from pathlib import Path
from typing import Annotated

import typer

from brain.composer import Composer
from brain.config import BrainConfig
from brain.exporter import Exporter
from brain.ingest import IngestPipeline
from brain.query import RAGEngine
from brain.reminder import Reminder
from brain.trend import TrendEngine, TrendFormatter

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
        raise typer.Exit(code=1) from e


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
            "--file",
            "-f",
            help="Path to the daily markdown summary to ingest.",
            exists=True,
            readable=True,
        ),
    ],
    sensitivity: Annotated[
        str,
        typer.Option(
            "--sensitivity",
            "-s",
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
            f"\n❌ Invalid sensitivity '{sensitivity}'. Use 'public' or 'sensitive'.",
            err=True,
        )
        raise typer.Exit(code=1)

    cfg = _get_config()

    try:
        pipeline = IngestPipeline(cfg)
        metadata = pipeline.route(file_path=file, sensitivity=sensitivity)

    except FileNotFoundError as e:
        typer.echo(f"\n❌ File not found: {e}", err=True)
        raise typer.Exit(code=1) from e

    except ValueError as e:
        typer.echo(f"\n❌ Ingestion error: {e}", err=True)
        raise typer.Exit(code=1) from e

    except Exception as e:
        typer.echo(f"\n❌ Unexpected error during ingestion: {e}", err=True)
        raise typer.Exit(code=1) from e

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
def compose(
    date: Annotated[
        str,
        typer.Option(
            "--date",
            "-d",
            help="Date to compose summary for (YYYY-MM-DD).",
        ),
    ],
    ingest: Annotated[
        bool,
        typer.Option(
            "--ingest",
            help="Automatically ingest the composed summary after writing.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Preview LLM output without writing or archiving files.",
        ),
    ] = False,
) -> None:
    """
    **Compose** a structured daily summary from input files.

    Reads all files from inputs/YYYY-MM-DD/, combines them via local LLM,
    writes to daily/sensitive/YYYY-MM-DD.md, extracts action items to todo.txt,
    and archives inputs.

    Examples:

        brain compose --date 2026-02-24

        brain compose --date 2026-02-24 --ingest

        brain compose --date 2026-02-24 --dry-run
    """
    _print_header("Daily Summary Composer")

    cfg = _get_config()

    try:
        composer = Composer(cfg)
        output_path = composer.compose(date=date, dry_run=dry_run)

    except FileNotFoundError as e:
        typer.echo(f"\n❌ {e}", err=True)
        raise typer.Exit(code=1) from e

    except ValueError as e:
        typer.echo(f"\n❌ {e}", err=True)
        raise typer.Exit(code=1) from e

    except Exception as e:
        typer.echo(f"\n❌ Unexpected error during compose: {e}", err=True)
        raise typer.Exit(code=1) from e

    # ── Optional auto-ingest ──────────────────────────────────────────────────
    if ingest and not dry_run:
        typer.echo("\n🔄 Auto-ingesting composed summary...")
        try:
            pipeline = IngestPipeline(cfg)
            pipeline.route(file_path=output_path, sensitivity="sensitive")
        except Exception as e:
            typer.echo(f"\n⚠️  Ingest failed after compose: {e}", err=True)
            typer.echo(
                f"   Run manually: brain ingest --file {output_path} --sensitivity sensitive",
                err=True,
            )
            raise typer.Exit(code=1) from e

    typer.echo("\n✅ Done.\n")


@app.command()
def remind(
    install: Annotated[
        bool,
        typer.Option(
            "--install",
            help="Generate launchd plist and install the daily reminder schedule.",
        ),
    ] = False,
    uninstall: Annotated[
        bool,
        typer.Option(
            "--uninstall",
            help="Unload and remove the launchd reminder schedule.",
        ),
    ] = False,
    status: Annotated[
        bool,
        typer.Option(
            "--status",
            help="Show reminder schedule and recent reminder files.",
        ),
    ] = False,
) -> None:
    """
    **Remind** — manage the daily Brain reminder system.

    Without flags, sends a reminder right now (useful for testing).

    Examples:

        brain remind

        brain remind --install

        brain remind --uninstall

        brain remind --status
    """
    _print_header("Daily Reminder")

    cfg = _get_config()

    try:
        reminder = Reminder(cfg)

        if install:
            reminder.install()
        elif uninstall:
            reminder.uninstall()
        elif status:
            reminder.status()
        else:
            # Default — send reminder now (test mode)
            reminder.send()

    except FileNotFoundError as e:
        typer.echo(f"\n❌ {e}", err=True)
        raise typer.Exit(code=1) from e

    except RuntimeError as e:
        typer.echo(f"\n❌ {e}", err=True)
        raise typer.Exit(code=1) from e

    except Exception as e:
        typer.echo(f"\n❌ Unexpected error: {e}", err=True)
        raise typer.Exit(code=1) from e

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

    Performs semantic search across your daily summaries using RAG
    (Retrieval Augmented Generation) with a local Ollama/Mistral LLM.

    The router automatically picks the best retrieval strategy:

    - **Pattern A** — structured/count questions → SQLite only (instant)
    - **Pattern B** — semantic questions → ChromaDB full scan
    - **Pattern C** — date-scoped questions → ChromaDB filtered by date
    - **Pattern D** — analytical/pattern questions → ChromaDB + SQLite

    Examples:

        brain query "What was I blocked on last month?"

        brain query "Find notes about feeling overwhelmed" --scope sensitive

        brain query "How many days did I work on the Brain project?" --export

        brain query "What patterns do you see in my blockers?"
    """
    _print_header("Semantic Query")
    typer.echo(f"   Question: {question}")
    typer.echo(f"   Scope   : {scope}")

    # ── Validate scope ────────────────────────────────────────────────────────
    if scope not in ("public", "sensitive", "all"):
        typer.echo(
            f"\n❌ Invalid scope '{scope}'. Use 'public', 'sensitive', or 'all'.",
            err=True,
        )
        raise typer.Exit(code=1)

    cfg = _get_config()

    try:
        engine = RAGEngine(cfg)
        result = engine.answer(question=question, scope=scope)

    except ValueError as e:
        typer.echo(f"\n❌ Query error: {e}", err=True)
        raise typer.Exit(code=1) from e

    except Exception as e:
        typer.echo(f"\n❌ Unexpected error during query: {e}", err=True)
        raise typer.Exit(code=1) from e

    # ── Render answer ─────────────────────────────────────────────────────────
    typer.echo()
    typer.echo(result.answer)
    typer.echo()

    # ── Query metadata footer ─────────────────────────────────────────────────
    typer.echo("─" * 40)
    typer.echo(f"  Pattern : {result.pattern}  |  Scope: {result.scope}")
    if result.dates_covered:
        typer.echo(f"  Dates   : {', '.join(result.dates_covered)}")
    if result.chunks_retrieved:
        typer.echo(f"  Chunks  : {result.chunks_retrieved} retrieved")
    typer.echo("─" * 40)

    if export:
        # Determine sensitivity from scope for export routing
        sensitivity = scope if scope in ("public", "sensitive") else "public"
        exporter = Exporter(cfg)
        _handle_export(
            content=result.to_markdown(),
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
        str | None,
        typer.Option(
            "--from",
            help="Start date inclusive (YYYY-MM-DD).",
        ),
    ] = None,
    date_to: Annotated[
        str | None,
        typer.Option(
            "--to",
            help="End date inclusive (YYYY-MM-DD). Defaults to today.",
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

    Examples:

        brain trend --field projects

        brain trend --field sentiment --from 2026-02-01

        brain trend --field blockers --from 2026-01-01 --to 2026-01-31 --sensitivity sensitive

        brain trend --field focus_areas --export
    """
    _print_header("Trend Analysis")

    # ── Validate field ────────────────────────────────────────────────────────
    valid_fields = ["projects", "blockers", "sentiment", "focus_areas", "people"]
    if field not in valid_fields:
        typer.echo(
            f"\n❌ Invalid field '{field}'. Choose from: {', '.join(valid_fields)}",
            err=True,
        )
        raise typer.Exit(code=1)

    # ── Validate sensitivity ──────────────────────────────────────────────────
    if sensitivity not in ("public", "sensitive", "all"):
        typer.echo(
            f"\n❌ Invalid sensitivity '{sensitivity}'. Use 'public', 'sensitive', or 'all'.",
            err=True,
        )
        raise typer.Exit(code=1)

    # ── Default date_to to today ──────────────────────────────────────────────
    from datetime import date as date_type

    date_to = date_to or date_type.today().isoformat()

    typer.echo(f"   Field      : {field}")
    typer.echo(f"   From       : {date_from or 'beginning'}")
    typer.echo(f"   To         : {date_to}")
    typer.echo(f"   Sensitivity: {sensitivity}")
    typer.echo()

    cfg = _get_config()

    try:
        engine = TrendEngine(cfg)
        result = engine.query(
            field=field,
            date_from=date_from,
            date_to=date_to,
            sensitivity=sensitivity,
        )

    except ValueError as e:
        typer.echo(f"\n❌ {e}", err=True)
        raise typer.Exit(code=1) from e

    except Exception as e:
        typer.echo(f"\n❌ Unexpected error during trend query: {e}", err=True)
        raise typer.Exit(code=1) from e

    # ── Format and display ────────────────────────────────────────────────────
    formatter = TrendFormatter()
    output = formatter.format(result)
    typer.echo(output)

    # ── Optional export ───────────────────────────────────────────────────────
    if export:
        export_sensitivity = (
            "public" if sensitivity in ("all", "public") else "sensitive"
        )
        exporter = Exporter(cfg)
        _handle_export(
            content=output,
            command="trend",
            sensitivity=export_sensitivity,
            exporter=exporter,
            title=f"Trend: {field}",
            date_from=date_from,
            date_to=date_to,
        )

    typer.echo("\n✅ Done.\n")


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
    **Summarize** your activity across a date range using RAG.

    Retrieves relevant chunks from ChromaDB for the date range and
    synthesizes a structured markdown summary via Ollama/Mistral.

    Examples:

        brain summarize --from 2026-02-01 --to 2026-02-20

        brain summarize --from 2026-01-01 --to 2026-01-31 --scope public --export

        brain summarize --from 2026-02-01 --to 2026-02-25 --scope sensitive
    """
    _print_header("Date Range Summary")
    typer.echo(f"   From : {date_from}")
    typer.echo(f"   To   : {date_to}")
    typer.echo(f"   Scope: {scope}")

    # ── Validate scope ────────────────────────────────────────────────────────
    if scope not in ("public", "sensitive", "all"):
        typer.echo(
            f"\n❌ Invalid scope '{scope}'. Use 'public', 'sensitive', or 'all'.",
            err=True,
        )
        raise typer.Exit(code=1)

    # ── Basic date format validation ──────────────────────────────────────────
    # Note: RAGEngine.summarize() also validates this — the check here is just
    # for fast CLI feedback before any LLM/DB calls are made.
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
    except ValueError as e:
        if "time data" in str(e):  # strptime format error
            typer.echo(
                "\n❌ Invalid date format. Use YYYY-MM-DD.",
                err=True,
            )
            raise typer.Exit(code=1) from e
        raise

    cfg = _get_config()

    try:
        engine = RAGEngine(cfg)
        result = engine.summarize(date_from=date_from, date_to=date_to, scope=scope)

    except ValueError as e:
        typer.echo(f"\n❌ Summarize error: {e}", err=True)
        raise typer.Exit(code=1) from e

    except Exception as e:
        typer.echo(f"\n❌ Unexpected error during summarize: {e}", err=True)
        raise typer.Exit(code=1) from e

    # ── Render answer ─────────────────────────────────────────────────────────
    typer.echo()
    typer.echo(result.answer)
    typer.echo()

    # ── Summary metadata footer ───────────────────────────────────────────────
    typer.echo("─" * 40)
    typer.echo(f"  Pattern : {result.pattern}  |  Scope: {result.scope}")
    if result.dates_covered:
        typer.echo(f"  Dates   : {', '.join(result.dates_covered)}")
    if result.chunks_retrieved:
        typer.echo(f"  Chunks  : {result.chunks_retrieved} retrieved")
    typer.echo("─" * 40)

    # ── Optional export ───────────────────────────────────────────────────────
    if export:
        sensitivity = scope if scope in ("public", "sensitive") else "public"
        exporter = Exporter(cfg)
        _handle_export(
            content=result.to_markdown(),
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
def status(
    detailed: Annotated[
        bool,
        typer.Option(
            "--detailed",
            "-d",
            help="Show per-date disk vs store breakdown and missing ingest commands.",
        ),
    ] = False,
) -> None:
    """
    **Status** — show current Brain configuration and store stats.

    Use **--detailed** to see a per-date breakdown of what's on disk
    vs what's ingested, with ready-to-run commands for any gaps.

    Examples:

        brain status

        brain status --detailed
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

    # ── Phase 3 query settings ────────────────────────────────────────────────
    typer.echo("\n🔍 Query Settings")
    typer.echo(f"   Top-K chunks      : {cfg.rag_top_k}")
    typer.echo(f"   Max context chunks: {cfg.rag_max_context_chunks}")
    typer.echo(f"   Similarity cutoff : {cfg.rag_similarity_threshold}")
    typer.echo(f"   Default scope     : {cfg.rag_default_scope}")
    typer.echo(f"   Max summarize days: {cfg.rag_max_summarize_days}")

    # ── SQLite stats ──────────────────────────────────────────────────────────
    typer.echo("\n🗃️  SQLite Store")
    if cfg.sqlite_db.exists():
        try:
            conn = sqlite3.connect(cfg.sqlite_db)
            total = conn.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0]
            public_count = conn.execute(
                "SELECT COUNT(*) FROM daily_summaries WHERE sensitivity='public'"
            ).fetchone()[0]
            sensitive_count = conn.execute(
                "SELECT COUNT(*) FROM daily_summaries WHERE sensitivity='sensitive'"
            ).fetchone()[0]
            latest = conn.execute("SELECT MAX(date) FROM daily_summaries").fetchone()[0]
            earliest = conn.execute("SELECT MIN(date) FROM daily_summaries").fetchone()[
                0
            ]
            conn.close()
            typer.echo(f"   Total entries  : {total}")
            typer.echo(f"   Public entries : {public_count}")
            typer.echo(f"   Sensitive      : {sensitive_count}")
            typer.echo(f"   Range          : {earliest or 'none'} → {latest or 'none'}")
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
            for name in (
                cfg.public_collection,
                cfg.sensitive_collection,
                cfg.blog_public_collection,
                cfg.blog_sensitive_collection,
            ):
                try:
                    col = client.get_collection(name)
                    typer.echo(f"   {name}: {col.count()} chunks")
                except Exception:
                    typer.echo(f"   {name}: not yet created")
        except Exception as e:
            typer.echo(f"   ⚠️  Could not read ChromaDB: {e}")
    else:
        typer.echo("   No vector store found — run 'brain ingest' first.")

    # ── Detailed per-date breakdown ───────────────────────────────────────────
    if detailed:
        typer.echo("\n📅  Per-Date Breakdown (disk vs store)")
        typer.echo(f"   {'DATE':<14} {'ON DISK':<24} {'INGESTED':<24} STATUS")
        typer.echo("   " + "─" * 72)

        try:
            # What's in SQLite
            conn = sqlite3.connect(cfg.sqlite_db)
            rows = conn.execute(
                "SELECT date, sensitivity FROM daily_summaries ORDER BY date DESC"
            ).fetchall()
            conn.close()

            ingested: dict[str, set] = {}
            for d, s in rows:
                ingested.setdefault(d, set()).add(s)

            # What's on disk — scan daily/ directories only
            on_disk: dict[str, set] = {}
            for f in cfg.daily_public_path.glob("*.md"):
                on_disk.setdefault(f.stem, set()).add("public")
            for f in cfg.daily_sensitive_path.glob("*.md"):
                on_disk.setdefault(f.stem, set()).add("sensitive")

            all_dates = sorted(set(ingested) | set(on_disk), reverse=True)
            needs_ingest: list[tuple[str, set]] = []

            for d in all_dates:
                disk = on_disk.get(d, set())
                store = ingested.get(d, set())
                todo = disk - store  # on disk but not yet ingested

                disk_str = " + ".join(sorted(disk)) if disk else "—"
                store_str = " + ".join(sorted(store)) if store else "—"

                if todo:
                    status_str = "⚠️  needs ingest"
                    needs_ingest.append((d, todo))
                elif disk and disk == store:
                    status_str = "✅ up to date"
                elif not disk and store:
                    status_str = "ℹ️  ingested (file removed)"
                else:
                    status_str = "—"

                typer.echo(f"   {d:<14} {disk_str:<24} {store_str:<24} {status_str}")

            # ── Commands for gaps ─────────────────────────────────────────────
            if needs_ingest:
                typer.echo(
                    f"\n   ⚠️  {sum(len(s) for _, s in needs_ingest)} file(s) on disk not yet ingested."
                )
                typer.echo("   Run these commands to catch up:\n")
                for d, sensitivities in sorted(needs_ingest):
                    for s in sorted(sensitivities):
                        typer.echo(
                            f"   brain ingest --file daily/{s}/{d}.md --sensitivity {s}"
                        )
                typer.echo()
                typer.echo("   Or ingest everything at once:")
                typer.echo("   uv run python scripts/reindex.py")
            else:
                typer.echo("\n   ✅ Everything on disk is ingested.")

        except Exception as e:
            typer.echo(f"   ⚠️  Could not build breakdown: {e}")

    typer.echo()


# ── Private Helpers ───────────────────────────────────────────────────────────


@app.command()
def dashboard(
    port: Annotated[
        int,
        typer.Option(
            "--port",
            "-p",
            help="Port to run the dashboard on.",
        ),
    ] = 8501,
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Don't open a browser window automatically.",
        ),
    ] = False,
) -> None:
    """
    **Dashboard** — launch the Streamlit browser dashboard.

    Opens the Brain dashboard in your default browser.
    Runs on localhost only — no external access.

    Examples:

        brain dashboard

        brain dashboard --port 8502

        brain dashboard --no-browser
    """
    import subprocess
    import sys
    from pathlib import Path

    _print_header("Brain Dashboard")

    dashboard_path = Path(__file__).parent.parent.parent / "dashboard.py"

    if not dashboard_path.exists():
        typer.echo(f"\n❌ dashboard.py not found at: {dashboard_path}", err=True)
        typer.echo(
            "   Make sure dashboard.py is in the project root directory.",
            err=True,
        )
        raise typer.Exit(code=1)

    args = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(dashboard_path),
        "--server.port",
        str(port),
        "--server.address",
        "localhost",
    ]

    if no_browser:
        args += ["--server.headless", "true"]

    typer.echo(f"\n   Dashboard : {dashboard_path.name}")
    typer.echo(f"   URL       : http://localhost:{port}")
    typer.echo(
        f"   Browser   : {'no' if no_browser else 'yes — opening automatically'}"
    )
    typer.echo()
    typer.echo("   Press Ctrl+C to stop.\n")

    try:
        subprocess.run(args, check=True)
    except KeyboardInterrupt:
        typer.echo("\n\n   Dashboard stopped.")
    except FileNotFoundError:
        typer.echo(
            "\n❌ Streamlit not found. Install it first:\n   uv add streamlit plotly",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except subprocess.CalledProcessError as e:
        typer.echo(f"\n❌ Dashboard failed: {e}", err=True)
        raise typer.Exit(code=1) from e


def _build_ingest_report(metadata: dict) -> str:
    """Build a markdown ingestion report from metadata."""
    projects = "\n".join(f"- {p}" for p in metadata["projects"]) or "- none"
    blockers = "\n".join(f"- {b}" for b in metadata["blockers"]) or "- none"
    people = "\n".join(f"- {p}" for p in metadata["people"]) or "- none"
    focus = "\n".join(f"- {f}" for f in metadata["focus_areas"]) or "- none"

    return f"""\
## Ingestion Report — {metadata["date"]}

| Field | Value |
|---|---|
| Date | {metadata["date"]} |
| Sensitivity | {metadata["sensitivity"]} |
| Sentiment | {metadata["sentiment"]} |
| Source | {metadata["raw_file"]} |

### Projects
{projects}

### Blockers
{blockers}

### People / Teams
{people}

### Focus Areas
{focus}
"""


# ── Anonymize command ─────────────────────────────────────────────────────────


@app.command()
def anonymize(
    file: Annotated[
        Path,
        typer.Argument(
            help="Path to the file to anonymize.",
        ),
    ],
    diff: Annotated[
        bool,
        typer.Option(
            "--diff",
            help="Show which lines were changed (printed to stderr).",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write anonymized content to file instead of stdout.",
        ),
    ] = None,
) -> None:
    """
    **Anonymize** a file — redact sensitive data for safe sharing or debugging.

    Applies all rules from config.yaml: people names, internal URLs, AWS IDs,
    secrets, IPs, hostnames, and local paths.

    Output goes to stdout by default (pipe-friendly). Use --output to write
    to a file instead.

    Examples:

        brain anonymize daily/sensitive/2026-02-24.md

        brain anonymize daily/sensitive/2026-02-24.md | pbcopy

        brain anonymize daily/sensitive/2026-02-24.md --diff

        brain anonymize daily/sensitive/2026-02-24.md --output /tmp/safe.md
    """
    import sys

    from brain.anonymizer import Anonymizer, print_diff, print_stats

    _print_header("Anonymize")

    if not file.exists():
        typer.echo(f"\n❌ File not found: {file}", err=True)
        raise typer.Exit(code=1)

    supported = {".md", ".txt", ".log", ".py", ".json"}
    if file.suffix not in supported:
        typer.echo(
            f"\n⚠️  Unexpected file type: {file.suffix} — proceeding anyway", err=True
        )

    cfg = _get_config()

    try:
        original = file.read_text(encoding="utf-8")
        anonymizer = Anonymizer(cfg)
        anonymized = anonymizer.anonymize(original)

        stats = anonymizer.redaction_stats(original, anonymized)
        print_stats(stats)

        if diff:
            print_diff(original, anonymized)

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(anonymized, encoding="utf-8")
            typer.echo(f"\n📄 Written to: {output}")
        else:
            # Write to stdout — typer.echo adds a newline, use sys.stdout directly
            sys.stdout.write(anonymized)

    except Exception as e:
        typer.echo(f"\n❌ Anonymization failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo("\n✅ Done.\n", err=True)


# ── Todo command ──────────────────────────────────────────────────────────────


@app.command()
def todo(
    list_todos: Annotated[
        bool,
        typer.Option("--list", help="List open todos."),
    ] = False,
    all_todos: Annotated[
        bool,
        typer.Option("--all", help="Include completed todos when listing."),
    ] = False,
    close: Annotated[
        str | None,
        typer.Option(
            "--close",
            help="Close a todo by its id: tag value (e.g. --close a3f291).",
        ),
    ] = None,
    suggest: Annotated[
        bool,
        typer.Option(
            "--suggest",
            help="Run matcher against most recent sensitive summary.",
        ),
    ] = False,
    include_medium: Annotated[
        bool,
        typer.Option(
            "--medium",
            help="Include medium-confidence matches in suggestions.",
        ),
    ] = False,
) -> None:
    """
    **Todo** — manage and review your action items.

    Run after compose to check suggestions, or use standalone to list
    and close items.

    Examples:

        brain todo --list

        brain todo --list --all

        brain todo --close a3f291

        brain todo --suggest

        brain todo --suggest --medium
    """
    from datetime import date as date_type

    from brain.todo_matcher import TodoMatcher

    _print_header("Todo")
    cfg = _get_config()

    try:
        matcher = TodoMatcher(cfg)

        # ── --close ───────────────────────────────────────────────────────────
        if close:
            today = date_type.today().isoformat()
            found = matcher.close_todo(close, today)
            if found:
                typer.echo(f"\n✅ Closed todo id:{close} (marked x {today})")
            else:
                typer.echo(
                    f"\n❌ No open todo found with id:{close}\n"
                    f"   Run 'brain todo --list' to see open items.",
                    err=True,
                )
                raise typer.Exit(code=1)

        # ── --list ────────────────────────────────────────────────────────────
        elif list_todos:
            open_lines, done_lines = matcher.list_all()

            typer.echo(f"\n📋 Open todos ({len(open_lines)})")
            typer.echo("─" * 40)
            if open_lines:
                for line in open_lines:
                    typer.echo(f"  {line}")
            else:
                typer.echo("  (none)")

            if all_todos and done_lines:
                typer.echo(f"\n✅ Completed ({len(done_lines)})")
                typer.echo("─" * 40)
                for line in done_lines:
                    typer.echo(f"  {line}")

        # ── --suggest ─────────────────────────────────────────────────────────
        elif suggest:
            # Find most recent sensitive summary
            sensitive_dir = cfg.daily_sensitive
            if not sensitive_dir.exists():
                typer.echo(
                    "\n❌ No sensitive summaries found.\n"
                    "   Run brain compose --date YYYY-MM-DD first.",
                    err=True,
                )
                raise typer.Exit(code=1)

            summaries = sorted(sensitive_dir.glob("*.md"), reverse=True)
            if not summaries:
                typer.echo(
                    "\n❌ No summaries found in daily/sensitive/\n"
                    "   Run brain compose --date YYYY-MM-DD first.",
                    err=True,
                )
                raise typer.Exit(code=1)

            latest = summaries[0]
            # Extract date from filename YYYY-MM-DD.md
            date_str = latest.stem

            typer.echo(f"\n   Summary : {latest.name}")
            typer.echo(f"   Open    : {len(matcher.list_open())} todo(s)")
            typer.echo()

            open_count = len(matcher.list_open())
            if open_count == 0:
                typer.echo("   No open todos — nothing to check.")
            else:
                typer.echo(f"   Scanning {open_count} open todo(s)...\n")
                content = latest.read_text(encoding="utf-8")
                matches = matcher.match(content, date_str, include_medium)

                if not matches:
                    typer.echo("   No open todos appear addressed in this summary.")
                else:
                    typer.echo(f"   {len(matches)} item(s) may have been addressed:\n")
                    for m in matches:
                        conf_label = "●" if m.confidence == "high" else "○"
                        typer.echo(f"   {conf_label} {m.raw_line}")
                        typer.echo(f"     ↳ {m.evidence}")
                        typer.echo()

                    typer.echo("   To close:")
                    typer.echo("     brain todo --close <id>")
                    if matches[0].todo_id:
                        typer.echo(f"     e.g. brain todo --close {matches[0].todo_id}")

        # ── no flag — show help hint ──────────────────────────────────────────
        else:
            open_lines = matcher.list_open()
            typer.echo(f"\n   {len(open_lines)} open todo(s) in {cfg.todo_txt.name}")
            typer.echo()
            typer.echo("   brain todo --list          show open todos")
            typer.echo("   brain todo --list --all    include completed")
            typer.echo("   brain todo --suggest       check against latest summary")
            typer.echo("   brain todo --close <id>    mark item done")

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"\n❌ Unexpected error: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo("\n✅ Done.\n")


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
