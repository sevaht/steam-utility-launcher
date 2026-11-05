from __future__ import annotations

import pytest

from steam_utility_launcher.ds2_inputs import (
    KEYS,
    MODIFIERS,
    MOUSE_BUTTONS,
    CodeNames,
)


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Esc", 0x01),
        ("1", 0x02),
        ("0", 0x0B),
        ("Q", 0x10),
        ("p", 0x19),
        ("A", 0x1E),
        ("c", 0x2E),
        ("B", 0x30),
        ("M", 0x32),
        ("Enter", 0x1C),
        ("backspace", 0x0E),
        ("LAlt", 0x38),
        ("Space", 0x39),
        ("Up", 0x79),
        ("Left", 0x7B),
        ("Right", 0x7C),
        ("Down", 0x7E),
    ],
)
def test_key_names_are_the_codes_the_game_stores(name: str, code: int) -> None:
    assert KEYS.parse(name) == code
    assert KEYS.name(code).lower() == name.lower()


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Left click", 0),
        ("Right click", 1),
        ("Click mouse wheel", 2),
        ("Mouse wheel up", 6),
        ("Mouse wheel down", 7),
        ("Double left click", 11),
        ("Double right click", 12),
        ("Nothing", 13),
    ],
)
def test_mouse_button_names(name: str, code: int) -> None:
    assert MOUSE_BUTTONS.parse(name) == code
    assert MOUSE_BUTTONS.name(code) == name


def test_modifier_names() -> None:
    assert MODIFIERS.parse("None") == 0
    assert MODIFIERS.parse("Shift") == 42
    assert MODIFIERS.parse("Ctrl") == 43


@pytest.mark.parametrize(
    "text",
    [
        "left click",
        "LEFT CLICK",
        "Left_Click",
        "left-click",
        "  Left   click ",
    ],
)
def test_names_ignore_case_spacing_and_separators(text: str) -> None:
    assert MOUSE_BUTTONS.parse(text) == 0


def test_a_number_or_hex_text_is_a_code_as_it_is() -> None:
    assert KEYS.parse(0x7D) == 0x7D
    assert KEYS.parse("0x7d") == 0x7D
    assert KEYS.parse("0X7D") == 0x7D
    assert KEYS.parse(0) == 0


@pytest.mark.parametrize(
    "bad", ["Nope", "", "0x", "0xzz", "0x100", -1, 0x100, "Left click"]
)
def test_unknown_names_and_codes_are_refused(bad: str | int) -> None:
    with pytest.raises(ValueError, match="key"):
        KEYS.parse(bad)


def test_a_refusal_says_what_kind_of_code_it_was() -> None:
    with pytest.raises(ValueError, match="mouse button"):
        MOUSE_BUTTONS.parse("Nope")
    with pytest.raises(ValueError, match="modifier"):
        MODIFIERS.parse(999)


def test_codes_without_a_name_are_shown_in_hex() -> None:
    assert KEYS.name(0x7D) == "0x7d"
    assert not KEYS.has_name(0x7D)
    assert KEYS.has_name(0x39)
    assert MOUSE_BUTTONS.name(3) == "0x03"


@pytest.mark.parametrize("names", [KEYS, MOUSE_BUTTONS, MODIFIERS])
def test_every_code_round_trips_through_its_name(names: CodeNames) -> None:
    for code in range(0x100):
        assert names.parse(names.name(code)) == code


def test_the_names_of_a_kind_are_distinct() -> None:
    with pytest.raises(ValueError, match="distinct"):
        CodeNames("thing", {"Left click": 1, "left_click": 2})


def test_a_code_with_several_names_is_shown_by_the_first() -> None:
    names = CodeNames("thing", {"First": 1, "Second": 1})
    assert names.name(1) == "First"
    assert names.parse("second") == 1
