#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check Python 3.10+
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found. Install Python 3.10+ and try again."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "ERROR: Python $PY_VERSION found, but 3.10+ is required."
    exit 1
fi

# Create venv if needed
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Install/upgrade dependencies
pip install -q -r requirements.txt

# Set GDAL environment
if [ -d "$SCRIPT_DIR/bin/linux-x64" ]; then
    export PATH="$SCRIPT_DIR/bin/linux-x64:$PATH"
    [ -d "$SCRIPT_DIR/bin/linux-x64/share/proj" ] && export PROJ_LIB="$SCRIPT_DIR/bin/linux-x64/share/proj"
    [ -d "$SCRIPT_DIR/bin/linux-x64/share/gdal" ] && export GDAL_DATA="$SCRIPT_DIR/bin/linux-x64/share/gdal"
    echo "Using bundled GDAL from bin/linux-x64/"
elif command -v gdalwarp &>/dev/null; then
    echo "Using system GDAL: $(gdalwarp --version 2>&1 | head -1)"
else
    echo "WARNING: No GDAL found. Pipelines that require GDAL will fail."
    echo "Install GDAL: sudo apt install gdal-bin"
fi

echo "Starting Geographica Companion..."
python3 companion.py
