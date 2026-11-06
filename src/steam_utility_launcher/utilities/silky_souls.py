from __future__ import annotations

import re

from steam_utility_launcher.github_release_updater import (
    ApplicationUpdater,
    Asset,
    GitHubRepository,
)
from steam_utility_launcher.utilities.game_utility import ExecutableUtility

UTILITY = ExecutableUtility(
    command="silky-souls",
    help="Run the Dark Souls: Remastered SilkySouls tool.",
    game_id="570940",
    executable="SilkySouls.exe",
    updater=ApplicationUpdater(
        name="SilkySouls",
        repository=GitHubRepository("SilkySouls", organization="borgCode"),
        assets=[
            Asset(pattern=re.compile(r"SilkySouls.exe"), archive_format=None)
        ],
        preserved_paths=set(),
        launch_registry_set={"mscoree": "native,builtin"},
        launch_registry_unset={"*mscoree"},
    ),
)
