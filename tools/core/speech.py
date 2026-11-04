"""When someone is talking, by loudness alone, ported from ZCuts
(engine/08_sound.py speech_runs and true_runs, engine/10_ab_camera.py
own_sound_score; build 259).

A stretch is speech when it stands well above the room's own level: the
threshold sits between the quiet floor and the loud top of the sound.
Pauses shorter than 0.35 s do not count as gaps and sounds shorter than
80 ms do not count as speech.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from tools.core.media import HOPS_PER_SECOND, Levels

SPEECH_HOLD = 35  # hops: a pause shorter than this is not a gap
SPEECH_MINIMUM = 8  # hops: a sound shorter than this is not speech
PAUSE_SHORTEST = 0.25  # seconds of quiet that count as a pause (ZCuts INTERVIEW_GAP)


def true_runs(flags: np.ndarray) -> list[tuple[int, int]]:
    padded = np.concatenate(([0], flags.astype(np.int8), [0]))
    edges = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]


def speech_runs(power: np.ndarray) -> Optional[list[tuple[int, int]]]:
    """(first hop, end hop) of each run of speech in 10 ms mean squares,
    or None when there is no clear speech to follow."""
    if power.size < 50:
        return None
    level = 10.0 * np.log10(power + 1e-10)
    live = level[level > -80.0]
    if live.size < 20:
        return None
    top = float(np.percentile(live, 90))
    if top < -50.0:
        return None
    floor = float(np.percentile(live, 10))
    threshold = max(top - 25.0, min(floor + 12.0, top - 6.0), -55.0)
    merged: list[tuple[int, int]] = []
    for first, last in true_runs(level > threshold):
        if merged and first - merged[-1][1] <= SPEECH_HOLD:
            merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
        else:
            merged.append((first, last))
    runs = [(a, b) for a, b in merged if b - a >= SPEECH_MINIMUM]
    return runs or None


def pauses(levels: Levels, lo: float, hi: float) -> list[tuple[float, float]]:
    """(centre, length) in file seconds of each pause between speech in
    [lo, hi), for moving camera switches onto."""
    runs = speech_runs(levels.window(lo, hi - lo))
    if not runs:
        return []
    out = []
    for (_a, end), (start, _b) in zip(runs[:-1], runs[1:]):
        gap = (start - end) / HOPS_PER_SECOND
        if gap >= PAUSE_SHORTEST:
            out.append((lo + (end + start) / (2.0 * HOPS_PER_SECOND), gap))
    return out


def own_sound_score(levels: Optional[Levels], lo: float, hi: float) -> float:
    """How clearly a camera's own microphone carries the talking (dB), from
    the middle minute of [lo, hi); -99 for no usable sound."""
    if levels is None:
        return -99.0
    span = min(60.0, hi - lo)
    power = levels.window(lo + max(0.0, (hi - lo - span) / 2.0), span)
    if power.size < 50:
        return -99.0
    level = 10.0 * np.log10(power + 1e-10)
    live = level[level > -80.0]
    if live.size < 20:
        return -99.0
    loud, quiet = float(np.percentile(live, 90)), float(np.percentile(live, 10))
    return loud + 0.5 * (loud - quiet)  # talking well above the room, and loud: a close mic
