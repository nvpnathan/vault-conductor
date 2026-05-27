#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== vault-conductor Setup ==="
echo

echo "Installing Python dependencies..."
cd "$REPO"
if ! uv sync --dev --quiet; then
  echo "  warn default .venv could not be updated; retrying with .venv-conductor"
  UV_PROJECT_ENVIRONMENT=.venv-conductor uv sync --dev --quiet
fi
echo "  ok deps installed (uv)"

if command -v cmux >/dev/null 2>&1; then
  echo
  if command -v codex >/dev/null 2>&1; then
    echo "Installing cmux hooks for Codex..."
    cmux hooks setup codex 2>/dev/null && echo "  ok Codex hooks installed" || echo "  skipped Codex hooks"
  fi
  if command -v claude >/dev/null 2>&1; then
    echo "Installing cmux hooks for Claude..."
    cmux hooks setup claude 2>/dev/null && echo "  ok Claude hooks installed" || echo "  skipped Claude hooks"
  fi
fi

echo
if cmux ping >/dev/null 2>&1; then
  echo "  ok cmux socket connected"
else
  echo "  warn cmux socket not responding; open cmux before starting live sessions"
fi

echo
echo "=== Setup complete ==="
echo
echo "Next steps:"
echo "  1. Initialize or repair the vault:"
echo "       uv run conductor init --vault \"\$HOME/Agent Control Room\" --repos \"\$HOME/repos\" --no-open"
echo
echo "  2. Check local setup:"
echo "       uv run conductor doctor --json"
echo
echo "  3. Start the watcher:"
echo "       uv run conductor watch"
