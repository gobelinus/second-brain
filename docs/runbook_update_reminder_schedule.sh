# 1. Update config.local.yaml (keeps change local, not tracked)
# Add or update the reminder schedule override:
cat >> config.local.yaml << 'EOF'
reminder:
  schedule:
    hour: 12
    minute: 0
EOF

# 2. Verify config loaded correctly
python -c "
from brain.config import BrainConfig
cfg = BrainConfig()
print(f'Reminder time: {cfg.reminder_hour:02d}:{cfg.reminder_minute:02d}')
"
# Expected: Reminder time: 12:00

# 3. Reinstall — unload old plist, generate new one with updated time, reload
brain remind --uninstall
brain remind --install

# 4. Verify launchd has the new time
launchctl list com.brain.reminder
# Check StartCalendarInterval shows Hour = 12
