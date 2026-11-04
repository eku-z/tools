"""Finding a song's beats, ported from ZCuts (engine/07_music.py
onset_envelope ... fit_beat_grid and detect_beats; build 259).

No model: an onset envelope (spectral flux in 32 log-spaced bands, 10 ms
steps at 16 kHz), a first tempo guess from autocorrelation, several tempo
candidates each followed by dynamic-programming beat tracking, the
candidate whose beats land on the strongest hits, a check against its 3:2
and 2:3 neighbours, the beat that starts each bar, and finally the tempo
to 0.02 BPM with the beats laid on an exact grid when the song holds it.
(Edits ported only the first half; this is all of ZCuts' tracker.)
"""

from __future__ import annotations

import math

import numpy as np

SAMPLE_RATE = 16000
BEAT_HOP = 160  # 10 ms steps
BEAT_WINDOW = 512
BEAT_RATE = SAMPLE_RATE / float(BEAT_HOP)
SONG_MAX_SECONDS = 1200
ONSET_LAG = 0.010  # measured: the hit is heard about 10 ms after the onset
SHIFT = (BEAT_WINDOW / 2.0 - BEAT_HOP / 2.0) / SAMPLE_RATE + ONSET_LAG
FOLD_UP = 4  # fold on a grid four times finer than the steps


def _correlation(envelope: np.ndarray) -> np.ndarray:
    size = 1
    while size < 2 * envelope.size:
        size *= 2
    spectrum = np.fft.rfft(envelope, size)
    return np.fft.irfft(spectrum * np.conj(spectrum))[:envelope.size]


def band_spectrogram(samples: np.ndarray) -> np.ndarray:
    window = np.hanning(BEAT_WINDOW).astype(np.float32)
    bins = BEAT_WINDOW // 2 + 1
    edges = np.unique(np.round(np.geomspace(2, bins - 1, 33)).astype(int))
    count = 1 + (samples.size - BEAT_WINDOW) // BEAT_HOP
    out = np.zeros((max(0, count), len(edges) - 1), dtype=np.float32)
    for first in range(0, max(0, count), 4000):
        last = min(count, first + 4000)
        index = np.arange(first, last)[:, None] * BEAT_HOP + np.arange(BEAT_WINDOW)[None, :]
        spectrum = np.abs(np.fft.rfft(samples[index] * window, axis=1))
        out[first:last] = np.log1p(50.0 * np.add.reduceat(spectrum, edges[:-1], axis=1)[:, :len(edges) - 1])
    return out


def flux_of(bands: np.ndarray, rate: float = BEAT_RATE) -> np.ndarray:
    """How strongly something new starts at each step, keeping only what
    stands out from the half second around it (ZCuts onset_envelope)."""
    rise = np.zeros(bands.shape[0], dtype=np.float32)
    rise[1:] = np.maximum(0.0, np.diff(bands, axis=0)).sum(axis=1)
    width = int(rate * 0.5) | 1
    local = np.convolve(rise, np.ones(width) / width, mode="same")
    rise = np.maximum(0.0, rise - local)
    deviation = float(rise.std())
    return rise / deviation if deviation > 0 else rise


def estimate_period(envelope: np.ndarray) -> float:
    """Steps between beats, favouring tempos around 120 BPM."""
    correlation = _correlation(envelope)
    low = int(BEAT_RATE * 60.0 / 200.0)
    high = min(int(BEAT_RATE * 60.0 / 60.0), envelope.size - 1)
    lags = np.arange(low, high + 1)
    tempo = 60.0 * BEAT_RATE / lags
    weight = np.exp(-0.5 * (np.log2(tempo / 120.0) / 0.9) ** 2)
    scores = correlation[lags] * weight
    for multiple in (2, 3):  # a real beat also repeats at twice and three times its spacing
        further = lags * multiple
        ok = further < envelope.size
        scores[ok] += 0.5 * correlation[further[ok]] * weight[ok]
    return float(lags[int(np.argmax(scores))])


def track_beats(envelope: np.ndarray, period: float) -> list[int]:
    """Pick the evenly spaced run of steps that hits the most onsets."""
    spread = max(1.0, period / 32.0)
    reach = int(4 * spread)
    kernel = np.exp(-0.5 * (np.arange(-reach, reach + 1) / spread) ** 2)
    local = np.convolve(envelope, kernel, mode="same")
    total = local.size
    offsets = np.arange(-int(round(2 * period)), -int(round(period / 2)))
    penalty = -100.0 * np.log(-offsets / period) ** 2
    score = local.copy()
    link = np.full(total, -1, dtype=np.int64)
    first_off, last_off, n_off = int(offsets[0]), int(offsets[-1]), offsets.size
    for index in range(total):
        first = index + first_off
        skip = -first if first < 0 else 0
        if skip >= n_off:
            continue
        candidates = score[first + skip:index + last_off + 1] + penalty[skip:]
        best = int(candidates.argmax())
        if candidates[best] > 0 or index > 2 * period:
            score[index] = local[index] + candidates[best]
            link[index] = first + skip + best
    tail = max(0, total - int(round(period)))
    position = tail + int(np.argmax(score[tail:]))
    beats = []
    while position >= 0:
        beats.append(position)
        position = int(link[position])
    beats.reverse()
    return beats


def strength_at(curve: np.ndarray, steps, reach: int = 3) -> np.ndarray:
    padded = np.pad(curve, reach)
    return np.array([padded[s:s + 2 * reach + 1].max() for s in steps])


def zscore(values) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    spread = values.std()
    return (values - values.mean()) / spread if spread > 0 else values * 0.0


def tempo_candidates(envelope: np.ndarray, first_guess: float) -> list[tuple[int, float]]:
    """A few plausible beat spacings (in steps), strongest first."""
    correlation = _correlation(envelope)
    low = int(BEAT_RATE * 60.0 / 200.0)
    high = min(int(BEAT_RATE * 60.0 / 60.0), envelope.size - 2)
    lags = np.arange(low, high + 1)
    values = correlation[lags]
    peaks = [int(lags[i]) for i in range(1, len(lags) - 1)
             if values[i] >= values[i - 1] and values[i] >= values[i + 1]]
    peaks.sort(key=lambda lag: -correlation[lag])
    chosen = [int(round(first_guess))]
    for lag in peaks[:6]:
        for option in (lag, lag * 2, lag // 2):
            if low <= option <= high and all(abs(option - c) > 0.04 * c for c in chosen):
                chosen.append(int(option))
    top = float(max(correlation[low:high + 1].max(), 1e-9))
    return [(lag, float(max(0.0, correlation[lag]) / top)) for lag in chosen[:8]]


def choose_beats(envelope: np.ndarray, bands: np.ndarray, first_guess: float) -> tuple[list[int], list]:
    """Follow the beat at each plausible tempo and keep the one whose beats
    land on the strongest hits. Returns (steps, the tempos tried)."""
    full = flux_of(bands)
    bass = flux_of(bands[:, :8])
    best = None
    tried = []
    options = tempo_candidates(envelope, first_guess)
    strongest = max(options, key=lambda option: option[1])[0]
    for period, repeats in options:
        steps = track_beats(envelope, float(period))
        if len(steps) < 8:
            continue
        points = np.asarray(steps, dtype=int)
        tempo = 60.0 * BEAT_RATE / float(np.median(np.diff(points)))
        hit = float(strength_at(full, points).mean() + strength_at(bass, points).mean())
        weight = float(np.exp(-0.5 * (np.log2(tempo / 105.0) / 0.75) ** 2))
        # The beats land on strong hits, the song really repeats at this
        # spacing, and the tempo is a usual one.
        score = hit * (0.35 + 0.65 * weight) * (0.5 + repeats)
        ratio = float(period) / float(strongest)
        if min(abs(ratio - r) / r for r in (0.5, 1.0, 2.0)) < 0.03:
            score *= 1.15
        tried.append((round(tempo, 1), round(score, 2)))
        if best is None or score > best[0]:
            best = (score, steps)
    return (best[1] if best else []), tried


def refine(bands: np.ndarray, steps, rate: float = BEAT_RATE) -> tuple[np.ndarray, int, dict]:
    """Halve a double-time reading, then find which beat starts each bar.
    Returns (steps, downbeat index 0..3, notes)."""
    steps = np.asarray(steps, dtype=int)
    full = flux_of(bands, rate)
    bass = flux_of(bands[:, :8], rate)
    notes: dict = {"halved": False}

    def strengths(points):
        return strength_at(full, points), strength_at(bass, points)

    all_strength, bass_strength = strengths(steps)
    tempo = 60.0 * rate / float(np.median(np.diff(steps)))
    if tempo > 112 and len(steps) >= 16:
        # Read twice too fast? Then every other "beat" is only a hi-hat tick:
        # clearly weaker overall, and almost never a bass hit.
        combined = zscore(all_strength) + zscore(bass_strength)
        even, odd = combined[0::2].mean(), combined[1::2].mean()
        bass_even, bass_odd = bass_strength[0::2].mean(), bass_strength[1::2].mean()
        bass_ratio = max(bass_even, bass_odd) / max(1e-6, min(bass_even, bass_odd))
        if max(even, odd) - min(even, odd) > 0.9 and bass_ratio > 1.8:
            steps = steps[0::2] if even > odd else steps[1::2]
            notes["halved"] = True

    def bar_scores(points):
        strong, low = strengths(points)
        middle = bands[:, 6:26]  # chords and bass notes usually change at the start of a bar
        spans = [middle[a:b].mean(axis=0) if b > a else middle[a] for a, b in zip(points[:-1], points[1:])]
        change = np.zeros(len(points))
        for index in range(1, len(spans)):
            one, two = spans[index - 1], spans[index]
            norm = float(np.linalg.norm(one) * np.linalg.norm(two))
            change[index] = 1.0 - float(np.dot(one, two)) / norm if norm > 0 else 0.0
        return low, change, strong

    low, change, strong = bar_scores(steps)
    # A slow reading can sit on the off-beats (the snares). Compare with the
    # grid shifted half a beat: the real one has the bass and chord changes.
    tempo = 60.0 * rate / float(np.median(np.diff(steps)))
    if tempo < 100 and len(steps) >= 12:
        shifted = ((steps[:-1] + steps[1:]) // 2).astype(int)
        low2, change2, strong2 = bar_scores(shifted)
        here = float(np.sort(low)[len(low) // 2:].mean() + 40 * np.sort(change)[-len(change) // 4:].mean())
        there = float(np.sort(low2)[len(low2) // 2:].mean() + 40 * np.sort(change2)[-len(change2) // 4:].mean())
        if there > 1.25 * here:
            steps = shifted
            low, change, strong = low2, change2, strong2
            notes["shifted"] = True
    score = zscore(low) + 1.5 * zscore(change) + 0.5 * zscore(strong)
    phases = [float(score[p::4].mean()) for p in range(4)]
    ordered = sorted(phases, reverse=True)
    notes["bar_confidence"] = float(ordered[0] - ordered[1])
    return steps, int(np.argmax(phases)), notes


def fine_envelope(envelope: np.ndarray):
    x = np.arange(envelope.size * FOLD_UP) / float(FOLD_UP)
    return x, np.interp(x, np.arange(envelope.size), envelope)


def folded_beat(x, fine, bpm, rate=BEAT_RATE):
    """The song folded onto one beat: (profile, steps per beat, bins)."""
    period = rate * 60.0 / bpm
    bins = max(8, int(round(period * FOLD_UP)))
    phase = ((x % period) / period * bins).astype(int) % bins
    counts = np.maximum(1, np.bincount(phase, minlength=bins))
    profile = np.bincount(phase, weights=fine, minlength=bins) / counts
    width = max(1, int(round(0.6 * FOLD_UP)))
    kernel = np.exp(-0.5 * (np.arange(-3 * width, 3 * width + 1) / width) ** 2)
    kernel /= kernel.sum()
    wrapped = np.concatenate([profile[-3 * width:], profile, profile[:3 * width]])
    smooth = np.convolve(wrapped, kernel, mode="same")[3 * width:-3 * width]
    return smooth, period, bins


def tempo_fit(x, fine, bpm, span=0.03, step=0.0004, rate=BEAT_RATE):
    """Best tempo near bpm: (strength, bpm, where the beat falls in steps,
    how much of the song's energy sits on that one spot)."""
    best = None
    for factor in np.arange(1.0 - span, 1.0 + span + 1e-9, step):
        smooth, period, bins = folded_beat(x, fine, bpm * factor, rate)
        base = float(np.median(smooth))
        top = float(smooth.max())
        if best is None or top - base > best[0]:
            extra = np.maximum(0.0, smooth - base).mean()
            best = (top - base, bpm * factor, int(smooth.argmax()) / float(bins) * period,
                    (top - base) / max(1e-9, float(extra)))
    return best


def usual_tempo(bpm: float) -> float:
    """The same tempo written the usual way, between 80 and 180 BPM."""
    while bpm < 80.0:
        bpm *= 2.0
    while bpm >= 180.0:
        bpm /= 2.0
    return bpm


def prior_distance(bpm: float) -> float:
    return abs(math.log2(max(1.0, bpm) / 110.0))


def settle_tempo(envelope: np.ndarray, steps, rate: float = BEAT_RATE):
    """Check the spacing found against its 3:2 and 2:3 neighbours, which a
    beat follower can mistake for the real one; the better spacing in
    steps, or None to keep the one it has."""
    x, fine = fine_envelope(envelope)
    current = 60.0 * rate / float(np.median(np.diff(steps)))
    here = tempo_fit(x, fine, current, 0.015, 0.0015, rate)
    chosen = here
    for ratio in (1.5, 2.0 / 3.0):
        other = current * ratio
        if not 58.0 <= other <= 200.0:
            continue
        fit = tempo_fit(x, fine, other, 0.015, 0.0015, rate)
        clearly = fit[0] >= 1.1 * chosen[0] and fit[3] >= 1.1 * chosen[3]
        tied = (fit[0] >= 0.95 * chosen[0] and fit[3] >= 0.95 * chosen[3]
                and prior_distance(fit[1]) < prior_distance(chosen[1]))
        if clearly or tied:
            chosen = fit
    return None if chosen is here else rate * 60.0 / chosen[1]


def fit_beat_grid(envelope: np.ndarray, steps, downbeat: int, typed: float = 0, rate: float = BEAT_RATE):
    """Tempo to 0.02 BPM, and the beats as an exact grid when the song holds
    its tempo. Returns (steps, downbeat, bpm, steady)."""
    steps = np.asarray(steps, dtype=float)
    x, fine = fine_envelope(envelope)
    rough = 60.0 * rate / float(np.median(np.diff(steps)))
    if typed:
        _strength, bpm, where, _share = tempo_fit(x, fine, float(typed), 0.002, 0.0002, rate)
        bpm = float(typed)
    else:
        _strength, bpm, where, _share = tempo_fit(x, fine, rough, 0.025, 0.0004, rate)
    period = rate * 60.0 / bpm
    k = np.round((steps - where) / period)
    misses = np.abs(steps - (where + k * period))
    if not (np.percentile(misses, 90) <= 3.0 and np.median(misses) <= 1.6):
        # Live playing: keep every beat where it was heard; the tempo is the
        # average of the whole song.
        slope = np.polyfit(np.arange(steps.size), steps, 1)[0]
        return steps.astype(int), downbeat, 60.0 * rate / slope, False
    first, last = int(k[0]), int(k[-1])
    beats = where + np.arange(first, last + 1) * period
    mark = int(round((steps[min(downbeat, len(steps) - 1)] - where) / period)) - first
    return beats, mark % 4, bpm, True


def beat_energy(samples: np.ndarray, times) -> list[float]:
    """How loud the song is around each beat, from 0 (quietest) to 1."""
    squares = np.concatenate([[0.0], np.cumsum(samples.astype(np.float64) ** 2)])
    half = int(1.5 * SAMPLE_RATE)
    levels = []
    for moment in times:
        middle = int(moment * SAMPLE_RATE)
        first, last = max(0, middle - half), min(samples.size, middle + half)
        levels.append((squares[last] - squares[first]) / max(1, last - first))
    levels = np.sqrt(np.array(levels))
    width = min(len(levels), 8)
    levels = np.convolve(levels, np.ones(width) / width, mode="same")
    ranks = np.argsort(np.argsort(levels))
    return [round(float(rank) / max(1, len(levels) - 1), 3) for rank in ranks]


class NoBeat(ValueError):
    pass


def detect(samples: np.ndarray, typed: float = 0) -> dict:
    """The beats of 16 kHz mono samples: {"times" (s), "period" (s), "bpm",
    "downbeat" (index of the first bar start), "bar_confidence", "steady",
    "energy", "duration", "other_tempos"}. ``typed`` forces the tempo."""
    samples = np.asarray(samples, dtype=np.float32)[: int(SONG_MAX_SECONDS * SAMPLE_RATE)]
    if samples.size < SAMPLE_RATE * 3:
        raise NoBeat("The song is under 3 seconds long.")
    bands = band_spectrogram(samples)
    envelope = flux_of(bands)
    tried = []
    if typed:
        steps = track_beats(envelope, BEAT_RATE * 60.0 / float(typed))
    else:
        steps, tried = choose_beats(envelope, bands, estimate_period(envelope))
        if len(steps) >= 8:
            better = settle_tempo(envelope, steps)
            if better:
                steps = track_beats(envelope, better)
    if len(steps) < 8:
        raise NoBeat("Could not find a steady beat in this song.")
    steps, downbeat, notes = refine(bands, steps)
    steps, downbeat, bpm, steady = fit_beat_grid(envelope, steps, downbeat, typed)
    times = [round(float(s) / BEAT_RATE + SHIFT, 4) for s in steps]
    if len(times) < 8:
        raise NoBeat("Could not find a steady beat in this song.")
    others = []
    for tempo, _score in sorted(tried, key=lambda row: -row[-1]):
        if abs(tempo - bpm) > 0.06 * bpm and all(abs(tempo - o) > 0.06 * o for o in others):
            others.append(tempo)
    return {"times": times, "period": 60.0 / bpm, "bpm": round(float(usual_tempo(bpm)), 2),
            "bpm_exact": round(float(bpm), 3), "downbeat": int(downbeat),
            "bar_confidence": float(notes.get("bar_confidence", 0.0)), "steady": bool(steady),
            "energy": beat_energy(samples, times), "typed": float(typed or 0),
            "other_tempos": [round(t) for t in others[:3]], "duration": samples.size / float(SAMPLE_RATE)}
