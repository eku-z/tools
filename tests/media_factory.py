"""Test media made with FFmpeg, with known answers.

- talk.mp4: 20 s, 25 fps, a tone that stops at 3-5 s and 9-10.5 s.
- cam_a.mp4 / cam_b.mp4: one irregular "voice" heard by two cameras;
  camera B started 1.3 s later, so a time in B's file plus 1.3 s is the
  same moment in A's (and in voice.wav, the separate recorder).
- song.wav: 30 s at 120 BPM, a click on every beat and a low kick on the
  first beat of each bar.
- canon_side.mp4: a small video with a Canon MakerNote saying the camera
  was on its side (AutoRotate 3: turn 270 degrees clockwise).
- phone_portrait.mp4: stored 320x180 with a display matrix turning it a
  quarter, as phones write a clip filmed upright: shown 180x320.
- four_three.mp4: 240x180, a 4:3 picture.

Made once per test run in a scratch folder (tests/run_tests.py sets
TOOLS_TEST_MEDIA); all sound is synthetic.
"""

from __future__ import annotations

import os
import struct
import subprocess
import tempfile

from tools.core.media import find_ffmpeg

SILENCES = [(3.0, 5.0), (9.0, 10.5)]
CAMERA_B_LATE = 1.3
BPM = 120.0

# Irregular bursts of noise, like speech with pauses of different lengths.
VOICE = "gt(sin(2*PI*T*0.37)+sin(2*PI*T*0.91+1)+0.6*sin(2*PI*T*2.3),0.2)"


def folder() -> str:
    path = os.environ.get("TOOLS_TEST_MEDIA") or os.path.join(tempfile.gettempdir(), "tools_test_media")
    os.makedirs(path, exist_ok=True)
    return path


def _ffmpeg(*args: str) -> None:
    subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True,
                   stdin=subprocess.DEVNULL)


def _video(seconds: float, size: str = "320x180", rate: int = 25) -> list[str]:
    return ["-f", "lavfi", "-i", "testsrc2=size={}:rate={}:duration={}".format(size, rate, seconds)]


def talk() -> str:
    path = os.path.join(folder(), "talk.mp4")
    if not os.path.isfile(path):
        quiet = "+".join("between(t,{},{})".format(a, b) for a, b in SILENCES)
        _ffmpeg(*_video(20), "-f", "lavfi", "-i", "sine=f=330:sample_rate=48000:duration=20",
                "-af", "volume='if({},0,0.5)':eval=frame".format(quiet),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path)
    return path


def _voice_source(seconds: float, late: float) -> list[str]:
    expr = VOICE.replace("T", "(t+{})".format(late))
    return ["-f", "lavfi", "-i", "anoisesrc=color=pink:sample_rate=48000:seed=7:duration={}".format(seconds + late),
            "-af", "atrim=start={},asetpts=PTS-STARTPTS,volume='0.6*{}':eval=frame".format(late, expr)]


def cameras() -> tuple[str, str, str]:
    a = os.path.join(folder(), "cam_a.mp4")
    b = os.path.join(folder(), "cam_b.mp4")
    voice = os.path.join(folder(), "voice.wav")
    if not os.path.isfile(a):
        _ffmpeg(*_video(60), *_voice_source(60, 0.0), "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", a)
    if not os.path.isfile(b):
        _ffmpeg(*_video(60), *_voice_source(60, CAMERA_B_LATE), "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", b)
    if not os.path.isfile(voice):
        _ffmpeg(*_voice_source(60, 0.0), voice)
    return a, b, voice


def song() -> str:
    path = os.path.join(folder(), "song.wav")
    if not os.path.isfile(path):
        beat = 60.0 / BPM
        click = "0.5*sin(2*PI*1500*t)*exp(-60*mod(t,{b}))".format(b=beat)
        kick = "0.9*sin(2*PI*60*t)*exp(-12*mod(t,{bar}))".format(bar=4 * beat)
        _ffmpeg("-f", "lavfi", "-i", "aevalsrc='{}+{}':s=44100:d=30".format(click, kick), path)
    return path


def canon_makernote(auto_rotate: int = 3) -> bytes:
    """A minimal little-endian Exif block: IFD0 -> Exif IFD -> MakerNote ->
    ShotInfo (tag 4, 34 shorts) with entry 27 = auto_rotate."""
    ifd0, exif, maker, shot = 8, 26, 44, 62
    out = bytearray(b"II*\x00" + struct.pack("<I", ifd0))
    out += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, exif) + struct.pack("<I", 0)
    out += struct.pack("<H", 1) + struct.pack("<HHII", 0x927C, 7, 1, maker) + struct.pack("<I", 0)
    out += struct.pack("<H", 1) + struct.pack("<HHII", 0x0004, 3, 34, shot) + struct.pack("<I", 0)
    values = [0] * 34
    values[27] = auto_rotate
    out += struct.pack("<34h", *values)
    return b"Exif\x00\x00" + bytes(out)


def phone_portrait() -> str:
    path = os.path.join(folder(), "phone_portrait.mp4")
    if not os.path.isfile(path):
        plain = os.path.join(folder(), "phone_plain.mp4")
        _ffmpeg(*_video(2, "320x180"), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", plain)
        _ffmpeg("-display_rotation", "90", "-i", plain, "-c", "copy", path)
    return path


def four_three() -> str:
    path = os.path.join(folder(), "four_three.mp4")
    if not os.path.isfile(path):
        _ffmpeg(*_video(2, "240x180"), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path)
    return path


def canon_side() -> str:
    path = os.path.join(folder(), "canon_side.mp4")
    if not os.path.isfile(path):
        _ffmpeg(*_video(4, "320x180"), "-f", "lavfi", "-i", "sine=f=220:duration=4", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path)
        with open(path, "ab") as fh:  # MP4 readers ignore what follows the last box
            fh.write(b"Canon EOS R6 Mark III\x00" + canon_makernote(3))
    return path
