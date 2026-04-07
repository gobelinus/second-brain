"""
calendar_puller.py — Google Calendar integration for Brain Phase 6a.

Pulls calendar events via Google Calendar MCP and writes a structured
daily_meetings.md file to inputs/YYYY-MM-DD/.

Key behaviours
──────────────
  Runs twice per launchd trigger (today + yesterday):
    Today    → scaffold meetings before the day starts
    Yesterday → backfill meetings added after the 9am morning pull

  Idempotent:
    If daily_meetings.md already exists for a date, it is SKIPPED.
    This protects meeting notes the user has already filled in.
    Use --force to overwrite (useful for backfilling missed days).

  Graceful degradation:
    If Google Calendar MCP is unreachable, the command prints a clear
    warning and exits cleanly. The daily workflow is never blocked.

  Output format:
    Each calendar event becomes an ## Meet: section in daily_meetings.md.
    This matches the daily_*.md glob — brain dump picks it up automatically.
    No extra steps needed to incorporate calendar events into the brain.

MCP tool used
─────────────
  Google Calendar MCP exposes: list_events (or similar)
  Server: https://gcal.mcp.claude.com/mcp

Usage
─────
  puller = CalendarPuller(config)

  # Standard daily run — today + yesterday
  result_today     = puller.pull(date.today().isoformat())
  result_yesterday = puller.pull((date.today() - timedelta(days=1)).isoformat())

  # Single date
  result = puller.pull("2026-04-02")
  result = puller.pull("2026-04-02", dry_run=True)
  result = puller.pull("2026-04-02", force=True)   # overwrite existing
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path

from brain.config import BrainConfig

# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class CalendarEvent:
    """
    A single calendar event, normalised from the MCP response.

    Fields
    ──────
    title           : event summary / title
    start           : start time as HH:MM string (empty if all-day)
    end             : end time as HH:MM string (empty if all-day)
    attendees       : list of attendee display names or emails
    location        : location string or None
    calendar_name   : which calendar this event belongs to
    is_all_day      : True for all-day events (holidays, OOO)
    duration_minutes: event duration in minutes (0 for all-day)
    response_status : "accepted" | "tentative" | "declined" | "needsAction"
    """

    title: str
    start: str = ""
    end: str = ""
    attendees: list[str] = field(default_factory=list)
    location: str | None = None
    calendar_name: str = ""
    is_all_day: bool = False
    duration_minutes: int = 0
    response_status: str = "accepted"


@dataclass
class CalendarResult:
    """
    Result of a single CalendarPuller.pull() call.

    Fields
    ──────
    date             : ISO date string YYYY-MM-DD
    events_found     : total events returned by MCP
    events_included  : events written to the output file
    events_skipped   : events filtered out (all-day, duration, etc.)
    output_path      : path to written daily_meetings.md (None if skipped/error)
    skipped_existing : True if file already existed and was not overwritten
    warnings         : non-fatal messages
    """

    date: str
    events_found: int = 0
    events_included: int = 0
    events_skipped: int = 0
    output_path: Path | None = None
    skipped_existing: bool = False
    warnings: list[str] = field(default_factory=list)


# ── CalendarPuller ────────────────────────────────────────────────────────────


class CalendarPuller:
    """
    Pulls Google Calendar events via MCP and writes daily_meetings.md.

    Instantiate once, call pull() for each date needed.
    All configuration comes from BrainConfig — no hardcoded values.
    """

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    # ── Public API ─────────────────────────────────────────────────────────────

    def pull(
        self,
        date: str,
        dry_run: bool = False,
        force: bool = False,
    ) -> CalendarResult:
        """
        Pull calendar events for a given date and write daily_meetings.md.

        Args:
            date    : ISO date string YYYY-MM-DD
            dry_run : print what would be written without writing anything
            force   : overwrite existing daily_meetings.md

        Returns:
            CalendarResult with event counts and output path.
        """
        _validate_date(date)
        result = CalendarResult(date=date)
        output_path = self.config.calendar_output_path(date)

        print(f"\n📅 Brain Calendar — {date}")

        # ── Enabled check ─────────────────────────────────────────────────────
        if not self.config.calendar_mcp_enabled:
            msg = "Google Calendar MCP disabled — set calendar.mcp.enabled: true in config.yaml"
            print(f"   ℹ️  {msg}")
            result.warnings.append(msg)
            return result

        # ── Idempotency check ─────────────────────────────────────────────────
        if output_path.exists() and not force:
            print(f"   ⚠️  Already exists — skipping: {output_path.name}")
            print("        Use --force to overwrite")
            result.skipped_existing = True
            result.output_path = output_path
            return result

        # ── Step 1: Fetch events from Google Calendar MCP ─────────────────────
        print("   [1/3] Fetching events from Google Calendar...")
        raw_events = self._fetch_events(date)

        if raw_events is None:
            msg = "Google Calendar MCP unavailable — check connection and auth"
            print(f"   ❌ {msg}")
            result.warnings.append(msg)
            return result

        result.events_found = len(raw_events)
        print(f"        {result.events_found} event(s) found")

        # ── Step 2: Filter events ─────────────────────────────────────────────
        print("   [2/3] Filtering events...")
        included, skipped = self._filter_events(raw_events)
        result.events_included = len(included)
        result.events_skipped = skipped

        if not included:
            print("        No events to write after filtering")
            print(
                f"        ({skipped} event(s) filtered — all-day or below min duration)"
            )
            # Still write an empty scaffold so brain dump sees the file
            included = []

        for event in included:
            jira_note = ""
            jira_ids = _extract_jira_ids_from_title(event.title)
            if jira_ids:
                jira_note = f" [{', '.join(jira_ids)}]"
            print(f"        ✅ {event.start}–{event.end}  {event.title}{jira_note}")

        if skipped > 0:
            print(
                f"        ⏭️  {skipped} event(s) filtered (all-day or below min duration)"
            )

        # ── Step 3: Write output ──────────────────────────────────────────────
        print(f"   [3/3] Writing{' (DRY RUN)' if dry_run else ''}...")
        content = self._format_output(included, date)

        if dry_run:
            print(f"        [DRY RUN] Would write: {output_path}")
            print(f"        {'─' * 54}")
            for line in content.splitlines()[:15]:
                print(f"        {line}")
            if len(content.splitlines()) > 15:
                print(f"        ... ({len(content.splitlines())} lines total)")
            print(f"        {'─' * 54}")
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            print(f"        ✅ Written: {output_path}")

        result.output_path = output_path
        print()
        print(
            f"   ✅ Calendar pull complete — {result.events_included} meeting(s) scaffolded"
        )
        print("      brain dump will pick this up automatically via daily_*.md glob")

        return result

    def install(self) -> Path:
        """
        Generate launchd plist and load it — mirrors reminder.py pattern.

        Returns:
            Path to the generated plist file.
        """
        print("\n📅 Brain Calendar — Install")
        print()

        plist_path = self._plist_path()

        print("  [1/2] Generating launchd plist...")
        plist_content = self._build_plist()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content, encoding="utf-8")
        print(f"        Written: {plist_path}")

        print("  [2/2] Loading plist into launchd...")
        self._launchctl("load", plist_path)
        print(f"        Loaded: {self.config.calendar_launchd_label}")

        print()
        print("  ✅ Calendar installed")
        print(
            f"     Scheduled: daily at {self.config.calendar_schedule_hour:02d}:{self.config.calendar_schedule_minute:02d}"
        )
        print("     Pulls     : today + yesterday in one run")
        print("     To remove : brain calendar --uninstall")
        return plist_path

    def uninstall(self) -> None:
        """Unload from launchd and remove the plist file."""
        print("\n📅 Brain Calendar — Uninstall")
        print()

        plist_path = self._plist_path()
        if not plist_path.exists():
            raise FileNotFoundError(
                f"Plist not found — calendar may not be installed.\n"
                f"Expected: {plist_path}"
            )

        print("  [1/2] Unloading from launchd...")
        self._launchctl("unload", plist_path)
        print(f"        Unloaded: {self.config.calendar_launchd_label}")

        print("  [2/2] Removing plist file...")
        plist_path.unlink()
        print(f"        Removed: {plist_path}")

        print()
        print("  ✅ Calendar uninstalled")

    def status(self) -> None:
        """Print calendar schedule and recent launchd job info."""
        import subprocess

        print("\n📅 Brain Calendar — Status")
        print()

        plist_path = self._plist_path()
        label = self.config.calendar_launchd_label

        print("  📋 Installation")
        if plist_path.exists():
            print(f"     Plist    : {plist_path} ✅")
        else:
            print("     Plist    : not found ❌  (run: brain calendar --install)")

        print(
            f"     Schedule : daily at {self.config.calendar_schedule_hour:02d}:{self.config.calendar_schedule_minute:02d}"
        )
        print(f"     Label    : {label}")
        print(
            f"     MCP      : {'enabled ✅' if self.config.calendar_mcp_enabled else 'disabled ❌'}"
        )
        print(f"     Endpoint : {self.config.calendar_mcp_server_url}")

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
                for line in result.stdout.splitlines():
                    if "LastExitStatus" in line or "PID" in line:
                        print(f"     {line.strip()}")
            else:
                print("     Status: not loaded ❌")
        except FileNotFoundError:
            print("     ⚠️  launchctl not available — are you on macOS?")

        print()
        print("  📄 Recent meeting files")
        # Check last 5 dates for daily_meetings.md
        today = date_type.today()
        found_any = False
        for i in range(7):
            check_date = (today - timedelta(days=i)).isoformat()
            path = self.config.calendar_output_path(check_date)
            if path.exists():
                print(f"     {check_date} — {path} ✅")
                found_any = True
        if not found_any:
            print("     No daily_meetings.md files found in last 7 days")
        print()

    # ── MCP Fetch ──────────────────────────────────────────────────────────────

    def _fetch_events(self, date: str) -> list[dict] | None:
        """
        Call Google Calendar MCP to list events for a given date.

        Returns list of raw event dicts from MCP, or None on failure.

        Google Calendar MCP tool: list_events
        Arguments: date range (start/end as ISO datetime strings)
        """
        # Build date range: full day in ISO 8601 format
        # Google Calendar MCP expects timeMin/timeMax as RFC 3339
        time_min = f"{date}T00:00:00Z"
        time_max = f"{date}T23:59:59Z"

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "list_events",
                "arguments": {
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": True,  # expand recurring events
                    "orderBy": "startTime",  # sort chronologically
                },
            },
            "id": 1,
        }

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url=self.config.calendar_mcp_server_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")

            envelope = json.loads(raw)

            if "error" in envelope:
                error_msg = envelope["error"].get("message", "Unknown MCP error")
                print(f"        ⚠️  MCP error: {error_msg}")
                return None

            result_block = envelope.get("result", {})
            content_list = result_block.get("content", [])
            text_content = next(
                (c.get("text", "") for c in content_list if c.get("type") == "text"),
                "",
            )

            if not text_content:
                return []

            data = json.loads(text_content)

            # Google Calendar MCP returns events under "items" or "events" key
            events = data.get("items", data.get("events", []))
            return events if isinstance(events, list) else []

        except urllib.error.HTTPError as e:
            print(f"        ⚠️  HTTP {e.code}: {e.reason}")
            return None
        except urllib.error.URLError as e:
            print(f"        ⚠️  Network error: {e.reason}")
            return None
        except TimeoutError:
            print("        ⚠️  MCP server timeout")
            return None
        except Exception as e:
            print(f"        ⚠️  Unexpected error: {type(e).__name__}: {e}")
            return None

    # ── Event Filtering ────────────────────────────────────────────────────────

    def _filter_events(
        self,
        raw_events: list[dict],
    ) -> tuple[list[CalendarEvent], int]:
        """
        Parse and filter raw MCP events into CalendarEvent objects.

        Returns:
            (included_events, skipped_count)

        Filtering rules (all config-driven):
          1. Skip all-day events if calendar.filters.skip_all_day_events: true
          2. Skip events shorter than calendar.filters.min_duration_minutes
          3. Skip declined events if calendar.filters.accepted_only: true
        """
        skip_all_day = self.config.calendar_skip_all_day
        min_duration = self.config.calendar_min_duration_minutes
        accepted_only = self.config.calendar_accepted_only

        included: list[CalendarEvent] = []
        skipped = 0

        for raw in raw_events:
            event = self._parse_event(raw)

            # Filter: all-day events
            if event.is_all_day and skip_all_day:
                skipped += 1
                continue

            # Filter: minimum duration
            if min_duration > 0 and event.duration_minutes < min_duration:
                skipped += 1
                continue

            # Filter: accepted only
            if accepted_only and event.response_status not in ("accepted", ""):
                skipped += 1
                continue

            included.append(event)

        return included, skipped

    def _parse_event(self, raw: dict) -> CalendarEvent:
        """
        Parse a single raw Google Calendar API event dict into CalendarEvent.

        Handles both timed events and all-day events.
        Google Calendar API event structure:
          {
            "summary": "Platform Sync",
            "start": {"dateTime": "2026-04-02T09:00:00+05:30"} | {"date": "2026-04-02"},
            "end":   {"dateTime": "2026-04-02T09:30:00+05:30"} | {"date": "2026-04-03"},
            "attendees": [{"displayName": "Alice", "email": "alice@..."}],
            "location": "Zoom",
            "organizer": {"displayName": "Bob"},
            "selfAttendeeStatus": "accepted"
          }
        """
        summary = raw.get("summary", "(No title)")

        # Detect all-day vs timed event
        start_block = raw.get("start", {})
        end_block = raw.get("end", {})

        is_all_day = "date" in start_block and "dateTime" not in start_block

        if is_all_day:
            start_str = ""
            end_str = ""
            duration_minutes = 0
        else:
            start_str = _parse_time(start_block.get("dateTime", ""))
            end_str = _parse_time(end_block.get("dateTime", ""))
            duration_minutes = _calc_duration(
                start_block.get("dateTime", ""),
                end_block.get("dateTime", ""),
            )

        # Attendees — filter out "resources" (conference rooms etc.)
        raw_attendees = raw.get("attendees", [])
        attendees = [
            a.get("displayName") or a.get("email", "")
            for a in raw_attendees
            if not a.get("resource", False)
            and a.get("email", "")  # skip entries with no email
        ]

        # Calendar name — from organizer or default
        organizer = raw.get("organizer", {})
        calendar_name = organizer.get("displayName", "") or "My Calendar"

        # Location
        location = raw.get("location") or None

        # My response status
        response_status = raw.get("selfAttendeeStatus", "accepted")

        return CalendarEvent(
            title=summary,
            start=start_str,
            end=end_str,
            attendees=attendees,
            location=location,
            calendar_name=calendar_name,
            is_all_day=is_all_day,
            duration_minutes=duration_minutes,
            response_status=response_status,
        )

    # ── Output Formatting ──────────────────────────────────────────────────────

    def _format_output(self, events: list[CalendarEvent], date: str) -> str:
        """
        Render all events as a daily_meetings.md file.

        Header block with metadata, then one ## Meet: section per event.
        Empty event list produces a stub file with a note — still useful
        as a signal that brain calendar ran for this date.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count_str = f"{len(events)}" if events else "0"
        filter_note = (
            " (all-day events excluded)" if self.config.calendar_skip_all_day else ""
        )

        lines = [
            f"# 📅 Daily Meetings — {date}",
            "",
            f"> **Generated at:** {now}",
            "> **Source:** Google Calendar",
            f"> **Events:** {count_str}{filter_note}",
            "",
        ]

        if not events:
            lines += [
                "---",
                "",
                "> ℹ️  No meetings scheduled for this date.",
                "> Delete this file if you want brain calendar to regenerate it.",
                "",
            ]
            return "\n".join(lines)

        lines.append("---")
        lines.append("")

        template = self.config.calendar_meeting_template
        if not template:
            # Fallback inline template if config is missing
            template = (
                "## Meet: {title} ({start} – {end})\n\n"
                "**Calendar:** {calendar_name}\n"
                "**Attendees:** {attendees}\n"
                "{location_line}"
                "<!-- add your notes here -->\n\n"
                "---\n"
            )

        for event in events:
            # Build location line — empty string if no location
            # Avoids a blank "**Location:**" line in the output
            if event.location:
                location_line = f"**Location:** {event.location}\n"
            else:
                location_line = ""

            attendees_str = ", ".join(event.attendees) if event.attendees else "—"

            section = template.format(
                title=event.title,
                start=event.start,
                end=event.end,
                calendar_name=event.calendar_name,
                attendees=attendees_str,
                location_line=location_line,
            )
            lines.append(section)

        return "\n".join(lines)

    # ── Launchd Helpers ────────────────────────────────────────────────────────

    def _plist_path(self) -> Path:
        """Resolve full path to the launchd plist file."""
        label = self.config.calendar_launchd_label
        return self.config.launchd_dir / f"{label}.plist"

    def _build_plist(self) -> str:
        """
        Generate launchd plist XML for daily_calendar.py.
        Mirrors the pattern from reminder.py._build_plist().
        """
        import sys

        python_path = sys.executable
        script_path = (
            Path(__file__).parent.parent.parent / "scripts" / "daily_calendar.py"
        ).resolve()
        working_dir = Path(__file__).parent.parent.parent.resolve()

        label = self.config.calendar_launchd_label
        hour = self.config.calendar_schedule_hour
        minute = self.config.calendar_schedule_minute

        log_dir = self.config.launchd_dir.resolve()
        stdout_log = log_dir / "calendar.stdout.log"
        stderr_log = log_dir / "calendar.stderr.log"

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
        """Run launchctl load or unload. Raises RuntimeError on failure."""
        import subprocess

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
        except FileNotFoundError:
            raise RuntimeError(
                "launchctl not found — are you on macOS?\n"
                f"You can manually {action} the plist:\n"
                f"  launchctl {action} {plist_path}"
            ) from None


# ── Module-Level Helpers ──────────────────────────────────────────────────────


def _validate_date(date: str) -> None:
    """Fail fast if date is not in YYYY-MM-DD format."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise ValueError(f"Invalid date format: '{date}'. Expected YYYY-MM-DD.")


def _parse_time(datetime_str: str) -> str:
    """
    Parse ISO 8601 datetime string to HH:MM format.

    Handles:
      "2026-04-02T09:00:00+05:30"  → "09:00"
      "2026-04-02T09:00:00Z"       → "09:00"
      ""                           → ""

    Note: time is shown in the timezone as returned by the API.
    Google Calendar MCP returns times in the calendar's timezone.
    """
    if not datetime_str:
        return ""
    try:
        # Strip timezone info for simple HH:MM parsing
        # Works for both +HH:MM and Z suffixes
        dt_clean = re.sub(r"([+-]\d{2}:\d{2}|Z)$", "", datetime_str)
        dt = datetime.fromisoformat(dt_clean)
        return dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def _calc_duration(start_str: str, end_str: str) -> int:
    """
    Calculate event duration in minutes from ISO datetime strings.
    Returns 0 if either string is empty or invalid.
    """
    if not start_str or not end_str:
        return 0
    try:
        start_clean = re.sub(r"([+-]\d{2}:\d{2}|Z)$", "", start_str)
        end_clean = re.sub(r"([+-]\d{2}:\d{2}|Z)$", "", end_str)
        start = datetime.fromisoformat(start_clean)
        end = datetime.fromisoformat(end_clean)
        delta = end - start
        return max(0, int(delta.total_seconds() / 60))
    except (ValueError, TypeError):
        return 0


def _extract_jira_ids_from_title(title: str) -> list[str]:
    """
    Extract Jira ticket IDs from an event title.
    Used to annotate the console output — not written to the file.
    e.g. "Incident Review — CS-15011" → ["CS-15011"]
    """
    found = re.findall(r"\b[A-Z]{2,9}-\d+\b", title)
    return sorted(set(found))
