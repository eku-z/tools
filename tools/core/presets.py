"""The silence settings, apart from the NumPy work so the window can show
them without loading NumPy (it loads after the window is on screen)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SilenceSettings:
    threshold_db: float = -35.0  # ZCuts DEFAULT_THRESHOLD_DB
    minimum: float = 0.40  # s, ZCuts MinimumSilenceMs 400
    padding: float = 0.12  # s, ZCuts PaddingMs 120


PRESETS = {
    "Natural": SilenceSettings(),
    "Tight": SilenceSettings(-35.0, 0.30, 0.08),
    "Loose": SilenceSettings(-38.0, 0.70, 0.20),  # ZCuts "Auto" -38/700/200
}
