#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

if [ -n "${VIRTUAL_ENV:-}${CONDA_PREFIX:-}" ]; then
    exec python "$DIR/run.py" "$@"
fi

if [ ! -f "$DIR/.venv/bin/python" ] || ! "$DIR/.venv/bin/python" -c "import ensurepip, sys; raise SystemExit(sys.version_info[:3] != (3, 14, 6))" >/dev/null 2>&1; then
    echo "Virtual environment is missing or unusable. Running setup.sh first..."
    "$DIR/setup.sh"
fi

exec "$DIR/.venv/bin/python" "$DIR/run.py" "$@"
