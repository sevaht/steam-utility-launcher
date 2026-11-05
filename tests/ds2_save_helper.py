"""Builds Dark Souls II saves in the real layout, for tests: a BND4 archive
whose profile has the keyboard and mouse tables and the settings, and whose
last entry mirrors the settings."""

from __future__ import annotations

import hashlib
import struct
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from steam_utility_launcher.ds2_controls import KEY_BIAS, KEYBOARD_TABLE_START

if TYPE_CHECKING:
    from collections.abc import Iterable

    from steam_utility_launcher.ds2_controls import KeyBinding, MouseBinding

KEY = bytes.fromhex("599f9b699640a55236ee2d70835ec744")
PROFILE_DATA_LENGTH = 0x400
PROFILE_MARKER_OFFSET = 0x370
SETTINGS_MARKER_OFFSET = 0x24
SETTINGS_DATA_LENGTH = 0x40
ENTRY_COUNT = 23
MARKER = bytes([10, 0, 0, 0])
# What follows the last record: not one (neither table accepts it).
_NOT_A_RECORD = struct.pack("<III", 0xFFFFFFFF, 0, 0)


def seal(iv: bytes, plain: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES128(KEY), modes.CBC(iv)).encryptor()
    body = iv + encryptor.update(plain) + encryptor.finalize()
    return hashlib.md5(body).digest() + body  # noqa: S324


def unseal(raw: bytes) -> bytes:
    """The decrypted bytes of an entry (length field, data, padding)."""
    iv = raw[16:32]
    decryptor = Cipher(algorithms.AES128(KEY), modes.CBC(iv)).decryptor()
    return decryptor.update(raw[32:])


def _flags(*, terms: bool) -> bytes:
    return bytes([1, int(terms), 0, 0])


def profile_data(
    keyboard: Iterable[tuple[int, KeyBinding]],
    mouse: Iterable[tuple[int, MouseBinding]] = (),
    *,
    terms: bool = False,
    marker: bool = True,
) -> bytearray:
    """A profile's data: the keyboard table where it starts, the mouse table
    right after it, and the settings block with the terms flag."""
    data = bytearray(PROFILE_DATA_LENGTH)
    offset = KEYBOARD_TABLE_START
    for action, key_binding in keyboard:
        struct.pack_into(
            "<III",
            data,
            offset,
            action,
            key_binding.key + KEY_BIAS,
            key_binding.modifier,
        )
        offset += 12
    for action, mouse_binding in mouse:
        struct.pack_into(
            "<III",
            data,
            offset,
            action,
            mouse_binding.button,
            mouse_binding.modifier,
        )
        offset += 12
    data[offset : offset + 12] = _NOT_A_RECORD
    if marker:
        data[PROFILE_MARKER_OFFSET : PROFILE_MARKER_OFFSET + 4] = MARKER
    flags = PROFILE_MARKER_OFFSET + 4
    data[flags : flags + 4] = _flags(terms=terms)
    return data


def settings_data(*, terms: bool = False) -> bytearray:
    data = bytearray(SETTINGS_DATA_LENGTH)
    data[SETTINGS_MARKER_OFFSET : SETTINGS_MARKER_OFFSET + 4] = MARKER
    flags = SETTINGS_MARKER_OFFSET + 4
    data[flags : flags + 4] = _flags(terms=terms)
    return data


def _plain(data: bytes) -> bytes:
    """An entry's decrypted bytes: the length field, the data, padding."""
    padding = (-(len(data) + 4)) % 16
    return struct.pack("<I", len(data)) + data + bytes(padding)


def build_save(  # noqa: PLR0913
    keyboard: Iterable[tuple[int, KeyBinding]],
    mouse: Iterable[tuple[int, MouseBinding]] = (),
    *,
    terms: bool = False,
    settings_terms: bool | None = None,
    marker: bool = True,
    entry_count: int = ENTRY_COUNT,
) -> bytes:
    """A save: the profile, other entries, and the settings as the last. The
    settings' terms flag matches the profile's unless `settings_terms` says."""
    datas: list[bytes | bytearray] = [
        profile_data(keyboard, mouse, terms=terms, marker=marker)
    ]
    datas += [bytes([index]) * 64 for index in range(1, entry_count - 1)]
    if entry_count > 1:
        mirrored = terms if settings_terms is None else settings_terms
        datas.append(settings_data(terms=mirrored))
    sealed = [
        seal(bytes([index + 1]) * 16, _plain(bytes(data)))
        for index, data in enumerate(datas)
    ]
    header = bytearray(64)
    header[:4] = b"BND4"
    struct.pack_into("<I", header, 12, len(sealed))
    entry_headers = bytearray()
    offset = 64 + 32 * len(sealed)
    for blob in sealed:
        entry_headers += struct.pack(
            "<IIQIIQ", 0x40, 0xFFFFFFFF, len(blob), offset, 0, 0
        )
        offset += len(blob)
    return bytes(header) + bytes(entry_headers) + b"".join(sealed)


def entry_bytes(data: bytes) -> list[bytes]:
    """The raw bytes of each entry in a save made by build_save."""
    (count,) = struct.unpack_from("<I", data, 12)
    entries = []
    for index in range(count):
        header = 64 + 32 * index
        (size,) = struct.unpack_from("<Q", data, header + 8)
        (offset,) = struct.unpack_from("<I", data, header + 16)
        entries.append(data[offset : offset + size])
    return entries
