"""
blog_ingest.py — ChromaDB-only ingestion pipeline for blog posts.

Blog posts are semantic content — they go into ChromaDB for similarity search
but NOT into SQLite (no daily aggregation value for blog content).

Flow:
  1. Extract metadata via BlogStructurer (Ollama/Mistral)
  2. Chunk markdown content
  3. Embed chunks via nomic-embed-text
  4. Store in blog_public or blog_sensitive ChromaDB collection

Chunk IDs use slug (LLM-derived from title) to ensure stable,
human-readable identifiers:
  Format: {sensitivity}_{slug}_chunk_{index}
  e.g.  : public_my-aws-post_chunk_0
          sensitive_internal-notes_chunk_0

Idempotent — re-ingesting the same file deletes existing chunks
by slug before writing new ones.
"""

from pathlib import Path

import chromadb
from chromadb.config import Settings

from brain.blog_structurer import BlogStructurer
from brain.chunker import Embedder, TextChunker
from brain.config import BrainConfig


class BlogIngestPipeline:
    """
    Orchestrates ChromaDB-only ingestion for blog posts.
    Composes BlogStructurer + TextChunker + Embedder + ChromaDB.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()
        self.structurer = BlogStructurer(self.config)
        self.chunker = TextChunker(
            chunk_size=self.config.blog_chunk_size,
            chunk_overlap=self.config.blog_chunk_overlap,
        )
        self.embedder = Embedder(self.config)

        self.config.chroma_store.mkdir(parents=True, exist_ok=True)
        self.chroma = chromadb.PersistentClient(
            path=str(self.config.chroma_store),
            settings=Settings(anonymized_telemetry=False),
        )

    def run(self, file_path: Path, sensitivity: str) -> dict:
        """
        Run full blog ingestion for a single markdown file.

        Args:
            file_path:   Path to the .md blog post
            sensitivity: 'public' or 'sensitive'

        Returns:
            metadata dict for confirmation/display
        """
        file_path = Path(file_path)

        print("\n🧠 Brain Blog Ingestion")
        print(f"   File       : {file_path}")
        print(f"   Sensitivity: {sensitivity}")
        print()

        # ── Step 1: Extract metadata ──────────────────────────────────────────
        print("  [1/3] Extracting blog metadata via LLM...")
        metadata = self.structurer.extract(file_path, sensitivity)
        print(f"        Title  : {metadata['title']}")
        print(f"        Slug   : {metadata['slug']}")
        print(f"        Status : {metadata['status']}")
        print(f"        Tags   : {metadata['tags']}")
        if metadata["date"]:
            print(f"        Date   : {metadata['date']}")
        if metadata["url"]:
            print(f"        URL    : {metadata['url']}")

        # ── Step 2: Chunk content ─────────────────────────────────────────────
        print("  [2/3] Chunking content...")
        content = file_path.read_text(encoding="utf-8")
        chunks = self.chunker.chunk(content)
        print(f"        {len(chunks)} chunk(s) created")

        # ── Step 3: Embed + store in ChromaDB ─────────────────────────────────
        print("  [3/3] Embedding and storing in ChromaDB...")
        self._delete_by_slug(metadata["slug"], sensitivity)
        embeddings = self.embedder.embed_chunks(chunks)
        self._upsert_chunks(chunks, embeddings, metadata)
        collection_name = self.config.blog_collection_for(sensitivity)
        print(f"        {len(chunks)} chunk(s) stored in '{collection_name}'")

        print()
        print("  ✅ Blog ingestion complete")
        return metadata

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _delete_by_slug(self, slug: str, sensitivity: str) -> None:
        """
        Remove all existing chunks for this slug + sensitivity before re-ingesting.
        Scoped by sensitivity to avoid touching the other collection.
        """
        collection_name = self.config.blog_collection_for(sensitivity)
        try:
            collection = self.chroma.get_collection(name=collection_name)
            collection.delete(where={"slug": slug, "sensitivity": sensitivity})
        except Exception:
            pass  # Collection doesn't exist yet — fine on first ingest

    def _upsert_chunks(
        self,
        chunks: list[str],
        embeddings: list[list[float]],
        metadata: dict,
    ) -> None:
        """
        Store blog chunks + embeddings in the correct ChromaDB collection.

        Chunk IDs use slug for human-readable, stable identifiers:
          {sensitivity}_{slug}_chunk_{index}
        """
        collection_name = self.config.blog_collection_for(metadata["sensitivity"])
        collection = self.chroma.get_or_create_collection(name=collection_name)

        # ChromaDB metadata values must be str/int/float — no lists, no None
        chunk_metadatas = [
            {
                "type": "blog",
                "title": metadata["title"],
                "slug": metadata["slug"],
                "date": metadata["date"] or "",
                "tags": ", ".join(metadata["tags"]),  # list → comma-joined str
                "status": metadata["status"],
                "url": metadata["url"] or "",
                "sensitivity": metadata["sensitivity"],
                "raw_file": metadata["raw_file"],
                "chunk_index": i,
            }
            for i in range(len(chunks))
        ]

        ids = [
            f"{metadata['sensitivity']}_{metadata['slug']}_chunk_{i}"
            for i in range(len(chunks))
        ]

        collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=chunk_metadatas,
        )
