#!/bin/bash
# FiberView - install and start from source on a Mac (for development).
# Double-click in Finder (first time: right-click -> Open). Needs Python 3.9+ (python.org or Homebrew).
# Creates a private environment in this folder (.venv); run it again to start FiberView.
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3.12 python3.11 python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer was not found. Install it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close"; exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Creating the Python environment with $PY ..."
  "$PY" -m venv .venv || { read -r -p "Could not create the environment. Press Enter"; exit 1; }
  .venv/bin/python -m pip install --upgrade pip
fi
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt || {
  read -r -p "Package installation failed. Press Enter to close"; exit 1; }
echo "Starting FiberView ..."
.venv/bin/python fiberview.py "$@"
