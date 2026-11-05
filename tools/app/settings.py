"""Where Tools keeps its settings and saved beats: %LOCALAPPDATA%\\Tools
(~/Library/Application Support/Tools on a Mac); TOOLS_DATA_DIR overrides it."""

from __future__ import annotations

import json
import os
import sys

# Every switch starts off (owner, 4 Nov 2026): you tick what you want by hand.
VERSION = 3  # settings saved by an older version are not used (their layout changed)
DEFAULTS = {
    "version": VERSION,
    "ticked": {"silences": False, "ab": False, "montage": False, "vertical": False, "look": False},
    "log_open": False,
    "silences": {"track": "Auto", "preset": "Natural", "threshold_db": -35, "minimum_ms": 400, "padding_ms": 120,
                 "all_tracks": False},
    "ab": {"every": 8, "pauses": False, "sync": False},
    "montage": {"track": "Auto", "song_path": "", "no_song": False, "per_cut": 0, "song_start": 0, "bpm": 0,
                "lead": 0, "fill_song": False, "pace": False, "mute": False},
    "vertical": {"upright": False},
    "look": {"lut": "None", "upright": False, "fill": False},
}


def data_dir() -> str:
    if os.environ.get("TOOLS_DATA_DIR"):
        path = os.environ["TOOLS_DATA_DIR"]
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Tools")
    else:
        path = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Tools")
    os.makedirs(path, exist_ok=True)
    return path


def load_defaults() -> dict:
    return json.loads(json.dumps(DEFAULTS))


def load() -> dict:
    settings = load_defaults()
    try:
        with open(os.path.join(data_dir(), "settings.json"), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return settings
    if not isinstance(saved, dict) or saved.get("version") != VERSION:
        return settings
    for key, value in saved.items():
        if isinstance(settings.get(key), dict) and isinstance(value, dict):
            settings[key].update({k: v for k, v in value.items() if k in settings[key]})
        elif key in settings:
            settings[key] = value
    return settings


def save(settings: dict) -> None:
    path = os.path.join(data_dir(), "settings.json")
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=1)
        os.replace(path + ".tmp", path)
    except OSError:
        pass
