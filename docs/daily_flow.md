brain dump --date YYYY-MM-DD
  ↓ (fills meet skeletons)
[run external PR comment script for each PR]
brain dump --date YYYY-MM-DD --copy-pr-comments
  ↓
[run external Jira script for each ticket]
brain dump --date YYYY-MM-DD --copy-jira-context
  ↓
scp inputs/YYYY-MM-DD/ to remote
python enricher.py --date YYYY-MM-DD    ← reads context/, no credentials
scp enriched/ back
brain compose --date YYYY-MM-DD --ingest
