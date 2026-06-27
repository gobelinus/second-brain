"""
config.py — Central configuration loader for Brain.

Load order:
  1. config.yaml         (base, version controlled)
  2. config.local.yaml   (local overrides, git ignored)

config.local.yaml values always win — machine-specific overrides only.
"""

from pathlib import Path

import yaml

# ── Root of the project (two levels up from src/brain/) ──────────────────────
ROOT = Path(__file__).parent.parent.parent


def _load_yaml(path: Path) -> dict:
    """Safely load a YAML file. Returns empty dict if file doesn't exist."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge override into base.
    Override values win at every level.
    """
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> dict:
    """
    Load and merge base config with local overrides.
    Always call this — never access config.yaml directly.
    """
    base = _load_yaml(ROOT / "config.yaml")
    local = _load_yaml(ROOT / "config.local.yaml")
    return _deep_merge(base, local)


# ── Typed convenience accessors ───────────────────────────────────────────────
# Keeps calling code clean: config.ollama_model not config["ollama"]["llm_model"]


class BrainConfig:
    """
    Typed wrapper around raw config dict.
    All keys are aligned to config.yaml structure — config.yaml is ground truth.
    """

    def __init__(self):
        self._cfg = load_config()
        self._root = Path(__file__).parent.parent.parent.resolve()

    # ── Ollama ────────────────────────────────────────────────────────────────

    @property
    def ollama_base_url(self) -> str:
        return self._cfg["ollama"]["base_url"]

    @property
    def ollama_llm_model(self) -> str:
        return self._cfg["ollama"]["llm_model"]

    @property
    def ollama_embed_model(self) -> str:
        return self._cfg["ollama"]["embed_model"]

    @property
    def ollama_temperature(self) -> int:
        return self._cfg["ollama"].get("temperature", 0)

    @property
    def ollama_timeout(self) -> int:
        return self._cfg["ollama"].get("timeout_seconds", 120)

    # ── Paths ─────────────────────────────────────────────────────────────────
    # All paths are resolved relative to project ROOT.
    # config.yaml key names: paths.daily_public, paths.daily_sensitive, etc.

    @property
    def daily_public(self) -> Path:
        return ROOT / self._cfg["paths"]["daily_public"]

    @property
    def daily_sensitive(self) -> Path:
        return ROOT / self._cfg["paths"]["daily_sensitive"]

    @property
    def inputs(self) -> Path:
        return ROOT / self._cfg["paths"]["inputs"]

    @property
    def inputs_archive(self) -> Path:
        return ROOT / self._cfg["paths"]["inputs_archive"]

    @property
    def exports_public(self) -> Path:
        return ROOT / self._cfg["paths"]["exports_public"]

    @property
    def exports_sensitive(self) -> Path:
        return ROOT / self._cfg["paths"]["exports_sensitive"]

    @property
    def exports_reminders(self) -> Path:
        return ROOT / self._cfg["paths"]["exports_reminders"]

    @property
    def chroma_store(self) -> Path:
        return ROOT / self._cfg["paths"]["store_chroma"]

    @property
    def sqlite_db(self) -> Path:
        return ROOT / self._cfg["paths"]["store_sqlite"]

    @property
    def journal_dir(self) -> Path:
        return ROOT / self._cfg["paths"]["journal"]

    @property
    def deltas_dir(self) -> Path:
        return ROOT / self._cfg["paths"]["deltas"]

    @property
    def wiki_dir(self) -> Path:
        return ROOT / self._cfg["paths"]["wiki"]

    @property
    def launchd_dir(self) -> Path:
        return ROOT / self._cfg["paths"]["launchd"]

    # ── Ingestion ─────────────────────────────────────────────────────────────
    # config.yaml section: ingest (not ingestion)

    @property
    def chunk_size(self) -> int:
        return self._cfg["ingest"]["chunk_size_words"]

    @property
    def chunk_overlap(self) -> int:
        return self._cfg["ingest"]["chunk_overlap_words"]

    @property
    def default_sensitivity(self) -> str:
        return self._cfg["ingest"].get("default_sensitivity", "public")

    # ── Metadata ──────────────────────────────────────────────────────────────
    # config.yaml key: metadata.valid_sentiments (not sentiment_options)

    @property
    def valid_sentiments(self) -> list[str]:
        return self._cfg["metadata"]["valid_sentiments"]

    @property
    def default_sentiment(self) -> str:
        return self._cfg["metadata"].get("default_sentiment", "neutral")

    # ── ChromaDB ──────────────────────────────────────────────────────────────
    # config.yaml structure: chroma.collections.public / .sensitive

    @property
    def public_collection(self) -> str:
        return self._cfg["chroma"]["collections"]["public"]

    @property
    def sensitive_collection(self) -> str:
        return self._cfg["chroma"]["collections"]["sensitive"]

    def chroma_collection_for(self, sensitivity: str) -> str:
        """Return the correct ChromaDB collection name for a given sensitivity."""
        if sensitivity == "public":
            return self.public_collection
        elif sensitivity == "sensitive":
            return self.sensitive_collection
        else:
            raise ValueError(
                f"Unknown sensitivity: '{sensitivity}'. Use 'public' or 'sensitive'."
            )

    def exports_path_for(self, sensitivity: str) -> Path:
        """Return the correct exports directory for a given sensitivity."""
        if sensitivity == "public":
            return self.exports_public
        elif sensitivity == "sensitive":
            return self.exports_sensitive
        else:
            raise ValueError(
                f"Unknown sensitivity: '{sensitivity}'. Use 'public' or 'sensitive'."
            )

    # ── Blog paths ────────────────────────────────────────────────────────────

    @property
    def blogs_public(self) -> Path:
        return ROOT / self._cfg["paths"]["blogs_public"]

    @property
    def blogs_sensitive(self) -> Path:
        return ROOT / self._cfg["paths"]["blogs_sensitive"]

    # ── Blog ChromaDB collections ─────────────────────────────────────────────

    @property
    def blog_public_collection(self) -> str:
        return self._cfg["chroma"]["collections"]["blog_public"]

    @property
    def blog_sensitive_collection(self) -> str:
        return self._cfg["chroma"]["collections"]["blog_sensitive"]

    def blog_collection_for(self, sensitivity: str) -> str:
        """Return the correct blog ChromaDB collection for a given sensitivity."""
        if sensitivity == "public":
            return self.blog_public_collection
        elif sensitivity == "sensitive":
            return self.blog_sensitive_collection
        else:
            raise ValueError(
                f"Unknown sensitivity: '{sensitivity}'. Use 'public' or 'sensitive'."
            )

    def is_blog_file(self, file_path: Path) -> bool:
        """
        Detect if a file is a blog post based on its path.
        Returns True if file lives under blogs/public/ or blogs/sensitive/.
        """
        resolved = Path(file_path).resolve()
        return (
            self.blogs_public.resolve() in resolved.parents
            or self.blogs_sensitive.resolve() in resolved.parents
        )

    # ── Blog structurer settings ──────────────────────────────────────────────

    @property
    def blog_extraction_prompt(self) -> str:
        """LLM prompt for extracting blog metadata. Placeholder: {content}"""
        return self._cfg.get("blog", {}).get("extraction_prompt", "")

    @property
    def blog_chunk_size(self) -> int:
        return self._cfg.get("blog", {}).get("chunk_size_words", 500)

    @property
    def blog_chunk_overlap(self) -> int:
        return self._cfg.get("blog", {}).get("chunk_overlap_words", 50)

    # ── Scanner ───────────────────────────────────────────────────────────────
    # Evaluation order enforced by scan_public_summaries.py:
    #   1. allowlist        → safe, skip further checks
    #   2. private_domains  → always critical, block commit
    #   3. blocklist        → severity-aware: critical/high block, warning logs

    @property
    def scanner_allowlist(self) -> list[str]:
        """Patterns explicitly safe — checked first, skips further checks."""
        return self._cfg.get("scanner", {}).get("allowlist", [])

    @property
    def scanner_private_domains(self) -> list[dict]:
        """
        Private domain patterns — always critical severity.
        Each entry: {"pattern": str, "description": str}
        """
        return self._cfg.get("scanner", {}).get("private_domains", [])

    @property
    def scanner_blocklist(self) -> list[dict]:
        """
        Ordered blocklist with severity levels.
        Each entry: {"pattern": str, "description": str, "severity": str}
        severity: critical | high | warning
        """
        return self._cfg.get("scanner", {}).get("blocklist", [])

    # ── Composer ──────────────────────────────────────────────────────────────

    @property
    def composer_output_sensitivity(self) -> str:
        """Sensitivity for composed output — always 'sensitive'."""
        return self._cfg.get("composer", {}).get("output_sensitivity", "sensitive")

    @property
    def composer_input_extensions(self) -> list[str]:
        """File extensions accepted as composer inputs."""
        return self._cfg.get("composer", {}).get("input_extensions", [".md"])

    @property
    def composer_archive_after_compose(self) -> bool:
        """Archive input files after successful compose."""
        return self._cfg.get("composer", {}).get("archive_after_compose", True)

    @property
    def composer_prompt_template(self) -> str:
        """
        LLM prompt template for combining daily notes.
        Placeholders injected at runtime: {date}, {content}
        """
        return self._cfg.get("composer", {}).get("prompt_template", "")

    def composer_inputs_path(self, date: str) -> Path:
        """Path to the inputs folder for a given date. e.g. inputs/2026-02-24/"""
        return self.inputs / date

    def composer_archive_path(self, date: str) -> Path:
        """Path to the archive folder for a given date. e.g. inputs/archive/2026-02-24/"""
        return self.inputs_archive / date

    def composer_output_path(self, date: str) -> Path:
        """Full output path for a composed summary. e.g. daily/sensitive/2026-02-24.md"""
        if self.composer_output_sensitivity == "sensitive":
            return self.daily_sensitive / f"{date}.md"
        return self.daily_public / f"{date}.md"

    # Add these two properties to BrainConfig, inside the # ── Composer ── section
    # Place them after composer_output_path()

    @property
    def composer_todo_prompt_template(self) -> str:
        """
        LLM prompt template for extracting action items into todo.txt format.
        Placeholders injected at runtime: {date}, {content}
        """
        return self._cfg.get("composer", {}).get("todo_prompt_template", "")

    @property
    def composer_due_date_offsets(self) -> dict:
        """
        Business day offsets for todo.txt due date injection.
        Keys: "A", "B", "C" — values: int (0 = no due date).
        """
        return self._cfg.get("composer", {}).get(
            "due_date_offsets",
            {
                "A": 2,
                "B": 4,
                "C": 0,
            },
        )

    @property
    def todo_txt(self) -> Path:
        """Path to the rolling todo.txt file at project root."""
        return ROOT / self._cfg["paths"].get("todo_txt", "todo.txt")

    # ── Reminder ──────────────────────────────────────────────────────────────

    @property
    def reminder_hour(self) -> int:
        """Hour to trigger the daily reminder (24h format)."""
        return self._cfg.get("reminder", {}).get("schedule", {}).get("hour", 9)

    @property
    def reminder_minute(self) -> int:
        """Minute to trigger the daily reminder."""
        return self._cfg.get("reminder", {}).get("schedule", {}).get("minute", 0)

    @property
    def reminder_notification_title(self) -> str:
        """macOS notification title."""
        return (
            self._cfg.get("reminder", {})
            .get("notification", {})
            .get("title", "🧠 Brain — Daily Summary")
        )

    @property
    def reminder_notification_message(self) -> str:
        """
        macOS notification message body.
        Placeholder injected at runtime: {date}
        """
        return (
            self._cfg.get("reminder", {})
            .get("notification", {})
            .get("message", "Time to capture today's summary.")
        )

    @property
    def reminder_notification_sound(self) -> str:
        """macOS notification sound name."""
        return (
            self._cfg.get("reminder", {})
            .get("notification", {})
            .get("sound", "default")
        )

    @property
    def reminder_launchd_label(self) -> str:
        """Launchd plist label — also used as the plist filename."""
        return self._cfg.get("reminder", {}).get("launchd_label", "com.brain.reminder")

    @property
    def reminder_checklist_template(self) -> str:
        """
        Markdown checklist template for daily reminder file.
        Placeholders injected at runtime: {date}, {timestamp}, {yesterday_status}
        """
        return self._cfg.get("reminder", {}).get("checklist_template", "")

    # ── Phase 3 — Query / RAG settings ───────────────────────────────────────

    @property
    def rag_top_k(self) -> int:
        """Number of chunks to retrieve from ChromaDB per query."""
        return self._cfg.get("query", {}).get("top_k", 5)

    @property
    def rag_max_context_chunks(self) -> int:
        """Hard cap on chunks sent to LLM — protects context window."""
        return self._cfg.get("query", {}).get("max_context_chunks", 8)

    @property
    def rag_default_scope(self) -> str:
        """Default search scope when --scope is not specified."""
        return self._cfg.get("query", {}).get("default_scope", "all")

    @property
    def rag_similarity_threshold(self) -> float:
        """
        ChromaDB L2 distance cutoff.
        Chunks with distance > this are dropped before reaching the LLM.
        0.0 = identical, 2.0 = completely unrelated. 1.4 is a good default.
        """
        return self._cfg.get("query", {}).get("similarity_threshold", 1.4)

    @property
    def rag_max_summarize_days(self) -> int:
        """Max days allowed in a single summarize call — prevents context blowout."""
        return self._cfg.get("query", {}).get("max_summarize_days", 90)

    @property
    def rag_system_prompt(self) -> str:
        """System prompt used for brain query (RAG answer mode)."""
        return self._cfg.get("query", {}).get(
            "system_prompt",
            (
                "You are a personal knowledge assistant. "
                "Answer questions based only on the provided context. "
                "If the context does not contain enough information, say so clearly."
            ),
        )

    @property
    def rag_summarize_system_prompt(self) -> str:
        """System prompt used for brain summarize (date range summary mode)."""
        return self._cfg.get("query", {}).get(
            "summarize_system_prompt",
            (
                "You are a personal knowledge assistant. "
                "Summarize the user's activity across the provided date range. "
                "Highlight recurring themes, blockers, and achievements. "
                "Format output as clean structured markdown."
            ),
        )

    @property
    def query_sqlite_trigger_phrases(self) -> list[str]:
        """
        Phrases that signal a structured/aggregate question → Pattern A routing.
        Loaded from config.yaml query.sqlite_trigger_phrases.
        """
        return self._cfg.get("query", {}).get(
            "sqlite_trigger_phrases",
            [
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
            ],
        )

    # ── Source directory paths — used by reindex script ───────────────────────
    @property
    def daily_public_path(self) -> Path:
        """Path to daily/public/ — git-tracked public summaries."""
        return self._root / self._cfg["paths"]["daily_public"]

    @property
    def daily_sensitive_path(self) -> Path:
        """Path to daily/sensitive/ — git-ignored sensitive summaries."""
        return self._root / self._cfg["paths"]["daily_sensitive"]

    @property
    def blogs_public_path(self) -> Path:
        """Path to blogs/public/ — git-tracked public blog posts."""
        return self._root / self._cfg["paths"]["blogs_public"]

    @property
    def blogs_sensitive_path(self) -> Path:
        """Path to blogs/sensitive/ — git-ignored sensitive blog posts."""
        return self._root / self._cfg["paths"]["blogs_sensitive"]

    # ── Dump ──────────────────────────────────────────────────────────────────────

    @property
    def dump_input_glob(self) -> str:
        """Glob pattern for daily input files. e.g. daily_*.md"""
        return self._cfg.get("dump", {}).get("input_glob", "daily_*.md")

    @property
    def dump_task_keywords(self) -> list[str]:
        """Lowercase keywords that classify an H2 section as a Task."""
        return (
            self._cfg.get("dump", {})
            .get("section_types", {})
            .get("task_keywords", ["task", "ticket", "fix", "bug"])
        )

    @property
    def dump_pr_keywords(self) -> list[str]:
        """Lowercase keywords that classify an H2 section as a PR Review."""
        return (
            self._cfg.get("dump", {})
            .get("section_types", {})
            .get("pr_keywords", ["pr", "pull request", "review"])
        )

    @property
    def dump_meeting_keywords(self) -> list[str]:
        """Lowercase keywords that classify an H2 section as a Meeting."""
        return (
            self._cfg.get("dump", {})
            .get("section_types", {})
            .get("meeting_keywords", ["meet", "standup", "sync", "1:1", "call"])
        )

    @property
    def dump_pr_merge_file(self) -> str:
        """Filename for the merged PR review output."""
        return self._cfg.get("dump", {}).get("pr_merge_file", "pr-reviews.md")

    @property
    def dump_output_prefixes(self) -> dict[str, str]:
        """Output filename prefixes per section type."""
        return self._cfg.get("dump", {}).get(
            "output_prefixes",
            {
                "task": "task-",
                "pr": "pr-reviews",
                "meeting": "meet-",
                "other": "misc-",
            },
        )

    @property
    def dump_meeting_skeleton_template(self) -> str:
        """Meeting skeleton markdown template."""
        return self._cfg.get("dump", {}).get("meeting_skeleton_template", "")

    @property
    def dump_task_file_template(self) -> str:
        """Task output markdown template."""
        return self._cfg.get("dump", {}).get("task_file_template", "")

    @property
    def dump_other_file_template(self) -> str:
        """Catch-all output markdown template."""
        return self._cfg.get("dump", {}).get("other_file_template", "")

    def dump_inputs_path(self, date: str) -> Path:
        """Path to the inputs folder for a given date. e.g. inputs/2026-04-02/"""
        return self.inputs / date

    # ── MCP — External integrations ───────────────────────────────────────────────
    @property
    def mcp_jira_enabled(self) -> bool:
        """Whether Jira MCP integration is enabled."""
        return self._cfg.get("mcp", {}).get("jira", {}).get("enabled", False)

    @property
    def mcp_jira_server_url(self) -> str:
        """Atlassian MCP server URL for Jira."""
        return (
            self._cfg.get("mcp", {})
            .get("jira", {})
            .get("server_url", "https://mcp.atlassian.com/mcp")
        )

    @property
    def mcp_jira_ticket_pattern(self) -> str:
        """Regex pattern for detecting Jira ticket IDs."""
        return (
            self._cfg.get("mcp", {})
            .get("jira", {})
            .get("ticket_id_pattern", r"[A-Z]{2,9}-\d+")
        )

    @property
    def mcp_confluence_enabled(self) -> bool:
        """Whether Confluence MCP integration is enabled."""
        return self._cfg.get("mcp", {}).get("confluence", {}).get("enabled", False)

    @property
    def mcp_confluence_server_url(self) -> str:
        """Atlassian MCP server URL for Confluence."""
        return (
            self._cfg.get("mcp", {})
            .get("confluence", {})
            .get("server_url", "https://mcp.atlassian.com/mcp")
        )

    @property
    def mcp_task_brain_context_top_k(self) -> int:
        """Max ChromaDB chunks for task enrichment brain context."""
        return self._cfg.get("mcp", {}).get("task", {}).get("brain_context_top_k", 5)

    @property
    def mcp_task_brain_context_max_rows(self) -> int:
        """Max SQLite rows for task enrichment brain context."""
        return (
            self._cfg.get("mcp", {}).get("task", {}).get("brain_context_max_rows", 10)
        )

    @property
    def mcp_task_ingest_filename_template(self) -> str:
        """Filename template for --ingest output. Placeholder: {ticket_id}"""
        return (
            self._cfg.get("mcp", {})
            .get("task", {})
            .get("ingest_filename_template", "task-{ticket_id}.md")
        )

    @property
    def mcp_task_brief_prompt_template(self) -> str:
        """LLM prompt for assembling the task brief."""
        return self._cfg.get("mcp", {}).get("task", {}).get("brief_prompt_template", "")

    # ── GitHub ────────────────────────────────────────────────────────────────────

    @property
    def github_username(self) -> str:
        """GitHub username for filtering PR comments. Set in config.local.yaml."""
        return self._cfg.get("github", {}).get("username", "")

    @property
    def github_mcp_enabled(self) -> bool:
        """Whether GitHub MCP integration is enabled (Phase 6b)."""
        return self._cfg.get("github", {}).get("mcp", {}).get("enabled", False)

    @property
    def github_mcp_server_url(self) -> str:
        """GitHub MCP server URL."""
        return (
            self._cfg.get("github", {})
            .get("mcp", {})
            .get("server_url", "https://api.githubcopilot.com/mcp/")
        )

    # ── Enricher ──────────────────────────────────────────────────────────────────

    @property
    def enricher_output_dir(self) -> str:
        """Subdirectory name for enriched output. e.g. 'enriched'"""
        return self._cfg.get("enricher", {}).get("output_dir", "enriched")

    @property
    def enricher_skip_prefixes(self) -> list[str]:
        """File prefixes to skip during enrichment."""
        return self._cfg.get("enricher", {}).get("skip_prefixes", ["daily_"])

    @property
    def enricher_meeting_unfilled_marker(self) -> str:
        """Text that signals an unfilled meeting skeleton."""
        return self._cfg.get("enricher", {}).get(
            "meeting_unfilled_marker", "add your notes here"
        )

    @property
    def enricher_external_docs_note(self) -> str:
        """Note template for flagging external docs in enriched output."""
        return self._cfg.get("enricher", {}).get("external_docs_note", "")

    @property
    def enricher_pr_comments_src_dir(self) -> Path:
        """Source directory where external PR comment script writes output."""
        raw = (
            self._cfg.get("enricher", {})
            .get("pr_comments", {})
            .get("src_dir", "~/.brain/pr-comments")
        )
        return Path(raw).expanduser()

    @property
    def enricher_pr_comments_filename_pattern(self) -> str:
        """Filename pattern for PR comment files. Placeholder: {pr_number}"""
        return (
            self._cfg.get("enricher", {})
            .get("pr_comments", {})
            .get("filename_pattern", "pr-{pr_number}-comments.md")
        )

    @property
    def enricher_pr_external_command(self) -> str:
        """Command string to print for PR comment fetching. Placeholder: {pr_number}"""
        return (
            self._cfg.get("enricher", {})
            .get("pr_comments", {})
            .get("external_command", "python scripts/get_pr_comments.py {pr_number}")
        )

    @property
    def enricher_task_jira_prompt_template(self) -> str:
        """LLM prompt for task enrichment with Jira context."""
        return self._cfg.get("enricher", {}).get("task_jira_prompt_template", "")

    @property
    def enricher_task_llm_prompt_template(self) -> str:
        """LLM prompt for task enrichment without Jira context."""
        return self._cfg.get("enricher", {}).get("task_llm_prompt_template", "")

    @property
    def enricher_pr_llm_prompt_template(self) -> str:
        """LLM prompt for PR review enrichment."""
        return self._cfg.get("enricher", {}).get("pr_llm_prompt_template", "")

    @property
    def enricher_jira_src_dir(self) -> Path:
        """Where external Jira script writes JSON output."""
        raw = (
            self._cfg.get("enricher", {})
            .get("jira_script", {})
            .get("src_dir", "~/.brain/jira-context")
        )
        return Path(raw).expanduser()

    @property
    def enricher_jira_filename_pattern(self) -> str:
        """Filename pattern for Jira JSON files. Placeholder: {ticket_id}"""
        return (
            self._cfg.get("enricher", {})
            .get("jira_script", {})
            .get("filename_pattern", "jira-{ticket_id}.json")
        )

    @property
    def enricher_jira_external_command(self) -> str:
        """Command to print for Jira context fetching. Placeholder: {ticket_id}"""
        return (
            self._cfg.get("enricher", {})
            .get("jira_script", {})
            .get("external_command", "python scripts/get_jira_ticket.py {ticket_id}")
        )

    @property
    def enricher_context_dir(self) -> str:
        """Subdirectory name for pre-fetched context files. e.g. 'context'"""
        return self._cfg.get("enricher", {}).get("context_dir", "context")

    def enricher_context_path(self, date: str) -> Path:
        """Full path to context/ directory for a given date."""
        return self.inputs / date / self.enricher_context_dir

    def enricher_output_path(self, date: str) -> Path:
        """Full path to enriched output directory for a given date."""
        return self.inputs / date / self.enricher_output_dir

    # ── Calendar ──────────────────────────────────────────────────────────────────

    @property
    def calendar_mcp_enabled(self) -> bool:
        """Whether Google Calendar MCP integration is enabled."""
        return self._cfg.get("calendar", {}).get("mcp", {}).get("enabled", False)

    @property
    def calendar_mcp_server_url(self) -> str:
        """Google Calendar MCP server URL."""
        return (
            self._cfg.get("calendar", {})
            .get("mcp", {})
            .get("server_url", "https://gcal.mcp.claude.com/mcp")
        )

    @property
    def calendar_skip_all_day(self) -> bool:
        """Skip all-day events (holidays, OOO markers)."""
        return (
            self._cfg.get("calendar", {})
            .get("filters", {})
            .get("skip_all_day_events", True)
        )

    @property
    def calendar_min_duration_minutes(self) -> int:
        """Minimum event duration to include. 0 = include all."""
        return (
            self._cfg.get("calendar", {})
            .get("filters", {})
            .get("min_duration_minutes", 0)
        )

    @property
    def calendar_accepted_only(self) -> bool:
        """Only include events where user has accepted the invite."""
        return (
            self._cfg.get("calendar", {}).get("filters", {}).get("accepted_only", False)
        )

    @property
    def calendar_output_filename(self) -> str:
        """Output filename for meeting scaffold. Must match daily_*.md glob."""
        return self._cfg.get("calendar", {}).get("output_filename", "daily_meetings.md")

    @property
    def calendar_schedule_hour(self) -> int:
        """Hour to trigger the calendar pull (24h format)."""
        return self._cfg.get("calendar", {}).get("schedule", {}).get("hour", 9)

    @property
    def calendar_schedule_minute(self) -> int:
        """Minute to trigger the calendar pull."""
        return self._cfg.get("calendar", {}).get("schedule", {}).get("minute", 0)

    @property
    def calendar_launchd_label(self) -> str:
        """Launchd plist label for the calendar job."""
        return (
            self._cfg.get("calendar", {})
            .get("schedule", {})
            .get("launchd_label", "com.brain.calendar")
        )

    @property
    def calendar_meeting_template(self) -> str:
        """Per-event markdown template. Placeholders: title, start, end,
        calendar_name, attendees, location_line."""
        return self._cfg.get("calendar", {}).get("meeting_template", "")

    def calendar_output_path(self, date: str) -> Path:
        """Full path to daily_meetings.md for a given date."""
        return self.inputs / date / self.calendar_output_filename

    # ── Update Suggester — Phase 6b ───────────────────────────────────────────────

    @property
    def updates_output_dir(self) -> Path:
        """Root directory for all update draft files."""
        return ROOT / self._cfg["updates"]["output_dir"]

    def updates_draft_path(self, date: str, ticket_id: str) -> Path:
        """Full path for a draft update file."""
        filename = self._cfg["updates"]["draft_filename_pattern"].format(
            date=date,
            ticket_id=ticket_id.upper(),
        )
        return self.updates_output_dir / filename

    def updates_approved_path(self, date: str, ticket_id: str) -> Path:
        """Full path for an approved update file."""
        filename = self._cfg["updates"]["approved_filename_pattern"].format(
            date=date,
            ticket_id=ticket_id.upper(),
        )
        return self.updates_output_dir / filename

    def updates_rejected_path(self, date: str, ticket_id: str) -> Path:
        """Full path for a rejected update file."""
        filename = self._cfg["updates"]["rejected_filename_pattern"].format(
            date=date,
            ticket_id=ticket_id.upper(),
        )
        return self.updates_output_dir / filename

    @property
    def updates_ticket_pattern(self) -> str:
        """Regex pattern for detecting Jira ticket IDs."""
        return self._cfg["updates"]["ticket_id_pattern"]

    @property
    def updates_comment_prompt_template(self) -> str:
        """LLM prompt template for drafting Jira comments."""
        return self._cfg["updates"]["comment_prompt_template"]

    # ── Enrich — Phase 7a ─────────────────────────────────────────────────────────

    @property
    def enrich_remote_host(self) -> str:
        """SSH config alias for remote enrichment server."""
        return self._cfg.get("enrich", {}).get("remote", {}).get("host", "workstation")

    @property
    def enrich_remote_brain_dir(self) -> str:
        """Path to brain project on remote server."""
        return self._cfg.get("enrich", {}).get("remote", {}).get("brain_dir", "~/brain")

    @property
    def enrich_upload_exclude(self) -> list[str]:
        """Directory names to exclude from upload to remote."""
        return self._cfg.get("enrich", {}).get("upload_exclude", ["enriched"])

    @property
    def enrich_ssh_timeout(self) -> int:
        """SSH timeout in seconds for remote enricher.py run."""
        return self._cfg.get("enrich", {}).get("ssh_timeout", 300)

    @property
    def enrich_stream_output(self) -> bool:
        """Stream remote stdout live to terminal."""
        return self._cfg.get("enrich", {}).get("stream_output", True)

    def enrich_local_inputs_path(self, date: str) -> Path:
        """Full path to local inputs/YYYY-MM-DD/"""
        return self.inputs / date

    def enrich_local_enriched_path(self, date: str) -> Path:
        """Full path to local inputs/YYYY-MM-DD/enriched/"""
        return self.inputs / date / self.enricher_output_dir

    def enrich_remote_inputs_path(self, date: str) -> str:
        """Full path to remote inputs/YYYY-MM-DD/ as string for scp/ssh."""
        return f"{self.enrich_remote_brain_dir}/inputs/{date}"

    def enrich_remote_enriched_path(self, date: str) -> str:
        """Full path to remote inputs/YYYY-MM-DD/enriched/ as string for scp."""
        return (
            f"{self.enrich_remote_brain_dir}/inputs/{date}/{self.enricher_output_dir}"
        )
