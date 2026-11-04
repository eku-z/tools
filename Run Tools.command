#!/bin/sh
# Start Tools on a Mac (python3 with PySide6-Essentials, numpy and av:
# python3 -m pip install -r requirements.txt). Not tried on a Mac yet.
cd "$(dirname "$0")" || exit 1
exec python3 -m tools "$@"
