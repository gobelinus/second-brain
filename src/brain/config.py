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
# Keeps calling code clean: config.ollama_model() not config["ollama"]["model"]


class BrainConfig:
    """
    Typed wrapper around raw config dict.
    Add accessors here as new modules need them.
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

    # ── Paths ─────────────────────────────────────────────────────────────────
    @property
    def public_daily(self) -> Path:
        return ROOT / self._cfg["paths"]["public_daily"]

    @property
    def sensitive_daily(self) -> Path:
        return ROOT / self._cfg["paths"]["sensitive_daily"]

    @property
    def public_exports(self) -> Path:
        return ROOT / self._cfg["paths"]["public_exports"]

    @property
    def sensitive_exports(self) -> Path:
        return ROOT / self._cfg["paths"]["sensitive_exports"]

    @property
    def chroma_store(self) -> Path:
        return ROOT / self._cfg["paths"]["chroma_store"]

    @property
    def sqlite_db(self) -> Path:
        return ROOT / self._cfg["paths"]["sqlite_db"]

    # ── Ingestion ─────────────────────────────────────────────────────────────
    @property
    def chunk_size(self) -> int:
        return self._cfg["ingestion"]["chunk_size"]

    @property
    def chunk_overlap(self) -> int:
        return self._cfg["ingestion"]["chunk_overlap"]

    # ── Metadata ──────────────────────────────────────────────────────────────
    @property
    def sentiment_options(self) -> list[str]:
        return self._cfg["metadata"]["sentiment_options"]

    # ── ChromaDB ──────────────────────────────────────────────────────────────
    @property
    def public_collection(self) -> str:
        return self._cfg["chroma"]["public_collection"]

    @property
    def sensitive_collection(self) -> str:
        return self._cfg["chroma"]["sensitive_collection"]

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
            return self.public_exports
        elif sensitivity == "sensitive":
            return self.sensitive_exports
        else:
            raise ValueError(
                f"Unknown sensitivity: '{sensitivity}'. Use 'public' or 'sensitive'."
            )
