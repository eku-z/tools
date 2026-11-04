"""Lining two recordings up by what they hear, ported from ZCuts
(engine/10_ab_camera.py audio_envelope, best_match, match_windows,
settle_matches, sync_offset_stream; build 259) by way of Edits
(edits/automation/sync.py).

Each recording's loudness every 10 ms is squashed with a log and its slow
drift removed, so a camera microphone and a clip-on microphone give the
same pattern; the best FFT cross-correlation peak, checked against the
runner-up, gives the offset. ZCuts' lip-movement fallback is not ported
(it needs to decode the picture, which Tools never does).
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from tools.core.media import HOPS_PER_SECOND, Levels

SYNC_RATE = HOPS_PER_SECOND  # envelope samples per second (10 ms)
SYNC_SEARCH = 90.0  # seconds either way to look for the match
SYNC_WINDOW = 90.0  # seconds of sound compared at a time
SYNC_MIN_STRENGTH = 0.18  # how alike the two sounds must be
SYNC_MIN_RATIO = 1.3  # best match against the next best
SYNC_AGREE = 0.06  # two windows may differ by this many seconds


def envelope(levels: Levels, start: float, length: float) -> Optional[np.ndarray]:
    """The loudness pattern of [start, start + length), or None when it is
    under 2 s or has no variation to match."""
    power = levels.window(start, length)
    if power.size < 2 * SYNC_RATE:
        return None
    loud = np.log(np.sqrt(power + 1e-12) + 1e-4)
    width = SYNC_RATE // 2
    trend = np.convolve(np.pad(loud, width, mode="edge"), np.ones(2 * width + 1) / (2 * width + 1),
                        mode="valid")[:loud.size]
    pattern = loud - trend
    spread = float(pattern.std())
    if spread < 1e-3:
        return None
    return (pattern / spread).astype(np.float32)


def best_match(master: np.ndarray, other: np.ndarray) -> Optional[tuple[float, float, float]]:
    """Where a short pattern fits best in a longer one: (shift in samples,
    strength 0..1, ratio to the runner-up)."""
    if other.size < master.size:
        return None
    size = 1 << int(master.size + other.size - 1).bit_length()
    spectrum = np.fft.rfft(other, size) * np.conj(np.fft.rfft(master, size))
    raw = np.fft.irfft(spectrum, size)[:other.size - master.size + 1]
    power = np.concatenate(([0.0], np.cumsum(other.astype(np.float64) ** 2)))
    local = np.sqrt(np.maximum(power[master.size:master.size + raw.size] - power[:raw.size], 1e-9))
    score = raw / (local * math.sqrt(float(np.sum(master.astype(np.float64) ** 2))) + 1e-9)
    peak = int(np.argmax(score))
    near = int(0.3 * SYNC_RATE)
    rest = np.concatenate((score[:max(0, peak - near)], score[peak + near + 1:]))
    runner = float(rest.max()) if rest.size else 0.0
    value = float(score[peak])
    shift = float(peak)
    if 0 < peak < score.size - 1:  # between two samples
        a, b, c = score[peak - 1], score[peak], score[peak + 1]
        bend = a - 2.0 * b + c
        if bend < 0:
            shift += 0.5 * (a - c) / bend
    return shift, value, value / max(runner, 0.02)


def _on_grid(levels: Levels, t: float) -> float:
    """The time of the 10 ms step a window starting at ``t`` really starts on."""
    return levels.start + max(0, int(round((t - levels.start) * SYNC_RATE))) / SYNC_RATE


def _match_windows(master: Levels, other: Levels, starts, window, other_start, other_span, notes):
    found = []
    other_start = _on_grid(other, other_start)
    wide = envelope(other, other_start, other_span)
    if wide is None:
        return found
    for start in starts:
        start = _on_grid(master, start)
        ref = envelope(master, start, window)
        if ref is None:
            continue
        m = best_match(ref, wide)
        if m is None:
            continue
        shift, value, ratio = m
        notes["strength"] = max(notes.get("strength", 0.0), value)
        if value >= SYNC_MIN_STRENGTH and ratio >= SYNC_MIN_RATIO:
            found.append((start - other_start - shift / SYNC_RATE, value))
    return found


def _settle(found):
    """Average the matches that agree; the strongest alone if they differ."""
    if not found:
        return None
    best = max(found, key=lambda f: f[1])
    agree = [f for f in found if abs(f[0] - best[0]) <= SYNC_AGREE]
    return sum(f[0] for f in agree) / len(agree), max(f[1] for f in agree), len(agree)


def offset(master: Levels, m_lo: float, m_hi: float, other: Levels, o_lo: float, o_hi: float,
           hint: float = 0.0, notes: Optional[dict] = None) -> Optional[tuple[float, float, int]]:
    """Seconds to add to a time in the other file to get the matching time
    in the master file, the match strength and how many windows agreed;
    None if nothing matched. ``[m_lo, m_hi)`` and ``[o_lo, o_hi)`` are the
    parts of each file in use; ``hint`` is where the timeline puts them now,
    which is searched first. When nothing is found there, the whole other
    file is searched."""
    notes = {} if notes is None else notes
    first, last = max(m_lo, o_lo + hint), min(m_hi, o_hi + hint)
    if last - first >= 20.0:
        length = last - first
        window = max(8.0, min(SYNC_WINDOW, 0.4 * length))
        if length >= window + 1.0:
            margin = min(SYNC_SEARCH, max(0.0, (length - 2 * window) / 2.0))
            low, high = first + margin, last - margin - window
            starts = ([low + 0.2 * (high - low), low + 0.8 * (high - low)] if high > low + 2.0
                      else [first + (length - window) / 2.0])
            other_start = max(0.0, starts[0] - hint - SYNC_SEARCH)
            span = window + 2 * SYNC_SEARCH + (starts[-1] - starts[0])
            r = _settle(_match_windows(master, other, starts, window, other_start, span, notes))
            if r is not None:
                return r
    length = m_hi - m_lo
    window = max(8.0, min(SYNC_WINDOW, 0.4 * length))
    if length < window + 1.0:
        return None
    room = length - window
    starts = [m_lo + room * f for f in (0.15, 0.5, 0.85)]
    span = min(o_hi + 900.0, 4 * 3600.0)
    return _settle(_match_windows(master, other, starts, window, 0.0, span, notes))
