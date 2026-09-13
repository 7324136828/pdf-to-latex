#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

if [ ! -f "$DIR/.venv/bin/python" ] || ! "$DIR/.venv/bin/python" -c "import ensurepip, sys; raise SystemExit(sys.version_info[:3] != (3, 14, 6))" >/dev/null 2>&1; then
    echo "Python 3.14.6 virtual environment is missing or unusable. Running setup.sh first..."
    "$DIR/setup.sh"
fi

exec "$DIR/.venv/bin/python" "$DIR/backend/convert_pdfs.py" "$@"
