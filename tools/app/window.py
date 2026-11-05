"""The Tools window: one row per job, working on the timeline open in
Resolve. Tick rows and press Apply to do them together (one Undo step),
or press a row's Run to do just that one. Each row's options stay hidden
behind its ⚙ until you want them; the log stays hidden until you open it.

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
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

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
ORDER = ["silences", "ab", "montage", "vertical", "look"]  # the rows, and the order Apply does them in
TITLES = {"silences": "Cut silences", "ab": "A/B camera", "montage": "Cut to the beat",
          "vertical": "Make it 9:16", "look": "Look"}
ACCENT = "#e8833a"
GO_STYLE = ("QPushButton { background: %(a)s; color: #1b1b1d; font-weight: bold; padding: 8px; border: none;"
            " border-radius: 5px; } QPushButton:hover { background: #f39a57; }"
            " QPushButton:disabled { background: #4a3a2e; color: #8a7a6e; }") % {"a": ACCENT}
ROW_STYLE = "QFrame#row { background: #26262a; border: 1px solid #333338; border-radius: 6px; }"


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("#1b1b1d"))
    font = QFont()  # the system's own font, on Windows and on a Mac
    font.setPixelSize(38)
    font.setBold(True)
    p.setFont(font)
    p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    p.end()
    return QIcon(pixmap)


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
        self._loading = True
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save)
        self.setWindowTitle("Tools {}".format(__version__))
        self.setWindowIcon(app_icon())
        self._build()
        self._load_settings()
        self._poll_timer = QTimer(self, interval=3000, timeout=self.refresh_status)
        if poll:
            QTimer.singleShot(0, self._after_show)

    # ---- layout ---------------------------------------------------------------------------

    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(6)

        top = QHBoxLayout()
        self.status = QLabel("Looking for Resolve…")
        self.status.setWordWrap(True)
        again = QToolButton(text="↻", toolTip="Look for Resolve again")
        again.clicked.connect(lambda *_: self.refresh_status())
        top.addWidget(self.status, 1)
        top.addWidget(again)
        outer.addLayout(top)

        self.rows: dict[str, dict] = {}
        self.rows["silences"] = self._row("silences", self._silences_quick(), self._silences_options())
        self.rows["ab"] = self._row("ab", self._ab_quick(), self._ab_options())
        self.rows["montage"] = self._row("montage", None, self._montage_options())
        self.rows["vertical"] = self._row("vertical", None, self._vertical_options())
        self.rows["look"] = self._row("look", self._look_quick(), self._look_options())
        for name in ORDER:
            outer.addWidget(self.rows[name]["frame"])

        bottom = QHBoxLayout()
        self.apply = QPushButton("Apply")
        self.apply.setStyleSheet(GO_STYLE)
        self.apply.setToolTip("Do every ticked job on the open timeline, top to bottom, as one Undo step.")
        self.apply.clicked.connect(lambda *_: self.run_ticked())
        self.stop = QPushButton("Stop")
        self.stop.clicked.connect(lambda *_: self._stop())
        self.stop.hide()
        self.undo = QPushButton("Undo", enabled=False)
        self.undo.clicked.connect(lambda *_: self._undo())
        bottom.addWidget(self.apply, 1)
        bottom.addWidget(self.stop, 1)
        bottom.addWidget(self.undo)
        outer.addLayout(bottom)

        self.progress = QProgressBar(textVisible=False, maximum=1000)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        outer.addWidget(self.progress)
        line = QHBoxLayout()
        self.step = _grey(QLabel("Tick what you want, then Apply. Run does one job now."))
        self.step.setWordWrap(True)
        self.log_button = QToolButton(text="Log", checkable=True, toolTip="Show what Tools did")
        self.log_button.toggled.connect(lambda on: self._show_log(on))
        line.addWidget(self.step, 1)
        line.addWidget(self.log_button)
        outer.addLayout(line)

        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(3000)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSizeF(max(8.0, mono.pointSizeF() - 0.5))
        self.log_view.setFont(mono)
        self.log_view.setMinimumHeight(160)
        self.log_view.hide()
        outer.addWidget(self.log_view, 1)
        outer.addStretch(0)
        self.setCentralWidget(root)
        self.setMinimumWidth(420)

    def _row(self, name: str, quick, options: QWidget) -> dict:
        frame = QFrame(objectName="row")
        frame.setStyleSheet(ROW_STYLE)
        box = QVBoxLayout(frame)
        box.setContentsMargins(8, 5, 6, 5)
        box.setSpacing(4)
        head = QHBoxLayout()
        tick = QCheckBox(TITLES[name])
        tick.toggled.connect(lambda *_: self._ticks_changed())
        head.addWidget(tick, 1)
        if quick is not None:
            head.addWidget(quick)
        gear = QToolButton(text="⚙", checkable=True, toolTip="Options")
        go = QToolButton(text="Run", toolTip="Do just this now, on the open timeline")
        go.clicked.connect(lambda *_: self.run(name))
        head.addWidget(gear)
        head.addWidget(go)
        box.addLayout(head)
        options.hide()
        box.addWidget(options)
        gear.toggled.connect(lambda on: self._show_options(options, on))
        return {"frame": frame, "tick": tick, "go": go, "options": options}

    def _panel(self):
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(22, 2, 4, 4)
        return panel, form

    def _silences_quick(self) -> QComboBox:
        self.s_preset = self._combo(["Natural", "Tight", "Loose", "Custom"])
        self.s_preset.setToolTip("How much to cut")
        self.s_preset.currentTextChanged.connect(lambda *_: self._custom_silence())
        return self.s_preset

    def _silences_options(self) -> QWidget:
        panel, form = self._panel()
        self.s_track = self._combo(TRACKS)
        self.s_threshold = self._spin(-60, -20, " dBFS")
        self.s_minimum = self._spin(100, 3000, " ms", 50)
        self.s_padding = self._spin(0, 800, " ms", 10)
        self.s_all = self._check("Cut every track (keeps them in step)")
        form.addRow("Track", self.s_track)
        form.addRow("Quieter than", self.s_threshold)
        form.addRow("For at least", self.s_minimum)
        form.addRow("Keep around words", self.s_padding)
        form.addRow(self.s_all)
        return panel

    def _ab_quick(self) -> QComboBox:
        self.a_every = self._combo(["every {} s".format(n) for n in EVERY])
        return self.a_every

    def _ab_options(self) -> QWidget:
        panel, form = self._panel()
        form.addRow(_grey(QLabel("The camera on the upper track is switched in over the lower one.",
                                 wordWrap=True)))
        self.a_sync = self._check("Line camera B up by its sound")
        self.a_pauses = self._check("Move each switch to a pause")
        form.addRow(self.a_sync)
        form.addRow(self.a_pauses)
        return panel

    def _montage_options(self) -> QWidget:
        panel, form = self._panel()
        self.m_track = self._combo(TRACKS)
        self.m_no_song = self._check("No song: cut to the tempo below")
        self.m_no_song.toggled.connect(lambda *_: self._no_song())
        song_row = QHBoxLayout()
        self.m_song = QLineEdit(placeholderText="The music on the timeline")
        self.m_song.textChanged.connect(lambda *_: self._changed())
        browse = QToolButton(text="…", toolTip="Pick a song")
        browse.clicked.connect(lambda *_: self._browse_song())
        song_row.addWidget(self.m_song, 1)
        song_row.addWidget(browse)
        self.m_bpm = self._spin(0, 250, " BPM")
        self.m_bpm.setSpecialValueText("Find it")
        self.m_per_cut = self._combo(PER_CUT)
        self.m_start = self._spin(0, 600, " s")
        self.m_lead = self._spin(0, 6, " fr")
        self.m_fill = self._check("Fill the whole song")
        self.m_pace = self._check("Faster cuts when the music builds")
        self.m_mute = self._check("No camera sound")
        form.addRow("Track", self.m_track)
        form.addRow(self.m_no_song)
        form.addRow("Song", song_row)
        form.addRow("Tempo", self.m_bpm)
        form.addRow("Beats a cut", self.m_per_cut)
        form.addRow("Song starts at", self.m_start)
        form.addRow("Cut early by", self.m_lead)
        for box in (self.m_fill, self.m_pace, self.m_mute):
            form.addRow(box)
        self.m_song_widgets = [self.m_song, browse, self.m_start, self.m_lead, self.m_fill, self.m_pace]
        return panel

    def _vertical_options(self) -> QWidget:
        panel, form = self._panel()
        self.v_upright = self._check("Turn clips filmed on their side upright (Canon)")
        form.addRow(self.v_upright)
        return panel

    def _look_quick(self) -> QComboBox:
        self.lut = QComboBox()
        self.lut.setMinimumContentsLength(14)
        self.lut.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.lut.setToolTip("The LUT to put on every clip")
        self.lut.addItem(look.NO_LUT)
        self.lut.currentIndexChanged.connect(lambda *_: self._changed())
        return self.lut

    def _look_options(self) -> QWidget:
        panel, form = self._panel()
        self.l_upright = self._check("Turn clips filmed on their side upright (Canon)")
        self.l_fill = self._check("Fill the frame (no black bars)")
        self.l_fill.setToolTip("Zoom each clip just enough to cover the frame, counting its rotation and "
                               "position. Clips that already fill it are left as they are.")
        form.addRow(self.l_upright)
        form.addRow(self.l_fill)
        return panel

    def _combo(self, items) -> QComboBox:
        combo = QComboBox()
        combo.addItems([str(i) for i in items])
        combo.currentIndexChanged.connect(lambda *_: self._changed())
        return combo

    def _spin(self, low: int, high: int, suffix: str, step: int = 1) -> QSpinBox:
        spin = QSpinBox(minimum=low, maximum=high, suffix=suffix, singleStep=step)
        spin.valueChanged.connect(lambda *_: self._changed())
        return spin

    def _check(self, text: str) -> QCheckBox:
        box = QCheckBox(text)
        box.toggled.connect(lambda *_: self._changed())
        return box

    def _show_options(self, panel: QWidget, on: bool) -> None:
        panel.setVisible(on)
        QTimer.singleShot(0, self._fit)

    def _show_log(self, on: bool) -> None:
        self.log_view.setVisible(on)
        self._changed()
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        """Grow or shrink to what is shown (the log may be stretched taller)."""
        self.centralWidget().layout().activate()
        needed = self.centralWidget().sizeHint().height()
        if not self.log_view.isVisible() or self.height() < needed:
            self.setMinimumHeight(0)
            self.resize(self.width(), needed)

    # ---- settings -----------------------------------------------------------------------------

    def _load_settings(self) -> None:
        self._loading = True
        s = self.settings
        sil, ab, mo = s["silences"], s["ab"], s["montage"]
        for name in ORDER:
            self.rows[name]["tick"].setChecked(bool(s["ticked"][name]))
        self.s_track.setCurrentText(str(sil["track"]))
        self.s_preset.setCurrentText(str(sil["preset"]))
        self.s_threshold.setValue(int(sil["threshold_db"]))
        self.s_minimum.setValue(int(sil["minimum_ms"]))
        self.s_padding.setValue(int(sil["padding_ms"]))
        self.s_all.setChecked(bool(sil["all_tracks"]))
        self.a_every.setCurrentText("every {} s".format(int(ab["every"])))
        self.a_sync.setChecked(bool(ab["sync"]))
        self.a_pauses.setChecked(bool(ab["pauses"]))
        self.m_track.setCurrentText(str(mo["track"]))
        self.m_no_song.setChecked(bool(mo["no_song"]))
        self.m_song.setText(str(mo["song_path"]))
        self.m_bpm.setValue(int(mo["bpm"]))
        self.m_per_cut.setCurrentText(str(mo["per_cut"] or "Auto"))
        self.m_start.setValue(int(mo["song_start"]))
        self.m_lead.setValue(int(mo["lead"]))
        self.m_fill.setChecked(bool(mo["fill_song"]))
        self.m_pace.setChecked(bool(mo["pace"]))
        self.m_mute.setChecked(bool(mo["mute"]))
        self.v_upright.setChecked(bool(s["vertical"]["upright"]))
        self.l_upright.setChecked(bool(s["look"]["upright"]))
        self.l_fill.setChecked(bool(s["look"]["fill"]))
        self.log_button.setChecked(bool(s.get("log_open")))
        self._loading = False
        self._custom_silence()
        self._no_song()
        self._ticks_changed()

    def _read_settings(self) -> dict:
        s = self.settings
        s["ticked"] = {name: self.rows[name]["tick"].isChecked() for name in ORDER}
        s["log_open"] = self.log_button.isChecked()
        s["silences"].update(track=self.s_track.currentText(), preset=self.s_preset.currentText(),
                             threshold_db=self.s_threshold.value(), minimum_ms=self.s_minimum.value(),
                             padding_ms=self.s_padding.value(), all_tracks=self.s_all.isChecked())
        s["ab"].update(every=int(self.a_every.currentText().split()[1]), sync=self.a_sync.isChecked(),
                       pauses=self.a_pauses.isChecked())
        per_cut = self.m_per_cut.currentText()
        s["montage"].update(track=self.m_track.currentText(), song_path=self.m_song.text().strip(),
                            no_song=self.m_no_song.isChecked(), bpm=self.m_bpm.value(),
                            per_cut=int(per_cut) if per_cut.isdigit() else 0, song_start=self.m_start.value(),
                            lead=self.m_lead.value(), fill_song=self.m_fill.isChecked(),
                            pace=self.m_pace.isChecked(), mute=self.m_mute.isChecked())
        s["vertical"].update(upright=self.v_upright.isChecked())
        if self.lut.count() > 1 or self.lut.currentText() != look.NO_LUT:
            s["look"]["lut"] = self.lut.currentText()
        s["look"].update(upright=self.l_upright.isChecked(), fill=self.l_fill.isChecked())
        return s

    def _changed(self) -> None:
        if not self._loading:
            self._save_timer.start()

    def _save(self) -> None:
        store.save(self._read_settings())

    def _ticks_changed(self) -> None:
        count = sum(self.rows[name]["tick"].isChecked() for name in ORDER)
        self.apply.setText("Apply {}".format(count) if count else "Apply")
        self.apply.setEnabled(count > 0 and not self.runner.busy)
        self._changed()

    def _custom_silence(self) -> None:
        preset = self.s_preset.currentText()
        custom = preset == "Custom"
        for spin in (self.s_threshold, self.s_minimum, self.s_padding):
            spin.setEnabled(custom)
        if not custom and preset in PRESETS:
            st = PRESETS[preset]
            self._loading, was = True, self._loading
            self.s_threshold.setValue(int(st.threshold_db))
            self.s_minimum.setValue(int(round(st.minimum * 1000)))
            self.s_padding.setValue(int(round(st.padding * 1000)))
            self._loading = was
        self._changed()

    def _no_song(self) -> None:
        """Without a song, what belongs to the song is greyed out, and the
        tempo is the one to cut to."""
        no_song = self.m_no_song.isChecked()
        for widget in self.m_song_widgets:
            widget.setEnabled(not no_song)
        self.m_bpm.setSpecialValueText("Type one" if no_song else "Find it")
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
        wanted = self.settings["look"].get("lut") or look.NO_LUT
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
            text = timeline.GetName() if timeline else "Open a timeline in Resolve"
        except Exception:  # noqa: BLE001
            text = "Connected to Resolve"
        self._set_status(text, ok=True)
        self.undo.setEnabled(bool(self.history.records))
        self.undo.setToolTip("Put '{}' back as it was before {}".format(self.history.records[-1].name,
                                                                         self.history.label)
                             if self.history.records else "Nothing to undo")

    def _set_status(self, text: str, ok: bool) -> None:
        self.status.setText(("● " if ok else "○ ") + text)
        p = self.status.palette()
        p.setColor(QPalette.ColorRole.WindowText, QColor("#7ccf8a" if ok else "#e0b070"))
        self.status.setPalette(p)

    # ---- jobs -----------------------------------------------------------------------------------

    def options(self, name: str) -> dict:
        s = self._read_settings()
        found = dict(s.get(name, {}))
        if name == "look":
            found["lut_path"] = self.luts.get(self.lut.currentText(), "")
        return found

    def run(self, name: str) -> None:
        """Just this job, now (its row's Run)."""
        self._start([name])

    def run_ticked(self) -> None:
        """Apply: every ticked job, top to bottom."""
        names = [name for name in ORDER if self.rows[name]["tick"].isChecked()]
        if names:
            self._start(names)

    def _start(self, names: list[str]) -> None:
        if self.runner.busy:
            return
        from tools.core.work import Context
        from tools.resolve import jobs

        self.ctx = Context(self.link, self.history, log=self.runner.logged.emit,
                           progress=self.runner.moved.emit, data_dir=store.data_dir())
        chosen = [(name, self.options(name)) for name in names]
        self._busy(True)
        self._began = time.perf_counter()
        self.log("")
        self.log("— {} —".format(", ".join(TITLES[n] for n in names)), bold=True)
        self.runner.start(jobs.run(self.ctx, chosen))

    def _busy(self, busy: bool) -> None:
        self.apply.setVisible(not busy)
        self.stop.setVisible(busy)
        self.progress.setVisible(busy)
        self.progress.setValue(0)
        for name in ORDER:
            self.rows[name]["go"].setEnabled(not busy)
        self.undo.setEnabled(not busy and bool(self.history.records))
        if busy:
            self.step.setText("Starting…")
        self._ticks_changed()

    def _moved(self, fraction: float, text: str) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        self.step.setText(text)

    def _finished(self, summary: str, error) -> None:
        took = time.perf_counter() - self._began
        self._busy(False)
        if error is None:
            self.log(summary, colour="#7ccf8a")
            self.step.setText("{} ({:.1f} s)".format(summary, took))
        elif str(error) == "Stopped.":
            self.log("Stopped.")
            self.step.setText("Stopped.")
        else:
            self.log(str(error) or type(error).__name__, colour="#ff7a6b")
            self.step.setText("Not done: {}".format(error))
        self.refresh_status()

    def _stop(self) -> None:
        if self.ctx is not None:
            self.ctx.stop.set()
            self.step.setText("Stopping…")

    def _undo(self) -> None:
        if self.runner.busy:
            return
        message = self.history.undo(self.link)
        self.log(message)
        self.step.setText(message)
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
