"""
dashboard.py — Brain Dashboard (Phase 4)

Tabs:
  📅 Daily Entries  — browse ingested dates, read summaries side-by-side
  🚧 Blockers       — frequency chart + full timeline table
  ✅ Action Items   — todo.txt grouped by date and priority  (Step 3)
  🔍 Query          — natural language RAG query interface   (Step 4)

Usage:
  uv run streamlit run dashboard.py
"""

import sqlite3
import sys
from collections import Counter
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.resolve()
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from brain.config import BrainConfig  # noqa

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="🧠 Brain",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

  html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }

  .stTabs [data-baseweb="tab-list"] { gap:0; border-bottom:2px solid #ddd8cc; }
  .stTabs [data-baseweb="tab"] {
    font-family:'IBM Plex Mono',monospace; font-size:0.78rem;
    letter-spacing:0.06em; color:#6b7280; padding:0.6rem 1.4rem;
    border-bottom:2px solid transparent; margin-bottom:-2px;
  }
  .stTabs [aria-selected="true"] { color:#2563eb; border-bottom:2px solid #2563eb; }

  section[data-testid="stSidebar"] { border-right:1px solid #ddd8cc; }

  .entry-card {
    padding:0.55rem 0.75rem; border-radius:6px;
    border:1px solid #ddd8cc; background:#ffffff; margin-bottom:0.35rem;
  }
  .entry-date {
    font-family:'IBM Plex Mono',monospace; font-size:0.82rem;
    font-weight:500; color:#1a1a2e;
  }
  .entry-badges { margin-top:0.25rem; display:flex; gap:0.3rem; }

  .badge {
    font-family:'IBM Plex Mono',monospace; font-size:0.62rem;
    padding:0.1rem 0.4rem; border-radius:3px; font-weight:500; letter-spacing:0.03em;
  }
  .badge-public  { background:#dcfce7; color:#166534; border:1px solid #86efac; }
  .badge-sensitive { background:#fee2e2; color:#991b1b; border:1px solid #fca5a5; }

  .content-header {
    font-family:'IBM Plex Mono',monospace; font-size:0.72rem; color:#6b7280;
    letter-spacing:0.1em; text-transform:uppercase; margin-bottom:1rem;
    padding-bottom:0.5rem; border-bottom:1px solid #ddd8cc;
  }
  .section-label {
    font-family:'IBM Plex Mono',monospace; font-size:0.7rem; color:#6b7280;
    letter-spacing:0.1em; text-transform:uppercase; margin-bottom:0.75rem;
  }
  .empty-state {
    display:flex; flex-direction:column; align-items:center;
    justify-content:center; min-height:38vh; color:#9ca3af;
    font-family:'IBM Plex Mono',monospace; font-size:0.82rem;
  }
  .empty-icon { font-size:2.2rem; margin-bottom:0.7rem; opacity:0.45; }

  .blocker-row {
    display:flex; align-items:flex-start; gap:1rem;
    padding:0.6rem 0; border-bottom:1px solid #eeeae1; font-size:0.85rem;
  }
  .blocker-date {
    font-family:'IBM Plex Mono',monospace; font-size:0.78rem;
    color:#6b7280; white-space:nowrap; min-width:90px;
  }
  .blocker-text { color:#1a1a2e; flex:1; }

  .stSelectbox label, .stDateInput label, .stRadio label,
  .stTextInput label, .stMultiSelect label {
    font-family:'IBM Plex Mono',monospace; font-size:0.72rem;
    color:#6b7280; letter-spacing:0.06em; text-transform:uppercase;
  }
  div[data-testid="stMetric"] {
    background:#ffffff; border:1px solid #ddd8cc;
    border-radius:6px; padding:0.7rem 1rem;
  }
  div[data-testid="stMetricValue"] {
    font-family:'IBM Plex Mono',monospace; font-size:1.5rem;
  }
  hr { border-color:#ddd8cc; }
</style>
""",
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
# Data loaders
# ══════════════════════════════════════════════════════════════════════════════


@st.cache_resource
def get_config() -> BrainConfig:
    return BrainConfig()


@st.cache_data(ttl=60)
def load_all_entries() -> list[dict]:
    cfg = get_config()
    if not cfg.sqlite_db.exists():
        return []
    conn = sqlite3.connect(cfg.sqlite_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, sensitivity, raw_file, sentiment
        FROM daily_summaries ORDER BY date DESC
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
def load_blockers(date_from=None, date_to=None, sensitivity="all") -> list[dict]:
    cfg = get_config()
    if not cfg.sqlite_db.exists():
        return []

    q = """
        SELECT ds.date, ds.sensitivity, je.value as blocker
        FROM daily_summaries ds, json_each(ds.blockers) je
        WHERE TRIM(je.value) != ''
    """
    params = []
    if date_from:
        q += " AND ds.date >= ?"
        params.append(date_from)
    if date_to:
        q += " AND ds.date <= ?"
        params.append(date_to)
    if sensitivity != "all":
        q += " AND ds.sensitivity = ?"
        params.append(sensitivity)
    q += " ORDER BY ds.date DESC"

    conn = sqlite3.connect(cfg.sqlite_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [
        {"date": r["date"], "blocker": r["blocker"], "sensitivity": r["sensitivity"]}
        for r in rows
    ]


def read_summary_file(raw_file: str) -> str | None:
    cfg = get_config()
    path = cfg._root / raw_file
    return path.read_text(encoding="utf-8") if path.exists() else None


def sentiment_emoji(s: str) -> str:
    return {
        "productive": "🟢",
        "energized": "🔵",
        "neutral": "⚪",
        "frustrated": "🟡",
        "overwhelmed": "🔴",
    }.get(s, "⚪")


# ══════════════════════════════════════════════════════════════════════════════
# Session state
# ══════════════════════════════════════════════════════════════════════════════

if "selected_date" not in st.session_state:
    st.session_state.selected_date = None
if "selected_sensitivity" not in st.session_state:
    st.session_state.selected_sensitivity = "public"


# ══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown(
        """
    <div style="padding:0.25rem 0 1rem 0;">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:1.05rem;
                   font-weight:500;color:#1a1a2e;letter-spacing:0.05em;">🧠 brain</span>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:0.68rem;
                   color:#6b7280;display:block;margin-top:0.1rem;">local · private · queryable</span>
    </div>
    """,
        unsafe_allow_html=True,
    )
    st.divider()

    entries = load_all_entries()
    c1, c2 = st.columns(2)
    c1.metric("Entries", len(entries))
    c2.metric("Latest", entries[0]["date"] if entries else "—")
    c1.metric("Public", sum(1 for e in entries if "public" in e["sensitivities"]))
    c2.metric("Sensitive", sum(1 for e in entries if "sensitive" in e["sensitivities"]))

    st.divider()
    st.markdown(
        "<div style='font-family:IBM Plex Mono,monospace;font-size:0.68rem;color:#9ca3af;text-align:center;'>refreshes every 60s</div>",
        unsafe_allow_html=True,
    )
    if st.button("↺  Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Tabs
# ══════════════════════════════════════════════════════════════════════════════

tab_entries, tab_blockers, tab_todos, tab_query = st.tabs(
    [
        "📅  Daily Entries",
        "🚧  Blockers",
        "✅  Action Items",
        "🔍  Query",
    ]
)


# ── TAB 1: Daily Entries ──────────────────────────────────────────────────────

with tab_entries:
    entries = load_all_entries()
    if not entries:
        st.markdown(
            '<div class="empty-state"><div class="empty-icon">📭</div><div>No entries ingested yet</div></div>',
            unsafe_allow_html=True,
        )
    else:
        left_col, right_col = st.columns([1, 2.8], gap="medium")

        with left_col:
            st.markdown(
                '<div class="section-label">Entries</div>', unsafe_allow_html=True
            )
            f1, f2 = st.columns(2)
            with f1:
                show_scope = st.selectbox(
                    "scope",
                    ["all", "public only", "sensitive only"],
                    label_visibility="collapsed",
                )
            with f2:
                search_date = st.text_input(
                    "filter", placeholder="filter…", label_visibility="collapsed"
                )

            st.markdown(
                "<div style='margin-top:0.4rem;'></div>", unsafe_allow_html=True
            )

            filtered = entries
            if show_scope == "public only":
                filtered = [e for e in filtered if "public" in e["sensitivities"]]
            elif show_scope == "sensitive only":
                filtered = [e for e in filtered if "sensitive" in e["sensitivities"]]
            if search_date:
                filtered = [e for e in filtered if search_date in e["date"]]

            for entry in filtered:
                d, sens = entry["date"], entry["sensitivities"]
                is_sel = st.session_state.selected_date == d
                badges = ""
                if "public" in sens:
                    badges += '<span class="badge badge-public">public</span>'
                if "sensitive" in sens:
                    badges += '<span class="badge badge-sensitive">sensitive</span>'

                border = "#2563eb" if is_sel else "#ddd8cc"
                bg = "#eff6ff" if is_sel else "#ffffff"
                st.markdown(
                    f'<div class="entry-card" style="border-color:{border};background:{bg};">'
                    f'<div class="entry-date">{sentiment_emoji(entry.get("sentiment", "neutral"))} {d}</div>'
                    f'<div class="entry-badges">{badges}</div></div>',
                    unsafe_allow_html=True,
                )
                if st.button("open", key=f"btn_{d}", use_container_width=True):
                    st.session_state.selected_date = d
                    st.session_state.selected_sensitivity = (
                        "public" if "public" in sens else "sensitive"
                    )
                    st.rerun()

        with right_col:
            selected = st.session_state.selected_date
            if not selected:
                st.markdown(
                    '<div class="empty-state"><div class="empty-icon">←</div><div>Select a date to read the summary</div></div>',
                    unsafe_allow_html=True,
                )
            else:
                entry = next((e for e in entries if e["date"] == selected), None)
                if entry:
                    sens_available, files = entry["sensitivities"], entry["files"]
                    hc, tc = st.columns([2, 1])
                    with hc:
                        st.markdown(
                            f'<div class="content-header">summary / {selected}</div>',
                            unsafe_allow_html=True,
                        )
                    with tc:
                        if len(sens_available) > 1:
                            chosen_sens = st.radio(
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
                            st.session_state.selected_sensitivity = chosen_sens
                        else:
                            chosen_sens = sens_available[0]

                    raw_file = files.get(chosen_sens)
                    if raw_file:
                        content = read_summary_file(raw_file)
                        if content:
                            st.markdown(content)
                        else:
                            st.warning(
                                f"File not on disk: `{raw_file}` — re-ingest to restore."
                            )
                    else:
                        st.info(f"No {chosen_sens} summary for {selected}.")


# ── TAB 2: Blockers ───────────────────────────────────────────────────────────

with tab_blockers:
    all_entries = load_all_entries()
    dates = sorted({e["date"] for e in all_entries})

    fc1, fc2, fc3, _ = st.columns([1, 1, 1, 2])
    with fc1:
        date_from_b = st.selectbox("From", ["(all)"] + dates, key="blocker_from")
    with fc2:
        date_to_b = st.selectbox(
            "To", ["(all)"] + list(reversed(dates)), key="blocker_to"
        )
    with fc3:
        sens_b = st.selectbox(
            "Sensitivity", ["all", "public", "sensitive"], key="blocker_sens"
        )

    date_from_val = None if date_from_b == "(all)" else date_from_b
    date_to_val = None if date_to_b == "(all)" else date_to_b
    blockers = load_blockers(date_from_val, date_to_val, sens_b)

    if not blockers:
        st.markdown(
            '<div class="empty-state"><div class="empty-icon">🎉</div><div>No blockers found</div></div>',
            unsafe_allow_html=True,
        )
    else:
        # ── Frequency chart ───────────────────────────────────────────────────
        st.markdown(
            '<div class="section-label" style="margin-top:1rem;">Frequency</div>',
            unsafe_allow_html=True,
        )

        counts = Counter(b["blocker"] for b in blockers)
        items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        labels = [
            (label[:52] + "…") if len(label) > 55 else label for label, _ in items
        ]
        values = [val for _, val in items]

        fig = go.Figure(
            go.Bar(
                x=values,
                y=labels,
                orientation="h",
                marker=dict(
                    color=values,
                    colorscale=[[0, "#bfdbfe"], [0.5, "#3b82f6"], [1, "#1d4ed8"]],
                    showscale=False,
                ),
                text=values,
                textposition="outside",
                hovertemplate="<b>%{y}</b><br>%{x} occurrence(s)<extra></extra>",
            )
        )
        fig.update_layout(
            margin=dict(l=0, r=40, t=10, b=10),
            height=max(280, len(labels) * 36),
            plot_bgcolor="#f8f6f1",
            paper_bgcolor="#f8f6f1",
            xaxis=dict(
                showgrid=True,
                gridcolor="#eeeae1",
                zeroline=False,
                tickfont=dict(family="IBM Plex Mono", size=11, color="#6b7280"),
            ),
            yaxis=dict(
                autorange="reversed",
                tickfont=dict(family="IBM Plex Sans", size=12, color="#1a1a2e"),
            ),
            hoverlabel=dict(
                bgcolor="#ffffff", font=dict(family="IBM Plex Sans", size=12)
            ),
        )
        st.plotly_chart(fig, use_container_width=True)

        # ── Timeline table ────────────────────────────────────────────────────
        st.markdown(
            f'<div class="section-label">Timeline — {len(blockers)} blocker(s)</div>',
            unsafe_allow_html=True,
        )

        rows_html = ""
        for b in blockers:
            badge = (
                '<span class="badge badge-public">public</span>'
                if b["sensitivity"] == "public"
                else '<span class="badge badge-sensitive">sensitive</span>'
            )
            rows_html += (
                f'<div class="blocker-row">'
                f'<div class="blocker-date">{b["date"]}</div>'
                f'<div class="blocker-text">{b["blocker"]}</div>'
                f"<div>{badge}</div>"
                f"</div>"
            )
        st.markdown(rows_html, unsafe_allow_html=True)


# ── TAB 3: Action Items (Step 3) ──────────────────────────────────────────────

with tab_todos:
    st.markdown(
        '<div class="empty-state"><div class="empty-icon">✅</div><div>Action Items — coming in Step 3</div></div>',
        unsafe_allow_html=True,
    )


# ── TAB 4: Query (Step 4) ─────────────────────────────────────────────────────

with tab_query:
    st.markdown(
        '<div class="empty-state"><div class="empty-icon">🔍</div><div>Query interface — coming in Step 4</div></div>',
        unsafe_allow_html=True,
    )
