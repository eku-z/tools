"""Finding silence, ported from ZCuts (engine/04_silence.py
detect_silence_intervals, merge_intervals, complement_ranges; build 259)
by way of Edits (edits/automation/silence.py).

A 20 ms window is silent at or below the threshold (dBFS); runs of silence
at least the minimum length are kept, shrunk by the padding at both ends,
and kept only if still at least the minimum length. Tools has no voice
detector (no models), so this is ZCuts' loudness-only rule.
"""

from __future__ import annotations

import numpy as np

from tools.core.media import HOPS_PER_SECOND, Levels
from tools.core.presets import PRESETS, SilenceSettings  # noqa: F401 - used by the jobs

WINDOW_HOPS = 2  # ZCuts ANALYSIS_WINDOW_MS = 20


def merge_intervals(intervals):
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda iv: iv[0])
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def complement_ranges(start, end, removals):
    """Kept ranges of [start, end) between removal intervals."""
    if end <= start:
        return []
    clipped = []
    for a, b in removals:
        a, b = max(start, a), min(end, b)
        if b > a:
            clipped.append((a, b))
    kept = []
    cursor = start
    for a, b in merge_intervals(clipped):
        if a > cursor:
            kept.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        kept.append((cursor, end))
    return [(a, b) for a, b in kept if b - a > 0.001]


def silent_runs(levels: Levels, threshold_db: float, minimum: float) -> list[tuple[float, float]]:
    """Every stretch (seconds into the file) whose 20 ms windows are all at
    or below the threshold and that lasts at least ``minimum``."""
    power = levels.power
    if power.size == 0:
        return []
    full = power.size // WINDOW_HOPS
    windows = power[:full * WINDOW_HOPS].reshape(full, WINDOW_HOPS).mean(axis=1)
    if power.size % WINDOW_HOPS:
        windows = np.append(windows, power[full * WINDOW_HOPS:].mean())
    # 20 log10(rms) with ZCuts' floor of 1e-6, as mean squares
    silent = (10.0 * np.log10(np.maximum(windows, 1e-12)) <= threshold_db).astype(np.int8)
    edges = np.diff(np.concatenate(([0], silent, [0])))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    step = WINDOW_HOPS / HOPS_PER_SECOND
    found = []
    for a, b in zip(starts, ends):
        t0 = levels.start + int(a) * step
        t1 = levels.end if int(b) >= silent.size else levels.start + int(b) * step
        if t1 - t0 >= minimum:
            found.append((t0, t1))
    return found


def detect_silence(levels: Levels, st: SilenceSettings) -> list[tuple[float, float]]:
    """ZCuts' detect_silence_intervals: the parts to cut, in file seconds."""
    out = []
    for a, b in silent_runs(levels, st.threshold_db, st.minimum):
        pa, pb = a + st.padding, b - st.padding
        if pb > pa and pb - pa >= st.minimum:
            out.append((pa, pb))
    return out


def keep_ranges(lo: float, hi: float, cuts) -> list[tuple[float, float]]:
    """What is kept of a clip playing [lo, hi) of its file."""
    return complement_ranges(lo, hi, cuts)
