#!/bin/bash
# SessionStart hook for Claude Code on the web.
# Prepares the Python environment so `logsig`, pytest, and the eval commands
# work immediately in a fresh remote container.
#
# Notes:
#  - The base image's *system* pip cannot build packages (install_layout error),
#    so we install into a project-local virtualenv and put it on PATH.
#  - Idempotent: safe to re-run; reuses an existing .venv and lets pip's cache
#    short-circuit already-installed deps.
set -euo pipefail

# Only run in the remote (web) environment; do nothing locally.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
ENV_FILE="${CLAUDE_ENV_FILE:-/dev/null}"
cd "$PROJECT_DIR"

# 1. Create the venv if missing (system pip is broken on the base image).
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

# 2. Install the package + dev deps (numpy, scipy, drain3, pytest).
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e '.[dev]'

# 3. Persist the venv on PATH for the rest of the session so `logsig`, `python`,
#    and `pytest` resolve to the venv without an explicit prefix.
{
  echo "export VIRTUAL_ENV=\"$PROJECT_DIR/.venv\""
  echo "export PATH=\"$PROJECT_DIR/.venv/bin:\$PATH\""
} >> "$ENV_FILE"

echo "logsig: environment ready (.venv with numpy/scipy/drain3/pytest)."
