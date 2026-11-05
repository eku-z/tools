"""The window, offscreen: rows, Run, Apply, Undo, settings."""

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
NAMES = ("silences", "ab", "montage", "vertical", "look")


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


def _window(r, settings=None):
    from tools.app.window import MainWindow

    _qt()
    window = MainWindow(link=Link(factory=lambda: (r, "")), settings=settings or store.load(), poll=False)
    window.show()
    return window


def _talk():
    r = fake.Resolve()
    pool = r.project.pool_item(mf.talk(), fps=25, seconds=20)
    timeline = r.project.new_timeline("Talk", 25)
    timeline.add_clip(pool, 0, 500)
    return r, timeline


def test_one_job_from_its_row_and_undo():
    r, timeline = _talk()
    window = _window(r, store.load_defaults())
    window.refresh_status()
    assert window.status.text().endswith("Talk"), window.status.text()
    window.rows["silences"]["go"].click()
    assert window.stop.isVisible() and not window.apply.isVisible()
    _wait(window)
    assert window.step.text().startswith("Cut 2 silence(s) from 'Talk'"), window.step.text()
    assert len(timeline.GetItemListInTrack("video", 1)) == 3 and r.project.GetCurrentTimeline() is timeline
    assert window.undo.isEnabled() and window.apply.isVisible()
    window.undo.click()
    assert window.step.text().startswith("Undone"), window.step.text()
    assert len(r.project.GetCurrentTimeline().GetItemListInTrack("video", 1)) == 1 and not window.undo.isEnabled()
    window.close()


def test_apply_does_what_is_ticked():
    r, timeline = _talk()
    window = _window(r, store.load_defaults())
    assert not window.apply.isEnabled() and window.apply.text() == "Apply"
    window.rows["silences"]["tick"].setChecked(True)
    window.rows["vertical"]["tick"].setChecked(True)
    assert window.apply.isEnabled() and window.apply.text() == "Apply 2"
    window.apply.click()
    _wait(window)
    assert "Applied 2 job(s) to 'Talk'" in window.step.text(), window.step.text()
    assert timeline.GetSetting("timelineResolutionHeight") == "1920" and len(timeline.GetItemListInTrack("video", 1)) == 3
    window.undo.click()
    back = r.project.GetCurrentTimeline()
    assert back.GetSetting("timelineResolutionHeight") == "1080" and len(back.GetItemListInTrack("video", 1)) == 1
    window.close()


def test_errors_and_stop():
    r = fake.Resolve()
    r.project.new_timeline("Empty", 25)
    window = _window(r, store.load_defaults())
    window.run("ab")
    _wait(window)
    assert window.step.text().startswith("Not done: The A/B cut needs two"), window.step.text()
    pool = r.project.pool_item(mf.talk(), fps=25, seconds=20)
    r.project.current.add_clip(pool, 0, 500)
    window.run("silences")
    window._stop()  # before the sound is read
    _wait(window)
    assert window.step.text() == "Stopped." and len(r.project.timelines) == 1
    window.close()


def test_options_are_hidden_until_asked_and_everything_starts_off():
    r, _timeline = _talk()
    window = _window(r, store.load_defaults())
    boxes = [window.a_pauses, window.a_sync, window.m_no_song, window.m_fill, window.m_pace, window.m_mute,
             window.l_fill, window.l_upright, window.v_upright, window.s_all] + \
        [window.rows[name]["tick"] for name in NAMES]
    assert not any(box.isChecked() for box in boxes), [b.text() for b in boxes if b.isChecked()]
    assert window.lut.currentText() == "None" and window.m_lead.value() == 0
    assert not any(window.rows[name]["options"].isVisible() for name in NAMES)
    assert not window.log_view.isVisible()
    small = window.height()
    gear = [b for b in window.rows["montage"]["frame"].findChildren(type(window.log_button)) if b.text() == "⚙"][0]
    gear.click()
    _qt().processEvents()
    assert window.rows["montage"]["options"].isVisible()
    window.m_no_song.setChecked(True)
    assert not window.m_song.isEnabled() and window.m_bpm.specialValueText() == "Type one"
    gear.click()
    _qt().processEvents()
    assert not window.rows["montage"]["options"].isVisible()
    assert window.height() <= small + 2, (window.height(), small)
    window.close()


def test_settings_are_kept():
    r, _timeline = _talk()
    window = _window(r, store.load_defaults())
    window.rows["look"]["tick"].setChecked(True)
    window.s_preset.setCurrentText("Custom")
    window.s_threshold.setValue(-42)
    window.a_every.setCurrentText("every 12 s")
    window.m_song.setText(r"D:\Music\song.mp3")
    window.l_fill.setChecked(True)
    window.close()
    with open(os.path.join(store.data_dir(), "settings.json"), encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["silences"]["threshold_db"] == -42 and saved["ab"]["every"] == 12 and saved["ticked"]["look"]
    again = _window(r)
    assert again.s_threshold.value() == -42 and again.s_threshold.isEnabled()
    assert again.m_song.text() == r"D:\Music\song.mp3" and again.l_fill.isChecked()
    assert again.rows["look"]["tick"].isChecked() and again.apply.text() == "Apply 1"
    assert again.options("look")["fill"] is True and again.options("look")["lut_path"] == ""
    again.close()


def test_the_window_opens_without_numpy():
    """NumPy, PyAV and the jobs load after the window shows."""
    code = ("import sys; sys.path.insert(0, {!r}); import tools.app.window; "
            "print(sorted(m for m in ('numpy', 'av', 'tools.resolve.jobs') if m in sys.modules))"
            ).format(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "[]", (out.stdout, out.stderr)
