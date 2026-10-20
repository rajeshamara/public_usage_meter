#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$script_dir"
if [[ -n "${USAGE_METER_PYTHON:-}" ]]; then
    python_exe="$USAGE_METER_PYTHON"
elif [[ -x "$script_dir/.venv/bin/python" ]]; then
    python_exe="$script_dir/.venv/bin/python"
else
    python_exe=python3
fi
exec "$python_exe" db_admin.py "$@"
