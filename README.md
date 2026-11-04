# Tools

Fast, light helpers for DaVinci Resolve Studio, taken from ZCuts. A small
Qt window beside Resolve: pick a job, press its button, and a new timeline
appears in your project. **Your own timelines are never changed**, and
Undo deletes what the last job made.

| Tab | What it makes |
|---|---|
| Silences | `<name> - Cut`: one video track with its silences cut out, by loudness (Natural / Tight / Loose, or your own threshold, length and padding). |
| A/B camera | `<name> - A-B Cut`: two camera files lined up by their sound, switching every N seconds, each switch moved to a pause. A separate sound recording on an audio track becomes the sound; otherwise the clearer camera's own. |
| Montage | `<name> - Montage`: one video track's clips cut to a song's beats, the song laid under them. The song is a file you pick or the music on the timeline. |
| 9:16 | `<name> 9x16`: a 1080x1920 copy of the open timeline, every clip zoomed to fill it. |
| Look | `<name> - Look`: a copy of the open timeline with a LUT on every clip and sideways clips turned upright. |

Every new timeline from the first three can also get the LUT, the
sideways fix and a 9:16 copy ("Every new timeline" box).

## Start

1. Python 3.12 with `py -3.12 -m pip install -r requirements.txt`, and
   FFmpeg (`winget install ffmpeg`, or `brew install ffmpeg` on a Mac).
2. In Resolve Studio: Preferences > System > General > External scripting
   using: **Local** (and Allow all), then save.
3. Double-click **Run Tools.bat** (a Mac: `Run Tools.command`, not tried yet).

Settings and the remembered beats of songs are in `%LOCALAPPDATA%\Tools`
(`~/Library/Application Support/Tools` on a Mac).

## Why it is fast

Measured on the owner's PC (Windows 11, Python 3.12, 4 Nov 2026):

- The window appears **0.41 s** after start (0.49 s for the whole process;
  2.5 s the very first time, while Python compiles the files). No web
  page, no AI models; NumPy and PyAV load after the window is up.
- Connecting to Resolve: 13 ms; checking what is open: 0.3 ms.
- Sound is read as loudness every 10 ms, never written to WAV files, and
  only the part of each file the timeline uses. A clip under 5 minutes
  reads through PyAV in about 0.06 s per minute of sound; longer files go
  to FFmpeg programs side by side (each costs 0.7 s to start, then reads
  20 minutes in 0.5 s).
- The tests (27) run in about 4 s.

## What it does not do (ZCuts does)

No speech model, so no ums, retakes or captions; no vision model, so no
talking/B-roll sorting, levelling, or following the person in 9:16 (clips
are framed around their centre). Sideways clips are found from the
camera's own orientation tag, which Canon cameras write (checked on the
owner's R6 Mark III files by Edits); other cameras' sideways clips are
not found. No lip-sync fallback in the A/B cut, no music mixing or
ducking, no PowerGrades.

## Not checked in real Resolve yet

Only a read-only connection to the owner's Resolve has run. The jobs are
tested against a stand-in Resolve (`tests/fake_resolve.py`), which follows
what ZCuts learned, but only real runs prove Resolve's behaviour:

- AppendToTimeline's endFrame is taken as exclusive (ZCuts' open question).
- Phone clips with a rotation in their file are left alone, on the belief
  that Resolve turns those itself.
- `DuplicateTimeline` (9:16 and Look) needs Resolve 18 or newer.

## Tests

```
py -3.12 tests/run_tests.py           # all (offscreen, stand-in Resolve)
py -3.12 tests/run_tests.py -k word   # only those whose name has the word
```

Test media is made with FFmpeg (`tests/media_factory.py`): a tone with
known silences, two "cameras" 1.3 s apart, a 120 BPM click track, and a
clip carrying a Canon "on its side" tag.

## Licence

GPL-3.0-or-later (see LICENSE). Tools is open source and never sold.
