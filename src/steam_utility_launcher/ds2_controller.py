"""Has Steam use this program's controller layout for Dark Souls II: Scholar of
the First Sin, whatever controller is used.

The layout sends the keys that the controls set by ds2_bindings use from a
gamepad's buttons. Steam keeps a layout for each kind of controller, so it is
put in place for each kind (see steam_input).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from steam_utility_launcher.games import DS2_SOTFS_ID
from steam_utility_launcher.steam import SteamError
from steam_utility_launcher.steam_input import (
    CONTROLLER_TYPES,
    ControllerConfigs,
    ControllerLayout,
    ControllerType,
    FileChange,
    plan_device_choices,
    plan_layouts,
    read_text,
    selected_template,
    write_changes,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from steam_utility_launcher.steam import Steam

logger = logging.getLogger(__name__)

_APP_ID = int(DS2_SOTFS_ID)
_GAME = "Dark Souls II: Scholar of the First Sin"


def _describe(
    configs: ControllerConfigs,
    layouts: Sequence[ControllerLayout],
    devices: dict[Path, ControllerLayout],
    change: FileChange,
) -> str:
    was = selected_template(read_text(change.path) or "", _APP_ID)
    was_text = f" (was {was})" if was else ""
    if change.path in devices:
        return (
            f"  choose {devices[change.path].title!r} for the controller in"
            f" {change.path.name}{was_text}"
        )
    for layout in layouts:
        if change.path == configs.selection_path(layout.controller_type):
            return (
                f"  choose {layout.title!r} for {layout.controller_type}"
                + was_text
            )
    return f"  write the layout to {change.path}"


def _device_warnings(unresolved: dict[str, str]) -> list[str]:
    return [
        f"  {name} chooses {template}, for one controller, which may take"
        " priority over the choice for its kind"
        for name, template in unresolved.items()
    ]


def _print_warnings(warnings: list[str]) -> None:
    if warnings:
        print("Note, selections for particular controllers:")
        print("\n".join(warnings))


def run(  # noqa: PLR0913
    *,
    steam: Steam,
    layout_file: Path | None = None,
    controller_types: Sequence[ControllerType] = CONTROLLER_TYPES,
    keep_device_choices: bool = False,
    assume_yes: bool = False,
    dry_run: bool = False,
    restart: bool = True,
    ask: Callable[[str], str] = input,
) -> int:
    try:
        base = (
            ControllerLayout.from_file(layout_file)
            if layout_file
            else ControllerLayout.bundled()
        )
        layouts = [base.for_type(kind) for kind in controller_types]
        location = steam.location
        configs = ControllerConfigs(
            location, int(location.account_directory().name)
        )
        changes = plan_layouts(
            configs, _APP_ID, layouts, creator=location.account_steam_id()
        )
        device_changes, unresolved = plan_device_choices(
            configs, _APP_ID, layouts
        )
        if keep_device_choices:
            unresolved |= {
                change.path.name: selected_template(
                    read_text(change.path) or "", _APP_ID
                )
                or ""
                for change, _ in device_changes
            }
            device_changes = []
        devices = {change.path: layout for change, layout in device_changes}
        changes += [change for change, _ in device_changes]
        warnings = _device_warnings(unresolved)
        kinds = ", ".join(layout.controller_type for layout in layouts)
        if not changes:
            print(f"Steam already uses {base.title!r} for {_GAME} ({kinds}).")
            _print_warnings(warnings)
            return 0
        report = "\n".join(
            _describe(configs, layouts, devices, c) for c in changes
        )
        if dry_run:
            print(f"{report}\n(dry run: nothing was changed)")
            _print_warnings(warnings)
            return 0
        closed = location.close_client_for_change(
            ask,
            assume_yes=assume_yes,
            why=f"change {_GAME}'s controller layout",
        )
        backups = write_changes(changes)
        if closed and restart:
            location.start_client()
    except (OSError, ValueError, SteamError) as error:
        logger.error("Could not set the layout: %s", error)  # noqa: TRY400
        return 1
    print(report)
    if backups:
        print("Backed up: " + ", ".join(backup.name for backup in backups))
    if closed:
        print("Steam was restarted." if restart else "Start Steam again.")
    print(f"Steam uses {base.title!r} for {_GAME} with: {kinds}.")
    _print_warnings(warnings)
    return 0
