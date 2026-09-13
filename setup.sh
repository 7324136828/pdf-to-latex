#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Python 3.14.6 is the project's tested runtime. The setup code verifies the
# patch release before creating or reusing the virtual environment.
PYTHON_CMD="python3.14"
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    echo "ERROR: Python 3.14.6 was not found." >&2
    echo "Install Python 3.14.6 from python.org, then run setup.sh again." >&2
    exit 1
fi

exec "$PYTHON_CMD" "$DIR/setup.py" "$@"
