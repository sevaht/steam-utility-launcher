"""Dark Souls II: Scholar of the First Sin's analog stick deadzone.

The game's own deadzone ignores a stick near either axis, which makes diagonal
aim awkward; the other Dark Souls games use a round one (36%). The "Center
Deadzone Mod" (Nexus Mods, DS2 mod 1200) changes that by rewriting the
function that works it out, and nothing else in the 28 MB executable: 202 bytes
at one place.

Nothing of the game is bundled. The two byte strings below are that one
function, as it is and as the mod has it; they are how this program recognizes
which one an executable has, and what it writes to switch between them. An
executable that has neither (another game version) is left alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from steam_utility_launcher.errors import UserError
from steam_utility_launcher.file_saver import FileSaver

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

GAME_EXECUTABLE = "DarkSoulsII.exe"
PATCH_OFFSET = 0xB08A1B

ORIGINAL_CODE = bytes.fromhex(
    "0f2fc6760df30f5cc2f30f5ec30f2fc6eb100f2ff0760df30f58c2f30f5ec30f"
    "2ff077030f28c6f30f114328488b4620f30f1048040f2fce760df30f5ccaf30f"
    "5ecb0f2fceeb100f2ff1760df30f58caf30f5ecb0f2ff177030f28cef30f114b"
    "2c488b4620f30f1040080f2fc6760df30f5cc2f30f5ec30f2fc6eb100f2ff076"
    "0df30f58c2f30f5ec30f2ff077030f28c6f30f114330488b4620f30f10400c0f"
    "2fc6760df30f5cc2f30f5ec30f2fc6eb100f2ff0760df30f58c2f30f5ec30f2f"
    "f077030f28c6f30f1143"
)

CENTERED_CODE = bytes.fromhex(
    "f30f104804f30f59c0f30f59c9f30f58e0f30f58e1f30f1000f30f104804f30f"
    "51e40f2fe2761af30f5ec4f30f5eccf30f5ce2f30f5ee3f30f59c4f30f59cceb"
    "12f30f10c6f30f10cef30f10ceeb044328eb09f30f10e6f30f114328f30f114b"
    "2c488b4620f30f104008f30f10480cf30f59c0f30f59c9f30f58e0f30f58e1f3"
    "0f104008f30f10480cf30f51e40f2fe2761af30f5ec4f30f5eccf30f5ce2f30f"
    "5ee3f30f59c4f30f59cceb11f30f10c6f30f10ceeb0710ceeb0710ceebf30f10"
    "e6f30f114330f30f114b"
)


class Deadzone(Enum):
    ORIGINAL = "the game's own (axial) deadzone"
    CENTERED = "the centered (round, 36%) deadzone"
    UNKNOWN = "an executable this does not recognize"


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        msg = f"Could not read {path}: {error}"
        raise UserError(msg) from error


def _code_in(data: bytes) -> bytes:
    return data[PATCH_OFFSET : PATCH_OFFSET + len(ORIGINAL_CODE)]


def deadzone_of(data: bytes) -> Deadzone:
    """Which deadzone the executable's bytes have."""
    code = _code_in(data)
    if code == ORIGINAL_CODE:
        return Deadzone.ORIGINAL
    if code == CENTERED_CODE:
        return Deadzone.CENTERED
    return Deadzone.UNKNOWN


def _with(data: bytes, code: bytes) -> bytes:
    end = PATCH_OFFSET + len(code)
    return data[:PATCH_OFFSET] + code + data[end:]


@dataclass(frozen=True)
class DeadzoneRequest:
    executable: Path
    # None to only say which it is.
    want: Deadzone | None = None
    dry_run: bool = False
    game_running: bool = False


class DeadzonePatcher:
    """Tells, or switches, the deadzone of the game's executable."""

    def __init__(self, request: DeadzoneRequest) -> None:
        self.request = request

    def run(self) -> None:
        request = self.request
        data = _read(request.executable)
        current = deadzone_of(data)
        print(f"{request.executable}: {current.value}.")
        if current is Deadzone.UNKNOWN:
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
        if request.want is Deadzone.UNKNOWN:
            msg = "Pick the original or the centered deadzone."
            raise ValueError(msg)
        if current is request.want:
            print("Already so; nothing to do.")
            return
        if request.dry_run:
            print(f"Would switch to {request.want.value}.")
            return
        if request.game_running:
            msg = "Close Dark Souls II first; the file can't be replaced while it runs."
            raise UserError(msg)
        code = (
            CENTERED_CODE
            if request.want is Deadzone.CENTERED
            else ORIGINAL_CODE
        )
        saver = FileSaver()
        try:
            # The executable is not backed up: the code it had is in this
            # module, and Steam's "verify" restores the whole file anyway.
            saver.write(request.executable, _with(data, code))
        except OSError as error:
            msg = f"Could not write {request.executable}: {error}"
            raise UserError(msg) from error
        print(f"Switched to {request.want.value}.")
