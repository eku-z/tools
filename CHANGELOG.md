# Changelog

## 0.4.1 (5 Nov 2026)

- Runs on a Mac (not tried on one yet): `bash setup_mac.sh` makes Tools
  its own Python 3.12 environment in `.venv` (as Edits' setup_mac.sh
  does), installs the packages, offers FFmpeg, makes the launchers
  double-clickable and ends with `--check`. `Run Tools.command` and
  `Run Tests.command` start it from Finder.
- Without the FFmpeg program, PyAV reads every file (before, long files
  needed FFmpeg).
- `python -m tools --check`: what Tools finds on the computer.
- 38 tests.

## 0.4.0 (4 Nov 2026)

The owner: work on the timeline I am in, and a faster, less cluttered flow.

- **Every job changes the open timeline** instead of making a new one.
  Before the first change the timeline is copied into a "Tools backups"
  bin; Undo puts the copy back under the timeline's name and in its bin,
  and refuses when the timeline was edited by hand since (the copy stays).
  No backup, no change.
- Cutting in place: the clips are taken off and their pieces put back at
  exact frames (ZCuts' Update timeline method), each with its clip's
  settings, colour label and grade (copied from the backup). Cut silences
  closes the gaps like a ripple delete, on one track and its sound, or on
  every track ("Cut every track"). A/B: the upper camera is switched in
  over the lower one, which stays. Cut to the beat follows the song where
  it already is on the timeline.
- **New window**: five rows (tick, main choice, ⚙ options, Run) instead of
  tabs and boxes; Apply does the ticked rows top to bottom as one Undo step;
  options and the log stay hidden until opened; the window sizes itself.
  Settings from 0.3 are not used (their layout changed).
- 36 tests.

## 0.3.0 (4 Nov 2026)

- Montage: **No song: cut to the tempo below**. Each clip of the track is
  cut, once and in order, to whole beats of the tempo you type (Beats a
  cut: Auto is about 2.5 s), from the middle of the clip; no song is placed
  and the clips keep their own sound.
- **Every switch starts off** (the owner's choice): Use in Apply, sync,
  pauses, Fill the whole song, faster cuts, no camera sound, Fill the frame,
  upright, also 9:16; Cut early starts at 0 frames. Settings saved by
  0.1-0.2 are not used once, so the new defaults take effect.
- **Apply** at the bottom: runs every job ticked "Use in Apply" on its tab,
  one after another (Silences, A/B camera, Montage, 9:16, Look), each on the
  timeline that was open when you pressed it, which is opened again at the
  end. A job that cannot be done is said and skipped; each job is its own
  Undo step. The buttons on the tabs still run one job.
- 33 tests.

## 0.2.0 (4 Nov 2026)

- Look: **Fill the frame (no black bars)**, on by default. Every clip in
  the copy is zoomed just enough to cover the frame, counting its rotation
  (a clip turned upright included) and how far it was moved with Pan and
  Tilt, plus 0.2 % so rounding never leaves a one-pixel line. Clips that
  already fill it, or that were zoomed in further by hand, are left alone.
- A clip's size is now read from its file as it is shown (anamorphic pixels
  widened, a portrait phone clip 1080x1920 rather than its stored
  1920x1080), for filling the frame and for the 9:16 copy: before, a
  portrait phone clip in a 9:16 copy would have been zoomed to 316 %.
- 29 tests.

## 0.1.0 (4 Nov 2026)

The first version: five jobs from ZCuts, in a window that opens in 0.41 s.

- Silences: ZCuts' loudness rule (20 ms windows, threshold, minimum,
  padding; presets Natural, Tight, Loose) on one video track, Auto picking
  the track with the most footage. Clips with a speed change, no file or
  no sound are kept whole.
- A/B camera: ZCuts' two-camera cut. The two files with the most footage,
  lined up by their sound (FFT match of loudness patterns, every sound
  stream of a camera that is silent on the first), switches every 5-30 s
  moved to pauses, the master sound unbroken on A1.
- Montage: all of ZCuts' beat tracker (tempo candidates, 3:2 check, bar
  starts, exact grid), pieces ending on beats and preferring the bar grid,
  Fill the whole song, faster cuts when the music builds, cut early by N
  frames; the song is placed on the first free audio track. Beats are
  remembered per song.
- 9:16 copy and Look copy of the open timeline; LUT on every clip (from
  Resolve's LUT folders, your own first; node graph fallback); clips filmed
  on their side turned upright from Canon's AutoRotate tag (Edits' reader).
- Undo deletes what the last job made (Resolve's open timeline is switched
  first, then each, then as Media Pool clips); refused after reconnecting
  or in another project.
- Sound read as 10 ms loudness through PyAV (short clips) or FFmpeg
  programs side by side (long files); identical to 0.0 dB on the test media.
- 27 tests against a stand-in Resolve and generated media.
