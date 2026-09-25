#!/usr/bin/env bash
# One-command setup for macOS and Linux.
#   ./setup.sh
set -euo pipefail

cd "$(dirname "$0")"
G=$'\033[92m'; R=$'\033[91m'; Y=$'\033[93m'; D=$'\033[2m'; X=$'\033[0m'

echo
echo "${D}sayso setup${X}"
echo

# --- python -----------------------------------------------------------
PY=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]:02d}")')
    if [ "$version" -ge 312 ]; then PY="$candidate"; break; fi
  fi
done

if [ -z "$PY" ]; then
  echo "${R}Python 3.12 or newer is required.${X}"
  echo "  macOS:  brew install python@3.12"
  echo "  Linux:  use your package manager, e.g. apt install python3.12 python3.12-venv"
  exit 1
fi
echo "${G}ok${X} python  $("$PY" --version)"

# --- environment ------------------------------------------------------
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  echo "${G}ok${X} created .venv"
else
  echo "${G}ok${X} .venv exists"
fi

./.venv/bin/pip install -q --upgrade pip
echo "${D}   installing dependencies (a few minutes on first run)...${X}"
./.venv/bin/pip install -q -r requirements.txt
echo "${G}ok${X} dependencies installed"

echo
echo "${D}Next steps:${X}"
echo "  1. ./.venv/bin/python -m shoonya.credentials  ${D}your API credentials${X}"
echo "  2. ./.venv/bin/python -m voice.calibrate      ${D}measures your microphone${X}"
echo "  3. ./.venv/bin/python -m shoonya.login        ${D}once per trading day${X}"
echo "  4. ./.venv/bin/python -m voice.doctor         ${D}checks everything${X}"
echo "  5. ./.venv/bin/python -m voice.main           ${D}run it${X}"
echo
