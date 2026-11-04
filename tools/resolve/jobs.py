"""The five jobs. Each reads the timeline open in Resolve and builds a new
one; your timeline is never changed, and Undo deletes what a job made.

- cut_silences: ZCuts' Analyze + Create timeline (engine/04_silence.py
  analyze_timeline, engine/11_apply.py kept_pieces and apply_quickcut).
- ab_camera: ZCuts' A/B camera cut (engine/10_ab_camera.py
  interview_inputs, plan_interview, apply_interview).
- montage: ZCuts' music montage (engine/07_music.py ensure_song,
  detect_beats, build_montage; engine/10_ab_camera.py place_song).
- vertical: ZCuts' 9:16 copy (engine/10_ab_camera.py vertical_of_open).
- look: ZCuts' Apply look on a copy (LUT on every clip, sideways clips
  upright).

Every new timeline from the first three also gets the finishing touches
(look.finish) and, when asked, a 9:16 copy of itself.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

from tools.core import Stopped, abcam, beats, camera, media, montage, speech, sync
from tools.core.silence import PRESETS, SilenceSettings, detect_silence, keep_ranges
from tools.core.work import Context, Heavy, Job
from tools.resolve import look
from tools.resolve import timeline as tl
from tools.resolve.link import project_identity
from tools.resolve.undo import UndoRecord

MUSIC_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff", ".wma", ".opus")


def _minutes(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "{:.1f} s".format(seconds)
    return "{}:{:02d}".format(int(seconds // 60), int(round(seconds % 60)))


class Build:
    """One job's new timeline(s): made, recorded for Undo from the moment
    each exists (so a job that stops part-way can still be taken back),
    and handed to the history when the job ends, finished or not."""

    def __init__(self, ctx: Context, label: str):
        self.ctx = ctx
        self.project = ctx.project()
        self.media_pool = self.project.GetMediaPool()
        if self.media_pool is None:
            raise RuntimeError("Could not reach the project's Media Pool.")
        self.original = self.project.GetCurrentTimeline()
        if self.original is None:
            raise RuntimeError("Open a timeline in Resolve first.")
        self.name = tl.name_of(self.original)
        self.record = UndoRecord(label, self.original, project_identity(self.project), ctx.link.generation)
        self.finished = False

    def new_timeline(self, suffix: str):
        """An empty timeline named after the original, open, with the
        original's frame rate and size. Returns (timeline, its fps)."""
        try:
            self.ctx.link.resolve.OpenPage("edit")  # adding pieces works from the Edit page
        except Exception:  # noqa: BLE001
            pass
        name = tl.unique_name(self.project, self.name + suffix)
        new = self.media_pool.CreateEmptyTimeline(name)
        if new is None:
            raise RuntimeError("Resolve could not make the new timeline '{}'.".format(name))
        self.record.made_timeline(new)
        if not self.project.SetCurrentTimeline(new):
            raise RuntimeError("Resolve could not open the new timeline.")
        self.record.label = "the timeline '{}'".format(name)
        return new, tl.match_settings(self.original, new)

    def duplicate(self, timeline, suffix: str):
        name = tl.unique_name(self.project, tl.name_of(timeline) + suffix)
        try:
            copy = timeline.DuplicateTimeline(name)
        except Exception:  # noqa: BLE001
            copy = None
        if not copy:
            raise RuntimeError("Resolve would not copy the timeline from a script.")
        self.record.made_timeline(copy)
        self.project.SetCurrentTimeline(copy)
        return copy

    def close(self, error: Optional[BaseException] = None) -> None:
        self.ctx.history.push(self.record)
        if error is None or not self.record.made:
            return
        self.ctx.log("An unfinished timeline was left in your Media Pool ({}). Press Undo to "
                     "delete it.".format(", ".join("'{}'".format(tl.name_of(t)) for t in self.record.made)))
        if not isinstance(error, Stopped):
            try:
                self.project.SetCurrentTimeline(self.original)
            except Exception:  # noqa: BLE001
                pass


def _finish(build: Build, timeline, options: dict) -> None:
    look.finish(build.project, timeline, options, build.ctx.log)
    if options.get("also_vertical"):
        if tl.is_vertical(timeline):
            build.ctx.log("The timeline is already vertical, so no 9:16 copy was made.")
        else:
            try:  # the main timeline is finished: a copy that fails does not undo it
                _frame_vertical(build, build.duplicate(timeline, " 9x16"), options)
            except RuntimeError as error:
                build.ctx.log("No 9:16 copy: {}".format(error))
            build.project.SetCurrentTimeline(timeline)


def _frame_vertical(build: Build, copy, options: dict) -> None:
    if not tl.make_vertical(copy):
        raise RuntimeError("Resolve would not make the copy 1080x1920 from a script.")
    counts = look.frame_pieces(build.project, copy, look.video_pieces(copy),
                               upright=options.get("upright", True), fill=True, reset=True)
    build.ctx.log("9:16 copy made: '{}' (1080x1920), {} clip(s) zoomed to fill it around their "
                  "centre{}.".format(tl.name_of(copy), counts["filled"] + counts["turned"],
                                     ", {} turned upright".format(counts["turned"]) if counts["turned"] else ""))
    if counts["refused"]:
        build.ctx.log("Resolve would not take the framing on {} clip(s).".format(counts["refused"]))


def _run(ctx: Context, label: str, body) -> Job:
    """Run a job body with its Build, so Undo always gets what was made."""
    build = Build(ctx, label)
    try:
        summary = yield from body(build)
    except BaseException as error:
        build.close(error)
        raise
    build.close()
    return summary


# ---- Cut silences ------------------------------------------------------------------------------

def silence_settings(options: dict) -> SilenceSettings:
    preset = options.get("preset", "Natural")
    if preset in PRESETS:
        return PRESETS[preset]
    return SilenceSettings(float(options.get("threshold_db", -35)), float(options.get("minimum_ms", 400)) / 1000.0,
                           float(options.get("padding_ms", 120)) / 1000.0)


def cut_silences(ctx: Context, options: dict) -> Job:
    def body(build: Build):
        log = ctx.log
        original = build.original
        track = tl.choose_track(original, options.get("track", "Auto"))
        clips, dropped = tl.track_clips(original, track)
        if not clips:
            raise RuntimeError("There are no clips on V{} of '{}'.".format(track, build.name))
        log("Timeline: {} | V{} | {} clip(s)".format(build.name, track, len(clips)))
        if dropped:
            log("Left out ({} item(s) a script cannot copy: titles, generators): {}".format(
                len(dropped), ", ".join(dropped[:6])))
        st = silence_settings(options)
        log("Silence: {:g} dBFS or quieter for {:.0f} ms, {:.0f} ms padding.".format(
            st.threshold_db, st.minimum * 1000, st.padding * 1000))
        spans: dict[str, tuple[float, float]] = {}
        for c in clips:
            if c.whole_reason is None:
                a, b = spans.get(c.path, (c.lo, c.hi))
                spans[c.path] = (min(a, c.lo), max(b, c.hi))
        reads = [(path, path, max(0.0, a - 0.5), b - a + 1.0, 0) for path, (a, b) in spans.items()]
        ctx.progress(0.02, "Reading the sound of {} file(s)".format(len(reads)))
        heard = yield Heavy(lambda: media.read_many(
            reads, ctx.stop, lambda n, total: ctx.progress(0.02 + 0.7 * n / total,
                                                           "Read the sound of {} of {} file(s)".format(n, total))))
        cuts_of: dict[str, list] = {}
        for path, levels in heard.items():
            if isinstance(levels, media.MediaError):
                log("Kept whole (its sound could not be read): {}. {}".format(os.path.basename(path), levels))
            elif levels is None:
                log("Kept whole (no sound): {}".format(os.path.basename(path)))
            else:
                cuts_of[path] = detect_silence(levels, st)
        infos, owners = [], []
        before = after = 0.0
        cut_count = 0
        for c in clips:
            before += (c.timeline_end - c.timeline_start) / c.timeline_fps
            if c.whole_reason is not None or c.path not in cuts_of:
                if c.whole_reason:
                    log("Kept whole ({}): {}".format(c.whole_reason, c.name))
                infos.append({"mediaPoolItem": c.pool, "startFrame": c.source_start, "endFrame": c.source_end})
                owners.append(c)
                after += (c.timeline_end - c.timeline_start) / c.timeline_fps
                continue
            kept = keep_ranges(c.lo, c.hi, cuts_of[c.path])
            cut_count += sum(1 for a, b in cuts_of[c.path] if b > c.lo and a < c.hi)
            for a, b in kept:
                first = max(int(round(a * c.source_fps)), c.source_start)
                last = min(int(round(b * c.source_fps)), c.source_end)
                if last > first:
                    infos.append({"mediaPoolItem": c.pool, "startFrame": first, "endFrame": last})
                    owners.append(c)
                    after += (last - first) / c.source_fps
        if not infos:
            raise RuntimeError("Everything on V{} is silent at this threshold: nothing would be kept. "
                               "Lower the threshold and try again.".format(track))
        ctx.check()
        new, _fps = build.new_timeline(" - Cut")
        ctx.progress(0.75, "Adding {} pieces to '{}'".format(len(infos), tl.name_of(new)))
        added, refused = tl.append(build.media_pool, infos)
        if not added:
            raise RuntimeError("Resolve did not add any piece to the new timeline.")
        for index in refused:
            info = infos[index]
            log("Left out: a piece of {} (frames {} to {}) that Resolve would not add.".format(
                owners[index].name, info["startFrame"], info["endFrame"]))
        if not tl.items(new, "video", 1):
            raise RuntimeError("Resolve put only the sound of the clips on the new timeline (no picture on "
                               "V1). In the Media Pool, check that Resolve can play the clip's picture.")
        ctx.check()
        ctx.progress(0.9, "Finishing touches")
        _finish(build, new, options)
        saved = before - after
        return ("Made '{}': {} pieces, {} silence(s) cut, {} shorter ({:.0f}%). Your timeline is "
                "unchanged.".format(tl.name_of(new), len(added), cut_count, _minutes(saved),
                                    100.0 * saved / before if before else 0.0))

    return (yield from _run(ctx, "the cut", body))


# ---- A/B camera ---------------------------------------------------------------------------------

def _camera_files(timeline, tl_fps: float) -> list[dict]:
    """The two camera files with the most footage on the video tracks, as
    {clip (the longest piece of that file), track, total}, lower track first."""
    found: dict[str, dict] = {}
    skipped = []
    for track in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
        for item in timeline.GetItemListInTrack("video", track) or []:
            c = tl.read_clip(item, tl_fps)
            if c is None or c.whole_reason is not None:
                skipped.append("{} ({})".format(tl.name_of(item, "a clip"), c.whole_reason if c else "no file"))
                continue
            entry = found.setdefault(c.path, {"clip": c, "track": track, "total": 0, "n": 0})
            length = c.timeline_end - c.timeline_start
            entry["total"] += length
            entry["n"] += 1
            if length > entry["clip"].timeline_end - entry["clip"].timeline_start:
                entry["clip"], entry["track"] = c, track
    ranked = sorted(found.values(), key=lambda e: -e["total"])[:2]
    if len(ranked) < 2:
        why = (" Could not use: " + "; ".join(sorted(set(skipped))[:3])) if skipped else ""
        raise RuntimeError("The A/B cut needs two different camera files on the timeline, on any video "
                           "tracks, and found {}.{}".format(len(ranked), why))
    return sorted(ranked, key=lambda e: e["track"])


def _master_audio(timeline, tl_fps: float, taken: set[str]) -> Optional[dict]:
    """The longest audio clip from a file that is not one of the cameras:
    a separate sound recording, which then is the master sound."""
    best = None
    for track in range(1, int(timeline.GetTrackCount("audio") or 0) + 1):
        for item in timeline.GetItemListInTrack("audio", track) or []:
            path = tl.file_of(item)
            if not path or not os.path.isfile(path) or os.path.normcase(path) in taken:
                continue
            length = int(item.GetEnd()) - int(item.GetStart())
            if best is None or length > best[0]:
                best = (length, item, path)
    if best is None:
        return None
    _length, item, path = best
    pool = item.GetMediaPoolItem()
    fps = tl.parse_fps(tl.prop(pool, "FPS"), tl_fps)
    first = tl.source_start_of(item)
    seconds = (int(item.GetEnd()) - int(item.GetStart())) / tl_fps
    return {"name": tl.name_of(item, "the sound"), "pool": pool, "path": path, "fps": fps,
            "lo": first / fps, "hi": first / fps + seconds, "tl_start": int(item.GetStart()) / tl_fps,
            "own": False, "stream": 0}


def ab_camera(ctx: Context, options: dict) -> Job:
    def body(build: Build):
        log = ctx.log
        original = build.original
        tl_fps = tl.timeline_fps(original)
        cams = _camera_files(original, tl_fps)
        angles = []
        for letter, entry in zip("AB", cams):
            c = entry["clip"]
            angles.append({"name": c.name, "clip": c, "pool": c.pool, "path": c.path, "fps": c.source_fps,
                           "lo": c.lo, "hi": c.hi, "tl_start": c.timeline_start / tl_fps, "stream": 0,
                           "track": entry["track"]})
            log("Camera {}: V{}, {}{}".format(letter, entry["track"], c.name,
                                              " (the longest of {} clips of that file)".format(entry["n"])
                                              if entry["n"] > 1 else ""))
        master = _master_audio(original, tl_fps, {os.path.normcase(a["path"]) for a in angles})
        reads = [("cam{}".format(i), a["path"], 0.0, None, 0) for i, a in enumerate(angles)]
        if master is not None:
            reads.append(("master", master["path"], max(0.0, master["lo"] - 1.0),
                          master["hi"] - master["lo"] + 2.0, 0))
        ctx.progress(0.05, "Reading the sound of the cameras")
        heard = yield Heavy(lambda: media.read_many(
            reads, ctx.stop, lambda n, total: ctx.progress(0.05 + 0.5 * n / total, "Read {} of {}".format(n, total))))
        for i, a in enumerate(angles):
            levels = heard.get("cam{}".format(i))
            a["levels"] = levels if isinstance(levels, media.Levels) else None
            a["score"] = speech.own_sound_score(a["levels"], a["lo"], a["hi"])
            if a["score"] <= -90.0:  # maybe the microphone is on another sound stream
                streams = media.probe(a["path"]).audio_streams
                for stream in range(1, min(4, streams)):
                    other = yield Heavy(lambda s=stream, p=a["path"]: media.read_levels(p, 0.0, None, s, ctx.stop))
                    score = speech.own_sound_score(other, a["lo"], a["hi"])
                    if score > a["score"]:
                        a["score"], a["stream"], a["levels"] = score, stream, other
            a["silent"] = a["score"] <= -90.0
        if master is not None:
            levels = heard.get("master")
            if not isinstance(levels, media.Levels):
                log("The separate sound '{}' could not be read, so a camera's sound is used.".format(master["name"]))
                master = None
            else:
                master["levels"] = levels
                log("Master sound: {} (the separate recording)".format(master["name"]))
        if master is None:
            scores = [a["score"] for a in angles]
            if max(scores) <= -90.0:
                raise RuntimeError("Neither camera has any sound, and there is no separate sound clip on the "
                                   "timeline. Put the sound you want on an audio track and try again.")
            if scores[1] > scores[0] + 1.0:
                angles.reverse()
            master = dict(angles[0], own=True)
            log("Master sound: camera A's own (the clearer of the two)")

        offsets = []
        for number, a in enumerate(angles):
            if master["own"] and number == 0:
                offsets.append(0.0)
                continue
            hint = (master["lo"] - master["tl_start"]) - (a["lo"] - a["tl_start"])
            found = None
            letter = "AB"[number]
            if options.get("sync", True) and not a["silent"] and a["levels"] is not None:
                ctx.progress(0.6 + 0.1 * number, "Lining up camera {} by sound".format(letter))
                notes: dict = {}
                found = yield Heavy(lambda a=a, h=hint, n=notes: sync.offset(
                    master["levels"], master["lo"], master["hi"], a["levels"], a["lo"], a["hi"], h, n))
                if found is None:
                    log("Camera {}: its sound did not match (best {:.0f}%, needs {:.0f}%), so it is lined up "
                        "where you placed it.".format(letter, 100 * notes.get("strength", 0.0),
                                                      100 * sync.SYNC_MIN_STRENGTH))
            elif a["silent"]:
                log("Camera {} has no sound of its own, so it is lined up where you placed it.".format(letter))
            if found is None:
                offsets.append(hint)
            else:
                moved = found[0] - hint
                offsets.append(found[0])
                log("Camera {} lined up by sound: {} (match {:.0f}%, {} place(s) agree).".format(
                    letter, "already in step" if abs(moved) < 0.02 else "moved {:.3f} s from where you placed it"
                    .format(moved), 100 * min(1.0, found[1]), found[2]))

        lo = max([master["lo"]] + [a["lo"] + o for a, o in zip(angles, offsets)]) + abcam.EDGE
        hi = min([master["hi"]] + [a["hi"] + o for a, o in zip(angles, offsets)]) - abcam.EDGE
        if hi - lo < 10.0:
            raise RuntimeError("Once lined up, the cameras and the sound overlap for only {:.0f} s, too short "
                               "for an A/B cut.".format(max(0.0, hi - lo)))
        every = max(2.0, float(options.get("every", 8.0)))
        snap = options.get("pauses", True)
        gaps = speech.pauses(master["levels"], lo, hi) if snap else []
        if snap and not gaps:
            log("No clear pauses were heard, so the switches are evenly spaced.")
        cuts, snapped = abcam.boundaries(lo, hi, every, gaps, snap)
        ctx.check()

        new, out_fps = build.new_timeline(" - A-B Cut")
        origin = tl.start_frame(new)
        cams_plan = [abcam.Angle(a["clip"].source_start, a["clip"].source_end, a["fps"], o)
                     for a, o in zip(angles, offsets)]
        placed, clamped, refused = 0, 0, 0
        current = 0
        pieces = len(cuts) - 1
        for number in range(pieces):
            ctx.check()
            ctx.progress(0.75 + 0.15 * number / max(1, pieces), "Adding piece {} of {}".format(number + 1, pieces))
            p, short = abcam.piece(number, current, cuts, lo, cams_plan, out_fps)
            clamped += short
            if p is None:
                continue
            info = {"mediaPoolItem": angles[p.angle]["pool"], "startFrame": p.first, "endFrame": p.last,
                    "mediaType": 1, "trackIndex": 1, "recordFrame": origin + p.record}
            result = build.media_pool.AppendToTimeline([info])
            if not result and p.last - p.first > 1:  # often the file's last frame
                info["endFrame"] = p.last - 1
                result = build.media_pool.AppendToTimeline([info])
            if not result:
                refused += 1
                if not placed and refused >= 3:
                    raise RuntimeError("Resolve would not add the first pieces of {} to the new timeline. Check "
                                       "that the clips are online and try again.".format(angles[p.angle]["name"]))
                continue
            placed += 1
            try:
                end = int(result[-1].GetEnd()) - origin
            except Exception:  # noqa: BLE001
                end = 0
            current = end if end > current else current + abcam.length_on_timeline(p, cams_plan, out_fps)
        if not placed:
            raise RuntimeError("No pieces could be added.")
        if clamped:
            log("{} piece(s) were shortened to stay inside their clip.".format(clamped))
        if refused:
            log("{} piece(s) were left out, which leaves a gap there.".format(refused))
        total = int(round((hi - lo) * out_fps))
        first = int(round(lo * master["fps"]))
        sound = build.media_pool.AppendToTimeline([{
            "mediaPoolItem": master["pool"], "startFrame": first,
            "endFrame": first + int(round(total * master["fps"] / out_fps)),
            "mediaType": 2, "trackIndex": 1, "recordFrame": origin}])
        if not sound:
            log("Resolve would not place the master sound. Drag {} onto A1 so it starts at the very "
                "beginning of the new timeline.".format(master["name"]))
        holes = tl.gaps_on(new)
        log("Checked: {}.".format("no gaps between pieces" if not holes else "{} gap(s) between pieces".format(holes)))
        ctx.progress(0.92, "Finishing touches")
        _finish(build, new, options)
        return ("Made '{}': {} pieces over {}, switching about every {:g} s{}. The sound plays unbroken on "
                "A1. Your timeline is unchanged.".format(
                    tl.name_of(new), placed, _minutes(hi - lo), every,
                    "; {} of {} switches moved to a pause".format(snapped, pieces - 1) if snap and gaps else ""))

    return (yield from _run(ctx, "the A/B cut", body))


# ---- Music montage ---------------------------------------------------------------------------------

def _song_on_timeline(timeline, footage: set[str]):
    """The longest music clip on the timeline's audio tracks: (path, pool item)."""
    best = None
    for item in tl.items(timeline, "audio"):
        path = tl.file_of(item)
        if (not path or not os.path.isfile(path) or os.path.normcase(path) in footage
                or not path.lower().endswith(MUSIC_EXTENSIONS)):
            continue
        length = int(item.GetEnd()) - int(item.GetStart())
        if best is None or length > best[0]:
            best = (length, path, item.GetMediaPoolItem())
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


def montage_job(ctx: Context, options: dict) -> Job:
    def body(build: Build):
        log = ctx.log
        original = build.original
        tl_fps = tl.timeline_fps(original)
        track = tl.choose_track(original, options.get("track", "Auto"))
        clips, _dropped = tl.track_clips(original, track)
        usable = [c for c in clips if c.whole_reason is None]
        for c in clips:
            if c.whole_reason is not None:
                log("Left out ({}): {}".format(c.whole_reason, c.name))
        if not usable:
            raise RuntimeError("There are no usable video clips on V{}.".format(track))
        path = (options.get("song_path") or "").strip().strip('"')
        song_pool = None
        if path and not os.path.isfile(path):
            raise RuntimeError("The song file was not found: {}".format(path))
        if not path:
            path, song_pool = _song_on_timeline(original, {os.path.normcase(c.path) for c in clips})
        if not path:
            raise RuntimeError("The montage needs a song: pick one with Browse, or put it on an audio track "
                               "of the timeline.")
        typed = float(options.get("bpm", 0) or 0)
        ctx.progress(0.1, "Listening for the beat")
        found = yield Heavy(lambda: song_beats(path, typed, ctx.data_dir, ctx.stop))
        log("Song: {} | {:.2f} BPM, {} beats, {} long{}.".format(
            os.path.basename(path), found["bpm"], len(found["times"]), _minutes(found["duration"]),
            " (beats remembered from last time)" if found.get("reused") else ""))
        if found.get("other_tempos") and not typed:
            log("  If the cuts feel off the beat, type the song's real tempo; also possible: {} BPM.".format(
                ", ".join(str(t) for t in found["other_tempos"])))
        song_start = max(0.0, float(options.get("song_start", 0) or 0))
        sources = [montage.Source(c.source_start, c.source_end, c.source_fps) for c in usable]
        plan = montage.plan(sources, found, tl_fps, int(options.get("per_cut", 0) or 0), song_start,
                            int(options.get("lead", 1)), bool(options.get("fill_song", True)),
                            bool(options.get("pace", False)))
        if not plan.pieces:
            raise RuntimeError("No clip is long enough for a piece of the montage.")
        for index in plan.unused:
            log("Too short for the montage: {}".format(usable[index].name))
        ctx.check()

        new, out_fps = build.new_timeline(" - Montage")
        origin = tl.start_frame(new)
        scale = out_fps / tl_fps if tl_fps else 1.0
        mute = options.get("mute", True)
        infos = []
        for p in plan.pieces:
            info = {"mediaPoolItem": usable[p.clip].pool, "startFrame": p.first, "endFrame": p.last,
                    "trackIndex": 1, "recordFrame": origin + int(round(p.position * scale))}
            if mute:
                info["mediaType"] = 1
            infos.append(info)
        ctx.progress(0.5, "Adding {} pieces on the beat".format(len(infos)))
        added, refused = tl.append(build.media_pool, infos, one_by_one=True)
        if not added:
            log("Resolve refused the exact placement, so the pieces are added one after another instead.")
            for info in infos:
                for key in ("trackIndex", "recordFrame", "mediaType"):
                    info.pop(key, None)
            added, refused = tl.append(build.media_pool, infos)
        if not added:
            raise RuntimeError("Resolve did not add any piece to the new timeline.")
        if refused:
            log("{} piece(s) Resolve would not add were left out.".format(len(refused)))
        ctx.check()

        ctx.progress(0.85, "Placing the song")
        if song_pool is None:
            song_pool = tl.find_pool_item(build.media_pool, path)
        offset = int(round(song_start * out_fps))
        music_end = int(round(found["duration"] * out_fps)) - offset
        if song_pool is None or music_end <= 0:
            log("The song could not be placed. Drag it onto an empty audio track so it starts at the very "
                "beginning of the new timeline; the cuts already sit on its beats.")
        else:
            used = 0
            count = int(new.GetTrackCount("audio") or 0)
            for index in range(1, count + 1):
                if new.GetItemListInTrack("audio", index):
                    used = index
            song_track = used + 1
            while count < song_track and (new.AddTrack("audio", "stereo") or new.AddTrack("audio")):
                count = int(new.GetTrackCount("audio") or 0)
            placed_song = count >= song_track and build.media_pool.AppendToTimeline([{
                "mediaPoolItem": song_pool, "startFrame": offset, "endFrame": offset + music_end,
                "mediaType": 2, "trackIndex": song_track, "recordFrame": origin}])
            if placed_song:
                log("Song placed on A{}, {} long.".format(song_track, _minutes(music_end / out_fps)))
            else:
                log("Resolve would not place the song. Drag it onto an empty audio track so it starts at the "
                    "very beginning of the new timeline.")
        short = (plan.song_end - plan.length) / tl_fps
        if short > 2.0:
            log("The clips cover {} of the song; {} has no picture (the footage ran out{}).".format(
                _minutes(plan.length / tl_fps), _minutes(short),
                "" if options.get("fill_song", True) else ": switch on Fill the whole song"))
        holes = tl.gaps_on(new)
        if holes:
            log("Check: {} blank spot(s) between pieces.".format(holes))
        ctx.progress(0.92, "Finishing touches")
        _finish(build, new, options)
        return ("Made '{}': {} pieces, {} beat(s) a cut{}. Your timeline is unchanged.".format(
            tl.name_of(new), len(added), plan.per_cut,
            ", {} not on a beat".format(plan.off_beat) if plan.off_beat else ""))

    return (yield from _run(ctx, "the montage", body))


# ---- On the open timeline: 9:16 copy and Look ----------------------------------------------------------

def vertical(ctx: Context, options: dict) -> Job:
    def body(build: Build):
        if tl.is_vertical(build.original):
            raise RuntimeError("'{}' is already vertical.".format(build.name))
        ctx.progress(0.3, "Copying the timeline")
        copy = build.duplicate(build.original, " 9x16")
        _frame_vertical(build, copy, options)
        return "Made '{}'. Your timeline is unchanged.".format(tl.name_of(copy))
        yield  # a generator, like every job

    return (yield from _run(ctx, "the 9:16 copy", body))


def look_copy(ctx: Context, options: dict) -> Job:
    def body(build: Build):
        lut = options.get("lut_path") or ""
        sideways = 0
        if options.get("upright", True):
            sideways = sum(1 for p in look.video_pieces(build.original) if camera.file_turn(tl.file_of(p)))
        if not lut and not sideways:
            raise RuntimeError("Nothing to do: no LUT is picked{}.".format(
                " and no clip was filmed with the camera on its side" if options.get("upright", True) else ""))
        ctx.progress(0.3, "Copying the timeline")
        copy = build.duplicate(build.original, " - Look")
        look.finish(build.project, copy, options, ctx.log)
        return "Made '{}'. Your timeline is unchanged.".format(tl.name_of(copy))
        yield  # a generator, like every job

    return (yield from _run(ctx, "the look", body))


JOBS = {"silences": cut_silences, "ab": ab_camera, "montage": montage_job, "vertical": vertical, "look": look_copy}
