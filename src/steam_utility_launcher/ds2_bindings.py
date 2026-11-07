"""Sets Dark Souls II: Scholar of the First Sin's controls from a file.

The game keeps them in its save file; see ds2_save.
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Protocol

from steam_utility_launcher import ds2_save
from steam_utility_launcher.ds2_controls import (
    Controls,
    KeyBinding,
    MouseBinding,
    action_name,
    parse_action,
)
from steam_utility_launcher.ds2_inputs import (
    KEYS,
    MODIFIERS,
    MOUSE_BUTTONS,
    CodeNames,
)
from steam_utility_launcher.ds2_newsave import new_save, steam_id_text
from steam_utility_launcher.errors import UserError
from steam_utility_launcher.file_saver import FileSaver
from steam_utility_launcher.host import Host

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from steam_utility_launcher.programs import WindowsFileSystem
    from steam_utility_launcher.steam import SteamLocation

logger = logging.getLogger(__name__)

SAVE_FILE_NAME = "DS2SOFS0000.sl2"
_CONFIG_RESOURCE = "ds2_key_bindings.toml"
_GAME_EXECUTABLE = "DarkSoulsII.exe"
_SAVES_DIRECTORY_NAME = "DarkSoulsII"
_KEYBOARD = "keyboard"
_MOUSE = "mouse"
_MODIFIER = "modifier"
_STEAM_ID_DECIMAL = re.compile(r"\d{17}")
_STEAM_ID_HEX = re.compile(r"[0-9a-fA-F]{16}")


class ConfigError(ValueError):
    pass


def _parse_code(
    names: CodeNames, value: object, *, where: str, what: str
) -> int:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        msg = f"{where}: {what} must be a name or a number, not {value!r}"
        raise ConfigError(msg)
    try:
        return names.parse(value)
    except ValueError as error:
        msg = f"{where}: {error}"
        raise ConfigError(msg) from error


def _parse_binding[B](
    value: object,
    *,
    where: str,
    names: CodeNames,
    field: str,
    make: Callable[[int, int], B],
) -> B:
    """A binding from a name or number, or from a table with the `field` (the
    key or button) and, if there is one, a `modifier`."""
    if not isinstance(value, dict):
        return make(_parse_code(names, value, where=where, what=field), 0)
    unknown = sorted(set(value) - {field, _MODIFIER})
    if unknown:
        msg = f"{where}: unknown field(s) {unknown}; use {field!r} and {_MODIFIER!r}"
        raise ConfigError(msg)
    if field not in value:
        msg = f"{where}: no {field!r}"
        raise ConfigError(msg)
    return make(
        _parse_code(names, value[field], where=where, what=field),
        _parse_code(
            MODIFIERS, value.get(_MODIFIER, 0), where=where, what=_MODIFIER
        ),
    )


def _parse_section[B](
    section: object,
    name: str,
    *,
    names: CodeNames,
    field: str,
    make: Callable[[int, int], B],
) -> dict[int, B]:
    if not isinstance(section, dict):
        msg = f"[{name}] must be a table"
        raise ConfigError(msg)
    bindings: dict[int, B] = {}
    for action_text, value in section.items():
        where = f"[{name}] {action_text}"
        try:
            action = parse_action(action_text)
        except ValueError as error:
            msg = f"{where}: {error}"
            raise ConfigError(msg) from error
        if action in bindings:
            msg = f"{where}: that action is listed twice"
            raise ConfigError(msg)
        bindings[action] = _parse_binding(
            value, where=where, names=names, field=field, make=make
        )
    return bindings


def parse_config(text: str) -> Controls:
    """The controls a TOML config describes: a `[keyboard]` and a `[mouse]`
    section, each an action's name and what to set it to."""
    try:
        table = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        msg = f"not valid TOML: {error}"
        raise ConfigError(msg) from error
    unknown = sorted(set(table) - {_KEYBOARD, _MOUSE})
    if unknown:
        msg = f"unknown section(s) {unknown}; use [{_KEYBOARD}] and [{_MOUSE}]"
        raise ConfigError(msg)
    return Controls(
        keyboard=_parse_section(
            table.get(_KEYBOARD, {}),
            _KEYBOARD,
            names=KEYS,
            field="key",
            make=KeyBinding,
        ),
        mouse=_parse_section(
            table.get(_MOUSE, {}),
            _MOUSE,
            names=MOUSE_BUTTONS,
            field="button",
            make=MouseBinding,
        ),
    )


def load_config(path: Path | None = None) -> Controls:
    """The controls to set: from the file at `path`, or the bundled one."""
    if path is None:
        source = resources.files(__package__) / "resources" / _CONFIG_RESOURCE
        text = source.read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    try:
        return parse_config(text)
    except ConfigError as error:
        msg = f"the key bindings file is not valid: {error}"
        raise ConfigError(msg) from error


def _toml_code(names: CodeNames, code: int) -> str:
    name = names.name(code)
    return name if name.startswith("0x") else json.dumps(name)


def _toml_binding(
    names: CodeNames, field: str, code: int, modifier: int
) -> str:
    if not modifier:
        return _toml_code(names, code)
    return (
        f"{{ {field} = {_toml_code(names, code)},"
        f" {_MODIFIER} = {_toml_code(MODIFIERS, modifier)} }}"
    )


def config_text(controls: Controls) -> str:
    """TOML that `parse_config` reads back as these controls."""
    lines = []
    if controls.keyboard:
        lines.append(f"[{_KEYBOARD}]")
        lines.extend(
            f"{action_name(action)} = "
            + _toml_binding(KEYS, "key", binding.key, binding.modifier)
            for action, binding in sorted(controls.keyboard.items())
        )
    if controls.mouse:
        lines.extend(["", f"[{_MOUSE}]"] if lines else [f"[{_MOUSE}]"])
        lines.extend(
            f"{action_name(action)} = "
            + _toml_binding(
                MOUSE_BUTTONS, "button", binding.button, binding.modifier
            )
            for action, binding in sorted(controls.mouse.items())
        )
    return "\n".join(lines) + "\n"


class _Binding(Protocol):
    @property
    def code(self) -> int: ...

    @property
    def modifier(self) -> int: ...


def _name(names: CodeNames, binding: _Binding) -> str:
    name = names.name(binding.code)
    if binding.modifier:
        return f"{MODIFIERS.name(binding.modifier)}+{name}"
    return name


def _change_lines[B: _Binding](
    table: str,
    names: CodeNames,
    current: Mapping[int, B],
    changes: Mapping[int, B],
) -> list[str]:
    return [
        f"  {table} {action_name(action)}: "
        + (_name(names, current[action]) if action in current else "(none)")
        + " -> "
        + _name(names, binding)
        for action, binding in sorted(changes.items())
    ]


def parse_steam_id(text: str) -> int:
    """A 64 bit Steam id from its digits, or from the 16 hex digits a save's
    folder is named by."""
    text = text.strip()
    if _STEAM_ID_DECIMAL.fullmatch(text):
        return int(text)
    if _STEAM_ID_HEX.fullmatch(text):
        return int(text, 16)
    msg = (
        f"not a Steam id: {text!r} (the 17 digit id, or the 16 hex digits"
        " of the folder the save is in)"
    )
    raise ValueError(msg)


class Ds2Saves:
    """Where the game keeps its saves on a system: a folder for each Steam
    account, in the roaming application data of the Windows the game runs in
    (its Proton prefix on Linux)."""

    def __init__(self, filesystem: WindowsFileSystem) -> None:
        self.filesystem = filesystem

    @property
    def directory(self) -> Path:
        return self.filesystem.roaming_appdata / _SAVES_DIRECTORY_NAME

    def path_for(self, steam_id: int) -> Path:
        """Where the save of a Steam account is, or would be."""
        return self.directory / steam_id_text(steam_id) / SAVE_FILE_NAME

    def find(self) -> Path:
        """The game's save file."""
        found = sorted(self.directory.glob(f"*/{SAVE_FILE_NAME}"))
        if not found:
            msg = (
                f"no save file under {self.directory}; pass --create to make"
                " one, or point to one with --save"
            )
            raise FileNotFoundError(msg)
        if len(found) > 1:
            msg = "more than one save file found; pick one with --save:\n" + (
                "\n".join(f"  {path}" for path in found)
            )
            raise FileNotFoundError(msg)
        return found[0]


def game_is_running(host: Host | None = None) -> bool:
    """Whether this user has the game running (in Proton, Wine or Windows)."""
    return (host or Host.current()).processes.is_running_with_command_line(
        _GAME_EXECUTABLE
    )


def _report(current: Controls, changes: Controls) -> list[str]:
    return [
        *_change_lines(_KEYBOARD, KEYS, current.keyboard, changes.keyboard),
        *_change_lines(_MOUSE, MOUSE_BUTTONS, current.mouse, changes.mouse),
    ]


@dataclass(frozen=True)
class _Plan:
    """What is to be written, and what to tell about it."""

    path: Path
    save: ds2_save.SaveFile
    report: list[str]
    replaces: bool

    def write(self) -> str | None:
        """Writes it, after backing up what it replaces; the backup's name,
        if there was one."""
        saver = FileSaver()
        backup = saver.back_up(self.path) if self.replaces else None
        saver.write(self.path, self.save.data)
        return backup.name if backup else None


def _update_plan(path: Path, config: Path | None) -> _Plan | None:
    """The save at `path` set to the config, or None if it already is."""
    save = ds2_save.SaveFile.read(path)
    changes = load_config(config).changes_from(save.controls)
    if not changes:
        return None
    return _Plan(
        path=path,
        save=save.with_controls(changes),
        report=_report(save.controls, changes),
        replaces=True,
    )


def _create_plan(
    path: Path, steam_id: int, config: Path | None, *, accept_terms: bool
) -> _Plan:
    """A new save, with the config's controls, to be made at `path`."""
    if path.exists():
        msg = f"{path} already exists; --create would replace it"
        raise FileExistsError(msg)
    save = new_save(steam_id, terms_accepted=accept_terms)
    changes = load_config(config).changes_from(save.controls)
    report = [
        f"Making a new save for Steam account {steam_id}"
        f" ({steam_id_text(steam_id)})"
        + (", with the terms and conditions accepted" if accept_terms else ""),
        *_report(save.controls, changes),
    ]
    return _Plan(
        path=path,
        save=save.with_controls(changes) if changes else save,
        report=report,
        replaces=False,
    )


@dataclass(frozen=True, kw_only=True)
class BindingsRequest:
    """What `ds2 bindings` was asked to do."""

    save: Path | None = None
    config: Path | None = None
    show: bool = False
    dry_run: bool = False
    create: bool = False
    accept_terms: bool = True
    steam_id: int | None = None
    saves: Ds2Saves | None = None
    location: SteamLocation | None = None
    host: Host | None = None


class BindingsEditor:
    """Sets the controls in a save, or makes a save that has them."""

    def __init__(self, request: BindingsRequest) -> None:
        self.request = request

    @property
    def _saves(self) -> Ds2Saves:
        if self.request.saves is None:
            msg = "pass --save to say which save file to use"
            raise UserError(msg)
        return self.request.saves

    @property
    def _account(self) -> int:
        if self.request.steam_id is not None:
            return self.request.steam_id
        if self.request.location is None:
            msg = "pass --steam-id to say which account the save is for"
            raise UserError(msg)
        return self.request.location.account_steam_id()

    def _plan(self) -> _Plan | None:
        request = self.request
        if not request.create:
            path = request.save or self._saves.find()
            return _update_plan(path, request.config)
        account = self._account
        path = request.save or self._saves.path_for(account)
        return _create_plan(
            path, account, request.config, accept_terms=request.accept_terms
        )

    def _show(self) -> None:
        path = self.request.save or self._saves.find()
        print(config_text(ds2_save.SaveFile.read(path).controls), end="")

    def run(self) -> None:
        """Does what was asked, and says what it did. Raises UserError if the
        game is running, which would undo it."""
        if self.request.show:
            self._show()
            return
        plan = self._plan()
        if plan is None:
            print("The key bindings are already as configured.")
            return
        report = "\n".join(plan.report)
        if self.request.dry_run:
            print(f"{report}\n(dry run: nothing was changed)")
            return
        if game_is_running(self.request.host):
            msg = (
                "Close Dark Souls II first: it rewrites its save file when it"
                " exits, which would undo this."
            )
            raise UserError(msg)
        backup = plan.write()
        done = f"{'Updated' if plan.replaces else 'Made'} {plan.path}"
        print(
            f"{report}\n{done}"
            + (f"\nBacked up the old one as {backup}" if backup else "")
        )
