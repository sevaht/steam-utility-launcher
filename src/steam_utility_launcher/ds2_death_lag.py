"""Dark Souls II: Scholar of the First Sin slowing down with every death.

The game's handler for Steam's "stats received" callback reads every
achievement and, when all of them but the first ("The Dark Soul": earn all
achievements) are unlocked, awards that first one and tells Steam to store it,
without checking that it is already unlocked. Steam answers with the "stats
stored" callback, whose handler asks for the stats again, which brings the
first callback back. On an account that has the other 37 achievements that is
an endless loop of about 120 Steam round trips every 300 ms, and every death
(which awards an achievement, and so stores) starts one more copy of it, which
is never removed; after a dozen or so the main thread has no time left for a
frame, until the game is restarted.

The fix changes 45 bytes of that handler so that it does not award the first
achievement when it is already unlocked. The loop then ends at its first pass,
and a death causes one extra pass instead of a permanent one. Awarding the
achievement for the first time still works, and nothing else in the 28 MB
executable changes. In instructions, where `skip` is the end of the handler:

    before                           after
    cmp  [rbx+0x7c], r15b            cmp  dil, [rbx+0x7c]
    jne  skip                        jbe  skip
    test dil, dil                    mov  rax, [rbx+0x68]
    je   skip                        cmp  byte [rax+0x190], r15b
    mov  rax, [rbx+0x68]             jne  skip     <- already unlocked
    mov  byte [rbx+0x7c], 1          mov  rdi, [rax+8]
    mov  rdi, [rax+8]                cmp  [rbx+0x74], r15b
    cmp  [rbx+0x74], r15b            sete [rbx+0x7c]
    je   skip                        je   skip
    mov  [rbx+0x7c], r15b

(`dil` is "all the others are unlocked", `[rbx+0x7c]` an "awarding" flag, and
`[rax+0x190]` the first achievement's "unlocked" byte; the two flags are only
ever 0 or 1, which is what lets the first two tests become one.)

The same 45 bytes can instead be changed in memory, by a tiny dinput8.dll
(resources/ds2_death_lag_dinput8.c) next to the executable, which the game
loads for its controller input and which leaves the executable file as it was.
Wine and Proton load a DLL a program imports from the program's own folder
before their own, so that is all it takes, there as on Windows. The executable
is changed or the DLL is installed, never both.

The fix is `patch` (by `--method executable`, the default, or `--method dll`)
and `restore`; see :mod:`steam_utility_launcher.patching` for what they and
`status` answer.

Nothing of the game is bundled. The two byte strings below are that range of
the handler, as the game has it and as it is fixed; they are how this program
recognizes which one an executable has, and what it writes to switch between
them. An executable that has neither (another game version) is left alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, unique
from importlib import resources
from typing import TYPE_CHECKING

from steam_utility_launcher.errors import UserError
from steam_utility_launcher.file_saver import FileSaver
from steam_utility_launcher.patching import (
    ByteEdit,
    BytePatch,
    FixReport,
    FixTarget,
    PatchMethod,
    PatchState,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

FIX_NAME = "death-lag-fix"

# The file offset of virtual address 0x140b5e82c, in the executable's first
# code section.
PATCH_OFFSET = 0xB5DC2C

ORIGINAL_CODE = bytes.fromhex(
    "44387b7c0f85b80000004084ff0f84af000000488b4368c6437c01488b7808"
    "44387b740f849900000044887b7c"
)

FIXED_CODE = bytes.fromhex(
    "403a7b7c0f86b8000000488b43684438b8900100000f85a7000000488b7808"
    "44387b740f94437c0f8495000000"
)

# The DLL that makes the change in memory, and what it is called in the game's
# folder (the name of the DLL the game loads for controller input).
DLL_RESOURCE = "ds2_death_lag_dinput8.dll"
DLL_NAME = "dinput8.dll"


PATCH = BytePatch((ByteEdit(PATCH_OFFSET, ORIGINAL_CODE, FIXED_CODE),))


@unique
class _Dll(Enum):
    ABSENT = "absent"
    OURS = "ours"
    OTHER = "other"


def bundled_dll() -> bytes:
    """The bytes of the dinput8.dll that makes the fix in memory."""
    source = resources.files("steam_utility_launcher") / "resources"
    return (source / DLL_RESOURCE).read_bytes()


@dataclass(frozen=True, slots=True, kw_only=True)
class _Steps:
    """What a switch has to do to the game's folder."""

    write_executable: bool
    install_dll: bool
    remove_dll: bool

    @property
    def is_needed(self) -> bool:
        return self.write_executable or self.install_dll or self.remove_dll


@dataclass(frozen=True, kw_only=True)
class DeathLagFix:
    """The fix, made in the executable or by a DLL beside it.

    Attributes:
        target: The game's executable, and the terms of the run.
        method: Where `patch` makes the fix: in the executable, or, leaving
            the executable as the game has it, by the dinput8.dll. The
            other methods' files are removed, so only one is ever there.
            `status` and `restore` don't use it.
    """

    target: FixTarget
    method: PatchMethod

    @property
    def _dll_path(self) -> Path:
        return self.target.executable.with_name(DLL_NAME)

    def _dll(self) -> _Dll:
        path = self._dll_path
        if not path.exists():
            return _Dll.ABSENT
        try:
            ours = path.read_bytes() == bundled_dll()
        except OSError:
            return _Dll.OTHER
        return _Dll.OURS if ours else _Dll.OTHER

    def status(self) -> FixReport:
        """Says whether the fix is made, and where."""
        exe_state = PATCH.state_of(self.target.read())
        dll = self._dll()
        self._warn_of_other_dll(dll)
        state, method = self._current(exe_state, dll)
        return self._report(state, method, is_changed=False)

    def patch(self) -> FixReport:
        """Makes the fix by the method, taking out the other's files."""
        return self._switch(PatchState.PATCHED, self.method)

    def restore(self) -> FixReport:
        """Takes the fix out of the executable, and ours out of the folder."""
        return self._switch(PatchState.ORIGINAL, None)

    @staticmethod
    def _current(
        exe_state: PatchState, dll: _Dll
    ) -> tuple[PatchState, PatchMethod | None]:
        if exe_state is PatchState.PATCHED:
            return PatchState.PATCHED, PatchMethod.EXECUTABLE
        # The DLL changes nothing in an executable that is already fixed.
        if exe_state is PatchState.ORIGINAL and dll is _Dll.OURS:
            return PatchState.PATCHED, PatchMethod.DLL
        return exe_state, None

    def _warn_of_other_dll(self, dll: _Dll) -> None:
        if dll is _Dll.OTHER:
            logger.warning(
                "There is a %s of another make beside %s, which is left"
                " alone.",
                DLL_NAME,
                self.target.executable.name,
            )

    def _switch(
        self, want: PatchState, method: PatchMethod | None
    ) -> FixReport:
        data = self.target.read()
        exe_state = PATCH.state_of(data)
        self.target.require_known_version(exe_state)
        dll = self._dll()
        self._warn_of_other_dll(dll)
        if method is PatchMethod.DLL and dll is _Dll.OTHER:
            msg = (
                f"There is a {DLL_NAME} of another make in the game's"
                " folder; it would have to be moved away first."
            )
            raise UserError(msg)
        want_dll = method is PatchMethod.DLL
        steps = _Steps(
            write_executable=exe_state
            is not self._executable_state_for(method),
            install_dll=want_dll and dll is not _Dll.OURS,
            remove_dll=dll is _Dll.OURS and not want_dll,
        )
        if not steps.is_needed:
            logger.info("%s is already %s.", self.target.executable, want)
            return self._report(want, method, is_changed=False)
        if self.target.is_dry_run:
            logger.info("Would make %s %s.", self.target.executable, want)
            return self._report(want, method, is_changed=True)
        self.target.require_game_closed()
        try:
            self._apply(steps, data, method)
        except OSError as error:
            msg = f"Could not change the game's files: {error}"
            raise UserError(msg) from error
        logger.info("Made %s %s.", self.target.executable, want)
        return self._report(want, method, is_changed=True)

    @staticmethod
    def _executable_state_for(method: PatchMethod | None) -> PatchState:
        # The DLL makes the fix in memory, so the file is the game's own.
        if method is PatchMethod.EXECUTABLE:
            return PatchState.PATCHED
        return PatchState.ORIGINAL

    def _apply(
        self, steps: _Steps, data: bytes, method: PatchMethod | None
    ) -> None:
        saver = FileSaver()
        if steps.remove_dll:
            self._dll_path.unlink()
        if steps.write_executable:
            # The executable is not backed up: the code it had is in this
            # module, and Steam's "verify" restores the whole file anyway.
            changed = (
                PATCH.applied_to(data)
                if self._executable_state_for(method) is PatchState.PATCHED
                else PATCH.reverted_in(data)
            )
            saver.write(self.target.executable, changed)
        if steps.install_dll:
            saver.write(self._dll_path, bundled_dll())

    def _report(
        self,
        state: PatchState,
        method: PatchMethod | None,
        *,
        is_changed: bool,
    ) -> FixReport:
        return FixReport(
            fix=FIX_NAME,
            path=self.target.executable,
            state=state,
            method=method if state is PatchState.PATCHED else None,
            is_changed=is_changed,
            is_dry_run=self.target.is_dry_run,
        )
