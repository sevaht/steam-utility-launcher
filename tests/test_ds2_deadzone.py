"""The Dark Souls II deadzone patch, on stand-in executables."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import application, ds2_bindings, ds2_deadzone
from steam_utility_launcher.ds2_deadzone import (
    CENTERED_CODE,
    ORIGINAL_CODE,
    PATCH_OFFSET,
    Deadzone,
    DeadzonePatcher,
    DeadzoneRequest,
)
from steam_utility_launcher.errors import UserError

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


@pytest.fixture(autouse=True)
def _game_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds2_bindings, "game_is_running", lambda _host: False)


def test_the_two_versions_are_the_same_size_and_differ() -> None:
    assert len(ORIGINAL_CODE) == len(CENTERED_CODE) == 202
    assert ORIGINAL_CODE != CENTERED_CODE


def test_deadzone_of_recognizes_each_and_nothing_else() -> None:
    assert ds2_deadzone.deadzone_of(_executable(ORIGINAL_CODE)) is (
        Deadzone.ORIGINAL
    )
    assert ds2_deadzone.deadzone_of(_executable(CENTERED_CODE)) is (
        Deadzone.CENTERED
    )
    assert ds2_deadzone.deadzone_of(_executable(b"\0" * 202)) is (
        Deadzone.UNKNOWN
    )
    assert ds2_deadzone.deadzone_of(b"short") is Deadzone.UNKNOWN


def test_on_and_off_switch_only_the_function(tmp_path: Path) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    DeadzonePatcher(DeadzoneRequest(path, Deadzone.CENTERED)).run()
    assert path.read_bytes() == _executable(CENTERED_CODE)
    DeadzonePatcher(DeadzoneRequest(path, Deadzone.ORIGINAL)).run()
    assert path.read_bytes() == _executable(ORIGINAL_CODE)
    assert [p.name for p in tmp_path.iterdir()] == ["DarkSoulsII.exe"]


def test_status_and_dry_run_change_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    DeadzonePatcher(DeadzoneRequest(path)).run()
    DeadzonePatcher(
        DeadzoneRequest(path, Deadzone.CENTERED, dry_run=True)
    ).run()
    assert path.read_bytes() == _executable(ORIGINAL_CODE)
    assert "Would switch" in capsys.readouterr().out


def test_it_is_already_so_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, CENTERED_CODE)
    DeadzonePatcher(DeadzoneRequest(path, Deadzone.CENTERED)).run()
    assert "Already so" in capsys.readouterr().out


def test_an_unknown_executable_is_never_written(tmp_path: Path) -> None:
    path = _write(tmp_path, b"\0" * 202)
    with pytest.raises(UserError, match="not a game version"):
        DeadzonePatcher(DeadzoneRequest(path, Deadzone.CENTERED)).run()
    assert path.read_bytes() == _executable(b"\0" * 202)
    DeadzonePatcher(DeadzoneRequest(path)).run()  # just asking is fine


def test_a_running_game_is_not_touched(tmp_path: Path) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    request = DeadzoneRequest(path, Deadzone.CENTERED, game_running=True)
    with pytest.raises(UserError, match="Close Dark Souls II"):
        DeadzonePatcher(request).run()
    assert path.read_bytes() == _executable(ORIGINAL_CODE)


def test_a_missing_file_is_a_user_error(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="Could not read"):
        DeadzonePatcher(DeadzoneRequest(tmp_path / "nope.exe")).run()


@pytest.mark.parametrize("group", ["ds2", "dark-souls-2"])
def test_both_names_of_the_group_work(
    group: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    arguments = [group, "deadzone-fix", "--executable", str(path)]
    assert application.main([*arguments, "status"]) == 0
    assert "axial" in capsys.readouterr().out
    assert application.main([*arguments, "on"]) == 0
    assert path.read_bytes() == _executable(CENTERED_CODE)
    assert application.main([*arguments, "off"]) == 0
    assert path.read_bytes() == _executable(ORIGINAL_CODE)
