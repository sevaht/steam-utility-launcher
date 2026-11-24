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

The fix is `patch` (the centered deadzone) and `restore` (the game's own); see
:mod:`steam_utility_launcher.patching` for what they and `status` answer.
"""

from __future__ import annotations

from steam_utility_launcher.patching import (
    ByteEdit,
    BytePatch,
    ExecutablePatcher,
    FixTarget,
)

FIX_NAME = "deadzone-fix"

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


PATCH = BytePatch((ByteEdit(PATCH_OFFSET, ORIGINAL_CODE, CENTERED_CODE),))


def deadzone_fix(target: FixTarget) -> ExecutablePatcher:
    """The centered deadzone, as a fix of `target`'s executable."""
    return ExecutablePatcher(FIX_NAME, PATCH, target)
