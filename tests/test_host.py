from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import psutil
import pytest

from steam_utility_launcher import application
from steam_utility_launcher.host import (
    APPLICATION_NAME,
    Host,
    LinuxHost,
    UnsupportedHostError,
    UserProcesses,
    WindowsHost,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_SLEEP = "import time; time.sleep(60)"


@pytest.fixture
def child() -> Iterator[subprocess.Popen[bytes]]:
    process = subprocess.Popen(
        [sys.executable, "-c", _SLEEP, "UniqueMarkerForTheTest.exe"],
        env={"MARKER_VARIABLE": "marker value"},
    )
    yield process
    process.kill()
    process.wait()


@pytest.mark.usefixtures("child")
def test_a_process_is_found_by_its_command_line() -> None:
    processes = UserProcesses()
    assert processes.is_running_with_command_line("UniqueMarkerForTheTest.exe")
    assert not processes.is_running_with_command_line("NoSuchMarker.exe")


def test_a_process_is_found_by_its_name(
    child: subprocess.Popen[bytes],
) -> None:
    name = psutil.Process(child.pid).name()
    assert UserProcesses().is_running(name)
    assert not UserProcesses().is_running("no-such-program-name")


def test_a_process_environment_variable_is_read(
    child: subprocess.Popen[bytes],
) -> None:
    name = psutil.Process(child.pid).name()
    assert (
        UserProcesses().environment_variable_of(name, "MARKER_VARIABLE")
        == "marker value"
    )
    assert (
        UserProcesses().environment_variable_of(name, "NO_SUCH_VARIABLE")
        is None
    )


def test_this_process_is_not_its_own_match() -> None:
    # This process's command line has pytest's arguments, which can be
    # anything, but never this text: it is only in the test's source.
    assert not UserProcesses().is_running_with_command_line(
        "text-only-in-the-source-" + "of-this-test"
    )


def test_the_data_directory_is_this_programs_own() -> None:
    assert Host.current().data_directory.name == APPLICATION_NAME


@pytest.mark.skipif(sys.platform == "win32", reason="Linux paths")
def test_on_linux_the_data_directory_follows_xdg_data_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    host = LinuxHost()
    assert host.data_directory == tmp_path / APPLICATION_NAME
    assert host.steam_root == tmp_path / "Steam"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux paths")
def test_on_linux_system_tool_folders_follow_xdg_data_dirs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_DIRS", "/a/share:/b/share")
    assert [str(path) for path in LinuxHost().system_steam_config_dirs] == [
        "/a/share/steam",
        "/b/share/steam",
    ]


def test_the_hosts_say_whether_proton_is_theirs() -> None:
    assert LinuxHost().supports_proton
    assert not WindowsHost().supports_proton


def test_windows_starts_steam_from_its_own_folder(tmp_path: Path) -> None:
    host = WindowsHost()
    assert host.steam_process_name == "steam.exe"
    assert host.steam_client_command(tmp_path) == [str(tmp_path / "steam.exe")]


def test_windows_finds_steam_without_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        pytest.skip("the registry is real there")
    monkeypatch.setenv("PROGRAMFILES(X86)", "/programs")
    assert (
        str(WindowsHost().steam_root).replace("\\", "/") == "/programs/Steam"
    )


def test_a_system_that_is_neither_linux_nor_windows_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "some-other-system")
    with pytest.raises(UnsupportedHostError, match="Linux and Windows"):
        Host.current()
    assert application.main(["zemu"]) == 1
    assert "Linux and Windows" in capsys.readouterr().err
