from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import KW_ONLY, dataclass
from logging import Handler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from . import dsr_gadget, hitman_peacock, silky_souls
from .steam import Steam

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
    manual_game_id_group = manual_parser.add_mutually_exclusive_group(
        required=True
    )
    manual_game_id_group.add_argument(
        "-a",
        "--auto",
        action="store_true",
        help="Automatically detect the game presently running in proton.",
    )
    manual_game_id_group.add_argument(
        "-g",
        "--game-id",
        help=(
            "The steam appid of the game.  Present in the store page URL."
            "  Dark Souls Remastered is 570940, for example."
        ),
    )
    manual_parser.add_argument(
        "command_line",
        nargs=argparse.REMAINDER,
        help="The command line of the binary to execute.",
    )
    subparsers.add_parser(
        "hitman-peacock", help="Run Hitman's Peacock private server."
    )
    subparsers.add_parser(
        "dsr-gadget", help="Run the Dark Souls: Remastered gadget."
    )
    subparsers.add_parser(
        "silky-souls", help="Run the Dark Souls: Remastered SilkySouls tool."
    )

    return parser


def _launch_preset(mode: str, steam: Steam | None) -> int:
    if mode == "hitman-peacock":
        return hitman_peacock.launch(steam=steam)
    if mode == "dsr-gadget":
        return dsr_gadget.launch(steam=steam)
    if mode == "silky-souls":
        return silky_souls.launch(steam=steam)
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

        if args.auto:
            game_id = Steam.running_proton_game_id()
            if not game_id:
                msg = "Could not detect a game running in proton."
                raise RuntimeError(msg)
        else:
            game_id = args.game_id
        steam.process_in_prefix(args.command_line, game_id=game_id).start()
    elif args.mode in {"hitman-peacock", "dsr-gadget", "silky-souls"}:
        if sys.platform == "linux":
            steam = Steam.from_detection()
        return _launch_preset(args.mode, steam)
    return 0
