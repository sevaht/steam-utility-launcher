"""Valve's KeyValues format, which Steam's `.vdf` files are written in.

A file is a `Section`: entries in order, each a string, an integer (binary
files only) or a nested `Section`. The text format (`config.vdf`,
`libraryfolders.vdf`, ...) and the binary one (`shortcuts.vdf`) read into, and
write from, the same model.

Only what Steam's files use is supported. Text: quoted and bare strings,
`//` comments, the escapes Valve defines. Binary: sections, strings and 32-bit
integers. Anything else (a conditional, an include, another binary value type,
truncated data) is a `KeyValuesError` and never a guess, because the files
this reads are rewritten, and a misread must not be written back.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, ClassVar, Literal, NamedTuple, NoReturn

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

type Value = str | int | Section


class KeyValuesError(ValueError):
    """The data is not KeyValues this module can read or write."""


class Section(dict[str, "Value"]):
    """The entries of one level of a KeyValues file, in order.

    Keys are compared exactly; Steam itself ignores case, and the files this
    program reads use the case it looks for. A key given twice in text is
    merged (sections) or replaced (strings), as Valve's parser does.
    """

    # ------------------------------------------------------------- reading

    def find(self, *path: str) -> Value | None:
        """What is at `path` below this section, or None if nothing is."""
        node: Value = self
        for key in path:
            if not isinstance(node, Section):
                return None
            found = node.get(key)
            if found is None:
                return None
            node = found
        return node

    def find_section(self, *path: str) -> Section | None:
        found = self.find(*path)
        return found if isinstance(found, Section) else None

    def find_string(self, *path: str) -> str | None:
        found = self.find(*path)
        return found if isinstance(found, str) else None

    def section(self, *path: str) -> Section:
        """The section at `path`. Raises KeyValuesError if there isn't one."""
        found = self.find_section(*path)
        if found is None:
            msg = f"no section {'/'.join(path)!r}"
            raise KeyValuesError(msg)
        return found

    def string(self, *path: str) -> str:
        """The string at `path`. Raises KeyValuesError if there isn't one."""
        found = self.find_string(*path)
        if found is None:
            msg = f"no string {'/'.join(path)!r}"
            raise KeyValuesError(msg)
        return found

    def integer(self, *path: str) -> int:
        """The integer at `path`. Raises KeyValuesError if there isn't one."""
        found = self.find(*path)
        if not isinstance(found, int):
            msg = f"no integer {'/'.join(path)!r}"
            raise KeyValuesError(msg)
        return found

    def subsections(self) -> Iterator[tuple[str, Section]]:
        """The entries that are sections, with their keys."""
        for key, value in self.items():
            if isinstance(value, Section):
                yield key, value

    def strings(self) -> Iterator[tuple[str, str]]:
        """The entries that are strings, with their keys."""
        for key, value in self.items():
            if isinstance(value, str):
                yield key, value

    def add_section(self, key: str, child: Section) -> None:
        """Adds `child` under `key`, merged into the section already there if
        there is one."""
        existing = self.get(key)
        if not isinstance(existing, Section):
            self[key] = child
            return
        for name, value in child.items():
            if isinstance(value, Section):
                existing.add_section(name, value)
            else:
                existing[name] = value

    # ------------------------------------------------------------- loading

    @classmethod
    def from_text(cls, text: str) -> Section:
        return _TextReader(text).read()

    @classmethod
    def from_text_file(cls, path: Path) -> Section:
        return cls.from_text(path.read_text(encoding="utf-8"))

    @classmethod
    def from_binary(cls, data: bytes) -> Section:
        return _BinaryReader(data).read()

    # ------------------------------------------------------------- writing

    def to_text(self) -> str:
        """The section as Steam writes a text file: tab-indented, one space
        between key and value."""
        return "".join(self.text_lines(0))

    def text_lines(self, level: int) -> Iterator[str]:
        """The lines of the text, for a section `level` levels in."""
        indent = "\t" * level
        for key, value in self.items():
            name = _escape(key)
            if isinstance(value, Section):
                yield f'{indent}"{name}"\n{indent}{{\n'
                yield from value.text_lines(level + 1)
                yield f"{indent}}}\n"
            else:
                yield f'{indent}"{name}" "{_escape(str(value))}"\n'

    def to_binary(self) -> bytes:
        """The section as Steam writes a binary file. Raises KeyValuesError
        for a value that can't be written."""
        return b"".join(self.binary_chunks()) + _END

    def binary_chunks(self) -> Iterator[bytes]:
        """The bytes of the entries, without the end of the section."""
        for key, value in self.items():
            name = _c_string(key)
            if isinstance(value, Section):
                yield _SECTION + name
                yield from value.binary_chunks()
                yield _END
            elif isinstance(value, str):
                yield _STRING + name + _c_string(value)
            elif -(2**31) <= value < 2**31:
                yield _INTEGER + name + struct.pack("<i", value)
            else:
                msg = f"{key!r}: {value} doesn't fit in 32 bits"
                raise KeyValuesError(msg)


# ------------------------------------------------------------------ text

_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "v": "\v",
    "b": "\b",
    "r": "\r",
    "f": "\f",
    "a": "\a",
    "\\": "\\",
    "?": "?",
    '"': '"',
    "'": "'",
}
_UNESCAPES = {character: f"\\{name}" for name, character in _ESCAPES.items()}


def _escape(text: str) -> str:
    return "".join(_UNESCAPES.get(character, character) for character in text)


class _Token(NamedTuple):
    kind: Literal["string", "open", "close", "end"]
    text: str = ""


class _TextReader:
    """Reads the text format, one token at a time."""

    _BARE_STOPS: ClassVar[frozenset[str]] = frozenset('"{} \t\r\n')

    def __init__(self, text: str) -> None:
        self._text = text.removeprefix("﻿")
        self._position = 0

    def read(self) -> Section:
        return self._section(depth=0)

    def _error(self, message: str) -> NoReturn:
        line = self._text.count("\n", 0, self._position) + 1
        text = f"line {line}: {message}"
        raise KeyValuesError(text)

    def _skip_blanks(self) -> None:
        text = self._text
        while self._position < len(text):
            if text[self._position] in " \t\r\n":
                self._position += 1
            elif text.startswith("//", self._position):
                end = text.find("\n", self._position)
                self._position = len(text) if end == -1 else end
            else:
                return

    def _quoted(self) -> str:
        text = self._text
        self._position += 1  # the opening quote
        pieces: list[str] = []
        while True:
            if self._position >= len(text):
                self._error("a string is not closed")
            character = text[self._position]
            if character == '"':
                self._position += 1
                return "".join(pieces)
            if character == "\\" and self._position + 1 < len(text):
                escaped = text[self._position + 1]
                if escaped in _ESCAPES:
                    pieces.append(_ESCAPES[escaped])
                else:
                    pieces.append(character + escaped)
                self._position += 2
                continue
            pieces.append(character)
            self._position += 1

    def _bare(self) -> str:
        start = self._position
        text = self._text
        while (
            self._position < len(text)
            and text[self._position] not in self._BARE_STOPS
        ):
            self._position += 1
        return text[start : self._position]

    def _token(self) -> _Token:
        self._skip_blanks()
        text = self._text
        if self._position >= len(text):
            return _Token("end")
        character = text[self._position]
        if character == "{":
            self._position += 1
            return _Token("open")
        if character == "}":
            self._position += 1
            return _Token("close")
        if character == '"':
            return _Token("string", self._quoted())
        return _Token("string", self._bare())

    def _section(self, depth: int) -> Section:
        section = Section()
        while True:
            token = self._token()
            if token.kind == "end":
                if depth:
                    self._error("a section is not closed")
                return section
            if token.kind == "close":
                if not depth:
                    self._error("unexpected '}'")
                return section
            if token.kind == "open":
                self._error("a section has no name")
            value = self._token()
            if value.kind == "open":
                section.add_section(token.text, self._section(depth + 1))
            elif value.kind == "string":
                section[token.text] = value.text
            else:
                self._error(f"{token.text!r} has no value")


# ---------------------------------------------------------------- binary

_SECTION = b"\x00"
_STRING = b"\x01"
_INTEGER = b"\x02"
_END = b"\x08"


def _c_string(text: str) -> bytes:
    if "\x00" in text:
        msg = f"{text!r} has a NUL in it, which a binary file can't hold"
        raise KeyValuesError(msg)
    return text.encode("utf-8") + b"\x00"


class _BinaryReader:
    """Reads the binary format."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0

    def _error(self, message: str, cause: Exception | None = None) -> NoReturn:
        text = f"byte {self._position}: {message}"
        raise KeyValuesError(text) from cause

    def _byte(self) -> bytes:
        if self._position >= len(self._data):
            self._error("the data ends in the middle of a section")
        value = self._data[self._position : self._position + 1]
        self._position += 1
        return value

    def _c_string(self) -> str:
        end = self._data.find(b"\x00", self._position)
        if end == -1:
            self._error("a string is not closed")
        raw = self._data[self._position : end]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            self._error(f"a string is not UTF-8: {error}", error)
        self._position = end + 1
        return text

    def _integer(self) -> int:
        end = self._position + 4
        if end > len(self._data):
            self._error("an integer is cut short")
        (value,) = struct.unpack_from("<i", self._data, self._position)
        self._position = end
        return int(value)

    def read(self) -> Section:
        """The whole data: a section, ended like any other, and nothing
        after it."""
        section = self._section()
        if self._position != len(self._data):
            self._error("there is data after the end")
        return section

    def _section(self) -> Section:
        section = Section()
        while True:
            start = self._position
            kind = self._byte()
            if kind == _END:
                return section
            key = self._c_string()
            if kind == _SECTION:
                section[key] = self._section()
            elif kind == _STRING:
                section[key] = self._c_string()
            elif kind == _INTEGER:
                section[key] = self._integer()
            else:
                self._position = start
                self._error(
                    f"the value type {kind.hex()} of {key!r} isn't supported"
                )
