#!/usr/bin/env bash
set -euo pipefail

pick_python() {
    if [[ -n "${PYTHON_BIN:-}" ]]; then
        printf '%s\n' "$PYTHON_BIN"
        return 0
    fi

    local candidate
    local candidates=(
        "/usr/bin/python3.12"
        "/usr/bin/python3.11"
        "/usr/bin/python3.10"
        "/usr/local/bin/python3"
        "python3"
        "python"
    )

    for candidate in "${candidates[@]}"; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi
        if "$candidate" - <<'PY' >/dev/null 2>&1
import sysconfig
shared = bool(sysconfig.get_config_var("Py_ENABLE_SHARED"))
ldlibrary = sysconfig.get_config_var("LDLIBRARY")
raise SystemExit(0 if shared and bool(ldlibrary) else 1)
PY
        then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

PYTHON_BIN="$(pick_python)" || {
    echo "ERROR: No shared-library Python interpreter was found for PyInstaller." >&2
    echo "Install a Python build with libpython*.so or set PYTHON_BIN=/path/to/python3.12" >&2
    exit 1
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p build/linux/site-packages
printf 'Using Python interpreter: %s\n' "$PYTHON_BIN"
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