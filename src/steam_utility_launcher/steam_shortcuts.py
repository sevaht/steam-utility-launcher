"""Adding a non-Steam game to Steam's library.

Steam keeps a user's non-Steam games in `shortcuts.vdf` and the compatibility
tool chosen for each in `config.vdf`. Steam holds both in memory and writes
them back when it exits, so changes made while it runs are lost: it has to be
closed first.

A shortcut is described by a `ShortcutSpec`, and a `ShortcutManager` makes
Steam's library match it. How the shortcut runs this program is the spec's
`LaunchStyle`: on Linux Steam runs Proton and this program wraps its command
(`WrapperStyle`); on Windows Steam runs this program itself (`DirectStyle`).
"""

from __future__ import annotations

import logging
import ntpath
import re
import shlex
import shutil
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

from . import svt_ge_proton
from .file_saver import FileSaver
from .keyvalues import KeyValuesError, Section, Value
from .steam import Steam, SteamError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .host import Host
    from .steam import SteamClient, SteamLocation

logger = logging.getLogger(__name__)

_APP_ID_HIGH_BIT = 0x80000000
_UNSIGNED_32 = 0xFFFFFFFF
_SIGNED_32_LIMIT = 0x80000000
_COMPAT_PRIORITY = "250"  # what Steam itself writes for a chosen tool
_COMPAT_SECTION = "CompatToolMapping"
_COMMAND_PLACEHOLDER = "%command%"


class ShortcutError(SteamError):
    """The shortcut couldn't be added; the message says why."""


class ShortcutEntry:
    """One non-Steam game in `shortcuts.vdf`, as Steam writes it."""

    def __init__(self, fields: Section) -> None:
        self.fields = fields

    @staticmethod
    def app_id_for(exe: str, name: str) -> int:
        """The app id Steam gives a new non-Steam game: a checksum of the
        quoted target and the name. It is fixed when the game is added: later
        edits to the entry don't change it."""
        return zlib.crc32(f'"{exe}"{name}'.encode()) | _APP_ID_HIGH_BIT

    @staticmethod
    def to_stored(app_id: int) -> int:
        """The id as `shortcuts.vdf` stores it (a signed 32-bit integer)."""
        return app_id - (1 << 32) if app_id >= _SIGNED_32_LIMIT else app_id

    @staticmethod
    def from_stored(stored: int) -> int:
        return stored & _UNSIGNED_32

    @classmethod
    def create(
        cls,
        *,
        name: str,
        exe: str,
        start_dir: str,
        launch_options: str,
        icon: Path | None,
    ) -> ShortcutEntry:
        items: list[tuple[str, Value]] = [
            ("appid", cls.to_stored(cls.app_id_for(exe, name))),
            ("AppName", name),
            ("Exe", f'"{exe}"'),
            ("StartDir", f'"{start_dir}"'),
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
            ("tags", Section()),
        ]
        return cls(Section(items))

    @property
    def app_id(self) -> int:
        return self.from_stored(self.fields.integer("appid"))

    @property
    def name(self) -> str:
        return self.fields.find_string("AppName") or ""

    @name.setter
    def name(self, value: str) -> None:
        self.fields["AppName"] = value

    @property
    def launch_options(self) -> str:
        return self.fields.find_string("LaunchOptions") or ""

    @launch_options.setter
    def launch_options(self, value: str) -> None:
        self.fields["LaunchOptions"] = value

    @property
    def icon(self) -> str | None:
        return self.fields.find_string("icon")

    @icon.setter
    def icon(self, value: Path) -> None:
        self.fields["icon"] = str(value)

    @property
    def has_target(self) -> bool:
        """Whether it runs anything (Steam shows no icon for one that
        doesn't)."""
        return bool((self.fields.find_string("Exe") or "").strip('"'))

    def set_target(self, exe: str, start_dir: str) -> None:
        self.fields["Exe"] = f'"{exe}"'
        self.fields["StartDir"] = f'"{start_dir}"'


class ShortcutsFile:
    """A user's `shortcuts.vdf`."""

    def __init__(self, path: Path, data: Section, entries: Section) -> None:
        self.path = path
        self.data = data
        self._entries = entries

    @classmethod
    def load(cls, path: Path) -> ShortcutsFile:
        """The file's shortcuts; none if it doesn't exist yet."""
        if not path.exists():
            entries = Section()
            return cls(path, Section([("shortcuts", entries)]), entries)
        try:
            data = Section.from_binary(path.read_bytes())
        except (OSError, KeyValuesError) as error:
            msg = f"Could not read {path}: {error}"
            raise ShortcutError(msg) from error
        found = data.find_section("shortcuts")
        if found is None:
            msg = f"{path} is not a Steam shortcuts file."
            raise ShortcutError(msg)
        return cls(path, data, found)

    def find(self, *, name: str, launch_marker: str) -> ShortcutEntry | None:
        """The existing entry for this program: one with the name, or whose
        launch options run `launch_marker` (so a renamed entry is still
        found)."""
        for _, fields in self._entries.subsections():
            entry = ShortcutEntry(fields)
            if entry.name == name or launch_marker in entry.launch_options:
                return entry
        return None

    def add(self, entry: ShortcutEntry) -> None:
        """Appends the entry under the next free index (Steam's keys are "0",
        "1", ... in order)."""
        indexes = [int(key) for key in self._entries if key.isdigit()]
        self._entries[str(max(indexes, default=-1) + 1)] = entry.fields

    def to_bytes(self) -> bytes:
        return self.data.to_binary()

    def save(self, saver: FileSaver) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        saver.back_up(self.path)
        saver.write(self.path, self.to_bytes())


@dataclass(frozen=True)
class CompatToolConfig:
    """The text of Steam's `config.vdf`, as far as the compatibility tool
    chosen for each app goes."""

    text: str

    @staticmethod
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

    def with_tool(self, app_id: int, tool_name: str) -> CompatToolConfig:
        """The config with `tool_name` chosen for the app, added to its mapping
        by inserting one block, so nothing else in the file is touched. Raises
        ShortcutError if the file has no compatibility tool mapping."""
        section = re.search(
            rf'^(?P<indent>[ \t]*)"{_COMPAT_SECTION}"[ \t]*\n[ \t]*\{{',
            self.text,
            re.MULTILINE,
        )
        if section is None:
            msg = f"Steam's config.vdf has no {_COMPAT_SECTION} section."
            raise ShortcutError(msg)
        opening = section.end() - 1
        closing = self._closing_brace(self.text, opening)
        indent = section["indent"]
        block = (
            f'{indent}\t"{app_id}"\n'
            f"{indent}\t{{\n"
            f'{indent}\t\t"name"\t\t"{tool_name}"\n'
            f'{indent}\t\t"config"\t\t""\n'
            f'{indent}\t\t"priority"\t\t"{_COMPAT_PRIORITY}"\n'
            f"{indent}\t}}\n"
        )
        line_start = self.text.rfind("\n", 0, closing) + 1
        return CompatToolConfig(
            self.text[:line_start] + block + self.text[line_start:]
        )

    def tool_of(self, app_id: int) -> str:
        """The tool the config names for the app, or ""."""
        try:
            config = Section.from_text(self.text)
        except KeyValuesError:
            return ""
        return (
            config.find_string(
                "InstallConfigStore",
                "Software",
                "Valve",
                "Steam",
                _COMPAT_SECTION,
                str(app_id),
                "name",
            )
            or ""
        )


class LaunchStyle(ABC):
    """How a shortcut runs this program, and so what its target, launch
    options and compatibility tool are."""

    @staticmethod
    def for_host(host: Host, steam: Steam | None) -> LaunchStyle:
        """Where Steam runs games with Proton this program wraps Steam's
        command; elsewhere Steam runs this program itself."""
        if host.supports_proton:
            return WrapperStyle.for_steam(Steam.require(steam))
        return DirectStyle()

    @abstractmethod
    def exe(self, program: list[str]) -> str:
        """What the shortcut runs, for `program` (this program's command)."""

    @abstractmethod
    def start_dir(self, program: list[str]) -> str:
        """The folder the shortcut starts in."""

    @abstractmethod
    def compat_tool(self) -> str | None:
        """The compatibility tool a new shortcut has to use, if any. Raises
        ShortcutError if there has to be one and none can be."""

    @abstractmethod
    def _tokens(
        self, program: list[str], mode: str, options: list[str]
    ) -> list[str]:
        """The words of the launch options."""

    @abstractmethod
    def _span(self, tokens: list[str], mode: str) -> tuple[int, int] | None:
        """Where the options of `mode` are in the launch options' words."""

    def launch_options(
        self, program: list[str], mode: str, options: list[str]
    ) -> str:
        return shlex.join(self._tokens(program, mode, options))

    def options_in(self, launch_options: str, mode: str) -> list[str] | None:
        """The options given to `mode` in the launch options, or None if they
        aren't in the form this program writes."""
        try:
            tokens = shlex.split(launch_options)
        except ValueError:
            return None
        span = self._span(tokens, mode)
        return None if span is None else tokens[span[0] : span[1]]

    def with_options(
        self,
        launch_options: str,
        program: list[str],
        mode: str,
        options: list[str],
    ) -> str:
        """The launch options with the options of `mode` replaced, and
        anything else in them kept. If they aren't in the form this program
        writes they are written afresh."""
        try:
            tokens = shlex.split(launch_options)
        except ValueError:
            return self.launch_options(program, mode, options)
        span = self._span(tokens, mode)
        if span is None:
            return self.launch_options(program, mode, options)
        return shlex.join([*tokens[: span[0]], *options, *tokens[span[1] :]])


class WrapperStyle(LaunchStyle):
    """`program mode options -- %command%` around Steam's own command, for a
    shortcut Steam runs with a Proton. Its target does nothing, as the wrapper
    never uses it (it can't be empty: Steam shows no icon for a shortcut that
    has no target)."""

    _TARGET = "/usr/bin/true"
    _TARGET_DIRECTORY = "/usr/bin/"

    def __init__(self, tool_name: Callable[[], str]) -> None:
        self._tool_name = tool_name

    @classmethod
    def for_steam(cls, steam: Steam) -> WrapperStyle:
        """A style whose shortcuts use the newest SVT-GE-Proton of `steam`."""

        def newest() -> str:
            # By number, so that SVT-GE-Proton-11 is newer than 9.
            names = sorted(
                (
                    tool.internal_name
                    for tool in steam.compatibility_tools
                    if svt_ge_proton.is_svt_ge_proton(tool)
                ),
                key=lambda name: [
                    int(part) for part in re.findall(r"[0-9]+", name)
                ],
                reverse=True,
            )
            if not names:
                msg = (
                    "SVT-GE-Proton isn't installed, and the shortcut needs"
                    " it: run `steam-utility-launcher proton svt-ge`, restart"
                    " Steam, then run this again."
                )
                raise ShortcutError(msg)
            return names[0]

        return cls(newest)

    @override
    def exe(self, program: list[str]) -> str:
        return self._TARGET

    @override
    def start_dir(self, program: list[str]) -> str:
        return self._TARGET_DIRECTORY

    @override
    def compat_tool(self) -> str | None:
        return self._tool_name()

    @override
    def _tokens(
        self, program: list[str], mode: str, options: list[str]
    ) -> list[str]:
        return [*program, mode, *options, "--", _COMMAND_PLACEHOLDER]

    @override
    def _span(self, tokens: list[str], mode: str) -> tuple[int, int] | None:
        """From after the mode word to the `--` that precedes Steam's
        command."""
        try:
            start = tokens.index(mode) + 1
            end = tokens.index("--", start)
        except ValueError:
            return None
        return start, end


class DirectStyle(LaunchStyle):
    """Steam runs this program itself, with `mode --run-from-steam options`
    (the marker tells it Steam started it, and not the user), and no
    compatibility tool."""

    MARKER = "--run-from-steam"

    @override
    def exe(self, program: list[str]) -> str:
        return program[0]

    @override
    def start_dir(self, program: list[str]) -> str:
        return ntpath.dirname(program[0]) + "\\"

    @override
    def compat_tool(self) -> str | None:
        return None

    @override
    def _tokens(
        self, program: list[str], mode: str, options: list[str]
    ) -> list[str]:
        return [*program[1:], mode, self.MARKER, *options]

    @override
    def _span(self, tokens: list[str], mode: str) -> tuple[int, int] | None:
        """Everything after the marker."""
        try:
            return tokens.index(self.MARKER) + 1, len(tokens)
        except ValueError:
            return None


@dataclass(frozen=True)
class ShortcutSpec:
    """A non-Steam game this program keeps in Steam's library: it runs
    `program mode options`, in the `style`. `icon` is the image file the entry
    shows (it needn't exist yet). `artwork` is a directory of Steam's library
    images (a game's folder in `appcache/librarycache`) whose banner, capsules
    and logo the entry gets, unless it already has its own."""

    name: str
    program: list[str]
    mode: str
    options: list[str]
    style: LaunchStyle
    icon: Path | None = None
    artwork: Path | None = None


# Steam's library images by the name they have in a game's folder in
# `appcache/librarycache`, and the suffix they have in the `grid` folder, where
# a non-Steam game's custom images go: `<app id><suffix>`.
_ARTWORK = (
    ("library_hero.jpg", "_hero.jpg"),  # the banner at the top of the page
    ("library_600x900.jpg", "p.jpg"),  # the portrait capsule
    ("header.jpg", ".jpg"),  # the wide capsule
    ("logo.png", "_logo.png"),  # the logo over the banner
)


@dataclass
class ShortcutManager:
    """Makes a Steam installation's library have the shortcuts asked for."""

    location: SteamLocation
    client: SteamClient
    ask: Callable[[str], str] = field(default=input)

    @property
    def _shortcuts_path(self) -> Path:
        return self.location.account_directory() / "config" / "shortcuts.vdf"

    def ensure(
        self,
        spec: ShortcutSpec,
        *,
        assume_yes: bool = False,
        launch: bool = True,
    ) -> int:
        """Makes sure Steam's library has the shortcut, and, with `launch`,
        has Steam start it. Returns its app id. Raises SteamError, saying why,
        if that can't be so.

        A missing entry is added. An existing one is left alone unless the
        options were given and differ from its own, or its icon isn't the
        spec's, or it has no target (Steam shows no icon then), in which case
        just those are replaced. Steam keeps its library in memory, so it is
        closed for a change (after asking unless `assume_yes`; the files are
        backed up first) and started again by the launch. Nothing is closed
        for an entry that is fine as it is.
        """
        shortcuts = ShortcutsFile.load(self._shortcuts_path)
        existing = shortcuts.find(name=spec.name, launch_marker=spec.mode)
        if existing is not None:
            app_id = existing.app_id
            self._update_existing(
                shortcuts, existing, spec, assume_yes=assume_yes
            )
        else:
            app_id = self._add(shortcuts, spec, assume_yes=assume_yes)
        if spec.artwork is not None:
            self.install_artwork(app_id, spec.artwork)
        if launch:
            print("Starting it through Steam...")
            self.client.launch_game(app_id)
        else:
            print("Start Steam and launch it from there.")
        return app_id

    def _add(
        self, shortcuts: ShortcutsFile, spec: ShortcutSpec, *, assume_yes: bool
    ) -> int:
        style = spec.style
        tool_name = style.compat_tool()
        self.client.close_for_change(
            self.ask, assume_yes=assume_yes, why="add the shortcut"
        )
        entry = ShortcutEntry.create(
            name=spec.name,
            exe=style.exe(spec.program),
            start_dir=style.start_dir(spec.program),
            launch_options=style.launch_options(
                spec.program, spec.mode, spec.options
            ),
            icon=spec.icon,
        )
        app_id = entry.app_id
        new_config: CompatToolConfig | None = None
        config_path = self.location.config_vdf
        if tool_name is not None:
            try:
                config_text = config_path.read_text(encoding="utf-8")
            except OSError as error:
                msg = f"Could not read {config_path}: {error}"
                raise ShortcutError(msg) from error
            new_config = CompatToolConfig(config_text).with_tool(
                app_id, tool_name
            )
            if new_config.tool_of(app_id) != tool_name:
                msg = (
                    "Could not add the compatibility tool to Steam's"
                    " config.vdf."
                )
                raise ShortcutError(msg)
        shortcuts.add(entry)
        saver = FileSaver()
        shortcuts.save(saver)
        if new_config is not None:
            saver.back_up(config_path)
            saver.write(config_path, new_config.text.encode("utf-8"))
        logger.info(
            "Added %s (app id %d) to %s", spec.name, app_id, shortcuts.path
        )
        print(
            f'Added "{spec.name}" to your Steam library'
            + (f" with {tool_name}." if tool_name else ".")
        )
        return app_id

    def _update_existing(
        self,
        shortcuts: ShortcutsFile,
        existing: ShortcutEntry,
        spec: ShortcutSpec,
        *,
        assume_yes: bool,
    ) -> None:
        style = spec.style
        label = existing.name
        current = existing.launch_options
        new_options = (
            bool(spec.options)
            and style.options_in(current, spec.mode) != spec.options
        )
        new_icon = spec.icon is not None and existing.icon != str(spec.icon)
        new_target = not existing.has_target
        # Only the name changes: the entry keeps its app id, and so its prefix.
        new_name = label != spec.name
        if not (new_options or new_icon or new_target or new_name):
            print(f'"{label}" is already in your Steam library.')
            return
        changes = [
            *(["name"] if new_name else []),
            *(["launch options"] if new_options else []),
            *(["icon"] if new_icon else []),
            *(["target"] if new_target else []),
        ]
        self.client.close_for_change(
            self.ask,
            assume_yes=assume_yes,
            why=f'update the {" and ".join(changes)} of "{label}"',
        )
        if new_name:
            existing.name = spec.name
        if new_options:
            existing.launch_options = style.with_options(
                current, spec.program, spec.mode, spec.options
            )
        if new_icon and spec.icon is not None:
            existing.icon = spec.icon
        if new_target:
            existing.set_target(
                style.exe(spec.program), style.start_dir(spec.program)
            )
        shortcuts.save(FileSaver())
        print(f'Updated the {" and ".join(changes)} of "{label}" in Steam.')
        if new_options:
            print(f"    {existing.launch_options}")

    def install_artwork(self, app_id: int, source: Path) -> list[str]:
        """Gives the non-Steam game `app_id` the library images in `source`,
        which Steam made for a game of its own, as its custom images. Images
        the game already has are left alone (they may be the user's choice),
        and so are ones `source` doesn't have. Returns the names of the ones
        copied. Failing to copy is only a warning: the images are
        decoration."""
        try:
            grid = self.location.account_directory() / "config" / "grid"
            copied = []
            for name, suffix in _ARTWORK:
                image = source / name
                target = grid / f"{app_id}{suffix}"
                if image.is_file() and not target.exists():
                    grid.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(image, target)
                    copied.append(target.name)
        except (OSError, SteamError):
            logger.warning(
                "Could not copy the images to Steam.", exc_info=True
            )
            return []
        if copied:
            print(
                f"Gave it the images {', '.join(copied)} (restart Steam to"
                " see)."
            )
        return copied
