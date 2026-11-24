"""The Mina the Hollower commands, under `mina` (or `mina-the-hollower`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from steam_utility_launcher import mina_wayland
from steam_utility_launcher.commands.base import CommandGroup
from steam_utility_launcher.commands.fix_commands import FixCommand
from steam_utility_launcher.games import MINA_THE_HOLLOWER_ID

if TYPE_CHECKING:
    import argparse

    from steam_utility_launcher.patching import Fix, FixTarget


class MinaWaylandFixCommand(FixCommand):
    name = mina_wayland.FIX_NAME
    help = (
        "Mina the Hollower on native Wayland, where it fails with NVIDIA's"
        " driver: two bugs of the game's own, fixed in its executable (a Vulkan"
        " structure of the wrong type, and a window size read before it"
        " exists). patch makes it work."
    )
    game_name = "Mina the Hollower"
    game_id = MINA_THE_HOLLOWER_ID
    executable_path = mina_wayland.GAME_EXECUTABLE

    @override
    def build_fix(self, args: argparse.Namespace, target: FixTarget) -> Fix:
        return mina_wayland.wayland_fix(target)


class MinaCommand(CommandGroup):
    name = "mina"
    aliases = ("mina-the-hollower",)
    help = "Mina the Hollower: wayland-fix."
    subcommands = (MinaWaylandFixCommand(),)
