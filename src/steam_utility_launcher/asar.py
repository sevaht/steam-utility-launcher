"""Reading one file out of an Electron `.asar` archive.

An asar is a 16-byte header (four little-endian 32-bit words, of which the
last, at offset 12, is the length of the JSON index), the JSON index, then the
files' contents. Offsets in the index count from the end of the JSON.
"""

from __future__ import annotations

import json
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_PREAMBLE = struct.Struct("<IIII")
_JSON_LENGTH_FIELD = 3
_SIZE_FIELD = 1


class AsarError(ValueError):
    """The archive is malformed, or doesn't have the file."""


def read_file(archive: Path, name: str) -> bytes:
    """The contents of `name` (like "dist/branding/logo.png") in the archive.

    Raises AsarError if it isn't a file in there, and OSError if the archive
    can't be read.
    """
    with archive.open("rb") as stream:
        preamble = stream.read(_PREAMBLE.size)
        if len(preamble) != _PREAMBLE.size:
            msg = f"{archive} is not an asar archive."
            raise AsarError(msg)
        json_length = _PREAMBLE.unpack(preamble)[_JSON_LENGTH_FIELD]
        try:
            node = json.loads(stream.read(json_length))
            for part in name.strip("/").split("/"):
                node = node["files"][part]
            offset = int(node["offset"])
            size = int(node["size"])
        except (ValueError, KeyError, TypeError) as error:
            msg = f"{archive} has no file {name}."
            raise AsarError(msg) from error
        base = _PREAMBLE.size + json_length
        # The index is padded to a multiple of four bytes: the contents start
        # at the next such boundary.
        base += -base % 4
        stream.seek(base + offset)
        content = stream.read(size)
    if len(content) != size:
        msg = f"{archive} is truncated."
        raise AsarError(msg)
    return content
