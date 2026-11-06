"""Where a Windows program runs: natively on Windows, or in a game's Proton
prefix on Linux.

A utility says which game it belongs to and asks the `ProgramRunner` to run its
programs and to say where Windows programs keep their files, so it is written
once for both systems.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from steam_utility_launcher.steam import Process, Steam

if TYPE_CHECKING:
    from steam_utility_launcher.host import Host


class WindowsFileSystem(Protocol):
    """Where the files of Windows programs are: a real Windows installation,
    or a Wine prefix."""

    @property
    def program_files(self) -> Path: ...

    @property
    def roaming_appdata(self) -> Path: ...

    @property
    def local_appdata(self) -> Path: ...


@dataclass(frozen=True)
class NativeWindows:
    """The Windows this program runs on."""

    @property
    def program_files(self) -> Path:
        return Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))

    @property
    def roaming_appdata(self) -> Path:
        return Path(os.environ["APPDATA"])

    @property
    def local_appdata(self) -> Path:
        return Path(os.environ["LOCALAPPDATA"])


class ProgramRunner(ABC):
    """Runs the Windows programs of one game."""

    @property
    @abstractmethod
    def filesystem(self) -> WindowsFileSystem:
        """Where the game's Windows programs keep their files."""

    @abstractmethod
    def process(
        self, command_line: list[str], *, cwd: Path | None = None
    ) -> Process:
        """A process that runs `command_line` as the game's programs run."""

    @staticmethod
    def for_game(
        host: Host, steam: Steam | None, game_id: str
    ) -> ProgramRunner:
        """The runner for a Steam game on this system: its Proton prefix if
        Windows programs need Proton here, else Windows itself."""
        if not host.supports_proton:
            return NativeRunner()
        return ProtonRunner(Steam.require(steam), game_id)


class NativeRunner(ProgramRunner):
    """Runs programs directly, as on Windows."""

    @property
    def filesystem(self) -> WindowsFileSystem:
        return NativeWindows()

    def process(
        self, command_line: list[str], *, cwd: Path | None = None
    ) -> Process:
        return Process(command_line, cwd=cwd)


class ProtonRunner(ProgramRunner):
    """Runs programs in the Proton prefix of a Steam game."""

    def __init__(self, steam: Steam, game_id: str) -> None:
        self.steam = steam
        self.game_id = game_id

    @property
    def filesystem(self) -> WindowsFileSystem:
        return self.steam.game_prefix(game_id=self.game_id)

    def process(
        self, command_line: list[str], *, cwd: Path | None = None
    ) -> Process:
        return self.steam.process_in_prefix(
            command_line, game_id=self.game_id, cwd=cwd
        )
