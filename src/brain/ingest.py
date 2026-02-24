"""
ingest.py — Central ingestion pipeline for Brain.

Orchestrates the full ingestion flow for a single daily summary:
  1. Validate file + sensitivity
  2. Extract metadata via Structurer (Ollama/Mistral)
  3. Store metadata in SQLite
  4. Chunk markdown content
  5. Embed chunks via nomic-embed-text
  6. Store chunks + embeddings in ChromaDB

Idempotent — re-ingesting the same file updates existing records
rather than creating duplicates.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import chromadb
from chromadb.config import Settings
import ollama

from brain.config import BrainConfig
from brain.structurer import Structurer


# ── Text Chunker ──────────────────────────────────────────────────────────────

class TextChunker:
    """
    Splits markdown text into overlapping chunks for embedding.
    Simple word-count based chunking — no heavy dependencies needed.
    """

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, text: str) -> list[str]:
        """
        Split text into chunks of ~chunk_size words with overlap.
        Returns list of chunk strings.
        """
        words = text.split()
        if not words:
            return []

        chunks = []
        start = 0

        while start < len(words):
            end = start + self.chunk_size
            chunk = " ".join(words[start:end])
            chunks.append(chunk)

            # Advance by chunk_size minus overlap
            start += self.chunk_size - self.chunk_overlap

            # Avoid infinite loop on very small texts
            if self.chunk_size <= self.chunk_overlap:
                break

        return chunks


# ── SQLite Store ──────────────────────────────────────────────────────────────

class SQLiteStore:
    """
    Manages the SQLite metadata store.
    Handles schema creation and upsert operations.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summaries (
                    date         TEXT PRIMARY KEY,
                    sensitivity  TEXT NOT NULL,
                    projects     TEXT NOT NULL DEFAULT '[]',
                    blockers     TEXT NOT NULL DEFAULT '[]',
                    people       TEXT NOT NULL DEFAULT '[]',
                    sentiment    TEXT NOT NULL DEFAULT 'neutral',
                    focus_areas  TEXT NOT NULL DEFAULT '[]',
                    raw_file     TEXT NOT NULL,
                    ingested_at  TEXT NOT NULL
                )
            """)
            conn.commit()

    def upsert(self, metadata: dict) -> None:
        """
        Insert or replace a metadata record.
        Re-ingesting the same date cleanly overwrites the old record.
        """
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_summaries
                    (date, sensitivity, projects, blockers, people,
                     sentiment, focus_areas, raw_file, ingested_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metadata["date"],
                metadata["sensitivity"],
                json.dumps(metadata["projects"]),
                json.dumps(metadata["blockers"]),
                json.dumps(metadata["people"]),
                metadata["sentiment"],
                json.dumps(metadata["focus_areas"]),
                metadata["raw_file"],
                datetime.now().isoformat(),
            ))
            conn.commit()

    def date_exists(self, date: str) -> bool:
        """Check if a date has already been ingested."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM daily_summaries WHERE date = ?", (date,)
            ).fetchone()
            return row is not None

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)


# ── ChromaDB Store ────────────────────────────────────────────────────────────

class ChromaStore:
    """
    Manages ChromaDB vector store operations.
    Maintains separate collections for public and sensitive content.
    """

    def __init__(self, chroma_path: Path, config: BrainConfig):
        self.config = config
        chroma_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(chroma_path),
            settings=Settings(anonymized_telemetry=False),
        )

    def upsert_chunks(
        self,
        chunks: list[str],
        embeddings: list[list[float]],
        metadata: dict,
    ) -> None:
        """
        Store chunks + embeddings in the correct collection.
        Uses date + chunk index as document ID — safe to re-ingest.
        """
        collection_name = self.config.chroma_collection_for(metadata["sensitivity"])
        collection = self.client.get_or_create_collection(name=collection_name)

        # Build per-chunk metadata (ChromaDB stores metadata per document)
        chunk_metadatas = [
            {
                "date": metadata["date"],
                "sensitivity": metadata["sensitivity"],
                "sentiment": metadata["sentiment"],
                "raw_file": metadata["raw_file"],
                "chunk_index": i,
                # Store as comma-separated strings — ChromaDB metadata is flat
                "projects": ", ".join(metadata["projects"]),
                "focus_areas": ", ".join(metadata["focus_areas"]),
            }
            for i in range(len(chunks))
        ]

        # IDs are deterministic — re-ingesting same file overwrites cleanly
        ids = [f"{metadata['date']}_chunk_{i}" for i in range(len(chunks))]

        collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=chunk_metadatas,
        )

    def delete_by_date(self, date: str, sensitivity: str) -> None:
        """Remove all chunks for a given date (used before re-ingestion)."""
        collection_name = self.config.chroma_collection_for(sensitivity)
        try:
            collection = self.client.get_collection(name=collection_name)
            collection.delete(where={"date": date})
        except Exception:
            pass  # Collection doesn't exist yet — that's fine


# ── Embedder ──────────────────────────────────────────────────────────────────

class Embedder:
    """
    Generates embeddings via local Ollama nomic-embed-text model.
    """

    def __init__(self, config: BrainConfig):
        self.model = config.ollama_embed_model

    def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        """
        Embed a list of text chunks.
        Returns a list of embedding vectors in the same order.
        """
        embeddings = []
        for chunk in chunks:
            response = ollama.embeddings(model=self.model, prompt=chunk)
            embeddings.append(response["embedding"])
        return embeddings


# ── Ingestion Pipeline ────────────────────────────────────────────────────────

class IngestPipeline:
    """
    Orchestrates the full ingestion flow.
    Composes Structurer + Chunker + Embedder + SQLiteStore + ChromaStore.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()
        self.structurer = Structurer(self.config)
        self.chunker = TextChunker(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        self.embedder = Embedder(self.config)
        self.sqlite = SQLiteStore(self.config.sqlite_db)
        self.chroma = ChromaStore(self.config.chroma_store, self.config)

    def run(self, file_path: Path, sensitivity: str) -> dict:
        """
        Run full ingestion for a single markdown file.

        Args:
            file_path:   Path to the .md daily summary
            sensitivity: 'public' or 'sensitive'

        Returns:
            metadata dict for confirmation/display
        """
        file_path = Path(file_path)

        print(f"\n🧠 Brain Ingestion")
        print(f"   File       : {file_path}")
        print(f"   Sensitivity: {sensitivity}")
        print()

        # ── Step 1: Extract metadata ──────────────────────────────────────────
        print("  [1/4] Extracting metadata via LLM...")
        metadata = self.structurer.extract(file_path, sensitivity)
        print(f"        Date      : {metadata['date']}")
        print(f"        Sentiment : {metadata['sentiment']}")
        print(f"        Projects  : {metadata['projects']}")
        print(f"        Blockers  : {len(metadata['blockers'])} found")

        # ── Step 2: Store metadata in SQLite ──────────────────────────────────
        print("  [2/4] Storing metadata in SQLite...")
        self.sqlite.upsert(metadata)
        action = "updated" if self.sqlite.date_exists(metadata["date"]) else "inserted"
        print(f"        Record {action} for date: {metadata['date']}")

        # ── Step 3: Chunk the markdown content ────────────────────────────────
        print("  [3/4] Chunking content...")
        content = file_path.read_text(encoding="utf-8")
        chunks = self.chunker.chunk(content)
        print(f"        {len(chunks)} chunk(s) created")

        # ── Step 4: Embed + store in ChromaDB ─────────────────────────────────
        print("  [4/4] Embedding and storing in ChromaDB...")
        # Clean previous embeddings for this date first (idempotent)
        self.chroma.delete_by_date(metadata["date"], sensitivity)
        embeddings = self.embedder.embed_chunks(chunks)
        self.chroma.upsert_chunks(chunks, embeddings, metadata)
        collection = self.config.chroma_collection_for(sensitivity)
        print(f"        {len(chunks)} chunk(s) stored in '{collection}'")

        print()
        print("  ✅ Ingestion complete")
        return metadata
