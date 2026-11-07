from __future__ import annotations

import argparse
import copy
import logging
import os
import sys
from dataclasses import KW_ONLY, dataclass
from logging import Handler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .commands import Command, Context, UsageError, default_commands
from .errors import UserError
from .host import Host, UnsupportedHostError
from .steam import CompatSession

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


class _ConsoleFormatter(logging.Formatter):
    """A failure that is a `UserError` is shown on the console as its message,
    with no traceback (the log file keeps the traceback)."""

    def format(self, record: logging.LogRecord) -> str:
        error = record.exc_info[1] if record.exc_info else None
        if not isinstance(error, UserError):
            return super().format(record)
        shown = copy.copy(record)
        shown.msg = str(error)
        shown.args = ()
        shown.exc_info = None
        shown.exc_text = None
        return super().format(shown)


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
        _ConsoleFormatter(fmt="{levelname:s}: {message:s}", style="{")
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


def _build_parser(
    commands: Sequence[Command], host: Host
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Starts applications inside the Proton prefix of a Steam game."
            if host.supports_proton
            else "Installs and starts game utilities."
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
    for command in commands:
        command.add_arguments(
            subparsers.add_parser(
                command.name, aliases=command.aliases, help=command.help
            ),
            host,
        )
    return parser


def main(
    argv: Sequence[str] | None = None, *, host: Host | None = None
) -> int:
    try:
        host = host or Host.current()
    except UnsupportedHostError as error:
        print(error, file=sys.stderr)
        return 1
    commands = default_commands()
    parser = _build_parser(commands, host)
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
    command = next(
        command
        for command in commands
        if args.mode in (command.name, *command.aliases)
    )
    reason = command.unsupported_reason(host)
    if reason is not None:
        logger.error("%s can't be used here. %s", command.name, reason)
        return 1
    context = Context(
        host=host,
        argv=sys.argv[1:] if argv is None else argv,
        session=CompatSession.from_environment(os.environ),
    )
    try:
        return command.run(args, context)
    except UsageError as error:
        parser.error(str(error))
    except UserError:
        logger.exception("%s failed", command.name)
        return 1
