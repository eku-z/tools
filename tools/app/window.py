"""The Tools window: one tab per job, the finishing touches every new
timeline gets, a log, Stop and Undo.

Startup is kept short: NumPy, PyAV and the jobs load on a helper thread
after the window is on screen, and Resolve is asked for only once the
window shows (and then every few seconds, only when Resolve is running).
"""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
                               QTabWidget, QToolButton, QVBoxLayout, QWidget)

from tools import __version__
from tools.app import settings as store
from tools.app.runner import Runner
from tools.core.presets import PRESETS
from tools.resolve import look
from tools.resolve.link import Link
from tools.resolve.undo import History

TRACKS = ["Auto"] + ["V{}".format(n) for n in range(1, 9)]
EVERY = [5, 8, 12, 20, 30]
PER_CUT = ["Auto", "1", "2", "4", "8", "16"]
ACCENT = "#e8833a"


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("#1b1b1d"))
    font = QFont("Segoe UI", 30)
    font.setBold(True)
    p.setFont(font)
    p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    p.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    def __init__(self, link: Link | None = None, settings: dict | None = None, poll: bool = True):
        super().__init__()
        self.link = link or Link()
        self.history = History()
        self.settings = settings if settings is not None else store.load()
        self.runner = Runner(self)
        self.runner.logged.connect(self.log)
        self.runner.moved.connect(self._moved)
        self.runner.finished.connect(self._finished)
        self.luts: dict[str, str] = {}
        self.ctx = None
        self._began = 0.0
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save)
        self.setWindowTitle("Tools {}".format(__version__))
        self.setWindowIcon(app_icon())
        self.resize(460, 680)
        self._build()
        self._load_settings()
        self._poll_timer = QTimer(self, interval=3000, timeout=self.refresh_status)
        if poll:
            QTimer.singleShot(0, self._after_show)

    # ---- layout ---------------------------------------------------------------------------

    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)

        top = QHBoxLayout()
        self.status = QLabel("Looking for Resolve…")
        self.status.setWordWrap(True)
        again = QToolButton(text="↻", toolTip="Look for Resolve again")
        again.clicked.connect(lambda *_: self.refresh_status())
        top.addWidget(self.status, 1)
        top.addWidget(again)
        outer.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._silences_tab(), "Silences")
        self.tabs.addTab(self._ab_tab(), "A/B camera")
        self.tabs.addTab(self._montage_tab(), "Montage")
        self.tabs.addTab(self._simple_tab("vertical", "Make a 9:16 copy",
                                          "A copy of the open timeline at 1080x1920, every clip zoomed to fill "
                                          "it around its centre. Clips filmed on their side are turned upright."),
                         "9:16")
        self.tabs.addTab(self._simple_tab("look", "Make the copy",
                                          "A copy of the open timeline with the LUT below on every clip, and "
                                          "clips filmed with the camera on its side turned upright."), "Look")
        self.tabs.currentChanged.connect(lambda *_: self._changed())
        outer.addWidget(self.tabs)

        finish = QGroupBox("Every new timeline")
        form = QFormLayout(finish)
        self.lut = QComboBox()
        self.lut.setMinimumContentsLength(18)
        self.lut.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.lut.addItem(look.NO_LUT)
        self.lut.currentIndexChanged.connect(lambda *_: self._changed())
        form.addRow("LUT", self.lut)
        self.upright = QCheckBox("Turn clips filmed on their side upright (Canon)")
        self.also_vertical = QCheckBox("Also make a 9:16 copy")
        for box in (self.upright, self.also_vertical):
            box.toggled.connect(lambda *_: self._changed())
            form.addRow(box)
        outer.addWidget(finish)

        bar = QHBoxLayout()
        self.progress = QProgressBar(textVisible=False, maximum=1000)
        self.progress.setFixedHeight(8)
        self.stop = QPushButton("Stop", enabled=False)
        self.stop.clicked.connect(lambda *_: self._stop())
        self.undo = QPushButton("Undo", enabled=False, toolTip="Delete the timeline(s) the last job made")
        self.undo.clicked.connect(lambda *_: self._undo())
        bar.addWidget(self.progress, 1)
        bar.addWidget(self.stop)
        bar.addWidget(self.undo)
        outer.addLayout(bar)
        self.step = _grey(QLabel(""))
        outer.addWidget(self.step)

        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(3000)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSizeF(max(8.0, mono.pointSizeF() - 0.5))
        self.log_view.setFont(mono)
        self.log_view.setPlaceholderText("What Tools does shows here. Your own timelines are never changed: "
                                         "every job makes a new one, and Undo deletes it.")
        outer.addWidget(self.log_view, 1)
        self.setCentralWidget(root)

    def _form_tab(self, name: str, button_text: str, hint: str):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_grey(QLabel(hint, wordWrap=True)))
        form = QFormLayout()
        layout.addLayout(form)
        layout.addStretch(1)
        go = QPushButton(button_text)
        go.setStyleSheet(GO_STYLE)
        go.clicked.connect(lambda *_: self.run(name))
        layout.addWidget(go)
        setattr(self, "go_" + name, go)
        return page, form

    def _simple_tab(self, name: str, button_text: str, hint: str) -> QWidget:
        return self._form_tab(name, button_text, hint)[0]

    def _silences_tab(self) -> QWidget:
        page, form = self._form_tab("silences", "Cut the silences",
                                    "A new timeline with the silences of one video track cut out, by "
                                    "loudness. Clips with a speed change or no file are kept whole.")
        self.s_track = self._combo(TRACKS)
        self.s_preset = self._combo(["Natural", "Tight", "Loose", "Custom"])
        self.s_threshold = self._spin(-60, -20, " dBFS")
        self.s_minimum = self._spin(100, 3000, " ms", 50)
        self.s_padding = self._spin(0, 800, " ms", 10)
        form.addRow("Track", self.s_track)
        form.addRow("Cutting", self.s_preset)
        form.addRow("Quieter than", self.s_threshold)
        form.addRow("For at least", self.s_minimum)
        form.addRow("Keep around words", self.s_padding)
        self.s_preset.currentTextChanged.connect(lambda *_: self._custom_silence())
        return page

    def _ab_tab(self) -> QWidget:
        page, form = self._form_tab("ab", "Make the A/B cut",
                                    "Two camera files on any video tracks. A separate sound recording on an "
                                    "audio track becomes the sound; otherwise the clearer camera's own.")
        self.a_every = self._combo(["{} s".format(n) for n in EVERY])
        self.a_pauses = QCheckBox("Move each switch to a pause")
        self.a_sync = QCheckBox("Line the cameras up by their sound")
        form.addRow("Switch camera every", self.a_every)
        form.addRow(self.a_pauses)
        form.addRow(self.a_sync)
        for box in (self.a_pauses, self.a_sync):
            box.toggled.connect(lambda *_: self._changed())
        return page

    def _montage_tab(self) -> QWidget:
        page, form = self._form_tab("montage", "Cut to the beat",
                                    "The clips of one video track cut to a song's beats, in order. The song "
                                    "is the file below, or the music on the timeline's audio tracks.")
        self.m_track = self._combo(TRACKS)
        song_row = QHBoxLayout()
        self.m_song = QLineEdit(placeholderText="The music on the timeline")
        self.m_song.textChanged.connect(lambda *_: self._changed())
        browse = QToolButton(text="…", toolTip="Pick a song")
        browse.clicked.connect(lambda *_: self._browse_song())
        song_row.addWidget(self.m_song, 1)
        song_row.addWidget(browse)
        self.m_per_cut = self._combo(PER_CUT)
        self.m_start = self._spin(0, 600, " s")
        self.m_bpm = self._spin(0, 250, " BPM")
        self.m_bpm.setSpecialValueText("Find it")
        self.m_lead = self._spin(0, 6, " fr")
        self.m_fill = QCheckBox("Fill the whole song")
        self.m_pace = QCheckBox("Faster cuts when the music builds")
        self.m_mute = QCheckBox("No camera sound, only the song")
        form.addRow("Track", self.m_track)
        form.addRow("Song", song_row)
        form.addRow("Beats a cut", self.m_per_cut)
        form.addRow("Song starts at", self.m_start)
        form.addRow("Tempo", self.m_bpm)
        form.addRow("Cut early by", self.m_lead)
        for box in (self.m_fill, self.m_pace, self.m_mute):
            box.toggled.connect(lambda *_: self._changed())
            form.addRow(box)
        return page

    def _combo(self, items) -> QComboBox:
        combo = QComboBox()
        combo.addItems([str(i) for i in items])
        combo.currentIndexChanged.connect(lambda *_: self._changed())
        return combo

    def _spin(self, low: int, high: int, suffix: str, step: int = 1) -> QSpinBox:
        spin = QSpinBox(minimum=low, maximum=high, suffix=suffix, singleStep=step)
        spin.valueChanged.connect(lambda *_: self._changed())
        return spin

    # ---- settings -----------------------------------------------------------------------------

    def _load_settings(self) -> None:
        self._loading = True
        s = self.settings
        sil, ab, mo, fin = s["silences"], s["ab"], s["montage"], s["finish"]
        self.s_track.setCurrentText(str(sil["track"]))
        self.s_preset.setCurrentText(str(sil["preset"]))
        self.s_threshold.setValue(int(sil["threshold_db"]))
        self.s_minimum.setValue(int(sil["minimum_ms"]))
        self.s_padding.setValue(int(sil["padding_ms"]))
        self.a_every.setCurrentText("{} s".format(int(ab["every"])))
        self.a_pauses.setChecked(bool(ab["pauses"]))
        self.a_sync.setChecked(bool(ab["sync"]))
        self.m_track.setCurrentText(str(mo["track"]))
        self.m_song.setText(str(mo["song_path"]))
        self.m_per_cut.setCurrentText(str(mo["per_cut"] or "Auto"))
        self.m_start.setValue(int(mo["song_start"]))
        self.m_bpm.setValue(int(mo["bpm"]))
        self.m_lead.setValue(int(mo["lead"]))
        self.m_fill.setChecked(bool(mo["fill_song"]))
        self.m_pace.setChecked(bool(mo["pace"]))
        self.m_mute.setChecked(bool(mo["mute"]))
        self.upright.setChecked(bool(fin["upright"]))
        self.also_vertical.setChecked(bool(fin["also_vertical"]))
        self.tabs.setCurrentIndex(int(s.get("tab", 0)))
        self._loading = False
        self._custom_silence()

    def _read_settings(self) -> dict:
        s = self.settings
        s["tab"] = self.tabs.currentIndex()
        s["silences"].update(track=self.s_track.currentText(), preset=self.s_preset.currentText(),
                             threshold_db=self.s_threshold.value(), minimum_ms=self.s_minimum.value(),
                             padding_ms=self.s_padding.value())
        s["ab"].update(every=int(self.a_every.currentText().split()[0]), pauses=self.a_pauses.isChecked(),
                       sync=self.a_sync.isChecked())
        per_cut = self.m_per_cut.currentText()
        s["montage"].update(track=self.m_track.currentText(), song_path=self.m_song.text().strip(),
                            per_cut=int(per_cut) if per_cut.isdigit() else 0, song_start=self.m_start.value(),
                            bpm=self.m_bpm.value(), lead=self.m_lead.value(), fill_song=self.m_fill.isChecked(),
                            pace=self.m_pace.isChecked(), mute=self.m_mute.isChecked())
        if self.lut.count() > 1 or self.lut.currentText() != look.NO_LUT:
            s["finish"]["lut"] = self.lut.currentText()
        s["finish"].update(upright=self.upright.isChecked(), also_vertical=self.also_vertical.isChecked())
        return s

    def _changed(self) -> None:
        if not getattr(self, "_loading", True):
            self._save_timer.start()

    def _save(self) -> None:
        store.save(self._read_settings())

    def _custom_silence(self) -> None:
        preset = self.s_preset.currentText()
        custom = preset == "Custom"
        for spin in (self.s_threshold, self.s_minimum, self.s_padding):
            spin.setEnabled(custom)
        if not custom and preset in PRESETS:
            st = PRESETS[preset]
            self._loading, was = True, getattr(self, "_loading", False)
            self.s_threshold.setValue(int(st.threshold_db))
            self.s_minimum.setValue(int(round(st.minimum * 1000)))
            self.s_padding.setValue(int(round(st.padding * 1000)))
            self._loading = was
        self._changed()

    def _browse_song(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Pick a song", os.path.dirname(self.m_song.text()),
                                              "Music (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.aif *.aiff *.opus);;"
                                              "All files (*)")
        if path:
            self.m_song.setText(os.path.normpath(path))

    # ---- after the window shows -------------------------------------------------------------

    def _after_show(self) -> None:
        self.fill_luts()
        threading.Thread(target=self._warm_up, daemon=True).start()
        self.refresh_status()
        self._poll_timer.start()

    @staticmethod
    def _warm_up() -> None:
        try:
            from tools.core import media
            from tools.resolve import jobs  # noqa: F401 - loads NumPy and the jobs

            media.warm_up()
        except Exception:  # noqa: BLE001 - they load again when a job needs them
            pass

    def fill_luts(self) -> None:
        self.luts = look.find_luts()
        wanted = self.settings["finish"].get("lut") or look.NO_LUT
        self._loading = True
        self.lut.clear()
        self.lut.addItem(look.NO_LUT)
        self.lut.addItems(list(self.luts))
        self.lut.setCurrentText(wanted if wanted in self.luts else look.NO_LUT)
        self._loading = False

    def refresh_status(self) -> None:
        if self.runner.busy:
            return
        project = self.link.ensure()
        if project is None:
            self._set_status(self.link.problem or "Resolve is not reachable.", ok=False)
            self._poll_timer.setInterval(5000 if "not open" in self.link.problem else 15000)
            return
        self._poll_timer.setInterval(3000)
        try:
            timeline = project.GetCurrentTimeline()
            text = "{} · {}".format(project.GetName(), timeline.GetName() if timeline else "no timeline open")
        except Exception:  # noqa: BLE001
            text = "Connected to Resolve"
        self._set_status(text, ok=True)
        self.undo.setEnabled(bool(self.history.records))
        self.undo.setToolTip("Delete {}".format(self.history.label) if self.history.records else "Nothing to undo")

    def _set_status(self, text: str, ok: bool) -> None:
        self.status.setText(("● " if ok else "○ ") + text)
        p = self.status.palette()
        p.setColor(QPalette.ColorRole.WindowText, QColor("#7ccf8a" if ok else "#e0b070"))
        self.status.setPalette(p)

    # ---- jobs -----------------------------------------------------------------------------------

    def options(self, name: str) -> dict:
        s = self._read_settings()
        finish = {"lut_path": self.luts.get(self.lut.currentText(), ""), "upright": self.upright.isChecked(),
                  "also_vertical": self.also_vertical.isChecked()}
        return dict(s.get(name, {}), **finish)

    def run(self, name: str) -> None:
        if self.runner.busy:
            return
        from tools.core.work import Context
        from tools.resolve import jobs

        self.ctx = Context(self.link, self.history, log=self.runner.logged.emit,
                           progress=self.runner.moved.emit, data_dir=store.data_dir())
        self._busy(True)
        self._began = time.perf_counter()
        self.log("")
        self.log("— {} —".format(self.tabs.tabText(self.tabs.currentIndex())), bold=True)
        self.runner.start(jobs.JOBS[name](self.ctx, self.options(name)))

    def _busy(self, busy: bool) -> None:
        for name in ("silences", "ab", "montage", "vertical", "look"):
            getattr(self, "go_" + name).setEnabled(not busy)
        self.stop.setEnabled(busy)
        self.undo.setEnabled(not busy and bool(self.history.records))
        if busy:
            self.progress.setValue(0)
            self.step.setText("Starting")
        else:
            self.progress.setValue(0)

    def _moved(self, fraction: float, text: str) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        self.step.setText(text)

    def _finished(self, summary: str, error) -> None:
        took = time.perf_counter() - self._began
        self._busy(False)
        if error is None:
            self.log(summary, colour="#7ccf8a")
            self.step.setText("Done in {:.1f} s".format(took))
        elif str(error) == "Stopped.":
            self.log("Stopped.")
            self.step.setText("Stopped")
        else:
            self.log(str(error) or type(error).__name__, colour="#ff7a6b")
            self.step.setText("Not done")
        self.refresh_status()

    def _stop(self) -> None:
        if self.ctx is not None:
            self.ctx.stop.set()
            self.step.setText("Stopping…")

    def _undo(self) -> None:
        if self.runner.busy:
            return
        self.log(self.history.undo(self.link))
        self.refresh_status()

    def log(self, text: str, colour: str = "", bold: bool = False) -> None:
        fmt = QTextCharFormat()
        if colour:
            fmt.setForeground(QColor(colour))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        cursor = self.log_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        if not self.log_view.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(text, fmt)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self._save_timer.stop()
        self._save()
        if self.ctx is not None:
            self.ctx.stop.set()
        super().closeEvent(event)


GO_STYLE = ("QPushButton { background: %(a)s; color: #1b1b1d; font-weight: bold; padding: 8px; border: none;"
            " border-radius: 5px; } QPushButton:hover { background: #f39a57; }"
            " QPushButton:disabled { background: #4a3a2e; color: #8a7a6e; }") % {"a": ACCENT}


def apply_theme(app) -> None:
    """Dark, like Resolve, with Qt's Fusion style and a palette: an
    application-wide style sheet made the window 0.2 s slower to appear."""
    app.setStyle("Fusion")
    p = QPalette()
    colours = {QPalette.ColorRole.Window: "#1f1f22", QPalette.ColorRole.WindowText: "#e6e6e6",
               QPalette.ColorRole.Base: "#2a2a2e", QPalette.ColorRole.AlternateBase: "#26262a",
               QPalette.ColorRole.Text: "#e6e6e6", QPalette.ColorRole.Button: "#303036",
               QPalette.ColorRole.ButtonText: "#e6e6e6", QPalette.ColorRole.Highlight: ACCENT,
               QPalette.ColorRole.HighlightedText: "#1b1b1d", QPalette.ColorRole.ToolTipBase: "#2a2a2e",
               QPalette.ColorRole.ToolTipText: "#e6e6e6", QPalette.ColorRole.PlaceholderText: "#7a7a80",
               QPalette.ColorRole.Mid: "#3a3a40", QPalette.ColorRole.Dark: "#18181a",
               QPalette.ColorRole.Light: "#3d3d44", QPalette.ColorRole.Link: ACCENT}
    for role, colour in colours.items():
        p.setColor(role, QColor(colour))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#6b6b6b"))
    app.setPalette(p)


def _grey(label: QLabel) -> QLabel:
    p = label.palette()
    p.setColor(QPalette.ColorRole.WindowText, QColor("#9a9aa0"))
    label.setPalette(p)
    return label
