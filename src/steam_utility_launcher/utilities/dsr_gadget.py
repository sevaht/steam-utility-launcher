from __future__ import annotations

import re

from steam_utility_launcher.github_release_updater import (
    ApplicationUpdater,
    ArchiveFormat,
    Asset,
    GitHubRepository,
)
from steam_utility_launcher.utilities.game_utility import ExecutableUtility

UTILITY = ExecutableUtility(
    command="dsr-gadget",
    help="Run the Dark Souls: Remastered gadget.",
    game_id="570940",
    executable="DSR-Gadget.exe",
    updater=ApplicationUpdater(
        name="DSR-Gadget",
        repository=GitHubRepository("DSR-Gadget", organization="JKAnderson"),
        assets=[
            Asset(
                pattern=re.compile(r"DSR\.Gadget(\.[0-9]+)+.zip"),
                archive_format=ArchiveFormat.ZIP,
                strip_archive_components=1,
            )
        ],
        preserved_paths=set(),
        launch_registry_set={"mscoree": "native,builtin"},
        launch_registry_unset={"*mscoree"},
    ),
)
