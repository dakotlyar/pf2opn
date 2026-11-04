#!/usr/bin/env bash
# Build the standalone one-file `pf2opn` executable into dist/pf2opn.
#
# Requires the `build` extra:
#   python -m venv .venv && .venv/bin/pip install -e '.[build]'
# Then, from the repo root (or anywhere): bash scripts/build-binary.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# Prefer the project venv (where the build extra is installed); fall back to python3.
if [ -z "${PYTHON:-}" ] && [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
else
  PYTHON="${PYTHON:-python3}"
fi

"$PYTHON" -m PyInstaller --clean --noconfirm pf2opn.spec
echo "Built: $(pwd)/dist/pf2opn"
