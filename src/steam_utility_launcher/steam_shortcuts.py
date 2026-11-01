"""Adding a non-Steam game to Steam's library (Linux).

Steam keeps a user's non-Steam games in `shortcuts.vdf` and the compatibility
tool chosen for each in `config.vdf`. Steam holds both in memory and writes
them back when it exits, so changes made while it runs are lost: it has to be
closed first.
"""

from __future__ import annotations

import collections
import collections.abc
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import vdf  # type: ignore[import-untyped]

from . import svt_ge_proton

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from .steam import Steam, SteamLocation

logger = logging.getLogger(__name__)

_STEAM_ID64_BASE = 76561197960265728
_APP_ID_HIGH_BIT = 0x80000000
_UNSIGNED_32 = 0xFFFFFFFF
_SIGNED_32_LIMIT = 0x80000000
_COMPAT_PRIORITY = "250"  # what Steam itself writes for a chosen tool
_SHUTDOWN_WAIT_SECONDS = 90
_SHUTDOWN_POLL_SECONDS = 1.0
_COMPAT_SECTION = "CompatToolMapping"
# What a new shortcut runs: a program that does nothing, as the wrapper never
# uses the target. It can't be empty: Steam shows no icon for a shortcut that
# has no target.
_TARGET = "/usr/bin/true"
_TARGET_DIRECTORY = "/usr/bin/"
# The low bits of the id Steam uses for a non-Steam game (app id << 32 | this).
_NON_STEAM_GAME_FLAG = 0x02000000


class ShortcutError(RuntimeError):
    """The shortcut couldn't be added; the message says why."""


def shortcut_app_id(exe: str, name: str) -> int:
    """The app id Steam gives a new non-Steam game: a checksum of the quoted
    target and the name. It is fixed when the game is added: later edits to
    the entry don't change it."""
    return zlib.crc32(f'"{exe}"{name}'.encode()) | _APP_ID_HIGH_BIT


def _signed(app_id: int) -> int:
    """The id as `shortcuts.vdf` stores it (a signed 32-bit integer)."""
    return app_id - (1 << 32) if app_id >= _SIGNED_32_LIMIT else app_id


def _unsigned(stored: int) -> int:
    return stored & _UNSIGNED_32


def account_directory(location: SteamLocation) -> Path:
    """`userdata/<account>` of the Steam account in use: the one that last
    signed in (or the only one there is)."""
    try:
        users = vdf.load(
            (location.root / "config" / "loginusers.vdf").open(
                encoding="utf-8"
            )
        )["users"]
    except (OSError, KeyError, SyntaxError) as error:
        msg = f"Could not read Steam's accounts: {error}"
        raise ShortcutError(msg) from error
    recent = [
        steam_id
        for steam_id, user in users.items()
        if any(
            key.lower() == "mostrecent" and value == "1"
            for key, value in user.items()
        )
    ]
    candidates = recent or list(users)
    if len(candidates) != 1:
        msg = (
            "Could not tell which Steam account to add the shortcut to; sign"
            " in to the one you want in Steam, then try again."
        )
        raise ShortcutError(msg)
    account = int(candidates[0]) - _STEAM_ID64_BASE
    return location.root / "userdata" / str(account)


def load_shortcuts(path: Path) -> collections.OrderedDict[str, object]:
    """The shortcuts file as {"shortcuts": {"0": {...}, ...}}; empty if it
    doesn't exist yet."""
    if not path.exists():
        return collections.OrderedDict(shortcuts=collections.OrderedDict())
    try:
        data = vdf.binary_loads(
            path.read_bytes(), mapper=collections.OrderedDict
        )
    except (OSError, SyntaxError, ValueError, KeyError) as error:
        msg = f"Could not read {path}: {error}"
        raise ShortcutError(msg) from error
    if not isinstance(data.get("shortcuts"), collections.OrderedDict):
        msg = f"{path} is not a Steam shortcuts file."
        raise ShortcutError(msg)
    return data  # type: ignore[no-any-return]


def find_shortcut(
    data: Mapping[str, object], *, name: str, launch_marker: str
) -> collections.abc.MutableMapping[str, object] | None:
    """The existing entry for this program: one with the name, or whose launch
    options run `launch_marker` (so a renamed entry is still found)."""
    shortcuts = data["shortcuts"]
    assert isinstance(shortcuts, collections.abc.Mapping)  # noqa: S101
    for entry in shortcuts.values():
        if entry.get("AppName") == name or launch_marker in str(
            entry.get("LaunchOptions", "")
        ):
            return entry  # type: ignore[no-any-return]
    return None


def new_entry(
    *, name: str, launch_options: str, icon: Path | None
) -> collections.OrderedDict[str, object]:
    """An entry as Steam writes one. The target does nothing: the wrapper
    never uses it, since it runs the program instead of what Steam passes."""
    exe = _TARGET
    return collections.OrderedDict(
        [
            ("appid", _signed(shortcut_app_id(exe, name))),
            ("AppName", name),
            ("Exe", f'"{exe}"'),
            ("StartDir", f'"{_TARGET_DIRECTORY}"'),
            ("icon", str(icon) if icon else ""),
            ("ShortcutPath", ""),
            ("LaunchOptions", launch_options),
            ("IsHidden", 0),
            ("AllowDesktopConfig", 1),
            ("AllowOverlay", 1),
            ("OpenVR", 0),
            ("Devkit", 0),
            ("DevkitGameID", ""),
            ("DevkitOverrideAppID", 0),
            ("LastPlayTime", 0),
            ("FlatpakAppID", ""),
            ("sortas", ""),
            ("tags", collections.OrderedDict()),
        ]
    )


def add_entry(
    data: collections.OrderedDict[str, object],
    entry: collections.OrderedDict[str, object],
) -> None:
    """Appends the entry under the next free index (Steam's keys are "0",
    "1", ... in order)."""
    shortcuts = data["shortcuts"]
    assert isinstance(shortcuts, collections.OrderedDict)  # noqa: S101
    indexes = [int(key) for key in shortcuts if key.isdigit()]
    shortcuts[str(max(indexes, default=-1) + 1)] = entry


def _closing_brace(text: str, opening: int) -> int:
    """Where the brace opened at `opening` is closed (braces inside quoted
    strings don't count)."""
    depth = 0
    in_string = False
    index = opening
    while index < len(text):
        char = text[index]
        if in_string:
            if char == "\\":
                index += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    msg = "unbalanced braces"
    raise ValueError(msg)


def with_compat_tool(config_text: str, app_id: int, tool_name: str) -> str:
    """`config.vdf` with `tool_name` chosen for the app, added to its mapping
    by inserting one block, so nothing else in the file is touched. Raises
    ShortcutError if the file has no compatibility tool mapping."""
    section = re.search(
        rf'^(?P<indent>[ \t]*)"{_COMPAT_SECTION}"[ \t]*\n[ \t]*\{{',
        config_text,
        re.MULTILINE,
    )
    if section is None:
        msg = f"Steam's config.vdf has no {_COMPAT_SECTION} section."
        raise ShortcutError(msg)
    opening = section.end() - 1
    closing = _closing_brace(config_text, opening)
    indent = section["indent"]
    block = (
        f'{indent}\t"{app_id}"\n'
        f"{indent}\t{{\n"
        f'{indent}\t\t"name"\t\t"{tool_name}"\n'
        f'{indent}\t\t"config"\t\t""\n'
        f'{indent}\t\t"priority"\t\t"{_COMPAT_PRIORITY}"\n'
        f"{indent}\t}}\n"
    )
    line_start = config_text.rfind("\n", 0, closing) + 1
    return config_text[:line_start] + block + config_text[line_start:]


def compat_tool_of(config_text: str, app_id: int) -> str:
    """The tool config.vdf names for the app, or ""."""
    try:
        config = vdf.loads(config_text)
        mapping = config["InstallConfigStore"]["Software"]["Valve"]["Steam"][
            _COMPAT_SECTION
        ]
        return str(mapping[str(app_id)]["name"])
    except (KeyError, SyntaxError, TypeError):
        return ""


def steam_is_running() -> bool:
    """Whether this user has a Steam client running."""
    uid = os.getuid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (
                entry / "comm"
            ).read_text().strip() == "steam" and entry.stat().st_uid == uid:
                return True
        except OSError:
            continue
    return False


def shut_down_steam(location: SteamLocation) -> None:
    """Asks Steam to exit and waits until it has."""
    program = shutil.which("steam") or str(location.root / "steam.sh")
    logger.info("Closing Steam...")
    try:
        subprocess.run(  # noqa: S603
            [program, "-shutdown"], check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        msg = f"Could not tell Steam to close: {error}"
        raise ShortcutError(msg) from error
    deadline = time.monotonic() + _SHUTDOWN_WAIT_SECONDS
    while steam_is_running():
        if time.monotonic() > deadline:
            msg = "Steam did not close; close it yourself and try again."
            raise ShortcutError(msg)
        time.sleep(_SHUTDOWN_POLL_SECONDS)


def launch_through_steam(location: SteamLocation, app_id: int) -> None:
    """Has Steam start the non-Steam game, starting Steam first if it isn't
    running (the game then starts once it is up). Steam runs on its own, not
    as a child of this program."""
    game_id = (app_id << 32) | _NON_STEAM_GAME_FLAG
    program = shutil.which("steam") or str(location.root / "steam.sh")
    try:
        subprocess.Popen(  # noqa: S603
            [program, f"steam://rungameid/{game_id}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        msg = f"Could not start the game through Steam: {error}"
        raise ShortcutError(msg) from error


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005


def _back_up(path: Path, stamp: str) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.sul-backup-{stamp}"))


def _write_atomically(path: Path, content: bytes) -> None:
    staging = path.with_name(path.name + ".sul-new")
    staging.write_bytes(content)
    staging.replace(path)


_COMMAND_PLACEHOLDER = "%command%"


def launch_options_for(
    program: list[str], mode: str, options: list[str]
) -> str:
    """The launch options that run `program mode options` as a wrapper around
    Steam's own command."""
    return shlex.join([*program, mode, *options, "--", _COMMAND_PLACEHOLDER])


def _options_span(tokens: list[str], mode: str) -> tuple[int, int] | None:
    """Where the options of `mode` are in the launch options' words: from
    after the mode word to the `--` that precedes Steam's command."""
    try:
        start = tokens.index(mode) + 1
        end = tokens.index("--", start)
    except ValueError:
        return None
    return start, end


def options_in(launch_options: str, mode: str) -> list[str] | None:
    """The options given to `mode` in the launch options, or None if they
    aren't in the form this program writes."""
    try:
        tokens = shlex.split(launch_options)
    except ValueError:
        return None
    span = _options_span(tokens, mode)
    return None if span is None else tokens[span[0] : span[1]]


def with_options(
    launch_options: str, program: list[str], mode: str, options: list[str]
) -> str:
    """The launch options with the options of `mode` replaced, and anything
    else in them (environment variables, a different path to this program,
    what follows the `--`) kept. If they aren't in the form this program
    writes they are written afresh."""
    try:
        tokens = shlex.split(launch_options)
    except ValueError:
        return launch_options_for(program, mode, options)
    span = _options_span(tokens, mode)
    if span is None:
        return launch_options_for(program, mode, options)
    return shlex.join([*tokens[: span[0]], *options, *tokens[span[1] :]])


def _ask_to_close_steam(
    ask: Callable[[str], str], *, assume_yes: bool, why: str
) -> bool:
    if assume_yes:
        return True
    try:
        answer = ask(
            f"Steam has to be closed to {why} (it saves its library when it"
            " exits, which would undo the change). Close Steam now? [y/N] "
        )
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _close_steam_for_change(
    location: SteamLocation,
    ask: Callable[[str], str],
    *,
    assume_yes: bool,
    why: str,
) -> None:
    """Gets Steam out of the way of a change to its files, if it is running."""
    if not steam_is_running():
        return
    if not _ask_to_close_steam(ask, assume_yes=assume_yes, why=why):
        msg = f"Close Steam, then run this again to {why}."
        raise ShortcutError(msg)
    shut_down_steam(location)


def _svt_tool_name(steam: Steam) -> str:
    # By number, so that SVT-GE-Proton-11 is newer than SVT-GE-Proton-9.
    names = sorted(
        (
            tool.internal_name
            for tool in steam.compatibility_tools
            if svt_ge_proton.is_svt_ge_proton(tool)
        ),
        key=lambda name: [int(part) for part in re.findall(r"[0-9]+", name)],
        reverse=True,
    )
    if not names:
        msg = (
            "SVT-GE-Proton isn't installed, and the shortcut needs it: run"
            " `steam-utility-launcher proton svt-ge`, restart Steam, then run"
            " this again."
        )
        raise ShortcutError(msg)
    return names[0]


def ensure_shortcut(  # noqa: PLR0913
    steam: Steam,
    *,
    name: str,
    program: list[str],
    mode: str,
    options: list[str],
    icon: Path | None = None,
    assume_yes: bool = False,
    launch: bool = True,
    ask: Callable[[str], str] = input,
) -> int | None:
    """Makes sure Steam's library has a non-Steam game `name` that runs
    `program mode options -- %command%` with an SVT-GE-Proton compatibility tool,
    and, with `launch`, has Steam start it. Returns its app id, or None, having
    said why, if that isn't so. `icon` is the image file the entry shows; the
    file needn't exist yet.

    A missing entry is added. An existing one is left alone unless `options`
    were given and differ from its own, or `icon` isn't its icon, or it has no
    target (Steam shows no icon then), in which case just those are replaced. Steam keeps its library in memory, so it is closed for a change
    (after asking unless `assume_yes`; the files are backed up first) and
    started again by the launch. Nothing is closed for an entry that is fine
    as it is.
    """
    location = steam.location
    try:
        account = account_directory(location)
        shortcuts_path = account / "config" / "shortcuts.vdf"
        data = load_shortcuts(shortcuts_path)
        existing = find_shortcut(data, name=name, launch_marker=mode)
        if existing is not None:
            app_id = _unsigned(int(str(existing["appid"])))
            _update_existing(
                location,
                shortcuts_path,
                data,
                existing,
                program=program,
                mode=mode,
                options=options,
                icon=icon,
                assume_yes=assume_yes,
                ask=ask,
            )
        else:
            tool_name = _svt_tool_name(steam)
            _close_steam_for_change(
                location, ask, assume_yes=assume_yes, why="add the shortcut"
            )
            app_id = _write_shortcut(
                location,
                shortcuts_path,
                data,
                name=name,
                launch_options=launch_options_for(program, mode, options),
                tool_name=tool_name,
                icon=icon,
            )
            print(f'Added "{name}" to your Steam library with {tool_name}.')
        if launch:
            print("Starting it through Steam...")
            launch_through_steam(location, app_id)
        else:
            print("Start Steam and launch it from there.")
    except ShortcutError as error:
        logger.error("%s", error)  # noqa: TRY400
        return None
    return app_id


def _update_existing(  # noqa: PLR0913
    location: SteamLocation,
    shortcuts_path: Path,
    data: collections.OrderedDict[str, object],
    existing: collections.abc.MutableMapping[str, object],
    *,
    program: list[str],
    mode: str,
    options: list[str],
    icon: Path | None,
    assume_yes: bool,
    ask: Callable[[str], str],
) -> None:
    label = existing.get("AppName")
    current = str(existing.get("LaunchOptions", ""))
    new_options = bool(options) and options_in(current, mode) != options
    new_icon = icon is not None and existing.get("icon") != str(icon)
    new_target = not str(existing.get("Exe", "")).strip('"')
    if not (new_options or new_icon or new_target):
        print(f'"{label}" is already in your Steam library.')
        return
    changes = [
        *(["launch options"] if new_options else []),
        *(["icon"] if new_icon else []),
        *(["target"] if new_target else []),
    ]
    _close_steam_for_change(
        location,
        ask,
        assume_yes=assume_yes,
        why=f'update the {" and ".join(changes)} of "{label}"',
    )
    if new_options:
        existing["LaunchOptions"] = with_options(
            current, program, mode, options
        )
    if new_icon:
        existing["icon"] = str(icon)
    if new_target:
        existing["Exe"] = f'"{_TARGET}"'
        existing["StartDir"] = f'"{_TARGET_DIRECTORY}"'
    _back_up(shortcuts_path, _stamp())
    _write_atomically(shortcuts_path, vdf.binary_dumps(data))
    print(f'Updated the {" and ".join(changes)} of "{label}" in Steam.')
    if new_options:
        print(f"    {existing['LaunchOptions']}")


def _write_shortcut(  # noqa: PLR0913
    location: SteamLocation,
    shortcuts_path: Path,
    data: collections.OrderedDict[str, object],
    *,
    name: str,
    launch_options: str,
    tool_name: str,
    icon: Path | None,
) -> int:
    config_path = location.config_vdf
    try:
        config_text = config_path.read_text(encoding="utf-8")
    except OSError as error:
        msg = f"Could not read {config_path}: {error}"
        raise ShortcutError(msg) from error
    entry = new_entry(name=name, launch_options=launch_options, icon=icon)
    app_id = _unsigned(int(str(entry["appid"])))
    new_config = with_compat_tool(config_text, app_id, tool_name)
    if compat_tool_of(new_config, app_id) != tool_name:
        msg = "Could not add the compatibility tool to Steam's config.vdf."
        raise ShortcutError(msg)
    add_entry(data, entry)
    stamp = _stamp()
    shortcuts_path.parent.mkdir(parents=True, exist_ok=True)
    _back_up(shortcuts_path, stamp)
    _back_up(config_path, stamp)
    _write_atomically(shortcuts_path, vdf.binary_dumps(data))
    _write_atomically(config_path, new_config.encode("utf-8"))
    logger.info("Added %s (app id %d) to %s", name, app_id, shortcuts_path)
    return app_id
