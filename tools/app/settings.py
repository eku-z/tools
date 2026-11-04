"""Where Tools keeps its settings and saved beats: %LOCALAPPDATA%\\Tools
(~/Library/Application Support/Tools on a Mac); TOOLS_DATA_DIR overrides it."""

from __future__ import annotations

import json
import os
import sys

DEFAULTS = {
    "tab": 0,
    "silences": {"track": "Auto", "preset": "Natural", "threshold_db": -35, "minimum_ms": 400, "padding_ms": 120},
    "ab": {"every": 8, "pauses": True, "sync": True},
    "montage": {"track": "Auto", "song_path": "", "per_cut": 0, "song_start": 0, "bpm": 0, "lead": 1,
                "fill_song": True, "pace": False, "mute": True},
    "finish": {"lut": "None", "upright": True, "also_vertical": False},
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


def load() -> dict:
    settings = json.loads(json.dumps(DEFAULTS))
    try:
        with open(os.path.join(data_dir(), "settings.json"), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
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
