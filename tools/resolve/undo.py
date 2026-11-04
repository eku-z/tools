"""Undo, ported from ZCuts (engine/14_undo.py UndoRecord, undo_last;
build 259), much smaller: Tools never changes a timeline you made, it only
makes new ones, so taking a job back is deleting what it made and opening
the timeline it started from again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.resolve import timeline as tl
from tools.resolve.link import project_identity


@dataclass
class UndoRecord:
    label: str
    original: object  # the timeline the job started from
    project: object  # project_identity of where it was made
    link: int  # Link.generation it was made over
    made: list = field(default_factory=list)

    def made_timeline(self, timeline) -> None:
        if timeline is not None and not any(m is timeline for m in self.made):
            self.made.append(timeline)


class History:
    def __init__(self):
        self.records: list[UndoRecord] = []

    def push(self, record: UndoRecord) -> None:
        if record.made:
            self.records.append(record)
            del self.records[:-20]

    @property
    def label(self) -> str:
        return self.records[-1].label if self.records else ""

    def undo(self, link) -> str:
        """Take the last job back. Returns what happened, in words."""
        if not self.records:
            return "Nothing to undo."
        record = self.records[-1]
        project = link.project()
        if project is None:
            return "Resolve is not answering, so nothing was undone."
        if record.link != link.generation:
            self.records.clear()
            return ("Tools had to connect to Resolve again since then, and Resolve does not accept "
                    "those timelines over a new connection. Delete them in Resolve by hand.")
        if project_identity(project) != record.project:
            return "That was done in another project. Open it again to undo it."
        media_pool = project.GetMediaPool()
        names = [tl.name_of(t) for t in record.made]
        try:
            if record.original is not None:
                project.SetCurrentTimeline(record.original)
        except Exception:  # noqa: BLE001
            pass
        left = tl.delete_timelines(project, media_pool, record.made, keep=record.original)
        if left:
            record.made = [t for t in record.made if tl.name_of(t) in left]
            return "Resolve would not delete {}. Delete it in Resolve by hand.".format(", ".join(left))
        self.records.pop()
        return "Undone: deleted {}.".format(", ".join("'{}'".format(n) for n in names))
