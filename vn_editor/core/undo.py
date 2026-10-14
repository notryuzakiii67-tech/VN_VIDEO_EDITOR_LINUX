"""Undo/redo.

Two mechanisms work together:

* :class:`Command` — small, explicit edits (move clip, set parameter, split).
* :class:`SnapshotCommand` — a whole-project before/after image, used by
  anything that touches a lot of state at once (templates, auto-captions,
  ripple operations).  Projects are small JSON documents, so this is cheap and
  removes a whole class of "undo left something behind" bugs.

A :class:`History` groups commands so a single user gesture is one undo step.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .model import Project


class Command:
    """Base class.  Sub-classes implement ``redo``/``undo``."""

    label: str = "Edit"
    mergeable: bool = False

    def redo(self, project: Project) -> None:      # pragma: no cover - abstract
        raise NotImplementedError

    def undo(self, project: Project) -> None:      # pragma: no cover - abstract
        raise NotImplementedError

    def can_merge(self, other: "Command") -> bool:
        return False

    def merge(self, other: "Command") -> None:     # pragma: no cover
        pass


class SnapshotCommand(Command):
    """Whole-project transaction."""

    coalesce: str = ""

    def __init__(self, label: str, before: Dict[str, Any],
                 after: Dict[str, Any], coalesce: str = "") -> None:
        self.label = label
        self.before = before
        self.after = after
        self.coalesce = coalesce

    def redo(self, project: Project) -> None:
        _apply_snapshot(project, self.after)

    def undo(self, project: Project) -> None:
        _apply_snapshot(project, self.before)


def _apply_snapshot(project: Project, snap: Dict[str, Any]) -> None:
    """Restore state in place (callers hold a reference to ``project``)."""
    restored = Project.from_dict(snap)
    project.name = restored.name
    project.settings = restored.settings
    project.tracks = restored.tracks
    project.assets = restored.assets
    project.markers = restored.markers
    project.beats = restored.beats
    project.beat_asset_id = restored.beat_asset_id
    project.editor_state = restored.editor_state
    project.modified = restored.modified
    project.id = restored.id


class FuncCommand(Command):
    """Convenience command built from two closures."""

    def __init__(self, label: str, do: Callable[[Project], None],
                 undo: Callable[[Project], None], merge_key: str = "") -> None:
        self.label = label
        self._do = do
        self._undo = undo
        self.merge_key = merge_key
        self.mergeable = bool(merge_key)
        self._stamp = time.time()

    def redo(self, project: Project) -> None:
        self._do(project)

    def undo(self, project: Project) -> None:
        self._undo(project)

    def can_merge(self, other: Command) -> bool:
        return (isinstance(other, FuncCommand)
                and bool(self.merge_key)
                and self.merge_key == other.merge_key)

    def merge(self, other: Command) -> None:
        assert isinstance(other, FuncCommand)
        self._do = other._do
        self._stamp = time.time()


class History:
    """Undo stack with grouping and dirty tracking."""

    def __init__(self, project: Project, limit: int = 250) -> None:
        self.project = project
        self.limit = limit
        self._undo: List[Command] = []
        self._redo: List[Command] = []
        self._group: Optional[List[Command]] = None
        self._saved_index = 0
        self._depth = 0

    # -- state ------------------------------------------------------------- #
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    @property
    def is_dirty(self) -> bool:
        return len(self._undo) != self._saved_index

    def mark_saved(self) -> None:
        self._saved_index = len(self._undo)

    def clear(self, project: Optional[Project] = None) -> None:
        if project is not None:
            self.project = project
        self._undo.clear()
        self._redo.clear()
        self._saved_index = 0

    # -- grouping ---------------------------------------------------------- #
    def begin(self, label: str = "Edit") -> None:
        self._depth += 1
        if self._depth == 1:
            self._group = []
            self._group_label = label

    def commit(self, label: Optional[str] = None) -> None:
        """Close the innermost group, pushing it as one undo step."""
        self._depth = max(0, self._depth - 1)
        if self._depth != 0 or self._group is None:
            return
        cmds = self._group
        self._group = None
        if not cmds:
            return
        if len(cmds) == 1:
            self.push(cmds[0], _already_applied=True)
        else:
            label = label or self._group_label
            # coalesce: re-apply everything through one snapshot is not needed
            # because the commands already ran; wrap them in a composite.
            self.push(CompositeCommand(label or "Edit", cmds),
                      _already_applied=True)

    def __enter__(self) -> "History":
        self.begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self._depth = max(0, self._depth - 1)
            if self._depth == 0:
                self._group = None
        return False

    # -- pushing ----------------------------------------------------------- #
    def push(self, command: Command, _already_applied: bool = False) -> None:
        if self._group is not None and not _already_applied:
            self._group.append(command)
        if not _already_applied:
            command.redo(self.project)
            self._record(command)
        else:
            self._record(command)

    def _record(self, command: Command) -> None:
        if command.mergeable and self._undo and self._undo[-1].can_merge(command):
            top = self._undo[-1]
            top.merge(command)
            self._redo.clear()
            return
        self._undo.append(command)
        if len(self._undo) > self.limit:
            self._undo.pop(0)
            self._saved_index = max(0, self._saved_index - 1)
        self._redo.clear()

    def record_applied(self, command: Command) -> None:
        """Register a command whose effect was already performed."""
        if self._group is not None:
            self._group.append(command)
        else:
            self._record(command)

    # -- transactions ------------------------------------------------------ #
    def snapshot(self) -> Dict[str, Any]:
        return self.project.snapshot()

    def end_coalescing(self) -> None:
        """Close an open group (alias of :meth:`commit`, for gesture ends)."""
        self.commit()

    def transaction(self, label: str = "Edit", fn=None,
                    coalesce: str = "") -> Optional["Transaction"]:
        """Two calling styles, both one undo step:

        * context manager:  ``with history.transaction("Trim"): ...``
        * helper:           ``history.transaction("Trim", lambda p: ...)``

        ``coalesce`` marks the step as mergeable with an immediately following
        step of the same key (used for drags and slider gestures).
        """
        if fn is None:
            return Transaction(self, label)
        before = self.snapshot()
        fn(self.project)
        after = self.snapshot()
        if after != before:
            cmd = SnapshotCommand(label, before, after)
            if coalesce:
                self._push_coalesced(cmd, coalesce)
            else:
                self.record_applied(cmd)
        return None

    def _push_coalesced(self, cmd: Command, key: str) -> None:
        """Merge consecutive steps tagged with the same coalesce key."""
        if self._group is not None:
            # inside an explicit group: keep separate entries
            self.record_applied(cmd)
            return
        top = self._undo[-1] if self._undo else None
        if isinstance(top, SnapshotCommand) and getattr(top, "coalesce", "") == key:
            top.after = cmd.after
            top.label = cmd.label
            self._redo.clear()
            return
        cmd.coalesce = key
        self.record_applied(cmd)

    # -- undo/redo --------------------------------------------------------- #
    def undo(self) -> bool:
        if not self._undo:
            return False
        cmd = self._undo.pop()
        try:
            cmd.undo(self.project)
        except Exception:
            self._undo.append(cmd)
            raise
        self._redo.append(cmd)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        cmd = self._redo.pop()
        try:
            cmd.redo(self.project)
        except Exception:
            self._redo.append(cmd)
            raise
        if not cmd.mergeable or not (self._undo and self._undo[-1].can_merge(cmd)):
            self._undo.append(cmd)
        else:
            self._undo[-1].merge(cmd)
        return True

    def labels(self) -> List[str]:
        return [c.label for c in self._undo]


class CompositeCommand(Command):
    def __init__(self, label: str, commands: List[Command]) -> None:
        self.label = label
        self.commands = commands

    def redo(self, project: Project) -> None:
        for c in self.commands:
            c.redo(project)

    def undo(self, project: Project) -> None:
        for c in reversed(self.commands):
            c.undo(project)


class Transaction:
    """Context manager capturing before/after project snapshots."""

    def __init__(self, history: History, label: str) -> None:
        self.history = history
        self.label = label
        self.before: Dict[str, Any] = {}

    def __enter__(self) -> "Transaction":
        self.before = self.history.snapshot()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            after = self.history.snapshot()
            if after != self.before:
                self.history.record_applied(
                    SnapshotCommand(self.label, self.before, after))
        return False
