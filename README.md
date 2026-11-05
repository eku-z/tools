# Tools

Fast, light helpers for DaVinci Resolve Studio, taken from ZCuts. A small
window beside Resolve that works on **the timeline you have open**.

```
● Vlog day 3                                   ↻
[x] Cut silences          [Natural  v]  [⚙] [Run]
[ ] A/B camera            [every 8 s v] [⚙] [Run]
[ ] Cut to the beat                     [⚙] [Run]
[ ] Make it 9:16                        [⚙] [Run]
[x] Look                  [LUT      v]  [⚙] [Run]
[        Apply 2        ]  [Undo]
```

- **Run** on a row does that one job now.
- Tick several rows and press **Apply** to do them top to bottom, as one
  Undo step.
- **⚙** opens a row's options; the log opens with **Log**. Everything
  starts off: you tick what you want.

| Row | What it does to the open timeline |
|---|---|
| Cut silences | Cuts the silences out of one video track (Auto: the one with most footage) and its sound, closing the gaps like a ripple delete. Natural / Tight / Loose, or your own threshold, length and padding. "Cut every track" cuts the other tracks at the same places so they stay in step. |
| A/B camera | Two cameras on two video tracks: the upper one is switched in over the lower one every N seconds (optionally lined up by sound, and each switch moved to a pause). The lower camera and its sound stay as they are. |
| Cut to the beat | One track's clips cut to a song's beats (the song on the timeline stays where it is; a song you pick is placed under the cut), or with **No song** to a tempo you type. |
| Make it 9:16 | Makes the timeline 1080x1920 and zooms every clip to fill it. |
| Look | A LUT on every clip, clips filmed on their side turned upright, and **Fill the frame** (every clip zoomed just enough that no black bars show, counting its rotation and position; clips already filling it are left alone). |

### Undo and the backups

Before its first change, Tools copies the timeline into a **Tools
backups** bin in the Media Pool, named `<name> (before Tools)`. **Undo**
puts that copy back in its place, under its name, so grades, effects and
titles are exactly as they were. If you edited the timeline by hand after
Tools changed it, Undo refuses (it would throw your edit away); the copy is
still in the bin. Delete old backups when you no longer need them.

Resolve's scripting cannot cut a clip where it is, so a cut clip is taken
off and its pieces put back at exact frames, each given the clip's
settings (zoom, position, crop, composite...), colour label and, copied from
the backup, its grade. Titles and generators cannot be put back by a
script, so they stay where they are.

## Start

**Windows**

1. Python 3.12 with `py -3.12 -m pip install -r requirements.txt`, and
   FFmpeg (`winget install ffmpeg`).
2. In Resolve Studio: Preferences > System > General > External scripting
   using: **Local** (and Allow all), then save.
3. Double-click **Run Tools.bat**.

**Mac** (Apple chip or Intel; not tried on a Mac yet)

1. Copy the Tools folder to the Mac. In Terminal, in that folder, run
   `bash setup_mac.sh`. It finds Python 3.12 (or offers to install it
   with Homebrew), makes Tools its own environment in `.venv`, installs
   the packages, offers FFmpeg (optional), and ends with a check of what
   it found.
2. In Resolve Studio: DaVinci Resolve > Preferences > System > General >
   External scripting using: **Local**, then save.
3. Double-click **Run Tools.command** in Finder.

`python -m tools --check` (on a Mac `.venv/bin/python -m tools --check`)
says what Tools finds: the packages, FFmpeg, Resolve's scripting library,
whether Resolve is running, the LUT folders.

Settings and the remembered beats of songs are in `%LOCALAPPDATA%\Tools`
(`~/Library/Application Support/Tools` on a Mac).

## Why it is fast

Measured on the owner's PC (Windows 11, Python 3.12, 4 Nov 2026):

- The window appears in about **0.4 s** (2.5 s the very first time, while
  Python compiles the files). No web page, no AI models; NumPy and PyAV
  load after the window is up.
- Connecting to Resolve: 13 ms; checking what is open: 0.3 ms.
- Sound is read as loudness every 10 ms, never written to WAV files, and
  only the part of each file the timeline uses. A clip under 5 minutes
  reads through PyAV in about 0.06 s per minute of sound; longer files go
  to FFmpeg programs side by side (each costs 0.7 s to start, then reads
  20 minutes in 0.5 s). Without the FFmpeg program (a Mac often has
  none), PyAV reads everything: 20 minutes in about 1.1 s.
- The tests (38) run in about 6-9 s. They make their test media with the
  FFmpeg program, so on a Mac they need it (`brew install ffmpeg`).

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
- That `DeleteClips`, putting pieces back at exact frames on an existing
  timeline, `GetProperty()`/`SetProperty` for every setting, `CopyGrades`
  from the backup timeline, `DuplicateTimeline`, `SetName` and moving a
  timeline between bins all work from a script (Resolve 18 or newer).
- Phone clips with a rotation in their file are left alone, on the belief
  that Resolve turns those itself; their size is read from the file as
  shown (a portrait clip is 1080x1920), for filling the frame and 9:16.
- Pan and Tilt are read as timeline pixels (as ZCuts set them).

## Tests

```
py -3.12 tests/run_tests.py           # all (offscreen, stand-in Resolve)
py -3.12 tests/run_tests.py -k word   # only those whose name has the word
```

Test media is made with FFmpeg (`tests/media_factory.py`): a tone with
known silences, two "cameras" 1.3 s apart, a 120 BPM click track, a clip
carrying a Canon "on its side" tag, a portrait phone clip and a 4:3 clip.

## Licence

GPL-3.0-or-later (see LICENSE). Tools is open source and never sold.
