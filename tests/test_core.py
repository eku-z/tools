"""The pure parts: silences, sync, A/B switches, beats, montage, framing,
camera orientation."""

from __future__ import annotations

import os

import numpy as np

import media_factory as mf
from tools.core import abcam, beats, camera, framing, media, montage, speech, sync
from tools.core.media import HOPS_PER_SECOND, Levels
from tools.core.silence import SilenceSettings, complement_ranges, detect_silence, merge_intervals


def _levels(seconds: float, quiet, loud=0.1, start: float = 0.0) -> Levels:
    power = np.full(int(seconds * HOPS_PER_SECOND), loud ** 2)
    for a, b in quiet:
        power[int((a - start) * HOPS_PER_SECOND):int((b - start) * HOPS_PER_SECOND)] = 0.0
    return Levels(start, power)


def test_silence_rule():
    lv = _levels(20, [(3.0, 5.0), (9.0, 9.3), (12.0, 13.0)])
    cuts = detect_silence(lv, SilenceSettings(-35, 0.40, 0.12))
    # 9.0-9.3 is shorter than the minimum; the others lose 0.12 s of padding at each end
    assert len(cuts) == 2, cuts
    assert abs(cuts[0][0] - 3.12) < 0.011 and abs(cuts[0][1] - 4.88) < 0.011, cuts
    assert abs(cuts[1][0] - 12.12) < 0.011 and abs(cuts[1][1] - 12.88) < 0.011, cuts
    assert complement_ranges(0, 10, [(2, 3), (2.5, 4), (8, 12)]) == [(0, 2), (4, 8)]
    assert merge_intervals([(5, 6), (1, 2), (1.5, 3)]) == [(1, 3), (5, 6)]


def test_silence_on_real_sound():
    path = mf.talk()
    for engine in ("pyav", "cli"):
        lv = media.read_levels(path, engine=engine)
        assert lv is not None and abs(lv.end - 20.0) < 0.1, (engine, lv.end)
        cuts = detect_silence(lv, SilenceSettings(-35, 0.40, 0.12))
        assert len(cuts) == 2, (engine, cuts)
        for (a, b), (qa, qb) in zip(cuts, mf.SILENCES):
            assert abs(a - (qa + 0.12)) < 0.04 and abs(b - (qb - 0.12)) < 0.04, (engine, cuts)
        # only part of the file
        part = media.read_levels(path, 8.0, 4.0, engine=engine)
        assert abs(part.start - 8.0) < 0.002 and abs(part.end - 12.0) < 0.05, (engine, part.start, part.end)
        cuts = detect_silence(part, SilenceSettings())
        assert len(cuts) == 1 and abs(cuts[0][0] - 9.12) < 0.04, (engine, cuts)


def test_both_decoders_agree():
    path = mf.cameras()[0]
    a = media.read_levels(path, 10.0, 30.0, engine="pyav")
    b = media.read_levels(path, 10.0, 30.0, engine="cli")
    n = min(a.power.size, b.power.size)
    assert abs(a.power.size - b.power.size) <= 2 and abs(a.start - b.start) < 0.002, (a.power.size, b.power.size)
    db = 10 * np.log10(np.vstack([a.power[:n], b.power[:n]]) + 1e-10)
    assert float(np.median(np.abs(db[0] - db[1]))) < 0.5, np.abs(db[0] - db[1]).max()
    many = media.read_many([("short", path, 10.0, 30.0, 0), ("long", path, 0.0, None, 0)])
    assert many["short"].power.size == a.power.size and abs(many["long"].end - 60.0) < 0.1


def test_probe_and_no_sound():
    info = media.probe(mf.talk())
    assert info.audio_streams == 1 and (info.width, info.height) == (320, 180), info
    assert abs(info.duration - 20.0) < 0.2, info
    silent = os.path.join(mf.folder(), "no_sound.mp4")
    if not os.path.isfile(silent):
        mf._ffmpeg(*mf._video(2), "-c:v", "libx264", "-preset", "ultrafast", silent)
    assert media.read_levels(silent) is None
    many = media.read_many([("a", mf.talk(), 0.0, 2.0, 0), ("b", silent, 0.0, None, 0)])
    assert isinstance(many["a"], Levels) and many["b"] is None


def test_sync_finds_the_offset():
    a, b, voice = mf.cameras()
    la, lb = media.read_levels(a), media.read_levels(b)
    found = sync.offset(la, 0.0, la.end, lb, 0.0, lb.end, hint=0.0)
    assert found is not None and abs(found[0] - mf.CAMERA_B_LATE) < 0.02, found
    # searching the whole file when the timeline places them far apart
    found = sync.offset(la, 0.0, la.end, lb, 0.0, lb.end, hint=-25.0)
    assert found is not None and abs(found[0] - mf.CAMERA_B_LATE) < 0.02, found
    lv = media.read_levels(voice)
    found = sync.offset(lv, 0.0, lv.end, la, 0.0, la.end, hint=0.3)
    assert found is not None and abs(found[0]) < 0.02, found


def test_pauses_and_switches():
    lv = _levels(60, [(7.5, 8.4), (16.2, 16.6), (24.0, 25.2), (40.0, 40.3)])
    gaps = speech.pauses(lv, 0.0, 60.0)
    # 40.0-40.3 is shorter than ZCuts' 0.35 s, so it is not a pause
    assert [round(c, 2) for c, _length in gaps] == [7.95, 16.4, 24.6], gaps
    assert [round(length, 2) for _c, length in gaps] == [0.9, 0.4, 1.2], gaps
    cuts, snapped = abcam.boundaries(0.0, 60.0, 8.0, gaps, True)
    assert cuts[0] == 0.0 and cuts[-1] == 60.0
    assert abs(cuts[1] - 7.95) < 0.01 and abs(cuts[2] - 16.4) < 0.01 and abs(cuts[3] - 24.6) < 0.01, cuts
    assert snapped >= 3
    assert all(b - a >= 3.2 - 1e-9 for a, b in zip(cuts[:-1], cuts[1:])), cuts
    plain, moved = abcam.boundaries(0.0, 60.0, 8.0, gaps, False)
    assert moved == 0 and plain[1] == 8.0


def test_ab_pieces_have_no_gaps():
    angles = [abcam.Angle(0, 60 * 25, 25.0, 0.0), abcam.Angle(0, 60 * 30, 30.0, 1.3)]
    cuts = [2.0, 10.0, 18.1, 26.0, 40.0]
    pieces, clamped = abcam.pieces(cuts, 2.0, angles, 25.0)
    assert [p.angle for p in pieces] == [0, 1, 0, 1] and clamped == 0
    position = 0
    for p in pieces:
        assert p.record == position
        position += abcam.length_on_timeline(p, angles, 25.0)
    assert position == 38 * 25
    second = pieces[1]  # camera B: master time 10.0 is B's file time 8.7, at 30 fps
    assert second.first == int(round(8.7 * 30)), second


def test_beats_of_a_click_track():
    samples = media.read_samples(mf.song())
    found = beats.detect(samples)
    assert abs(found["bpm"] - mf.BPM) < 0.5, found["bpm"]
    gaps = np.diff(found["times"])
    assert abs(float(np.median(gaps)) - 0.5) < 0.01
    # beats sit on the clicks (every 0.5 s from 0)
    errors = [abs(t - round(t / 0.5) * 0.5) for t in found["times"]]
    assert max(errors) < 0.03, max(errors)
    bar_starts = found["times"][found["downbeat"]::4]
    assert all(abs(t - round(t / 2.0) * 2.0) < 0.03 for t in bar_starts), bar_starts[:4]


def test_montage_lands_on_beats():
    period = 0.5
    found = {"times": [i * period for i in range(120)], "period": period, "duration": 60.0, "downbeat": 0,
             "energy": []}
    sources = [montage.Source(0, 25 * 20, 25.0), montage.Source(100, 100 + 25 * 6, 25.0),
               montage.Source(0, 50 * 15, 50.0), montage.Source(0, 5, 25.0)]
    plan = montage.plan(sources, found, 25.0, per_cut=0, lead=1)
    assert plan.per_cut == 4  # 4 beats of 0.5 s are nearest 2.5 s
    beat_frames = {int(round(t * 25)) - 1 for t in found["times"]}
    position = 0
    for p in plan.pieces:
        assert p.position == position, (p, position)
        assert p.position + p.length in beat_frames or not p.on_beat, p
        src = sources[p.clip]
        assert src.first_frame <= p.first < p.last <= src.end_frame
        position += p.length
    assert 3 in plan.unused  # 5 frames is too short
    assert plan.length >= plan.song_end - 0.5 * period * 25 - 25, (plan.length, plan.song_end)  # whole song filled
    assert [p.clip for p in plan.pieces[:3]] == [0, 1, 2]
    once = montage.plan(sources, found, 25.0, fill_song=False)
    assert len(once.pieces) == 3


def test_framing():
    assert abs(framing.parse_fps("29.97") - 30000 / 1001) < 1e-9
    assert framing.parse_fps("25") == 25.0 and framing.parse_fps("nonsense", 24.0) == 24.0
    assert framing.parse_fps("30000/1001") == 30000 / 1001
    assert framing.parse_size("3840x2160") == (3840.0, 2160.0)
    # a 16:9 clip on a 9:16 frame covers it at 3.16x (1920/1080 squared, with a little spare)
    z = framing.cover_zoom((1920, 1080), (1080, 1920), 0.0, "scaleToFit", 1.0)
    assert abs(z - (1920 / 1080) ** 2) < 1e-6, z
    # Resolve fits the stored (landscape) picture first, 1080x607.5, then it is turned
    upright_tall = framing.placement((3840, 2160), (1080, 1920), 270, fill=True)
    assert upright_tall.rotation == 90.0 and abs(upright_tall.zoom - 1920 / 1080 * 1.002) < 1e-6, upright_tall
    upright_wide = framing.placement((3840, 2160), (1920, 1080), 90, fill=False)
    assert upright_wide.rotation == -90.0 and abs(upright_wide.zoom - 1080 / 1920) < 1e-6, upright_wide
    plain = framing.placement((1920, 1080), (1920, 1080), 0, fill=True)
    assert plain.zoom == 1.0 and plain.rotation == 0.0


def test_canon_orientation():
    assert camera.canon_auto_rotate(mf.canon_makernote(3)) == 270
    assert camera.canon_auto_rotate(mf.canon_makernote(1)) == 90
    assert camera.canon_auto_rotate(mf.canon_makernote(0)) == 0
    assert camera.canon_auto_rotate(b"no exif here") == 0
    assert camera.file_turn(mf.canon_side()) == 270
    assert camera.file_turn(mf.talk()) == 0
    assert media.probe(mf.canon_side()).width == 320  # still a readable video
