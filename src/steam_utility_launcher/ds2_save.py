"""Dark Souls II: Scholar of the First Sin's save file (DS2SOFS0000.sl2).

The file is a BND4 archive of entries. Each entry is a 16 byte MD5 of what
follows, a 16 byte IV, and the data encrypted with AES-128-CBC; once
decrypted, that is a 4 byte length and the data, then padding. The key is the
one community save editors (SL2Bonfire, souls_givifier) use.

The first entry is the profile, which holds the controls (see ds2_controls)
and some settings. The settings are mirrored in the last entry.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from steam_utility_launcher.ds2_controls import ControlsError, ControlTables
from steam_utility_launcher.ds2_settings import (
    MIRRORED_SETTINGS,
    PROFILE_SETTINGS,
    SettingsBlock,
    SettingsError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from steam_utility_launcher.ds2_controls import Controls

_KEY = bytes.fromhex("599f9b699640a55236ee2d70835ec744")
_ARCHIVE_IDENTIFIER = b"BND4"
_ARCHIVE_HEADER_LENGTH = 64
_ENTRY_HEADER_LENGTH = 32
_ENTRY_COUNT_OFFSET = 12
_ENTRY_SIZE_OFFSET = 8
_ENTRY_DATA_OFFSET_OFFSET = 16
_CHECKSUM_LENGTH = 16
_IV_LENGTH = 16
_BLOCK_LENGTH = 16
_LENGTH_FIELD = struct.Struct("<I")
# How the game lays out its archive: what its header holds that is not a count
# or a size, and what each entry's header and name are like.
_ARCHIVE_HEADER = struct.Struct("<4sIIIQ8sQQQ8x")
_ARCHIVE_FLAGS = 0x10000
_ARCHIVE_VERSION = b"14e503cb"
_ARCHIVE_FORMAT = 0x2001
_ENTRY_HEADER = struct.Struct("<IIQIIQ")
_ENTRY_FLAGS = 0x50
_ENTRY_NO_ID = 0xFFFFFFFF
_NAME_FORMAT = "USER_DATA{:03d}"
_DATA_ALIGNMENT = 128

PROFILE_ENTRY = 0
# The settings' mirror.
SETTINGS_ENTRY = 22


class SaveError(RuntimeError):
    pass


# The profile's settings and the last entry's, which are the same.
_SETTINGS = (
    (PROFILE_ENTRY, PROFILE_SETTINGS),
    (SETTINGS_ENTRY, MIRRORED_SETTINGS),
)


@dataclass(frozen=True)
class _Entry:
    offset: int
    size: int


class _Opened:
    """An entry decrypted: its IV, and its length field, data and padding."""

    def __init__(self, iv: bytes, plain: bytearray) -> None:
        self.iv = iv
        self.plain = plain

    @property
    def data(self) -> memoryview:
        """Just the data, as a writable view of the decrypted bytes."""
        (length,) = _LENGTH_FIELD.unpack_from(self.plain)
        start = _LENGTH_FIELD.size
        if start + length > len(self.plain):
            msg = "an entry's length is more than it holds"
            raise SaveError(msg)
        return memoryview(self.plain)[start : start + length]


def _entries(data: bytes) -> list[_Entry]:
    if (
        len(data) < _ARCHIVE_HEADER_LENGTH
        or data[: len(_ARCHIVE_IDENTIFIER)] != _ARCHIVE_IDENTIFIER
    ):
        msg = "not a Dark Souls II save file (no BND4 archive)"
        raise SaveError(msg)
    (count,) = struct.unpack_from("<I", data, _ENTRY_COUNT_OFFSET)
    entries = []
    for index in range(count):
        header = _ARCHIVE_HEADER_LENGTH + index * _ENTRY_HEADER_LENGTH
        if header + _ENTRY_HEADER_LENGTH > len(data):
            msg = "the save file is truncated"
            raise SaveError(msg)
        (size,) = struct.unpack_from("<Q", data, header + _ENTRY_SIZE_OFFSET)
        (offset,) = struct.unpack_from(
            "<I", data, header + _ENTRY_DATA_OFFSET_OFFSET
        )
        encrypted = size - _CHECKSUM_LENGTH - _IV_LENGTH
        if (
            offset + size > len(data)
            or encrypted < _BLOCK_LENGTH
            or encrypted % _BLOCK_LENGTH
        ):
            msg = f"save file entry {index} is not valid"
            raise SaveError(msg)
        entries.append(_Entry(offset=offset, size=size))
    if len(entries) <= PROFILE_ENTRY:
        msg = "the save file has no entries"
        raise SaveError(msg)
    return entries


def _open(data: bytes, entry: _Entry) -> _Opened:
    raw = data[entry.offset : entry.offset + entry.size]
    checksum, body = raw[:_CHECKSUM_LENGTH], raw[_CHECKSUM_LENGTH:]
    if hashlib.md5(body).digest() != checksum:  # noqa: S324
        msg = "an entry's checksum is wrong; the save file is damaged"
        raise SaveError(msg)
    iv = body[:_IV_LENGTH]
    decryptor = Cipher(algorithms.AES128(_KEY), modes.CBC(iv)).decryptor()
    plain = decryptor.update(body[_IV_LENGTH:]) + decryptor.finalize()
    return _Opened(iv, bytearray(plain))


def _seal(opened: _Opened) -> bytes:
    encryptor = Cipher(
        algorithms.AES128(_KEY), modes.CBC(opened.iv)
    ).encryptor()
    body = opened.iv + encryptor.update(bytes(opened.plain))
    body += encryptor.finalize()
    return hashlib.md5(body).digest() + body  # noqa: S324


def _edit_entry(
    data: bytes, entry: _Entry, edit: Callable[[memoryview], None]
) -> bytes:
    """The file with one entry's data edited by `edit` and the entry sealed
    again, and the rest of the file as it was."""
    opened = _open(data, entry)
    edit(opened.data)
    sealed = _seal(opened)
    if len(sealed) != entry.size:
        msg = "the edited entry changed size"
        raise SaveError(msg)
    return data[: entry.offset] + sealed + data[entry.offset + entry.size :]


def _random_iv() -> bytes:
    return os.urandom(_IV_LENGTH)


def _plain(data: bytes | bytearray) -> bytearray:
    """What is encrypted for an entry's data: its length, the data, and
    padding up to a whole block, which holds the number of bytes it is (and is
    left out if the data fills the blocks)."""
    padding = -(_LENGTH_FIELD.size + len(data)) % _BLOCK_LENGTH
    return bytearray(
        _LENGTH_FIELD.pack(len(data)) + data + bytes([padding]) * padding
    )


class SaveFile:
    """A save file's bytes. Nothing here changes them: what changes
    something gives the changed save back."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._entries = _entries(data)

    @classmethod
    def build(
        cls,
        entries: Sequence[bytes | bytearray],
        *,
        make_iv: Callable[[], bytes] = _random_iv,
    ) -> SaveFile:
        """A save with these entries' data (the first is the profile), laid out
        as the game does. `make_iv` gives each entry's IV."""
        names_offset = _ARCHIVE_HEADER_LENGTH + _ENTRY_HEADER_LENGTH * len(
            entries
        )
        names = [
            _NAME_FORMAT.format(index).encode("utf-16-le") + b"\0\0"
            for index in range(len(entries))
        ]
        data_offset = -(
            -(names_offset + sum(map(len, names))) // _DATA_ALIGNMENT
        )
        data_offset *= _DATA_ALIGNMENT
        sealed = [_seal(_Opened(make_iv(), _plain(data))) for data in entries]
        headers = []
        name_offset, offset = names_offset, data_offset
        for name, blob in zip(names, sealed, strict=True):
            headers.append(
                _ENTRY_HEADER.pack(
                    _ENTRY_FLAGS,
                    _ENTRY_NO_ID,
                    len(blob),
                    offset,
                    name_offset,
                    0,
                )
            )
            name_offset += len(name)
            offset += len(blob)
        archive_header = _ARCHIVE_HEADER.pack(
            _ARCHIVE_IDENTIFIER,
            0,
            _ARCHIVE_FLAGS,
            len(entries),
            _ARCHIVE_HEADER_LENGTH,
            _ARCHIVE_VERSION,
            _ENTRY_HEADER_LENGTH,
            data_offset,
            _ARCHIVE_FORMAT,
        )
        head = archive_header + b"".join(headers) + b"".join(names)
        return cls(head.ljust(data_offset, b"\0") + b"".join(sealed))

    @classmethod
    def read(cls, path: Path) -> SaveFile:
        return cls(path.read_bytes())

    @property
    def data(self) -> bytes:
        return self._data

    def _entry(self, index: int) -> _Entry:
        if index >= len(self._entries):
            msg = f"the save file has no entry {index}"
            raise SaveError(msg)
        return self._entries[index]

    def _profile_data(self, index: int = PROFILE_ENTRY) -> memoryview:
        return _open(self._data, self._entry(index)).data

    @property
    def controls(self) -> Controls:
        """Every action's keyboard and mouse bindings."""
        try:
            return ControlTables(self._profile_data()).read()
        except ControlsError as error:
            raise SaveError(str(error)) from error

    def with_controls(self, changes: Controls) -> SaveFile:
        """The save with these actions' bindings changed. The ones it doesn't
        name, and everything else in the file, are left as they are."""

        def edit(data: memoryview) -> None:
            ControlTables(data).write(changes)

        try:
            result = SaveFile(
                _edit_entry(self._data, self._entry(PROFILE_ENTRY), edit)
            )
            if changes.changes_from(result.controls):
                msg = "the edited save did not read back as intended"
                raise SaveError(msg)
        except ControlsError as error:
            raise SaveError(str(error)) from error
        return result

    @property
    def terms_accepted(self) -> bool:
        """Whether the terms and conditions the game asks for on a new save
        have been accepted."""
        try:
            states = {
                block.terms_accepted(self._profile_data(entry))
                for entry, block in _SETTINGS
            }
        except SettingsError as error:
            raise SaveError(str(error)) from error
        if len(states) != 1:
            msg = "the save file's two copies of the terms flag disagree"
            raise SaveError(msg)
        return states.pop()

    def with_terms_accepted(self, *, accepted: bool = True) -> SaveFile:
        data = self._data
        for entry, block in _SETTINGS:

            def edit(view: memoryview, block: SettingsBlock = block) -> None:
                block.set_terms_accepted(view, accepted=accepted)

            try:
                # The entries are the same size after an edit, so they stay put.
                data = _edit_entry(data, self._entry(entry), edit)
            except SettingsError as error:
                raise SaveError(str(error)) from error
        result = SaveFile(data)
        if result.terms_accepted != accepted:
            msg = "the edited save did not read back as intended"
            raise SaveError(msg)
        return result
