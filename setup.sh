#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== vault-conductor Setup ==="
echo ""

# Python deps via uv
echo "Installing Python dependencies..."
cd "$REPO" && uv sync --quiet
echo "  ✓ deps installed (uv)"

# cmux Claude Code hooks
if command -v claude &>/dev/null && command -v cmux &>/dev/null; then
    echo ""
    echo "Installing cmux hooks for Claude Code..."
    cmux hooks setup claude 2>/dev/null && echo "  ✓ cmux hooks installed" || echo "  · cmux hooks: skipped (may already be installed)"
fi

# Verify cmux socket
echo ""
if cmux ping &>/dev/null 2>&1; then
    echo "  ✓ cmux socket connected"
else
    echo "  ⚠ cmux socket not responding — open cmux app first"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Set your vault path in vault_conductor/main.py (VAULT variable)."
echo ""
echo "  2. Open your vault in Obsidian."
echo ""
echo "  3. Start the orchestrator (watches kanban → spawns agents):"
echo "       uv run orchestrator"
echo ""
echo "  4. Drag a project card to 'In Progress' on the kanban board."
echo "     The orchestrator will spawn a cmux workspace with Claude Code."
echo ""
