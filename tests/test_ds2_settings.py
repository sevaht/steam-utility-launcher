from __future__ import annotations

import struct

import pytest

from steam_utility_launcher.ds2_settings import (
    MIRRORED_SETTINGS,
    PROFILE_SETTINGS,
    SettingsBlock,
    SettingsError,
)

HEADER = (4, 109, 0x1370)
LENGTH = 0x2000


def _new(block: SettingsBlock, *, terms: bool = False) -> bytearray:
    data = bytearray(LENGTH)
    block.initialize(
        data, header=HEADER, section_size=904, terms_accepted=terms
    )
    return data


def test_the_two_blocks_are_where_the_save_has_them() -> None:
    assert PROFILE_SETTINGS.start == 0x34C
    assert MIRRORED_SETTINGS.start == 0


def test_a_new_block_is_laid_out_as_the_game_lays_it_out() -> None:
    data = _new(SettingsBlock(0x100), terms=True)
    assert struct.unpack_from("<III", data, 0x100) == HEADER
    assert struct.unpack_from("<I", data, 0x100 + 0x24)[0] == 10
    assert bytes(data[0x100 + 0x28 : 0x100 + 0x2C]) == bytes([1, 1, 0, 0])
    assert struct.unpack_from("<I", data, 0x100 + 0x2C)[0] == 1
    assert struct.unpack_from("<III", data, 0x100 + 0x1390) == (13, 0x102, 904)


def test_a_new_block_changes_nothing_but_its_own_fields() -> None:
    data = _new(SettingsBlock(0))
    written = {i for i, byte in enumerate(data) if byte}
    assert written <= set(range(0x30)) | set(range(0x1390, 0x139C))
    assert len(written) == 12  # the same twelve the game's own save has


def test_the_terms_flag_is_read_and_set() -> None:
    block = SettingsBlock(0x34C)
    data = _new(block)
    assert not block.terms_accepted(data)
    block.set_terms_accepted(data, accepted=True)
    assert block.terms_accepted(data)
    assert data[0x34C + 0x29] == 1
    block.set_terms_accepted(data, accepted=False)
    assert not block.terms_accepted(data)


def test_setting_the_flag_changes_that_byte_only() -> None:
    block = SettingsBlock(0)
    data = _new(block)
    before = bytes(data)
    block.set_terms_accepted(data, accepted=True)
    assert [i for i in range(len(data)) if data[i] != before[i]] == [0x29]


def test_a_view_of_the_data_is_edited_in_place() -> None:
    block = SettingsBlock(0)
    backing = _new(block)
    block.set_terms_accepted(memoryview(backing), accepted=True)
    assert block.terms_accepted(backing)


def test_data_without_the_marker_has_no_terms_flag() -> None:
    block = SettingsBlock(0)
    data = bytearray(LENGTH)
    with pytest.raises(SettingsError, match="terms flag"):
        block.terms_accepted(data)
    with pytest.raises(SettingsError, match="terms flag"):
        block.set_terms_accepted(data, accepted=True)
    assert not any(data)  # and it changed nothing


def test_a_flag_that_is_not_a_flag_is_refused() -> None:
    block = SettingsBlock(0)
    data = _new(block)
    data[0x29] = 7
    with pytest.raises(SettingsError, match="terms flag"):
        block.terms_accepted(data)


@pytest.mark.parametrize("length", [0, 0x20, 0x2C])
def test_data_too_short_for_the_settings_is_refused(length: int) -> None:
    block = SettingsBlock(0)
    with pytest.raises(SettingsError, match="too short"):
        block.terms_accepted(bytearray(length))
    with pytest.raises(SettingsError, match="too short"):
        block.initialize(
            bytearray(length),
            header=HEADER,
            section_size=1,
            terms_accepted=False,
        )
