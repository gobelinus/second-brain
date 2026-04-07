#!/usr/bin/env python3
"""
enricher.py — Standalone task enrichment script for Brain Phase 5b.

Runs on the remote Ubuntu server. Reads task-*.md, pr-reviews.md, and
pr-*-comments.md files from inputs/YYYY-MM-DD/, enriches each with
external context (Jira MCP, GitHub MCP) and local LLM assembly, then
writes structured output to inputs/YYYY-MM-DD/enriched/.

This script is intentionally standalone — it has no dependency on the
brain CLI. It only needs:
  - src/brain/config.py   (BrainConfig)
  - src/brain/mcp_client.py (MCPClient)
  - Ollama running locally on the remote server
  - config.yaml + config.local.yaml (same layered pattern as local)

Usage
─────
  python enricher.py --date 2026-04-02
  python enricher.py --date 2026-04-02 --dry-run
  python enricher.py --date 2026-04-02 --file task-cs-15011-hotfix.md
  python enricher.py --date 2026-04-02 --verbose

File Classification
───────────────────
  task-*.md  + <!-- jira:TICKET -->  →  Jira MCP + LLM brief
  task-*.md  (no jira tag)           →  LLM-only structuring
  pr-reviews.md                      →  LLM PR summary
                                        (combined with pr-*-comments.md)
  pr-*-comments.md                   →  Input for PR enrichment (not enriched directly)
  meet-*.md  (unfilled skeleton)     →  SKIP + warn
  daily_*.md                         →  SKIP (source files)
  misc-*.md                          →  LLM-only structuring

Output
──────
  inputs/YYYY-MM-DD/enriched/task-cs-15011-hotfix.md
  inputs/YYYY-MM-DD/enriched/task-ef-route53.md
  inputs/YYYY-MM-DD/enriched/pr-reviews.md

Privacy Boundary
────────────────
  External data (Jira, GitHub) is fetched via MCP.
  The local Ollama LLM assembles the brief on this machine.
  No private notes leave this server — assembly is always local.

Idempotency
───────────
  Existing enriched files are SKIPPED with a warning.
  Re-run safely — only missing files are processed.
  Use --force to overwrite existing enriched files.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import ollama

# ── Path setup — works whether run from project root or scripts/ ──────────────
# Adds src/ to sys.path so brain.config and brain.mcp_client import cleanly
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent if _SCRIPT_DIR.name == "scripts" else _SCRIPT_DIR
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from brain.config import BrainConfig  # noqa: E402
from brain.mcp_client import (  # noqa: E402
    GitHubPR,
    JiraTicket,
    MCPClient,
)

# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class EnrichmentResult:
    """
    Result of enriching a single file.

    Fields
    ──────
    source_file    : original filename e.g. "task-cs-15011-hotfix.md"
    output_file    : enriched filename e.g. "enriched/task-cs-15011-hotfix.md"
    enrichment_type: "task_jira" | "task_llm" | "pr" | "skipped" | "failed"
    jira_ids       : Jira ticket IDs fetched
    pr_numbers     : PR numbers enriched
    external_urls  : non-Jira/GitHub URLs found (surfaced for manual review)
    warning        : skip/fail reason if not enriched
    """

    source_file: str
    output_file: str = ""
    enrichment_type: str = ""
    jira_ids: list[str] = field(default_factory=list)
    pr_numbers: list[str] = field(default_factory=list)
    external_urls: list[str] = field(default_factory=list)
    warning: str = ""


@dataclass
class EnricherStats:
    """
    Summary stats for a full enricher run.
    """

    date: str
    total: int = 0
    enriched: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[EnrichmentResult] = field(default_factory=list)

    def record(self, result: EnrichmentResult) -> None:
        self.results.append(result)
        self.total += 1
        if result.enrichment_type == "skipped":
            self.skipped += 1
        elif result.enrichment_type == "failed":
            self.failed += 1
        else:
            self.enriched += 1


# ── Enricher ──────────────────────────────────────────────────────────────────


class Enricher:
    """
    Orchestrates enrichment of all eligible files for a given date.

    Routing logic:
      task-*.md + jira tag  → _enrich_task_jira()
      task-*.md (no tag)    → _enrich_task_llm()
      pr-reviews.md         → _enrich_pr()
      misc-*.md             → _enrich_task_llm()  (same LLM-only path)
      everything else       → skip

    All MCP calls fail gracefully — if Jira is unreachable, the task
    falls back to LLM-only enrichment automatically.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()
        self.mcp = MCPClient(self.config)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(
        self,
        date: str,
        dry_run: bool = False,
        force: bool = False,
        target_file: str | None = None,
        verbose: bool = False,
    ) -> EnricherStats:
        """
        Run enrichment for all eligible files on the given date.

        Args:
            date        : ISO date string YYYY-MM-DD
            dry_run     : print what would happen, write nothing
            force       : overwrite existing enriched files
            target_file : enrich only this specific filename (for debugging)
            verbose     : print LLM prompts and full responses

        Returns:
            EnricherStats with per-file results and summary counts
        """
        _validate_date(date)

        inputs_path = self.config.inputs / date
        output_path = self.config.enricher_output_path(date)

        print("\n🧠 Brain Enricher")
        print(f"   Date       : {date}")
        print(f"   Input dir  : {inputs_path}")
        print(f"   Output dir : {output_path}")
        print(f"   Dry run    : {dry_run}")
        print(f"   Force      : {force}")
        print()

        if not inputs_path.exists():
            raise FileNotFoundError(
                f"Inputs directory not found: {inputs_path}\n"
                f"scp the inputs directory from your local machine first."
            )

        stats = EnricherStats(date=date)

        # ── Collect eligible files ────────────────────────────────────────────
        files = self._collect_files(inputs_path, target_file)
        print(f"  Found {len(files)} eligible file(s) to process\n")

        # ── Collect PR comment files (inputs for PR enrichment) ───────────────
        pr_comment_files = sorted(inputs_path.glob("pr-*-comments.md"))

        # ── Process each file ─────────────────────────────────────────────────
        for file_path in files:
            print(f"  ── {file_path.name} {'─' * (50 - len(file_path.name))}")
            result = self._process_file(
                file_path=file_path,
                output_path=output_path,
                pr_comment_files=pr_comment_files,
                dry_run=dry_run,
                force=force,
                verbose=verbose,
            )
            stats.record(result)
            print()

        # ── Print summary ─────────────────────────────────────────────────────
        self._print_summary(stats, dry_run)
        return stats

    # ── File Collection ────────────────────────────────────────────────────────

    def _collect_files(
        self,
        inputs_path: Path,
        target_file: str | None,
    ) -> list[Path]:
        """
        Collect files eligible for enrichment.

        Excludes:
          - daily_*.md (source dump files)
          - pr-*-comments.md (input files, not enriched directly)
          - enriched/ subdirectory contents
          - Any file matching a skip prefix from config
        """
        skip_prefixes = self.config.enricher_skip_prefixes

        if target_file:
            path = inputs_path / target_file
            if not path.exists():
                raise FileNotFoundError(f"File not found: {path}")
            return [path]

        all_files = sorted(
            f
            for f in inputs_path.iterdir()
            if f.is_file()
            and f.suffix == ".md"
            # skip source dump files
            and not any(f.name.startswith(p) for p in skip_prefixes)
            # skip PR comment input files — they feed PR enrichment, not enriched directly
            and not re.match(r"^pr-\d+-comments\.md$", f.name)
            # skip enriched/ subdirectory (if somehow a file got placed there)
            and f.parent.name != self.config.enricher_output_dir
        )

        return all_files

    # ── File Routing ───────────────────────────────────────────────────────────

    def _process_file(
        self,
        file_path: Path,
        output_path: Path,
        pr_comment_files: list[Path],
        dry_run: bool,
        force: bool,
        verbose: bool,
    ) -> EnrichmentResult:
        """
        Route a single file to the correct enrichment path.
        Returns EnrichmentResult regardless of outcome.
        """
        content = file_path.read_text(encoding="utf-8")
        dest = output_path / file_path.name

        # ── Idempotency check ─────────────────────────────────────────────────
        if dest.exists() and not force:
            print(f"  ⚠️  Already enriched — skipping: {file_path.name}")
            print("       Use --force to overwrite")
            return EnrichmentResult(
                source_file=file_path.name,
                output_file=str(dest),
                enrichment_type="skipped",
                warning="Already exists — use --force to overwrite",
            )

        # ── Skip unfilled meeting skeletons ───────────────────────────────────
        marker = self.config.enricher_meeting_unfilled_marker
        if file_path.name.startswith("meet-") and marker in content:
            print(f"  ⚠️  Unfilled meeting skeleton — skipping: {file_path.name}")
            print("       Fill the skeleton before enriching")
            return EnrichmentResult(
                source_file=file_path.name,
                output_file="",
                enrichment_type="skipped",
                warning="Unfilled meeting skeleton — fill notes first",
            )

        # ── Route by filename and content ─────────────────────────────────────
        try:
            if file_path.name == "pr-reviews.md":
                return self._enrich_pr(
                    file_path=file_path,
                    content=content,
                    dest=dest,
                    pr_comment_files=pr_comment_files,
                    dry_run=dry_run,
                    verbose=verbose,
                )

            # task-*.md and misc-*.md — check for Jira tag
            jira_ids = _extract_jira_tags(content, self.config.mcp_jira_ticket_pattern)

            if jira_ids and self.config.mcp_jira_enabled:
                return self._enrich_task_jira(
                    file_path=file_path,
                    content=content,
                    dest=dest,
                    jira_ids=jira_ids,
                    dry_run=dry_run,
                    verbose=verbose,
                )
            else:
                return self._enrich_task_llm(
                    file_path=file_path,
                    content=content,
                    dest=dest,
                    jira_ids=jira_ids,
                    dry_run=dry_run,
                    verbose=verbose,
                )

        except Exception as e:
            print(f"  ❌ Enrichment failed: {e}")
            return EnrichmentResult(
                source_file=file_path.name,
                output_file="",
                enrichment_type="failed",
                warning=str(e),
            )

    # ── Enrichment Paths ───────────────────────────────────────────────────────

    def _enrich_task_jira(
        self,
        file_path: Path,
        content: str,
        dest: Path,
        jira_ids: list[str],
        dry_run: bool,
        verbose: bool,
    ) -> EnrichmentResult:
        """
        Enrich a task file that has Jira ticket tags.

        Flow:
          1. Fetch each Jira ticket via MCP
          2. Detect external URLs in content (Confluence, Google Docs etc.)
          3. Call LLM with notes + Jira context → structured brief
          4. Write enriched file
        """
        print(f"  📋 Task + Jira: {', '.join(jira_ids)}")

        # ── Step 1: Fetch Jira tickets ────────────────────────────────────────
        jira_tickets: list[JiraTicket] = []
        for ticket_id in jira_ids:
            ticket = self.mcp.get_jira_ticket(ticket_id)
            if ticket:
                jira_tickets.append(ticket)
            else:
                print(f"       ⚠️  Jira unavailable for {ticket_id} — using LLM-only")

        # If all Jira fetches failed, fall back to LLM-only
        if not jira_tickets:
            print("       Falling back to LLM-only enrichment")
            return self._enrich_task_llm(
                file_path=file_path,
                content=content,
                dest=dest,
                jira_ids=jira_ids,
                dry_run=dry_run,
                verbose=verbose,
            )

        # ── Step 2: Detect external URLs ──────────────────────────────────────
        external_urls = _extract_external_urls(content, jira_ids)

        # ── Step 3: Assemble Jira context block ───────────────────────────────
        jira_context = "\n\n".join(t.to_context_block() for t in jira_tickets)

        # ── Step 4: Call LLM ──────────────────────────────────────────────────
        print("       Calling LLM for brief assembly...")
        title = _extract_title(content, file_path.name)
        date = file_path.parent.name

        template = self.config.enricher_task_jira_prompt_template
        if not template:
            raise ValueError(
                "enricher.task_jira_prompt_template is empty in config.yaml.\n"
                "Add the enricher: section to config.yaml — see Phase 5b Step 1."
            )
        prompt = template.format(
            title=title,
            date=date,
            original_notes=content,
            jira_context=jira_context,
        )
        brief = self._call_llm(prompt, verbose=verbose)

        # ── Step 5: Build enriched output ─────────────────────────────────────
        enriched = _build_task_output(
            title=title,
            date=date,
            source_file=file_path.name,
            enrichment_type="Jira MCP + LLM",
            jira_ids=jira_ids,
            original_notes=content,
            brief=brief,
            external_urls=external_urls,
            external_docs_note=self.config.enricher_external_docs_note,
        )

        _write_enriched(dest, enriched, dry_run)
        print(
            f"       {'[DRY RUN] Would write' if dry_run else '✅ Written'}: enriched/{dest.name}"
        )

        return EnrichmentResult(
            source_file=file_path.name,
            output_file=str(dest),
            enrichment_type="task_jira",
            jira_ids=jira_ids,
            external_urls=external_urls,
        )

    def _enrich_task_llm(
        self,
        file_path: Path,
        content: str,
        dest: Path,
        jira_ids: list[str],
        dry_run: bool,
        verbose: bool,
    ) -> EnrichmentResult:
        """
        Enrich a task file using LLM only — no external MCP context.

        Used when:
          - No Jira tag present
          - Jira MCP disabled
          - All Jira fetches failed (fallback from _enrich_task_jira)
          - misc-*.md catch-all files
        """
        reason = "no Jira tag" if not jira_ids else "Jira unavailable"
        print(f"  📋 Task (LLM-only — {reason})")

        external_urls = _extract_external_urls(content, jira_ids)
        title = _extract_title(content, file_path.name)
        date = file_path.parent.name

        print("       Calling LLM for structuring...")
        template = self.config.enricher_task_llm_prompt_template
        if not template:
            raise ValueError(
                "enricher.task_llm_prompt_template is empty in config.yaml.\n"
                "Add the enricher: section to config.yaml — see Phase 5b Step 1."
            )
        prompt = template.format(
            title=title,
            date=date,
            original_notes=content,
        )
        brief = self._call_llm(prompt, verbose=verbose)

        enriched = _build_task_output(
            title=title,
            date=date,
            source_file=file_path.name,
            enrichment_type="LLM-only",
            jira_ids=jira_ids,
            original_notes=content,
            brief=brief,
            external_urls=external_urls,
            external_docs_note=self.config.enricher_external_docs_note,
        )

        _write_enriched(dest, enriched, dry_run)
        print(
            f"       {'[DRY RUN] Would write' if dry_run else '✅ Written'}: enriched/{dest.name}"
        )

        return EnrichmentResult(
            source_file=file_path.name,
            output_file=str(dest),
            enrichment_type="task_llm",
            jira_ids=jira_ids,
            external_urls=external_urls,
        )

    def _enrich_pr(
        self,
        file_path: Path,
        content: str,
        dest: Path,
        pr_comment_files: list[Path],
        dry_run: bool,
        verbose: bool,
    ) -> EnrichmentResult:
        """
        Enrich pr-reviews.md using:
          1. PR comment files from external script (pr-*-comments.md)
          2. GitHub MCP metadata if enabled
          3. Local LLM summary

        The external PR comment files are the primary source of rich content.
        GitHub MCP adds structured metadata (status, verdict, files changed).
        LLM synthesises everything into a structured review summary.
        """
        print("  🔀 PR Review enrichment")
        date = file_path.parent.name

        # ── Step 1: Detect PR numbers in pr-reviews.md ────────────────────────
        pr_numbers = _extract_pr_numbers_from_content(content)
        if not pr_numbers:
            print("       ⚠️  No PR numbers detected in pr-reviews.md")
            print("       Proceeding with LLM-only summary of review notes")

        print(
            f"       PRs detected: {', '.join('#' + n for n in pr_numbers) or 'none'}"
        )

        # ── Step 2: Read PR comment files ─────────────────────────────────────
        pr_comment_blocks: list[str] = []
        matched_pr_numbers: list[str] = []

        for pr_num in pr_numbers:
            # Find matching comment file — pr-{number}-comments.md
            comment_file = file_path.parent / f"pr-{pr_num}-comments.md"
            if comment_file.exists():
                comment_content = comment_file.read_text(encoding="utf-8")
                pr_comment_blocks.append(
                    f"### PR #{pr_num} comment threads\n\n{comment_content}"
                )
                matched_pr_numbers.append(pr_num)
                print(f"       ✅ Loaded: pr-{pr_num}-comments.md")
            else:
                print(
                    f"       ⚠️  Missing: pr-{pr_num}-comments.md — run external script first"
                )

        # Also include any pr-*-comments.md files not matched by number detection
        # (catches edge case where PR number wasn't in pr-reviews.md text)
        for cf in pr_comment_files:
            num_match = re.search(r"pr-(\d+)-comments\.md", cf.name)
            if num_match and num_match.group(1) not in matched_pr_numbers:
                comment_content = cf.read_text(encoding="utf-8")
                pr_comment_blocks.append(
                    f"### PR #{num_match.group(1)} comment threads\n\n{comment_content}"
                )
                print(f"       ✅ Loaded (unmatched): {cf.name}")

        # ── Step 3: Fetch GitHub MCP metadata (optional) ──────────────────────
        github_prs: list[GitHubPR] = []
        if self.config.github_mcp_enabled:
            for pr_num in pr_numbers:
                # Try to extract repo from URL in content
                repo = _extract_repo_for_pr(content, pr_num)
                pr_meta = self.mcp.get_github_pr(pr_num, repo)
                if pr_meta:
                    github_prs.append(pr_meta)
        else:
            print("       ℹ️  GitHub MCP disabled — using comment files only")

        # ── Step 4: Assemble prompt ───────────────────────────────────────────
        pr_summaries = (
            "\n\n---\n\n".join(pr_comment_blocks)
            if pr_comment_blocks
            else "(no comment files found)"
        )

        # Add GitHub MCP metadata blocks if available
        if github_prs:
            github_context = "\n\n".join(pr.to_context_block() for pr in github_prs)
            pr_summaries = f"## GitHub PR Metadata\n\n{github_context}\n\n---\n\n## Comment Threads\n\n{pr_summaries}"

        print("       Calling LLM for PR summary...")
        template = self.config.enricher_pr_llm_prompt_template
        if not template:
            raise ValueError(
                "enricher.pr_llm_prompt_template is empty in config.yaml.\n"
                "Add the enricher: section to config.yaml — see Phase 5b Step 1."
            )

        prompt = template.format(
            date=date,
            review_notes=content,
            pr_summaries=pr_summaries,
        )
        brief = self._call_llm(prompt, verbose=verbose)

        # ── Step 5: Build enriched output ─────────────────────────────────────
        enriched = _build_pr_output(
            date=date,
            source_file=file_path.name,
            pr_numbers=pr_numbers,
            brief=brief,
            original_notes=content,
            github_enabled=self.config.github_mcp_enabled,
        )

        _write_enriched(dest, enriched, dry_run)
        print(
            f"       {'[DRY RUN] Would write' if dry_run else '✅ Written'}: enriched/{dest.name}"
        )

        return EnrichmentResult(
            source_file=file_path.name,
            output_file=str(dest),
            enrichment_type="pr",
            pr_numbers=pr_numbers,
        )

    # ── LLM ───────────────────────────────────────────────────────────────────

    def _call_llm(self, prompt: str, verbose: bool = False) -> str:
        """
        Call local Ollama with the enrichment prompt.
        Raises ValueError on empty response.
        """
        if verbose:
            print(f"\n       [PROMPT]\n{prompt[:500]}...\n")

        response = ollama.chat(
            model=self.config.ollama_llm_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": self.config.ollama_temperature},
        )

        result = response["message"]["content"].strip()

        if not result:
            raise ValueError(
                f"LLM returned empty response.\n"
                f"Check Ollama is running: ollama run {self.config.ollama_llm_model}"
            )

        if verbose:
            print(f"\n       [RESPONSE]\n{result[:500]}...\n")

        return result

    # ── Summary ────────────────────────────────────────────────────────────────

    def _print_summary(self, stats: EnricherStats, dry_run: bool) -> None:
        """Print enrichment run summary with next-step instructions."""
        label = " (DRY RUN)" if dry_run else ""
        print(f"  {'─' * 58}")
        print(f"  ✅ Enrichment complete{label}")
        print()
        print(f"     Total files : {stats.total}")
        print(f"     Enriched    : {stats.enriched}")
        print(f"     Skipped     : {stats.skipped}")
        print(f"     Failed      : {stats.failed}")
        print()

        # ── Per-file breakdown ────────────────────────────────────────────────
        if stats.results:
            print("     Per-file results:")
            for r in stats.results:
                icon = {
                    "task_jira": "📋",
                    "task_llm": "📋",
                    "pr": "🔀",
                    "skipped": "⏭️ ",
                    "failed": "❌",
                }.get(r.enrichment_type, "•")

                type_label = {
                    "task_jira": "Jira+LLM",
                    "task_llm": "LLM-only",
                    "pr": "PR+LLM",
                    "skipped": "skipped",
                    "failed": "FAILED",
                }.get(r.enrichment_type, r.enrichment_type)

                suffix = ""
                if r.jira_ids:
                    suffix += f" [{', '.join(r.jira_ids)}]"
                if r.pr_numbers:
                    suffix += f" [#{', #'.join(r.pr_numbers)}]"
                if r.warning:
                    suffix += f" — {r.warning}"

                print(f"       {icon} {r.source_file:<40} {type_label}{suffix}")

        # ── External URLs surfaced ────────────────────────────────────────────
        all_external = [
            (r.source_file, url) for r in stats.results for url in r.external_urls
        ]
        if all_external:
            print()
            print(
                f"     ⚠️  External docs mentioned ({len(all_external)}) — review manually:"
            )
            for source, url in all_external:
                print(f"       [{source}] {url}")

        # ── Failed files ──────────────────────────────────────────────────────
        failed = [r for r in stats.results if r.enrichment_type == "failed"]
        if failed:
            print()
            print(
                f"     ❌ Failed ({len(failed)}) — re-run individually with --verbose:"
            )
            for r in failed:
                print(
                    f"       python enricher.py --date {stats.date} --file {r.source_file} --verbose"
                )

        # ── Next steps ────────────────────────────────────────────────────────
        if not dry_run and stats.enriched > 0:
            print()
            print("  ─── Next Steps ───────────────────────────────────────────")
            print("  1️⃣  scp enriched/ back to local machine:")
            print(
                f"       scp -r remote:inputs/{stats.date}/enriched/ inputs/{stats.date}/"
            )
            print("  2️⃣  Run on local:")
            print(f"       brain compose --date {stats.date} --ingest")
            print("  ─────────────────────────────────────────────────────────")


# ── Output Builders ───────────────────────────────────────────────────────────


def _build_task_output(
    title: str,
    date: str,
    source_file: str,
    enrichment_type: str,
    jira_ids: list[str],
    original_notes: str,
    brief: str,
    external_urls: list[str],
    external_docs_note: str,
) -> str:
    """
    Build the enriched markdown content for a task file.

    Structure:
      Header block (metadata)
      Original Notes (preserved verbatim)
      ── LLM Brief ──
      External References (if any non-Jira/GitHub URLs found)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    jira_tag_line = ""
    if jira_ids:
        jira_tag_line = (
            "\n" + "\n".join(f"<!-- jira:{jid} -->" for jid in jira_ids) + "\n"
        )

    external_section = ""
    if external_urls:
        note = external_docs_note.strip()
        url_lines = "\n".join(f"> - {url}" for url in external_urls)
        external_section = f"\n\n## External References\n\n{note}\n{url_lines}"

    return f"""# {title} — {date}

> **Source:** {source_file}
> **Enriched:** {timestamp}
> **Method:** {enrichment_type}
{jira_tag_line}
---

## Original Notes

{original_notes.strip()}

---

{brief.strip()}
{external_section}
"""


def _build_pr_output(
    date: str,
    source_file: str,
    pr_numbers: list[str],
    brief: str,
    original_notes: str,
    github_enabled: bool,
) -> str:
    """
    Build the enriched markdown content for pr-reviews.md.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pr_refs = ", ".join(f"#{n}" for n in pr_numbers) if pr_numbers else "none detected"
    github_note = "GitHub MCP + " if github_enabled else ""

    return f"""# PR Review Summary — {date}

> **Source:** {source_file}
> **Enriched:** {timestamp}
> **Method:** {github_note}External comment threads + LLM
> **PRs:** {pr_refs}

---

## Original PR Notes

{original_notes.strip()}

---

{brief.strip()}
"""


# ── Module-Level Helpers ──────────────────────────────────────────────────────


def _validate_date(date: str) -> None:
    """Fail fast if date is not in YYYY-MM-DD format."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise ValueError(f"Invalid date format: '{date}'. Expected YYYY-MM-DD.")


def _extract_jira_tags(content: str, pattern: str) -> list[str]:
    """
    Extract Jira ticket IDs from <!-- jira:TICKET --> comment tags.

    Only reads the structured tags written by brain dump — not all
    Jira IDs in the body text. This is intentional: the <!-- jira: -->
    tag is an explicit signal that this task is linked to that ticket.

    Falls back to scanning the full content if no tags found — covers
    files written before the tag convention was established.
    """
    # Primary: structured tags from brain dump
    tagged = re.findall(r"<!--\s*jira:([A-Z]{2,9}-\d+)\s*-->", content)
    if tagged:
        return sorted(set(tagged))

    # Fallback: scan full content with Jira pattern
    try:
        found = re.findall(pattern, content)
        return sorted(set(found))
    except re.error:
        return []


def _extract_external_urls(content: str, jira_ids: list[str]) -> list[str]:
    """
    Extract non-Jira, non-GitHub URLs from content.

    These are surfaced in the enriched output as requiring manual review.
    Phase 6b will enrich Confluence and Google Doc URLs automatically.

    Excludes:
      - github.com URLs (handled by GitHub MCP)
      - atlassian.net/browse/* (Jira ticket URLs)
      - localhost, 127.0.0.1
    """
    all_urls = re.findall(
        r"https?://[^\s\)\]\>\"\']+",
        content,
    )

    external: list[str] = []
    seen: set[str] = set()

    for url in all_urls:
        url = url.rstrip(".,;)")

        # Skip Jira ticket browse URLs
        if "atlassian.net/browse/" in url:
            continue
        # Skip GitHub URLs (handled by MCP or comment script)
        if "github.com" in url:
            continue
        # Skip localhost
        if "localhost" in url or "127.0.0.1" in url:
            continue
        # Deduplicate
        if url in seen:
            continue

        seen.add(url)
        external.append(url)

    return external


def _extract_title(content: str, fallback_filename: str) -> str:
    """
    Extract the document title from content.

    Looks for:
      1. H1 heading: # Title
      2. H2 heading: ## Title (after stripping type prefix)
      3. Fallback: filename without extension and prefix

    Examples:
      "# Task: EF Route 53 TXT Record — 2026-04-02" → "EF Route 53 TXT Record"
      filename "task-ef-route53.md" → "ef-route53"
    """
    # H1 heading
    h1 = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if h1:
        title = h1.group(1).strip()
        # Strip "Task: " or "Meet: " prefix
        title = re.sub(r"^[\w\s]{1,20}:\s*", "", title).strip()
        # Strip trailing date " — YYYY-MM-DD"
        title = re.sub(r"\s*—\s*\d{4}-\d{2}-\d{2}\s*$", "", title).strip()
        return title or fallback_filename

    # Fallback to filename
    stem = Path(fallback_filename).stem
    # Remove type prefix: task-, meet-, misc-
    stem = re.sub(r"^(task|meet|misc|pr)-", "", stem)
    return stem


def _extract_pr_numbers_from_content(content: str) -> list[str]:
    """
    Extract PR numbers from pr-reviews.md content.
    Same logic as dump.py _extract_pr_numbers — duplicated here
    so enricher.py stays standalone with no dependency on dump.py.
    """
    found: set[str] = set()

    # Full GitHub URL
    found.update(
        re.findall(
            r"https?://github\.com/[^/]+/[^/]+/pull/(\d+)",
            content,
        )
    )
    # Hash ref — min 3 digits
    found.update(
        re.findall(
            r"(?:^|[\s,(])#(\d{3,})",
            content,
            flags=re.MULTILINE,
        )
    )
    # plain pull/N
    found.update(re.findall(r"\bpull/(\d+)\b", content))

    return sorted(found)


def _extract_repo_for_pr(content: str, pr_number: str) -> str:
    """
    Try to extract "org/repo" for a given PR number from content.

    Looks for GitHub URL containing the PR number.
    Returns empty string if not found — caller uses default_repo.
    """
    pattern = rf"https?://github\.com/([^/]+/[^/]+)/pull/{re.escape(pr_number)}"
    match = re.search(pattern, content)
    if match:
        return match.group(1).rstrip("/")
    return ""


def _write_enriched(dest: Path, content: str, dry_run: bool) -> None:
    """Write enriched content to dest, creating parent dirs as needed."""
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


# ── CLI Entry Point ───────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="🧠 Brain Enricher — enrich daily task files with Jira + LLM context.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python enricher.py --date 2026-04-02
  python enricher.py --date 2026-04-02 --dry-run
  python enricher.py --date 2026-04-02 --file task-cs-15011-hotfix.md
  python enricher.py --date 2026-04-02 --file task-cs-15011-hotfix.md --verbose
  python enricher.py --date 2026-04-02 --force
        """,
    )

    parser.add_argument(
        "--date",
        required=True,
        help="Date to enrich (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without writing any files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing enriched files",
    )
    parser.add_argument(
        "--file",
        metavar="FILENAME",
        help="Enrich only this specific file (e.g. task-cs-15011-hotfix.md)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print LLM prompts and responses for debugging",
    )

    return parser.parse_args()


def main() -> int:
    """
    Entry point. Returns exit code:
      0 — all files enriched successfully (or nothing to do)
      1 — one or more files failed
      2 — fatal error (bad date, missing directory etc.)
    """
    args = _parse_args()

    try:
        config = BrainConfig()
        enricher = Enricher(config)
        stats = enricher.run(
            date=args.date,
            dry_run=args.dry_run,
            force=args.force,
            target_file=args.file,
            verbose=args.verbose,
        )

    except FileNotFoundError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return 2

    except ValueError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return 2

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user", file=sys.stderr)
        return 2

    except Exception as e:
        print(f"\n❌ Unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    # Exit 1 if any files failed — composable with cron/alerting
    return 1 if stats.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
