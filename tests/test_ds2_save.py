from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher.ds2_controls import (
    Action,
    Controls,
    KeyBinding,
    MouseBinding,
)
from steam_utility_launcher.ds2_inputs import KEYS, MODIFIERS
from steam_utility_launcher.ds2_save import SaveError, SaveFile

from .ds2_save_helper import (
    PROFILE_MARKER_OFFSET,
    SETTINGS_MARKER_OFFSET,
    build_save,
    entry_bytes,
    seal,
    unseal,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SHIFT = MODIFIERS.parse("Shift")
KEYBOARD = {
    int(Action.RUN_FORWARD): KeyBinding(KEYS.parse("W")),
    int(Action.TOGGLE_MENU_LEFT): KeyBinding(KEYS.parse("Left"), SHIFT),
    int(Action.FUNCTION_1): KeyBinding(KEYS.parse("E")),
    int(Action.FUNCTION_2): KeyBinding(KEYS.parse("N")),
}
MOUSE = {
    int(Action.ATTACK_RIGHT_HAND): MouseBinding(1),
    int(Action.JUMP): MouseBinding(2, SHIFT),
}


def _save(
    *,
    terms: bool = False,
    settings_terms: bool | None = None,
    marker: bool = True,
) -> SaveFile:
    return SaveFile(
        build_save(
            KEYBOARD.items(),
            MOUSE.items(),
            terms=terms,
            settings_terms=settings_terms,
            marker=marker,
        )
    )


def test_reads_the_controls() -> None:
    controls = _save().controls
    assert controls.keyboard == KEYBOARD
    assert controls.mouse == MOUSE


def test_reads_a_save_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "DS2SOFS0000.sl2"
    path.write_bytes(_save().data)
    assert SaveFile.read(path).controls.keyboard == KEYBOARD


def test_setting_controls_changes_only_the_profile_entry() -> None:
    save = _save()
    patched = save.with_controls(
        Controls(
            keyboard={
                int(Action.FUNCTION_1): KeyBinding(KEYS.parse("P")),
                int(Action.TOGGLE_MENU_LEFT): KeyBinding(KEYS.parse("C")),
            },
            mouse={int(Action.JUMP): MouseBinding(0)},
        )
    )
    controls = patched.controls
    assert controls.keyboard[int(Action.FUNCTION_1)] == KeyBinding(
        KEYS.parse("P")
    )
    assert controls.keyboard[int(Action.TOGGLE_MENU_LEFT)] == KeyBinding(
        KEYS.parse("C")
    )
    assert controls.mouse[int(Action.JUMP)] == MouseBinding(0)
    assert controls.keyboard[int(Action.RUN_FORWARD)] == KeyBinding(
        KEYS.parse("W")
    )
    assert len(patched.data) == len(save.data)
    before, after = entry_bytes(save.data), entry_bytes(patched.data)
    assert before[0] != after[0]
    assert before[1:] == after[1:]
    # The archive's own headers are untouched too.
    assert save.data[: 64 + 32 * 23] == patched.data[: 64 + 32 * 23]


def test_a_changed_profile_is_sealed_with_a_good_checksum() -> None:
    patched = _save().with_controls(
        Controls(keyboard={int(Action.FUNCTION_1): KeyBinding(5)})
    )
    # It opens again, which checks the checksum, and decrypts to the change.
    assert patched.controls.keyboard[int(Action.FUNCTION_1)].key == 5
    profile = entry_bytes(patched.data)[0]
    assert profile[:16] == seal(profile[16:32], unseal(profile))[:16]


def test_setting_what_is_there_changes_nothing() -> None:
    save = _save()
    assert save.with_controls(save.controls).data == save.data
    assert save.with_controls(Controls()).data == save.data


def test_setting_does_not_change_the_save_it_is_done_to() -> None:
    save = _save()
    original = save.data
    save.with_controls(Controls(keyboard={26: KeyBinding(1)}))
    assert save.data == original
    assert save.controls.keyboard == KEYBOARD


def test_an_action_the_save_lacks_is_an_error() -> None:
    with pytest.raises(SaveError, match="no controls"):
        _save().with_controls(
            Controls(keyboard={int(Action.WALK): KeyBinding(1)})
        )


def test_a_save_without_the_controls_is_refused() -> None:
    data = build_save([])
    with pytest.raises(SaveError, match="keyboard controls"):
        SaveFile(data).controls  # noqa: B018


def test_a_damaged_entry_is_refused() -> None:
    data = bytearray(_save().data)
    (offset,) = struct.unpack_from("<I", data, 64 + 16)
    data[offset + 40] ^= 0xFF
    with pytest.raises(SaveError, match="checksum"):
        SaveFile(bytes(data)).controls  # noqa: B018


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"NOPE" + bytes(100),
        b"BND4" + bytes(8) + (1).to_bytes(4, "little"),
        b"BND4" + bytes(8) + (0).to_bytes(4, "little") + bytes(60),
    ],
)
def test_files_that_are_not_saves_are_refused(data: bytes) -> None:
    with pytest.raises(SaveError):
        SaveFile(data)


def test_a_truncated_save_is_refused() -> None:
    with pytest.raises(SaveError):
        SaveFile(_save().data[:-20])


def test_terms_start_not_accepted_and_can_be_accepted() -> None:
    save = _save()
    assert not save.terms_accepted
    accepted = save.with_terms_accepted()
    assert accepted.terms_accepted
    assert not save.terms_accepted
    assert not accepted.with_terms_accepted(accepted=False).terms_accepted


def test_accepting_terms_changes_the_flag_in_both_copies_and_no_more() -> None:
    save = _save()
    accepted = save.with_terms_accepted()
    before, after = entry_bytes(save.data), entry_bytes(accepted.data)
    changed = [i for i in range(len(before)) if before[i] != after[i]]
    assert changed == [0, 22]
    profile = unseal(after[0])[4:]
    settings = unseal(after[22])[4:]
    assert profile[PROFILE_MARKER_OFFSET + 5] == 1
    assert settings[SETTINGS_MARKER_OFFSET + 5] == 1
    # Nothing else in them moved, controls included.
    assert accepted.controls == save.controls


def test_accepting_terms_twice_is_the_same_as_once() -> None:
    accepted = _save().with_terms_accepted()
    assert accepted.with_terms_accepted().data == accepted.data


def test_a_save_made_accepted_reads_as_accepted() -> None:
    assert _save(terms=True).terms_accepted


def test_terms_copies_that_disagree_are_an_error() -> None:
    save = _save(terms=True, settings_terms=False)
    with pytest.raises(SaveError, match="disagree"):
        save.terms_accepted  # noqa: B018


def test_a_save_without_the_settings_block_has_no_terms_flag() -> None:
    save = _save(marker=False)
    with pytest.raises(SaveError, match="terms flag"):
        save.terms_accepted  # noqa: B018
    with pytest.raises(SaveError, match="terms flag"):
        save.with_terms_accepted()
    assert save.controls.keyboard == KEYBOARD  # the rest still works


def test_a_save_without_a_last_settings_entry_has_no_terms_flag() -> None:
    save = SaveFile(build_save(KEYBOARD.items(), entry_count=3))
    with pytest.raises(SaveError, match="no entry 22"):
        save.terms_accepted  # noqa: B018


# --- building a save


def _ivs() -> Callable[[], bytes]:
    counter = iter(range(1, 1000))
    return lambda: bytes([next(counter)]) * 16


def test_a_built_save_has_the_games_archive_layout() -> None:
    save = SaveFile.build([b"a" * 10, b"b" * 20, b"c" * 5], make_iv=_ivs())
    data = save.data
    assert data[:4] == b"BND4"
    assert struct.unpack_from("<I", data, 12)[0] == 3  # entries
    assert data[24:32] == b"14e503cb"
    (data_start,) = struct.unpack_from("<Q", data, 40)
    assert data_start % 128 == 0
    assert data_start >= 64 + 3 * 32 + 3 * 26
    names = data[64 + 3 * 32 : 64 + 3 * 32 + 3 * 26]
    assert names.decode("utf-16-le").split("\0")[:3] == [
        "USER_DATA000",
        "USER_DATA001",
        "USER_DATA002",
    ]
    first = struct.unpack_from("<I", data, 64 + 16)[0]
    assert first == data_start
    assert set(data[64 + 3 * 32 + 3 * 26 : data_start]) <= {0}


def test_a_built_save_reads_back_its_entries() -> None:
    entries = [b"x" * 7, bytes(range(40)), b"z" * 33]
    save = SaveFile.build(entries, make_iv=_ivs())
    opened = [unseal(raw)[4:] for raw in entry_bytes(save.data)]
    for entry, plain in zip(entries, opened, strict=True):
        assert plain.startswith(entry)
        assert set(plain[len(entry) :]) <= {len(plain) - len(entry)}


@pytest.mark.parametrize(
    ("length", "padding"),
    [(12, 0), (13, 15), (27, 1), (28, 0), (0, 12), (1, 11)],
)
def test_entries_are_padded_to_a_block_with_the_padding_as_the_value(
    length: int, padding: int
) -> None:
    save = SaveFile.build([bytes([7]) * length], make_iv=_ivs())
    plain = unseal(entry_bytes(save.data)[0])
    assert (len(plain)) % 16 == 0
    assert struct.unpack_from("<I", plain)[0] == length
    assert len(plain) == 4 + length + padding
    assert plain[4 + length :] == bytes([padding]) * padding


def test_each_entry_gets_its_own_iv_and_a_good_checksum() -> None:
    save = SaveFile.build([b"a", b"a", b"a"], make_iv=_ivs())
    raws = entry_bytes(save.data)
    assert len({raw[16:32] for raw in raws}) == 3
    assert raws[0][32:] != raws[1][32:]  # same data, different IVs
    assert SaveFile(save.data).data == save.data  # opens: checksums hold


def test_built_saves_use_random_ivs_unless_given() -> None:
    first = SaveFile.build([b"a" * 32])
    second = SaveFile.build([b"a" * 32])
    assert first.data != second.data
    assert unseal(entry_bytes(first.data)[0]) != b""
    assert (
        unseal(entry_bytes(first.data)[0])[4:36]
        == unseal(entry_bytes(second.data)[0])[4:36]
    )


def test_rebuilding_a_built_save_from_its_own_entries_gives_the_same_file() -> (
    None
):
    entries = [b"a" * 100, b"b" * 17, b"c" * 64]
    built = SaveFile.build(entries, make_iv=_ivs())
    ivs = iter([raw[16:32] for raw in entry_bytes(built.data)])
    datas = []
    for raw in entry_bytes(built.data):
        plain = unseal(raw)
        (length,) = struct.unpack_from("<I", plain)
        datas.append(plain[4 : 4 + length])
    assert SaveFile.build(datas, make_iv=lambda: next(ivs)).data == built.data
