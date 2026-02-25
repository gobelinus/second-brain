"""
daily_reminder.py — launchd entry point for the daily Brain reminder.

This script is called by launchd at the scheduled time (default 09:00).
It delegates entirely to Reminder.send() — no logic lives here.

Do not run this directly for testing — use the CLI instead:
  brain remind

Install/uninstall the launchd schedule via:
  brain remind --install
  brain remind --uninstall
"""

import sys
from pathlib import Path

# Allow running as a standalone script from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from brain.reminder import Reminder


def main() -> int:
    try:
        r = Reminder()
        r.send()
        return 0
    except Exception as e:
        print(f"❌ Reminder failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
