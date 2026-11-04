"""Cutting clips to a song's beats, ported from ZCuts (engine/07_music.py
beats_per_cut, timeline_frames, whole_source_frames, largest_gap and
build_montage's place_broll; build 259).

Every clip is B-roll here: ZCuts told talking from B-roll with its vision
model and the words, and Tools has neither. Clips are used in timeline
order, each piece lasting about the same number of beats (Auto aims for
about 2.5 s) and ending on a beat, preferring the bar grid. With "Fill the
whole song", when the clips run out before the song, it goes round again
taking unused parts of each clip, then the same shots again. Without the
vision scores, each piece is the middle of the part of the clip left.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field


@dataclass
class Source:
    first_frame: int  # the part of the file the timeline uses, end exclusive
    end_frame: int
    fps: float


@dataclass
class Piece:
    clip: int  # index into the sources
    first: int  # source frames, end exclusive
    last: int
    position: int  # timeline frames from the start of the montage
    length: int  # timeline frames
    on_beat: bool = True


@dataclass
class Plan:
    pieces: list[Piece] = field(default_factory=list)
    per_cut: int = 0
    length: int = 0  # timeline frames the pieces fill
    song_end: int = 0  # timeline frames of song after its start
    off_beat: int = 0
    extra: int = 0  # pieces from a second pass over the clips
    repeats: int = 0  # pieces that play a stretch again
    unused: list[int] = field(default_factory=list)  # clips too short to use


def beats_per_cut(period: float, chosen: int = 0) -> int:
    """Beats each piece lasts; Auto aims for about 2.5 seconds."""
    if chosen:
        return int(chosen)
    return min((1, 2, 4, 8, 16), key=lambda beats: abs(beats * period - 2.5))


def timeline_frames(src: Source, timeline_fps: float, source_frames: int) -> int:
    """Whole timeline frames a run of source frames fills (a 60 fps clip on a
    30 fps timeline can otherwise come out half a frame long, which Resolve
    shows as a one-frame gap)."""
    return int(math.floor(source_frames * timeline_fps / src.fps + 1e-6))


def whole_source_frames(src: Source, timeline_fps: float, source_frames: int) -> int:
    """source_frames trimmed so it fills an exact number of timeline frames."""
    ratio = timeline_fps / src.fps
    if abs(ratio - 1.0) < 1e-9:
        return source_frames
    fitted = int(round(timeline_frames(src, timeline_fps, source_frames) / ratio))
    return max(0, min(source_frames, fitted))


def largest_gap(bounds, windows, margin):
    """The longest unused stretch of a clip, as (first, last) source frames."""
    best = None
    cursor = bounds[0]
    for first, last in sorted(windows) + [(bounds[1] + margin, bounds[1] + margin)]:
        gap = (cursor, first - margin)
        if gap[1] > gap[0] and (best is None or gap[1] - gap[0] > best[1] - best[0]):
            best = gap
        cursor = max(cursor, last + margin)
    return best


def plan(sources: list[Source], beats: dict, timeline_fps: float, per_cut: int = 0,
         song_start: float = 0.0, lead: int = 1, fill_song: bool = True, pace: bool = False) -> Plan:
    """Which part of every clip goes in the music cut, in order.
    ``beats`` is what beats.detect returns; ``song_start`` skips that many
    seconds of the song; ``lead`` cuts that many frames before each beat, so
    the new picture is on screen as the beat hits."""
    period = beats["period"] * timeline_fps
    result = Plan(per_cut=beats_per_cut(beats["period"], per_cut))
    energy = beats.get("energy") or []
    pace = pace and len(energy) == len(beats["times"])
    by_frame: dict[int, float] = {}
    beat_number: dict[int, int] = {}
    for index, moment in enumerate(beats["times"]):
        if moment >= song_start:
            frame = int(round((moment - song_start) * timeline_fps)) - max(0, lead)
            if frame >= 0:
                by_frame[frame] = energy[index] if pace else 0.5
                beat_number[frame] = index
    beat_frames = sorted(by_frame)
    song_end = max(0.0, (beats["duration"] - song_start) * timeline_fps)
    result.song_end = int(song_end)
    shortest = max(0.4 * timeline_fps, 0.5 * period)
    downbeat = int(beats.get("downbeat", 0))
    position = [0.0]
    used: dict[int, list] = {}

    def beats_here() -> int:
        if not pace or not beat_frames:
            return result.per_cut
        index = min(len(beat_frames) - 1, bisect.bisect_left(beat_frames, position[0]))
        level = by_frame[beat_frames[index]]
        if level >= 0.7:
            return max(1, result.per_cut // 2)
        if level <= 0.3:
            return result.per_cut * 2
        return result.per_cut

    def place(clip: int, low: int, high: int, fussy: bool = False) -> bool:
        """Add one piece from this stretch of the clip, ending on a beat."""
        src = sources[clip]
        here = position[0]
        ratio = timeline_fps / src.fps
        available = min(timeline_frames(src, timeline_fps, high - low), song_end - here)
        if available < min(shortest, 0.3 * timeline_fps):
            return False
        beats_now = beats_here()
        wanted = beats_now * period
        if fussy and available < 0.75 * wanted:  # a fill-in piece is a proper length, not a flash
            return False
        target = here + wanted
        floor = max(shortest, 0.7 * wanted) if fussy else shortest
        reachable = [b for b in beat_frames if here + floor <= b <= here + available + 0.5]
        # Prefer the bar grid: every 4 beats lands on the first beat of a
        # bar, every 2 on beats one and three.
        grid = beats_now if beats_now in (1, 2, 4) else 4
        on_grid = [b for b in reachable if (beat_number[b] - downbeat) % grid == 0]
        if on_grid:
            reachable = on_grid
        length = (min(reachable, key=lambda b: abs(b - target)) - here) if reachable else min(available, wanted)
        source_frames = min(max(1, int(round(length / ratio))), high - low)
        source_frames = whole_source_frames(src, timeline_fps, source_frames)
        if source_frames < 1:
            return False
        first = low + max(0, (high - low - source_frames) // 2)  # the middle of what is left
        first = max(low, min(first, high - source_frames))
        length = timeline_frames(src, timeline_fps, source_frames)
        if length < 1:
            return False
        used.setdefault(clip, []).append((first, first + source_frames))
        result.pieces.append(Piece(clip, first, first + source_frames, int(round(here)), length, bool(reachable)))
        result.off_beat += not reachable
        position[0] = here + length
        return True

    placed = []
    for clip, src in enumerate(sources):
        if position[0] >= song_end - shortest:
            break
        if place(clip, src.first_frame, src.end_frame):
            placed.append(clip)
        else:
            result.unused.append(clip)

    if fill_song and placed:
        # Still song left: go round again, taking a different part of each
        # clip, so the same shot never plays twice in a row.
        for _round in range(6):
            added = 0
            for clip in placed:
                if position[0] >= song_end - shortest:
                    break
                src = sources[clip]
                gap = largest_gap((src.first_frame, src.end_frame), used.get(clip, []), int(0.5 * src.fps))
                if gap and place(clip, gap[0], gap[1], fussy=True):
                    added += 1
            result.extra += added
            if not added:
                break
        # Every clip is used up: go round the same shots again rather than
        # leave the picture empty (a clip shorter than a beat would flicker).
        for _round in range(60):
            if position[0] >= song_end - shortest:
                break
            added = 0
            for clip in placed:
                if position[0] >= song_end - shortest:
                    break
                src = sources[clip]
                if (src.end_frame - src.first_frame) / src.fps < beats["period"]:
                    continue
                if place(clip, src.first_frame, src.end_frame):
                    added += 1
            result.repeats += added
            if not added:
                break
    result.length = int(round(position[0]))
    return result
