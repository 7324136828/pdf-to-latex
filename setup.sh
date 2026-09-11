#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Prefer Python 3.12 or 3.11 if available
PYTHON_CMD="python3"
if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_CMD="python3.12"
elif command -v python3.11 >/dev/null 2>&1; then
    PYTHON_CMD="python3.11"
fi

exec "$PYTHON_CMD" "$DIR/setup.py" "$@"
