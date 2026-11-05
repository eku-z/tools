#!/bin/bash
# Start Tools on a Mac (double-click in Finder). Set up first: bash setup_mac.sh
cd "$(dirname "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
    echo "Tools is not set up yet. In Terminal, in this folder, run: bash setup_mac.sh"
    read -r -p "Press Return to close."
    exit 1
fi
exec .venv/bin/python -m tools "$@"
