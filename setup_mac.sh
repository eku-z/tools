#!/bin/bash
# Set Tools up on a Mac (Apple chip or Intel), like Edits' setup_mac.sh:
#
#   bash setup_mac.sh
#
# Python 3.12 in its own environment in .venv (nothing else on the Mac
# changes), Tools' packages, then a check. FFmpeg is optional: without it
# PyAV reads every file (long ones a little slower).
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This is for macOS. On Windows: Run Tools.bat (see README.md)." >&2
    exit 1
fi

# 1. Python 3.12 (python.org's installer or Homebrew's; the system python3 is too old)
PY=""
for cand in python3.12 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
            /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$(command -v "$cand")"; break; fi
done
if [[ -z "$PY" ]] && command -v brew >/dev/null 2>&1; then
    read -r -p "Python 3.12 is not installed. Install it with Homebrew (brew install python@3.12)? [y/N] " ok
    if [[ "$ok" == "y" || "$ok" == "Y" ]]; then
        brew install python@3.12
        PY="$(brew --prefix)/bin/python3.12"
    fi
fi
if [[ -z "$PY" ]]; then
    echo "Python 3.12 is needed: install it from https://www.python.org/downloads/macos/" >&2
    echo "(or with Homebrew: brew install python@3.12), then run bash setup_mac.sh again." >&2
    exit 1
fi
echo "Python: $PY ($("$PY" --version))"

# 2. Its own environment
if [[ ! -x .venv/bin/python ]]; then
    "$PY" -m venv .venv
fi
VPY=.venv/bin/python
"$VPY" -m pip install --upgrade pip >/dev/null
"$VPY" -m pip install -r requirements.txt

# 3. FFmpeg, optional (faster on long interviews)
if ! command -v ffmpeg >/dev/null 2>&1 && [[ ! -x /opt/homebrew/bin/ffmpeg && ! -x /usr/local/bin/ffmpeg ]]; then
    if command -v brew >/dev/null 2>&1; then
        read -r -p "FFmpeg is not installed (optional: long files read faster with it). Install it with Homebrew? [y/N] " ok
        if [[ "$ok" == "y" || "$ok" == "Y" ]]; then brew install ffmpeg; fi
    fi
fi

# 4. The launchers: double-clickable (a folder copied from another computer
#    loses this, and macOS may hold back scripts it was given)
chmod +x "Run Tools.command" "Run Tests.command" 2>/dev/null || true
xattr -d com.apple.quarantine "Run Tools.command" "Run Tests.command" 2>/dev/null || true

# 5. What Tools found
echo
"$VPY" -m tools --check
echo
echo "Done. Start Tools by double-clicking \"Run Tools.command\" in Finder."
echo "In Resolve Studio: Preferences > System > General > External scripting using: Local."
