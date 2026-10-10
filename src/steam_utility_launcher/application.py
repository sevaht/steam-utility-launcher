from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import KW_ONLY, dataclass
from logging import Handler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from . import hwid_access
from .steam import Steam
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Starts applications inside the Proton prefix of a Steam game."
        )
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
        help="Install and run ROTK Launcher for Z1 Battle Royale.",
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


def _launch_preset(  # noqa: PLR0913
    mode: str,
    steam: Steam | None,
    *,
    force: bool,
    configure: bool = True,
    proton: Path | None = None,
    tpm: bool = False,
    tpm_endorsement: bool = False,
) -> int:
    if mode == "hitman-peacock":
        return hitman_peacock.launch(steam=steam)
    if mode == "dsr-gadget":
        return dsr_gadget.launch(steam=steam)
    if mode == "silky-souls":
        return silky_souls.launch(steam=steam)
    if mode == "rotk-launcher":
        return rotk_launcher.launch(
            steam=steam, force=force, tpm=tpm, tpm_endorsement=tpm_endorsement
        )
    if mode == "zemu":
        return zemu.launch(
            steam=steam, force=force, configure=configure, proton=proton
        )
    raise NotImplementedError


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
            force=getattr(args, "force", False),
            configure=not getattr(args, "no_configure", False),
            proton=Path(proton) if proton else None,
            tpm=getattr(args, "tpm", False),
            tpm_endorsement=getattr(args, "tpm_endorsement", False),
        )
    return 0
