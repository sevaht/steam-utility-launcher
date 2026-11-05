"""Dark Souls II: Scholar of the First Sin's controls, as its save keeps them.

The profile holds two tables of 12 byte records, `[action][code][modifier]`
(three little-endian 32 bit numbers), one after the other: the keyboard's and
the mouse's. Both are keyed by the same action ids. A keyboard record's code is
a key's code plus `KEY_BIAS`; a mouse record's is the button's code as it is.
The modifier is what is held with it (see ds2_inputs), or 0 for none.

The keyboard table is what the game has for every action, so it ends at a code
too small to be a key's. The mouse table comes straight after it and holds the
actions that can be used with the mouse.
"""

from __future__ import annotations

import enum
import re
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from steam_utility_launcher.ds2_inputs import KEYS, MODIFIERS, MOUSE_BUTTONS

if TYPE_CHECKING:
    from collections.abc import Mapping

# Added to a key's code to give what the keyboard table stores.
KEY_BIAS = 0x45
# Where the keyboard table starts in the profile's data.
KEYBOARD_TABLE_START = 0x4C

_RECORD = struct.Struct("<III")
# What ends the tables when there is no mouse table to end them: not a record.
_END_OF_TABLES = (0xFFFFFFFF, 0, 0)
_MAX_FIELD = 0xFF
_MAX_MOUSE_RECORDS = 64
_GENERIC_ACTION = re.compile(r"action_(\d+)")


class ControlsError(ValueError):
    """The data does not have the controls where they are expected."""


class Action(enum.IntEnum):
    """What can be bound, by the id the game stores it under. The comments are
    the game's names for them. The ids missing here (18 to 21) are not in the
    tables."""

    SWITCH_SPELLS = 0  # Switch spells
    SWITCH_ITEMS = 1  # Switch items
    SWITCH_LEFT_WEAPON = 2  # Switch left weapon
    SWITCH_RIGHT_WEAPON = 3  # Switch right weapon
    OPEN_START_MENU = 4  # Open Start Menu
    OPEN_GESTURE_MENU = 5  # Open Gesture Menu
    ATTACK_LEFT_HAND = 6  # Attack (left hand)
    ATTACK_RIGHT_HAND = 7  # Attack (right hand)
    STRONG_ATTACK_LEFT_HAND = 8  # Strong attack (left hand)
    STRONG_ATTACK_RIGHT_HAND = 9  # Strong attack (right hand)
    JUMP = 10  # Jump
    RESET_CAMERA = 11  # Reset camera/Target Lock/Release
    INTERACT = 12  # Interact
    DASH_BACKSTEP_ROLL = 13  # Dash/Backstep/Roll
    USE_ITEM = 14  # Use item
    WIELD_RIGHT_WEAPON_TWO_HANDED = 15  # Wield right weapon two-handed
    CONFIRM = 16  # Confirm
    CANCEL = 17  # Cancel
    TILT_CAMERA_UP = 22  # Tilt camera up
    TILT_CAMERA_DOWN = 23  # Tilt camera down
    TILT_CAMERA_LEFT = 24  # Tilt camera left/Next target (left)
    TILT_CAMERA_RIGHT = 25  # Tilt camera right/Next target (right)
    RUN_FORWARD = 26  # Run (forward)
    RUN_BACKWARD = 27  # Run (backward)
    RUN_LEFT = 28  # Run (left)
    RUN_RIGHT = 29  # Run (right)
    AUTO_DASH = 30  # Auto-dash
    AUTO_WALK = 31  # Auto-walk
    LOCK_GUARD = 32  # Lock guard
    MOVE_CURSOR_UP = 33  # Move cursor (up)
    MOVE_CURSOR_DOWN = 34  # Move cursor (down)
    MOVE_CURSOR_LEFT = 35  # Move cursor (left)
    MOVE_CURSOR_RIGHT = 36  # Move cursor (right)
    TOGGLE_MENU_LEFT = 37  # Toggle menu (left)
    TOGGLE_MENU_RIGHT = 38  # Toggle menu (right)
    FUNCTION_1 = 39  # Function 1
    FUNCTION_2 = 40  # Function 2
    WALK = 41  # Walk


def action_name(action: int) -> str:
    """The name used for an action id (`action_<id>` if it has none here)."""
    try:
        return Action(action).name.lower()
    except ValueError:
        return f"action_{action}"


def parse_action(text: str) -> int:
    """The id of an action from its name, or `action_<id>`."""
    member = Action.__members__.get(text.strip().upper())
    if member is not None:
        return int(member)
    generic = _GENERIC_ACTION.fullmatch(text.strip().lower())
    if generic and int(generic[1]) <= _MAX_FIELD:
        return int(generic[1])
    msg = f"unknown action: {text!r}"
    raise ValueError(msg)


def _checked_field(name: str, value: int) -> None:
    if not 0 <= value <= _MAX_FIELD:
        msg = f"{name} out of range: {value}"
        raise ValueError(msg)


@dataclass(frozen=True)
class KeyBinding:
    """A key (its code in ds2_inputs.KEYS) held with a modifier, or with none
    (0)."""

    key: int
    modifier: int = 0

    def __post_init__(self) -> None:
        _checked_field("key", self.key)
        _checked_field("modifier", self.modifier)

    @property
    def code(self) -> int:
        return self.key


@dataclass(frozen=True)
class MouseBinding:
    """A mouse button (its code in ds2_inputs.MOUSE_BUTTONS) held with a
    modifier, or with none (0)."""

    button: int
    modifier: int = 0

    def __post_init__(self) -> None:
        _checked_field("button", self.button)
        _checked_field("modifier", self.modifier)

    @property
    def code(self) -> int:
        return self.button


@dataclass(frozen=True)
class Controls:
    """What actions are set to, by action id; any of them, not every one."""

    keyboard: Mapping[int, KeyBinding] = field(default_factory=dict)
    mouse: Mapping[int, MouseBinding] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.keyboard or self.mouse)

    def changes_from(self, current: Controls) -> Controls:
        """What of these differs from `current`, or is not in it."""
        return Controls(
            keyboard={
                action: binding
                for action, binding in self.keyboard.items()
                if current.keyboard.get(action) != binding
            },
            mouse={
                action: binding
                for action, binding in self.mouse.items()
                if current.mouse.get(action) != binding
            },
        )


def _key(
    action: Action, key: str, modifier: str = "None"
) -> tuple[int, KeyBinding]:
    return action, KeyBinding(KEYS.parse(key), MODIFIERS.parse(modifier))


def _button(
    action: Action, button: str, modifier: str = "None"
) -> tuple[int, MouseBinding]:
    return action, MouseBinding(
        MOUSE_BUTTONS.parse(button), MODIFIERS.parse(modifier)
    )


# What a new save has, in the order the game stores them.
DEFAULT_CONTROLS = Controls(
    keyboard=dict(
        [
            _key(Action.RUN_FORWARD, "W"),
            _key(Action.RUN_BACKWARD, "S"),
            _key(Action.RUN_LEFT, "A"),
            _key(Action.RUN_RIGHT, "D"),
            _key(Action.TILT_CAMERA_UP, "I"),
            _key(Action.TILT_CAMERA_DOWN, "K"),
            _key(Action.TILT_CAMERA_RIGHT, "L"),
            _key(Action.TILT_CAMERA_LEFT, "J"),
            _key(Action.WALK, "LAlt"),
            _key(Action.DASH_BACKSTEP_ROLL, "Space"),
            _key(Action.JUMP, "F"),
            _key(Action.AUTO_WALK, "Z"),
            _key(Action.AUTO_DASH, "X"),
            _key(Action.LOCK_GUARD, "M"),
            _key(Action.RESET_CAMERA, "O"),
            _key(Action.SWITCH_SPELLS, "Up"),
            _key(Action.SWITCH_ITEMS, "Down"),
            _key(Action.SWITCH_RIGHT_WEAPON, "Right"),
            _key(Action.SWITCH_LEFT_WEAPON, "Left"),
            _key(Action.ATTACK_RIGHT_HAND, "H"),
            _key(Action.STRONG_ATTACK_RIGHT_HAND, "G"),
            _key(Action.ATTACK_LEFT_HAND, "U"),
            _key(Action.STRONG_ATTACK_LEFT_HAND, "Y"),
            _key(Action.USE_ITEM, "E"),
            _key(Action.INTERACT, "Enter"),
            _key(Action.WIELD_RIGHT_WEAPON_TWO_HANDED, "N"),
            _key(Action.OPEN_START_MENU, "Esc"),
            _key(Action.OPEN_GESTURE_MENU, "T"),
            _key(Action.MOVE_CURSOR_UP, "Up"),
            _key(Action.MOVE_CURSOR_DOWN, "Down"),
            _key(Action.MOVE_CURSOR_RIGHT, "Right"),
            _key(Action.MOVE_CURSOR_LEFT, "Left"),
            _key(Action.CONFIRM, "Enter"),
            _key(Action.CANCEL, "Backspace"),
            _key(Action.TOGGLE_MENU_LEFT, "Left", "Shift"),
            _key(Action.TOGGLE_MENU_RIGHT, "Right", "Shift"),
            _key(Action.FUNCTION_1, "E"),
            _key(Action.FUNCTION_2, "N"),
        ]
    ),
    mouse=dict(
        [
            _button(Action.DASH_BACKSTEP_ROLL, "Nothing"),
            _button(Action.JUMP, "Click mouse wheel", "Shift"),
            _button(Action.RESET_CAMERA, "Click mouse wheel"),
            _button(Action.SWITCH_SPELLS, "Mouse wheel up"),
            _button(Action.SWITCH_ITEMS, "Mouse wheel down"),
            _button(Action.SWITCH_RIGHT_WEAPON, "Right click", "Ctrl"),
            _button(Action.SWITCH_LEFT_WEAPON, "Left click", "Ctrl"),
            _button(Action.ATTACK_RIGHT_HAND, "Right click"),
            _button(Action.STRONG_ATTACK_RIGHT_HAND, "Double right click"),
            _button(Action.ATTACK_LEFT_HAND, "Left click"),
            _button(Action.STRONG_ATTACK_LEFT_HAND, "Double left click"),
            _button(Action.USE_ITEM, "Right click", "Shift"),
            _button(Action.INTERACT, "Left click", "Shift"),
            _button(
                Action.WIELD_RIGHT_WEAPON_TWO_HANDED,
                "Click mouse wheel",
                "Ctrl",
            ),
            _button(Action.OPEN_START_MENU, "Nothing"),
            _button(Action.OPEN_GESTURE_MENU, "Nothing"),
        ]
    ),
)


def _plausible(record: tuple[int, int, int]) -> bool:
    """Whether numbers could be a record: the ones in them are all small."""
    return all(value <= _MAX_FIELD for value in record)


class ControlTables:
    """The controls in a profile's decrypted data, which this edits in place
    (a `bytearray`, or a writable view of one). It remembers where each
    action's record is, and reads what is there."""

    def __init__(self, data: bytearray | memoryview) -> None:
        self._data = data
        self._keyboard, mouse_start = self._find_keyboard_table()
        self._mouse = self._find_mouse_table(mouse_start)

    @classmethod
    def create(
        cls, data: bytearray | memoryview, controls: Controls
    ) -> ControlTables:
        """Lays out these controls as the tables, the keyboard's records from
        where its table starts and the mouse's right after, over whatever is
        there. The data after them is left as it is, except that with no mouse
        table a record that is not one follows, to end the keyboard's."""
        offset = KEYBOARD_TABLE_START
        records = [
            (action, binding.key + KEY_BIAS, binding.modifier)
            for action, binding in controls.keyboard.items()
        ] + [
            (action, binding.button, binding.modifier)
            for action, binding in controls.mouse.items()
        ]
        if offset + _RECORD.size * len(records) > len(data):
            msg = "there is no room in the data for the controls"
            raise ControlsError(msg)
        for record in records:
            _RECORD.pack_into(data, offset, *record)
            offset += _RECORD.size
        if not controls.mouse and offset + _RECORD.size <= len(data):
            # Zeros here would read as a mouse record, of action 0.
            _RECORD.pack_into(data, offset, *_END_OF_TABLES)
        tables = cls(data)
        if tables.read() != controls:
            msg = "the controls do not read back from the tables laid out"
            raise ControlsError(msg)
        return tables

    def _record_at(self, offset: int) -> tuple[int, int, int] | None:
        if offset + _RECORD.size > len(self._data):
            return None
        action, code, modifier = _RECORD.unpack_from(self._data, offset)
        return int(action), int(code), int(modifier)

    def _find_keyboard_table(self) -> tuple[dict[int, int], int]:
        """Each action's offset, and where the table ends."""
        offsets: dict[int, int] = {}
        offset = KEYBOARD_TABLE_START
        while True:
            record = self._record_at(offset)
            if record is None:
                break
            action, code, _ = record
            if code < KEY_BIAS or action > _MAX_FIELD:
                break
            if action in offsets:
                msg = f"the keyboard table has action {action} twice"
                raise ControlsError(msg)
            offsets[action] = offset
            offset += _RECORD.size
        if not offsets:
            msg = "could not find the keyboard controls"
            raise ControlsError(msg)
        return offsets, offset

    def _find_mouse_table(self, start: int) -> dict[int, int]:
        """Each action's offset, for as many records as there are, up to the
        first that is not one: the data after the table is not records, and a
        repeated action means it has run into something else."""
        offsets: dict[int, int] = {}
        offset = start
        while len(offsets) < _MAX_MOUSE_RECORDS:
            record = self._record_at(offset)
            if record is None or not _plausible(record):
                break
            if record[0] in offsets:
                break
            offsets[record[0]] = offset
            offset += _RECORD.size
        return offsets

    def read(self) -> Controls:
        keyboard = {}
        for action, offset in self._keyboard.items():
            _, code, modifier = _RECORD.unpack_from(self._data, offset)
            keyboard[action] = KeyBinding(
                key=code - KEY_BIAS, modifier=modifier
            )
        mouse = {}
        for action, offset in self._mouse.items():
            _, code, modifier = _RECORD.unpack_from(self._data, offset)
            mouse[action] = MouseBinding(button=code, modifier=modifier)
        return Controls(keyboard=keyboard, mouse=mouse)

    def write(self, changes: Controls) -> None:
        """Sets the given actions. Every one has to be in the data already:
        a table can't grow."""
        missing = {
            "keyboard": sorted(set(changes.keyboard) - set(self._keyboard)),
            "mouse": sorted(set(changes.mouse) - set(self._mouse)),
        }
        problems = [
            f"{table}: {[action_name(action) for action in actions]}"
            for table, actions in missing.items()
            if actions
        ]
        if problems:
            msg = "the save has no controls for " + "; ".join(problems)
            raise ControlsError(msg)
        for action, key_binding in changes.keyboard.items():
            self._set(
                self._keyboard[action],
                action,
                key_binding.key + KEY_BIAS,
                key_binding.modifier,
            )
        for action, mouse_binding in changes.mouse.items():
            self._set(
                self._mouse[action],
                action,
                mouse_binding.button,
                mouse_binding.modifier,
            )

    def _set(self, offset: int, action: int, code: int, modifier: int) -> None:
        _RECORD.pack_into(self._data, offset, action, code, modifier)
