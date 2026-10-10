#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p build/linux/site-packages
"$PYTHON_BIN" -m pip install --target build/linux/site-packages -r requirements-build.txt
PYTHONPATH="$ROOT/build/linux/site-packages${PYTHONPATH:+:$PYTHONPATH}" \
"$PYTHON_BIN" -m PyInstaller \
    --noconfirm \
    --clean \
    --onefile \
    --windowed \
    --name DEADGRID \
    --add-data "$ROOT/DeadGrid_ Gritty Urban Target Logo.png:." \
    --distpath dist/linux \
    --workpath build/linux/work \
    --specpath build/linux \
    deadgrid.py

printf 'Built %s\n' "$ROOT/dist/linux/DEADGRID"