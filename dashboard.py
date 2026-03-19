"""
dashboard.py — Brain Dashboard (Phase 4)

A local Streamlit dashboard for querying and exploring your second brain.
Runs entirely on localhost — no server, no auth, no telemetry.

Tabs:
  📅 Daily Entries  — browse ingested dates, read summaries side-by-side
  🚧 Blockers       — frequency chart + full timeline table
  ✅ Action Items   — todo.txt grouped by date and priority
  🔍 Query          — natural language RAG query interface   (Step 4)

Usage:
  uv run streamlit run dashboard.py
  brain dashboard                        # via CLI (Step 5)
"""

import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.resolve()
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from brain.config import BrainConfig  # noqa

# ── Page config — must be first Streamlit call ────────────────────────────────
st.set_page_config(
    page_title="🧠 Brain",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Theme + custom CSS ────────────────────────────────────────────────────────
st.markdown(
    """
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

  html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }

  .stApp { background-color: #ffffff; }

  section[data-testid="stSidebar"] {
    background-color: #f8fafc;
    border-right: 1px solid #e2e8f0;
  }

  /* Tab bar */
  .stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 2px solid #e2e8f0;
    background-color: #ffffff;
  }
  .stTabs [data-baseweb="tab"] {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.78rem;
    letter-spacing: 0.04em;
    color: #64748b;
    padding: 0.6rem 1.4rem;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    background: transparent;
  }
  .stTabs [aria-selected="true"] {
    color: #2563eb;
    border-bottom: 2px solid #2563eb;
    background: transparent;
  }

  /* Entry cards */
  .entry-date {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85rem;
    font-weight: 500;
    color: #0f172a;
  }
  .entry-badges { margin-top: 0.25rem; display: flex; gap: 0.3rem; }

  .badge {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.6rem;
    padding: 0.1rem 0.45rem;
    border-radius: 3px;
    font-weight: 500;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .badge-public    { background:#dcfce7; color:#15803d; border:1px solid #86efac; }
  .badge-sensitive { background:#fee2e2; color:#b91c1c; border:1px solid #fca5a5; }

  /* Panel headers */
  .panel-header {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    color: #94a3b8;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 0.8rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #e2e8f0;
  }

  /* Empty state */
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 35vh;
    color: #94a3b8;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85rem;
    text-align: center;
  }
  .empty-icon { font-size: 2rem; margin-bottom: 0.6rem; opacity: 0.5; }

  /* Streamlit component overrides */
  div[data-testid="stMetric"] {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 0.7rem 0.9rem;
  }
  div[data-testid="stMetricValue"] {
    font-family: 'IBM Plex Mono', monospace;
    color: #0f172a;
  }
  .stSelectbox label, .stDateInput label,
  .stRadio label, .stTextInput label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    color: #64748b;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  hr { border-color: #e2e8f0; }

  /* Blocker timeline table */
  /* ── todo.txt items ── */
  .todo-date-header {
    font-family:'IBM Plex Mono',monospace; font-size:0.78rem; font-weight:500;
    color:#1a1a2e; letter-spacing:0.05em; padding:0.8rem 0 0.4rem 0;
    border-bottom:2px solid #ddd8cc; margin-bottom:0.4rem;
    display:flex; align-items:center; gap:0.6rem;
  }
  .todo-item {
    display:flex; align-items:flex-start; gap:0.75rem;
    padding:0.5rem 0.6rem; border-radius:5px;
    margin-bottom:0.25rem; border:1px solid transparent;
    font-size:0.85rem; color:#1a1a2e; transition:background 0.1s;
  }
  .todo-item:hover { background:#f0f4ff; border-color:#ddd8cc; }
  .priority-badge {
    font-family:'IBM Plex Mono',monospace; font-size:0.72rem; font-weight:500;
    padding:0.15rem 0.45rem; border-radius:4px; white-space:nowrap;
    min-width:28px; text-align:center;
  }
  .priority-a { background:#fee2e2; color:#991b1b; border:1px solid #fca5a5; }
  .priority-b { background:#fef3c7; color:#92400e; border:1px solid #fcd34d; }
  .priority-c { background:#dcfce7; color:#166534; border:1px solid #86efac; }
  .todo-text { flex:1; line-height:1.45; }
  .todo-tags { margin-top:0.2rem; display:flex; flex-wrap:wrap; gap:0.3rem; }
  .tag-project {
    font-family:'IBM Plex Mono',monospace; font-size:0.65rem;
    background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe;
    padding:0.05rem 0.35rem; border-radius:3px;
  }
  .tag-context {
    font-family:'IBM Plex Mono',monospace; font-size:0.65rem;
    background:#f5f3ff; color:#5b21b6; border:1px solid #c4b5fd;
    padding:0.05rem 0.35rem; border-radius:3px;
  }
  .todo-summary-bar {
    display:flex; gap:1rem; margin-bottom:1.2rem;
    padding:0.8rem 1rem; background:#f8f6f1;
    border:1px solid #ddd8cc; border-radius:6px;
  }
  .todo-summary-item {
    font-family:'IBM Plex Mono',monospace; font-size:0.78rem; color:#6b7280;
  }
  .todo-summary-item span {
    font-weight:500; color:#1a1a2e; margin-right:0.3rem;
  }

  /* ── Query tab ── */
  .query-answer {
    background:#ffffff; border:1px solid #ddd8cc; border-radius:8px;
    padding:1.5rem 1.8rem; margin-top:1rem;
  }
  .query-meta-bar {
    display:flex; flex-wrap:wrap; gap:1rem; align-items:center;
    padding:0.6rem 1rem; background:#f8f6f1;
    border:1px solid #ddd8cc; border-radius:6px;
    margin-top:1rem; font-family:'IBM Plex Mono',monospace;
    font-size:0.72rem; color:#6b7280;
  }
  .query-meta-item { display:flex; gap:0.3rem; align-items:center; }
  .query-meta-item span { color:#1a1a2e; font-weight:500; }
  .pattern-badge {
    font-family:'IBM Plex Mono',monospace; font-size:0.72rem;
    font-weight:500; padding:0.15rem 0.5rem; border-radius:4px;
    background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe;
  }
  .query-history-item {
    padding:0.45rem 0.7rem; border-radius:5px; border:1px solid #ddd8cc;
    background:#ffffff; margin-bottom:0.3rem; cursor:pointer;
    font-size:0.82rem; color:#374151; display:flex;
    justify-content:space-between; align-items:center;
    transition:border-color 0.1s;
  }
  .query-history-item:hover { border-color:#2563eb; color:#1a1a2e; }
  .query-history-time {
    font-family:'IBM Plex Mono',monospace; font-size:0.68rem;
    color:#9ca3af; white-space:nowrap; margin-left:1rem;
  }

  .blocker-row {
    display: flex;
    align-items: flex-start;
    gap: 1rem;
    padding: 0.55rem 0.6rem;
    border-bottom: 1px solid #f1f5f9;
    font-size: 0.85rem;
  }
  .blocker-row:hover { background: #f8fafc; border-radius: 4px; }
  .blocker-date {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    color: #64748b;
    min-width: 6.5rem;
    padding-top: 0.05rem;
  }
  .blocker-text  { color: #0f172a; flex: 1; }
  .blocker-badge { min-width: 4rem; text-align: right; }
</style>
""",
    unsafe_allow_html=True,
)


# ── Config + data loaders ──────────────────────────────────────────────────────


@st.cache_resource
def get_config() -> BrainConfig:
    return BrainConfig()


@st.cache_data(ttl=60)
def load_all_entries() -> list[dict]:
    """All ingested entries from SQLite, grouped by date, newest first."""
    cfg = get_config()
    if not cfg.sqlite_db.exists():
        return []

    conn = sqlite3.connect(cfg.sqlite_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, sensitivity, raw_file, sentiment
        FROM daily_summaries
        ORDER BY date DESC
    """).fetchall()
    conn.close()

    by_date: dict[str, dict] = {}
    for row in rows:
        d = row["date"]
        if d not in by_date:
            by_date[d] = {
                "date": d,
                "sensitivities": [],
                "files": {},
                "sentiment": row["sentiment"],
            }
        by_date[d]["sensitivities"].append(row["sensitivity"])
        by_date[d]["files"][row["sensitivity"]] = row["raw_file"]

    return list(by_date.values())


@st.cache_data(ttl=60)
def load_blockers(
    date_from: str | None = None,
    date_to: str | None = None,
    sensitivity: str = "all",
) -> list[dict]:
    """
    Load all blocker entries from SQLite using json_each() to explode arrays.
    Returns list of {date, blocker, sensitivity} dicts, newest first.
    """
    cfg = get_config()
    if not cfg.sqlite_db.exists():
        return []

    conditions = ["json_each.value != ''", "json_each.value IS NOT NULL"]
    params: list = []

    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date <= ?")
        params.append(date_to)
    if sensitivity != "all":
        conditions.append("sensitivity = ?")
        params.append(sensitivity)

    where = " AND ".join(conditions)
    conn = sqlite3.connect(cfg.sqlite_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        SELECT date, sensitivity, json_each.value AS blocker
        FROM   daily_summaries, json_each(blockers)
        WHERE  {where}
        ORDER  BY date DESC
    """,
        params,
    ).fetchall()
    conn.close()

    return [
        {"date": r["date"], "blocker": r["blocker"], "sensitivity": r["sensitivity"]}
        for r in rows
    ]


@st.cache_data(ttl=60)
def load_sentiment_timeline(
    date_from: str | None = None,
    date_to: str | None = None,
    sensitivity: str = "all",
) -> list[dict]:
    """
    Load one sentiment row per day from SQLite.
    When both public + sensitive exist for the same date, sensitive wins
    (it has more context). Returns list of {date, sentiment} dicts, oldest first.
    """
    cfg = get_config()
    if not cfg.sqlite_db.exists():
        return []

    conditions: list[str] = []
    params: list = []

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

    conn = sqlite3.connect(cfg.sqlite_db)
    conn.row_factory = sqlite3.Row
    # MAX(sensitivity) — 'sensitive' > 'public' lexicographically, so sensitive wins
    rows = conn.execute(
        f"""
        SELECT date, MAX(sensitivity) AS sensitivity,
               MAX(CASE WHEN sensitivity = 'sensitive' THEN sentiment ELSE NULL END)
                 AS sens_sentiment,
               MAX(CASE WHEN sensitivity = 'public'    THEN sentiment ELSE NULL END)
                 AS pub_sentiment
        FROM daily_summaries
        {where}
        GROUP BY date
        ORDER BY date ASC
    """,
        params,
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        # Prefer sensitive sentiment; fall back to public
        sentiment = r["sens_sentiment"] or r["pub_sentiment"] or "neutral"
        result.append({"date": r["date"], "sentiment": sentiment})

    return result


def _init_query_history_table() -> None:
    """
    Create the query_history table if it doesn't exist.
    Called once at startup — idempotent.
    """
    cfg = get_config()
    cfg.sqlite_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.sqlite_db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            asked_at         TEXT    NOT NULL,
            question         TEXT    NOT NULL,
            scope            TEXT    NOT NULL DEFAULT 'all',
            pattern          TEXT,
            answer           TEXT    NOT NULL,
            chunks_retrieved INTEGER NOT NULL DEFAULT 0,
            dates_covered    TEXT    NOT NULL DEFAULT '[]',  -- JSON array
            rationale        TEXT    NOT NULL DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


def save_query_to_history(question: str, scope: str, result) -> None:
    """
    Persist a query result to SQLite query_history table.
    result is a QueryResult dataclass from brain.query.
    Clears the @st.cache_data for load_query_history so the UI
    reflects the new row immediately.
    """
    import json as _json

    cfg = get_config()
    conn = sqlite3.connect(cfg.sqlite_db)
    conn.execute(
        """
        INSERT INTO query_history
            (asked_at, question, scope, pattern, answer,
             chunks_retrieved, dates_covered, rationale)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            datetime.now().isoformat(),
            question,
            scope,
            getattr(result, "pattern", ""),
            getattr(result, "answer", ""),
            getattr(result, "chunks_retrieved", 0),
            _json.dumps(getattr(result, "dates_covered", [])),
            getattr(result, "rationale", ""),
        ),
    )
    conn.commit()
    conn.close()
    # Bust the cache so load_query_history returns the new row
    load_query_history.clear()


@st.cache_data(ttl=30)
def load_query_history(limit: int = 20) -> list[dict]:
    """
    Load the most recent query history rows from SQLite, newest first.
    Returns list of plain dicts (serialisation-safe for session state).
    """
    import json as _json

    cfg = get_config()
    if not cfg.sqlite_db.exists():
        return []
    try:
        conn = sqlite3.connect(cfg.sqlite_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, asked_at, question, scope, pattern,
                   answer, chunks_retrieved, dates_covered, rationale
            FROM   query_history
            ORDER  BY id DESC
            LIMIT  ?
        """,
            (limit,),
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        # Table doesn't exist yet — first run before any query
        return []

    return [
        {
            "id": r["id"],
            "asked_at": r["asked_at"],
            "question": r["question"],
            "scope": r["scope"],
            "pattern": r["pattern"] or "—",
            "answer": r["answer"],
            "chunks_retrieved": r["chunks_retrieved"],
            "dates_covered": _json.loads(r["dates_covered"]),
            "rationale": r["rationale"],
            # Friendly display time
            "time": r["asked_at"][11:16],  # HH:MM from ISO string
        }
        for r in rows
    ]


@st.cache_data(ttl=30)
def load_todos() -> list[dict]:
    """
    Parse todo.txt from project root.

    todo.txt format:
        # 2026-02-24              ← date separator comment
        (A) 2026-02-24 Fix X +Project @context
        (B) 2026-02-24 Do Y +AnotherProject

    Returns list of dicts:
        {
            "date":     "2026-02-24",
            "priority": "A",          # A | B | C | None
            "text":     "Fix X",      # description stripped of tags
            "projects": ["Project"],
            "contexts": ["context"],
            "raw":      "(A) 2026-02-24 Fix X +Project @context",
        }
    Skips blank lines and comment lines (# ...).
    """
    import re

    cfg = get_config()
    todo_path = cfg.todo_txt
    if not todo_path.exists():
        return []

    items: list[dict] = []
    current_date: str | None = None

    for line in todo_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue

        # Date separator comment — # YYYY-MM-DD
        if line.startswith("#"):
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", line)
            if date_match:
                current_date = date_match.group(1)
            continue

        # Priority — (A), (B), (C) or none
        priority_match = re.match(r"^\(([ABC])\)\s+", line)
        priority = priority_match.group(1) if priority_match else None

        # Strip priority prefix for tag parsing
        rest = re.sub(r"^\([ABC]\)\s+", "", line)

        # Strip inline date (YYYY-MM-DD) immediately after priority
        date_match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+", rest)
        item_date = current_date
        if date_match:
            item_date = date_match.group(1)
            rest = rest[date_match.end() :]

        # Extract +Project and @context tags
        projects = re.findall(r"\+(\S+)", rest)
        contexts = re.findall(r"@(\S+)", rest)

        # Clean text — remove tags
        text = re.sub(r"\+\S+", "", rest)
        text = re.sub(r"@\S+", "", text)
        text = re.sub(r"\s{2,}", " ", text).strip()

        if not text:
            continue

        items.append(
            {
                "date": item_date or "unknown",
                "priority": priority,
                "text": text,
                "projects": projects,
                "contexts": contexts,
                "raw": line,
            }
        )

    return items


def read_summary_file(raw_file: str) -> str | None:
    cfg = get_config()
    path = cfg._root / raw_file
    return path.read_text(encoding="utf-8") if path.exists() else None


def sentiment_emoji(sentiment: str) -> str:
    return {
        "productive": "🟢",
        "energized": "🔵",
        "neutral": "⚪",
        "frustrated": "🟡",
        "overwhelmed": "🔴",
    }.get(sentiment, "⚪")


# ── Session state ──────────────────────────────────────────────────────────────

if "selected_date" not in st.session_state:
    st.session_state.selected_date = None
if "selected_sensitivity" not in st.session_state:
    st.session_state.selected_sensitivity = "public"
if "query_result" not in st.session_state:
    st.session_state.query_result = None
if "query_error" not in st.session_state:
    st.session_state.query_error = None

# Init history table (idempotent) and pre-populate session state from DB
_init_query_history_table()
if "query_history" not in st.session_state:
    st.session_state.query_history = load_query_history(limit=20)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(
        """
    <div style="padding:0.4rem 0 1rem 0;">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:1.05rem;
                   font-weight:600;color:#0f172a;letter-spacing:0.04em;">
        🧠 brain
      </span><br>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:0.68rem;
                   color:#94a3b8;">local second brain</span>
    </div>
    <hr style="margin:0 0 1rem 0;">
    """,
        unsafe_allow_html=True,
    )

    entries = load_all_entries()
    st.metric("Total entries", len(entries))
    st.metric("Public", sum(1 for e in entries if "public" in e["sensitivities"]))
    st.metric("Sensitive", sum(1 for e in entries if "sensitive" in e["sensitivities"]))
    st.metric("Latest", entries[0]["date"] if entries else "—")

    st.markdown("<hr style='margin:1rem 0;'>", unsafe_allow_html=True)
    st.caption("Auto-refreshes every 60s")
    if st.button("↺  Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ── Tab bar ───────────────────────────────────────────────────────────────────

tab_entries, tab_blockers, tab_mood, tab_todos, tab_query = st.tabs(
    [
        "📅  Daily Entries",
        "🚧  Blockers",
        "😌  Mood",
        "✅  Action Items",
        "🔍  Query",
    ]
)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — DAILY ENTRIES
# ══════════════════════════════════════════════════════════════════════════════

with tab_entries:
    entries = load_all_entries()

    if not entries:
        st.markdown(
            """
        <div class="empty-state">
          <div class="empty-icon">📭</div>
          <div>No entries ingested yet.</div>
          <div style="margin-top:0.4rem;font-size:0.75rem;color:#94a3b8;">
            brain ingest --file daily/public/YYYY-MM-DD.md
          </div>
        </div>""",
            unsafe_allow_html=True,
        )
    else:
        left_col, right_col = st.columns([1, 2.8], gap="medium")

        # ── Left panel — date list ────────────────────────────────────────────
        with left_col:
            st.markdown(
                '<div class="panel-header">Entries</div>', unsafe_allow_html=True
            )

            fc1, fc2 = st.columns(2)
            with fc1:
                show_scope = st.selectbox(
                    "scope",
                    ["all", "public only", "sensitive only"],
                    label_visibility="collapsed",
                )
            with fc2:
                search_date = st.text_input(
                    "filter",
                    placeholder="filter…",
                    label_visibility="collapsed",
                )

            st.markdown("<div style='margin-top:0.4rem'></div>", unsafe_allow_html=True)

            filtered = entries
            if show_scope == "public only":
                filtered = [e for e in entries if "public" in e["sensitivities"]]
            elif show_scope == "sensitive only":
                filtered = [e for e in entries if "sensitive" in e["sensitivities"]]
            if search_date:
                filtered = [e for e in filtered if search_date in e["date"]]

            for entry in filtered:
                d = entry["date"]
                sens = entry["sensitivities"]
                sentiment = entry.get("sentiment", "neutral")
                is_sel = st.session_state.selected_date == d

                badges = ""
                if "public" in sens:
                    badges += '<span class="badge badge-public">pub</span>'
                if "sensitive" in sens:
                    badges += '<span class="badge badge-sensitive">sens</span>'

                bg = "#eff6ff" if is_sel else "#ffffff"
                border = "#93c5fd" if is_sel else "#e2e8f0"

                st.markdown(
                    f"""<div style="padding:0.5rem 0.7rem;border-radius:6px;
                        border:1px solid {border};background:{bg};margin-bottom:0.3rem;">
                      <div class="entry-date">{sentiment_emoji(sentiment)}  {d}</div>
                      <div class="entry-badges">{badges}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
                if st.button(
                    "open", key=f"btn_{d}", use_container_width=True, type="secondary"
                ):
                    st.session_state.selected_date = d
                    st.session_state.selected_sensitivity = (
                        "public" if "public" in sens else "sensitive"
                    )
                    st.rerun()

        # ── Right panel — summary content ─────────────────────────────────────
        with right_col:
            selected = st.session_state.selected_date

            if not selected:
                st.markdown(
                    """
                <div class="empty-state">
                  <div class="empty-icon">←</div>
                  <div>Select a date to read the summary</div>
                </div>""",
                    unsafe_allow_html=True,
                )
            else:
                entry = next((e for e in entries if e["date"] == selected), None)
                if entry:
                    sens_available = entry["sensitivities"]
                    files = entry["files"]

                    hdr_col, tog_col = st.columns([3, 1])
                    with hdr_col:
                        st.markdown(
                            f'<div class="panel-header">summary / {selected}</div>',
                            unsafe_allow_html=True,
                        )
                    with tog_col:
                        if len(sens_available) > 1:
                            chosen = st.radio(
                                "view",
                                sens_available,
                                horizontal=True,
                                index=sens_available.index(
                                    st.session_state.selected_sensitivity
                                )
                                if st.session_state.selected_sensitivity
                                in sens_available
                                else 0,
                                label_visibility="collapsed",
                            )
                            st.session_state.selected_sensitivity = chosen
                        else:
                            chosen = sens_available[0]

                    raw_file = files.get(chosen)
                    if raw_file:
                        content = read_summary_file(raw_file)
                        if content:
                            st.markdown(content)
                        else:
                            st.warning(f"File not found on disk: `{raw_file}`")
                    else:
                        st.info(f"No {chosen} summary for {selected}.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — BLOCKERS
# ══════════════════════════════════════════════════════════════════════════════

with tab_blockers:
    # ── Controls row ──────────────────────────────────────────────────────────
    ctrl1, ctrl2, ctrl3 = st.columns([1.5, 1.5, 1])

    all_entries = load_all_entries()
    dates_avail = [e["date"] for e in all_entries]

    with ctrl1:
        default_from = (datetime.today() - timedelta(days=90)).strftime("%Y-%m-%d")
        earliest = min(dates_avail) if dates_avail else default_from
        b_from = st.date_input(
            "From",
            value=datetime.strptime(max(default_from, earliest), "%Y-%m-%d"),
            key="b_from",
        )

    with ctrl2:
        b_to = st.date_input("To", value=datetime.today(), key="b_to")

    with ctrl3:
        b_sens = st.selectbox(
            "Sensitivity", ["all", "public", "sensitive"], key="b_sens"
        )

    blockers = load_blockers(
        date_from=b_from.strftime("%Y-%m-%d"),
        date_to=b_to.strftime("%Y-%m-%d"),
        sensitivity=b_sens,
    )

    st.markdown("<hr style='margin:0.5rem 0 1rem 0;'>", unsafe_allow_html=True)

    if not blockers:
        st.markdown(
            """
        <div class="empty-state">
          <div class="empty-icon">🎉</div>
          <div>No blockers in this date range.</div>
        </div>""",
            unsafe_allow_html=True,
        )
    else:
        # ── Frequency chart ───────────────────────────────────────────────────
        counts = Counter(b["blocker"] for b in blockers)
        top_n = 20
        chart_data = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
        labels = [item[0] for item in chart_data]
        values = [item[1] for item in chart_data]
        disp_lbls = [label[:58] + "…" if len(label) > 58 else label for label in labels]

        fig = go.Figure(
            go.Bar(
                x=values,
                y=disp_lbls,
                orientation="h",
                marker=dict(
                    color=values,
                    colorscale=[[0, "#bfdbfe"], [1, "#2563eb"]],
                    showscale=False,
                ),
                hovertemplate="<b>%{y}</b><br>%{x} occurrence(s)<extra></extra>",
                text=values,
                textposition="outside",
                textfont=dict(family="IBM Plex Mono", size=11, color="#64748b"),
            )
        )
        fig.update_layout(
            height=max(280, len(chart_data) * 32 + 60),
            margin=dict(l=10, r=40, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(
                showgrid=True,
                gridcolor="#f1f5f9",
                zeroline=False,
                tickfont=dict(family="IBM Plex Mono", size=10, color="#94a3b8"),
                title=dict(
                    text="occurrences",
                    font=dict(family="IBM Plex Mono", size=10, color="#94a3b8"),
                ),
            ),
            yaxis=dict(
                autorange="reversed",
                tickfont=dict(family="IBM Plex Sans", size=12, color="#0f172a"),
                ticksuffix="  ",
            ),
            hoverlabel=dict(
                bgcolor="#ffffff",
                bordercolor="#e2e8f0",
                font=dict(family="IBM Plex Sans", size=12),
            ),
        )

        st.markdown(
            f'<div class="panel-header">Blocker frequency — '
            f"top {min(top_n, len(chart_data))} of {len(counts)} unique "
            f"({len(blockers)} total occurrences)</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig, use_container_width=True)

        # ── Timeline table ────────────────────────────────────────────────────
        st.markdown(
            f'<div class="panel-header" style="margin-top:1.5rem;">'
            f"Timeline — all {len(blockers)} entries</div>",
            unsafe_allow_html=True,
        )

        search_b = st.text_input(
            "search",
            placeholder="filter blocker text…",
            label_visibility="collapsed",
            key="b_search",
        )

        shown = (
            [b for b in blockers if search_b.lower() in b["blocker"].lower()]
            if search_b
            else blockers
        )

        for b in shown:
            badge_cls = (
                "badge-public" if b["sensitivity"] == "public" else "badge-sensitive"
            )
            badge_lbl = "pub" if b["sensitivity"] == "public" else "sens"
            st.markdown(
                f"""<div class="blocker-row">
                  <div class="blocker-date">{b["date"]}</div>
                  <div class="blocker-text">{b["blocker"]}</div>
                  <div class="blocker-badge">
                    <span class="badge {badge_cls}">{badge_lbl}</span>
                  </div>
                </div>""",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — MOOD / SENTIMENT TIMELINE
# ══════════════════════════════════════════════════════════════════════════════

with tab_mood:
    # ── Controls ──────────────────────────────────────────────────────────────
    m_col1, m_col2, m_col3 = st.columns([1.5, 1.5, 1])

    all_entries_m = load_all_entries()
    dates_avail_m = [e["date"] for e in all_entries_m]

    with m_col1:
        default_from_m = (datetime.today() - timedelta(days=90)).strftime("%Y-%m-%d")
        earliest_m = min(dates_avail_m) if dates_avail_m else default_from_m
        m_from = st.date_input(
            "From",
            value=datetime.strptime(max(default_from_m, earliest_m), "%Y-%m-%d"),
            key="m_from",
        )
    with m_col2:
        m_to = st.date_input("To", value=datetime.today(), key="m_to")
    with m_col3:
        m_sens = st.selectbox(
            "Sensitivity", ["all", "public", "sensitive"], key="m_sens"
        )

    timeline = load_sentiment_timeline(
        date_from=m_from.strftime("%Y-%m-%d"),
        date_to=m_to.strftime("%Y-%m-%d"),
        sensitivity=m_sens,
    )

    st.markdown("<hr style='margin:0.5rem 0 1rem 0;'>", unsafe_allow_html=True)

    if not timeline:
        st.markdown(
            """
        <div class="empty-state">
          <div class="empty-icon">📭</div>
          <div>No sentiment data in this date range.</div>
          <div style="margin-top:0.4rem;font-size:0.75rem;color:#94a3b8;">
            brain ingest --file daily/public/YYYY-MM-DD.md
          </div>
        </div>""",
            unsafe_allow_html=True,
        )
    else:
        # ── Sentiment colour map — consistent with emoji map ──────────────────
        SENTIMENT_COLOUR = {
            "productive": "#22c55e",  # green
            "energized": "#3b82f6",  # blue
            "neutral": "#94a3b8",  # slate
            "frustrated": "#f59e0b",  # amber
            "overwhelmed": "#ef4444",  # red
        }
        SENTIMENT_ORDER = [
            "productive",
            "energized",
            "neutral",
            "frustrated",
            "overwhelmed",
        ]

        dates = [r["date"] for r in timeline]
        sentiments = [r["sentiment"] for r in timeline]
        colours = [SENTIMENT_COLOUR.get(s, "#94a3b8") for s in sentiments]

        # Numeric y-axis: 0 = best (productive), 4 = worst (overwhelmed)
        # Gives chart a natural "up is good" feel
        SENTIMENT_Y = {s: i for i, s in enumerate(SENTIMENT_ORDER)}
        y_vals = [SENTIMENT_Y.get(s, 2) for s in sentiments]

        # ── Line + scatter chart ──────────────────────────────────────────────
        fig_m = go.Figure()

        # Faint connecting line — shows continuity between days
        fig_m.add_trace(
            go.Scatter(
                x=dates,
                y=y_vals,
                mode="lines",
                line=dict(color="#e2e8f0", width=1.5),
                hoverinfo="skip",
                showlegend=False,
            )
        )

        # One scatter trace per sentiment so legend works cleanly
        for sentiment in SENTIMENT_ORDER:
            mask = [s == sentiment for s in sentiments]
            x_filt = [d for d, m in zip(dates, mask, strict=False) if m]
            y_filt = [y for y, m in zip(y_vals, mask, strict=False) if m]
            count = sum(mask)
            if not x_filt:
                continue

            fig_m.add_trace(
                go.Scatter(
                    x=x_filt,
                    y=y_filt,
                    mode="markers",
                    name=f"{sentiment_emoji(sentiment)}  {sentiment}  ({count}d)",
                    marker=dict(
                        color=SENTIMENT_COLOUR[sentiment],
                        size=10,
                        line=dict(color="white", width=1.5),
                    ),
                    hovertemplate=(f"<b>{sentiment}</b><br>%{{x}}<extra></extra>"),
                )
            )

        fig_m.update_layout(
            height=320,
            margin=dict(l=10, r=20, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(
                showgrid=True,
                gridcolor="#f1f5f9",
                zeroline=False,
                tickfont=dict(family="IBM Plex Mono", size=10, color="#94a3b8"),
                tickangle=-35,
            ),
            yaxis=dict(
                tickmode="array",
                tickvals=list(SENTIMENT_Y.values()),
                ticktext=[f"{sentiment_emoji(s)}  {s}" for s in SENTIMENT_ORDER],
                tickfont=dict(family="IBM Plex Sans", size=12, color="#0f172a"),
                gridcolor="#f1f5f9",
                zeroline=False,
                autorange="reversed",  # productive at top, overwhelmed at bottom
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="left",
                x=0,
                font=dict(family="IBM Plex Sans", size=11, color="#374151"),
                bgcolor="rgba(0,0,0,0)",
            ),
            hoverlabel=dict(
                bgcolor="#ffffff",
                bordercolor="#e2e8f0",
                font=dict(family="IBM Plex Sans", size=12),
            ),
        )

        st.markdown(
            f'<div class="panel-header">Mood timeline — {len(timeline)} day(s)</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig_m, use_container_width=True)

        # ── Distribution summary bar ──────────────────────────────────────────
        dist = Counter(sentiments)
        total = len(sentiments)

        summary_items = "".join(
            f'<div class="todo-summary-item">'
            f"  {sentiment_emoji(s)} <span>{dist[s]}</span> {s}"
            f'  <span style="color:#9ca3af;font-size:0.7rem;">'
            f"    ({round(dist[s] / total * 100)}%)"
            f"  </span>"
            f"</div>"
            for s in SENTIMENT_ORDER
            if dist[s] > 0
        )
        st.markdown(
            f'<div class="todo-summary-bar">{summary_items}</div>',
            unsafe_allow_html=True,
        )

        # ── Day-by-day table ──────────────────────────────────────────────────
        st.markdown(
            '<div class="panel-header" style="margin-top:1.2rem;">Day by day</div>',
            unsafe_allow_html=True,
        )
        for row in reversed(timeline):  # newest first in table
            s = row["sentiment"]
            colour = SENTIMENT_COLOUR.get(s, "#94a3b8")
            st.markdown(
                f"""<div class="blocker-row">
                  <div class="blocker-date">{row["date"]}</div>
                  <div class="blocker-text" style="color:{colour};font-weight:500;">
                    {sentiment_emoji(s)}&nbsp; {s}
                  </div>
                </div>""",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — ACTION ITEMS
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════

with tab_todos:
    todos = load_todos()

    if not todos:
        cfg_t = get_config()
        st.markdown(
            f"""
        <div class="empty-state">
          <div class="empty-icon">✅</div>
          <div>No action items found</div>
          <div style="margin-top:0.4rem;font-size:0.73rem;color:#9ca3af;">
            Expected: {cfg_t.todo_txt}
          </div>
        </div>""",
            unsafe_allow_html=True,
        )
    else:
        # ── Filters row ───────────────────────────────────────────────────────
        tf1, tf2, tf3, _ = st.columns([1, 1, 1, 1.5])

        with tf1:
            # Collect all unique dates for filter
            todo_dates = sorted(
                {t["date"] for t in todos if t["date"] != "unknown"}, reverse=True
            )
            date_filter = st.selectbox("Date", ["(all)"] + todo_dates, key="todo_date")

        with tf2:
            priority_filter = st.multiselect(
                "Priority",
                ["A", "B", "C"],
                default=["A", "B", "C"],
                key="todo_priority",
            )

        with tf3:
            # Collect all unique projects
            all_projects = sorted({p for t in todos for p in t["projects"]})
            project_filter = st.selectbox(
                "Project", ["(all)"] + all_projects, key="todo_project"
            )

        # ── Apply filters ─────────────────────────────────────────────────────
        filtered_todos = todos
        if date_filter != "(all)":
            filtered_todos = [t for t in filtered_todos if t["date"] == date_filter]
        if priority_filter != ["A", "B", "C"]:
            filtered_todos = [
                t
                for t in filtered_todos
                if t["priority"] in priority_filter
                or (t["priority"] is None and None in priority_filter)
            ]
        if project_filter != "(all)":
            filtered_todos = [
                t for t in filtered_todos if project_filter in t["projects"]
            ]

        # ── Summary bar ───────────────────────────────────────────────────────
        count_a = sum(1 for t in filtered_todos if t["priority"] == "A")
        count_b = sum(1 for t in filtered_todos if t["priority"] == "B")
        count_c = sum(1 for t in filtered_todos if t["priority"] == "C")
        count_total = len(filtered_todos)

        st.markdown(
            f'<div class="todo-summary-bar">'
            f'  <div class="todo-summary-item"><span>{count_total}</span>total</div>'
            f'  <div class="todo-summary-item"><span style="color:#991b1b">{count_a}</span>(A) urgent</div>'
            f'  <div class="todo-summary-item"><span style="color:#92400e">{count_b}</span>(B) important</div>'
            f'  <div class="todo-summary-item"><span style="color:#166534">{count_c}</span>(C) low priority</div>'
            f"</div>",
            unsafe_allow_html=True,
        )

        if not filtered_todos:
            st.markdown(
                '<div class="empty-state" style="min-height:20vh;">'
                '<div class="empty-icon">🔍</div>'
                "<div>No items match this filter</div></div>",
                unsafe_allow_html=True,
            )
        else:
            # ── Group by date, render each group ──────────────────────────────

            # Sort: (A) first, then (B), then (C), then None — within each date
            priority_order = {"A": 0, "B": 1, "C": 2, None: 3}
            sorted_todos = sorted(
                filtered_todos,
                key=lambda t: (t["date"], priority_order.get(t["priority"], 3)),
                reverse=True,  # newest date first
            )

            # Re-sort so newest date is first but items within date sorted by priority
            # groupby needs contiguous groups — pre-sort by date desc, priority asc
            sorted_todos = sorted(
                filtered_todos,
                key=lambda t: (
                    # negate date string for descending (lexicographic trick)
                    [-ord(c) for c in t["date"]],
                    priority_order.get(t["priority"], 3),
                ),
            )

            # Group by date
            grouped: dict[str, list] = {}
            for item in sorted_todos:
                grouped.setdefault(item["date"], []).append(item)

            for date_key in sorted(grouped.keys(), reverse=True):
                group = grouped[date_key]

                # Date header with item count
                st.markdown(
                    f'<div class="todo-date-header">'
                    f"  <span>📅</span>"
                    f"  <span>{date_key}</span>"
                    f'  <span style="font-weight:400;color:#6b7280;font-size:0.72rem;">'
                    f"    {len(group)} item(s)"
                    f"  </span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                for item in group:
                    p = item["priority"] or "–"
                    p_class = {
                        "A": "priority-a",
                        "B": "priority-b",
                        "C": "priority-c",
                    }.get(p, "priority-c")

                    # Build project + context tag chips
                    tags_html = ""
                    if item["projects"] or item["contexts"]:
                        tags_html = '<div class="todo-tags">'
                        for proj in item["projects"]:
                            tags_html += f'<span class="tag-project">+{proj}</span>'
                        for ctx in item["contexts"]:
                            tags_html += f'<span class="tag-context">@{ctx}</span>'
                        tags_html += "</div>"

                    st.markdown(
                        f'<div class="todo-item">'
                        f'  <span class="priority-badge {p_class}">({p})</span>'
                        f'  <div class="todo-text">'
                        f"    <div>{item['text']}</div>"
                        f"    {tags_html}"
                        f"  </div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — QUERY
# ══════════════════════════════════════════════════════════════════════════════

with tab_query:
    # ── Input row ─────────────────────────────────────────────────────────────
    q_col, scope_col, btn_col = st.columns([4, 1, 0.7], gap="small")

    with q_col:
        question_input = st.text_input(
            "question",
            placeholder="What was I blocked on last month?  /  Summarise my Azure work  /  How many days did I feel overwhelmed?",
            label_visibility="collapsed",
            key="query_input",
        )
    with scope_col:
        scope_input = st.selectbox(
            "scope",
            ["all", "public", "sensitive"],
            label_visibility="collapsed",
            key="query_scope",
        )
    with btn_col:
        ask_clicked = st.button("Ask  →", use_container_width=True, type="primary")

    # ── Hint line ─────────────────────────────────────────────────────────────
    st.markdown(
        "<div style='font-family:IBM Plex Mono,monospace;font-size:0.68rem;"
        "color:#9ca3af;margin-top:-0.3rem;margin-bottom:0.8rem;'>"
        "Tip: questions with dates route to ChromaDB + SQLite. "
        'Counting questions ("how many...") use SQLite only.</div>',
        unsafe_allow_html=True,
    )

    # ── Run query ─────────────────────────────────────────────────────────────
    if ask_clicked and question_input.strip():
        st.session_state.query_error = None
        st.session_state.query_result = None

        with st.spinner("Thinking… (calling local LLM — may take 15–30s)"):
            try:
                from brain.query import RAGEngine

                engine = RAGEngine(get_config())
                result = engine.answer(
                    question=question_input.strip(),
                    scope=scope_input,
                )
                st.session_state.query_result = result

                # Persist to SQLite — survives refresh and restarts
                save_query_to_history(
                    question=question_input.strip(),
                    scope=scope_input,
                    result=result,
                )
                # Refresh session state history from DB
                st.session_state.query_history = load_query_history(limit=20)

            except Exception as e:
                st.session_state.query_error = str(e)

    elif ask_clicked and not question_input.strip():
        st.warning("Type a question first.")

    # ── Render result ─────────────────────────────────────────────────────────
    result = st.session_state.query_result
    error = st.session_state.query_error

    if error:
        st.error(
            f"**Query failed:** {error}\n\n"
            "Check that Ollama is running: `ollama serve`\n"
            f"And that your model is available: `ollama run {get_config().ollama_llm_model}`"
        )

    elif result:
        # Answer block
        st.markdown(
            f'<div class="query-answer">{result.answer}</div>',
            unsafe_allow_html=True,
        )

        # Metadata bar
        dates_str = (
            ", ".join(result.dates_covered[:6])
            + (" …" if len(result.dates_covered) > 6 else "")
            if result.dates_covered
            else "—"
        )
        date_range_str = ""
        if result.date_from or result.date_to:
            date_range_str = (
                f'<div class="query-meta-item">range '
                f"<span>{result.date_from or '—'} → {result.date_to or '—'}</span></div>"
            )

        st.markdown(
            f'<div class="query-meta-bar">'
            f'  <div class="query-meta-item">'
            f'    pattern <span class="pattern-badge">{result.pattern}</span>'
            f"  </div>"
            f'  <div class="query-meta-item">scope <span>{result.scope}</span></div>'
            f'  <div class="query-meta-item">chunks <span>{result.chunks_retrieved}</span></div>'
            f'  <div class="query-meta-item">dates <span>{dates_str}</span></div>'
            f"  {date_range_str}"
            f"</div>",
            unsafe_allow_html=True,
        )

    elif not ask_clicked:
        # No query yet — show example questions
        st.markdown(
            """
        <div class="empty-state" style="min-height:30vh;">
          <div class="empty-icon">🔍</div>
          <div>Ask a question about your brain</div>
          <div style="margin-top:1rem;text-align:left;max-width:480px;">
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.72rem;
                        color:#6b7280;margin-bottom:0.5rem;letter-spacing:0.05em;">
              EXAMPLE QUESTIONS
            </div>
            <div style="font-size:0.82rem;color:#374151;line-height:2;">
              What was I blocked on last month?<br>
              Summarise my Azure work in February<br>
              How many days did I feel overwhelmed?<br>
              What patterns do you see in my blockers?<br>
              Which projects took the most focus?
            </div>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    # ── Query history (persistent — survives refresh) ─────────────────────────
    history = st.session_state.query_history
    # Skip index 0 only if it matches the current active question
    current_q = question_input.strip() if ask_clicked else None
    display_history = (
        [h for h in history if h["question"] != current_q] if current_q else history
    )

    if display_history:
        st.markdown("<hr style='margin:1.5rem 0 1rem 0;'>", unsafe_allow_html=True)
        st.markdown(
            "<div class='panel-header'>Query history</div>",
            unsafe_allow_html=True,
        )
        for i, h in enumerate(display_history):
            date_label = h["asked_at"][:10]  # YYYY-MM-DD
            time_label = h["time"]  # HH:MM
            pattern = h["pattern"] or "—"
            scope = h["scope"]

            col_q, col_meta, col_btn = st.columns([4, 2, 0.8], gap="small")
            with col_q:
                st.markdown(
                    f"<div style='font-size:0.85rem;color:#0f172a;"
                    f"padding:0.4rem 0;line-height:1.4;'>{h['question']}</div>",
                    unsafe_allow_html=True,
                )
            with col_meta:
                st.markdown(
                    f"<div style='font-family:IBM Plex Mono,monospace;font-size:0.68rem;"
                    f"color:#94a3b8;padding:0.5rem 0;'>"
                    f"{date_label} {time_label} · "
                    f"<span class=\"pattern-badge\" style='font-size:0.65rem;'>{pattern}</span>"
                    f" · {scope}</div>",
                    unsafe_allow_html=True,
                )
            with col_btn:
                if st.button(
                    "↩ view", key=f"hist_{i}_{h['id']}", use_container_width=True
                ):
                    # Re-surface stored answer — no LLM call
                    from brain.query import QueryResult

                    st.session_state.query_result = QueryResult(
                        question=h["question"],
                        answer=h["answer"],
                        pattern=h["pattern"] or "—",
                        scope=h["scope"],
                        chunks_retrieved=h["chunks_retrieved"],
                        dates_covered=h["dates_covered"],
                        date_from=None,
                        date_to=None,
                        rationale=h["rationale"],
                    )
                    st.session_state.query_error = None
                    st.rerun()

            st.markdown(
                "<div style='border-bottom:1px solid #f1f5f9;margin:0.1rem 0;'></div>",
                unsafe_allow_html=True,
            )
