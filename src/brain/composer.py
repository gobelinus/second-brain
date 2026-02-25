"""
composer.py — Combine multiple daily input files into a single structured summary.

Reads all markdown/text files from inputs/YYYY-MM-DD/, combines them via a
local Ollama/Mistral LLM call, writes the result to daily/sensitive/YYYY-MM-DD.md,
and optionally archives the input files.

Flow:
  inputs/YYYY-MM-DD/
    ├── ai_summary.md
    ├── meeting_standup.md
    └── notes.md
          │
          ▼  (LLM combine)
  daily/sensitive/YYYY-MM-DD.md
          │
          ▼  (archive)
  inputs/archive/YYYY-MM-DD/

All settings (prompt template, extensions, archive behaviour) are driven
by config.yaml — no hardcoded values here.
"""

import re
import shutil
from pathlib import Path

import ollama

from brain.config import BrainConfig

# ── Composer ──────────────────────────────────────────────────────────────────


class Composer:
    """
    Combines multiple daily input files into a single structured
    sensitive summary using a local Ollama LLM call.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    def compose(self, date: str, dry_run: bool = False) -> Path:
        """
        Main entry point. Compose a structured summary for the given date.

        Args:
            date:    ISO date string — YYYY-MM-DD
            dry_run: If True, print LLM output but do not write or archive files.

        Returns:
            Path to the written output file (or would-be path on dry_run).

        Raises:
            FileNotFoundError: If inputs directory or files don't exist.
            ValueError:        If date format is invalid or no input files found.
        """
        self._validate_date(date)

        inputs_path = self.config.composer_inputs_path(date)
        output_path = self.config.composer_output_path(date)

        print("\n🧠 Brain Composer")
        print(f"   Date       : {date}")
        print(f"   Inputs dir : {inputs_path}")
        print(f"   Output     : {output_path}")
        print()

        # ── Step 1: Collect input files ───────────────────────────────────────
        print("  [1/5] Collecting input files...")
        input_files = self._collect_inputs(inputs_path)
        for f in input_files:
            print(f"        + {f.name}")

        # ── Step 2: Read and concatenate content ──────────────────────────────
        print("  [2/5] Reading input content...")
        combined_content = self._read_inputs(input_files)
        word_count = len(combined_content.split())
        print(f"        {len(input_files)} file(s), ~{word_count} words total")

        # ── Step 3: Call LLM to compose structured summary ────────────────────
        print("  [3/5] Composing summary via LLM...")
        composed = self._call_llm(date, combined_content)
        print(f"        Output: ~{len(composed.split())} words")

        # ── Step 4: Extract action items into todo.txt ────────────────────────
        print("  [4/5] Extracting action items...")
        todo_items = self._extract_todos(date, combined_content)
        if todo_items:
            print(f"        {len(todo_items)} action item(s) found")
        else:
            print("        No action items found")

        if dry_run:
            print()
            print("  [DRY RUN] Summary preview:")
            print("  " + "─" * 58)
            print(composed)
            print("  " + "─" * 58)
            if todo_items:
                print()
                print("  [DRY RUN] Action items preview:")
                print("  " + "─" * 58)
                for item in todo_items:
                    print(f"  {item}")
                print("  " + "─" * 58)
            print("  [DRY RUN] No files written or archived.")
            return output_path

        # ── Step 5: Write output + append todos + optionally archive ──────────
        print("  [5/5] Writing output files...")
        self._write_output(output_path, composed)
        print(f"        Summary  : {output_path}")

        if todo_items:
            todo_path = self._append_todos(date, todo_items)
            print(f"        Todos    : {todo_path} (+{len(todo_items)} items)")

        if self.config.composer_archive_after_compose:
            archive_path = self.config.composer_archive_path(date)
            self._archive_inputs(inputs_path, archive_path)
            print(f"        Archived : {archive_path}")

        print()
        print("  ✅ Compose complete")
        print(f"     Next: brain ingest --file {output_path} --sensitivity sensitive")
        return output_path

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _validate_date(self, date: str) -> None:
        """Fail fast if date is not in YYYY-MM-DD format."""
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            raise ValueError(f"Invalid date format: '{date}'. Expected YYYY-MM-DD.")

    def _collect_inputs(self, inputs_path: Path) -> list[Path]:
        """
        Collect all input files from the inputs directory.
        Filters by configured extensions. Sorts for deterministic ordering.

        Raises:
            FileNotFoundError: If the inputs directory doesn't exist.
            ValueError:        If no files with accepted extensions are found.
        """
        if not inputs_path.exists():
            raise FileNotFoundError(
                f"Inputs directory not found: {inputs_path}\n"
                f"Create it and drop your notes there first:\n"
                f"  mkdir -p {inputs_path}"
            )

        allowed_extensions = self.config.composer_input_extensions
        files = sorted(
            [
                f
                for f in inputs_path.iterdir()
                if f.is_file() and f.suffix in allowed_extensions
            ]
        )

        if not files:
            raise ValueError(
                f"No input files found in {inputs_path}\n"
                f"Accepted extensions: {allowed_extensions}"
            )

        return files

    def _read_inputs(self, input_files: list[Path]) -> str:
        """
        Read and concatenate all input files.
        Each file is labelled with its filename as a section header
        so the LLM has provenance context.
        """
        sections = []
        for f in input_files:
            content = f.read_text(encoding="utf-8").strip()
            if content:
                sections.append(f"### Source: {f.name}\n\n{content}")

        if not sections:
            raise ValueError("All input files are empty — nothing to compose.")

        return "\n\n---\n\n".join(sections)

    def _call_llm(self, date: str, content: str) -> str:
        """
        Call local Ollama LLM with the compose prompt from config.
        Returns the composed markdown string.

        Raises:
            ValueError: If the LLM returns an empty response.
        """
        prompt_template = self.config.composer_prompt_template
        if not prompt_template:
            raise ValueError(
                "composer.prompt_template is empty in config.yaml. "
                "Cannot compose without a prompt template."
            )

        prompt = prompt_template.format(date=date, content=content)

        response = ollama.chat(
            model=self.config.ollama_llm_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": self.config.ollama_temperature},
        )

        result = response["message"]["content"].strip()

        if not result:
            raise ValueError(
                "LLM returned an empty response. "
                "Check that Ollama is running and the model is available:\n"
                f"  ollama run {self.config.ollama_llm_model}"
            )

        return result

    def _write_output(self, output_path: Path, content: str) -> None:
        """
        Write composed content to the output path.
        Creates parent directories if needed.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

    # ── New private method — add after _write_output() ────────────────────────────
    def _extract_todos(self, date: str, content: str) -> list[str]:
        """
        Call LLM to extract action items from input content in todo.txt format.
        Returns a list of todo.txt lines, empty list if none found or no template.

        Fails softly — a todo extraction failure should never block the summary.
        """
        prompt_template = self.config.composer_todo_prompt_template
        if not prompt_template:
            return []

        prompt = prompt_template.format(date=date, content=content)

        try:
            response = ollama.chat(
                model=self.config.ollama_llm_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0},
            )
            raw = response["message"]["content"].strip()

            # Parse into individual lines, filter blank lines and commentary
            lines = [
                line.strip()
                for line in raw.splitlines()
                if line.strip() and line.strip().startswith("(")
            ]
            return lines

        except Exception as e:
            print(f"  ⚠️  Todo extraction failed (non-blocking): {e}")
            return []

    def _append_todos(self, date: str, todo_items: list[str]) -> Path:
        """
        Append extracted todo items to the rolling todo.txt file.
        Adds a date separator comment before each day's items so the file
        remains readable and greppable.

        Format:
            # 2026-02-24
            (A) 2026-02-24 Fix hotfix approval +Anvil @review
            (B) 2026-02-24 Reduce Prod0 instances +Autoscaling @infra
        """
        todo_path = self.config.todo_txt
        todo_path.parent.mkdir(parents=True, exist_ok=True)

        # Build the block to append
        separator = f"\n# {date}\n"
        block = separator + "\n".join(todo_items) + "\n"

        with open(todo_path, "a", encoding="utf-8") as f:
            f.write(block)

        return todo_path

    def _archive_inputs(self, inputs_path: Path, archive_path: Path) -> None:
        """
        Move all files from inputs_path into archive_path.
        Creates archive directory if needed.
        Leaves the inputs_path directory itself in place (empty).

        Skips files that already exist in the archive rather than overwriting —
        prints a warning so nothing is silently lost.
        """
        archive_path.mkdir(parents=True, exist_ok=True)

        for f in inputs_path.iterdir():
            if not f.is_file():
                continue

            dest = archive_path / f.name

            if dest.exists():
                print(
                    f"  ⚠️  Archive target already exists, skipping: {dest.name}\n"
                    f"      Manually move or remove: {dest}"
                )
                continue

            shutil.move(str(f), str(dest))
