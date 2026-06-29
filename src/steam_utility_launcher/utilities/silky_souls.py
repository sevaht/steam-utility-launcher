from __future__ import annotations

import re
import sys

from steam_utility_launcher.github_release_updater import (
    ApplicationUpdater,
    Asset,
    GitHubRepository,
)
from steam_utility_launcher.steam import Process, Steam


def launch(*, steam: Steam | None = None) -> int:
    updater = ApplicationUpdater(
        name="SilkySouls",
        repository=GitHubRepository("SilkySouls", organization="borgCode"),
        assets=[
            Asset(pattern=re.compile(r"SilkySouls.exe"), archive_format=None)
        ],
        preserved_paths=set(),
        launch_registry_set={"mscoree": "native,builtin"},
        launch_registry_unset={"*mscoree"},
    )
    install_directory = updater.default_install_directory
    tool_path = [str(install_directory / "SilkySouls.exe")]
    if sys.platform.startswith("linux"):
        if not steam:
            msg = "steam context must be provided on linux!"
            raise AssertionError(msg)
        processes = [
            steam.process_in_prefix(
                tool_path, game_id="570940", cwd=install_directory
            )
        ]
    else:
        processes = [Process(tool_path, cwd=install_directory)]
    updater.update()
    updater.launch(processes, wait=True)

    return 0
