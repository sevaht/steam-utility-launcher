"""Real Steam objects over a folder, for tests of what reads Steam's files."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from steam_utility_launcher.keyvalues import Section
from steam_utility_launcher.steam import (
    CompatibilityTool,
    Steam,
    SteamLocation,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def make_location(root: Path) -> SteamLocation:
    """A Steam installation in `root`, which needn't exist."""
    return SteamLocation(root=root, system_config_dirs=[])


def make_tool(install_path: Path, internal_name: str) -> CompatibilityTool:
    """A compatibility tool installed in `install_path`."""
    return CompatibilityTool(
        internal_name=internal_name,
        manifest_vdf=install_path / "toolmanifest.vdf",
        install_path=install_path,
        display_name=internal_name,
        binary_path=install_path / "proton",
        binary_argument_template=[],
    )


def bare_steam(root: Path | None = None) -> Steam:
    """A Steam with no tools, in a folder that needn't exist (nothing is
    written there), for what only needs to be given one."""
    return Steam(
        location=make_location(root or Path("/steam-for-the-test")),
        compatibility_tools=[],
    )


def make_steam(
    root: Path,
    tools: Sequence[CompatibilityTool] = (),
    *,
    tool_choices: Mapping[str, str] | None = None,
) -> Steam:
    """A Steam in `root` whose `config.vdf` has Steam run each app in
    `tool_choices` (app id to the tool's name) with that tool."""
    mapping = Section(
        [
            (
                app_id,
                Section([("name", name), ("config", ""), ("priority", "250")]),
            )
            for app_id, name in (tool_choices or {}).items()
        ]
    )
    steam_section = Section([("CompatToolMapping", mapping)])
    config = Section(
        [
            (
                "InstallConfigStore",
                Section(
                    [
                        (
                            "Software",
                            Section(
                                [
                                    (
                                        "Valve",
                                        Section([("Steam", steam_section)]),
                                    )
                                ]
                            ),
                        )
                    ]
                ),
            )
        ]
    )
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "config.vdf").write_text(
        config.to_text(), encoding="utf-8"
    )
    return Steam(location=make_location(root), compatibility_tools=list(tools))
