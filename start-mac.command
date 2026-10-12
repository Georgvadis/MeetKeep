#!/bin/zsh
set -e
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ -x /opt/homebrew/opt/python@3.14/bin/python3.14 ]]; then
  PYTHON_BIN=/opt/homebrew/opt/python@3.14/bin/python3.14
elif [[ -x /opt/homebrew/opt/python@3.13/bin/python3.13 ]]; then
  PYTHON_BIN=/opt/homebrew/opt/python@3.13/bin/python3.13
else
  PYTHON_BIN=python3
fi
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements.txt
if [[ "$1" == "--prepare-only" ]]; then
  exit 0
fi
.venv/bin/python sbervideo_downloader.py
