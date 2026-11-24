"""Known changes to a game's files: recognizing them, applying and undoing them.

A fix is a set of byte edits at fixed offsets of a game's executable. Each edit
knows the bytes the game has and the bytes the fix writes, so the same data
tells which one a file has, applies the fix, and takes it back out. Nothing of
the game is bundled, only the few bytes that are changed.

Every fix is used the same way, by `status`, `patch` and `restore`, and each
answers with a :class:`FixReport`, which is what the commands print: a single
word on standard output (see :class:`PatchState`), or one line of JSON, so a
script can read it. Anything meant for a person is logged, not printed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum, unique
from itertools import pairwise
from typing import TYPE_CHECKING, Protocol

from steam_utility_launcher.errors import UserError
from steam_utility_launcher.file_saver import FileSaver

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


@unique
class PatchState(StrEnum):
    """What a file has of a fix; the value is the word the commands print."""

    # None of the fix's edits are in the file; it is as the game has it.
    ORIGINAL = "original"
    # All of the fix's edits are in the file.
    PATCHED = "patched"
    # Some of the fix's edits are in the file, the rest are as the game has
    # them. Patching finishes the fix, and restoring takes it out.
    PARTIAL = "partial"
    # Where an edit is, the file has neither version of it: another version
    # of the game. It is never written.
    UNRECOGNIZED = "unrecognized"


@unique
class PatchMethod(StrEnum):
    """Where a fix that has more than one way of being made is made."""

    EXECUTABLE = "executable"
    DLL = "dll"


@unique
class FixAction(StrEnum):
    """What a command does with a fix; the value is the word that asks for it."""

    STATUS = "status"
    PATCH = "patch"
    RESTORE = "restore"

    def run(self, fix: Fix) -> FixReport:
        """Does it to `fix`."""
        match self:
            case FixAction.STATUS:
                return fix.status()
            case FixAction.PATCH:
                return fix.patch()
            case FixAction.RESTORE:
                return fix.restore()


@dataclass(frozen=True, slots=True)
class ByteEdit:
    """One change to a run of bytes at a fixed offset of a file.

    Attributes:
        offset: Where in the file the run starts.
        original: The bytes the game has there.
        patched: The bytes the fix writes there; as many as `original`.
    """

    offset: int
    original: bytes
    patched: bytes

    def __post_init__(self) -> None:
        if len(self.original) != len(self.patched):
            msg = (
                f"The edit at {self.offset:#x} changes {len(self.original)}"
                f" bytes into {len(self.patched)}; an edit does not move"
                " anything."
            )
            raise ValueError(msg)
        if self.original == self.patched:
            msg = f"The edit at {self.offset:#x} changes nothing."
            raise ValueError(msg)

    @property
    def end(self) -> int:
        """The offset just past the run."""
        return self.offset + len(self.original)

    def state_in(self, data: bytes) -> PatchState:
        """Which version of the run `data` has: the original, the patched or
        neither."""
        run = data[self.offset : self.end]
        if run == self.original:
            return PatchState.ORIGINAL
        if run == self.patched:
            return PatchState.PATCHED
        return PatchState.UNRECOGNIZED


@dataclass(frozen=True, slots=True)
class BytePatch:
    """The byte edits that make up one fix of a file."""

    edits: tuple[ByteEdit, ...]

    def __post_init__(self) -> None:
        if not self.edits:
            msg = "A patch needs at least one edit."
            raise ValueError(msg)
        ordered = sorted(self.edits, key=lambda edit: edit.offset)
        for before, after in pairwise(ordered):
            if before.end > after.offset:
                msg = (
                    f"The edits at {before.offset:#x} and {after.offset:#x}"
                    " overlap."
                )
                raise ValueError(msg)

    def state_of(self, data: bytes) -> PatchState:
        """Which version of the fix the file's bytes have."""
        states = {edit.state_in(data) for edit in self.edits}
        if PatchState.UNRECOGNIZED in states:
            return PatchState.UNRECOGNIZED
        if len(states) > 1:
            return PatchState.PARTIAL
        return next(iter(states))

    def applied_to(self, data: bytes) -> bytes:
        """The file's bytes with every edit made."""
        return self._replaced(data, patched=True)

    def reverted_in(self, data: bytes) -> bytes:
        """The file's bytes with every edit taken out."""
        return self._replaced(data, patched=False)

    def _replaced(self, data: bytes, *, patched: bool) -> bytes:
        result = bytearray(data)
        for edit in self.edits:
            result[edit.offset : edit.end] = (
                edit.patched if patched else edit.original
            )
        return bytes(result)


@dataclass(frozen=True, slots=True, kw_only=True)
class FixReport:
    """What a command found or did, which is what it prints.

    Attributes:
        fix: The fix's name, as the command names it.
        path: The executable the fix is for.
        state: What the file has now (or, in a dry run, would have after).
        method: Where the fix is made, if it has more than one way and is
            made; otherwise None.
        is_changed: Whether files were changed (in a dry run, would be).
        is_dry_run: Whether nothing was written, whatever the rest says.
    """

    fix: str
    path: Path
    state: PatchState
    method: PatchMethod | None = None
    is_changed: bool = False
    is_dry_run: bool = False

    def as_text(self) -> str:
        """The state's word, and nothing else."""
        return str(self.state)

    def as_json(self) -> str:
        """One line of JSON: the same facts as the fields."""
        return json.dumps(
            {
                "fix": self.fix,
                "path": str(self.path),
                "state": str(self.state),
                "method": None if self.method is None else str(self.method),
                "changed": self.is_changed,
                "dry_run": self.is_dry_run,
            }
        )


class Fix(Protocol):
    """A fix of a game's files, which can be asked about and switched."""

    def status(self) -> FixReport:
        """Says what the files have of the fix; changes nothing."""
        ...

    def patch(self) -> FixReport:
        """Makes the fix. Raises UserError if it can't be made."""
        ...

    def restore(self) -> FixReport:
        """Takes the fix out, back to the game's own. Raises UserError if it
        can't be."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class FixTarget:
    """The game executable a fix is for, and the terms of the run.

    Attributes:
        executable: The game's executable.
        game_name: What the game is called in messages.
        is_dry_run: Say what would change, without changing anything.
        is_game_running: Whether the game is running, which keeps its
            files from being replaced.
    """

    executable: Path
    game_name: str
    is_dry_run: bool = False
    is_game_running: bool = False

    def read(self) -> bytes:
        """The executable's bytes."""
        try:
            return self.executable.read_bytes()
        except OSError as error:
            msg = f"Could not read {self.executable}: {error}"
            raise UserError(msg) from error

    def require_known_version(self, state: PatchState) -> None:
        """Raises UserError if `state` is of a game version the fix is not
        known for."""
        if state is PatchState.UNRECOGNIZED:
            msg = (
                f"{self.executable} is not a game version the fix is known"
                " for, so it is left alone. After a game update, verify the"
                " game files in Steam."
            )
            raise UserError(msg)

    def require_game_closed(self) -> None:
        """Raises UserError if files can't be written because the game is
        running."""
        if self.is_game_running:
            msg = (
                f"Close {self.game_name} first; its files can't be replaced"
                " while it runs."
            )
            raise UserError(msg)


@dataclass(frozen=True)
class ExecutablePatcher:
    """A fix made by editing the game's executable alone.

    The executable is not backed up: its original bytes are the patch's own,
    so `restore` writes them back, and Steam's "verify integrity of game
    files" restores the whole file anyway.
    """

    name: str
    byte_patch: BytePatch
    target: FixTarget

    def status(self) -> FixReport:
        """Says what the executable has of the fix."""
        state = self.byte_patch.state_of(self.target.read())
        return self._report(state, is_changed=False)

    def patch(self) -> FixReport:
        """Makes every edit of the fix in the executable."""
        return self._switch(PatchState.PATCHED)

    def restore(self) -> FixReport:
        """Writes the game's own bytes back."""
        return self._switch(PatchState.ORIGINAL)

    def _switch(self, want: PatchState) -> FixReport:
        data = self.target.read()
        state = self.byte_patch.state_of(data)
        self.target.require_known_version(state)
        if state is want:
            logger.info("%s is already %s.", self.target.executable, want)
            return self._report(want, is_changed=False)
        if self.target.is_dry_run:
            logger.info("Would make %s %s.", self.target.executable, want)
            return self._report(want, is_changed=True)
        self.target.require_game_closed()
        changed = (
            self.byte_patch.applied_to(data)
            if want is PatchState.PATCHED
            else self.byte_patch.reverted_in(data)
        )
        try:
            FileSaver().write(self.target.executable, changed)
        except OSError as error:
            msg = f"Could not write {self.target.executable}: {error}"
            raise UserError(msg) from error
        logger.info("Made %s %s.", self.target.executable, want)
        return self._report(want, is_changed=True)

    def _report(self, state: PatchState, *, is_changed: bool) -> FixReport:
        return FixReport(
            fix=self.name,
            path=self.target.executable,
            state=state,
            is_changed=is_changed,
            is_dry_run=self.target.is_dry_run,
        )
