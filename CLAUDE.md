# CLAUDE.md

Tools is a light, fast PySide6 (Qt Widgets) helper for DaVinci Resolve
Studio, grown from ZCuts: cut silences, A/B camera cut, cut to the beat
(or a set tempo), 9:16, Look (LUT, upright, fill the frame), all on the
open timeline. Every switch starts off (the owner's choice). Read `README.md` first (what it
does, measured speed, what is not checked in real Resolve), then
`CHANGELOG.md`.

## Hard rules

- **Never edit ZCuts** (`C:\Users\samue\Desktop\ZCuts`) or Edits
  (`C:\Users\samue\Desktop\Edits`). Read them and port code into Tools with
  a source reference in the docstring ("ported from ZCuts
  (engine/NN_file.py function; build N)"); never import from them.
- **Jobs change the open timeline, and only after a backup.**
  `jobs.Session.before_change` copies it into the "Tools backups" bin before
  the first change (no copy, no change); Undo puts the copy back
  (`undo.History.undo`). Cutting is `timeline.put_back`: take clips off, put
  pieces back at exact frames with each clip's settings and grade.
- **Resolve is only called on the window's thread.** A job is a generator
  (`tools/core/work.py`): it calls Resolve itself and `yield`s a `Heavy`
  for slow pure work (reading sound, finding beats), which runs on a
  helper thread. Helpers never touch Resolve or Qt.
- Keep one link to Resolve (`resolve.link.Link`); Resolve refuses clips and
  timelines from one connection in calls over another (ZCuts rule 2).
- AppendToTimeline: source frames, endFrame exclusive, recordFrame from the
  timeline's start frame; Resolve can drop the rest of a list after one bad
  piece, so use `timeline.append` (its fallbacks).
- No models and no web engine: the window must keep opening in well under
  a second. NumPy, PyAV and the jobs load after it shows
  (`test_the_window_opens_without_numpy`).
- `tools/core` is pure (no Qt, no Resolve) and tested on its own.
- GPL-3.0-or-later, open source, never sold (owner, 4 Nov 2026).
- Keep README.md and CHANGELOG.md true; measured numbers, not guesses.
  Never commit frames, names or transcripts from the owner's footage.

## Commands

```
py -3.12 -m tools                      # run (or Run Tools.bat)
py -3.12 -m tools --startup-time       # how long the window takes to appear
py -3.12 -m tools --check              # what Tools finds (packages, FFmpeg, Resolve, LUTs)
bash setup_mac.sh                      # a Mac: .venv, packages, check; then Run Tools.command
py -3.12 tests/run_tests.py            # all tests
py -3.12 tests/run_tests.py -k word    # filter
```

Test media comes from `tests/media_factory.py`; jobs are tested against
`tests/fake_resolve.py`. Never run a job on the owner's real Resolve
project without asking: a read-only check is fine.
