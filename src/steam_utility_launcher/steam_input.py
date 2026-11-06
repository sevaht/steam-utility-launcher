"""Steam Input controller layouts: putting one in place for a game, and having
Steam use it.

A layout is a VDF file Steam keeps for each game it has been set up for, in
`Steam Controller Configs/<account>/config/<app id>/`, named from its title.
Which one a game uses for a kind of controller is chosen in that kind's
`configset_<controller type>.vdf`, next to the folders of layouts. Steam keeps
these in memory and writes them when it exits, so it has to be closed for a
change.

Both are edited as text, not parsed and written back, so what is not changed
is left exactly as Steam wrote it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

from steam_utility_launcher.file_saver import FileSaver
from steam_utility_launcher.keyvalues import KeyValuesError, Section
from steam_utility_launcher.steam import SteamError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from steam_utility_launcher.steam import SteamLocation

_LAYOUT_RESOURCE = "ds2_controller_layout.vdf"
_CONFIGS_FOLDER = "Steam Controller Configs"
_CREATOR = "creator"
_URL = "url"
_TITLE = "title"
_CONTROLLER_TYPE = "controller_type"
_CONTROLLER_CAPS = "controller_caps"
_DEVICE_SELECTION_PREFIX = "configset_controller_"


@dataclass(frozen=True)
class ControllerType:
    """A kind of controller Steam has layouts of its own for, and so a file
    of its own that says which a game uses."""

    name: str  # as Steam names it, like `controller_ps5`
    label: str  # for people
    # What Steam writes as the `controller_caps` of a layout for it. It says
    # what the controller has, and Steam writes 0 for some, so what is not
    # known here is 0.
    caps: int


# The kinds a gamepad layout applies to, the ones whose caps Steam wrote in
# layouts it saved (see the notes on each). The Xbox Elite is not one: Steam
# keeps what it uses for an Elite in the Xbox One's file.
CONTROLLER_TYPES = (
    ControllerType("controller_generic", "Generic", 1590271),
    ControllerType("controller_xbox360", "Xbox 360", 0),
    ControllerType("controller_xboxone", "Xbox One", 546849791),
    ControllerType("controller_ps4", "PS4", 0),
    ControllerType("controller_ps5", "PS5", 0),
    ControllerType("controller_switch_pro", "Switch Pro", 68963327),
)


def parse_controller_type(text: str) -> ControllerType:
    """A type from its name with or without `controller_`, or its label, in
    any case (`ps5`, `Controller_PS5`, `Switch Pro`)."""
    wanted = text.strip().lower().replace(" ", "_").replace("-", "_")
    wanted = wanted.removeprefix("controller_")
    for controller_type in CONTROLLER_TYPES:
        names = {
            controller_type.name.removeprefix("controller_"),
            controller_type.label.lower().replace(" ", "_"),
        }
        if wanted in names:
            return controller_type
    known = ", ".join(
        t.name.removeprefix("controller_") for t in CONTROLLER_TYPES
    )
    msg = f"unknown controller type {text!r}; use one of: {known}"
    raise ValueError(msg)


def _metadata_pattern(key: str) -> re.Pattern[str]:
    """A line of the layout's own metadata: one tab in, so not one of the
    translations or the groups' lines further in."""
    return re.compile(rf'^\t"{key}"\t\t"([^"\n]*)"$', re.MULTILINE)


@dataclass(frozen=True)
class ControllerLayout:
    """A controller layout, as the text of the file Steam keeps it in."""

    text: str

    @classmethod
    def bundled(cls) -> ControllerLayout:
        """The layout this program ships (see resources/)."""
        source = resources.files(__package__) / "resources" / _LAYOUT_RESOURCE
        return cls(source.read_text(encoding="utf-8"))

    @classmethod
    def from_file(cls, path: Path) -> ControllerLayout:
        return cls(path.read_text(encoding="utf-8"))

    def _value(self, key: str) -> str:
        found = _metadata_pattern(key).search(self.text)
        if found is None:
            msg = f"the controller layout has no {key!r}"
            raise SteamError(msg)
        return found[1]

    def _with_value(self, key: str, value: str) -> ControllerLayout:
        _ = self._value(key)  # that it is there
        line = f'\t"{key}"\t\t"{value}"'
        return ControllerLayout(
            _metadata_pattern(key).sub(lambda _: line, self.text, count=1)
        )

    @property
    def title(self) -> str:
        return self._value(_TITLE)

    @property
    def controller_type(self) -> str:
        """The kind of controller it is for, like `controller_generic`."""
        return self._value(_CONTROLLER_TYPE)

    @property
    def name(self) -> str:
        """What Steam calls it: its title, in lower case, and an index (a
        layout of that title is `_0`)."""
        title = self.title
        if not title or "/" in title or "\0" in title:
            msg = f"the controller layout's title can't be used: {title!r}"
            raise SteamError(msg)
        return f"{title.lower()}_0"

    def prepared(self, *, creator: int, url: str) -> ControllerLayout:
        """The layout as made by this Steam account, and saved at `url`: what
        a layout has to say of where it came from, which is not shipped."""
        return self._with_value(_CREATOR, str(creator))._with_value(_URL, url)

    def for_type(self, controller_type: ControllerType) -> ControllerLayout:
        """The same layout for another kind of controller: the layout is of
        the buttons and sticks by where they are, so only what says what it is
        for changes, and its title, to tell them apart in Steam. A layout
        already for that kind is returned as it is."""
        if self.controller_type == controller_type.name:
            return self
        return (
            self._with_value(_TITLE, f"{self.title} - {controller_type.label}")
            ._with_value(_CONTROLLER_TYPE, controller_type.name)
            ._with_value(_CONTROLLER_CAPS, str(controller_type.caps))
        )


def read_text(path: Path) -> str | None:
    """What a file has, or None if there isn't one."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        msg = f"Could not read {path}: {error}"
        raise SteamError(msg) from error


@dataclass(frozen=True)
class FileChange:
    """A file to write: its whole new content."""

    path: Path
    content: str

    @property
    def needed(self) -> bool:
        return read_text(self.path) != self.content


def _app_block(app_id: int) -> re.Pattern[str]:
    """An app's entry in a configset file: a line with its id, then its fields
    in braces, one tab in."""
    return re.compile(
        rf'^\t"{app_id}"\n\t\{{\n(?:\t\t[^\n]*\n)*\t\}}\n', re.MULTILINE
    )


def selected_template(configset: str, app_id: int) -> str | None:
    """The layout a configset has chosen for an app (as `CLOUD_<app
    id>/<name>`), or None if it has not chosen one by name."""
    try:
        config = Section.from_text(configset)
    except KeyValuesError as error:
        msg = f"the controller selection file is not valid: {error}"
        raise SteamError(msg) from error
    return config.find_string("controller_config", str(app_id), "template")


def with_selection(configset: str | None, app_id: int, template: str) -> str:
    """The configset with the app set to use `template`: its entry replaced if
    it has one, added if not, and the rest left as it was."""
    block = f'\t"{app_id}"\n\t{{\n\t\t"template"\t\t"{template}"\n\t}}\n'
    if not configset or not configset.strip():
        return f'"controller_config"\n{{\n{block}}}\n'
    pattern = _app_block(app_id)
    if pattern.search(configset):
        return pattern.sub(lambda _: block, configset, count=1)
    closing = configset.rstrip().rfind("}")
    if closing < 0 or not configset.lstrip().startswith('"controller_config"'):
        msg = "the controller selection file is not in the form Steam writes"
        raise SteamError(msg)
    return configset[:closing] + block + configset[closing:]


@dataclass(frozen=True)
class ControllerConfigs:
    """Where one Steam account keeps its controller layouts and which it uses."""

    location: SteamLocation
    account: int

    @property
    def directory(self) -> Path:
        return (
            self.location.steamapps
            / "common"
            / _CONFIGS_FOLDER
            / str(self.account)
            / "config"
        )

    def layout_path(self, app_id: int, layout: ControllerLayout) -> Path:
        return self.directory / str(app_id) / f"{layout.name}.vdf"

    def selection_path(self, controller_type: str) -> Path:
        return self.directory / f"configset_{controller_type}.vdf"

    def autosave_url(self, app_id: int, controller_type: str) -> str:
        """Where Steam says a layout was last saved to as it was edited."""
        path = self.directory / str(app_id) / f"{controller_type}.vdf"
        return f"autosave://{path}"


def plan_layouts(
    configs: ControllerConfigs,
    app_id: int,
    layouts: Sequence[ControllerLayout],
    *,
    creator: int,
) -> list[FileChange]:
    """The files to write so that the app uses these layouts, each for the
    kind of controller it is for. Only the ones that would change are listed.
    """
    changes = []
    for layout in layouts:
        prepared = layout.prepared(
            creator=creator,
            url=configs.autosave_url(app_id, layout.controller_type),
        )
        selection_path = configs.selection_path(layout.controller_type)
        template = f"CLOUD_{app_id}/{layout.name}"
        changes += [
            FileChange(configs.layout_path(app_id, layout), prepared.text),
            FileChange(
                selection_path,
                with_selection(read_text(selection_path), app_id, template),
            ),
        ]
    return [change for change in changes if change.needed]


def device_selections(
    configs: ControllerConfigs, app_id: int
) -> dict[str, str]:
    """The layouts chosen for the app for particular controllers (by their
    USB ids, not their kind): each file's name, and the layout it names. They
    may take priority over the choice for the kind of controller."""
    chosen = {}
    for path in sorted(configs.directory.glob("configset_*.vdf")):
        if path.name.startswith(_DEVICE_SELECTION_PREFIX):
            continue
        template = selected_template(read_text(path) or "", app_id)
        if template is not None:
            chosen[path.name] = template
    return chosen


def plan_device_choices(
    configs: ControllerConfigs,
    app_id: int,
    layouts: Sequence[ControllerLayout],
) -> tuple[list[tuple[FileChange, ControllerLayout]], dict[str, str]]:
    """The changes that make the choices for particular controllers (see
    `device_selections`) choose these layouts instead, each the one for the
    kind of controller the layout it chose now is for (found in that layout's
    own file), and the layout it becomes. Only the ones that would change are
    listed. A choice this can't tell the kind of (its layout isn't in the
    app's folder, or is for a kind there is no layout for here) is left as it
    is, and returned: the file's name, and what it chooses."""
    ours = {layout.controller_type: layout for layout in layouts}
    prefix = f"CLOUD_{app_id}/"
    changes = []
    unresolved = {}
    for name, template in device_selections(configs, app_id).items():
        layout = None
        if template.startswith(prefix):
            chosen = (
                configs.directory
                / str(app_id)
                / f"{template[len(prefix):]}.vdf"
            )
            try:
                kind = ControllerLayout.from_file(chosen).controller_type
            except (OSError, SteamError):
                kind = None
            layout = ours.get(kind) if kind else None
        if layout is None:
            if template not in {f"{prefix}{item.name}" for item in layouts}:
                unresolved[name] = template
            continue
        path = configs.directory / name
        change = FileChange(
            path,
            with_selection(read_text(path), app_id, f"{prefix}{layout.name}"),
        )
        if change.needed:
            changes.append((change, layout))
    return changes, unresolved


def write_changes(changes: list[FileChange]) -> list[Path]:
    """Writes the files, each whole or not at all, after backing up the ones
    that are there. The backups' paths."""
    saver = FileSaver()
    backups = []
    for change in changes:
        backup = saver.back_up(change.path)
        if backup is not None:
            backups.append(backup)
        saver.write(change.path, change.content.encode("utf-8"))
    return backups
