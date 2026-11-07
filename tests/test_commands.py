from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import application, ds2_controller
from steam_utility_launcher.commands import default_commands
from steam_utility_launcher.host import LinuxHost, WindowsHost
from steam_utility_launcher.programs import (
    NativeRunner,
    ProgramRunner,
    ProtonRunner,
)
from steam_utility_launcher.steam import Steam, SteamError
from steam_utility_launcher.utilities.game_utility import ExecutableUtility

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """application.main sets up logging on the stderr pytest is capturing,
    which is closed by the next test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_every_command_has_its_own_name() -> None:
    names = [command.name for command in default_commands()]
    assert len(names) == len(set(names))
    assert {"rotk-launcher", "ds2", "proton", "zemu"} <= set(names)


@pytest.mark.parametrize("host", [LinuxHost(), WindowsHost()])
def test_the_parser_can_be_built_and_asked_for_help_on_every_system(
    host: LinuxHost | WindowsHost,
) -> None:
    with pytest.raises(SystemExit) as stopped:
        application.main(["--help"], host=host)
    assert stopped.value.code == 0


@pytest.mark.parametrize(
    "arguments",
    [
        ["rotk-launcher", "--hud", "fps"],
        ["rotk-launcher", "--default-hud"],
        ["rotk-launcher", "--max-fps", "60"],
        ["rotk-launcher", "--vsync", "0"],
        ["rotk-launcher", "--tpm"],
        ["rotk-launcher", "--tpm-endorsement"],
        ["rotk-launcher", "--allow-any-proton"],
        ["rotk-launcher", "--no-steam-overlay"],
        ["zemu", "--proton", "/x"],
        ["zemu", "--no-configure"],
    ],
)
def test_options_that_are_for_proton_are_refused_on_windows(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        application.main(arguments, host=WindowsHost())
    assert stopped.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [["rotk-launcher", "--hud", "fps"], ["zemu", "--no-configure"]],
)
def test_the_same_options_are_accepted_on_linux(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls, _host=None: object())
    )
    from steam_utility_launcher.commands.utility_commands import (
        RotkLauncherCommand,
        ZEmuCommand,
    )

    monkeypatch.setattr(RotkLauncherCommand, "run", lambda *_a: 0)
    monkeypatch.setattr(ZEmuCommand, "run", lambda *_a: 0)
    assert application.main(arguments, host=LinuxHost()) == 0


def test_the_flag_steam_starts_the_windows_shortcut_with_is_not_for_linux() -> (
    None
):
    with pytest.raises(SystemExit):
        application.main(
            ["rotk-launcher", "--run-from-steam"], host=LinuxHost()
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ["proton"],
        ["manual", "-g", "1", "x.exe"],
        ["prefix-path", "-g", "1"],
        ["enable-hwid-access"],
    ],
)
def test_commands_about_proton_say_so_on_windows_instead_of_failing(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert application.main(arguments, host=WindowsHost()) == 1
    err = capsys.readouterr().err
    assert f"{arguments[0]} can't be used here" in err
    assert "Traceback" not in err


def test_the_commands_for_proton_are_available_on_linux() -> None:
    for command in default_commands():
        assert command.unsupported_reason(LinuxHost()) is None


def _recording(seen: list[ProgramRunner]) -> Callable[..., int]:
    """A `launch` for a utility that only notes the runner it was given."""

    def launch(_self: object, runner: ProgramRunner, _host: object) -> int:
        seen.append(runner)
        return 0

    return launch


def test_a_game_utility_runs_natively_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[ProgramRunner] = []
    monkeypatch.setattr(ExecutableUtility, "launch", _recording(seen))
    assert application.main(["dsr-gadget"], host=WindowsHost()) == 0
    assert isinstance(seen[0], NativeRunner)


def test_a_game_utility_runs_in_the_games_prefix_on_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[ProgramRunner] = []
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls, _host=None: object())
    )
    monkeypatch.setattr(ExecutableUtility, "launch", _recording(seen))
    assert application.main(["dsr-gadget"], host=LinuxHost()) == 0
    assert isinstance(seen[0], ProtonRunner)
    assert seen[0].game_id == "570940"


def test_the_controller_layout_command_works_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        WindowsHost, "steam_root", property(lambda _s: tmp_path)
    )
    seen: list[ds2_controller.ControllerRequest] = []

    def run(self: ds2_controller.LayoutInstaller) -> None:
        seen.append(self.request)

    monkeypatch.setattr(ds2_controller.LayoutInstaller, "run", run)
    assert (
        application.main(["ds2", "controller", "-y"], host=WindowsHost()) == 0
    )
    (request,) = seen
    assert request.location.root == tmp_path
    assert request.client.host.name == "windows"
    assert request.assume_yes is True


# --- how a failure is reported


def _failing_installer(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def run(_self: object) -> None:
        raise error

    monkeypatch.setattr(ds2_controller.LayoutInstaller, "run", run)


def test_a_failure_is_reported_once_as_a_message_with_no_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _failing_installer(monkeypatch, SteamError("Steam is stuck"))
    assert application.main(["ds2", "controller"], host=WindowsHost()) == 1
    err = capsys.readouterr().err
    assert err.strip() == "ERROR: Could not set the layout: Steam is stuck"


def test_a_failure_puts_its_traceback_in_the_log_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _failing_installer(monkeypatch, SteamError("Steam is stuck"))
    log = tmp_path / "sul.log"
    arguments = ["--log-file", str(log), "ds2", "controller"]
    assert application.main(arguments, host=WindowsHost()) == 1
    text = log.read_text()
    assert "ds2 failed" in text
    assert "Traceback" in text
    assert "Steam is stuck" in text


@pytest.mark.parametrize("error", [OSError("disk gone"), ValueError("bad")])
def test_the_errors_a_command_expects_get_the_commands_context(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    _failing_installer(monkeypatch, error)
    assert application.main(["ds2", "controller"], host=WindowsHost()) == 1
    assert f"Could not set the layout: {error}" in capsys.readouterr().err


def test_an_error_nobody_expected_is_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _failing_installer(monkeypatch, KeyError("a bug"))
    with pytest.raises(KeyError):
        application.main(["ds2", "controller"], host=WindowsHost())


def test_the_console_shows_the_exception_for_anything_but_a_user_error() -> (
    None
):
    formatter = application._ConsoleFormatter(fmt="{message}", style="{")
    error = OSError("disk gone")
    record = logging.LogRecord(
        "x", logging.ERROR, "f", 1, "Refusing", (), (OSError, error, None)
    )
    assert "OSError: disk gone" in formatter.format(record)
