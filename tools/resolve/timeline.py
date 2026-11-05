"""Reading a Resolve timeline and building new ones, ported from ZCuts
(engine/03_helpers.py, engine/11_apply.py match_timeline_settings and
append_one_by_one, engine/14_undo.py delete_timelines, engine/07_music.py
find_pool_item and report_gaps; build 259).

Times: AppendToTimeline takes source frames with endFrame EXCLUSIVE (all
of ZCuts' timing assumes it; the owner's frames check is still open) and
recordFrame in timeline frames, counted from the timeline's own start
frame (usually 01:00:00:00).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from tools.core.framing import parse_fps, parse_size

SOLID = "nested timeline or compound clip"


def prop(pool_item, name: str) -> str:
    try:
        value = pool_item.GetClipProperty(name)
    except Exception:  # noqa: BLE001
        return ""
    return "" if value is None else str(value).strip()


def timeline_fps(timeline) -> float:
    fps = parse_fps(timeline.GetSetting("timelineFrameRate"), 0.0)
    if fps <= 0:
        raise RuntimeError("Could not read the timeline's frame rate.")
    return fps


def name_of(thing, default: str = "a timeline") -> str:
    try:
        name = thing.GetName()
        return str(name) if name else default
    except Exception:  # noqa: BLE001
        return default


def start_frame(timeline) -> int:
    try:
        return int(timeline.GetStartFrame() or 0)
    except Exception:  # noqa: BLE001
        return 0


def frame_size(timeline) -> tuple[float, float]:
    try:
        size = (float(timeline.GetSetting("timelineResolutionWidth")),
                float(timeline.GetSetting("timelineResolutionHeight")))
        if size[0] > 0 and size[1] > 0:
            return size
    except Exception:  # noqa: BLE001
        pass
    return 1920.0, 1080.0


def mismatch_mode(timeline, project) -> str:
    """How Resolve fits a clip of another size: scaleToFit unless set."""
    for holder in (timeline, project):
        try:
            value = holder.GetSetting("timelineInputResMismatchBehavior")
            if value:
                return str(value)
        except Exception:  # noqa: BLE001
            continue
    return "scaleToFit"


def items(timeline, kind: str = "video", track: Optional[int] = None) -> list:
    """The clips on one track, or on every track of that kind."""
    try:
        tracks = [track] if track else range(1, int(timeline.GetTrackCount(kind) or 0) + 1)
        found = []
        for index in tracks:
            found += list(timeline.GetItemListInTrack(kind, index) or [])
        return found
    except Exception:  # noqa: BLE001
        return []


def file_of(item) -> str:
    try:
        pool = item.GetMediaPoolItem()
    except Exception:  # noqa: BLE001
        return ""
    path = prop(pool, "File Path") if pool is not None else ""
    return os.path.abspath(path) if path else ""


# ---- reading the clips of a track -----------------------------------------------------------

@dataclass
class Clip:
    item: object
    pool: object
    name: str
    path: str  # "" for compound/nested clips
    timeline_start: int
    timeline_end: int
    timeline_fps: float
    source_fps: float
    source_start: int  # source frames, end exclusive
    source_end: int
    expected_end: int
    whole_reason: Optional[str] = None  # kept uncut, and why

    @property
    def lo(self) -> float:
        return self.source_start / self.source_fps

    @property
    def hi(self) -> float:
        return self.source_end / self.source_fps

    @property
    def size(self):
        return parse_size(prop(self.pool, "Resolution"))


def source_start_of(item) -> int:
    for getter in ("GetSourceStartFrame", "GetLeftOffset"):
        fn = getattr(item, getter, None)
        if callable(fn):
            value = fn()
            if value is not None:
                return int(round(float(value)))
    raise RuntimeError("Could not read where a timeline clip starts in its file.")


def source_bounds(item, tl_fps: float, src_fps: float) -> tuple[int, int, int, bool]:
    """(source start, exclusive source end, expected end, retimed). When
    Resolve reports a source range clearly another length than normal speed
    gives, the clip has a speed change."""
    first = source_start_of(item)
    duration = float(item.GetEnd()) - float(item.GetStart())
    if duration <= 0:
        raise RuntimeError("The timeline clip has no length.")
    frames = int(round(duration / tl_fps * src_fps))
    expected = first + frames
    retimed, actual = False, expected
    getter = getattr(item, "GetSourceEndFrame", None)
    if callable(getter):
        try:
            value = getter()
            if value is not None:
                reported = int(round(float(value)))
                if reported - first > 0 and abs(reported - first - frames) > max(3, int(0.03 * frames)):
                    retimed, actual = True, reported
        except Exception:  # noqa: BLE001
            pass
    return first, actual, expected, retimed


def speed_percent(item) -> Optional[float]:
    try:
        return round(float(str(item.GetProperty("Speed")).strip().rstrip("%")), 3)
    except Exception:  # noqa: BLE001
        return None


def read_clip(item, tl_fps: float) -> Optional[Clip]:
    """A timeline piece as a Clip, or None for titles, generators and other
    pieces with no source clip."""
    pool = item.GetMediaPoolItem()
    if pool is None:
        return None
    path = prop(pool, "File Path")
    reason = None
    if not path:
        reason = SOLID
    elif not os.path.isfile(path):
        reason = "media offline"
    src_fps = parse_fps(prop(pool, "FPS"), tl_fps)
    try:
        first, end, expected, retimed = source_bounds(item, tl_fps, src_fps)
    except Exception:  # noqa: BLE001
        return None
    if retimed and reason is None:
        reason = "speed change"
    return Clip(item, pool, name_of(item, "Unnamed clip"), os.path.abspath(path) if path else "",
                int(item.GetStart()), int(item.GetEnd()), tl_fps, src_fps, first, end, expected, reason)


def track_clips(timeline, track: int) -> tuple[list[Clip], list[str]]:
    """The clips of one video track in timeline order, and the names of the
    pieces a script cannot put on a new timeline. Clips that cannot be cut
    (compound clips, offline media, speed changes) are marked whole."""
    tl_fps = timeline_fps(timeline)
    clips, dropped = [], []
    for item in timeline.GetItemListInTrack("video", track) or []:
        clip = read_clip(item, tl_fps)
        if clip is None:
            dropped.append(name_of(item, "Unnamed clip"))
        else:
            clips.append(clip)
    # If every ordinary clip looks retimed by exactly the ratio of the frame
    # rates while Resolve says it plays at 100 %, the reading is a frame-rate
    # mix-up, not a speed change (ZCuts builds 222 and 224).
    ordinary = [c for c in clips if c.whole_reason in (None, "speed change")]
    flagged = [c for c in ordinary if c.whole_reason == "speed change"]

    def mix_up(c: Clip) -> bool:
        expected, actual = c.expected_end - c.source_start, c.source_end - c.source_start
        if expected <= 0 or actual <= 0:
            return False
        ratio, mixed = actual / float(expected), c.timeline_fps / float(c.source_fps)
        if abs(mixed - 1.0) < 0.01 or not any(abs(ratio - w) <= 0.03 * w for w in (mixed, 1.0 / mixed)):
            return False
        return speed_percent(c.item) == 100.0

    if len(ordinary) >= 3 and len(flagged) == len(ordinary) and all(mix_up(c) for c in flagged):
        for c in flagged:
            c.whole_reason, c.source_end = None, c.expected_end
    clips.sort(key=lambda c: c.timeline_start)
    return clips, dropped


def choose_track(timeline, choice: str = "Auto") -> int:
    """The chosen video track, or on Auto the one with the most real footage."""
    count = int(timeline.GetTrackCount("video") or 0)
    if count < 1:
        raise RuntimeError("This timeline has no video tracks.")
    choice = (choice or "Auto").strip().upper()
    if choice.startswith("V") and choice[1:].isdigit():
        wanted = int(choice[1:])
        if wanted > count:
            raise RuntimeError("This timeline has no {} track (it has {} video tracks).".format(choice, count))
        return wanted
    best, best_seconds, solid = 1, -1.0, {}
    for index in range(1, count + 1):
        seconds = 0.0
        for item in timeline.GetItemListInTrack("video", index) or []:
            try:
                pool = item.GetMediaPoolItem()
                length = float(item.GetEnd()) - float(item.GetStart())
            except Exception:  # noqa: BLE001
                continue
            if pool is None:
                continue
            if prop(pool, "File Path"):
                seconds += length
            else:
                solid[index] = solid.get(index, 0.0) + length
        if seconds > best_seconds + 0.001:
            best, best_seconds = index, seconds
    if best_seconds <= 0:
        if solid:
            return max(solid, key=solid.get)
        raise RuntimeError("No video track on '{}' holds video clips.".format(name_of(timeline)))
    return best


# ---- making new timelines ----------------------------------------------------------------------

def timeline_names(project) -> set[str]:
    names = set()
    try:
        for index in range(1, int(project.GetTimelineCount() or 0) + 1):
            found = project.GetTimelineByIndex(index)
            if found is not None:
                names.add(name_of(found))
    except Exception:  # noqa: BLE001
        pass
    return names


def unique_name(project, base: str) -> str:
    existing = timeline_names(project)
    if base not in existing:
        return base
    suffix = 2
    while "{} {}".format(base, suffix) in existing:
        suffix += 1
    return "{} {}".format(base, suffix)


def match_settings(original, new) -> float:
    """Give a new timeline the original's frame rate and picture size (a new
    empty timeline starts from the project's defaults). Returns the rate the
    new timeline really has."""
    names = ("timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight",
             "timelineDropFrameTimecode")
    wanted = {}
    for name in names:
        value = original.GetSetting(name)
        if value not in (None, ""):
            wanted[name] = str(value)
    if any(str(new.GetSetting(name)) != value for name, value in wanted.items()):
        new.SetSetting("useCustomSettings", "1")
        for name, value in wanted.items():
            new.SetSetting(name, value)
    return parse_fps(new.GetSetting("timelineFrameRate"), 0.0) or timeline_fps(original)


def make_vertical(timeline) -> bool:
    """Make a timeline 1080x1920. True when Resolve took it."""
    try:
        timeline.SetSetting("useCustomSettings", "1")
        timeline.SetSetting("timelineResolutionWidth", "1080")
        timeline.SetSetting("timelineResolutionHeight", "1920")
        return (int(float(timeline.GetSetting("timelineResolutionWidth"))),
                int(float(timeline.GetSetting("timelineResolutionHeight")))) == (1080, 1920)
    except Exception:  # noqa: BLE001
        return False


def is_vertical(timeline) -> bool:
    width, height = frame_size(timeline)
    return height > width


def append(media_pool, infos: list[dict], one_by_one: bool = False) -> tuple[list, list[int]]:
    """Add pieces to the current timeline. Returns (added items, indexes of
    pieces Resolve would not take).

    Resolve can take only the start of a list (it stops at a piece it
    dislikes and drops the rest without a word) or refuse the whole list for
    one bad piece, so what it did not take is added one at a time; a refused
    piece is tried once more a frame shorter, since a piece that ends exactly
    on its file's last frame is the usual reason (ZCuts builds 216 and 224).
    With ``one_by_one`` (pieces placed at exact frames, like a montage) every
    piece goes alone, so a refused one cannot drop the rest."""
    added: list = []
    taken = 0
    if not one_by_one and infos:
        result = media_pool.AppendToTimeline(list(infos))
        if isinstance(result, (list, tuple)) and result:
            added = list(result)
            taken = len(added)
            if taken >= len(infos):
                return added, []
    refused = []
    for index in range(taken, len(infos)):
        info = infos[index]
        result = media_pool.AppendToTimeline([info])
        if not result and info["endFrame"] - info["startFrame"] > 1:
            shorter = dict(info, endFrame=info["endFrame"] - 1)
            result = media_pool.AppendToTimeline([shorter])
            if result:
                infos[index] = shorter
        if result:
            added += list(result)
        else:
            refused.append(index)
    return added, refused


def gaps_on(timeline, track: int = 1) -> int:
    """Blank spots between the pieces of a video track."""
    pieces = items(timeline, "video", track)
    return sum(1 for a, b in zip(pieces[:-1], pieces[1:]) if int(b.GetStart()) > int(a.GetEnd()))


def find_pool_item(media_pool, path: str):
    """The Media Pool clip for a file, importing it when it is not there."""
    wanted = os.path.normcase(os.path.abspath(path))
    folders = []
    try:
        folders = [media_pool.GetRootFolder()]
    except Exception:  # noqa: BLE001
        pass
    seen = 0
    while folders and seen < 500:
        folder = folders.pop()
        seen += 1
        if folder is None:
            continue
        try:
            for clip in folder.GetClipList() or []:
                clip_path = prop(clip, "File Path")
                if clip_path and os.path.normcase(os.path.abspath(clip_path)) == wanted:
                    return clip
            folders.extend(folder.GetSubFolderList() or [])
        except Exception:  # noqa: BLE001
            continue
    try:
        imported = media_pool.ImportMedia([path]) or []
        return imported[0] if imported else None
    except Exception:  # noqa: BLE001
        return None


def delete_timelines(project, media_pool, timelines, keep=None) -> list[str]:
    """Delete timelines Tools made. Returns the names still there.

    Resolve refuses to delete the open timeline and answers False without
    saying why, so another one is opened first. If deleting them together
    fails, each is tried alone, then as its clip in the Media Pool; what is
    really gone is checked by name afterwards."""
    names = [name_of(t) for t in timelines]
    try:
        current = project.GetCurrentTimeline()
        if current is not None and name_of(current) in names:
            options = [keep] if keep is not None else []
            options += [project.GetTimelineByIndex(i) for i in range(1, int(project.GetTimelineCount() or 0) + 1)]
            for option in options:
                if option is not None and name_of(option) not in names and project.SetCurrentTimeline(option):
                    break
    except Exception:  # noqa: BLE001
        pass

    def attempt(action):
        try:
            action()
        except Exception:  # noqa: BLE001
            pass
        now = timeline_names(project)
        return [n for n in names if n in now]

    left = attempt(lambda: media_pool.DeleteTimelines(list(timelines)))
    if left:
        left = attempt(lambda: [media_pool.DeleteTimelines([t]) for t in timelines if name_of(t) in left])
    if left:
        def as_clips():
            pool_items = [t.GetMediaPoolItem() for t in timelines if name_of(t) in left]
            pool_items = [i for i in pool_items if i is not None]
            if pool_items:
                media_pool.DeleteClips(pool_items)
        left = attempt(as_clips)
    return left


# ---- changing the open timeline in place ----------------------------------------------------------
# Resolve's scripting cannot cut or move a clip. What ZCuts' Update
# timeline did (engine/11_live_update.py delete_items, append_entries, and
# CopyGrades for the colour) is the way: take the clips off and put their
# kept pieces back at exact frames, each piece given its clip's settings.

READ_ONLY = {"Speed"}  # properties Resolve reports but a script cannot set


@dataclass
class Placed:
    """A clip about to be taken off, with what its pieces need from it."""
    item: object
    kind: str  # "video" or "audio"
    track: int
    index: int  # its place on the track, left to right (to find it in the backup)
    pool: object
    path: str
    start: int  # timeline frames, end exclusive
    end: int
    source_start: int
    rate: float  # source frames per timeline frame
    props: dict
    colour: str


def properties(item) -> dict:
    try:
        found = item.GetProperty()
    except Exception:  # noqa: BLE001
        return {}
    return {k: v for k, v in dict(found or {}).items() if k not in READ_ONLY}


def placed(timeline, kind: str, track: int, index: int, item) -> Optional[Placed]:
    """What it takes to put this clip's pieces back, or None for a piece
    with no Media Pool clip (titles, generators), which cannot be added."""
    try:
        pool = item.GetMediaPoolItem()
    except Exception:  # noqa: BLE001
        pool = None
    if pool is None:
        return None
    tl_fps = timeline_fps(timeline)
    src_fps = parse_fps(prop(pool, "FPS"), tl_fps) if kind == "video" else tl_fps
    try:
        colour = str(item.GetClipColor() or "")
    except Exception:  # noqa: BLE001
        colour = ""
    return Placed(item, kind, track, index, pool, file_of(item), int(item.GetStart()), int(item.GetEnd()),
                  source_start_of(item), src_fps / tl_fps, properties(item), colour)


def every_clip(timeline, kinds=("video", "audio")) -> list[tuple[str, int, int, object]]:
    """(kind, track, index, item) of every piece on the timeline."""
    out = []
    for kind in kinds:
        for track in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
            row = sorted(timeline.GetItemListInTrack(kind, track) or [], key=lambda i: int(i.GetStart()))
            out += [(kind, track, index, item) for index, item in enumerate(row)]
    return out


def linked_audio(timeline, video_item) -> list[tuple[int, int, object]]:
    """(track, index, item) of the sound that came with a video clip: audio
    over the same frames from the same file (Resolve does not say which
    pieces are linked)."""
    path = file_of(video_item)
    start, end = int(video_item.GetStart()), int(video_item.GetEnd())
    return [(track, index, item) for kind, track, index, item in every_clip(timeline, ("audio",))
            if path and int(item.GetStart()) == start and int(item.GetEnd()) == end and file_of(item) == path]


def fingerprint(timeline) -> tuple:
    """What is on a timeline, to tell whether it was changed since."""
    try:
        return tuple((kind, track, int(i.GetStart()), int(i.GetEnd()), file_of(i))
                     for kind, track, _index, i in every_clip(timeline))
    except Exception:  # noqa: BLE001
        return ()


def put_back(project, media_pool, timeline, olds: list[Placed], pieces: dict, backup=None) -> dict:
    """Take ``olds`` off the timeline and put ``pieces[id(old)]`` back, each
    (first source frame, end, timeline frame) one piece of that clip on its
    own track, with the clip's settings, colour label and (copied from the
    backup, where the clip still is) its grade. Returns counts: added,
    refused, grades, no_grade."""
    counts = {"added": 0, "refused": 0, "grades": 0, "no_grade": 0}
    if not olds:
        return counts
    if not timeline.DeleteClips([p.item for p in olds], False):
        raise RuntimeError("Resolve would not take the clips off the timeline, so nothing was changed.")
    infos, owners = [], []
    for old in olds:
        for first, last, record in pieces.get(id(old), []):
            if last > first:
                infos.append({"mediaPoolItem": old.pool, "startFrame": int(first), "endFrame": int(last),
                              "mediaType": 1 if old.kind == "video" else 2, "trackIndex": old.track,
                              "recordFrame": int(record)})
                owners.append(old)
    added, refused = append(media_pool, infos)
    counts["refused"] = len(refused)
    at = {}
    for kind, track, _index, item in every_clip(timeline):
        at[(kind, track, int(item.GetStart()))] = item
    twins = {}
    if backup is not None:
        twins = {(kind, track, index): item for kind, track, index, item in every_clip(backup)}
    grade_groups: dict[int, list] = {}
    for n, (info, old) in enumerate(zip(infos, owners)):
        if n in refused:
            continue
        piece = at.get((old.kind, old.track, info["recordFrame"]))
        if piece is None:
            continue
        counts["added"] += 1
        for key, value in old.props.items():
            try:
                piece.SetProperty(key, value)
            except Exception:  # noqa: BLE001
                pass
        if old.colour:
            try:
                piece.SetClipColor(old.colour)
            except Exception:  # noqa: BLE001
                pass
        if old.kind == "video":
            grade_groups.setdefault(id(old), [old, []])[1].append(piece)
    for old, group in grade_groups.values():
        twin = twins.get((old.kind, old.track, old.index))
        done = False
        if twin is not None:
            try:
                done = bool(twin.CopyGrades(group))
            except Exception:  # noqa: BLE001
                done = False
        counts["grades" if done else "no_grade"] += len(group)
    return counts


def find_folder_of(media_pool, pool_item):
    """The Media Pool folder holding this clip (or timeline), or None."""
    try:
        wanted = pool_item.GetUniqueId()
    except Exception:  # noqa: BLE001
        wanted = None
    try:
        folders = [media_pool.GetRootFolder()]
    except Exception:  # noqa: BLE001
        return None
    seen = 0
    while folders and seen < 500:
        folder = folders.pop()
        seen += 1
        try:
            for clip in folder.GetClipList() or []:
                if clip is pool_item or (wanted and clip.GetUniqueId() == wanted):
                    return folder
            folders.extend(folder.GetSubFolderList() or [])
        except Exception:  # noqa: BLE001
            continue
    return None


def backup_folder(media_pool, name: str = "Tools backups"):
    """The Media Pool bin the backups go in, made the first time."""
    try:
        root = media_pool.GetRootFolder()
        for folder in root.GetSubFolderList() or []:
            if str(folder.GetName()) == name:
                return folder
        return media_pool.AddSubFolder(root, name)
    except Exception:  # noqa: BLE001
        return None


def move_to(media_pool, timeline, folder) -> bool:
    if folder is None:
        return False
    try:
        item = timeline.GetMediaPoolItem()
        return bool(item is not None and media_pool.MoveClips([item], folder))
    except Exception:  # noqa: BLE001
        return False
