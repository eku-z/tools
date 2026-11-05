#!/bin/bash
# Run every Tools test on a Mac (offscreen, stand-in Resolve). Set up first: bash setup_mac.sh
cd "$(dirname "$0")" || exit 1
.venv/bin/python tests/run_tests.py "$@"
read -r -p "Press Return to close."
