"""
enrich_runner.py — Phase 7a: brain enrich automation.

Automates the manual scp + ssh + scp back workflow for remote enrichment.

Workflow
────────
  Step 1: Validate local inputs/ directory exists for date
  Step 2: scp local inputs/YYYY-MM-DD/ → remote (excluding enriched/)
  Step 3: ssh remote: python enricher.py --date YYYY-MM-DD
  Step 4: scp remote enriched/ → local inputs/YYYY-MM-DD/enriched/
  Step 5: Print next step hint

Exit codes mirror enricher.py:
  0 = clean
  1 = partial failure — enriched/ copied back, some files failed
  2 = fatal failure — enriched/ not copied back

Uses system ssh/scp via subprocess — zero extra dependencies.
SSH config alias used — no credentials in config.

Usage:
  runner = EnrichRunner(config)
  result = runner.run(date="2026-04-10")
  result = runner.run(date="2026-04-10", dry_run=True)
  result = runner.run(date="2026-04-10", verbose=True)
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from brain.config import BrainConfig

# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class EnrichResult:
    """
    Result of a single EnrichRunner.run() call.

    Fields
    ──────
    date           : ISO date string YYYY-MM-DD
    uploaded       : True if scp upload succeeded
    remote_exit    : exit code from remote enricher.py (0/1/2/-1 if not run)
    downloaded     : True if scp download of enriched/ succeeded
    warnings       : non-fatal messages
    enriched_path  : local path to enriched/ directory (None if not downloaded)
    """

    date: str
    uploaded: bool = False
    remote_exit: int = -1
    downloaded: bool = False
    warnings: list[str] = field(default_factory=list)
    enriched_path: Path | None = None


# ── EnrichRunner ──────────────────────────────────────────────────────────────


class EnrichRunner:
    """
    Automates remote enrichment via system ssh/scp.

    Instantiate once, call run() per date.
    All config from BrainConfig — no hardcoded values.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(
        self,
        date: str,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> EnrichResult:
        """
        Run the full remote enrichment pipeline for a given date.

        Args:
            date    : ISO date string YYYY-MM-DD
            dry_run : print commands without executing
            verbose : pass --verbose to remote enricher.py

        Returns:
            EnrichResult with upload/download status and remote exit code.
        """
        result = EnrichResult(date=date)

        local_inputs = self.config.enrich_local_inputs_path(date)
        local_enriched = self.config.enrich_local_enriched_path(date)
        remote_inputs = self.config.enrich_remote_inputs_path(date)
        remote_enriched = self.config.enrich_remote_enriched_path(date)
        host = self.config.enrich_remote_host

        print(f"\n🚀 Brain Enrich — {date}")
        print(f"   Host     : {host}")
        print(f"   Local    : {local_inputs}")
        print(f"   Remote   : {remote_inputs}")
        print()

        # ── Step 1: Validate local inputs ─────────────────────────────────────
        print("   [1/4] Validating local inputs...")
        if not local_inputs.exists():
            raise FileNotFoundError(
                f"No inputs directory found for {date}.\n"
                f"Expected: {local_inputs}\n"
                f"Run brain dump --date {date} first."
            )

        input_files = list(local_inputs.glob("*.md"))
        if not input_files:
            raise FileNotFoundError(
                f"No .md files found in {local_inputs}.\n"
                f"Run brain dump --date {date} first."
            )

        print(f"        ✅ {len(input_files)} file(s) found in inputs/{date}/")

        # ── Step 2: scp upload ────────────────────────────────────────────────
        print("   [2/4] Uploading to remote...")
        upload_cmd = self._build_upload_cmd(
            local_inputs=local_inputs,
            remote_inputs=remote_inputs,
            host=host,
            date=date,
        )
        print(f"        {' '.join(upload_cmd)}")

        if dry_run:
            print("        [DRY RUN] skipping")
        else:
            exit_code = self._run_cmd(upload_cmd, stream=False)
            if exit_code != 0:
                raise RuntimeError(
                    f"scp upload failed (exit {exit_code}).\n"
                    f"Check SSH connectivity: ssh {host}"
                )
            result.uploaded = True
            print("        ✅ Upload complete")

        # ── Step 3: ssh run enricher.py ───────────────────────────────────────
        print("   [3/4] Running enricher.py on remote...")
        ssh_cmd = self._build_ssh_cmd(
            host=host,
            date=date,
            verbose=verbose,
        )
        print(f"        {' '.join(ssh_cmd)}")
        print()

        if dry_run:
            print("        [DRY RUN] skipping")
            result.remote_exit = 0
        else:
            remote_exit = self._run_cmd(
                ssh_cmd,
                stream=self.config.enrich_stream_output,
                timeout=self.config.enrich_ssh_timeout,
            )
            result.remote_exit = remote_exit

            print()
            if remote_exit == 0:
                print("        ✅ Remote enrichment complete")
            elif remote_exit == 1:
                msg = "Remote enrichment partially failed (exit 1) — downloading partial results"
                print(f"        ⚠️  {msg}")
                result.warnings.append(msg)
            else:
                msg = f"Remote enrichment fatally failed (exit {remote_exit}) — skipping download"
                print(f"        ❌ {msg}")
                result.warnings.append(msg)
                return result  # fatal — do not attempt download

        # ── Step 4: scp download enriched/ ───────────────────────────────────
        print("   [4/4] Downloading enriched/ from remote...")
        download_cmd = self._build_download_cmd(
            host=host,
            remote_enriched=remote_enriched,
            local_enriched=local_enriched,
        )
        print(f"        {' '.join(download_cmd)}")

        if dry_run:
            print("        [DRY RUN] skipping")
        else:
            local_enriched.mkdir(parents=True, exist_ok=True)
            exit_code = self._run_cmd(download_cmd, stream=False)
            if exit_code != 0:
                msg = f"scp download failed (exit {exit_code}) — enriched/ may be empty on remote"
                print(f"        ⚠️  {msg}")
                result.warnings.append(msg)
            else:
                result.downloaded = True
                result.enriched_path = local_enriched
                enriched_files = list(local_enriched.glob("*.md"))
                print(f"        ✅ Downloaded {len(enriched_files)} enriched file(s)")

        # ── Step 5: Next step hint ────────────────────────────────────────────
        print()
        print("   ─" * 20)
        if not dry_run and result.downloaded:
            print("   ✅ Enrichment complete — ready to compose")
            print(f"      Next: brain compose --date {date} --ingest")
        elif dry_run:
            print("   [DRY RUN] Commands printed — nothing executed")
            print("      Run without --dry-run to execute")
        print()

        return result

    # ── Command Builders ──────────────────────────────────────────────────────

    def _build_upload_cmd(
        self,
        local_inputs: Path,
        remote_inputs: str,
        host: str,
        date: str,
    ) -> list[str]:
        """
        Build scp upload command.

        Uploads inputs/YYYY-MM-DD/ to remote, excluding configured directories.
        Uses rsync if available for exclusion support, falls back to scp.

        rsync is preferred — scp has no native exclude support.
        """
        exclude_args: list[str] = []
        for exc in self.config.enrich_upload_exclude:
            exclude_args += ["--exclude", exc]

        return [
            "rsync",
            "-az",  # archive + compress
            "--progress",
            *exclude_args,
            f"{local_inputs}/",  # trailing slash = contents only
            f"{host}:{remote_inputs}/",
        ]

    def _build_ssh_cmd(
        self,
        host: str,
        date: str,
        verbose: bool,
    ) -> list[str]:
        """Build ssh command to run enricher.py on remote."""
        brain_dir = self.config.enrich_remote_brain_dir
        remote_cmd = f"cd {brain_dir} && python enricher.py --date {date}"

        if verbose:
            remote_cmd += " --verbose"

        return ["ssh", host, remote_cmd]

    def _build_download_cmd(
        self,
        host: str,
        remote_enriched: str,
        local_enriched: Path,
    ) -> list[str]:
        """Build scp download command for enriched/ directory."""
        return [
            "scp",
            "-r",
            f"{host}:{remote_enriched}/",
            f"{local_enriched}/",
        ]

    # ── Command Execution ─────────────────────────────────────────────────────

    def _run_cmd(
        self,
        cmd: list[str],
        stream: bool = False,
        timeout: int | None = None,
    ) -> int:
        """
        Run a shell command and return its exit code.

        Args:
            cmd     : command as list of strings
            stream  : if True, stream stdout line by line to terminal
            timeout : optional timeout in seconds

        Returns:
            Exit code of the command.
        """
        try:
            if stream:
                # Stream stdout line by line — essential for long enrichments
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                if process.stdout:
                    for line in process.stdout:
                        print(f"        {line}", end="", flush=True)

                process.wait(timeout=timeout)
                return process.returncode

            else:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if result.returncode != 0 and result.stderr:
                    print(f"        stderr: {result.stderr.strip()}")
                return result.returncode

        except subprocess.TimeoutExpired:
            print(f"        ⚠️  Command timed out after {timeout}s")
            return 2

        except FileNotFoundError as e:
            # rsync/scp/ssh not found
            cmd_name = cmd[0]
            print(f"        ⚠️  {cmd_name} not found: {e}")
            if cmd_name == "rsync":
                print("        Install rsync: brew install rsync")
            return 2

        except Exception as e:
            print(f"        ⚠️  Unexpected error: {type(e).__name__}: {e}")
            return 2
