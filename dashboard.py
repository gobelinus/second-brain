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

tab_entries, tab_blockers, tab_todos, tab_query = st.tabs(
    [
        "📅  Daily Entries",
        "🚧  Blockers",
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
# TAB 3 — ACTION ITEMS
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
# TAB 4 — QUERY (Step 4)
# ══════════════════════════════════════════════════════════════════════════════

with tab_query:
    st.markdown(
        """
    <div class="empty-state">
      <div class="empty-icon">🔍</div>
      <div>Query interface — coming in Step 4</div>
    </div>""",
        unsafe_allow_html=True,
    )
