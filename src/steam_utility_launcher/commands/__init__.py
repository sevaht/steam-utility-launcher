"""The subcommands, one class each."""

from __future__ import annotations

from steam_utility_launcher.commands.base import Command, Context, UsageError
from steam_utility_launcher.commands.ds2_commands import (
    Ds2BindingsCommand,
    Ds2ControllerCommand,
)
from steam_utility_launcher.commands.prefix_commands import (
    ManualCommand,
    PrefixPathCommand,
)
from steam_utility_launcher.commands.system_commands import (
    HwidAccessCommand,
    ProtonCommand,
)
from steam_utility_launcher.commands.utility_commands import (
    RotkLauncherCommand,
    UtilityCommand,
    ZEmuCommand,
)
from steam_utility_launcher.utilities import (
    dsr_gadget,
    hitman_peacock,
    silky_souls,
)

__all__ = ["Command", "Context", "UsageError", "default_commands"]


def default_commands() -> list[Command]:
    """Every subcommand, in the order they are listed."""
    return [
        ManualCommand(),
        PrefixPathCommand(),
        UtilityCommand(hitman_peacock.UTILITY),
        UtilityCommand(dsr_gadget.UTILITY),
        UtilityCommand(silky_souls.UTILITY),
        RotkLauncherCommand(),
        ProtonCommand(),
        ZEmuCommand(),
        Ds2BindingsCommand(),
        Ds2ControllerCommand(),
        HwidAccessCommand(),
    ]
