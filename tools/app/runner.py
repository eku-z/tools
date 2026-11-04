"""Running a job in the window: its Resolve calls on the window's thread,
each ``Heavy`` it yields on a helper thread (see tools/core/work.py)."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal


class Runner(QObject):
    logged = Signal(str)
    moved = Signal(float, str)
    finished = Signal(str, object)  # summary, error (None when it ended well)
    _resume = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.job = None
        self._resume.connect(self._step, Qt.ConnectionType.QueuedConnection)

    @property
    def busy(self) -> bool:
        return self.job is not None

    def start(self, job) -> None:
        self.job = job
        self._step(None, None)

    def _step(self, value, error) -> None:
        try:
            item = self.job.throw(error) if error is not None else self.job.send(value)
        except StopIteration as done:
            self.job = None
            self.finished.emit(done.value or "", None)
            return
        except BaseException as failure:  # noqa: BLE001 - reported in the window
            self.job = None
            self.finished.emit("", failure)
            return
        threading.Thread(target=self._work, args=(item,), daemon=True).start()

    def _work(self, item) -> None:
        try:
            value = item.fn()
        except BaseException as failure:  # noqa: BLE001 - handed back to the job
            self._resume.emit(None, failure)
        else:
            self._resume.emit(value, None)
