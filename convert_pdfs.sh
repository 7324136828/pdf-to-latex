#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

if [ ! -f "$DIR/.venv/bin/python" ]; then
    echo "Virtual environment not found. Running setup.sh first..."
    "$DIR/setup.sh"
fi

exec "$DIR/.venv/bin/python" "$DIR/backend/convert_pdfs.py" "$@"
