#!/usr/bin/env python3
"""
daily_calendar.py — launchd entry point for brain calendar.

Called by launchd at 09:00 daily.
Pulls Google Calendar events for today + yesterday in one pass.

Today    → scaffold meetings before the day starts
Yesterday → backfill meetings added after the 9am morning pull

Idempotent — existing daily_meetings.md files are skipped.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure src/ is on path when run by launchd
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from brain.calendar_puller import CalendarPuller
from brain.config import BrainConfig

cfg = BrainConfig()
puller = CalendarPuller(cfg)

today = date.today().isoformat()
yesterday = (date.today() - timedelta(days=1)).isoformat()

print("🧠 Brain Calendar — daily run")
print(f"   Pulling: {today} + {yesterday}")
print()

puller.pull(today)
puller.pull(yesterday)
