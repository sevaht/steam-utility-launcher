"""The commands that set up the system for the others: Proton builds, and
access to the hardware identity."""

from __future__ import annotations

import logging
import tarfile
from typing import TYPE_CHECKING, override

from steam_utility_launcher import hwid_access, proton_tools
from steam_utility_launcher.commands.base import Command, Context
from steam_utility_launcher.errors import UserError

if TYPE_CHECKING:
    import argparse

    from steam_utility_launcher.host import Host

logger = logging.getLogger(__name__)


class ProtonCommand(Command):
    name = "proton"
    help = (
        "Install a Proton build in Steam, or update it to the latest"
        " release. Without a name, lists what can be installed."
    )

    proton_only_because = "Proton is for Steam on Linux."

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        parser.add_argument(
            "name",
            nargs="?",
            choices=[source.key for source in proton_tools.SOURCES],
            help=(
                "ge: GE-Proton; svt-ge: SVT-GE-Proton, GE-Proton with the"
                " fixes ROTK Launcher needs."
            ),
        )
        parser.add_argument(
            "--tag",
            default="",
            help=(
                "Install this release (e.g. GE-Proton10-4) instead of the"
                " latest."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Install even if it is already installed.",
        )

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        try:
            steam = context.steam
            if args.name is None:
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
                steam,
                proton_tools.source_for_key(args.name),
                tag=args.tag,
                force=args.force,
            )
        except (OSError, RuntimeError, ValueError, tarfile.TarError) as error:
            msg = f"Could not install Proton: {error}"
            raise UserError(msg) from error
        return 0


class HwidAccessCommand(Command):
    name = "enable-hwid-access"
    help = (
        "Make the firmware serial numbers ROTK's hardware check reads"
        " readable by your user, and let your user use the TPM (one-time;"
        " uses sudo)."
    )

    proton_only_because = (
        "It is for the Proton prefix ROTK Launcher runs in; Windows already"
        " lets programs read this."
    )

    @override
    def add_arguments(
        self, parser: argparse.ArgumentParser, host: Host
    ) -> None:
        parser.add_argument(
            "--disable",
            action="store_true",
            help=(
                "Undo it: make those files root-only again and remove the"
                " TPM rule."
            ),
        )
        parser.add_argument(
            "-y",
            "--yes",
            action="store_true",
            help="Don't ask for confirmation before running the sudo step.",
        )

    @override
    def run(self, args: argparse.Namespace, context: Context) -> int:
        return hwid_access.run(disable=args.disable, assume_yes=args.yes)
