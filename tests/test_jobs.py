"""The five jobs against the stand-in Resolve, with real generated media.
Every job changes the open timeline; Undo puts the backup back."""

from __future__ import annotations

import os
import tempfile

import fake_resolve as fake
import media_factory as mf
from tools.core import Stopped
from tools.core.work import Context, run_sync
from tools.resolve import jobs, look
from tools.resolve import timeline as tl
from tools.resolve.link import Link
from tools.resolve.undo import History


def _setup():
    r = fake.Resolve()
    link = Link(factory=lambda: (r, ""))
    logs: list[str] = []
    ctx = Context(link, History(), log=logs.append, data_dir=os.environ["TOOLS_DATA_DIR"])
    return r, r.project, ctx, logs


def _one(ctx, name, options=None):
    return run_sync(jobs.run(ctx, [(name, options or {})]))


def _video(timeline, track=1):
    return timeline.GetItemListInTrack("video", track) or []


def _audio(timeline, track=1):
    return timeline.GetItemListInTrack("audio", track) or []


def _contiguous(pieces) -> bool:
    return all(b.start == a.end for a, b in zip(pieces[:-1], pieces[1:]))


def _undone(ctx, project, timeline, name):
    message = ctx.history.undo(ctx.link)
    assert message.startswith("Undone"), message
    back = project.GetCurrentTimeline()
    assert back.GetName() == name and back is not timeline and back in project.timelines
    assert timeline not in project.timelines
    return back


def test_ripple():
    old = tl.Placed(None, "video", 1, 0, None, "", 100, 200, 1000, 1.0, {}, "")
    # cut before the clip moves it; cuts inside split it; a cut over its start trims it
    assert jobs.ripple(old, [(10, 20), (120, 130), (150, 160)]) == [(1000, 1020, 90), (1030, 1050, 110),
                                                                   (1060, 1100, 130)]
    assert jobs.ripple(old, [(90, 110)]) == [(1010, 1100, 90)]
    assert jobs.ripple(old, [(250, 300)]) == [(1000, 1100, 100)]
    assert jobs.ripple(old, [(90, 210)]) == []
    double = tl.Placed(None, "video", 1, 0, None, "", 100, 200, 1000, 2.0, {}, "")  # 50 fps on a 25 timeline
    assert jobs.ripple(double, [(150, 160)]) == [(1000, 1100, 100), (1120, 1200, 150)]


def test_cut_silences_in_place():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20, resolution="320x180")
    timeline = project.new_timeline("Talk", 25)
    first = timeline.add_clip(pool, 0, 500)  # the whole 20 s
    timeline.add_clip(pool, 200, 450)  # 8-18 s again
    first[0].props.update(ZoomX=1.2, ZoomY=1.2)
    first[0].grade, first[0].colour = "warm", "Orange"
    summary = _one(ctx, "silences", {"preset": "Natural"})
    assert project.GetCurrentTimeline() is timeline and timeline.GetName() == "Talk", "the same timeline"
    pieces = _video(timeline)
    got = [(p.source_start, p.source_end) for p in pieces]
    # 3.12-4.88 s and 9.12-10.38 s are cut (padding 0.12 s), in both clips
    assert got == [(0, 78), (122, 228), (260, 500), (200, 228), (260, 450)], got
    assert _contiguous(pieces) and pieces[0].start == timeline.GetStartFrame()
    assert [(p.source_start, p.start) for p in _audio(timeline)] == [(p.source_start, p.start) for p in pieces]
    assert all(p.props["ZoomX"] == 1.2 and p.grade == "warm" and p.colour == "Orange" for p in pieces[:3])
    assert "Cut 3 silence(s) from 'Talk'" in summary, summary
    backup = project.timelines[-1]
    assert backup.GetName() == "Talk (before Tools)" and len(_video(backup)) == 2
    assert project.media_pool.folder_of(backup).GetName() == "Tools backups"
    assert project.media_pool.folder_of(timeline) is project.media_pool.root
    back = _undone(ctx, project, timeline, "Talk")
    assert [(p.source_start, p.source_end) for p in _video(back)] == [(0, 500), (200, 450)]
    assert project.media_pool.folder_of(back) is project.media_pool.root  # back where it was
    assert [t.name for t in project.timelines] == ["Talk"]


def test_other_tracks_stay_or_move_in_step():
    r, project, ctx, logs = _setup()
    talk = project.pool_item(mf.talk(), fps=25, seconds=20)
    broll = project.pool_item(mf.cameras()[0], fps=25, seconds=60)
    timeline = project.new_timeline("Talk", 25)
    origin = timeline.GetStartFrame()
    timeline.add_clip(talk, 0, 500)
    timeline.add_clip(broll, 0, 75, track=2, start=origin + 250, audio_track=2)  # 10-13 s, over the 2nd silence
    _one(ctx, "silences", {})
    assert [p.start - origin for p in _video(timeline, 2)] == [250]  # left where it was
    assert any("Only V1 and its sound were cut" in line for line in logs), logs
    ctx.history.undo(ctx.link)
    timeline = project.GetCurrentTimeline()
    _one(ctx, "silences", {"all_tracks": True})
    # with every track cut: it starts inside the 2nd cut (frames 228-260), so it loses its first 10
    # frames and moves up by both cuts (44 + 32 frames)
    moved = _video(timeline, 2)
    assert [(p.start - origin, p.source_start) for p in moved] == [(260 - 44 - 32, 10)], \
        [(p.start - origin, p.source_start) for p in moved]
    assert _audio(timeline, 2)[0].start == moved[0].start


def test_nothing_to_cut_changes_nothing():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20)
    timeline = project.new_timeline("Talk", 25)
    timeline.add_clip(pool, 0, 70)  # 0-2.8 s: no silence
    summary = _one(ctx, "silences", {})
    assert "nothing changed" in summary and len(project.timelines) == 1 and not ctx.history.records


def test_stop_part_way_can_be_undone():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20)
    timeline = project.new_timeline("Talk", 25)
    timeline.add_clip(pool, 0, 500)
    real = timeline.DeleteClips

    def delete_then_stop(items, ripple=False):
        ctx.stop.set()
        return real(items, ripple)

    timeline.DeleteClips = delete_then_stop
    try:
        run_sync(jobs.run(ctx, [("silences", {}), ("vertical", {})]))
    except Stopped:
        pass
    else:
        raise AssertionError("it should have stopped")
    assert any("Press Undo" in line for line in logs), logs
    _undone(ctx, project, timeline, "Talk")


def test_no_backup_no_change():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20)
    timeline = project.new_timeline("Talk", 25)
    timeline.add_clip(pool, 0, 500)
    timeline.DuplicateTimeline = lambda name: None
    try:
        _one(ctx, "silences", {})
    except RuntimeError as error:
        assert "backup" in str(error)
    else:
        raise AssertionError("no backup, no change")
    assert [(p.source_start, p.source_end) for p in _video(timeline)] == [(0, 500)]


def test_undo_refused_after_your_own_changes():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20)
    timeline = project.new_timeline("Talk", 25)
    timeline.add_clip(pool, 0, 500)
    _one(ctx, "silences", {})
    timeline.add_clip(pool, 0, 25, track=3)  # you kept editing
    message = ctx.history.undo(ctx.link)
    assert "was changed after Tools" in message and "Talk (before Tools)" in message, message
    assert timeline in project.timelines


def _ab_timeline(project):
    a, b, _voice = mf.cameras()
    pa = project.pool_item(a, fps=25, seconds=60)
    pb = project.pool_item(b, fps=25, seconds=60)
    t = project.new_timeline("Interview", 25)
    t.add_clip(pa, 0, 1500, track=1)
    t.add_clip(pb, 0, 1450, track=2, start=t.GetStartFrame(), audio_track=2)  # placed together, not in step
    return t, pa, pb


def test_ab_camera_in_place():
    r, project, ctx, logs = _setup()
    timeline, pa, pb = _ab_timeline(project)
    summary = _one(ctx, "ab", {"every": 8, "pauses": True, "sync": True})
    assert project.GetCurrentTimeline() is timeline
    assert [(p.source_start, p.source_end) for p in _video(timeline, 1)] == [(0, 1500)]  # camera A stays
    b = _video(timeline, 2)
    assert len(b) >= 3 and all(p.pool is pb for p in b)
    origin = timeline.GetStartFrame()
    for p in b:  # B is in step with A: B's file time = A's time - 1.3 s (half a frame either way)
        assert abs((p.source_start - (p.start - origin)) / 25.0 + mf.CAMERA_B_LATE) <= 0.5 / 25 + 1e-9, p.source_start
    assert all(b2.start > b1.end for b1, b2 in zip(b[:-1], b[1:]))  # A shows in between
    assert not _audio(timeline, 2) and len(_audio(timeline, 1)) == 1  # A's sound plays throughout
    assert "moved to a pause" in summary and any("lined up by sound" in line for line in logs), (summary, logs)


def test_ab_without_sync_uses_where_you_placed_them():
    r, project, ctx, logs = _setup()
    timeline, pa, pb = _ab_timeline(project)
    _one(ctx, "ab", {"every": 5})
    origin = timeline.GetStartFrame()
    assert all(p.source_start == p.start - origin for p in _video(timeline, 2))


def test_cut_to_a_song_on_the_timeline():
    r, project, ctx, logs = _setup()
    a, b, _voice = mf.cameras()
    clips = [project.pool_item(p, fps=25, seconds=s, resolution="320x180")
             for p, s in ((mf.talk(), 20), (a, 60), (b, 60))]
    song = project.pool_item(mf.song(), seconds=30, video=False)
    t = project.new_timeline("Trip", 25)
    origin = t.GetStartFrame()
    t.add_clip(clips[0], 0, 100)
    t.add_clip(clips[1], 250, 400)
    t.add_clip(clips[2], 0, 75)
    t.add_clip(song, 0, 750, audio_track=2, start=origin)
    summary = _one(ctx, "montage", {"lead": 1, "fill_song": True, "mute": True})
    assert project.GetCurrentTimeline() is t and "to the beat" in summary, summary
    pieces = _video(t)
    assert _contiguous(pieces) and pieces[0].start == origin
    for p in pieces[:-1]:  # every cut one frame before a beat (beats every 12.5 frames)
        beat = (p.end - origin + 1) / 12.5
        assert abs(beat - round(beat)) < 0.05, (p.end - origin, beat)
    assert not _audio(t, 1), "no camera sound"
    assert [(i.pool, i.start, i.end) for i in _audio(t, 2)] == [(song, origin, origin + 750)]  # the song stays
    assert pieces[-1].end - origin >= 750 - 20


def test_cut_to_a_set_tempo():
    r, project, ctx, logs = _setup()
    a, b, _voice = mf.cameras()
    clips = [project.pool_item(p, fps=25, seconds=60, resolution="320x180") for p in (a, b)]
    t = project.new_timeline("Trip", 25)
    t.add_clip(clips[0], 0, 250)  # 10 s
    t.add_clip(clips[1], 500, 700)  # 8 s
    try:
        _one(ctx, "montage", {"no_song": True, "bpm": 0})
    except RuntimeError as error:
        assert "type the tempo" in str(error)
    else:
        raise AssertionError("a tempo is needed")
    summary = _one(ctx, "montage", {"no_song": True, "bpm": 100, "per_cut": 4})
    assert "at 100 BPM" in summary, summary
    pieces = _video(t)
    origin = t.GetStartFrame()
    assert _contiguous(pieces) and len(pieces) == 2
    assert all(p.end - p.start == 60 for p in pieces)  # 4 beats of 15 frames
    assert [p.source_start for p in pieces] == [95, 570]  # the middle of each clip
    assert [(p.pool, p.start) for p in _audio(t)] == [(p.pool, p.start) for p in pieces]  # their own sound
    assert pieces[0].start == origin


def test_vertical_in_place():
    r, project, ctx, logs = _setup()
    wide = project.pool_item(mf.talk(), fps=25, seconds=20, resolution="320x180")
    side = project.pool_item(mf.canon_side(), fps=25, seconds=4, resolution="320x180")
    phone = project.pool_item(mf.phone_portrait(), fps=25, seconds=2, resolution="320x180")
    t = project.new_timeline("Mixed", 25)
    for pool, n in ((wide, 100), (side, 100), (phone, 50)):
        t.add_clip(pool, 0, n)
    _one(ctx, "vertical", {"upright": True})
    assert (t.GetSetting("timelineResolutionWidth"), t.GetSetting("timelineResolutionHeight")) == ("1080", "1920")
    a, b, c = _video(t)
    assert abs(a.props["ZoomX"] - (1920 / 1080) ** 2 * 1.002) < 1e-6 and a.props["RotationAngle"] == 0.0
    assert b.props["RotationAngle"] == 90.0 and abs(b.props["ZoomX"] - 1920 / 1080 * 1.002) < 1e-6, b.props
    assert c.props["ZoomX"] == 1.0, "the phone clip is shown tall already"
    assert "already vertical" in _one(ctx, "vertical", {})
    back = _undone(ctx, project, t, "Mixed")
    assert back.GetSetting("timelineResolutionWidth") == "1920" and _video(back)[0].props["ZoomX"] == 1.0


def test_look_in_place():
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
    assert names.index("Mine") < min(i for i, n in enumerate(names) if " / " in n)  # your own first
    four_three = project.pool_item(mf.four_three(), fps=25, seconds=2, resolution="240x180")
    full = project.pool_item(mf.talk(), fps=25, seconds=20, resolution="320x180")
    phone = project.pool_item(mf.phone_portrait(), fps=25, seconds=2, resolution="320x180")
    side = project.pool_item(mf.canon_side(), fps=25, seconds=4, resolution="320x180")
    t = project.new_timeline("Mixed", 25)
    for pool in (four_three, full, full, full, phone, side):
        t.add_clip(pool, 0, 50)
    _video(t)[2].props["Pan"] = 96.0  # moved right by hand: its left edge would show
    _video(t)[3].props.update(ZoomX=1.5, ZoomY=1.5)  # zoomed in by hand: already covers
    r.lut_by_graph = True  # newer Resolve: only through the node graph
    assert "nothing changed" in _one(ctx, "look", {})  # nothing picked
    _one(ctx, "look", {"lut_path": luts["Mine"], "upright": True, "fill": True})
    pieces = _video(t)
    assert [p.lut for p in pieces] == [luts["Mine"]] * 6
    zooms = [round(p.props["ZoomX"], 4) for p in pieces]
    expected = [round(1920 / 1440 * 1.002, 4),  # 4:3 fitted to 1440x1080: bars at the sides
                1.0,  # already fills
                round(2112 / 1920 * 1.002, 4),  # 1920 + 2 x 96 px
                1.5,  # your own zoom is kept
                round(1920 / 607.5 * 1.002, 4),  # portrait phone clip, shown 607.5x1080
                round(1920 / 1080 * 1.002, 4)]  # Canon on its side, turned: 1080x1920 shown
    assert zooms == expected, (zooms, expected)
    assert pieces[5].props["RotationAngle"] == 90.0
    back = _undone(ctx, project, t, "Mixed")
    assert all(p.lut is None for p in _video(back))


def test_apply_does_the_ticked_jobs_as_one_undo():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20, resolution="320x180")
    timeline = project.new_timeline("Talk", 25)
    timeline.add_clip(pool, 0, 500)
    summary = run_sync(jobs.run(ctx, [("silences", {}), ("ab", {}), ("vertical", {}), ("look", {"fill": True})]))
    assert "Applied 4 job(s) to 'Talk'" in summary, summary
    assert any(line.startswith("A/B camera: not done.") for line in logs), logs
    assert len(_video(timeline)) == 3 and timeline.GetSetting("timelineResolutionHeight") == "1920"
    assert [t.name for t in project.timelines] == ["Talk", "Talk (before Tools)"]  # one backup
    back = _undone(ctx, project, timeline, "Talk")
    assert len(_video(back)) == 1 and back.GetSetting("timelineResolutionHeight") == "1080"
    assert ctx.history.undo(ctx.link) == "Nothing to undo."


def test_grades_that_cannot_be_copied_are_said():
    r, project, ctx, logs = _setup()
    r.no_copy_grades = True
    pool = project.pool_item(mf.talk(), fps=25, seconds=20)
    project.new_timeline("Talk", 25).add_clip(pool, 0, 500)
    _one(ctx, "silences", {})
    assert any("would not copy the grade onto 3 piece(s)" in line for line in logs), logs


def test_undo_after_reconnecting_is_refused():
    r, project, ctx, logs = _setup()
    pool = project.pool_item(mf.talk(), fps=25, seconds=20)
    project.new_timeline("Talk", 25).add_clip(pool, 0, 500)
    _one(ctx, "silences", {})
    r.closed = True
    assert ctx.link.project() is None
    r.closed = False
    ctx.link.ensure()
    message = ctx.history.undo(ctx.link)
    assert "connect to Resolve again" in message and "Talk (before Tools)" in message, message


def test_resolve_closed_and_opened_again():
    r = fake.Resolve()
    running = [True]
    link = Link(factory=lambda: (r, ""), running=lambda: running[0])
    assert link.ensure() is r.project and link.generation == 1
    r.manager.GetCurrentProject = lambda: None
    running[0] = False
    assert link.ensure() is None and link.problem == "DaVinci Resolve is not open." and link.resolve is None
    running[0] = True
    r2 = fake.Resolve()
    link._factory = lambda: (r2, "")
    assert link.ensure() is r2.project and link.generation == 2


def test_no_resolve_says_why():
    link = Link(factory=lambda: (None, "Tools cannot connect to DaVinci Resolve."))
    ctx = Context(link, History())
    try:
        _one(ctx, "silences")
    except RuntimeError as error:
        assert "cannot connect" in str(error)
    else:
        raise AssertionError("expected an error")
