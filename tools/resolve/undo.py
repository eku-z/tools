"""Undo, ported from ZCuts (engine/14_undo.py UndoRecord, undo_last;
build 259), much smaller.

Tools changes the timeline you are working in. Before its first change it
duplicates that timeline into the "Tools backups" bin; Undo opens the
copy, deletes the changed timeline, and gives the copy the timeline's name
and place again, so everything (grades, effects, titles) is exactly as it
was. Refused when the timeline was changed by hand afterwards, which Undo
would throw away: the copy is still in the bin then.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.resolve import timeline as tl
from tools.resolve.link import project_identity


@dataclass
class UndoRecord:
    label: str
    timeline: object  # the timeline that was changed
    name: str
    project: object  # project_identity of where it was changed
    link: int  # Link.generation it was changed over
    backup: object = None  # the copy from before
    folder: object = None  # the Media Pool folder the timeline was in
    after: tuple = field(default_factory=tuple)  # tl.fingerprint once the job was done


class History:
    def __init__(self):
        self.records: list[UndoRecord] = []

    def push(self, record: UndoRecord) -> None:
        if record.backup is not None:
            self.records.append(record)
            del self.records[:-20]

    @property
    def label(self) -> str:
        return self.records[-1].label if self.records else ""

    def undo(self, link) -> str:
        """Put the timeline back as it was before the last job. Returns what
        happened, in words."""
        if not self.records:
            return "Nothing to undo."
        record = self.records[-1]
        project = link.project()
        if project is None:
            return "Resolve is not answering, so nothing was undone."
        backup_name = tl.name_of(record.backup)
        if record.link != link.generation:
            self.records.clear()
            return ("Tools had to connect to Resolve again since then, so it cannot undo. The timeline as it "
                    "was is '{}' in the Tools backups bin.".format(backup_name))
        if project_identity(project) != record.project:
            return "That was done in another project. Open it again to undo it."
        if record.after and tl.fingerprint(record.timeline) != record.after:
            self.records.pop()
            return ("'{}' was changed after Tools changed it, and Undo would throw that away, so it did "
                    "nothing. The timeline as it was before Tools is '{}' in the Tools backups bin.".format(
                        record.name, backup_name))
        media_pool = project.GetMediaPool()
        project.SetCurrentTimeline(record.backup)
        left = tl.delete_timelines(project, media_pool, [record.timeline], keep=record.backup)
        if left:
            return ("Resolve would not delete the changed '{}'. The timeline as it was is '{}' in the Tools "
                    "backups bin.".format(record.name, backup_name))
        try:
            record.backup.SetName(record.name)
        except Exception:  # noqa: BLE001
            pass
        tl.move_to(media_pool, record.backup, record.folder)
        self.records.pop()
        return "Undone: '{}' is back as it was before {}.".format(record.name, record.label)
