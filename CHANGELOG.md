# Changelog

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
