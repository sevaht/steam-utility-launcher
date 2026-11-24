"""The Dark Souls II commands, under `ds2` (or `dark-souls-2`): its controls,
its controller layout, its analog stick deadzone, and the slowdown it gets with
every death."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, override

from steam_utility_launcher import (
    ds2_bindings,
    ds2_controller,
    ds2_deadzone,
    ds2_death_lag,
)
from steam_utility_launcher.commands.base import (
    Command,
    CommandGroup,
    Context,
    UsageError,
    argument_type,
)
from steam_utility_launcher.commands.fix_commands import FixCommand
from steam_utility_launcher.errors import UserError
from steam_utility_launcher.games import DS2_SOTFS_ID
from steam_utility_launcher.patching import PatchMethod
from steam_utility_launcher.programs import ProgramRunner
from steam_utility_launcher.steam_input import (
    CONTROLLER_TYPES,
    parse_controller_type,
)

if TYPE_CHECKING:
    import argparse

    from steam_utility_launcher.host import Host
    from steam_utility_launcher.patching import Fix, FixTarget


class Ds2BindingsCommand(Command):
    name = "bindings"
    help = (
        "Set Dark Souls II: Scholar of the First Sin's keyboard and mouse"
        " controls, which the game keeps in its save file. The game has to be"
        " closed."
    )

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        parser.add_argument(
            "--config",
            help=(
                "A TOML file of the controls to set (see resources/"
                "ds2_key_bindings.toml); defaults to the bundled one."
            ),
        )
        parser.add_argument(
            "--save",
            help=(
                "The save file (DS2SOFS0000.sl2); defaults to the one in the"
                " game's own folder."
            ),
        )
        parser.add_argument(
            "--show",
            action="store_true",
            help=(
                "Print the save file's controls, as a config file, instead"
                " of changing them."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Say what would change, without changing anything.",
        )
        parser.add_argument(
            "--create",
            action="store_true",
            help=(
                "Make a new save (for the Steam account in use, or"
                " --steam-id), with the controls set and the game's terms"
                " and conditions accepted, instead of changing one. It won't"
                " replace a save."
            ),
        )
        parser.add_argument(
            "--steam-id",
            help=(
                "The Steam account a new save is for: its 17 digit id, or"
                " the 16 hex digits of the folder a save is in. Defaults to"
                " the account in use."
            ),
        )
        parser.add_argument(
            "--no-accept-terms",
            action="store_true",
            help="With --create, leave the game to ask about its terms itself.",
        )

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        if args.show and args.create:
            msg = "--show and --create can't be used together."
            raise UsageError(msg)
        if args.steam_id and not args.create:
            msg = "--steam-id is only for --create."
            raise UsageError(msg)
        try:
            steam_id = (
                ds2_bindings.parse_steam_id(args.steam_id)
                if args.steam_id
                else None
            )
        except ValueError as error:
            raise UsageError(str(error)) from error
        host = context.host
        saves = None
        if not args.save:
            saves = ds2_bindings.Ds2Saves(
                ProgramRunner.for_game(
                    host, context.steam_if_proton, DS2_SOTFS_ID
                ).filesystem
            )
        request = ds2_bindings.BindingsRequest(
            save=Path(args.save) if args.save else None,
            config=Path(args.config) if args.config else None,
            show=args.show,
            dry_run=args.dry_run,
            create=args.create,
            accept_terms=not args.no_accept_terms,
            steam_id=steam_id,
            saves=saves,
            location=(
                context.location if args.create and steam_id is None else None
            ),
            host=host,
        )
        try:
            ds2_bindings.BindingsEditor(request).run()
        except (OSError, ValueError, UserError) as error:
            msg = f"Could not set the keys: {error}"
            raise UserError(msg) from error
        return 0


class Ds2ControllerCommand(Command):
    name = "controller"
    help = (
        "Have Steam use this program's controller layout for Dark Souls II:"
        " Scholar of the First Sin, which sends the keys the controls from"
        " `ds2 bindings` use from a gamepad's buttons."
    )

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        parser.add_argument(
            "--layout",
            help=(
                "A layout file (as Steam saves one) to use instead of the"
                " bundled one."
            ),
        )
        parser.add_argument(
            "--controller-type",
            action="append",
            dest="controller_types",
            type=argument_type(parse_controller_type),
            metavar="TYPE",
            help=(
                "A kind of controller to set the layout for (generic,"
                " xbox360, xboxone, ps4, ps5, switch_pro); give it more than"
                " once for several. By default, all of them."
            ),
        )
        parser.add_argument(
            "--keep-device-choices",
            action="store_true",
            help=(
                "Leave alone the layouts Steam has chosen for particular"
                " controllers (by their USB ids), which take priority over"
                " the choice for their kind. By default they are changed to"
                " this layout, for the kind of controller each one's layout"
                " is for."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Say what would change, without changing anything.",
        )
        parser.add_argument(
            "-y",
            "--yes",
            action="store_true",
            help="Don't ask before closing Steam, which has to be closed.",
        )
        parser.add_argument(
            "--no-restart",
            action="store_true",
            help="Don't start Steam again afterwards, if it had to be closed.",
        )

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        request = ds2_controller.ControllerRequest(
            location=context.location,
            client=context.client,
            layout_file=Path(args.layout) if args.layout else None,
            controller_types=args.controller_types or CONTROLLER_TYPES,
            keep_device_choices=args.keep_device_choices,
            assume_yes=args.yes,
            dry_run=args.dry_run,
            restart=not args.no_restart,
        )
        try:
            ds2_controller.LayoutInstaller(request).run()
        except (OSError, ValueError, UserError) as error:
            msg = f"Could not set the layout: {error}"
            raise UserError(msg) from error
        return 0


class _Ds2FixCommand(FixCommand):
    """A fix of the game's executable, which is in its `Game` folder."""

    game_name = "Dark Souls II"
    game_id = DS2_SOTFS_ID
    executable_path = f"Game/{ds2_deadzone.GAME_EXECUTABLE}"

    @override
    def is_game_running(self, host: Host) -> bool:
        return ds2_bindings.game_is_running(host)


class Ds2DeadzoneFixCommand(_Ds2FixCommand):
    name = "deadzone-fix"
    help = (
        "Dark Souls II: Scholar of the First Sin's analog stick deadzone: the"
        " game's own, or the round one of the other Dark Souls games (the"
        " Center Deadzone Mod's change, made here without bundling the game)."
        " patch makes it round."
    )

    @override
    def build_fix(self, args: argparse.Namespace, target: FixTarget) -> Fix:
        return ds2_deadzone.deadzone_fix(target)


class Ds2DeathLagFixCommand(_Ds2FixCommand):
    name = "death-lag-fix"
    help = (
        "Dark Souls II: Scholar of the First Sin getting slower with every"
        " death: an endless Steam achievements loop on an account that has"
        " every achievement, which each death makes one copy bigger. The fix"
        " stops the game awarding an achievement it already has: 45 bytes,"
        " changed in its executable or, leaving the executable alone, in"
        " memory by a tiny dinput8.dll (--method dll)."
    )

    @override
    def add_fix_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--method",
            type=PatchMethod,
            choices=list(PatchMethod),
            default=PatchMethod.EXECUTABLE,
            metavar="{executable,dll}",
            help=(
                "With patch: where the fix is made. executable (the"
                " default): in the executable; dll: by a dinput8.dll in the"
                " game's folder, which leaves the executable as the game"
                " has it."
            ),
        )

    @override
    def build_fix(self, args: argparse.Namespace, target: FixTarget) -> Fix:
        return ds2_death_lag.DeathLagFix(target=target, method=args.method)


class Ds2Command(CommandGroup):
    name = "ds2"
    aliases = ("dark-souls-2",)
    help = (
        "Dark Souls II: Scholar of the First Sin: bindings, controller,"
        " deadzone-fix, death-lag-fix."
    )
    subcommands = (
        Ds2BindingsCommand(),
        Ds2ControllerCommand(),
        Ds2DeadzoneFixCommand(),
        Ds2DeathLagFixCommand(),
    )
