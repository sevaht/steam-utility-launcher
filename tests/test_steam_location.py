from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher.steam import SteamError, SteamLocation

if TYPE_CHECKING:
    from pathlib import Path


def _location(root: Path) -> SteamLocation:
    return SteamLocation(root=root, system_config_dirs=[])


def test_the_client_is_started_by_the_steam_on_the_path_or_by_its_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    location = _location(tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/steam")
    assert location.client_program() == "/usr/bin/steam"
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert location.client_program() == str(tmp_path / "steam.sh")


def test_the_client_is_asked_to_exit_and_waited_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    location = _location(tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: "/bin/steam")
    asked: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda command, **_k: asked.append(command)
    )
    still_running = [True, True, False]
    monkeypatch.setattr(
        SteamLocation,
        "is_client_running",
        staticmethod(lambda: still_running.pop(0)),
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    location.shut_down_client()
    assert asked == [["/bin/steam", "-shutdown"]]
    assert still_running == []


def test_a_client_that_does_not_exit_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    location = _location(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: None)
    monkeypatch.setattr(
        SteamLocation, "is_client_running", staticmethod(lambda: True)
    )
    clock = iter(range(0, 10_000, 40))
    monkeypatch.setattr("time.monotonic", lambda: next(clock))
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    with pytest.raises(SteamError, match="did not close"):
        location.shut_down_client()


def test_a_client_that_cannot_be_told_to_exit_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(*_a: object, **_k: object) -> None:
        command = "steam"
        raise subprocess.TimeoutExpired(command, 30)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(SteamError, match="Could not tell Steam to close"):
        _location(tmp_path).shut_down_client()


def test_a_game_that_cannot_be_started_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(*_a: object, **_k: object) -> None:
        msg = "no such program"
        raise OSError(msg)

    monkeypatch.setattr(subprocess, "Popen", fail)
    with pytest.raises(SteamError, match="Could not start the game"):
        _location(tmp_path).launch_game(42)


def test_an_unreadable_account_list_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SteamError, match="Could not read Steam's accounts"):
        _location(tmp_path).account_directory()
