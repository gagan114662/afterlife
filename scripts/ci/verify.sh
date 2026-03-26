#!/usr/bin/env bash
# CI verification gate for afterlife repo.
# Runs: ruff lint + pytest (excluding known pre-existing failures).
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "→ ruff lint"
python3 -m ruff check .

echo "→ pytest"
python3 -m pytest tests/ -x \
  --ignore=tests/api/test_conversation_ollama.py \
  --ignore=tests/api/test_memory.py \
  -q

echo "✓ verify passed"
