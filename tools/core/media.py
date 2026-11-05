"""Reading sound, with PyAV and with the FFmpeg program.

Ported from ZCuts (engine/01_setup.py find_ffmpeg and helper_flags,
engine/08_sound.py decode_audio, engine/04_silence.py extract_all_audio,
engine/10_ab_camera.py audio_stream_count; build 259).

ZCuts wrote each clip's whole sound to a WAV file and read it back. Tools
keeps only the loudness of every 10 ms (``read_levels``): an hour of sound
is 360 000 numbers instead of a 115 MB file, and only the part of the
file the timeline uses is read.

Two ways to decode, measured on this PC (Nov 2026, FFmpeg 9.0.2 "full"
from WinGet, PyAV 19.0.1), the answers identical to 0.0 dB:
- the FFmpeg program costs 0.70 s just to start (a 227 MB program), then
  decodes 20 minutes of AAC in 0.5 s; four side by side take 1.46 s;
- PyAV starts in 0.06 s but decodes 20 minutes in 1.09 s, and holds
  Python's lock while it does, so four threads took 14 s.
So short reads (up to ``PYAV_LONGEST``) go through PyAV one after another
on one helper thread, and long ones to FFmpeg programs side by side.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from tools.core import Stopped

RATE = 16000  # ZCuts' analysis rate (SAMPLE_RATE)
HOP = 160  # 10 ms at 16 kHz (ZCuts SPEECH_HOP)
HOPS_PER_SECOND = RATE // HOP
LIMIT_SECONDS = 1800  # ZCuts FFMPEG_LIMIT_SECONDS: a stalled drive never hangs a job
READERS = 4  # FFmpeg processes side by side (ZCuts: min(4, cores))
PYAV_LONGEST = 300.0  # seconds: shorter reads use PyAV (see above)


class MediaError(RuntimeError):
    pass


# ---- the two decoders ------------------------------------------------------------------

_found: list[str] = []
_av: list = []


def find_ffmpeg() -> Optional[str]:
    """TOOLS_FFMPEG, then Tools/ffmpeg/, then PATH (and Homebrew on a Mac,
    which apps opened from the Finder do not have on their PATH)."""
    if _found:
        return _found[0]
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    candidates = [os.environ.get("TOOLS_FFMPEG", ""),
                  str(Path(__file__).resolve().parents[2] / "ffmpeg" / name),
                  shutil.which("ffmpeg") or "",
                  "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    for path in candidates:
        if path and os.path.isfile(path):
            _found.append(path)
            return path
    return None


def pyav():
    """The av module, or None (not installed, or TOOLS_NO_PYAV is set)."""
    if not _av:
        module = None
        if not os.environ.get("TOOLS_NO_PYAV"):
            try:
                import av as module
            except ImportError:
                module = None
        _av.append(module)
    return _av[0]


def warm_up() -> None:
    """Load PyAV ahead of the first job (0.8 s the first time after a restart)."""
    pyav()


def _ffmpeg() -> str:
    path = find_ffmpeg()
    if path is None:
        raise MediaError("FFmpeg was not found. Install it (winget install ffmpeg, or brew "
                         "install ffmpeg on a Mac) or put ffmpeg.exe in the Tools\\ffmpeg folder.")
    return path


def helper_flags() -> int:
    """No console window, and below normal priority so Resolve and the
    window do not stutter while FFmpeg works (ZCuts helper_flags)."""
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def no_audio_stream(message: str) -> bool:
    """FFmpeg failed only because the file has no sound (ZCuts no_audio_stream)."""
    text = (message or "").lower()
    return "matches no streams" in text or "does not contain any stream" in text or "no audio" in text


# ---- what is in a file -------------------------------------------------------------

@dataclass
class Probe:
    audio_streams: int = 0
    width: int = 0
    height: int = 0
    duration: float = 0.0


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_SIZE = re.compile(r"Stream #\d+:\d+.*?: Video:.*?(\d{2,5})x(\d{2,5})(?:.*?\[SAR (\d+):(\d+))?")
_ROTATION = re.compile(r"rotation of (-?\d+(?:\.\d+)?) degrees")
_probes: dict = {}
_shown: dict = {}


def _key(path: str):
    st = os.stat(path)
    return os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime


def _shown_av(av, path: str):
    with av.open(path) as container:
        if not container.streams.video:
            return None
        s = container.streams.video[0]
        width, height = float(s.codec_context.width or 0), float(s.codec_context.height or 0)
        sar = s.codec_context.sample_aspect_ratio or s.sample_aspect_ratio
        rotation = 0
        try:  # phones write the turn into the file; PyAV gives it on a frame
            rotation = int(getattr(next(container.decode(s)), "rotation", 0) or 0)
        except (StopIteration, av.error.FFmpegError):
            pass
    return width * float(sar) if sar else width, height, rotation


def _shown_cli(path: str):
    done = subprocess.run([_ffmpeg(), "-hide_banner", "-nostdin", "-i", path], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60, creationflags=helper_flags())
    text = done.stderr.decode("utf-8", "replace")
    m = _VIDEO_SIZE.search(text)
    if not m:
        return None
    width, height = float(m.group(1)), float(m.group(2))
    if m.group(3) and float(m.group(4)) > 0:
        width *= float(m.group(3)) / float(m.group(4))
    r = _ROTATION.search(text)
    return width, height, int(round(float(r.group(1)))) if r else 0


def shown_size(path: str) -> Optional[tuple[float, float]]:
    """A file's picture size as it is shown: wider for anamorphic pixels,
    and the other way round when the file says to show it turned a quarter
    (phones), which Resolve does by itself. None for no picture. Read from
    the file, not Resolve's "Resolution" (that may be the stored size)."""
    try:
        key = _key(path)
    except OSError:
        return None
    if key not in _shown:
        av = pyav()
        try:
            found = _shown_av(av, path) if av is not None else _shown_cli(path)
        except Exception:  # noqa: BLE001 - unreadable: Resolve's own size is used instead
            found = None
        if found is not None and found[0] > 0 and found[1] > 0:
            width, height, rotation = found
            _shown[key] = (height, width) if rotation % 180 == 90 else (width, height)
        else:
            _shown[key] = None
    return _shown[key]


def _probe_av(av, path: str) -> Probe:
    with av.open(path) as container:
        found = Probe(audio_streams=len(container.streams.audio),
                      duration=(container.duration or 0) / 1_000_000.0)
        if container.streams.video:
            context = container.streams.video[0].codec_context
            found.width, found.height = int(context.width or 0), int(context.height or 0)
        return found


def _probe_cli(path: str) -> Probe:
    done = subprocess.run([_ffmpeg(), "-hide_banner", "-nostdin", "-i", path], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60, creationflags=helper_flags())
    text = done.stderr.decode("utf-8", "replace")
    found = Probe(audio_streams=len(re.findall(r"Stream #\d+:\d+.*?: Audio", text)))
    m = _VIDEO_SIZE.search(text)
    if m:
        found.width, found.height = int(m.group(1)), int(m.group(2))  # as stored
    m = _DURATION.search(text)
    if m:
        found.duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return found


def probe(path: str) -> Probe:
    """A file's sound streams, picture size and length; remembered per
    file, size and date."""
    try:
        key = _key(path)
    except OSError:
        return Probe()
    if key not in _probes:
        av = pyav()
        try:
            _probes[key] = _probe_av(av, path) if av is not None else _probe_cli(path)
        except Exception:  # noqa: BLE001 - an unreadable file has nothing in it
            return Probe()
    return _probes[key]


# ---- decoding into blocks of int16 ----------------------------------------------------------

class _Blocks:
    """Hands decoded int16 samples on in whole 10 ms hops, from ``skip``
    samples in, at most ``wanted`` of them. ``full`` once enough came."""

    def __init__(self, take: Callable[[np.ndarray], None], wanted: Optional[int]):
        self.take, self.wanted = take, wanted
        self.skip = 0
        self.given = 0
        self.left = np.zeros(0, dtype=np.int16)

    @property
    def full(self) -> bool:
        return self.wanted is not None and self.given >= self.wanted

    def add(self, samples: np.ndarray) -> None:
        if self.skip:
            cut = min(self.skip, samples.size)
            samples, self.skip = samples[cut:], self.skip - cut
        if self.wanted is not None:
            samples = samples[:max(0, self.wanted - self.given)]
        if not samples.size:
            return
        if self.left.size:
            samples = np.concatenate((self.left, samples))
        usable = samples.size // HOP * HOP
        self.left = samples[usable:]
        if usable:
            self.take(samples[:usable])
            self.given += usable


def _check(stop: Optional[threading.Event], began: float) -> None:
    if stop is not None and stop.is_set():
        raise Stopped()
    if time.monotonic() - began > LIMIT_SECONDS:
        raise MediaError("Reading the sound took longer than {} s (is the drive still "
                         "connected?)".format(LIMIT_SECONDS))


def _decode_av(av, path: str, start: float, length: Optional[float], stream: int, rate: int,
               take: Callable[[np.ndarray], None], stop: Optional[threading.Event]) -> Optional[float]:
    """Decode with PyAV. Returns the file time the first sample handed on
    belongs to, or None when the file has no such sound stream."""
    began = time.monotonic()
    try:
        container = av.open(path)
    except av.error.FFmpegError as error:
        raise MediaError("Could not open {}: {}".format(os.path.basename(path), error)) from None
    with container:
        if stream >= len(container.streams.audio):
            return None
        s = container.streams.audio[stream]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        if start > 0.5:
            # Half a second early: a seek can land on the packet after the
            # one asked for, and AAC needs a packet before to decode cleanly.
            try:
                container.seek(int((start - 0.5) / s.time_base), stream=s)
            except av.error.FFmpegError:
                container.seek(0)
        blocks = _Blocks(take, None if length is None else int(round(length * rate)))
        first: Optional[float] = None
        try:
            for frame in container.decode(s):
                _check(stop, began)
                if first is None:
                    here = float(frame.pts * frame.time_base) if frame.pts is not None else 0.0
                    blocks.skip = max(0, int(round((start - here) * rate)))
                    first = here + blocks.skip / rate
                for out in resampler.resample(frame):
                    blocks.add(out.to_ndarray().reshape(-1))
                if blocks.full:
                    break
            else:
                for out in resampler.resample(None):
                    blocks.add(out.to_ndarray().reshape(-1))
        except av.error.FFmpegError as error:
            raise MediaError("Could not decode the sound of {}: {}".format(os.path.basename(path), error)) from None
        return start if first is None else first


def _decode_cli(path: str, start: float, length: Optional[float], stream: int, rate: int,
                take: Callable[[np.ndarray], None], stop: Optional[threading.Event]) -> Optional[float]:
    """Decode with the FFmpeg program through a pipe."""
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", "{:.3f}".format(start)]
    if length is not None:
        cmd += ["-t", "{:.3f}".format(max(0.01, length))]
    cmd += ["-i", path, "-vn", "-sn", "-dn", "-map", "0:a:{}".format(stream),
            "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=helper_flags())
    errors: list[bytes] = []
    drain = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
    drain.start()
    began = time.monotonic()
    blocks = _Blocks(take, None)
    try:
        while True:
            _check(stop, began)
            data = process.stdout.read(HOP * 2 * 3000)
            if not data:
                break
            blocks.add(np.frombuffer(data[:len(data) // 2 * 2], dtype="<i2"))
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        drain.join(timeout=5)
    text = b"".join(errors).decode("utf-8", "replace").strip()
    if process.returncode:
        if no_audio_stream(text):
            return None
        raise MediaError("FFmpeg could not read the sound of {}: {}".format(os.path.basename(path), text[-300:]))
    return max(0.0, start)


def is_short(path: str, start: float, length: Optional[float]) -> bool:
    """Is this read short enough for PyAV (and is PyAV here)? Without the
    FFmpeg program (a Mac often has none) PyAV reads everything."""
    if pyav() is None:
        return False
    if find_ffmpeg() is None:
        return True
    if length is None:  # to the end of the file
        duration = probe(path).duration
        length = duration - start if duration > 0 else None
    return length is not None and length <= PYAV_LONGEST


def _decode(path, start, length, stream, rate, take, stop, engine: str = "auto") -> Optional[float]:
    if engine == "pyav" or (engine == "auto" and is_short(path, start, length)):
        av = pyav()
        if av is not None:
            return _decode_av(av, path, start, length, stream, rate, take, stop)
    return _decode_cli(path, start, length, stream, rate, take, stop)


# ---- what the jobs read -------------------------------------------------------------

@dataclass
class Levels:
    """Mean square loudness of every 10 ms of one sound stream, from
    ``start`` seconds into the file (sample values -1..1)."""
    start: float
    power: np.ndarray  # float64, one per hop

    @property
    def end(self) -> float:
        return self.start + self.power.size / HOPS_PER_SECOND

    def window(self, start: float, length: float) -> np.ndarray:
        a = max(0, int(round((start - self.start) * HOPS_PER_SECOND)))
        b = min(self.power.size, int(round((start + length - self.start) * HOPS_PER_SECOND)))
        return self.power[a:b] if b > a else self.power[:0]


def read_levels(path: str, start: float = 0.0, length: Optional[float] = None, stream: int = 0,
                stop: Optional[threading.Event] = None, engine: str = "auto") -> Optional[Levels]:
    """The loudness of every 10 ms of part of a file's sound, read at 16 kHz
    mono. None when the file has no such sound stream."""
    parts: list[np.ndarray] = []

    def take(samples: np.ndarray) -> None:
        x = samples.astype(np.float64) / 32768.0
        parts.append(np.mean((x * x).reshape(-1, HOP), axis=1))

    first = _decode(path, start, length, stream, RATE, take, stop, engine)
    if first is None or not parts:
        return None
    return Levels(first, np.concatenate(parts))


def read_samples(path: str, start: float = 0.0, length: Optional[float] = None, stream: int = 0,
                 stop: Optional[threading.Event] = None, engine: str = "auto") -> Optional[np.ndarray]:
    """Mono float32 samples at 16 kHz of part of a file's sound, or None."""
    parts: list[np.ndarray] = []
    first = _decode(path, start, length, stream, RATE, lambda s: parts.append(s.astype(np.float32) / 32768.0),
                    stop, engine)
    if first is None or not parts:
        return None
    return np.concatenate(parts)


def read_many(jobs: list[tuple], stop: Optional[threading.Event] = None,
              progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """``read_levels`` for several (key, path, start, length, stream).
    Short reads go through PyAV one after another, long ones to FFmpeg
    programs side by side, both at once. Returns {key: Levels, None or the
    MediaError}; a Stop ends them all."""
    out: dict = {}
    if not jobs:
        return out
    done = [0]
    lock = threading.Lock()

    def one(job, engine):
        key, path, start, length, stream = job
        try:
            result = read_levels(path, start, length, stream, stop, engine)
        except MediaError as error:
            result = error
        with lock:
            out[key] = result
            done[0] += 1
            if progress is not None:
                progress(done[0], len(jobs))

    def short_lane(batch):
        for job in batch:
            one(job, "pyav")

    short = [j for j in jobs if is_short(j[1], j[2], j[3])]
    long = [j for j in jobs if j not in short]
    workers = max(1, min(READERS, os.cpu_count() or 2, len(long))) + bool(short)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(short_lane, short)] if short else []
        futures += [pool.submit(one, job, "cli") for job in long]
        for future in futures:
            future.result()  # raises Stopped from any of them
    return out
