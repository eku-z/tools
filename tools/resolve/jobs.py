"""The five jobs. Each changes the timeline you are working in; before the
first change Tools puts a copy of it in the "Tools backups" bin, and Undo
puts that copy back (tools/resolve/undo.py).

- silences: ZCuts' Analyze + cut (engine/04_silence.py analyze_timeline,
  engine/11_apply.py kept_pieces), done in place like a ripple delete.
- ab: ZCuts' A/B camera cut (engine/10_ab_camera.py interview_inputs,
  plan_interview, apply_interview): the upper camera shows on every
  second stretch, lined up with the lower one, which stays as it is.
- montage: ZCuts' music montage (engine/07_music.py ensure_song,
  detect_beats, build_montage; engine/10_ab_camera.py place_song), or cut
  to a set tempo without a song.
- vertical: ZCuts' 9:16 framing (engine/10_ab_camera.py vertical_of_open).
- look: LUT, sideways clips upright, fill the frame.

``run`` does one job or several (Apply) on the open timeline, one backup
and one Undo step for all of them.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

from tools.core import Stopped, abcam, beats, camera, media, montage, speech, sync
from tools.core.silence import PRESETS, SilenceSettings, detect_silence, keep_ranges, merge_intervals
from tools.core.work import Context, Heavy, Job
from tools.resolve import look
from tools.resolve import timeline as tl
from tools.resolve.link import project_identity
from tools.resolve.undo import UndoRecord

MUSIC_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff", ".wma", ".opus")
ORDER = ["silences", "ab", "montage", "vertical", "look"]  # the order Apply runs them in
TITLES = {"silences": "Cut silences", "ab": "A/B camera", "montage": "Cut to the beat", "vertical": "Make it 9:16",
          "look": "Look"}


def _minutes(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "{:.1f} s".format(seconds)
    return "{}:{:02d}".format(int(seconds // 60), int(round(seconds % 60)))


class Session:
    """The open timeline, changed in place: backed up before its first
    change, and handed to Undo when the jobs end, finished or not."""

    def __init__(self, ctx: Context, label: str):
        self.ctx = ctx
        self.project = ctx.project()
        self.media_pool = self.project.GetMediaPool()
        if self.media_pool is None:
            raise RuntimeError("Could not reach the project's Media Pool.")
        self.timeline = self.project.GetCurrentTimeline()
        if self.timeline is None:
            raise RuntimeError("Open a timeline in Resolve first.")
        self.name = tl.name_of(self.timeline)
        self.record = UndoRecord(label, self.timeline, self.name, project_identity(self.project),
                                 ctx.link.generation)

    def before_change(self) -> None:
        """Copy the timeline into the Tools backups bin, once."""
        if self.record.backup is not None:
            return
        try:
            self.ctx.link.resolve.OpenPage("edit")  # adding pieces works from the Edit page
        except Exception:  # noqa: BLE001
            pass
        copy = None
        try:
            copy = self.timeline.DuplicateTimeline(tl.unique_name(self.project, self.name + " (before Tools)"))
        except Exception:  # noqa: BLE001
            copy = None
        if not copy:
            raise RuntimeError("Resolve would not make a backup copy of '{}', so nothing was changed."
                               .format(self.name))
        try:
            self.record.folder = tl.find_folder_of(self.media_pool, self.timeline.GetMediaPoolItem())
        except Exception:  # noqa: BLE001
            self.record.folder = None
        self.record.backup = copy
        tl.move_to(self.media_pool, copy, tl.backup_folder(self.media_pool))
        self.project.SetCurrentTimeline(self.timeline)

    def close(self, error: Optional[BaseException] = None) -> None:
        if self.record.backup is not None:
            self.record.after = tl.fingerprint(self.timeline)
            self.ctx.history.push(self.record)
            if error is not None:
                self.ctx.log("'{}' may be changed part-way. Press Undo to put it back as it was.".format(self.name))


# ---- Cut silences ------------------------------------------------------------------------------

def silence_settings(options: dict) -> SilenceSettings:
    preset = options.get("preset", "Natural")
    if preset in PRESETS:
        return PRESETS[preset]
    return SilenceSettings(float(options.get("threshold_db", -35)), float(options.get("minimum_ms", 400)) / 1000.0,
                           float(options.get("padding_ms", 120)) / 1000.0)


def ripple(old: tl.Placed, cuts: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
    """A clip's pieces once ``cuts`` (timeline frames, merged, in order) are
    taken out of the timeline and everything after closes up."""
    def piece(x0: int, x1: int, removed: int):
        return (old.source_start + int(round((x0 - old.start) * old.rate)),
                old.source_start + int(round((x1 - old.start) * old.rate)), x0 - removed)

    out = []
    removed = 0  # frames cut out before ``cursor``
    cursor = old.start
    for a, b in cuts:
        if b <= old.start:
            removed += b - a
            continue
        if a >= old.end:
            break
        if a < old.start:
            removed += old.start - a
        inside_a, inside_b = max(a, old.start), min(b, old.end)
        if inside_a > cursor:
            out.append(piece(cursor, inside_a, removed))
        removed += inside_b - inside_a
        cursor = inside_b
    if cursor < old.end:
        out.append(piece(cursor, old.end, removed))
    return [(f, l, r) for f, l, r in out if l > f]


def silences(s: Session, options: dict) -> Job:
    ctx, log, timeline = s.ctx, s.ctx.log, s.timeline
    track = tl.choose_track(timeline, options.get("track", "Auto"))
    clips, _dropped = tl.track_clips(timeline, track)
    if not clips:
        raise RuntimeError("There are no clips on V{}.".format(track))
    st = silence_settings(options)
    spans: dict[str, tuple[float, float]] = {}
    for c in clips:
        if c.whole_reason is None:
            a, b = spans.get(c.path, (c.lo, c.hi))
            spans[c.path] = (min(a, c.lo), max(b, c.hi))
    reads = [(path, path, max(0.0, a - 0.5), b - a + 1.0, 0) for path, (a, b) in spans.items()]
    ctx.progress(0.02, "Listening to {} file(s)".format(len(reads)))
    heard = yield Heavy(lambda: media.read_many(
        reads, ctx.stop, lambda n, total: ctx.progress(0.02 + 0.6 * n / total, "Listened to {} of {}".format(n, total))))
    cuts = []
    for c in clips:
        levels = heard.get(c.path)
        if c.whole_reason is not None:
            log("Left as it is ({}): {}".format(c.whole_reason, c.name))
            continue
        if not isinstance(levels, media.Levels):
            log("Left as it is (no sound to listen to): {}".format(c.name))
            continue
        kept = keep_ranges(c.lo, c.hi, detect_silence(levels, st))
        edges = [c.lo] + [x for ab in kept for x in ab] + [c.hi]
        for a, b in zip(edges[0::2], edges[1::2]):  # the gaps between what is kept
            first = c.timeline_start + int(round((a - c.lo) * c.timeline_fps))
            last = c.timeline_start + int(round((b - c.lo) * c.timeline_fps))
            if last > first:
                cuts.append((first, min(last, c.timeline_end)))
    cuts = [(int(a), int(b)) for a, b in merge_intervals(cuts) if b > a]
    if not cuts:
        return "No silence found on V{} at this setting: nothing changed.".format(track)
    ctx.check()
    s.before_change()
    if options.get("all_tracks"):
        targets = [p for kind, t, i, item in tl.every_clip(timeline) for p in [tl.placed(timeline, kind, t, i, item)]]
        left = sum(1 for p in targets if p is None)
        olds = [p for p in targets if p is not None]
        if left:
            log("{} title(s) or generator(s) stay where they are: a script cannot put them back.".format(left))
    else:
        olds = []
        row = sorted(timeline.GetItemListInTrack("video", track) or [], key=lambda i: int(i.GetStart()))
        for index, item in enumerate(row):
            p = tl.placed(timeline, "video", track, index, item)
            if p is None:
                continue
            olds.append(p)
            olds += [tl.placed(timeline, "audio", t, i, a) for t, i, a in tl.linked_audio(timeline, item)]
        others = [1 for kind, t, _i, item in tl.every_clip(timeline)
                  if not (kind == "video" and t == track) and int(item.GetEnd()) > cuts[0][0]]
        if len(others) > len([o for o in olds if o.kind == "audio"]):
            log("Only V{} and its sound were cut; clips on other tracks stay where they were. Switch on "
                "\"Cut every track\" to keep them in step.".format(track))
    ctx.progress(0.75, "Cutting {} silence(s)".format(len(cuts)))
    counts = tl.put_back(s.project, s.media_pool, timeline, olds, {id(p): ripple(p, cuts) for p in olds},
                         s.record.backup)
    _report(log, counts)
    saved = sum(b - a for a, b in cuts) / tl.timeline_fps(timeline)
    return "Cut {} silence(s) from '{}': {} shorter.".format(len(cuts), s.name, _minutes(saved))


def _report(log, counts: dict) -> None:
    if counts["refused"]:
        log("{} piece(s) Resolve would not put back.".format(counts["refused"]))
    if counts["no_grade"]:
        log("Resolve would not copy the grade onto {} piece(s): grade after cutting, or copy it from the "
            "backup in the Tools backups bin.".format(counts["no_grade"]))


# ---- A/B camera ---------------------------------------------------------------------------------

def _camera_files(timeline, tl_fps: float) -> list[dict]:
    """The two camera files with the most footage on the video tracks, as
    {clip (the longest piece of that file), track, index, n}, lower first."""
    found: dict[str, dict] = {}
    skipped = []
    for kind, track, index, item in tl.every_clip(timeline, ("video",)):
        c = tl.read_clip(item, tl_fps)
        if c is None or c.whole_reason is not None:
            skipped.append("{} ({})".format(tl.name_of(item, "a clip"), c.whole_reason if c else "no file"))
            continue
        entry = found.setdefault(c.path, {"clip": c, "track": track, "index": index, "total": 0, "n": 0})
        length = c.timeline_end - c.timeline_start
        entry["total"] += length
        entry["n"] += 1
        if length > entry["clip"].timeline_end - entry["clip"].timeline_start:
            entry.update(clip=c, track=track, index=index)
    ranked = sorted(found.values(), key=lambda e: -e["total"])[:2]
    if len(ranked) < 2:
        why = (" Could not use: " + "; ".join(sorted(set(skipped))[:3])) if skipped else ""
        raise RuntimeError("The A/B cut needs two different camera files on two video tracks, and found {}.{}"
                           .format(len(ranked), why))
    ranked.sort(key=lambda e: e["track"])
    if ranked[0]["track"] == ranked[1]["track"]:
        raise RuntimeError("Put the two cameras on different video tracks (one above the other).")
    return ranked


def ab(s: Session, options: dict) -> Job:
    ctx, log, timeline = s.ctx, s.ctx.log, s.timeline
    tl_fps = tl.timeline_fps(timeline)
    low, high = _camera_files(timeline, tl_fps)
    a, b = low["clip"], high["clip"]
    log("Camera A (stays): V{} {} | camera B (switched in): V{} {}".format(low["track"], a.name, high["track"],
                                                                           b.name))
    ctx.progress(0.05, "Listening to the cameras")
    heard = yield Heavy(lambda: media.read_many([("a", a.path, 0.0, None, 0), ("b", b.path, 0.0, None, 0)], ctx.stop))
    levels = {k: (v if isinstance(v, media.Levels) else None) for k, v in heard.items()}
    if speech.own_sound_score(levels["a"], a.lo, a.hi) <= -90.0:  # maybe on another sound stream
        for stream in range(1, min(4, media.probe(a.path).audio_streams)):
            other = yield Heavy(lambda st=stream: media.read_levels(a.path, 0.0, None, st, ctx.stop))
            if speech.own_sound_score(other, a.lo, a.hi) > -90.0:
                levels["a"] = other
                break
    hint = (a.lo - a.timeline_start / tl_fps) - (b.lo - b.timeline_start / tl_fps)
    offset = hint
    if options.get("sync"):
        if levels["a"] is None or levels["b"] is None:
            log("A camera has no sound, so B is lined up where you placed it.")
        else:
            ctx.progress(0.6, "Lining up camera B by sound")
            notes: dict = {}
            found = yield Heavy(lambda: sync.offset(levels["a"], a.lo, a.hi, levels["b"], b.lo, b.hi, hint, notes))
            if found is None:
                log("B's sound did not match A's (best {:.0f}%, needs {:.0f}%), so B is lined up where you "
                    "placed it.".format(100 * notes.get("strength", 0.0), 100 * sync.SYNC_MIN_STRENGTH))
            else:
                offset = found[0]
                log("Camera B lined up by sound: {} (match {:.0f}%).".format(
                    "already in step" if abs(offset - hint) < 0.02 else
                    "moved {:.3f} s from where you placed it".format(offset - hint), 100 * min(1.0, found[1])))
    lo = max(a.lo, b.lo + offset) + abcam.EDGE
    hi = min(a.hi, b.hi + offset) - abcam.EDGE
    if hi - lo < 10.0:
        raise RuntimeError("The two cameras overlap for only {:.0f} s, too short for an A/B cut.".format(max(0, hi - lo)))
    every = max(2.0, float(options.get("every", 8.0)))
    snap = bool(options.get("pauses"))
    gaps = speech.pauses(levels["a"], lo, hi) if (snap and levels["a"] is not None) else []
    cuts, snapped = abcam.boundaries(lo, hi, every, gaps, snap)
    ctx.check()

    def at(t: float) -> int:  # camera A's file time -> timeline frame
        return a.timeline_start + int(round((t - a.lo) * tl_fps))

    s.before_change()
    item = sorted(timeline.GetItemListInTrack("video", high["track"]) or [],
                  key=lambda i: int(i.GetStart()))[high["index"]]
    old = tl.placed(timeline, "video", high["track"], high["index"], item)
    sound = [tl.placed(timeline, "audio", t, i, x) for t, i, x in tl.linked_audio(timeline, item)]
    pieces = []
    for k in range(1, len(cuts) - 1, 2):  # B shows on stretches 2, 4, 6...
        record, end = at(cuts[k]), at(cuts[k + 1])
        first = max(0, int(round((cuts[k] - offset) * b.source_fps)))
        pieces.append((first, first + int(round((end - record) * b.source_fps / tl_fps)), record))
    counts = tl.put_back(s.project, s.media_pool, timeline, [old] + sound, {id(old): pieces}, s.record.backup)
    _report(log, counts)
    if sound:
        log("Camera B's own sound was taken off; camera A's sound plays throughout.")
    return ("A/B cut on '{}': camera B shows on {} stretch(es), switching about every {:g} s{}.".format(
        s.name, counts["added"], every,
        "; {} switch(es) moved to a pause".format(snapped) if snap and gaps else ""))


# ---- Cut to the beat ---------------------------------------------------------------------------------

def _song_on_timeline(timeline, footage: set[str]):
    """The longest music clip on the audio tracks: (path, timeline item)."""
    best = None
    for item in tl.items(timeline, "audio"):
        path = tl.file_of(item)
        if (not path or not os.path.isfile(path) or os.path.normcase(path) in footage
                or not path.lower().endswith(MUSIC_EXTENSIONS)):
            continue
        length = int(item.GetEnd()) - int(item.GetStart())
        if best is None or length > best[0]:
            best = (length, path, item)
    return (best[1], best[2]) if best else (None, None)


def _beats_cache(data_dir: str, path: str, typed: float) -> str:
    st = os.stat(path)
    key = "{}|{:.6f}|{}|{}|tools-beats-1".format(os.path.abspath(path), st.st_mtime, st.st_size, typed)
    return os.path.join(data_dir, "beats", hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:24] + ".json")


def song_beats(path: str, typed: float, data_dir: str, stop=None) -> dict:
    """The song's beats, saved for next time (helper thread)."""
    cache = _beats_cache(data_dir, path, typed) if data_dir else ""
    if cache and os.path.isfile(cache):
        try:
            with open(cache, "r", encoding="utf-8") as fh:
                found = json.load(fh)
            found["reused"] = True
            return found
        except (OSError, ValueError):
            pass
    duration = media.probe(path).duration
    length = min(duration, beats.SONG_MAX_SECONDS) if duration > 0 else beats.SONG_MAX_SECONDS
    samples = media.read_samples(path, 0.0, length, 0, stop)
    if samples is None:
        raise RuntimeError("The song has no sound: {}".format(path))
    found = beats.detect(samples, typed)
    if cache:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(found, fh)
            os.replace(cache + ".tmp", cache)
        except OSError:
            pass
    return found


def _free_audio_track(timeline, start: int, end: int) -> int:
    for track in range(1, int(timeline.GetTrackCount("audio") or 0) + 1):
        if not any(int(i.GetStart()) < end and int(i.GetEnd()) > start
                   for i in timeline.GetItemListInTrack("audio", track) or []):
            return track
    timeline.AddTrack("audio", "stereo") or timeline.AddTrack("audio")
    return int(timeline.GetTrackCount("audio") or 1)


def montage_job(s: Session, options: dict) -> Job:
    ctx, log, timeline = s.ctx, s.ctx.log, s.timeline
    tl_fps = tl.timeline_fps(timeline)
    track = tl.choose_track(timeline, options.get("track", "Auto"))
    clips, _dropped = tl.track_clips(timeline, track)
    usable = [c for c in clips if c.whole_reason is None]
    if not usable:
        raise RuntimeError("There are no usable video clips on V{}.".format(track))
    typed = float(options.get("bpm", 0) or 0)
    sources = [montage.Source(c.source_start, c.source_end, c.source_fps) for c in usable]
    origin = usable[0].timeline_start
    path, song_item = None, None
    if options.get("no_song"):
        if typed <= 0:
            raise RuntimeError("To cut without a song, type the tempo (BPM) to cut to.")
        found = beats.steady(typed, sum((x.end_frame - x.first_frame) / x.fps for x in sources))
        song_start, lead, fill_song = 0.0, 0, False
        log("No song: cutting to a steady {:g} BPM.".format(typed))
    else:
        path = (options.get("song_path") or "").strip().strip('"')
        if path and not os.path.isfile(path):
            raise RuntimeError("The song file was not found: {}".format(path))
        if not path:
            path, song_item = _song_on_timeline(timeline, {os.path.normcase(c.path) for c in clips})
        if not path:
            raise RuntimeError("Cutting to the beat needs a song: pick one, put it on an audio track, or "
                               "switch on No song and type a tempo.")
        ctx.progress(0.1, "Listening for the beat")
        found = dict((yield Heavy(lambda: song_beats(path, typed, ctx.data_dir, ctx.stop))))
        log("Song: {} | {:.2f} BPM{}.".format(os.path.basename(path), found["bpm"],
                                               " (remembered from last time)" if found.get("reused") else ""))
        lead, fill_song = int(options.get("lead", 0)), bool(options.get("fill_song"))
        if song_item is not None:
            # The song stays where it is; the cuts follow it from where it starts.
            pool = song_item.GetMediaPoolItem()
            song_fps = tl.parse_fps(tl.prop(pool, "FPS"), tl_fps)
            song_start = tl.source_start_of(song_item) / song_fps
            origin = int(song_item.GetStart())
            found["duration"] = min(found["duration"],
                                    song_start + (int(song_item.GetEnd()) - origin) / tl_fps)
        else:
            song_start = max(0.0, float(options.get("song_start", 0) or 0))
    plan = montage.plan(sources, found, tl_fps, int(options.get("per_cut", 0) or 0), song_start, lead,
                        fill_song, bool(options.get("pace")) and not options.get("no_song"))
    if not plan.pieces:
        raise RuntimeError("No clip is long enough for a piece.")
    ctx.check()
    s.before_change()
    mute = bool(options.get("mute"))
    row = sorted(timeline.GetItemListInTrack("video", track) or [], key=lambda i: int(i.GetStart()))
    olds, video_of, sound_of = [], {}, {}
    for index, item in enumerate(row):
        p = tl.placed(timeline, "video", track, index, item)
        if p is None:
            continue
        olds.append(p)
        video_of[(p.start, p.path)] = p
        sound = [tl.placed(timeline, "audio", t, i, x) for t, i, x in tl.linked_audio(timeline, item)]
        olds += sound
        sound_of[(p.start, p.path)] = sound
    pieces: dict[int, list] = {}
    for piece in plan.pieces:
        c = usable[piece.clip]
        key = (c.timeline_start, c.path)
        for owner in [video_of.get(key)] + ([] if mute else sound_of.get(key, [])):
            if owner is not None:
                pieces.setdefault(id(owner), []).append((piece.first, piece.last, origin + piece.position))
    ctx.progress(0.6, "Cutting {} pieces to the beat".format(len(plan.pieces)))
    counts = tl.put_back(s.project, s.media_pool, timeline, olds, pieces, s.record.backup)
    _report(log, counts)
    if path and song_item is None:
        pool = tl.find_pool_item(s.media_pool, path)
        offset = int(round(song_start * tl_fps))
        end = int(round(found["duration"] * tl_fps)) - offset
        song_track = _free_audio_track(timeline, origin, origin + end)
        if pool is None or end <= 0 or not s.media_pool.AppendToTimeline([{
                "mediaPoolItem": pool, "startFrame": offset, "endFrame": offset + end, "mediaType": 2,
                "trackIndex": song_track, "recordFrame": origin}]):
            log("Resolve would not place the song. Drag it onto an empty audio track at the start of the cut.")
        else:
            log("Song placed on A{}.".format(song_track))
    return "Cut {} piece(s) to the beat on V{} of '{}', {} beat(s) a cut{}.".format(
        len(plan.pieces), track, s.name, plan.per_cut, " at {:g} BPM".format(typed) if options.get("no_song") else "")


# ---- 9:16 and Look -------------------------------------------------------------------------------------

def vertical(s: Session, options: dict) -> Job:
    if tl.is_vertical(s.timeline):
        return "'{}' is already vertical: nothing changed.".format(s.name)
    s.before_change()
    if not tl.make_vertical(s.timeline):
        raise RuntimeError("Resolve would not make '{}' 1080x1920 from a script.".format(s.name))
    counts = look.frame_pieces(s.project, s.timeline, look.video_pieces(s.timeline),
                               upright=bool(options.get("upright")), fill=True, reset=True)
    if counts["refused"]:
        s.ctx.log("Resolve would not take the framing on {} clip(s).".format(counts["refused"]))
    return "'{}' is 9:16 now (1080x1920): {} clip(s) zoomed to fill it{}.".format(
        s.name, counts["filled"] + counts["turned"],
        ", {} turned upright".format(counts["turned"]) if counts["turned"] else "")
    yield  # a generator, like every job


def look_job(s: Session, options: dict) -> Job:
    pieces = look.video_pieces(s.timeline)
    lut = options.get("lut_path") or ""
    sideways = sum(1 for p in pieces if camera.file_turn(tl.file_of(p))) if options.get("upright") else 0
    if not lut and not sideways and not options.get("fill"):
        return "Look: nothing picked (no LUT, nothing to turn upright, Fill the frame off): nothing changed."
    s.before_change()
    look.finish(s.project, s.timeline, options, s.ctx.log)
    if options.get("fill"):
        counts = look.fill_frame(s.project, s.timeline, pieces)
        s.ctx.log("Fill the frame: {} clip(s) zoomed so no black bars show, {} already filled it.".format(
            counts["filled"], counts["already"]))
    return "Look done on '{}'.".format(s.name)
    yield  # a generator, like every job


BODIES = {"silences": silences, "ab": ab, "montage": montage_job, "vertical": vertical, "look": look_job}


def run(ctx: Context, chosen: list[tuple[str, dict]]) -> Job:
    """One job, or several one after another (Apply), on the open timeline:
    one backup and one Undo step. In Apply, a job that cannot be done is
    said and the next goes on; Stop ends them all."""
    label = TITLES[chosen[0][0]] if len(chosen) == 1 else "Apply"
    session = Session(ctx, label)
    lines = []
    try:
        for name, options in chosen:
            ctx.check()
            session.project.SetCurrentTimeline(session.timeline)
            try:
                summary = yield from BODIES[name](session, options)
            except Stopped:
                raise
            except Exception as error:  # noqa: BLE001
                if len(chosen) == 1:
                    raise
                summary = "{}: not done. {}".format(TITLES[name], error)
            lines.append(summary)
            if len(chosen) > 1:
                ctx.log(summary)
    except BaseException as error:
        session.close(error)
        raise
    session.close()
    if len(chosen) == 1:
        return lines[0]
    return "Applied {} job(s) to '{}'.".format(len(chosen), session.name)
