"""The five jobs against the stand-in Resolve, with real generated media."""

from __future__ import annotations

import os
import tempfile

import fake_resolve as fake
import media_factory as mf
from tools.core import Stopped
from tools.core.work import Context, run_sync
from tools.resolve import jobs, look
from tools.resolve.link import Link
from tools.resolve.undo import History


def _setup():
    r = fake.Resolve()
    link = Link(factory=lambda: (r, ""))
    logs: list[str] = []
    ctx = Context(link, History(), log=logs.append, data_dir=os.environ["TOOLS_DATA_DIR"])
    return r, r.project, ctx, logs


def _video(timeline, track=1):
    return timeline.GetItemListInTrack("video", track) or []


def _contiguous(pieces) -> bool:
    return all(b.start == a.end for a, b in zip(pieces[:-1], pieces[1:]))


def test_cut_silences():
    r, project, ctx, logs = _setup()
    path = mf.talk()
    pool = project.pool_item(path, fps=25, seconds=20, resolution="320x180")
    original = project.new_timeline("Talk", 25)
    original.add_clip(pool, 0, 500)  # the whole 20 s
    original.add_clip(pool, 200, 450)  # 8-18 s again
    summary = run_sync(jobs.cut_silences(ctx, {"preset": "Natural", "upright": True}))
    new = project.GetCurrentTimeline()
    assert new.GetName() == "Talk - Cut" and "Talk - Cut" in summary, summary
    pieces = _video(new)
    got = [(p.source_start, p.source_end) for p in pieces]
    # 3.12-4.88 s and 9.12-10.38 s are cut (padding 0.12 s), in both clips
    assert got == [(0, 78), (122, 228), (260, 500), (200, 228), (260, 450)], got
    assert _contiguous(pieces) and pieces[0].start == new.GetStartFrame()
    assert len(new.GetItemListInTrack("audio", 1)) == 5
    assert len(_video(original)) == 2 and original.GetName() == "Talk"  # untouched
    assert r.page == "edit"
    assert "3 silence(s) cut" in summary, summary  # two in the first clip, one in the second
    message = ctx.history.undo(ctx.link)
    assert message.startswith("Undone"), message
    assert project.GetCurrentTimeline() is original and [t.name for t in project.timelines] == ["Talk"]


def test_cut_silences_with_a_vertical_copy_and_refused_pieces():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20, resolution="320x180")
    # Resolve thinks the file is a frame shorter than the clip claims: the
    # last piece is refused as a list, then taken one frame shorter.
    pool.frames = 499
    original = project.new_timeline("Talk", 25)
    original.add_clip(pool, 0, 499)
    original.tracks["video"][0][0].source_end = 500
    original.tracks["video"][0][0].end += 1
    run_sync(jobs.cut_silences(ctx, {"also_vertical": True}))
    names = [t.name for t in project.timelines]
    assert names == ["Talk", "Talk - Cut", "Talk - Cut 9x16"], names
    cut = project.timelines[1]
    assert project.GetCurrentTimeline() is cut
    assert _video(cut)[-1].source_end == 499  # one frame shorter
    tall = project.timelines[2]
    assert (tall.GetSetting("timelineResolutionWidth"), tall.GetSetting("timelineResolutionHeight")) == ("1080", "1920")
    zoom = _video(tall)[0].props["ZoomX"]
    assert abs(zoom - (1920 / 1080) ** 2 * 1.002) < 1e-6, zoom
    assert _video(cut)[0].props["ZoomX"] == 1.0  # the wide one keeps its framing
    assert ctx.history.undo(ctx.link).startswith("Undone")
    assert [t.name for t in project.timelines] == ["Talk"]


def test_stop_leaves_an_undoable_timeline():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20)
    original = project.new_timeline("Talk", 25)
    original.add_clip(pool, 0, 500)
    real_append = project.media_pool.AppendToTimeline

    def append_then_stop(infos):
        ctx.stop.set()
        return real_append(infos)

    project.media_pool.AppendToTimeline = append_then_stop
    try:
        run_sync(jobs.cut_silences(ctx, {}))
    except Stopped:
        pass
    else:
        raise AssertionError("the job should have stopped")
    assert any("unfinished timeline" in line for line in logs), logs
    assert ctx.history.undo(ctx.link).startswith("Undone")
    assert [t.name for t in project.timelines] == ["Talk"]


def _ab_timeline(project, master_audio=False):
    a, b, voice = mf.cameras()
    pa = project.pool_item(a, fps=25, seconds=60)
    pb = project.pool_item(b, fps=25, seconds=60)
    t = project.new_timeline("Interview", 25)
    t.add_clip(pa, 0, 1500, track=1)
    t.add_clip(pb, 0, 1450, track=2, start=t.GetStartFrame(), audio_track=2)  # placed together, not in step
    pv = None
    if master_audio:
        pv = project.pool_item(voice, seconds=60, video=False)
        t.add_clip(pv, 0, 1500, audio_track=3, start=t.GetStartFrame())
    return t, pa, pb, pv


def _check_ab(new, pa, pb, fps=25.0):
    pieces = _video(new)
    assert len(pieces) >= 5 and _contiguous(pieces)
    assert [p.pool for p in pieces[:4]] in ([pa, pb, pa, pb], [pb, pa, pb, pa])
    origin = new.GetStartFrame()
    # each camera's file time minus its place on the timeline is constant,
    # and the two differ by camera B's 1.3 s
    # (1.3 s is 32.5 frames at 25 fps, so a piece can round half a frame either way)
    lead = {}
    for p in pieces:
        lead.setdefault(p.pool, []).append((p.source_start - (p.start - origin)) / fps)
    assert all(max(v) - min(v) <= 1.0 / fps + 1e-9 for v in lead.values()), lead
    gap = sum(lead[pa]) / len(lead[pa]) - sum(lead[pb]) / len(lead[pb])
    assert abs(gap - mf.CAMERA_B_LATE) < 0.05, gap
    sound = new.GetItemListInTrack("audio", 1)
    assert len(sound) == 1 and sound[0].start == origin and abs(sound[0].end - pieces[-1].end) <= 2, sound
    return sound[0]


def test_ab_camera_on_the_cameras_own_sound():
    r, project, ctx, logs = _setup()
    original, pa, pb, _pv = _ab_timeline(project)
    summary = run_sync(jobs.ab_camera(ctx, {"every": 8, "pauses": True, "sync": True}))
    new = project.GetCurrentTimeline()
    assert new.GetName() == "Interview - A-B Cut", summary
    sound = _check_ab(new, pa, pb)
    assert sound.pool in (pa, pb)
    assert any("lined up by sound" in line for line in logs), logs
    assert "moved to a pause" in summary, summary


def test_ab_camera_with_a_separate_recorder():
    r, project, ctx, logs = _setup()
    original, pa, pb, pv = _ab_timeline(project, master_audio=True)
    run_sync(jobs.ab_camera(ctx, {"every": 5}))
    new = project.GetCurrentTimeline()
    sound = _check_ab(new, pa, pb)
    assert sound.pool is pv
    assert any("separate recording" in line for line in logs), logs


def test_montage():
    r, project, ctx, logs = _setup()
    a, b, _voice = mf.cameras()
    clips = [project.pool_item(p, fps=25, seconds=s, resolution="320x180")
             for p, s in ((mf.talk(), 20), (a, 60), (b, 60))]
    song = project.pool_item(mf.song(), seconds=30, video=False)
    t = project.new_timeline("Trip", 25)
    t.add_clip(clips[0], 0, 100)
    t.add_clip(clips[1], 250, 400)
    t.add_clip(clips[2], 0, 75)
    t.add_clip(song, 0, 750, audio_track=2, start=t.GetStartFrame())
    summary = run_sync(jobs.montage_job(ctx, {"per_cut": 0, "lead": 1, "fill_song": True, "mute": True}))
    new = project.GetCurrentTimeline()
    assert new.GetName() == "Trip - Montage", summary
    pieces = _video(new)
    origin = new.GetStartFrame()
    assert _contiguous(pieces) and pieces[0].start == origin
    for p in pieces[:-1]:  # every cut one frame before a beat (beats every 12.5 frames)
        beat = (p.end - origin + 1) / 12.5
        assert abs(beat - round(beat)) < 0.05, (p.end - origin, beat)
    assert not new.GetItemListInTrack("audio", 1) or new.GetItemListInTrack("audio", 1)[0].pool is song
    music = [i for k in range(1, new.GetTrackCount("audio") + 1) for i in new.GetItemListInTrack("audio", k)]
    assert len(music) == 1 and music[0].pool is song and music[0].end - music[0].start == 750, music
    assert pieces[-1].end - origin >= 750 - 20  # the whole song has pictures
    tempo = next(float(line.split("|")[1].split()[0]) for line in logs if line.startswith("Song:"))
    assert abs(tempo - mf.BPM) < 0.1, logs
    # the beats are remembered for next time
    logs.clear()
    project.SetCurrentTimeline(t)
    run_sync(jobs.montage_job(ctx, {}))
    assert any("remembered" in line for line in logs), logs


def test_vertical_and_upright():
    r, project, ctx, logs = _setup()
    wide = project.pool_item(mf.talk(), fps=25, seconds=20, resolution="320x180")
    side = project.pool_item(mf.canon_side(), fps=25, seconds=4, resolution="320x180")
    t = project.new_timeline("Mixed", 25)
    t.add_clip(wide, 0, 100)
    t.add_clip(side, 0, 100)
    run_sync(jobs.vertical(ctx, {"upright": True}))
    tall = project.GetCurrentTimeline()
    assert tall.GetName() == "Mixed 9x16"
    a, b = _video(tall)
    assert abs(a.props["ZoomX"] - (1920 / 1080) ** 2 * 1.002) < 1e-6 and a.props["RotationAngle"] == 0.0
    assert b.props["RotationAngle"] == 90.0 and abs(b.props["ZoomX"] - 1920 / 1080 * 1.002) < 1e-6, b.props
    assert _video(t)[1].props["RotationAngle"] == 0.0  # the original is untouched
    project.SetCurrentTimeline(tall)
    try:
        run_sync(jobs.vertical(ctx, {}))
    except RuntimeError as error:
        assert "already vertical" in str(error)
    else:
        raise AssertionError("a vertical timeline should be refused")


def test_look_copy_puts_the_lut_on():
    r, project, ctx, logs = _setup()
    folder = tempfile.mkdtemp(prefix="tools_luts_")
    os.makedirs(os.path.join(folder, "Film"))
    for name in (os.path.join(folder, "Mine.cube"), os.path.join(folder, "Film", "Warm.cube")):
        with open(name, "w", encoding="utf-8") as fh:
            fh.write("LUT_3D_SIZE 2\n")
    os.environ["TOOLS_LUT_DIR"] = folder
    try:
        luts = look.find_luts()
    finally:
        del os.environ["TOOLS_LUT_DIR"]
    names = list(luts)  # (Resolve's own LUTs on this computer are listed too)
    assert "Mine" in names and "Film / Warm" in names
    assert names.index("Mine") < min(i for i, n in enumerate(names) if " / " in n)  # your own first
    wide = project.pool_item(mf.talk(), fps=25, seconds=20)
    side = project.pool_item(mf.canon_side(), fps=25, seconds=4, resolution="320x180")
    t = project.new_timeline("Grade", 25)
    t.add_clip(wide, 0, 100)
    t.add_clip(side, 0, 100)
    r.lut_by_graph = True  # newer Resolve: only through the node graph
    run_sync(jobs.look_copy(ctx, {"lut_path": luts["Mine"], "upright": True}))
    copy = project.GetCurrentTimeline()
    assert copy.GetName() == "Grade - Look"
    assert [p.lut for p in _video(copy)] == [luts["Mine"]] * 2
    assert _video(copy)[1].props["RotationAngle"] == 90.0
    assert abs(_video(copy)[1].props["ZoomX"] - 1080 / 1920) < 1e-6  # shown whole on a wide frame
    assert all(p.lut is None for p in _video(t))
    # nothing to do: refused, nothing made
    project.SetCurrentTimeline(t)
    t.tracks["video"][0].pop()
    try:
        run_sync(jobs.look_copy(ctx, {"lut_path": "", "upright": True}))
    except RuntimeError as error:
        assert "Nothing to do" in str(error)
    else:
        raise AssertionError("expected a refusal")
    assert [x.name for x in project.timelines] == ["Grade", "Grade - Look"]


def test_undo_after_reconnecting_is_refused():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20)
    project.new_timeline("Talk", 25).add_clip(pool, 0, 500)
    run_sync(jobs.cut_silences(ctx, {}))
    r.closed = True
    assert ctx.link.project() is None  # the old link is gone
    r.closed = False
    ctx.link.ensure()  # a new link
    message = ctx.history.undo(ctx.link)
    assert "connect to Resolve again" in message, message
    assert len(project.timelines) == 2


def test_resolve_closed_and_opened_again():
    r = fake.Resolve()
    running = [True]
    link = Link(factory=lambda: (r, ""), running=lambda: running[0])
    assert link.ensure() is r.project and link.generation == 1
    r.project.name = None  # Resolve answers None once it is gone...
    r.manager.GetCurrentProject = lambda: None
    running[0] = False  # ...and its window has closed
    assert link.ensure() is None and link.problem == "DaVinci Resolve is not open." and link.resolve is None
    running[0] = True
    r2 = fake.Resolve()
    link._factory = lambda: (r2, "")
    assert link.ensure() is r2.project and link.generation == 2


def test_a_failed_vertical_copy_keeps_the_cut():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20)
    project.new_timeline("Talk", 25).add_clip(pool, 0, 500)
    real = fake.Timeline.DuplicateTimeline
    fake.Timeline.DuplicateTimeline = lambda self, name: None  # Resolve refuses the copy
    try:
        summary = run_sync(jobs.cut_silences(ctx, {"also_vertical": True}))
    finally:
        fake.Timeline.DuplicateTimeline = real
    assert "Made 'Talk - Cut'" in summary and any(line.startswith("No 9:16 copy") for line in logs), logs
    assert [t.name for t in project.timelines] == ["Talk", "Talk - Cut"]


def test_no_resolve_says_why():
    link = Link(factory=lambda: (None, "Tools cannot connect to DaVinci Resolve."))
    ctx = Context(link, History())
    try:
        run_sync(jobs.cut_silences(ctx, {}))
    except RuntimeError as error:
        assert "cannot connect" in str(error)
    else:
        raise AssertionError("expected an error")
