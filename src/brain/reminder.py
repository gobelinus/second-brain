"""
reminder.py — Daily reminder system for Brain.

Responsibilities:
  1. Send a macOS notification prompting daily summary capture
  2. Write a dated checklist markdown to exports/reminders/
  3. Check if yesterday's summary was ingested (SQLite query)
  4. Generate + install/uninstall a launchd plist for scheduled runs

Used directly via CLI:
  brain remind              — send reminder now (test)
  brain remind --install    — generate plist + load into launchd
  brain remind --uninstall  — unload + remove plist
  brain remind --status     — show schedule + last run info

Also called by scripts/daily_reminder.py (launchd entry point).
"""

import sqlite3
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from brain.config import BrainConfig

# ── Reminder ──────────────────────────────────────────────────────────────────


class Reminder:
    """
    Sends daily reminders, writes checklists, and manages the
    launchd scheduled job for automated morning triggers.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    # ── Public API ────────────────────────────────────────────────────────────

    def send(self) -> Path:
        """
        Send today's reminder — the main daily flow.

        Actions:
          1. Query SQLite to check if yesterday was ingested
          2. Send macOS notification
          3. Write checklist markdown to exports/reminders/

        Returns:
            Path to the written checklist file.
        """
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        print("\n🧠 Brain Reminder")
        print(f"   Date      : {today}")
        print()

        # ── Step 1: Check yesterday's ingest status ───────────────────────────
        print("  [1/3] Checking yesterday's ingest status...")
        yesterday_status = self._check_ingest_status(yesterday)
        print(f"        {yesterday} → {yesterday_status}")

        # ── Step 2: Send macOS notification ───────────────────────────────────
        print("  [2/3] Sending macOS notification...")
        self._send_notification(today)
        print("        Notification sent ✅")

        # ── Step 3: Write checklist ────────────────────────────────────────────
        print("  [3/3] Writing checklist...")
        checklist_path = self._write_checklist(today, yesterday_status)
        print(f"        Written: {checklist_path}")

        print()
        print("  ✅ Reminder complete")
        print(f"     Drop notes in: inputs/{today}/")
        print(f"     Then run    : brain compose --date {today} --ingest")
        return checklist_path

    def install(self) -> Path:
        """
        Generate a launchd plist and load it into launchd.

        Returns:
            Path to the generated plist file.

        Raises:
            RuntimeError: If launchctl load fails.
        """
        print("\n🧠 Brain Reminder — Install")
        print()

        plist_path = self._plist_path()

        # ── Generate plist ────────────────────────────────────────────────────
        print("  [1/2] Generating launchd plist...")
        plist_content = self._build_plist()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content, encoding="utf-8")
        print(f"        Written: {plist_path}")

        # ── Load into launchd ─────────────────────────────────────────────────
        print("  [2/2] Loading plist into launchd...")
        self._launchctl("load", plist_path)
        print(f"        Loaded: {self.config.reminder_launchd_label}")

        print()
        print("  ✅ Reminder installed")
        print(
            f"     Scheduled: daily at {self.config.reminder_hour:02d}:{self.config.reminder_minute:02d}"
        )
        print("     To remove : brain remind --uninstall")
        return plist_path

    def uninstall(self) -> None:
        """
        Unload from launchd and remove the plist file.

        Raises:
            FileNotFoundError: If plist doesn't exist (not installed).
        """
        print("\n🧠 Brain Reminder — Uninstall")
        print()

        plist_path = self._plist_path()

        if not plist_path.exists():
            raise FileNotFoundError(
                f"Plist not found — reminder may not be installed.\n"
                f"Expected: {plist_path}"
            )

        # ── Unload from launchd ───────────────────────────────────────────────
        print("  [1/2] Unloading from launchd...")
        self._launchctl("unload", plist_path)
        print(f"        Unloaded: {self.config.reminder_launchd_label}")

        # ── Remove plist file ─────────────────────────────────────────────────
        print("  [2/2] Removing plist file...")
        plist_path.unlink()
        print(f"        Removed: {plist_path}")

        print()
        print("  ✅ Reminder uninstalled")

    def status(self) -> None:
        """
        Print reminder schedule and recent launchd job info.
        """
        print("\n🧠 Brain Reminder — Status")
        print()

        plist_path = self._plist_path()
        label = self.config.reminder_launchd_label

        # ── Installation status ───────────────────────────────────────────────
        print("  📋 Installation")
        if plist_path.exists():
            print(f"     Plist    : {plist_path} ✅")
        else:
            print("     Plist    : not found ❌  (run: brain remind --install)")

        print(
            f"     Schedule : daily at {self.config.reminder_hour:02d}:{self.config.reminder_minute:02d}"
        )
        print(f"     Label    : {label}")

        # ── Launchd job info ──────────────────────────────────────────────────
        print()
        print("  🔄 Launchd job info")
        try:
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("     Status: loaded ✅")
                # Print last exit status if available in output
                for line in result.stdout.splitlines():
                    if "LastExitStatus" in line or "PID" in line:
                        print(f"     {line.strip()}")
            else:
                print("     Status: not loaded ❌")
        except FileNotFoundError:
            print("     ⚠️  launchctl not available — are you on macOS?")

        # ── Recent reminder files ─────────────────────────────────────────────
        print()
        print("  📄 Recent reminders")
        reminders_path = self.config.exports_reminders
        if reminders_path.exists():
            recent = sorted(reminders_path.glob("*.md"), reverse=True)[:5]
            if recent:
                for r in recent:
                    print(f"     {r.name}")
            else:
                print("     No reminder files yet")
        else:
            print("     Reminders directory not created yet")

        print()

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _check_ingest_status(self, check_date: str) -> str:
        """
        Query SQLite to check if a given date has been ingested.
        Returns a human-readable status string.
        """
        db_path = self.config.sqlite_db

        if not db_path.exists():
            return "⚠️  No database found — has anything been ingested yet?"

        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT sensitivity FROM daily_summaries WHERE date = ?",
                (check_date,),
            ).fetchall()
            conn.close()

            if not rows:
                return f"❌ Not ingested — consider running: brain compose --date {check_date} --ingest"

            sensitivities = [r[0] for r in rows]
            return f"✅ Ingested ({', '.join(sensitivities)})"

        except Exception as e:
            return f"⚠️  Could not check status: {e}"

    def _send_notification(self, today: str) -> None:
        """
        Send a macOS notification via osascript.
        Fails silently with a warning if osascript is unavailable
        (e.g. running on Linux in CI).
        """
        title = self.config.reminder_notification_title
        message = self.config.reminder_notification_message.format(date=today)
        sound = self.config.reminder_notification_sound

        script = (
            f'display notification "{message}" '
            f'with title "{title}" '
            f'sound name "{sound}"'
        )

        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError:
            print("        ⚠️  osascript not found — skipping notification (non-macOS?)")
        except subprocess.CalledProcessError as e:
            print(f"        ⚠️  Notification failed: {e.stderr.decode().strip()}")

    def _write_checklist(self, today: str, yesterday_status: str) -> Path:
        """
        Write a dated checklist markdown file to exports/reminders/.
        Returns the path to the written file.
        """
        reminders_path = self.config.exports_reminders
        reminders_path.mkdir(parents=True, exist_ok=True)

        output_path = reminders_path / f"{today}_reminder.md"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        template = self.config.reminder_checklist_template
        content = template.format(
            date=today,
            timestamp=timestamp,
            yesterday_status=yesterday_status,
        )

        output_path.write_text(content, encoding="utf-8")
        return output_path

    def _plist_path(self) -> Path:
        """Resolve the full path to the launchd plist file."""
        label = self.config.reminder_launchd_label
        return self.config.launchd_dir / f"{label}.plist"

    def _build_plist(self) -> str:
        """
        Generate the launchd plist XML content.
        Resolves the absolute path to scripts/daily_reminder.py
        and the Python interpreter in the active virtual environment.
        """
        import sys
        from pathlib import Path

        # Use the Python interpreter from the active venv
        python_path = sys.executable

        # Resolve absolute path to the entry point script
        script_path = (
            Path(__file__).parent.parent.parent / "scripts" / "daily_reminder.py"
        ).resolve()

        # Working directory is project root
        working_dir = Path(__file__).parent.parent.parent.resolve()

        label = self.config.reminder_launchd_label
        hour = self.config.reminder_hour
        minute = self.config.reminder_minute

        # Log files land in launchd/ directory
        log_dir = self.config.launchd_dir.resolve()
        stdout_log = log_dir / "reminder.stdout.log"
        stderr_log = log_dir / "reminder.stderr.log"

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{working_dir}</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>{stdout_log}</string>

    <key>StandardErrorPath</key>
    <string>{stderr_log}</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""

    def _launchctl(self, action: str, plist_path: Path) -> None:
        """
        Run launchctl load or unload.
        Raises RuntimeError with clean message on failure.
        """
        try:
            result = subprocess.run(
                ["launchctl", action, str(plist_path)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"launchctl {action} failed:\n{result.stderr.strip()}"
                )
        except FileNotFoundError as e:
            raise RuntimeError(
                "launchctl not found — are you on macOS?\n"
                f"You can manually {action} the plist:\n"
                f"  launchctl {action} {plist_path}"
            ) from e
