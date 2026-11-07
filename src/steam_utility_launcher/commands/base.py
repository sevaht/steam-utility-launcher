"""What every subcommand is built from."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from steam_utility_launcher.errors import UserError
from steam_utility_launcher.steam import (
    CompatSession,
    Steam,
    SteamClient,
    SteamLocation,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from steam_utility_launcher.host import Host


class UsageError(Exception):
    """The arguments given make no sense together; the message says why."""


def argument_type[T](parse: Callable[[str], T]) -> Callable[[str], T]:
    """An argparse `type` from a function that raises ValueError for text it
    can't use, which argparse then reports as a usage error."""

    def convert(text: str) -> T:
        try:
            return parse(text)
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error)) from None

    return convert


@dataclass
class Context:
    """What a command runs with: the system, the command line as given, what
    Steam says of the launch if it launched this program, and Steam (found
    when a command asks for it, so commands that don't need it don't depend
    on it)."""

    host: Host
    argv: Sequence[str]
    session: CompatSession | None = None
    _steam: Steam | None = field(default=None, init=False, repr=False)

    @property
    def steam(self) -> Steam:
        """Steam and its compatibility tools."""
        if self._steam is None:
            self._steam = Steam.from_detection(self.host)
        return self._steam

    @property
    def steam_if_proton(self) -> Steam | None:
        """Steam, on a system that runs Windows programs with Proton."""
        return self.steam if self.host.supports_proton else None

    @property
    def location(self) -> SteamLocation:
        return SteamLocation.from_detection(self.host)

    @property
    def client(self) -> SteamClient:
        return SteamClient(self.location, self.host)


class Command(ABC):
    """A subcommand: its name and arguments, and what it does."""

    name: str
    help: str
    # Other names the subcommand answers to.
    aliases: tuple[str, ...] = ()
    # Why the command is only for systems that run Windows programs with
    # Proton, if it is.
    proton_only_because: str | None = None

    def unsupported_reason(self, host: Host) -> str | None:
        """Why the command can't be used on this system, if it can't."""
        if self.proton_only_because and not host.supports_proton:
            return self.proton_only_because
        return None

    @abstractmethod
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        """Gives the subcommand's parser its arguments. Those that only mean
        something on some systems are only added there."""

    @abstractmethod
    def run(self, args: argparse.Namespace, context: Context) -> int:
        """Does it, and returns the exit status. Raises UsageError for
        arguments that can't be used together."""


class CommandGroup(Command):
    """A subcommand made of subcommands: `group name <subcommand> ...`."""

    subcommands: Sequence[Command]

    def _dest(self) -> str:
        return f"{self.name}_subcommand"

    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        subparsers = parser.add_subparsers(dest=self._dest(), required=True)
        for command in self.subcommands:
            command.add_arguments(
                subparsers.add_parser(
                    command.name, aliases=command.aliases, help=command.help
                ),
                host,
            )

    def run(self, args: argparse.Namespace, context: Context) -> int:
        chosen = getattr(args, self._dest())
        command = next(
            command
            for command in self.subcommands
            if chosen in (command.name, *command.aliases)
        )
        reason = command.unsupported_reason(context.host)
        if reason is not None:
            msg = f"{self.name} {command.name} can't be used here. {reason}"
            raise UserError(msg)
        return command.run(args, context)
