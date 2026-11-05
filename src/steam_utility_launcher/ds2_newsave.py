"""Makes a new Dark Souls II: Scholar of the First Sin save file.

A save from the game's first start is nearly all empty: the character slots,
and the entry after them, are not read while the slots are empty, so they are
zero here. What the game does read is in the profile (the first entry) and the
settings (the last), and that is small: a few numbers it checks, the Steam
account, the controls, and the settings with their terms-and-conditions flag.
The rest of what the game writes there is leftover memory that it ignores.

This writes what the game reads, and nothing more.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from steam_utility_launcher.ds2_controls import DEFAULT_CONTROLS, ControlTables
from steam_utility_launcher.ds2_save import SaveFile
from steam_utility_launcher.ds2_settings import (
    MIRRORED_SETTINGS,
    PROFILE_SETTINGS,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from steam_utility_launcher.ds2_controls import Controls

MAX_STEAM_ID = 2**64 - 1

# How many bytes of data each entry has: the profile, ten character slots, ten
# more of what goes with them, an entry that is not read, and the settings.
_PROFILE_LENGTH = 6788
_CHARACTER_SLOT_LENGTH = 111292
_CHARACTER_SLOT_COUNT = 10
_SLOT_EXTRA_LENGTH = 501928
_UNREAD_LENGTH = 2097152
_SETTINGS_LENGTH = 13232

# The profile's own contents, besides the controls and the settings.
_PROFILE_HEADER = struct.Struct("<III")
_PROFILE_HEADER_VALUES = (3, 102, 812)
# Seen in every new save; what they are for isn't known.
_OPTIONS_OFFSET = 0x23
_OPTIONS = bytes.fromhex(
    "32 32 01 01 01 01 01 01 01 32 32 32 00 00 01 00 32 32"
)
_STEAM_ID_OFFSET = 0x35
_CONTROL_COUNT_OFFSET = 0x48
# What follows the numbers at the start of the settings (and the size of the
# section that ends them, which the game doesn't read).
_PROFILE_SETTINGS_HEADER = (4, 109, 0x1370)
_PROFILE_SETTINGS_SECTION = 904
_MIRRORED_SETTINGS_HEADER = (4, 110, 0x1370)
_MIRRORED_SETTINGS_SECTION = 0x2000


def steam_id_text(steam_id: int) -> str:
    """How a save names its Steam account: the 64 bit id in 16 hex digits,
    which is also the name of the folder the save is in."""
    if not 0 < steam_id <= MAX_STEAM_ID:
        msg = f"not a Steam id: {steam_id}"
        raise ValueError(msg)
    return f"{steam_id:016x}"


def _profile(
    steam_id: int, controls: Controls, *, terms_accepted: bool
) -> bytearray:
    data = bytearray(_PROFILE_LENGTH)
    _PROFILE_HEADER.pack_into(data, 0, *_PROFILE_HEADER_VALUES)
    data[_OPTIONS_OFFSET : _OPTIONS_OFFSET + len(_OPTIONS)] = _OPTIONS
    id_text = steam_id_text(steam_id).encode("ascii")
    data[_STEAM_ID_OFFSET : _STEAM_ID_OFFSET + len(id_text)] = id_text
    ControlTables.create(data, controls)
    struct.pack_into(
        "<I",
        data,
        _CONTROL_COUNT_OFFSET,
        len(controls.keyboard) + len(controls.mouse),
    )
    PROFILE_SETTINGS.initialize(
        data,
        header=_PROFILE_SETTINGS_HEADER,
        section_size=_PROFILE_SETTINGS_SECTION,
        terms_accepted=terms_accepted,
    )
    return data


def _settings(*, terms_accepted: bool) -> bytearray:
    data = bytearray(_SETTINGS_LENGTH)
    MIRRORED_SETTINGS.initialize(
        data,
        header=_MIRRORED_SETTINGS_HEADER,
        section_size=_MIRRORED_SETTINGS_SECTION,
        terms_accepted=terms_accepted,
    )
    return data


def new_save(
    steam_id: int,
    *,
    controls: Controls = DEFAULT_CONTROLS,
    terms_accepted: bool = False,
    make_iv: Callable[[], bytes] | None = None,
) -> SaveFile:
    """A save for this Steam account (its 64 bit id) with these controls, as
    the game makes one but for its leftover memory. `terms_accepted` is the
    flag for the terms and conditions the game asks about on a new save."""
    entries: list[bytes | bytearray] = [
        _profile(steam_id, controls, terms_accepted=terms_accepted)
    ]
    entries += [bytes(_CHARACTER_SLOT_LENGTH)] * _CHARACTER_SLOT_COUNT
    entries += [bytes(_SLOT_EXTRA_LENGTH)] * _CHARACTER_SLOT_COUNT
    entries.append(bytes(_UNREAD_LENGTH))
    entries.append(_settings(terms_accepted=terms_accepted))
    if make_iv is None:
        return SaveFile.build(entries)
    return SaveFile.build(entries, make_iv=make_iv)
