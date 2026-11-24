"""The Mina the Hollower Wayland fix, on stand-in executables."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import application, mina_wayland
from steam_utility_launcher.mina_wayland import PATCH

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SIZE = 0xCCC300


def _executable(*, is_patched: bool) -> bytes:
    data = bytearray(b"\x11" * SIZE)
    for edit in PATCH.edits:
        data[edit.offset : edit.end] = (
            edit.patched if is_patched else edit.original
        )
    return bytes(data)


def _write(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / mina_wayland.GAME_EXECUTABLE
    path.write_bytes(content)
    return path


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """application.main sets up logging on the stderr pytest is capturing."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_it_is_the_three_edits_the_diagnosis_found() -> None:
    assert [
        (edit.offset, edit.original.hex(), edit.patched.hex())
        for edit in PATCH.edits
    ] == [
        (0x2F5FA7, "a0d99a3b", "70e19a3b"),
        (0xCCC2F1, "90", "88"),
        (0xCCC2F7, "94", "8c"),
    ]


def test_the_surface_type_becomes_the_wayland_one() -> None:
    edit = PATCH.edits[0]
    assert int.from_bytes(edit.original, "little") == 1000004000  # Xlib
    assert int.from_bytes(edit.patched, "little") == 1000006000  # Wayland


@pytest.mark.parametrize("group", ["mina", "mina-the-hollower"])
def test_status_patch_and_restore_through_either_name(
    group: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, _executable(is_patched=False))
    arguments = [group, "wayland-fix", "--executable", str(path)]
    assert application.main(arguments) == 0  # status is the default
    assert capsys.readouterr().out == "original\n"
    assert application.main([*arguments, "patch"]) == 0
    assert capsys.readouterr().out == "patched\n"
    assert path.read_bytes() == _executable(is_patched=True)
    assert application.main([*arguments, "status"]) == 0
    assert capsys.readouterr().out == "patched\n"
    assert application.main([*arguments, "restore"]) == 0
    assert capsys.readouterr().out == "original\n"
    assert path.read_bytes() == _executable(is_patched=False)
    assert [entry.name for entry in tmp_path.iterdir()] == [path.name]


def test_json_says_the_same_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, _executable(is_patched=False))
    arguments = ["mina", "wayland-fix", "--executable", str(path), "--json"]
    assert application.main([*arguments, "patch", "--dry-run"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer == {
        "fix": "wayland-fix",
        "path": str(path),
        "state": "patched",
        "method": None,
        "changed": True,
        "dry_run": True,
    }
    assert path.read_bytes() == _executable(is_patched=False)


def test_one_edit_alone_is_partial_and_patching_finishes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = bytearray(_executable(is_patched=False))
    first = PATCH.edits[0]
    data[first.offset : first.end] = first.patched
    path = _write(tmp_path, bytes(data))
    arguments = ["mina", "wayland-fix", "--executable", str(path)]
    application.main(arguments)
    assert capsys.readouterr().out == "partial\n"
    application.main([*arguments, "patch"])
    assert path.read_bytes() == _executable(is_patched=True)


@pytest.mark.parametrize("action", ["patch", "restore"])
def test_another_game_version_is_refused_and_untouched(
    action: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, b"\x22" * SIZE)
    arguments = ["mina", "wayland-fix", "--executable", str(path)]
    assert application.main([*arguments, action]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing a script could mistake for a state
    assert "not a game version" in captured.err
    assert path.read_bytes() == b"\x22" * SIZE
    assert application.main(arguments) == 0
    assert capsys.readouterr().out == "unrecognized\n"
