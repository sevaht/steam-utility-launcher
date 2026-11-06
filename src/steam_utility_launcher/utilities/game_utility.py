"""Tools that are downloaded from GitHub and run alongside a game."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from steam_utility_launcher.github_release_updater import (
        ApplicationUpdater,
    )
    from steam_utility_launcher.host import Host
    from steam_utility_launcher.programs import ProgramRunner


@dataclass(frozen=True)
class GameUtility(ABC):
    """A tool for one Steam game, which has a command of its own."""

    command: str
    help: str
    game_id: str

    @abstractmethod
    def launch(self, runner: ProgramRunner, host: Host) -> int:
        """Installs or updates the tool, then runs it for the game."""


@dataclass(frozen=True)
class ExecutableUtility(GameUtility):
    """A tool that is one program, run in the game's prefix (or natively on
    Windows) once it is up to date."""

    updater: ApplicationUpdater
    executable: str

    @override
    def launch(self, runner: ProgramRunner, host: Host) -> int:
        directory = self.updater.default_install_directory
        process = runner.process(
            [str(directory / self.executable)], cwd=directory
        )
        self.updater.update()
        self.updater.launch([process], wait=True)
        return 0
