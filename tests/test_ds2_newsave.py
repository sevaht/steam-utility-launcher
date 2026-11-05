from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher.ds2_controls import (
    DEFAULT_CONTROLS,
    Action,
    Controls,
    ControlsError,
    KeyBinding,
    MouseBinding,
)
from steam_utility_launcher.ds2_inputs import KEYS
from steam_utility_launcher.ds2_newsave import new_save, steam_id_text

from .ds2_save_helper import entry_bytes, unseal

if TYPE_CHECKING:
    from steam_utility_launcher.ds2_save import SaveFile

STEAM_ID = 76561197969290206
FOLDER = "011000010089b3de"


def _data(save: SaveFile, entry: int) -> bytes:
    """An entry's data, without its length field and padding."""
    plain = unseal(entry_bytes(save.data)[entry])
    (length,) = struct.unpack_from("<I", plain)
    return plain[4 : 4 + length]


def test_a_steam_id_is_named_by_its_16_hex_digits() -> None:
    assert steam_id_text(STEAM_ID) == FOLDER
    assert steam_id_text(1) == "0000000000000001"
    assert steam_id_text(2**64 - 1) == "ffffffffffffffff"


@pytest.mark.parametrize("bad", [0, -1, 2**64])
def test_something_that_is_not_a_steam_id_is_refused(bad: int) -> None:
    with pytest.raises(ValueError, match="not a Steam id"):
        steam_id_text(bad)
    with pytest.raises(ValueError, match="not a Steam id"):
        new_save(bad)


def test_a_new_save_is_the_size_of_the_games() -> None:
    assert len(new_save(STEAM_ID).data) == 8251680


def test_a_new_save_has_the_games_entries() -> None:
    save = new_save(STEAM_ID)
    lengths = [len(_data(save, index)) for index in range(23)]
    assert lengths == [6788, *[111292] * 10, *[501928] * 10, 2097152, 13232]


def test_the_character_slots_and_the_entry_after_them_are_empty() -> None:
    save = new_save(STEAM_ID)
    assert all(not any(_data(save, index)) for index in range(1, 22))


def test_a_new_save_has_the_games_default_controls() -> None:
    assert new_save(STEAM_ID).controls == DEFAULT_CONTROLS


def test_a_new_save_can_start_with_other_controls() -> None:
    controls = Controls(
        keyboard={
            **DEFAULT_CONTROLS.keyboard,
            int(Action.FUNCTION_1): KeyBinding(KEYS.parse("P")),
        },
        mouse=DEFAULT_CONTROLS.mouse,
    )
    assert new_save(STEAM_ID, controls=controls).controls == controls


def test_controls_that_cannot_be_laid_out_are_refused() -> None:
    odd = Controls(
        keyboard=DEFAULT_CONTROLS.keyboard, mouse={13: MouseBinding(0x50)}
    )
    with pytest.raises(ControlsError):
        new_save(STEAM_ID, controls=odd)


def test_the_terms_start_unaccepted_and_can_be_accepted() -> None:
    assert not new_save(STEAM_ID).terms_accepted
    assert new_save(STEAM_ID, terms_accepted=True).terms_accepted


def test_the_profile_holds_the_steam_account_the_controls_and_their_count() -> (
    None
):
    profile = _data(new_save(STEAM_ID), 0)
    assert struct.unpack_from("<III", profile) == (3, 102, 812)
    assert profile[0x35:0x45] == FOLDER.encode()
    assert struct.unpack_from("<I", profile, 0x48)[0] == 38 + 16
    assert struct.unpack_from("<III", profile, 0x4C)[0] == Action.RUN_FORWARD


def test_the_profile_holds_the_settings_and_so_does_the_last_entry() -> None:
    save = new_save(STEAM_ID, terms_accepted=True)
    profile, settings = _data(save, 0), _data(save, 22)
    assert struct.unpack_from("<III", profile, 0x34C) == (4, 109, 0x1370)
    assert struct.unpack_from("<III", settings, 0) == (4, 110, 0x1370)
    for data, start in ((profile, 0x34C), (settings, 0)):
        assert struct.unpack_from("<I", data, start + 0x24)[0] == 10
        assert data[start + 0x28 : start + 0x2C] == bytes([1, 1, 0, 0])
    assert struct.unpack_from("<III", profile, 0x34C + 0x1390) == (
        13,
        0x102,
        904,
    )
    assert struct.unpack_from("<III", settings, 0x1390) == (13, 0x102, 0x2000)


def test_only_what_the_game_reads_is_written() -> None:
    save = new_save(STEAM_ID, terms_accepted=True)
    profile_written = sum(1 for byte in _data(save, 0) if byte)
    settings_written = sum(1 for byte in _data(save, 22) if byte)
    assert settings_written == 12  # as in the game's own save
    unaccepted = new_save(STEAM_ID)
    assert sum(1 for byte in _data(unaccepted, 22) if byte) == 11
    assert profile_written < 1100  # tables, the account, and a few numbers


def test_a_save_for_another_account_differs_in_its_account_only() -> None:
    other = 76561197960265729  # a different Steam id
    first, second = new_save(STEAM_ID), new_save(other)
    for index in range(23):
        a, b = _data(first, index), _data(second, index)
        differing = [i for i in range(len(a)) if a[i] != b[i]]
        if index == 0:
            assert differing
            assert all(0x35 <= i < 0x45 for i in differing)
        else:
            assert not differing


def test_the_ivs_can_be_chosen_to_make_a_save_repeatable() -> None:
    def ivs() -> object:
        counter = iter(range(1, 100))
        return lambda: bytes([next(counter)]) * 16

    first = new_save(STEAM_ID, make_iv=ivs())  # type: ignore[arg-type]
    second = new_save(STEAM_ID, make_iv=ivs())  # type: ignore[arg-type]
    assert first.data == second.data
    assert first.data != new_save(STEAM_ID).data


def test_a_new_save_can_be_edited_like_any_other() -> None:
    save = new_save(STEAM_ID).with_controls(
        Controls(
            keyboard={int(Action.FUNCTION_1): KeyBinding(KEYS.parse("P"))}
        )
    )
    assert save.controls.keyboard[int(Action.FUNCTION_1)] == KeyBinding(
        KEYS.parse("P")
    )
    assert save.with_terms_accepted().terms_accepted
