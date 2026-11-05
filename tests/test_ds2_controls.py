from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher.ds2_controls import (
    DEFAULT_CONTROLS,
    KEY_BIAS,
    Action,
    Controls,
    ControlsError,
    ControlTables,
    KeyBinding,
    MouseBinding,
    action_name,
    parse_action,
)
from steam_utility_launcher.ds2_inputs import KEYS, MODIFIERS, MOUSE_BUTTONS

from .ds2_save_helper import profile_data

if TYPE_CHECKING:
    from collections.abc import Callable

SHIFT = MODIFIERS.parse("Shift")
CTRL = MODIFIERS.parse("Ctrl")
KEYBOARD = {
    Action.RUN_FORWARD: KeyBinding(KEYS.parse("W")),
    Action.JUMP: KeyBinding(KEYS.parse("F")),
    Action.TOGGLE_MENU_LEFT: KeyBinding(KEYS.parse("Left"), SHIFT),
    Action.FUNCTION_1: KeyBinding(KEYS.parse("E")),
}
MOUSE = {
    Action.ATTACK_RIGHT_HAND: MouseBinding(MOUSE_BUTTONS.parse("Right click")),
    Action.SWITCH_LEFT_WEAPON: MouseBinding(
        MOUSE_BUTTONS.parse("Left click"), CTRL
    ),
}


def _data(
    keyboard: dict[Action, KeyBinding] | None = None,
    mouse: dict[Action, MouseBinding] | None = None,
) -> bytearray:
    return profile_data(
        (KEYBOARD if keyboard is None else keyboard).items(),
        (MOUSE if mouse is None else mouse).items(),
    )


def test_every_action_has_a_distinct_id_and_name() -> None:
    assert len(Action) == 38
    assert len({action.value for action in Action}) == 38
    assert not {18, 19, 20, 21} & {action.value for action in Action}


def test_actions_are_named_and_parsed_back() -> None:
    for action in Action:
        assert parse_action(action_name(action)) == action
    assert action_name(26) == "run_forward"
    assert parse_action("RUN_FORWARD") == 26
    assert parse_action("  Run_Forward ") == 26


def test_an_id_with_no_name_is_still_an_action() -> None:
    assert action_name(99) == "action_99"
    assert parse_action("action_99") == 99
    assert parse_action("ACTION_99") == 99


@pytest.mark.parametrize("bad", ["nope", "", "action_", "action_256", "26"])
def test_unknown_actions_are_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="unknown action"):
        parse_action(bad)


@pytest.mark.parametrize(
    "make",
    [
        lambda: KeyBinding(256),
        lambda: KeyBinding(-1),
        lambda: KeyBinding(0, 256),
        lambda: MouseBinding(256),
        lambda: MouseBinding(0, -1),
    ],
)
def test_bindings_outside_the_range_of_a_code_are_refused(
    make: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match="out of range"):
        make()


def test_bindings_have_a_code_whatever_they_bind() -> None:
    assert KeyBinding(5).code == 5
    assert MouseBinding(6).code == 6


def test_controls_are_empty_until_they_hold_something() -> None:
    assert not Controls()
    assert Controls(keyboard={1: KeyBinding(2)})
    assert Controls(mouse={1: MouseBinding(2)})


def test_changes_are_what_differs_or_is_missing() -> None:
    current = Controls(
        keyboard={1: KeyBinding(10), 2: KeyBinding(20)},
        mouse={1: MouseBinding(0)},
    )
    wanted = Controls(
        keyboard={1: KeyBinding(10), 2: KeyBinding(21), 3: KeyBinding(30)},
        mouse={1: MouseBinding(0, SHIFT)},
    )
    assert wanted.changes_from(current) == Controls(
        keyboard={2: KeyBinding(21), 3: KeyBinding(30)},
        mouse={1: MouseBinding(0, SHIFT)},
    )
    assert not current.changes_from(current)
    assert not Controls().changes_from(current)


def test_the_tables_are_read_as_bindings() -> None:
    controls = ControlTables(_data()).read()
    assert controls.keyboard == {
        int(action): binding for action, binding in KEYBOARD.items()
    }
    assert controls.mouse == {
        int(action): binding for action, binding in MOUSE.items()
    }


def test_the_keyboard_table_stores_keys_with_a_bias_and_the_mouse_does_not() -> (
    None
):
    data = _data()
    raw = struct.unpack_from("<III", data, 0x4C)
    assert raw == (Action.RUN_FORWARD, KEYS.parse("W") + KEY_BIAS, 0)
    mouse_start = 0x4C + 12 * len(KEYBOARD)
    raw_mouse = struct.unpack_from("<III", data, mouse_start)
    assert raw_mouse == (Action.ATTACK_RIGHT_HAND, 1, 0)


def test_a_modifier_does_not_end_the_keyboard_table() -> None:
    # The first record has one, and so does one in the middle.
    keyboard = {
        Action.RUN_FORWARD: KeyBinding(KEYS.parse("W"), SHIFT),
        Action.JUMP: KeyBinding(KEYS.parse("F")),
        Action.WALK: KeyBinding(KEYS.parse("LAlt"), SHIFT),
        Action.FUNCTION_1: KeyBinding(KEYS.parse("E")),
    }
    controls = ControlTables(_data(keyboard)).read()
    assert set(controls.keyboard) == {int(action) for action in keyboard}
    assert controls.keyboard[int(Action.WALK)].modifier == SHIFT


def test_the_mouse_table_is_optional() -> None:
    controls = ControlTables(_data(mouse={})).read()
    assert controls.mouse == {}
    assert controls.keyboard


def test_the_mouse_table_ends_where_records_stop() -> None:
    data = _data()
    # Numbers no record has, the way the real data after the table is.
    end = 0x4C + 12 * (len(KEYBOARD) + len(MOUSE))
    struct.pack_into("<III", data, end, 4202124019, 32767, 4202122064)
    struct.pack_into("<III", data, end + 12, 1, 2, 3)
    assert set(ControlTables(data).read().mouse) == {
        int(action) for action in MOUSE
    }


def test_the_mouse_table_ends_at_a_repeated_action() -> None:
    # Zeros after it would read as records of action 0, which it may have.
    mouse = {
        Action.SWITCH_SPELLS: MouseBinding(6),
        Action.JUMP: MouseBinding(2, SHIFT),
    }
    data = _data(mouse=mouse)
    end = 0x4C + 12 * (len(KEYBOARD) + len(mouse))
    data[end : end + 48] = bytes(48)
    assert set(ControlTables(data).read().mouse) == {0, int(Action.JUMP)}


def test_the_mouse_table_is_bounded() -> None:
    # Far more records than the game has, each a different action.
    data = bytearray(0x1000)
    struct.pack_into("<III", data, 0x4C, 26, KEYS.parse("W") + KEY_BIAS, 0)
    for index in range(100):
        struct.pack_into("<III", data, 0x4C + 12 * (1 + index), index, 1, 0)
    assert len(ControlTables(data).read().mouse) == 64


def test_no_keyboard_table_is_an_error() -> None:
    with pytest.raises(ControlsError, match="keyboard controls"):
        ControlTables(bytearray(0x200))


def test_an_action_twice_in_the_keyboard_table_is_an_error() -> None:
    data = profile_data(
        [(26, KeyBinding(KEYS.parse("W"))), (26, KeyBinding(KEYS.parse("S")))]
    )
    with pytest.raises(ControlsError, match="twice"):
        ControlTables(data)


def test_data_that_ends_inside_a_record_is_read_up_to_it() -> None:
    data = _data()
    controls = ControlTables(data[: 0x4C + 12 * len(KEYBOARD) + 5]).read()
    assert len(controls.keyboard) == len(KEYBOARD)
    assert controls.mouse == {}


def test_writing_sets_the_bindings_and_changes_nothing_else() -> None:
    data = _data()
    before = bytes(data)
    tables = ControlTables(data)
    tables.write(
        Controls(
            keyboard={
                int(Action.FUNCTION_1): KeyBinding(KEYS.parse("P")),
                int(Action.TOGGLE_MENU_LEFT): KeyBinding(KEYS.parse("C")),
            },
            mouse={
                int(Action.ATTACK_RIGHT_HAND): MouseBinding(
                    MOUSE_BUTTONS.parse("Mouse wheel up"), SHIFT
                )
            },
        )
    )
    changed = {i for i in range(len(data)) if data[i] != before[i]}
    # Only the key and modifier of the three records set, never their ids.
    first_record = 0x4C
    mouse_record = first_record + 12 * len(KEYBOARD)
    allowed = set()
    for record in (
        first_record + 12 * 2,  # Toggle menu left
        first_record + 12 * 3,  # Function 1
        mouse_record,  # Attack (right hand)
    ):
        allowed |= set(range(record + 4, record + 12))
    assert changed
    assert changed <= allowed
    controls = tables.read()
    assert controls.keyboard[int(Action.FUNCTION_1)] == KeyBinding(
        KEYS.parse("P")
    )
    assert controls.keyboard[int(Action.TOGGLE_MENU_LEFT)] == KeyBinding(
        KEYS.parse("C")
    )  # the Shift was cleared
    assert controls.mouse[int(Action.ATTACK_RIGHT_HAND)] == MouseBinding(
        6, SHIFT
    )
    untouched = (
        ControlTables(bytearray(before)).read().keyboard[int(Action.JUMP)]
    )
    assert controls.keyboard[int(Action.JUMP)] == untouched


def test_reading_after_writing_gives_what_was_written() -> None:
    tables = ControlTables(_data())
    tables.write(Controls(keyboard={26: KeyBinding(KEYS.parse("S"))}))
    assert tables.read().keyboard[26] == KeyBinding(KEYS.parse("S"))
    tables.write(Controls(keyboard={26: KeyBinding(KEYS.parse("W"))}))
    assert tables.read().keyboard[26] == KeyBinding(KEYS.parse("W"))


def test_writing_edits_a_view_of_the_data_in_place() -> None:
    backing = _data()
    ControlTables(memoryview(backing)[:]).write(
        Controls(keyboard={26: KeyBinding(KEYS.parse("S"))})
    )
    assert ControlTables(backing).read().keyboard[26].key == KEYS.parse("S")


def test_writing_an_action_the_data_lacks_is_an_error_and_changes_nothing() -> (
    None
):
    data = _data()
    before = bytes(data)
    with pytest.raises(ControlsError, match=r"run_left.*attack_left_hand"):
        ControlTables(data).write(
            Controls(
                keyboard={
                    26: KeyBinding(1),
                    int(Action.RUN_LEFT): KeyBinding(2),
                },
                mouse={int(Action.ATTACK_LEFT_HAND): MouseBinding(0)},
            )
        )
    assert bytes(data) == before


# --- the game's defaults, and laying out the tables


def test_the_defaults_are_the_games_own() -> None:
    keyboard, mouse = DEFAULT_CONTROLS.keyboard, DEFAULT_CONTROLS.mouse
    assert len(keyboard) == 38
    assert len(mouse) == 16
    assert set(keyboard) == {int(action) for action in Action}
    assert keyboard[int(Action.RUN_FORWARD)] == KeyBinding(KEYS.parse("W"))
    assert keyboard[int(Action.FUNCTION_1)] == KeyBinding(KEYS.parse("E"))
    assert keyboard[int(Action.FUNCTION_2)] == KeyBinding(KEYS.parse("N"))
    assert keyboard[int(Action.TOGGLE_MENU_LEFT)] == KeyBinding(
        KEYS.parse("Left"), SHIFT
    )
    assert mouse[int(Action.JUMP)] == MouseBinding(2, SHIFT)
    assert mouse[int(Action.SWITCH_RIGHT_WEAPON)] == MouseBinding(1, CTRL)
    assert mouse[int(Action.OPEN_START_MENU)] == MouseBinding(13)


def test_the_defaults_are_in_the_order_the_game_stores_them() -> None:
    assert list(DEFAULT_CONTROLS.keyboard)[:3] == [26, 27, 28]
    assert list(DEFAULT_CONTROLS.keyboard)[-4:] == [37, 38, 39, 40]
    assert list(DEFAULT_CONTROLS.mouse)[:2] == [13, 10]


def _empty(length: int = 0x1000) -> bytearray:
    return bytearray(length)


def test_the_tables_are_laid_out_and_read_back() -> None:
    data = _empty()
    tables = ControlTables.create(data, DEFAULT_CONTROLS)
    assert tables.read() == DEFAULT_CONTROLS
    assert ControlTables(data).read() == DEFAULT_CONTROLS


def test_laying_out_the_tables_writes_only_the_records() -> None:
    data = _empty()
    data[-8:] = b"leftover"
    ControlTables.create(data, DEFAULT_CONTROLS)
    end = 0x4C + 12 * (38 + 16)
    assert not any(data[:0x4C])
    assert not any(data[end:-8])
    assert data[-8:] == b"leftover"
    assert struct.unpack_from("<III", data, 0x4C)[0] == Action.RUN_FORWARD


def test_laid_out_tables_can_then_be_edited() -> None:
    tables = ControlTables.create(_empty(), DEFAULT_CONTROLS)
    tables.write(Controls(keyboard={39: KeyBinding(KEYS.parse("P"))}))
    assert tables.read().keyboard[39] == KeyBinding(KEYS.parse("P"))


def test_laying_out_tables_with_no_room_is_an_error() -> None:
    with pytest.raises(ControlsError, match="no room"):
        ControlTables.create(_empty(0x100), DEFAULT_CONTROLS)


def test_controls_that_would_not_read_back_are_refused() -> None:
    # A mouse code as big as a key's would be taken for one, so it can't be
    # told from the end of the keyboard table.
    odd = Controls(
        keyboard={26: KeyBinding(KEYS.parse("W"))},
        mouse={13: MouseBinding(KEY_BIAS)},
    )
    with pytest.raises(ControlsError, match="read back"):
        ControlTables.create(_empty(), odd)


def test_tables_without_a_mouse_table_are_laid_out_too() -> None:
    only_keyboard = Controls(keyboard=DEFAULT_CONTROLS.keyboard)
    tables = ControlTables.create(_empty(), only_keyboard)
    assert tables.read() == only_keyboard
