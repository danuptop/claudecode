#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install Python dependencies required by pipeline scripts
pip install --quiet notion-client requests

# Install linting/testing tools
pip install --quiet flake8 pytest

# Set PYTHONPATH so scripts/ modules can import each other
echo "export PYTHONPATH=\"${CLAUDE_PROJECT_DIR}/scripts:\${PYTHONPATH:-}\"" >> "$CLAUDE_ENV_FILE"
