"""The operating system this program runs on, as one object.

Everything that differs between Linux and Windows is answered here, so the rest
of the program asks the `Host` and never checks the platform itself: where this
program keeps its data, where Steam is, which processes are running, and how a
program is started on its own. `psutil` does the process work and
`platformdirs` the placement of files, on both systems.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs
import psutil

if TYPE_CHECKING:
    from collections.abc import Sequence

APPLICATION_NAME = "steam-utility-launcher"


class UnsupportedHostError(RuntimeError):
    """This program is for Linux and Windows, and this is neither."""


class UserProcesses:
    """The running processes of the user this program runs as."""

    def __init__(self) -> None:
        self._username = psutil.Process().username()

    def _own(self) -> list[psutil.Process]:
        found = []
        for process in psutil.process_iter():
            try:
                if (
                    process.pid != os.getpid()
                    and process.username() == self._username
                ):
                    found.append(process)
            except psutil.Error:
                continue
        return found

    def is_running(self, name: str) -> bool:
        """Whether a process is named `name` (`steam`, or `steam.exe`)."""
        for process in self._own():
            try:
                if process.name() == name:
                    return True
            except psutil.Error:
                continue
        return False

    def is_running_with_command_line(self, text: str) -> bool:
        """Whether a process has `text` anywhere in its command line."""
        for process in self._own():
            try:
                if any(text in argument for argument in process.cmdline()):
                    return True
            except psutil.Error:
                continue
        return False

    def environment_variable_of(self, name: str, variable: str) -> str | None:
        """`variable` in the environment of the first process named `name`
        that has it."""
        for process in self._own():
            try:
                if process.name() != name:
                    continue
                value = process.environ().get(variable)
            except psutil.Error:
                continue
            if value is not None:
                return value
        return None


class Host(ABC):
    """What the operating system provides, which differs between systems."""

    name: str  # as release assets name the system: linux, windows
    supports_proton: bool

    @classmethod
    def current(cls) -> Host:
        if sys.platform == "win32":
            return WindowsHost()
        if sys.platform.startswith("linux"):
            return LinuxHost()
        msg = f"Only Linux and Windows are supported, not {sys.platform}."
        raise UnsupportedHostError(msg)

    @property
    def data_directory(self) -> Path:
        """Where this program keeps what it downloads and installs."""
        return Path(
            platformdirs.user_data_dir(APPLICATION_NAME, appauthor=False)
        )

    @property
    def processes(self) -> UserProcesses:
        return UserProcesses()

    @property
    @abstractmethod
    def steam_root(self) -> Path:
        """The folder Steam is installed in."""

    @abstractmethod
    def steam_client_command(self, root: Path) -> list[str]:
        """What starts the Steam client installed in `root`."""

    @property
    def system_steam_config_dirs(self) -> list[Path]:
        """Folders besides Steam's own that may list compatibility tools."""
        return []

    @property
    @abstractmethod
    def steam_process_name(self) -> str:
        """The name of the Steam client's process."""

    @abstractmethod
    def spawn_detached(self, command: Sequence[str]) -> None:
        """Starts a program that keeps running on its own, not as a child of
        this one. Raises OSError if it can't be started."""


class LinuxHost(Host):
    name = "linux"
    supports_proton = True

    @property
    def steam_root(self) -> Path:
        return Path(platformdirs.user_data_dir(appauthor=False)) / "Steam"

    @property
    def steam_process_name(self) -> str:
        return "steam"

    def steam_client_command(self, root: Path) -> list[str]:
        return [shutil.which("steam") or str(root / "steam.sh")]

    @property
    def system_steam_config_dirs(self) -> list[Path]:
        return [
            Path(path) / "steam"
            for path in platformdirs.site_data_dir(
                appauthor=False, multipath=True
            ).split(os.pathsep)
        ]

    def spawn_detached(self, command: Sequence[str]) -> None:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


class WindowsHost(Host):
    name = "windows"
    supports_proton = False

    @property
    def steam_root(self) -> Path:
        if sys.platform == "win32":
            import winreg

            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"
                ) as key:
                    return Path(str(winreg.QueryValueEx(key, "SteamPath")[0]))
            except OSError:
                pass
        program_files = os.environ.get(
            "PROGRAMFILES(X86)", r"C:\Program Files (x86)"
        )
        return Path(program_files) / "Steam"

    @property
    def steam_process_name(self) -> str:
        return "steam.exe"

    def steam_client_command(self, root: Path) -> list[str]:
        return [str(root / "steam.exe")]

    def spawn_detached(self, command: Sequence[str]) -> None:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
