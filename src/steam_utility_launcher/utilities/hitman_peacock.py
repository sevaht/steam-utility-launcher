from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from steam_utility_launcher.github_release_updater import (
    ApplicationUpdater,
    ArchiveFormat,
    Asset,
    GitHubRepository,
)
from steam_utility_launcher.steam import Process
from steam_utility_launcher.utilities.game_utility import GameUtility

if TYPE_CHECKING:
    from steam_utility_launcher.host import Host
    from steam_utility_launcher.programs import ProgramRunner

_PORT = "4747"


@dataclass(frozen=True)
class PeacockUtility(GameUtility):
    """The Peacock private server, and the patcher that points the game at it.
    The server runs natively (with Node on Linux, as its own script on
    Windows); only the patcher belongs in the game's prefix."""

    def _updater(self, host: Host) -> ApplicationUpdater:
        suffix = "-linux" if host.name == "linux" else ""
        return ApplicationUpdater(
            name="Peacock",
            repository=GitHubRepository(
                "Peacock", organization="thepeacockproject"
            ),
            assets=[
                Asset(
                    pattern=re.compile(rf"Peacock-v[^-]+\{suffix}.zip"),
                    archive_format=ArchiveFormat.ZIP,
                    strip_archive_components=1,
                )
            ],
            preserved_paths={
                Path("userdata"),
                Path("contracts"),
                Path("contractSessions"),
            },
        )

    def launch(self, runner: ProgramRunner, host: Host) -> int:
        env = os.environ.copy()
        env["PORT"] = _PORT
        patcher_args = ["--headless", "--domain", f"127.0.0.1:{_PORT}"]
        updater = self._updater(host)
        directory = updater.default_install_directory
        patcher = runner.process(
            [str(directory / "PeacockPatcher.exe"), *patcher_args]
        )
        if host.supports_proton:
            server = Process(["node", "chunk0.js"], env=env, cwd=directory)
            processes = [patcher, server]
        else:
            server = Process(
                [str(directory / "Start Server.cmd")], env=env, cwd=directory
            )
            processes = [server, patcher]
        updater.update()
        # The server's console window is the user's to close on Windows.
        updater.launch(processes, wait=host.supports_proton)
        return 0


UTILITY = PeacockUtility(
    command="hitman-peacock",
    help="Run Hitman's Peacock private server.",
    game_id="1659040",
)
