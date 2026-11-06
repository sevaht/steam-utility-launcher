"""Has Steam use this program's controller layout for Dark Souls II: Scholar of
the First Sin, whatever controller is used.

The layout sends the keys that the controls set by ds2_bindings use from a
gamepad's buttons. Steam keeps a layout for each kind of controller, so it is
put in place for each kind (see steam_input).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from steam_utility_launcher.games import DS2_SOTFS_ID
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

    from steam_utility_launcher.steam import SteamClient, SteamLocation

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


@dataclass(frozen=True, kw_only=True)
class ControllerRequest:
    """What `ds2-controller` was asked to do."""

    location: SteamLocation
    client: SteamClient
    layout_file: Path | None = None
    controller_types: Sequence[ControllerType] = CONTROLLER_TYPES
    keep_device_choices: bool = False
    assume_yes: bool = False
    dry_run: bool = False
    restart: bool = True
    ask: Callable[[str], str] = input


class LayoutInstaller:
    """Has Steam use the bundled controller layout for the game."""

    def __init__(self, request: ControllerRequest) -> None:
        self.request = request

    def run(self) -> None:
        """Does what was asked, and says what it did."""
        request = self.request
        location = request.location
        base = (
            ControllerLayout.from_file(request.layout_file)
            if request.layout_file
            else ControllerLayout.bundled()
        )
        layouts = [base.for_type(kind) for kind in request.controller_types]
        configs = ControllerConfigs(
            location, int(location.account_directory().name)
        )
        changes = plan_layouts(
            configs, _APP_ID, layouts, creator=location.account_steam_id()
        )
        device_changes, unresolved = plan_device_choices(
            configs, _APP_ID, layouts
        )
        if request.keep_device_choices:
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
            return
        report = "\n".join(
            _describe(configs, layouts, devices, c) for c in changes
        )
        if request.dry_run:
            print(f"{report}\n(dry run: nothing was changed)")
            _print_warnings(warnings)
            return
        closed = request.client.close_for_change(
            request.ask,
            assume_yes=request.assume_yes,
            why=f"change {_GAME}'s controller layout",
        )
        backups = write_changes(changes)
        if closed and request.restart:
            request.client.start()
        print(report)
        if backups:
            print("Backed up: " + ", ".join(backup.name for backup in backups))
        if closed:
            print(
                "Steam was restarted."
                if request.restart
                else "Start Steam again."
            )
        print(f"Steam uses {base.title!r} for {_GAME} with: {kinds}.")
        _print_warnings(warnings)
