from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import asar
from tests.asar_helper import make_asar

if TYPE_CHECKING:
    from pathlib import Path


def test_a_file_is_read_by_its_path(tmp_path: Path) -> None:
    archive = make_asar(
        tmp_path,
        {"a/b/one.bin": b"first", "a/two.bin": b"second!", "three": b"3"},
    )
    assert asar.read_file(archive, "a/b/one.bin") == b"first"
    assert asar.read_file(archive, "/a/two.bin") == b"second!"
    assert asar.read_file(archive, "three") == b"3"


def test_a_file_that_is_not_there_is_an_error(tmp_path: Path) -> None:
    archive = make_asar(tmp_path, {"a/one": b"x"})
    with pytest.raises(asar.AsarError):
        asar.read_file(archive, "a/missing")
    with pytest.raises(asar.AsarError):
        asar.read_file(archive, "a")  # a folder, not a file


def test_something_that_is_not_an_archive_is_an_error(tmp_path: Path) -> None:
    short = tmp_path / "short"
    short.write_bytes(b"x")
    with pytest.raises(asar.AsarError):
        asar.read_file(short, "a")
    garbage = tmp_path / "garbage"
    garbage.write_bytes(bytes(16) + b"not json")
    with pytest.raises(asar.AsarError):
        asar.read_file(garbage, "a")


def test_a_truncated_archive_is_an_error(tmp_path: Path) -> None:
    archive = make_asar(tmp_path, {"a": b"0123456789"})
    archive.write_bytes(archive.read_bytes()[:-4])
    with pytest.raises(asar.AsarError, match="truncated"):
        asar.read_file(archive, "a")
