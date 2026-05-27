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

echo
echo "Installing conductor CLI..."
uv tool install -e "$REPO" --force --quiet
if conductor --help >/dev/null 2>&1; then
  echo "  ok conductor installed"
else
  echo "  warn conductor command did not run after install"
fi
UV_TOOL_BIN="$(uv tool dir --bin 2>/dev/null || true)"
case ":$PATH:" in
  *":$UV_TOOL_BIN:"*) ;;
  *)
    if [ -n "$UV_TOOL_BIN" ]; then
      echo "  warn $UV_TOOL_BIN is not on PATH; add it to use conductor without uv run"
    fi
    ;;
esac

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
echo "Installing Agent Control Room skill for Codex..."
CODEX_SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$CODEX_SKILLS_DIR"
rm -rf "$CODEX_SKILLS_DIR/agent-control-room"
cp -R "$REPO/skills/agent-control-room" "$CODEX_SKILLS_DIR/"
echo "  ok skill installed at $CODEX_SKILLS_DIR/agent-control-room"

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
echo "       conductor init --vault \"\$HOME/Agent Control Room\" --repos \"\$HOME/repos\" --no-open"
echo
echo "  2. Check local setup:"
echo "       conductor doctor --json"
echo
echo "  3. Start the watcher:"
echo "       conductor watch"
