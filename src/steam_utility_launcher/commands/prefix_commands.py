"""The commands that work with a game's Proton prefix."""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING, override

from steam_utility_launcher.commands.base import Command, Context, UsageError
from steam_utility_launcher.steam import Steam

if TYPE_CHECKING:
    from steam_utility_launcher.host import Host

logger = logging.getLogger(__name__)

_PROTON_ONLY = "It works with a Proton prefix, which only Linux has."


class GameCommand(Command):
    """A command about the Steam game given by its id, or the one that is
    running in Proton."""

    proton_only_because = _PROTON_ONLY

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "-a",
            "--auto",
            action="store_true",
            help="Automatically detect the game presently running in proton.",
        )
        group.add_argument(
            "-g",
            "--game-id",
            help=(
                "The steam appid of the game.  Present in the store page URL."
                "  Dark Souls Remastered is 570940, for example."
            ),
        )

    @staticmethod
    def game_id(args: argparse.Namespace, context: Context) -> str:
        if args.auto:
            game_id = Steam.running_proton_game_id(context.host)
            if not game_id:
                msg = "Could not detect a game running in proton."
                raise RuntimeError(msg)
            return game_id
        return str(args.game_id)


class ManualCommand(GameCommand):
    name = "manual"
    help = "Run with a manual invocation."

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        super().add_arguments(parser, host)
        parser.add_argument(
            "-w",
            "--wait",
            action="store_true",
            help=(
                "Wait for the launched process to exit, then exit with its"
                " status code."
            ),
        )
        parser.add_argument(
            "command_line",
            nargs=argparse.REMAINDER,
            help="The command line of the binary to execute.",
        )

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        if not args.command_line:
            msg = "You must specify a command to run."
            raise UsageError(msg)
        child = context.steam.process_in_prefix(
            args.command_line, game_id=self.game_id(args, context)
        ).start()
        if not args.wait:
            return 0
        logger.info("Waiting for pid=%s to exit...", child.pid)
        return_code = child.wait()
        logger.info(
            "pid=%s exited with status code %s", child.pid, return_code
        )
        return return_code


class PrefixPathCommand(GameCommand):
    name = "prefix-path"
    help = "Print the Proton (WINE) prefix path used by a game."

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        print(
            context.steam.game_wine_prefix(game_id=self.game_id(args, context))
        )
        return 0
