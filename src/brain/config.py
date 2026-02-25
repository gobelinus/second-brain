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
