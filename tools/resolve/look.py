"""LUTs, sideways clips and the 9:16 framing, ported from ZCuts
(engine/13_colour.py lut_folders, find_luts, apply_lut; engine/15_picture.py
fix_picture's quarter-turn and fill branches; engine/10_ab_camera.py
vertical_of_open; build 259).
"""

from __future__ import annotations

import os

from tools.core import camera
from tools.core.framing import placement
from tools.resolve import timeline as tl

LUT_EXTENSIONS = (".cube", ".3dl", ".dat", ".olut", ".ilut", ".davlut", ".mga", ".m3d", ".clf")
NO_LUT = "None"


def lut_folders() -> list[str]:
    """Where Resolve keeps its LUTs, built-in and your own."""
    folders = []
    if os.environ.get("TOOLS_LUT_DIR"):
        folders.append(os.environ["TOOLS_LUT_DIR"])
    for base in (os.environ.get("PROGRAMDATA"), os.environ.get("APPDATA")):
        if base:
            folders.append(os.path.join(base, "Blackmagic Design", "DaVinci Resolve", "Support", "LUT"))
    folders += ["/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT",
                os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                             "Blackmagic Design", "DaVinci Resolve", "LUT"),
                "/opt/resolve/LUT"]
    return [folder for folder in folders if os.path.isdir(folder)]


def find_luts() -> dict[str, str]:
    """{name shown in the list: file}, your own (loose in the folder) first.
    Resolve's scripting cannot list its LUTs, so its folders are read."""
    loose, grouped = {}, {}
    for root in lut_folders():
        for folder, _subfolders, files in os.walk(root):
            for name in files:
                if not name.lower().endswith(LUT_EXTENSIONS):
                    continue
                inside = os.path.relpath(folder, root)
                title = os.path.splitext(name)[0]
                if inside in (".", ""):
                    loose.setdefault(title, os.path.join(folder, name))
                else:
                    grouped.setdefault("{} / {}".format(inside.replace(os.sep, " / "), title),
                                       os.path.join(folder, name))
    out = {}
    for group in (loose, grouped):
        for shown in sorted(group, key=str.lower):
            out.setdefault(shown, group[shown])
    return out


def apply_lut(project, pieces, path: str) -> int:
    """Put the LUT on the first node of every piece. How many took it."""
    try:
        project.RefreshLUTList()
    except Exception:  # noqa: BLE001
        pass
    done = 0
    for piece in pieces:
        applied = False
        try:
            applied = bool(piece.SetLUT(1, path))
        except Exception:  # noqa: BLE001
            applied = False
        if not applied:  # newer Resolve versions set LUTs through the clip's node graph
            try:
                graph = piece.GetNodeGraph()
                applied = bool(graph and graph.SetLUT(1, path))
            except Exception:  # noqa: BLE001
                applied = False
        done += applied
    return done


def video_pieces(timeline) -> list:
    """Every video piece with a file behind it, on every track."""
    return [p for p in tl.items(timeline, "video") if tl.file_of(p)]


def frame_pieces(project, timeline, pieces, upright: bool, fill: bool, reset: bool = False) -> dict:
    """Turn sideways clips upright and, with ``fill``, zoom every clip to
    cover the frame (the 9:16 copy). ``reset`` first puts each piece back to
    a plain picture, so the framing for a new shape starts clean. Returns
    counts: turned, filled, refused."""
    frame = tl.frame_size(timeline)
    mode = tl.mismatch_mode(timeline, project)
    counts = {"turned": 0, "filled": 0, "refused": 0}
    for piece in pieces:
        path = tl.file_of(piece)
        try:
            size = tl.parse_size(tl.prop(piece.GetMediaPoolItem(), "Resolution")) or frame
        except Exception:  # noqa: BLE001
            size = frame
        turn = camera.file_turn(path) if (upright and path) else 0
        if not turn and not fill and not reset:
            continue
        t = placement(size, frame, turn, fill, mode)
        try:
            piece.SetProperty("ZoomGang", True)
            if reset:
                piece.SetProperty("Pan", 0.0)
                piece.SetProperty("Tilt", 0.0)
            piece.SetProperty("ZoomX", t.zoom)
            piece.SetProperty("ZoomY", t.zoom)
            if turn or reset:
                piece.SetProperty("RotationAngle", t.rotation)
        except Exception:  # noqa: BLE001
            counts["refused"] += 1
            continue
        counts["turned"] += bool(turn)
        counts["filled"] += bool(fill and t.zoom > 1.004 and not turn)
    return counts


def finish(project, timeline, options, log) -> None:
    """The finishing touches every new timeline gets: sideways clips upright
    and the LUT on every clip."""
    pieces = video_pieces(timeline)
    if options.get("upright", True):
        counts = frame_pieces(project, timeline, pieces, upright=True, fill=False)
        if counts["turned"]:
            log("Turned upright: {} clip(s) filmed with the camera on its side.".format(counts["turned"]))
    lut = options.get("lut_path") or ""
    if lut:
        if not os.path.isfile(lut):
            log("The LUT was not found, so none was put on: {}".format(lut))
            return
        done = apply_lut(project, pieces, lut)
        name = os.path.splitext(os.path.basename(lut))[0]
        if done == len(pieces):
            log("LUT '{}' put on all {} clip(s).".format(name, done))
        elif done:
            log("LUT '{}' put on {} of {} clip(s); Resolve refused the rest.".format(name, done, len(pieces)))
        else:
            log("Resolve would not take the LUT '{}' from a script. Put it on by hand: on the Color "
                "page select all clips, then right-click the LUT in the LUT browser.".format(name))
