"""The window, offscreen: a job run through the Qt runner, Undo, settings."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import fake_resolve as fake
import media_factory as mf
from tools.app import settings as store
from tools.resolve.link import Link

_app = []


def _qt():
    from PySide6.QtWidgets import QApplication

    from tools.app.window import apply_theme

    if not _app:
        _app.append(QApplication.instance() or QApplication([]))
        apply_theme(_app[0])
    return _app[0]


def _wait(window, seconds=30.0):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    window.runner.finished.connect(lambda *_: loop.quit())
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    if window.runner.busy:
        loop.exec()
    assert not window.runner.busy, "the job did not finish"


def _window(r):
    from tools.app.window import MainWindow

    _qt()
    window = MainWindow(link=Link(factory=lambda: (r, "")), settings=store.load(), poll=False)
    window.show()
    return window


def test_a_job_from_the_window_and_undo():
    r = fake.Resolve()
    pool = r.project.pool_item(mf.talk(), fps=25, seconds=20)
    r.project.new_timeline("Talk", 25).add_clip(pool, 0, 500)
    window = _window(r)
    window.refresh_status()
    assert "Talk" in window.status.text(), window.status.text()
    window.tabs.setCurrentIndex(0)
    window.run("silences")
    assert window.stop.isEnabled() and not window.go_silences.isEnabled()
    _wait(window)
    text = window.log_view.toPlainText()
    assert "Made 'Talk - Cut'" in text, text
    assert window.step.text().startswith("Done in"), window.step.text()
    assert window.undo.isEnabled() and window.go_silences.isEnabled()
    assert r.project.GetCurrentTimeline().GetName() == "Talk - Cut"
    window.undo.click()
    assert "Undone" in window.log_view.toPlainText()
    assert [t.name for t in r.project.timelines] == ["Talk"] and not window.undo.isEnabled()
    window.close()


def test_errors_and_stop_show_in_the_log():
    r = fake.Resolve()
    r.project.new_timeline("Empty", 25)
    window = _window(r)
    window.run("ab")
    _wait(window)
    assert "needs two different camera files" in window.log_view.toPlainText()
    assert window.step.text() == "Not done"
    pool = r.project.pool_item(mf.talk(), fps=25, seconds=20)
    r.project.current.add_clip(pool, 0, 500)
    window.run("silences")
    window._stop()  # before the sound is read
    _wait(window)
    assert window.log_view.toPlainText().rstrip().endswith("Stopped."), window.log_view.toPlainText()
    assert [t.name for t in r.project.timelines] == ["Empty"]
    window.close()


def test_settings_are_kept():
    r = fake.Resolve()
    r.project.new_timeline("Edit", 25)
    window = _window(r)
    window.s_preset.setCurrentText("Custom")
    window.s_threshold.setValue(-42)
    window.a_every.setCurrentText("12 s")
    window.m_song.setText(r"D:\Music\song.mp3")
    window.also_vertical.setChecked(True)
    window.tabs.setCurrentIndex(2)
    window.close()
    with open(os.path.join(store.data_dir(), "settings.json"), encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["silences"]["threshold_db"] == -42 and saved["ab"]["every"] == 12, saved
    again = _window(r)
    assert again.s_threshold.value() == -42 and again.s_threshold.isEnabled()
    assert again.m_song.text() == r"D:\Music\song.mp3" and again.also_vertical.isChecked()
    assert again.tabs.currentIndex() == 2
    options = again.options("silences")
    assert options["preset"] == "Custom" and options["also_vertical"] is True and options["lut_path"] == ""
    again.close()


def test_the_window_opens_without_numpy():
    """NumPy, PyAV and the jobs load after the window shows."""
    code = ("import sys; sys.path.insert(0, {!r}); import tools.app.window; "
            "print(sorted(m for m in ('numpy', 'av', 'tools.resolve.jobs') if m in sys.modules))"
            ).format(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "[]", (out.stdout, out.stderr)
