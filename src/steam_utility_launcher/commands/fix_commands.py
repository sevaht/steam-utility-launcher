"""The subcommands that ask about, make or take out a fix of a game's files.

Every one is used the same way:

    <fix> [status|patch|restore] [--executable FILE] [--dry-run] [--json]

and answers on standard output with the state of the fix (see
:class:`steam_utility_launcher.patching.PatchState`) or, with `--json`, one
line of JSON; what is said for a person is logged, so `-v` shows it.
"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, override

from steam_utility_launcher.commands.base import Command, Context
from steam_utility_launcher.patching import FixAction, FixTarget

if TYPE_CHECKING:
    import argparse

    from steam_utility_launcher.host import Host
    from steam_utility_launcher.patching import Fix


class FixCommand(Command):
    """A subcommand for one fix of one game's executable."""

    # What the game is called in messages.
    game_name: str
    # Steam's id of the game, which tells where it is installed.
    game_id: str
    # The executable's path in the game's install folder.
    executable_path: str

    @abstractmethod
    def build_fix(self, args: argparse.Namespace, target: FixTarget) -> Fix:
        """The fix, made for `target` as the arguments say."""

    def add_fix_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Gives the parser the arguments the fix has besides the common
        ones."""

    def is_game_running(self, host: Host) -> bool:
        """Whether the game is running, with its files in use."""
        return host.processes.is_running_with_command_line(
            Path(self.executable_path).name
        )

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        parser.add_argument(
            "action",
            nargs="?",
            type=FixAction,
            choices=list(FixAction),
            default=FixAction.STATUS,
            metavar="{status,patch,restore}",
            help=(
                "status (the default): say whether the fix is made, changing"
                " nothing; patch: make it; restore: take it out, back to the"
                " game's own."
            ),
        )
        parser.add_argument(
            "--executable",
            help=(
                f"The {Path(self.executable_path).name} to use; defaults to"
                " the one in the game's Steam install."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=("Say what the state would be, without changing anything."),
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Answer with one line of JSON instead of the state's word.",
        )
        self.add_fix_arguments(parser)

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        action: FixAction = args.action
        if args.executable:
            executable = Path(args.executable)
        else:
            executable = (
                context.steam.game_install_path(game_id=self.game_id)
                / self.executable_path
            )
        target = FixTarget(
            executable=executable,
            game_name=self.game_name,
            is_dry_run=args.dry_run,
            # Not looked for with --executable: the path given is likely in
            # this very command line. A Windows game that runs has its file
            # locked, so the write would fail on its own.
            is_game_running=(
                action is not FixAction.STATUS
                and not args.executable
                and self.is_game_running(context.host)
            ),
        )
        report = action.run(self.build_fix(args, target))
        print(report.as_json() if args.json else report.as_text())
        return 0
