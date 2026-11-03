#!/usr/bin/env bash
# Build the standalone one-file `pf2opn` executable into dist/pf2opn.
#
# Requires the `build` extra:
#   python -m venv .venv && .venv/bin/pip install -e '.[build]'
# Then, from the repo root (or anywhere): bash scripts/build-binary.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"

"$PYTHON" -m PyInstaller --clean --noconfirm pf2opn.spec
echo "Built: $(pwd)/dist/pf2opn"
