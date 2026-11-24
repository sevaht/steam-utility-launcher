"""The Dark Souls II deadzone fix, on stand-in executables."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import application
from steam_utility_launcher.ds2_deadzone import (
    CENTERED_CODE,
    ORIGINAL_CODE,
    PATCH,
    PATCH_OFFSET,
)
from steam_utility_launcher.patching import PatchState

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _executable(code: bytes) -> bytes:
    return b"\x11" * PATCH_OFFSET + code + b"\x22" * 100


def _write(tmp_path: Path, code: bytes) -> Path:
    path = tmp_path / "DarkSoulsII.exe"
    path.write_bytes(_executable(code))
    return path


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """application.main sets up logging on the stderr pytest is capturing."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_the_two_versions_are_the_same_size_and_differ() -> None:
    assert len(ORIGINAL_CODE) == len(CENTERED_CODE) == 202
    assert ORIGINAL_CODE != CENTERED_CODE


def test_the_patch_recognizes_each_version_and_nothing_else() -> None:
    assert PATCH.state_of(_executable(ORIGINAL_CODE)) is PatchState.ORIGINAL
    assert PATCH.state_of(_executable(CENTERED_CODE)) is PatchState.PATCHED
    assert PATCH.state_of(_executable(b"\0" * 202)) is (
        PatchState.UNRECOGNIZED
    )
    assert PATCH.state_of(b"short") is PatchState.UNRECOGNIZED


@pytest.mark.parametrize("group", ["ds2", "dark-souls-2"])
def test_status_patch_and_restore_through_either_name(
    group: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    arguments = [group, "deadzone-fix", "--executable", str(path)]
    assert application.main(arguments) == 0  # status is the default
    assert capsys.readouterr().out == "original\n"
    assert application.main([*arguments, "patch"]) == 0
    assert capsys.readouterr().out == "patched\n"
    assert path.read_bytes() == _executable(CENTERED_CODE)
    assert application.main([*arguments, "status"]) == 0
    assert capsys.readouterr().out == "patched\n"
    assert application.main([*arguments, "restore"]) == 0
    assert capsys.readouterr().out == "original\n"
    assert path.read_bytes() == _executable(ORIGINAL_CODE)
    assert [entry.name for entry in tmp_path.iterdir()] == [path.name]


def test_a_dry_run_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    arguments = ["ds2", "deadzone-fix", "--executable", str(path)]
    assert application.main([*arguments, "patch", "--dry-run"]) == 0
    assert capsys.readouterr().out == "patched\n"
    assert path.read_bytes() == _executable(ORIGINAL_CODE)


def test_an_unknown_executable_is_refused_and_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, b"\0" * 202)
    arguments = ["ds2", "deadzone-fix", "--executable", str(path)]
    assert application.main([*arguments, "patch"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not a game version" in captured.err
    assert path.read_bytes() == _executable(b"\0" * 202)
    assert application.main(arguments) == 0  # just asking is fine
    assert capsys.readouterr().out == "unrecognized\n"


def test_a_missing_file_is_an_error_with_no_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = [
        "ds2",
        "deadzone-fix",
        "--executable",
        str(tmp_path / "nope.exe"),
    ]
    assert application.main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Could not read" in captured.err
