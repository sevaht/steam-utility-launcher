from __future__ import annotations

import argparse
import logging
import os
import sys
import tarfile
from dataclasses import KW_ONLY, dataclass
from logging import Handler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from . import (
    __version__,
    ds2_bindings,
    ds2_controller,
    hwid_access,
    proton_tools,
)
from .steam import CompatSession, Steam
from .steam_input import (
    CONTROLLER_TYPES,
    ControllerType,
    parse_controller_type,
)
from .utilities import (
    dsr_gadget,
    hitman_peacock,
    rotk_launcher,
    silky_souls,
    zemu,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


@dataclass
class LogFileOptions:
    path: Path
    _ = KW_ONLY
    max_kb: int
    backup_count: int
    level: int = logging.DEBUG
    encoding: str = "utf-8"
    append: bool = True

    def create_handler(self) -> Handler:
        handler = RotatingFileHandler(
            self.path,
            mode="a" if self.append else "w",
            encoding=self.encoding,
            maxBytes=self.max_kb * 1024,
            backupCount=self.backup_count,
        )
        handler.setLevel(self.level)
        return handler


def configure_logging(
    console_level: int, log_file_options: LogFileOptions | None = None
) -> None:
    class SuppressConsoleOutputForMainModule(logging.Filter):
        def __init__(self) -> None:
            super().__init__()

        def filter(self, record: logging.LogRecord) -> bool:
            return record.name != (
                f"{__package__}.__main__" if __package__ else "__main__"
            )

    logging.getLogger().handlers = []
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(
        logging.Formatter(fmt="{levelname:s}: {message:s}", style="{")
    )
    console_handler.addFilter(SuppressConsoleOutputForMainModule())
    logging.getLogger().addHandler(console_handler)
    global_level = console_level
    if log_file_options:
        global_level = min(global_level, log_file_options.level)
        file_handler = log_file_options.create_handler()
        file_handler.setFormatter(
            logging.Formatter(
                fmt=(
                    "[{asctime:s}.{msecs:03.0f}]"
                    " [{levelname:s}] {module:s}: {message:s}"
                ),
                datefmt="%Y-%m-%d %H:%M:%S",
                style="{",
            )
        )
        logging.getLogger().addHandler(file_handler)
    logging.getLogger().setLevel(global_level)
    logger.info("logging configured")


def _add_game_id_group(parser: argparse.ArgumentParser) -> None:
    game_id_group = parser.add_mutually_exclusive_group(required=True)
    game_id_group.add_argument(
        "-a",
        "--auto",
        action="store_true",
        help="Automatically detect the game presently running in proton.",
    )
    game_id_group.add_argument(
        "-g",
        "--game-id",
        help=(
            "The steam appid of the game.  Present in the store page URL."
            "  Dark Souls Remastered is 570940, for example."
        ),
    )


def _resolve_game_id(args: argparse.Namespace) -> str:
    if args.auto:
        game_id = Steam.running_proton_game_id()
        if not game_id:
            msg = "Could not detect a game running in proton."
            raise RuntimeError(msg)
        return game_id
    return str(args.game_id)


def _controller_type_argument(text: str) -> ControllerType:
    try:
        return parse_controller_type(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _hud_argument(text: str) -> str:
    try:
        return rotk_launcher.hud_value(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _max_fps_argument(text: str) -> int:
    try:
        return rotk_launcher.max_fps_value(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _vsync_argument(text: str) -> int:
    try:
        return rotk_launcher.vsync_value(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _add_ds2_bindings_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    ds2_bindings_parser = subparsers.add_parser(
        "ds2-bindings",
        help=(
            "Set Dark Souls II: Scholar of the First Sin's keyboard and mouse"
            " controls, which the game keeps in its save file. The game has"
            " to be closed."
        ),
    )
    ds2_bindings_parser.add_argument(
        "--config",
        help=(
            "A TOML file of the controls to set (see resources/"
            "ds2_key_bindings.toml); defaults to the bundled one."
        ),
    )
    ds2_bindings_parser.add_argument(
        "--save",
        help="The save file (DS2SOFS0000.sl2); defaults to the one in Proton.",
    )
    ds2_bindings_parser.add_argument(
        "--show",
        action="store_true",
        help=(
            "Print the save file's controls, as a config file, instead of"
            " changing them."
        ),
    )
    ds2_bindings_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Say what would change, without changing anything.",
    )
    ds2_bindings_parser.add_argument(
        "--create",
        action="store_true",
        help=(
            "Make a new save (for the Steam account in use, or --steam-id),"
            " with the controls set and the game's terms and conditions"
            " accepted, instead of changing one. It won't replace a save."
        ),
    )
    ds2_bindings_parser.add_argument(
        "--steam-id",
        help=(
            "The Steam account a new save is for: its 17 digit id, or the 16"
            " hex digits of the folder a save is in. Defaults to the account"
            " in use."
        ),
    )
    ds2_bindings_parser.add_argument(
        "--no-accept-terms",
        action="store_true",
        help="With --create, leave the game to ask about its terms itself.",
    )


def _add_ds2_controller_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "ds2-controller",
        help=(
            "Have Steam use this program's controller layout for Dark Souls"
            " II: Scholar of the First Sin, which sends the keys the controls"
            " from ds2-bindings use from a gamepad's buttons."
        ),
    )
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
        type=_controller_type_argument,
        metavar="TYPE",
        help=(
            "A kind of controller to set the layout for (generic, xbox360,"
            " xboxone, ps4, ps5, switch_pro); give it more than once for"
            " several. By default, all of them."
        ),
    )
    parser.add_argument(
        "--keep-device-choices",
        action="store_true",
        help=(
            "Leave alone the layouts Steam has chosen for particular"
            " controllers (by their USB ids), which take priority over the"
            " choice for their kind. By default they are changed to this"
            " layout, for the kind of controller each one's layout is for."
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Starts applications inside the Proton prefix of a Steam game."
        )
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    log_group = parser.add_argument_group("logging")
    log_group.add_argument(
        "--log-file",
        metavar="FILE",
        help="Path to a file where logs will be written, if specified.",
    )
    log_verbosity_group = log_group.add_mutually_exclusive_group(
        required=False
    )
    log_verbosity_group.add_argument(
        "-v",
        "--verbose",
        action="store_const",
        dest="console_level",
        const=logging.INFO,
        help="Increase console log level to INFO.",
    )
    log_verbosity_group.add_argument(
        "-q",
        "--quiet",
        action="store_const",
        dest="console_level",
        const=logging.ERROR,
        help="Decrease console log level to ERROR.  Overrides -v.",
    )
    log_verbosity_group.add_argument(
        "--debug",
        action="store_const",
        dest="console_level",
        const=logging.DEBUG,
        help="Maximizes console log verbosity to DEBUG.  Overrides -v and -q.",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    manual_parser = subparsers.add_parser(
        "manual", help="Run with a manual invocation."
    )
    _add_game_id_group(manual_parser)
    manual_parser.add_argument(
        "-w",
        "--wait",
        action="store_true",
        help=(
            "Wait for the launched process to exit, then exit with its"
            " status code."
        ),
    )
    manual_parser.add_argument(
        "command_line",
        nargs=argparse.REMAINDER,
        help="The command line of the binary to execute.",
    )
    prefix_path_parser = subparsers.add_parser(
        "prefix-path",
        help="Print the Proton (WINE) prefix path used by a game.",
    )
    _add_game_id_group(prefix_path_parser)
    subparsers.add_parser(
        "hitman-peacock", help="Run Hitman's Peacock private server."
    )
    subparsers.add_parser(
        "dsr-gadget", help="Run the Dark Souls: Remastered gadget."
    )
    subparsers.add_parser(
        "silky-souls", help="Run the Dark Souls: Remastered SilkySouls tool."
    )
    rotk_launcher_parser = subparsers.add_parser(
        "rotk-launcher",
        help=(
            "Install and run ROTK Launcher for Z1 Battle Royale. On Linux this"
            " wraps a Steam shortcut's launch: set the shortcut's launch"
            " options to `steam-utility-launcher rotk-launcher -- %%command%%`;"
            " run on its own, it adds that shortcut to Steam."
        ),
    )
    rotk_launcher_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=(
            "When adding the shortcut to Steam, close Steam without asking"
            " (it has to be closed for the change to be saved)."
        ),
    )
    rotk_launcher_parser.add_argument(
        "--no-launch",
        action="store_true",
        help=(
            "When adding or updating the shortcut in Steam, don't also have"
            " Steam start it."
        ),
    )
    rotk_launcher_parser.add_argument(
        "steam_command",
        nargs="*",
        metavar="COMMAND",
        help=(
            "Steam's command for the shortcut, which it puts where"
            " `%%command%%` is in the launch options (after a `--`)."
        ),
    )
    rotk_launcher_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Install the latest ROTK Launcher release even if one is"
            " already installed. Without this, an existing installation is"
            " left as-is (ROTK Launcher updates itself once running); a"
            " newer release only triggers a log warning."
        ),
    )
    rotk_launcher_parser.add_argument(
        rotk_launcher.ALLOW_ANY_PROTON_FLAG,
        action="store_true",
        help=(
            "Run with whatever Proton Steam uses for Z1 Battle Royale even if"
            " it isn't SVT-GE-Proton. Without this, ROTK Launcher refuses to"
            " start unless the game is set to SVT-GE-Proton (see the"
            " proton command), since the game can hang while starting"
            " on other Protons."
        ),
    )
    hud_group = rotk_launcher_parser.add_mutually_exclusive_group()
    hud_group.add_argument(
        "--hud",
        metavar="ITEMS",
        type=_hud_argument,
        help=(
            "Show DXVK's overlay in the game: what DXVK_HUD takes, a"
            " comma-separated list of items and name=value options, without"
            " spaces, such as fps,gpuload,scale=1.5,opacity=0.8 (items"
            " include fps, gpuload, frametimes, memory, devinfo; scale is"
            " 0.25 to 4, opacity 0.1 to 1)."
        ),
    )
    hud_group.add_argument(
        "--default-hud",
        action="store_true",
        help=(
            "Show DXVK's overlay with the preferred set of items, which is"
            f" {rotk_launcher.DEFAULT_HUD}."
        ),
    )
    rotk_launcher_parser.add_argument(
        "--max-fps",
        metavar="FPS",
        type=_max_fps_argument,
        help=(
            "Limit the game to this many frames per second (DXVK's"
            " dxgi.maxFrameRate), for example to match your monitor's"
            " refresh rate instead of using vsync. No limit by default."
        ),
    )
    rotk_launcher_parser.add_argument(
        "--vsync",
        metavar="N",
        type=_vsync_argument,
        help=(
            "Override the game's vsync (DXVK's dxgi.syncInterval): 0 turns it"
            " off, 1 shows a frame every monitor refresh, 2 every second"
            " refresh (half the refresh rate), up to 4. Not set by default,"
            " which leaves it to the game's own setting."
        ),
    )
    rotk_launcher_parser.add_argument(
        "--no-steam-overlay",
        action="store_true",
        help=(
            "Take Steam's overlay away from the launcher and the game, even"
            " if it is enabled for the shortcut in Steam. Without this it is"
            " left as Steam set it up. Only useful if the game stalls"
            " while starting with the overlay."
        ),
    )
    rotk_launcher_parser.add_argument(
        "--tpm",
        action="store_true",
        help=(
            "Opt in to TPM attestation: the PowerShell stand-in answers ROTK"
            " Launcher's TPM proofs with this machine's real TPM. Off by"
            " default, which behaves exactly like a PC without a TPM."
        ),
    )
    rotk_launcher_parser.add_argument(
        "--tpm-endorsement",
        action="store_true",
        help=(
            "Opt in to the TPM's endorsement-key extras: send the EK"
            " certificate chain (fetched once from the TPM maker, AMD only)"
            " and let the TPM perform the server's credential activation."
            " Implies --tpm. Off by default; only useful if the server starts"
            " requiring it."
        ),
    )
    proton_parser = subparsers.add_parser(
        "proton",
        help=(
            "Install a Proton build in Steam, or update it to the latest"
            " release. Without a name, lists what can be installed."
        ),
    )
    proton_parser.add_argument(
        "name",
        nargs="?",
        choices=[source.key for source in proton_tools.SOURCES],
        help=(
            "ge: GE-Proton; svt-ge: SVT-GE-Proton, GE-Proton with the fixes"
            " ROTK Launcher needs."
        ),
    )
    proton_parser.add_argument(
        "--tag",
        default="",
        help=(
            "Install this release (e.g. GE-Proton10-4) instead of the latest."
        ),
    )
    proton_parser.add_argument(
        "--force",
        action="store_true",
        help="Install even if it is already installed.",
    )
    zemu_parser = subparsers.add_parser(
        "zemu",
        help=(
            "Install and run ZEmu Launcher (https://zemu.uk) for H1Z1: King of"
            " the Kill, pointed at your Proton."
        ),
    )
    zemu_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Install the latest ZEmu Launcher release even if one is already"
            " installed. Without this, an existing installation is left as-is"
            " (ZEmu Launcher updates itself once running); a newer release"
            " only triggers a log warning."
        ),
    )
    zemu_parser.add_argument(
        "--no-configure",
        action="store_true",
        help=(
            "Don't touch ZEmu Launcher's settings. Without this, its runtime"
            " follows the Proton used for Z1 Battle Royale; use this to pick"
            " one in ZEmu's own Properties screen instead."
        ),
    )
    zemu_parser.add_argument(
        "--proton",
        metavar="PATH",
        help=(
            "The Proton to use (its folder or its `proton` script). Defaults"
            " to the one Steam uses for Z1 Battle Royale, else the newest"
            " stable one installed."
        ),
    )
    _add_ds2_bindings_parser(subparsers)
    _add_ds2_controller_parser(subparsers)
    hwid_access_parser = subparsers.add_parser(
        "enable-hwid-access",
        help=(
            "Make the firmware serial numbers ROTK's hardware check reads"
            " readable by your user, and let your user use the TPM (one-time;"
            " uses sudo)."
        ),
    )
    hwid_access_parser.add_argument(
        "--disable",
        action="store_true",
        help="Undo it: make those files root-only again and remove the TPM rule.",
    )
    hwid_access_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Don't ask for confirmation before running the sudo step.",
    )

    return parser


def _shortcut_options(argv: Sequence[str], mode: str) -> list[str]:
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
        if option not in {"-y", "--yes", "--no-launch"}
    ]


def _launch_preset(  # noqa: PLR0913
    mode: str,
    steam: Steam | None,
    options: rotk_launcher.LaunchOptions,
    *,
    configure: bool = True,
    proton: Path | None = None,
    steam_command: list[str] | None = None,
    session: CompatSession | None = None,
) -> int:
    if mode == "hitman-peacock":
        return hitman_peacock.launch(steam=steam)
    if mode == "dsr-gadget":
        return dsr_gadget.launch(steam=steam)
    if mode == "silky-souls":
        return silky_souls.launch(steam=steam)
    if mode == "rotk-launcher":
        return rotk_launcher.launch(
            steam=steam,
            options=options,
            steam_command=steam_command,
            session=session,
        )
    if mode == "zemu":
        return zemu.launch(
            steam=steam,
            force=options.force,
            configure=configure,
            proton=proton,
        )
    raise NotImplementedError


def _install_proton(*, name: str | None, tag: str, force: bool) -> int:
    if sys.platform != "linux":
        logger.error("Proton is for Steam on Linux.")
        return 1
    try:
        steam = Steam.from_detection()
        if name is None:
            for source in proton_tools.SOURCES:
                found = proton_tools.installed_in(steam, source)
                print(
                    f"{source.key:7} {source.title}"
                    f" (https://github.com/{source.repository})"
                    + (
                        ": installed: "
                        + ", ".join(tool.internal_name for tool in found)
                        if found
                        else ""
                    )
                )
            return 0
        proton_tools.install(
            steam, proton_tools.source_for_key(name), tag=tag, force=force
        )
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as error:
        logger.error("Could not install Proton: %s", error)  # noqa: TRY400
        return 1
    return 0


def _set_ds2_bindings(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    if args.show and args.create:
        parser.error("--show and --create can't be used together.")
    if args.steam_id and not args.create:
        parser.error("--steam-id is only for --create.")
    try:
        steam_id = (
            ds2_bindings.parse_steam_id(args.steam_id)
            if args.steam_id
            else None
        )
    except ValueError as error:
        parser.error(str(error))
    needs_steam = not args.save or (args.create and steam_id is None)
    if needs_steam and sys.platform != "linux":
        parser.error("--save (and --steam-id) are required except on Linux.")
    return ds2_bindings.run(
        save=Path(args.save) if args.save else None,
        config=Path(args.config) if args.config else None,
        show=args.show,
        dry_run=args.dry_run,
        create=args.create,
        accept_terms=not args.no_accept_terms,
        steam_id=steam_id,
        steam=Steam.from_detection() if needs_steam else None,
    )


def _use_ds2_controller(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    if sys.platform != "linux":
        parser.error("Steam's controller layouts are only handled on Linux.")
    return ds2_controller.run(
        steam=Steam.from_detection(),
        layout_file=Path(args.layout) if args.layout else None,
        controller_types=args.controller_types or CONTROLLER_TYPES,
        keep_device_choices=args.keep_device_choices,
        assume_yes=args.yes,
        dry_run=args.dry_run,
        restart=not args.no_restart,
    )


def _run_ds2(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.mode == "ds2-controller":
        return _use_ds2_controller(args, parser)
    return _set_ds2_bindings(args, parser)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(args=argv)
    configure_logging(
        console_level=args.console_level or logging.WARNING,
        log_file_options=(
            None
            if not args.log_file
            else LogFileOptions(
                path=Path(args.log_file),
                max_kb=512,  # 0 for unbounded size and no rotation
                backup_count=1,  # 0 for no rolling backups
            )
        ),
    )
    steam: Steam | None = None
    if args.mode == "manual":
        if not args.command_line:
            parser.error("You must specify a command to run.")
        steam = Steam.from_detection()
        game_id = _resolve_game_id(args)
        child = steam.process_in_prefix(
            args.command_line, game_id=game_id
        ).start()
        if args.wait:
            logger.info("Waiting for pid=%s to exit...", child.pid)
            return_code = child.wait()
            logger.info(
                "pid=%s exited with status code %s", child.pid, return_code
            )
            return return_code
    elif args.mode == "enable-hwid-access":
        return hwid_access.run(disable=args.disable, assume_yes=args.yes)
    elif args.mode in {"ds2-bindings", "ds2-controller"}:
        return _run_ds2(args, parser)
    elif args.mode == "proton":
        return _install_proton(name=args.name, tag=args.tag, force=args.force)
    elif args.mode == "prefix-path":
        steam = Steam.from_detection()
        game_id = _resolve_game_id(args)
        print(steam.game_wine_prefix(game_id=game_id))
    elif args.mode in {
        "hitman-peacock",
        "dsr-gadget",
        "silky-souls",
        "rotk-launcher",
        "zemu",
    }:
        if sys.platform == "linux":
            steam = Steam.from_detection()
        proton = getattr(args, "proton", None)
        return _launch_preset(
            args.mode,
            steam,
            rotk_launcher.LaunchOptions(
                force=getattr(args, "force", False),
                tpm=getattr(args, "tpm", False),
                tpm_endorsement=getattr(args, "tpm_endorsement", False),
                allow_any_proton=getattr(args, "allow_any_proton", False),
                hud=(
                    rotk_launcher.DEFAULT_HUD
                    if getattr(args, "default_hud", False)
                    else getattr(args, "hud", None)
                ),
                max_fps=getattr(args, "max_fps", None),
                vsync=getattr(args, "vsync", None),
                no_steam_overlay=getattr(args, "no_steam_overlay", False),
                shortcut_options=_shortcut_options(
                    sys.argv[1:] if argv is None else argv, args.mode
                ),
                assume_yes=getattr(args, "yes", False),
                start_shortcut=not getattr(args, "no_launch", False),
            ),
            configure=not getattr(args, "no_configure", False),
            proton=Path(proton) if proton else None,
            steam_command=getattr(args, "steam_command", None),
            session=CompatSession.from_environment(os.environ),
        )
    return 0
