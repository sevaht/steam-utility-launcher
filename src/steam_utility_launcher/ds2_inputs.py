"""Names for the codes Dark Souls II stores for keys, mouse buttons and
modifiers.

Only codes that have been seen in the game are named. The rest can still be
given by number, which is the code as the game stores it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_CODE = 0xFF
_HEX_PREFIX = "0x"


def _normalized(text: str) -> str:
    """Names match whatever the case, spacing, `_` or `-` between words."""
    return " ".join(text.replace("_", " ").replace("-", " ").lower().split())


class CodeNames:
    """The names of one kind of code, to and from the codes."""

    def __init__(self, kind: str, codes: Mapping[str, int]) -> None:
        self._kind = kind
        self._by_normalized_name = {
            _normalized(name): code for name, code in codes.items()
        }
        self._names: dict[int, str] = {}
        for name, code in codes.items():
            self._names.setdefault(code, name)
        if len(self._by_normalized_name) != len(codes):
            msg = f"{kind} names are not distinct"
            raise ValueError(msg)

    def parse(self, value: str | int) -> int:
        """The code for a name, or for a number (or hex text like `0x7e`),
        which is a code taken as it is."""
        if isinstance(value, int):
            return self._checked(value)
        code = self._by_normalized_name.get(_normalized(value))
        if code is not None:
            return code
        if value.strip().lower().startswith(_HEX_PREFIX):
            try:
                return self._checked(int(value, 16))
            except ValueError:
                pass
        msg = f"unknown {self._kind}: {value!r}"
        raise ValueError(msg)

    def name(self, code: int) -> str:
        """The name of a code, or the code in hex if it has none."""
        return self._names.get(code, f"{_HEX_PREFIX}{code:02x}")

    def has_name(self, code: int) -> bool:
        return code in self._names

    def _checked(self, code: int) -> int:
        if not 0 <= code <= MAX_CODE:
            msg = f"{self._kind} code out of range: {code}"
            raise ValueError(msg)
        return code


def _build_key_codes() -> dict[str, int]:
    codes = {
        "Esc": 0x01,
        "Minus": 0x0C,
        "Equals": 0x0D,
        "Backspace": 0x0E,
        "Tab": 0x0F,
        "LeftBracket": 0x1A,
        "RightBracket": 0x1B,
        "Enter": 0x1C,
        "LCtrl": 0x1D,
        "Semicolon": 0x27,
        "Apostrophe": 0x28,
        "Grave": 0x29,
        "LShift": 0x2A,
        "Backslash": 0x2B,
        "Comma": 0x33,
        "Period": 0x34,
        "Slash": 0x35,
        "RShift": 0x36,
        "LAlt": 0x38,
        "Space": 0x39,
        # Not DirectInput's codes for them: the game has its own.
        "Up": 0x79,
        "Left": 0x7B,
        "Right": 0x7C,
        "Down": 0x7E,
    }
    for first, keys in (
        (0x02, "1234567890"),
        (0x10, "QWERTYUIOP"),
        (0x1E, "ASDFGHJKL"),
        (0x2C, "ZXCVBNM"),
    ):
        codes.update({key: first + index for index, key in enumerate(keys)})
    return codes


# From Esc to Space the codes are DirectInput's scancodes. Letters, Esc, Enter,
# Backspace, Left Alt and Space have been seen in the game; the other keys in
# that range are assumed to follow. Keys past Space (F1-F12, the numpad, the
# right-hand Ctrl and Alt, Home/End and the like) are not named, since how the
# game numbers them hasn't been checked.
KEYS = CodeNames("key", _build_key_codes())

MOUSE_BUTTONS = CodeNames(
    "mouse button",
    {
        "Left click": 0,
        "Right click": 1,
        "Click mouse wheel": 2,
        "Mouse wheel up": 6,
        "Mouse wheel down": 7,
        "Double left click": 11,
        "Double right click": 12,
        "Nothing": 13,
    },
)

# What is held with a key or button. Not the codes of the keys themselves.
MODIFIERS = CodeNames("modifier", {"None": 0, "Shift": 42, "Ctrl": 43})
