"""A stand-in for DaVinci Resolve's scripting API, enough for Tools' jobs.

It follows what ZCuts learned from the real one (HANDOFF.md, Resolve
rules): AppendToTimeline takes source frames with endFrame exclusive and
recordFrame in timeline frames from the timeline's start (01:00:00:00); a
list with one piece out of range is refused as a whole; the open timeline
cannot be deleted. Only real runs prove Resolve's behaviour.
"""

from __future__ import annotations

import copy
import itertools
import os

_ids = itertools.count(1)


class PoolItem:
    def __init__(self, path, fps=25.0, frames=0, resolution="1920x1080", video=True, audio=True, name=None):
        self.path, self.fps, self.frames = path, fps, frames
        self.resolution, self.video, self.audio = resolution, video, audio
        self.name = name or os.path.basename(path)
        self.id = next(_ids)

    def GetClipProperty(self, name):
        return {"File Path": self.path, "FPS": "{:g}".format(self.fps) if self.video else "",
                "Resolution": self.resolution if self.video else "", "Type": "Video" if self.video else "Audio"
                }.get(name, "")

    def GetName(self):
        return self.name


class Graph:
    def __init__(self, item):
        self.item = item

    def SetLUT(self, node, path):
        self.item.lut = path
        return True


class Item:
    def __init__(self, timeline, kind, pool, start, end, source_start, source_end):
        self.timeline, self.kind, self.pool = timeline, kind, pool
        self.start, self.end = start, end
        self.source_start, self.source_end = source_start, source_end
        self.props = {"ZoomX": 1.0, "ZoomY": 1.0, "RotationAngle": 0.0, "Pan": 0.0, "Tilt": 0.0, "ZoomGang": True,
                      "Speed": "100"}
        self.lut = None
        self.id = next(_ids)

    def GetName(self):
        return self.pool.name if self.pool else "Title"

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetSourceStartFrame(self):
        return self.source_start

    def GetSourceEndFrame(self):
        return self.source_end

    def GetLeftOffset(self):
        return self.source_start

    def GetMediaPoolItem(self):
        return self.pool

    def GetProperty(self, key):
        return self.props.get(key)

    def SetProperty(self, key, value):
        self.props[key] = value
        return True

    def SetLUT(self, node, path):
        if self.timeline.resolve.lut_by_graph:
            return False
        self.lut = path
        return True

    def GetNodeGraph(self):
        return Graph(self)


class Timeline:
    def __init__(self, project, name, settings=None):
        self.project, self.resolve = project, project.resolve
        self.name = name
        self.settings = dict(settings or project.defaults)
        self.tracks = {"video": [[]], "audio": [[]]}
        self.markers = []
        self.pool_item = PoolItem("", name=name, video=False, audio=False)
        self.id = next(_ids)

    @property
    def fps(self):
        return float(self.settings["timelineFrameRate"])

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return str(self.id)

    def GetSetting(self, name):
        return self.settings.get(name, "")

    def SetSetting(self, name, value):
        if name == "timelineFrameRate" and any(self.tracks["video"]) and self.resolve.lock_rate:
            return False
        self.settings[name] = str(value)
        return True

    def GetStartFrame(self):
        # 01:00:00:00 counted at the nominal rate: 86400 on a 23.976 timeline
        # (read from the owner's Resolve 21, 4 Nov 2026)
        return 3600 * int(round(self.fps))

    def GetTrackCount(self, kind):
        return len(self.tracks[kind])

    def GetItemListInTrack(self, kind, index):
        if not 1 <= index <= len(self.tracks[kind]):
            return None
        return sorted(self.tracks[kind][index - 1], key=lambda i: i.start)

    def AddTrack(self, kind, sub=None):
        self.tracks[kind].append([])
        return True

    def AddMarker(self, frame, colour, name, note, duration, data=""):
        self.markers.append((frame, colour, name, duration))
        return True

    def GetMediaPoolItem(self):
        return self.pool_item

    def DuplicateTimeline(self, name):
        copy_ = Timeline(self.project, name, self.settings)
        for kind in ("video", "audio"):
            copy_.tracks[kind] = []
            for track in self.tracks[kind]:
                new_track = []
                for item in track:
                    twin = copy.copy(item)
                    twin.props = dict(item.props)
                    twin.timeline = copy_
                    twin.id = next(_ids)
                    new_track.append(twin)
                copy_.tracks[kind].append(new_track)
        self.project.timelines.append(copy_)
        return copy_

    def end_of(self, kind, track):
        items = self.tracks[kind][track - 1] if track <= len(self.tracks[kind]) else []
        return max((i.end for i in items), default=self.GetStartFrame())

    # helpers for the tests -------------------------------------------------------
    def add_clip(self, pool, source_start, source_end, track=1, start=None, audio_track=None, video=True):
        """Put a clip on the timeline as a person would (picture and its sound)."""
        record = self.end_of("video", track) if start is None else start
        length = int(round((source_end - source_start) * self.fps / pool.fps)) if pool.fps else source_end - source_start
        made = []
        if video and pool.video:
            while len(self.tracks["video"]) < track:
                self.tracks["video"].append([])
            made.append(Item(self, "video", pool, record, record + length, source_start, source_end))
            self.tracks["video"][track - 1].append(made[-1])
        if pool.audio:
            a = audio_track or track
            while len(self.tracks["audio"]) < a:
                self.tracks["audio"].append([])
            made.append(Item(self, "audio", pool, record, record + length, source_start, source_end))
            self.tracks["audio"][a - 1].append(made[-1])
        return made


class Folder:
    def __init__(self, media_pool):
        self.media_pool = media_pool

    def GetClipList(self):
        return list(self.media_pool.clips)

    def GetSubFolderList(self):
        return []


class MediaPool:
    def __init__(self, project):
        self.project, self.resolve = project, project.resolve
        self.clips: list[PoolItem] = []

    def GetRootFolder(self):
        return Folder(self)

    def ImportMedia(self, paths):
        out = []
        for path in paths:
            if os.path.isfile(path):
                out.append(PoolItem(path, fps=25.0, frames=10 ** 7, video=False))
                self.clips.append(out[-1])
        return out

    def CreateEmptyTimeline(self, name):
        if any(t.name == name for t in self.project.timelines):
            return None
        timeline = Timeline(self.project, name)
        self.project.timelines.append(timeline)
        return timeline

    def AppendToTimeline(self, infos):
        timeline = self.project.current
        if timeline is None:
            return []
        self.resolve.append_calls += 1
        for info in infos:  # one bad piece and the whole list is refused
            pool = info["mediaPoolItem"]
            if info["startFrame"] < 0 or info["endFrame"] <= info["startFrame"] or info["endFrame"] > pool.frames:
                return []
            if pool in self.resolve.refuse:
                return []
        added = []
        for info in infos:
            pool = info["mediaPoolItem"]
            track = int(info.get("trackIndex", 1))
            kinds = {1: ["video"], 2: ["audio"]}.get(info.get("mediaType"), ["video", "audio"])
            kinds = [k for k in kinds if (pool.video if k == "video" else pool.audio)]
            fps = pool.fps if pool.video else timeline.fps
            length = int(round((info["endFrame"] - info["startFrame"]) * timeline.fps / fps))
            record = info.get("recordFrame")
            if record is None:
                record = max(timeline.end_of(k, track) for k in kinds) if kinds else timeline.GetStartFrame()
            for kind in kinds:
                while len(timeline.tracks[kind]) < track:
                    timeline.tracks[kind].append([])
                item = Item(timeline, kind, pool, int(record), int(record) + length,
                            info["startFrame"], info["endFrame"])
                timeline.tracks[kind][track - 1].append(item)
                added.append(item)
        return added

    def DeleteTimelines(self, timelines):
        if any(t is self.project.current for t in timelines):
            return False
        for t in timelines:
            if t in self.project.timelines:
                self.project.timelines.remove(t)
        return True

    def DeleteClips(self, clips):
        return False


class Project:
    def __init__(self, resolve, name="Test project"):
        self.resolve, self.name = resolve, name
        self.defaults = {"timelineFrameRate": "24", "timelineResolutionWidth": "1920",
                         "timelineResolutionHeight": "1080", "timelineDropFrameTimecode": "0"}
        self.timelines: list[Timeline] = []
        self.current = None
        self.media_pool = MediaPool(self)

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return "project-" + self.name

    def GetMediaPool(self):
        return self.media_pool

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        if timeline not in self.timelines:
            return False
        self.current = timeline
        return True

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1] if 1 <= index <= len(self.timelines) else None

    def GetSetting(self, name):
        return self.defaults.get(name, "")

    def RefreshLUTList(self):
        return True

    # helpers for the tests -------------------------------------------------------
    def new_timeline(self, name="Edit", fps=25, size=(1920, 1080)):
        t = Timeline(self, name, {"timelineFrameRate": "{:g}".format(fps), "timelineResolutionWidth": str(size[0]),
                                  "timelineResolutionHeight": str(size[1]), "timelineDropFrameTimecode": "0"})
        self.timelines.append(t)
        self.current = t
        return t

    def pool_item(self, path, fps=25.0, seconds=None, resolution="1920x1080", video=True, audio=True):
        frames = int(round((seconds if seconds is not None else 3600) * (fps if video else 25.0)))
        item = PoolItem(path, fps, frames, resolution, video, audio)
        self.media_pool.clips.append(item)
        return item


class ProjectManager:
    def __init__(self, resolve):
        self.resolve = resolve

    def GetCurrentProject(self):
        if self.resolve.closed:
            raise RuntimeError("Resolve is gone")
        return self.resolve.project


class Resolve:
    def __init__(self):
        self.project = Project(self)
        self.manager = ProjectManager(self)
        self.page = "media"
        self.closed = False
        self.refuse: set = set()  # pool items whose pieces are refused
        self.lut_by_graph = False  # newer Resolve: SetLUT only through the node graph
        self.lock_rate = False
        self.append_calls = 0

    def GetProjectManager(self):
        return self.manager

    def OpenPage(self, page):
        self.page = page
        return True
