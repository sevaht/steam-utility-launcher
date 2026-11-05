"""The settings structure in Dark Souls II: Scholar of the First Sin's save.

The profile has one inside its data, and the last entry is one (it is the
same, with a bigger section at its end). It starts with three numbers, has a
marker and some flags (one of which is whether the terms and conditions have
been accepted), and ends in a section that is a header (a type, some flags and
a size) and then the section's data, which is not needed.

Everything here is an offset in the data of an entry.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_HEADER = struct.Struct("<III")
_MARKER_OFFSET = 0x24
_MARKER = 10
_FLAGS_OFFSET = _MARKER_OFFSET + 4
_TERMS_FLAG_INDEX = 1
# A number after the flags, which is 1 in a new save.
_AFTER_FLAGS_OFFSET = _MARKER_OFFSET + 8
_SECTION_OFFSET = 0x1390
_SECTION_HEADER = struct.Struct("<III")
_SECTION_TYPE = 13
_SECTION_FLAGS = 0x102


class SettingsError(ValueError):
    """The data does not have the settings where they are expected."""


@dataclass(frozen=True)
class SettingsBlock:
    """Where a settings structure starts in an entry's data."""

    start: int

    def _position(self, data: bytearray | memoryview, offset: int) -> int:
        position = self.start + offset
        if position + struct.calcsize("<I") > len(data):
            msg = "the data is too short for the settings"
            raise SettingsError(msg)
        return position

    def terms_accepted(self, data: bytearray | memoryview) -> bool:
        """Whether the terms and conditions have been accepted."""
        marker = self._position(data, _MARKER_OFFSET)
        flag = self._position(data, _FLAGS_OFFSET + _TERMS_FLAG_INDEX)
        (found,) = struct.unpack_from("<I", data, marker)
        if found != _MARKER or data[flag] > 1:
            msg = "could not find the terms flag in the settings"
            raise SettingsError(msg)
        return bool(data[flag])

    def set_terms_accepted(
        self, data: bytearray | memoryview, *, accepted: bool
    ) -> None:
        self.terms_accepted(data)  # that they are where they should be
        data[self._position(data, _FLAGS_OFFSET + _TERMS_FLAG_INDEX)] = int(
            accepted
        )

    def initialize(
        self,
        data: bytearray | memoryview,
        *,
        header: tuple[int, int, int],
        section_size: int,
        terms_accepted: bool,
    ) -> None:
        """Writes a new settings structure, over data that is zero."""
        position = self._position
        _HEADER.pack_into(data, position(data, 0), *header)
        struct.pack_into("<I", data, position(data, _MARKER_OFFSET), _MARKER)
        struct.pack_into(
            "<4B",
            data,
            position(data, _FLAGS_OFFSET),
            1,
            int(terms_accepted),
            0,
            0,
        )
        struct.pack_into("<I", data, position(data, _AFTER_FLAGS_OFFSET), 1)
        _SECTION_HEADER.pack_into(
            data,
            position(data, _SECTION_OFFSET),
            _SECTION_TYPE,
            _SECTION_FLAGS,
            section_size,
        )


# In the profile, and the last entry's.
PROFILE_SETTINGS = SettingsBlock(start=0x34C)
MIRRORED_SETTINGS = SettingsBlock(start=0)
