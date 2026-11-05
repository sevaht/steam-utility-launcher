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


def _accounts(root: Path, users: str) -> SteamLocation:
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "loginusers.vdf").write_text(
        f'"users"\n{{\n{users}}}\n', encoding="utf-8"
    )
    return _location(root)


def _user(steam_id: int, *, recent: bool) -> str:
    return (
        f'\t"{steam_id}"\n\t{{\n\t\t"AccountName"\t\t"someone"\n'
        f'\t\t"MostRecent"\t\t"{int(recent)}"\n\t}}\n'
    )


def test_the_account_in_use_is_the_one_that_last_signed_in(
    tmp_path: Path,
) -> None:
    location = _accounts(
        tmp_path,
        _user(76561197960265729, recent=False)
        + _user(76561197969290206, recent=True),
    )
    assert location.account_steam_id() == 76561197969290206
    assert location.account_directory() == tmp_path / "userdata" / "9024478"


def test_the_only_account_is_the_one_in_use(tmp_path: Path) -> None:
    location = _accounts(tmp_path, _user(76561197969290206, recent=False))
    assert location.account_steam_id() == 76561197969290206


def test_accounts_with_no_clear_one_in_use_are_an_error(
    tmp_path: Path,
) -> None:
    location = _accounts(
        tmp_path,
        _user(76561197960265729, recent=False)
        + _user(76561197969290206, recent=False),
    )
    with pytest.raises(SteamError, match="which Steam account"):
        location.account_steam_id()


def test_steam_is_closed_for_a_change_after_asking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    closed: list[bool] = []
    monkeypatch.setattr(
        SteamLocation, "is_client_running", staticmethod(lambda: True)
    )
    monkeypatch.setattr(
        SteamLocation, "shut_down_client", lambda _self: closed.append(True)
    )
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return " Yes "

    assert _location(tmp_path).close_client_for_change(
        ask, assume_yes=False, why="do a thing"
    )
    assert closed == [True]
    assert "do a thing" in prompts[0]


def test_a_steam_that_is_not_running_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        SteamLocation, "is_client_running", staticmethod(lambda: False)
    )

    def never(_prompt: str) -> str:
        pytest.fail("nothing to ask about")

    assert not _location(tmp_path).close_client_for_change(
        never, assume_yes=False, why="x"
    )


@pytest.mark.parametrize("answer", ["n", "", "maybe"])
def test_steam_is_not_closed_if_the_answer_is_not_yes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: str
) -> None:
    def must_not_close(_self: object) -> None:
        pytest.fail("it should not have been closed")

    monkeypatch.setattr(
        SteamLocation, "is_client_running", staticmethod(lambda: True)
    )
    monkeypatch.setattr(SteamLocation, "shut_down_client", must_not_close)
    with pytest.raises(SteamError, match="Close Steam, then run this again"):
        _location(tmp_path).close_client_for_change(
            lambda _p: answer, assume_yes=False, why="x"
        )


def test_no_answer_at_all_is_a_no(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        SteamLocation, "is_client_running", staticmethod(lambda: True)
    )

    def eof(_prompt: str) -> str:
        raise EOFError

    with pytest.raises(SteamError, match="Close Steam"):
        _location(tmp_path).close_client_for_change(
            eof, assume_yes=False, why="x"
        )


def test_steam_is_started_on_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started: list[tuple[list[str], bool]] = []

    def popen(command: list[str], **options: object) -> None:
        started.append((command, bool(options["start_new_session"])))

    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/steam")
    monkeypatch.setattr(subprocess, "Popen", popen)
    _location(tmp_path).start_client()
    assert started == [(["/usr/bin/steam"], True)]


def test_steam_that_cannot_be_started_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(*_a: object, **_k: object) -> None:
        msg = "no such program"
        raise OSError(msg)

    monkeypatch.setattr(subprocess, "Popen", fail)
    with pytest.raises(SteamError, match="Could not start Steam"):
        _location(tmp_path).start_client()
