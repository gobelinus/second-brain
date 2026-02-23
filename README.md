cat > README.md << 'EOF'
# 🧠 Brain — Personal Second Brain

A local, private, queryable knowledge system built from daily activity summaries.

## Stack
- **LLM**: Ollama + Mistral 7B (local)
- **Embeddings**: Ollama + nomic-embed-text (local)
- **Vector Store**: ChromaDB (persistent, local)
- **Structured Store**: SQLite (local)
- **CLI**: Typer

## Setup
```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Pull required Ollama models
ollama pull mistral
ollama pull nomic-embed-text
```

## Usage
```bash
# Ingest a daily summary
brain ingest --file daily/public/2026-02-20.md --sensitivity public

# Query your brain
brain query "What was I blocked on last month?"

# Summarize a date range
brain summarize --from 2026-02-01 --to 2026-02-20 --export
```

## Privacy
- `daily/sensitive/` — git ignored, stays local
- `store/` — git ignored, stays local
- `config.local.yaml` — git ignored, stays local
- `.env` — git ignored, never committed
EOF
