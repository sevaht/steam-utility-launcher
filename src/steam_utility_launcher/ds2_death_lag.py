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

Nothing of the game is bundled. The two byte strings below are that range of
the handler, as the game has it and as it is fixed; they are how this program
recognizes which one an executable has, and what it writes to switch between
them. An executable that has neither (another game version) is left alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from typing import TYPE_CHECKING

from steam_utility_launcher.errors import UserError
from steam_utility_launcher.file_saver import FileSaver

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

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


class DeathLag(Enum):
    ORIGINAL = (
        "the game's own achievement handling (the one that can slow the"
        " game down with every death)"
    )
    FIXED = (
        "the fixed achievement handling, in the executable (no slowdown with"
        " deaths)"
    )
    DLL = (
        "the fixed achievement handling, made by the dinput8.dll in the"
        " game's folder (no slowdown with deaths)"
    )
    UNKNOWN = "an executable this does not recognize"


class _Dll(Enum):
    ABSENT = "absent"
    OURS = "ours"
    OTHER = "other"


def bundled_dll() -> bytes:
    source = resources.files("steam_utility_launcher") / "resources"
    return (source / DLL_RESOURCE).read_bytes()


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        msg = f"Could not read {path}: {error}"
        raise UserError(msg) from error


def _code_in(data: bytes) -> bytes:
    return data[PATCH_OFFSET : PATCH_OFFSET + len(ORIGINAL_CODE)]


def death_lag_of(data: bytes) -> DeathLag:
    """Which handling the executable's bytes have."""
    code = _code_in(data)
    if code == ORIGINAL_CODE:
        return DeathLag.ORIGINAL
    if code == FIXED_CODE:
        return DeathLag.FIXED
    return DeathLag.UNKNOWN


def _with(data: bytes, code: bytes) -> bytes:
    end = PATCH_OFFSET + len(code)
    return data[:PATCH_OFFSET] + code + data[end:]


def _dll_in(path: Path) -> _Dll:
    if not path.exists():
        return _Dll.ABSENT
    try:
        ours = path.read_bytes() == bundled_dll()
    except OSError:
        return _Dll.OTHER
    return _Dll.OURS if ours else _Dll.OTHER


@dataclass(frozen=True)
class DeathLagRequest:
    executable: Path
    # None to only say which it is.
    want: DeathLag | None = None
    dry_run: bool = False
    game_running: bool = False


class DeathLagPatcher:
    """Tells, or switches, the achievement handling of the game: changed in
    its executable, by a DLL, or the game's own."""

    def __init__(self, request: DeathLagRequest) -> None:
        self.request = request

    def run(self) -> None:
        request = self.request
        dll_path = request.executable.with_name(DLL_NAME)
        data = _read(request.executable)
        in_exe = death_lag_of(data)
        dll = _dll_in(dll_path)
        # The DLL changes nothing in an executable that is already fixed.
        current = (
            DeathLag.DLL
            if dll is _Dll.OURS and in_exe is DeathLag.ORIGINAL
            else in_exe
        )
        print(f"{request.executable}: {current.value}.")
        if dll is _Dll.OTHER:
            print(
                f"There is a {DLL_NAME} of another make in that folder, which"
                " is left alone."
            )
        if in_exe is DeathLag.UNKNOWN:
            msg = (
                "This is not a game version the patch is known for, so it is"
                " left alone. After a game update, verify the game files in"
                " Steam."
            )
            if request.want is not None:
                raise UserError(msg)
            return
        if request.want is None:
            return
        self._check_wanted(dll)
        want_code = (
            FIXED_CODE if request.want is DeathLag.FIXED else ORIGINAL_CODE
        )
        stale_dll = dll is _Dll.OURS and request.want is not DeathLag.DLL
        if current is request.want and not stale_dll:
            print("Already so; nothing to do.")
            return
        if request.dry_run:
            print(f"Would switch to {request.want.value}.")
            return
        if request.game_running:
            msg = (
                "Close Dark Souls II first; its files can't be replaced while"
                " it runs."
            )
            raise UserError(msg)
        try:
            self._switch(data, dll, dll_path, want_code)
        except OSError as error:
            msg = f"Could not change the game's files: {error}"
            raise UserError(msg) from error
        print(f"Switched to {request.want.value}.")

    def _check_wanted(self, dll: _Dll) -> None:
        want = self.request.want
        if want is DeathLag.UNKNOWN:
            msg = "Pick the original, the fixed or the DLL handling."
            raise ValueError(msg)
        if want is DeathLag.DLL and dll is _Dll.OTHER:
            msg = (
                f"There is a {DLL_NAME} of another make in the game's"
                " folder; it would have to be moved away first."
            )
            raise UserError(msg)

    def _switch(
        self, data: bytes, dll: _Dll, dll_path: Path, want_code: bytes
    ) -> None:
        request = self.request
        saver = FileSaver()
        if request.want is DeathLag.DLL:
            # The executable is not backed up: the code it had is in this
            # module, and Steam's "verify" restores the whole file anyway.
            if _code_in(data) != want_code:
                saver.write(request.executable, _with(data, want_code))
            if dll is not _Dll.OURS:
                saver.write(dll_path, bundled_dll())
            return
        if dll is _Dll.OURS:
            dll_path.unlink()
        if _code_in(data) != want_code:
            saver.write(request.executable, _with(data, want_code))
