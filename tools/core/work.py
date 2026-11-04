"""How a job runs: on the window's thread, handing slow work to a helper.

A job is a generator. It talks to Resolve itself (always on the window's
thread, as ZCuts requires) and ``yield``s a ``Heavy`` for anything slow
(reading sound, finding beats); the runner does that on a helper thread
and sends the answer back in, or throws its error in. ``run_sync`` runs a
job straight through, for the tests; the window uses its own Qt runner.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Generator, Optional

from tools.core import Stopped


@dataclass
class Heavy:
    fn: Callable[[], Any]


Job = Generator[Heavy, Any, str]


class Context:
    """What a job may use: the link to Resolve, the undo history, the log,
    the progress bar and the Stop button. ``log`` and ``progress`` may be
    called from helper threads."""

    def __init__(self, link, history, log: Callable[[str], None] = print,
                 progress: Optional[Callable[[float, str], None]] = None, data_dir: str = ""):
        self.link = link
        self.history = history
        self.log = log
        self.progress = progress or (lambda fraction, text: None)
        self.stop = threading.Event()
        self.data_dir = data_dir

    def check(self) -> None:
        if self.stop.is_set():
            raise Stopped()

    def project(self):
        project = self.link.ensure()
        if project is None:
            raise RuntimeError(self.link.problem or "Tools cannot reach Resolve.")
        return project


def run_sync(job: Job) -> str:
    """Run a job to its end on this thread. Returns its summary."""
    value: Any = None
    error: Optional[BaseException] = None
    while True:
        try:
            item = job.throw(error) if error is not None else job.send(value)
        except StopIteration as done:
            return done.value or ""
        value, error = None, None
        try:
            value = item.fn()
        except BaseException as e:  # noqa: BLE001 - handed back to the job
            error = e
