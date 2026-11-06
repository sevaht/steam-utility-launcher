from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher.keyvalues import KeyValuesError, Section

if TYPE_CHECKING:
    from collections.abc import Callable

# ------------------------------------------------------------------- text


def test_text_is_read_as_nested_sections_of_strings() -> None:
    section = Section.from_text(
        '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve" "yes"\n'
        '\t\t"count"\t\t"3"\n\t}\n}\n'
    )
    assert section.string("InstallConfigStore", "Software", "Valve") == "yes"
    assert section.string("InstallConfigStore", "Software", "count") == "3"


def test_bare_words_are_read_as_strings() -> None:
    section = Section.from_text("name value\nwrap { inner 1 }")
    assert section.string("name") == "value"
    assert section.string("wrap", "inner") == "1"


def test_comments_and_blank_lines_are_skipped() -> None:
    section = Section.from_text(
        '// a comment\n\n"a" "1" // after a value\n"b" "http://x"\n'
    )
    assert section == {"a": "1", "b": "http://x"}


def test_a_byte_order_mark_is_ignored() -> None:
    assert Section.from_text('\ufeff"a" "1"') == {"a": "1"}


def test_valves_escapes_are_undone_and_others_are_left_alone() -> None:
    section = Section.from_text(r'"a" "tab\there \\ \"q\" \d C:\Games"')
    assert section.string("a") == 'tab\there \\ "q" \\d C:\\Games'


def test_a_string_may_span_lines() -> None:
    assert Section.from_text('"a" "one\ntwo"').string("a") == "one\ntwo"


def test_a_key_given_twice_replaces_a_string_and_merges_a_section() -> None:
    section = Section.from_text(
        '"s" { "a" "1" "b" "1" }\n"s" { "b" "2" "c" "3" }\n"x" "1"\n"x" "2"\n'
    )
    assert section.section("s") == {"a": "1", "b": "2", "c": "3"}
    assert section.string("x") == "2"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('"a" "unclosed', "not closed"),
        ('"a" { "b" "1"', "section is not closed"),
        ('"a" "1" }', "unexpected '}'"),
        ('"a" }', "has no value"),
        ('{ "a" "1" }', "has no name"),
        ('"a"', "has no value"),
    ],
)
def test_text_that_is_not_keyvalues_is_refused_with_its_line(
    text: str, message: str
) -> None:
    with pytest.raises(KeyValuesError, match=message) as refused:
        Section.from_text(text)
    assert "line 1" in str(refused.value)


def test_the_line_of_a_problem_is_named() -> None:
    with pytest.raises(KeyValuesError, match="line 3"):
        Section.from_text('"a" "1"\n"b" "2"\n"c" }\n')


def test_text_is_written_as_steam_writes_it() -> None:
    section = Section(
        [
            ("libraryfolders", Section([("0", Section([("path", "/x")]))])),
            ("count", "2"),
        ]
    )
    assert section.to_text() == (
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path" "/x"\n\t}\n}\n'
        '"count" "2"\n'
    )


def test_text_is_escaped_as_valve_escapes_it() -> None:
    section = Section([("a", 'Z:\\Games "x"\tit\'s?')])
    assert section.to_text() == '"a" "Z:\\\\Games \\"x\\"\\tit\\\'s\\?"\n'


def test_text_survives_a_round_trip() -> None:
    text = '"a"\n{\n\t"b" "line\\nbreak"\n\t"c"\n\t{\n\t}\n}\n'
    assert Section.from_text(text).to_text() == text


def test_integers_are_written_into_text_as_digits() -> None:
    assert Section([("n", 7)]).to_text() == '"n" "7"\n'


# ----------------------------------------------------------------- reading


def _nested() -> Section:
    return Section(
        [
            ("name", "x"),
            ("n", 4),
            ("inner", Section([("deep", Section([("leaf", "v")]))])),
        ]
    )


def test_values_are_found_by_their_path() -> None:
    section = _nested()
    assert section.string("inner", "deep", "leaf") == "v"
    assert section.integer("n") == 4
    assert section.section("inner", "deep") == {"leaf": "v"}
    assert section.find() is section


def test_what_is_not_there_is_none_when_asked_for_that() -> None:
    section = _nested()
    assert section.find("nope") is None
    assert section.find("name", "deeper") is None
    assert section.find_string("n") is None
    assert section.find_section("name") is None


@pytest.mark.parametrize(
    "ask",
    [
        lambda s: s.section("nope"),
        lambda s: s.section("name"),
        lambda s: s.string("n"),
        lambda s: s.string("inner", "nope"),
        lambda s: s.integer("name"),
    ],
)
def test_what_is_not_there_is_refused_when_it_is_required(
    ask: Callable[[Section], object],
) -> None:
    with pytest.raises(KeyValuesError, match="no "):
        ask(_nested())


def test_entries_can_be_walked_by_kind() -> None:
    section = _nested()
    assert [key for key, _ in section.subsections()] == ["inner"]
    assert list(section.strings()) == [("name", "x")]


# ------------------------------------------------------------------ binary

# \x00 shortcuts \x00 | \x00 0 \x00 | \x02 appid \x00 <-5> | \x01 AppName \x00
# Game \x00 | \x00 tags \x00 \x08 | \x08 (entry) | \x08 (shortcuts) | \x08
_ONE_SHORTCUT = (
    b"\x00shortcuts\x00"
    b"\x000\x00"
    b"\x02appid\x00\xfb\xff\xff\xff"
    b"\x01AppName\x00G\xc3\xa4me\x00"
    b"\x00tags\x00\x08"
    b"\x08"
    b"\x08"
    b"\x08"
)


def test_a_binary_file_is_read_and_written_back_byte_for_byte() -> None:
    section = Section.from_binary(_ONE_SHORTCUT)
    entry = section.section("shortcuts", "0")
    assert entry.integer("appid") == -5
    assert entry.string("AppName") == "Gäme"
    assert entry.section("tags") == {}
    assert section.to_binary() == _ONE_SHORTCUT


def test_an_empty_section_is_a_lone_end_marker() -> None:
    assert Section().to_binary() == b"\x08"
    assert Section.from_binary(b"\x08") == {}


def test_the_order_of_entries_is_kept() -> None:
    section = Section([("b", "1"), ("a", 2), ("c", Section())])
    assert list(Section.from_binary(section.to_binary())) == ["b", "a", "c"]


@pytest.mark.parametrize("number", [-(2**31), -1, 0, 2**31 - 1])
def test_integers_that_fit_in_32_bits_round_trip(number: int) -> None:
    section = Section([("n", number)])
    assert Section.from_binary(section.to_binary()) == {"n": number}


@pytest.mark.parametrize("number", [2**31, -(2**31) - 1])
def test_an_integer_that_does_not_fit_is_not_written(number: int) -> None:
    with pytest.raises(KeyValuesError, match="32 bits"):
        Section([("n", number)]).to_binary()


def test_a_string_with_a_nul_is_not_written() -> None:
    with pytest.raises(KeyValuesError, match="NUL"):
        Section([("a", "x\x00y")]).to_binary()
    with pytest.raises(KeyValuesError, match="NUL"):
        Section([("a\x00", "x")]).to_binary()


@pytest.mark.parametrize("kind", [b"\x03", b"\x05", b"\x06", b"\x07", b"\x0a"])
def test_a_value_type_that_is_not_supported_is_refused_at_its_byte(
    kind: bytes,
) -> None:
    with pytest.raises(KeyValuesError, match="isn't supported") as refused:
        Section.from_binary(b"\x01a\x00b\x00" + kind + b"k\x00" + bytes(8))
    assert "byte 5" in str(refused.value)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "ends in the middle"),
        (b"\x01a\x00b\x00", "ends in the middle"),  # no end marker
        (b"\x01a\x00b", "not closed"),
        (b"\x01a", "not closed"),
        (b"\x02a\x00\x01\x00", "cut short"),
        (b"\x01a\x00\xff\x00\x08", "not UTF-8"),
        (b"\x08\x08", "after the end"),
        (b"\x00a\x00\x01b\x00c\x00", "ends in the middle"),
    ],
)
def test_damaged_binary_data_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(KeyValuesError, match=message):
        Section.from_binary(data)
