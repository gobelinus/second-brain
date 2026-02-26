"""
blog_structurer.py — Extract structured metadata from blog post markdown files.

Calls local Ollama/Mistral to parse a blog post and extract metadata.
Unlike daily summaries, blog posts have no fixed schema — the LLM infers
title, slug, tags, status, and url from content structure.

ChromaDB metadata schema per chunk:
{
    "type":        "blog",
    "title":       "Post title",
    "slug":        "post-title",
    "date":        "YYYY-MM-DD" | null,
    "tags":        "tag1, tag2, tag3",   (comma-joined for ChromaDB compat)
    "status":      "draft" | "published" | "internal",
    "url":         "https://..." | null,
    "sensitivity": "public" | "sensitive",
    "raw_file":    "blogs/public/my-post.md",
    "chunk_index": 0
}
"""

import json
import re
from pathlib import Path

import ollama

from brain.config import BrainConfig


class BlogStructurer:
    """
    Extracts structured metadata from a blog post markdown file
    using a local Ollama LLM call.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    def extract(self, file_path: Path, sensitivity: str) -> dict:
        """
        Main entry point. Extract metadata from a blog post file.

        Args:
            file_path:   Path to the .md blog post file
            sensitivity: "public" or "sensitive"

        Returns:
            dict with all metadata fields populated, ready for ChromaDB storage.

        Raises:
            FileNotFoundError: If file doesn't exist.
            ValueError:        If file is empty or LLM returns invalid response.
        """
        file_path = Path(file_path)
        self._validate_inputs(file_path, sensitivity)

        content = self._read_file(file_path)
        raw_metadata = self._call_llm(content)
        validated = self._validate_metadata(raw_metadata, file_path)

        return {
            "type": "blog",
            "title": validated["title"],
            "slug": validated["slug"],
            "date": validated["date"],
            "tags": validated["tags"],
            "status": validated["status"],
            "url": validated["url"],
            "sensitivity": sensitivity,
            "raw_file": str(file_path),
        }

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _validate_inputs(self, file_path: Path, sensitivity: str) -> None:
        """Fail fast on bad inputs before any LLM call."""
        if not file_path.exists():
            raise FileNotFoundError(f"Blog file not found: {file_path}")
        if file_path.suffix != ".md":
            raise ValueError(f"Expected a .md file, got: {file_path.suffix}")
        if sensitivity not in ("public", "sensitive"):
            raise ValueError(
                f"Invalid sensitivity '{sensitivity}'. Use 'public' or 'sensitive'."
            )

    def _read_file(self, file_path: Path) -> str:
        """Read markdown content, strip excessive whitespace."""
        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"Blog file is empty: {file_path}")
        return content

    def _call_llm(self, content: str) -> dict:
        """
        Call local Ollama LLM with the blog extraction prompt from config.
        Returns parsed JSON dict or raises on failure.
        """
        prompt_template = self.config.blog_extraction_prompt
        if not prompt_template:
            raise ValueError("blog.extraction_prompt is empty in config.yaml.")

        prompt = prompt_template.format(content=content)

        response = ollama.chat(
            model=self.config.ollama_llm_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0},  # deterministic extraction
        )

        raw_text = response["message"]["content"].strip()
        return self._parse_json(raw_text)

    def _parse_json(self, raw_text: str) -> dict:
        """
        Safely parse LLM JSON response.
        Handles markdown fences if model wraps output in them.
        """
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"LLM returned invalid JSON.\nError: {e}\nRaw response:\n{raw_text}"
            ) from e

    def _validate_metadata(self, raw: dict, file_path: Path) -> dict:
        """
        Validate and sanitise LLM output.
        Falls back to safe defaults rather than crashing.
        """
        valid_statuses = ("draft", "published", "internal")

        # Title — fall back to filename if LLM missed it
        title = raw.get("title", "").strip()
        if not title:
            title = file_path.stem.replace("-", " ").replace("_", " ").title()
            print(f"  ⚠️  Could not extract title — using filename: '{title}'")

        # Slug — derive from title if LLM missed it or returned empty
        slug = raw.get("slug", "").strip()
        if not slug:
            slug = self._slugify(title)
            print(f"  ⚠️  Could not extract slug — derived from title: '{slug}'")

        # Date — null is acceptable for blog posts
        date = raw.get("date")
        if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date)):
            print(f"  ⚠️  Invalid date format '{date}' — setting to null")
            date = None

        # Tags — must be a list of strings
        tags = raw.get("tags", [])
        if not isinstance(tags, list):
            print("  ⚠️  Tags was not a list — resetting to []")
            tags = []
        # Normalise: lowercase, strip whitespace
        tags = [t.lower().strip() for t in tags if isinstance(t, str) and t.strip()]

        # Status
        status = raw.get("status", "draft").lower().strip()
        if status not in valid_statuses:
            print(f"  ⚠️  Invalid status '{status}' — defaulting to 'draft'")
            status = "draft"

        # URL — null is fine
        url = raw.get("url")
        if url and not str(url).startswith("http"):
            print(f"  ⚠️  URL doesn't look valid '{url}' — setting to null")
            url = None

        return {
            "title": title,
            "slug": slug,
            "date": date,
            "tags": tags,
            "status": status,
            "url": url,
        }

    def _slugify(self, text: str) -> str:
        """Convert a title string to a url-friendly slug."""
        slug = text.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)  # remove special chars
        slug = re.sub(r"[\s_]+", "-", slug)  # spaces/underscores → hyphens
        slug = re.sub(r"-+", "-", slug)  # collapse multiple hyphens
        return slug.strip("-")
