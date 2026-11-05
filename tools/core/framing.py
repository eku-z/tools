"""Frame rates, sizes and how far to zoom a clip, ported from ZCuts
(engine/03_helpers.py parse_fps, engine/12_cleanup_and_events.py
parse_size and cover_zoom, engine/09_reframe.py fitted_size, and the
quarter-turn branch of engine/15_picture.py fix_picture; build 259).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

FPS_TEXT = re.compile(r"^\s*(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?")
ROTATION_SIGN = 1.0  # ZCuts LEVEL_SIGN: Resolve's RotationAngle turns anticlockwise
VERTICAL = (1080, 1920)


def parse_fps(value, fallback: float = 24.0) -> float:
    """A frame rate from Resolve or FFmpeg ("29.97", "30000/1001", 25).
    Resolve writes NTSC rates rounded; a rate that is a whole number divided
    by 1.001 is read as exactly that (ZCuts build 169)."""
    if value is None:
        return fallback
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            match = FPS_TEXT.match(str(value))
            if not match:
                return fallback
            result = float(match.group(1))
            if match.group(2) is not None:
                result /= float(match.group(2))
        if result <= 0 or not math.isfinite(result):
            return fallback
        whole = round(result * 1.001)
        if abs(result - result // 1) > 1e-9 and abs(result - whole / 1.001) < 0.005:
            result = whole * 1000.0 / 1001.0
        return result
    except (ValueError, ZeroDivisionError):
        return fallback


def parse_size(text):
    """'3840x2160' -> (3840.0, 2160.0), or None."""
    match = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)", str(text or ""))
    if not match:
        return None
    width, height = float(match.group(1)), float(match.group(2))
    return (width, height) if width > 0 and height > 0 else None


def fitted_size(source_size, frame_size, mode="scaleToFit"):
    """The size Resolve first gives a clip before any zoom is added."""
    frame_w, frame_h = frame_size
    source_w, source_h = source_size
    if mode == "stretch":
        return frame_w, frame_h
    if mode == "scaleToCrop":
        fit = max(frame_w / source_w, frame_h / source_h)
    elif mode == "centerCrop":
        fit = 1.0
    else:
        fit = min(frame_w / source_w, frame_h / source_h)
    return source_w * fit, source_h * fit


def cover_zoom(source_size, frame_size, degrees=0.0, mode="scaleToFit", spare=1.02, margin=0.0, shift=(0.0, 0.0)):
    """Zoom needed so a (slightly) rotated picture still fills the whole
    frame. Resolve first fits the clip inside the frame, so a clip whose
    shape differs from the timeline's starts out smaller in one direction;
    that is included. ``shift`` is how far the picture was moved (Resolve's
    Pan and Tilt, in timeline pixels): the edge it moved away from needs
    that much more picture."""
    frame_w, frame_h = frame_size
    picture_w, picture_h = fitted_size(source_size, frame_size, mode)
    folded = abs(degrees) % 180.0  # a half turn needs no more than none
    radians = math.radians(min(folded, 180.0 - folded))
    wide = frame_w + 2.0 * (margin + abs(shift[0]))
    high = frame_h + 2.0 * (margin + abs(shift[1]))
    need_w = wide * math.cos(radians) + high * math.sin(radians)
    need_h = wide * math.sin(radians) + high * math.cos(radians)
    return max(need_w / picture_w, need_h / picture_h) * spare


@dataclass
class Transform:
    zoom: float = 1.0
    rotation: float = 0.0  # Resolve RotationAngle (anticlockwise degrees)


def placement(source_size, frame_size, turn: int, fill: bool, mode="scaleToFit") -> Transform:
    """How to show a clip that needs ``turn`` degrees clockwise to be upright.

    A quarter turn: on a tall frame it fills (the shapes nearly match, so
    very little is cropped); on a wide frame it is shown whole, with bars
    at the sides, unless ``fill``. Without a quarter turn, ``fill`` zooms
    just enough to cover the frame (a clip that already matches its shape
    is left at 100 %)."""
    turn = int(turn) % 360
    rotation = 0.0
    if turn in (90, 270):
        rotation = ROTATION_SIGN * (90.0 if turn == 270 else -90.0)
        fitted_w, fitted_h = fitted_size(source_size, frame_size, mode)
        fit = min(frame_size[0] / fitted_h, frame_size[1] / fitted_w)
        cover = max(frame_size[0] / fitted_h, frame_size[1] / fitted_w)
        tall = frame_size[1] > frame_size[0]
        return Transform(cover * 1.002 if (tall or fill) else fit, rotation)
    if turn == 180:
        rotation = 180.0
    if fill:
        zoom = cover_zoom(source_size, frame_size, 0.0, mode, 1.002)
        return Transform(zoom if zoom > 1.004 else 1.0, rotation)
    return Transform(1.0, rotation)
