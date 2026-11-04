"""Which way up a clip was filmed, without a model, ported from Edits
(edits/media/camera.py canon_auto_rotate, v0.14.2).

ZCuts found sideways clips with its vision model (SigLIP 2 and a trained
check on six frames). Tools reads what the camera itself wrote: Canon
writes no rotation into the MP4's track matrix (so Resolve shows the clip
sideways), but its MakerNote does: ShotInfo entry 27, "AutoRotate" in
ExifTool's Canon tables (0 none, 1 = turn 90 degrees clockwise, 2 = 180,
3 = 270), from the camera's orientation sensor. Measured by Edits on the
owner's R6 Mark III files: 3 on a clip filmed with the camera on its side,
0 on two filmed level.

Phones write a display matrix instead, which Resolve applies by itself,
so those clips are left alone. Only the first and last 256 KB of a file
are read.
"""

from __future__ import annotations

import os
import re
import struct

_READ = 256 * 1024
_AUTO_ROTATE = {0: 0, 1: 90, 2: 180, 3: 270}
_turns: dict = {}


def _ends(path: str) -> bytes:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(_READ)
            if size > 2 * _READ:
                fh.seek(size - _READ)
            return head + fh.read()
    except OSError:
        return b""


def canon_auto_rotate(data: bytes) -> int:
    """Clockwise degrees to show a Canon clip upright, from the Exif in its
    MP4 (MakerNote ShotInfo[27], AutoRotate); 0 when absent or unreadable."""
    at = data.find(b"Exif\x00\x00")
    if at < 0:
        return 0
    tiff = data[at + 6:at + 6 + 1_000_000]
    try:
        end = "<" if tiff[:2] == b"II" else ">"

        def entries(off: int) -> dict:
            (n,) = struct.unpack_from(end + "H", tiff, off)
            out = {}
            for i in range(min(n, 512)):
                tag, typ, count = struct.unpack_from(end + "HHI", tiff, off + 2 + 12 * i)
                out[tag] = (typ, count, off + 2 + 12 * i + 8)
            return out

        def pointer(entry) -> int:
            return struct.unpack_from(end + "I", tiff, entry[2])[0]

        ifd0 = entries(struct.unpack_from(end + "I", tiff, 4)[0])
        exif = entries(pointer(ifd0[0x8769]))
        maker = entries(pointer(exif[0x927C]))
        typ, count, _ = shot = maker[0x0004]
        if typ != 3 or count <= 27:
            return 0
        (value,) = struct.unpack_from(end + "h", tiff, pointer(shot) + 2 * 27)
        return _AUTO_ROTATE.get(value, 0)
    except (KeyError, struct.error, IndexError):
        return 0


def camera_name(data: bytes) -> str:
    m = re.search(rb"Canon EOS[ -~]{0,30}", data)
    return re.sub(r"\s+", " ", m.group(0).decode("latin-1")).strip() if m else ""


def file_turn(path: str) -> int:
    """Clockwise degrees that show this file upright in Resolve (0: as it
    is, or unknown); remembered per file, size and date."""
    try:
        st = os.stat(path)
    except OSError:
        return 0
    key = (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime)
    if key not in _turns:
        data = _ends(path)
        _turns[key] = canon_auto_rotate(data) if camera_name(data) else 0
    return _turns[key]
