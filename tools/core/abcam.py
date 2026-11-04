"""A/B camera cut planning, ported from ZCuts (engine/10_ab_camera.py
interview_boundaries and the piece loop of apply_interview; build 259).

Switch points are spaced evenly (every N seconds) and each is moved to a
nearby pause in the sound, preferring a longer pause (the end of a
sentence over a breath). All times are seconds of the master sound.
"""

from __future__ import annotations

from dataclasses import dataclass

EDGE = 0.1  # seconds kept clear of the ends of the clips (ZCuts INTERVIEW_EDGE)
PAUSE_LENGTH = 0.25  # a pause given as a bare centre counts as this long


def boundaries(lo: float, hi: float, every: float, gaps, snap: bool = True) -> tuple[list[float], int]:
    """Evenly spaced switch times, each moved to a pause within reach.
    ``gaps`` are pause centres or (centre, length). Returns (cuts including
    lo and hi, how many were moved)."""
    shortest = max(1.5, 0.4 * every)
    reach = min(2.0, 0.3 * every)
    cuts = [lo]
    snapped = 0
    step = 1
    pauses = [(float(g[0]), float(g[1])) if isinstance(g, (tuple, list)) else (float(g), PAUSE_LENGTH)
              for g in gaps]
    while lo + step * every <= hi - shortest:
        wanted = lo + step * every
        pick = wanted
        if snap:
            near = [(-(min(length, 1.5) - 0.25 * abs(centre - wanted)), abs(centre - wanted), centre)
                    for centre, length in pauses if abs(centre - wanted) <= reach]
            if near:
                pick = min(near)[2]
        if pick - cuts[-1] >= shortest and hi - pick >= shortest:
            snapped += pick != wanted
            cuts.append(pick)
        step += 1
    cuts.append(hi)
    return cuts, snapped


@dataclass
class Angle:
    """One camera: the part of its file in use, its frame rate, and the
    seconds to add to a time in its file to get master-sound time."""
    first_frame: int
    end_frame: int  # exclusive
    fps: float
    offset: float


@dataclass
class Piece:
    angle: int  # index into the angles
    first: int  # source frames, end exclusive
    last: int
    record: int  # timeline frame (from the timeline's start)


def piece(number: int, current: int, cuts: list[float], lo: float, angles: list[Angle],
          out_fps: float) -> tuple[Piece | None, bool]:
    """Piece ``number`` (camera A, B, A, B...), placed at timeline frame
    ``current``, where the piece before really ended. It starts at the
    master sound's moment for that spot, so the lining up stays exact and
    there are no gaps even when a camera's frame rate differs from the
    timeline's. Returns (piece or None, whether it was shortened to stay
    inside its clip)."""
    end_tl = int(round((cuts[number + 1] - lo) * out_fps))
    wanted_tl = end_tl - current
    if wanted_tl < 1:
        return None, False
    which = number % len(angles)
    angle = angles[which]
    here = lo + current / out_fps
    first = int(round((here - angle.offset) * angle.fps))
    last = first + max(1, int(round(wanted_tl * angle.fps / out_fps)))
    wanted = last - first
    first = max(first, angle.first_frame)
    last = min(last, angle.end_frame)
    if last <= first:
        return None, True
    return Piece(which, first, last, current), last - first != wanted


def length_on_timeline(p: Piece, angles: list[Angle], out_fps: float) -> int:
    return int(round((p.last - p.first) * out_fps / angles[p.angle].fps))


def pieces(cuts: list[float], lo: float, angles: list[Angle], out_fps: float) -> tuple[list[Piece], int]:
    """Every piece, each placed where the one before ends."""
    out: list[Piece] = []
    clamped = 0
    current = 0
    for number in range(len(cuts) - 1):
        p, short = piece(number, current, cuts, lo, angles, out_fps)
        clamped += short
        if p is not None:
            out.append(p)
            current += length_on_timeline(p, angles, out_fps)
    return out, clamped
