"""
chunker.py — Text chunking and embedding utilities for Brain.

Extracted from ingest.py to avoid circular imports between
ingest.py and blog_ingest.py — both pipelines share these utilities.

Consumers:
  - ingest.py      (daily summary pipeline)
  - blog_ingest.py (blog post pipeline)
"""

import ollama

from brain.config import BrainConfig

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
