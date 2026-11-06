"""The commands that install a utility and run it."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, override

from steam_utility_launcher.commands.base import (
    Command,
    Context,
    argument_type,
)
from steam_utility_launcher.programs import ProgramRunner
from steam_utility_launcher.utilities import rotk_launcher
from steam_utility_launcher.utilities.zemu import ZEmuLauncher

if TYPE_CHECKING:
    from collections.abc import Sequence

    from steam_utility_launcher.host import Host
    from steam_utility_launcher.utilities.game_utility import GameUtility


class UtilityCommand(Command):
    """The command of a utility for a game, which runs it in the game's
    prefix on Linux and natively on Windows."""

    def __init__(self, utility: GameUtility) -> None:
        self.utility = utility
        self.name = utility.command
        self.help = utility.help

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        """A utility has nothing to ask for."""

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        runner = ProgramRunner.for_game(
            context.host, context.steam_if_proton, self.utility.game_id
        )
        return self.utility.launch(runner, context.host)


class RotkLauncherCommand(Command):
    name = "rotk-launcher"
    help = (
        "Install and run ROTK Launcher for Z1 Battle Royale, as a Steam"
        " shortcut this adds to your library. On Linux Steam runs it with"
        " Proton, and the shortcut's launch options are `steam-utility-"
        "launcher rotk-launcher -- %%command%%`."
    )

    # What only matters to adding the shortcut is not in the shortcut's
    # launch options.
    _NOT_FOR_THE_SHORTCUT = frozenset({"-y", "--yes", "--no-launch"})

    @classmethod
    def shortcut_options(cls, argv: Sequence[str], mode: str) -> list[str]:
        """The options of `mode` as given, for the launch options of the Steam
        shortcut it adds (what only matters to adding it is left out)."""
        given = list(argv)
        if mode not in given:
            return []
        options = given[given.index(mode) + 1 :]
        if "--" in options:
            options = options[: options.index("--")]
        return [
            option
            for option in options
            if option not in cls._NOT_FOR_THE_SHORTCUT
        ]

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        parser.add_argument(
            "-y",
            "--yes",
            action="store_true",
            help=(
                "When adding the shortcut to Steam, close Steam without"
                " asking (it has to be closed for the change to be saved)."
            ),
        )
        parser.add_argument(
            "--no-launch",
            action="store_true",
            help=(
                "When adding or updating the shortcut in Steam, don't also"
                " have Steam start it."
            ),
        )
        parser.add_argument(
            "steam_command",
            nargs="*",
            metavar="COMMAND",
            help=(
                "Steam's command for the shortcut, which it puts where"
                " `%%command%%` is in the launch options (after a `--`)."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Install the latest ROTK Launcher release even if one is"
                " already installed. Without this, an existing installation"
                " is left as-is (ROTK Launcher updates itself once running);"
                " a newer release only triggers a log warning."
            ),
        )
        if host.supports_proton:
            self._add_proton_arguments(parser)
            parser.set_defaults(run_from_steam=False)
        else:
            parser.add_argument(
                "--run-from-steam", action="store_true", help=argparse.SUPPRESS
            )
            parser.set_defaults(
                allow_any_proton=False,
                hud=None,
                default_hud=False,
                max_fps=None,
                vsync=None,
                no_steam_overlay=False,
                tpm=False,
                tpm_endorsement=False,
            )

    @staticmethod
    def _add_proton_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            rotk_launcher.ALLOW_ANY_PROTON_FLAG,
            action="store_true",
            help=(
                "Run with whatever Proton Steam uses for Z1 Battle Royale"
                " even if it isn't SVT-GE-Proton. Without this, ROTK"
                " Launcher refuses to start unless the game is set to"
                " SVT-GE-Proton (see the proton command), since the game can"
                " hang while starting on other Protons."
            ),
        )
        hud_group = parser.add_mutually_exclusive_group()
        hud_group.add_argument(
            "--hud",
            metavar="ITEMS",
            type=argument_type(rotk_launcher.hud_value),
            help=(
                "Show DXVK's overlay in the game: what DXVK_HUD takes, a"
                " comma-separated list of items and name=value options,"
                " without spaces, such as fps,gpuload,scale=1.5,opacity=0.8"
                " (items include fps, gpuload, frametimes, memory, devinfo;"
                " scale is 0.25 to 4, opacity 0.1 to 1)."
            ),
        )
        hud_group.add_argument(
            "--default-hud",
            action="store_true",
            help=(
                "Show DXVK's overlay with the preferred set of items, which"
                f" is {rotk_launcher.DEFAULT_HUD}."
            ),
        )
        parser.add_argument(
            "--max-fps",
            metavar="FPS",
            type=argument_type(rotk_launcher.max_fps_value),
            help=(
                "Limit the game to this many frames per second (DXVK's"
                " dxgi.maxFrameRate), for example to match your monitor's"
                " refresh rate instead of using vsync. No limit by default."
            ),
        )
        parser.add_argument(
            "--vsync",
            metavar="N",
            type=argument_type(rotk_launcher.vsync_value),
            help=(
                "Override the game's vsync (DXVK's dxgi.syncInterval): 0"
                " turns it off, 1 shows a frame every monitor refresh, 2"
                " every second refresh (half the refresh rate), up to 4. Not"
                " set by default, which leaves it to the game's own setting."
            ),
        )
        parser.add_argument(
            "--no-steam-overlay",
            action="store_true",
            help=(
                "Take Steam's overlay away from the launcher and the game,"
                " even if it is enabled for the shortcut in Steam. Without"
                " this it is left as Steam set it up. Only useful if the game"
                " stalls while starting with the overlay."
            ),
        )
        parser.add_argument(
            "--tpm",
            action="store_true",
            help=(
                "Opt in to TPM attestation: the PowerShell stand-in answers"
                " ROTK Launcher's TPM proofs with this machine's real TPM."
                " Off by default, which behaves exactly like a PC without a"
                " TPM."
            ),
        )
        parser.add_argument(
            "--tpm-endorsement",
            action="store_true",
            help=(
                "Opt in to the TPM's endorsement-key extras: send the EK"
                " certificate chain (fetched once from the TPM maker, AMD"
                " only) and let the TPM perform the server's credential"
                " activation. Implies --tpm. Off by default; only useful if"
                " the server starts requiring it."
            ),
        )

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        options = rotk_launcher.LaunchOptions(
            force=args.force,
            tpm=args.tpm,
            tpm_endorsement=args.tpm_endorsement,
            allow_any_proton=args.allow_any_proton,
            hud=(rotk_launcher.DEFAULT_HUD if args.default_hud else args.hud),
            max_fps=args.max_fps,
            vsync=args.vsync,
            no_steam_overlay=args.no_steam_overlay,
            shortcut_options=self.shortcut_options(context.argv, self.name),
            assume_yes=args.yes,
            start_shortcut=not args.no_launch,
            run_from_steam=args.run_from_steam,
        )
        return rotk_launcher.RotkLauncher(
            context.host, context.steam_if_proton
        ).launch(
            options, steam_command=args.steam_command, session=context.session
        )


class ZEmuCommand(Command):
    name = "zemu"
    help = (
        "Install and run ZEmu Launcher (https://zemu.uk) for H1Z1: King of"
        " the Kill, pointed at your Proton on Linux."
    )

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Install the latest ZEmu Launcher release even if one is"
                " already installed. Without this, an existing installation"
                " is left as-is (ZEmu Launcher updates itself once running);"
                " a newer release only triggers a log warning."
            ),
        )
        if not host.supports_proton:
            parser.set_defaults(no_configure=False, proton=None)
            return
        parser.add_argument(
            "--no-configure",
            action="store_true",
            help=(
                "Don't touch ZEmu Launcher's settings. Without this, its"
                " runtime follows the Proton used for Z1 Battle Royale; use"
                " this to pick one in ZEmu's own Properties screen instead."
            ),
        )
        parser.add_argument(
            "--proton",
            metavar="PATH",
            help=(
                "The Proton to use (its folder or its `proton` script)."
                " Defaults to the one Steam uses for Z1 Battle Royale, else"
                " the newest stable one installed."
            ),
        )

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        return ZEmuLauncher(context.host).launch(
            steam=context.steam_if_proton,
            force=args.force,
            configure=not args.no_configure,
            proton=Path(args.proton) if args.proton else None,
        )
