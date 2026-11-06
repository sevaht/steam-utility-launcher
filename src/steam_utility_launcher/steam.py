from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from steam_utility_launcher.errors import UserError
from steam_utility_launcher.host import Host
from steam_utility_launcher.keyvalues import KeyValuesError, Section
from steam_utility_launcher.wine_prefix import WinePrefix

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

_STEAM_ID64_BASE = 76561197960265728
_SHUTDOWN_WAIT_SECONDS = 90
_SHUTDOWN_POLL_SECONDS = 1.0
# The low bits of the id Steam uses for a non-Steam game (app id << 32 | this).
_NON_STEAM_GAME_FLAG = 0x02000000


class SteamError(UserError):
    """Steam's files or client don't allow what was asked; the message says
    why."""


def _wine_beside(proton_script: Path) -> Path | None:
    """Proton's own `wine`, which sits beside its `proton` script, or None."""
    wine = proton_script.parent / "files" / "bin" / "wine"
    return wine if wine.exists() else None


class RunningProcess(Protocol):
    """What this program asks of a program it has started, which is what a
    `subprocess.Popen` offers."""

    @property
    def pid(self) -> int: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...


@dataclass
class Process:
    command_line: list[str]
    env: dict[str, str] | None = None
    cwd: Path | None = None
    utility_binary_name: str | None = None
    prefix_runner: list[str] | None = None
    registry_set: dict[str, str] = field(default_factory=dict)
    registry_unset: set[str] = field(default_factory=set)

    def _registry_runner(self) -> list[str]:
        if not self.prefix_runner:
            return []
        proton_binary = Path(self.prefix_runner[0])
        if proton_binary.name == "proton":
            wine_binary = _wine_beside(proton_binary)
            if wine_binary is not None:
                return [str(wine_binary)]
        return list(self.prefix_runner)

    def _query_override_value(self, key: str) -> str | None:
        registry_runner = self._registry_runner()
        if not registry_runner:
            return None
        command = [
            *registry_runner,
            "reg",
            "query",
            r"HKCU\Software\Wine\DllOverrides",
            "/v",
            key,
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                env=self.env,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Timed out querying Wine override %s", key)
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if "REG_SZ" in line:
                _, value = line.split("REG_SZ", maxsplit=1)
                return value.strip()
        return None

    def _apply_registry_overrides(self) -> None:
        registry_runner = self._registry_runner()
        if not registry_runner:
            return
        for key, value in self.registry_set.items():
            existing_value = self._query_override_value(key)
            if existing_value == value:
                continue
            command = [
                *registry_runner,
                "reg",
                "add",
                r"HKCU\Software\Wine\DllOverrides",
                "/v",
                key,
                "/d",
                value,
                "/f",
            ]
            try:
                subprocess.run(command, check=True, env=self.env, timeout=15)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                logger.warning(
                    "Failed to set Wine override %s=%s; continuing launch",
                    key,
                    value,
                )
        for key in self.registry_unset:
            existing_value = self._query_override_value(key)
            if existing_value is None:
                continue
            command = [
                *registry_runner,
                "reg",
                "delete",
                r"HKCU\Software\Wine\DllOverrides",
                "/v",
                key,
                "/f",
            ]
            try:
                subprocess.run(command, check=False, env=self.env, timeout=15)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Timed out unsetting Wine override %s; continuing launch",
                    key,
                )

    def __post_init__(self) -> None:
        if not self.command_line:
            msg = "command_line must be a non-empty list."
            raise AssertionError(msg)

    def start(self) -> RunningProcess:
        if self.utility_binary_name is not None:
            utility_path = Path(self.utility_binary_name)
            is_path_like = (
                utility_path.is_absolute() or len(utility_path.parts) > 1
            )
            if is_path_like:
                if not utility_path.exists():
                    msg = f"Utility path does not exist: {utility_path}"
                    raise ValueError(msg)
            elif shutil.which(self.utility_binary_name) is None:
                msg = (
                    "Couldn't find utility in PATH:"
                    f" {self.utility_binary_name}"
                )
                raise ValueError(msg)

        command_line = list(self.command_line)
        logger.info(f"Starting subprocess: {command_line}")
        self._apply_registry_overrides()
        return subprocess.Popen(
            command_line,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            cwd=self.cwd,
            env=self.env,
        )


_PROTON_PATTERN = re.compile(r"(?i)proton")


def _normalize_tool_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


@dataclass
class CompatibilityTool:
    internal_name: str
    manifest_vdf: Path
    install_path: Path
    display_name: str
    binary_path: Path
    binary_argument_template: list[str]

    def is_proton(self) -> bool:
        # By name anywhere in it (Proton-ROTK, GE-Proton10-4, SVT-GE-Proton-11,
        # not only names that start with it), or by being run through a
        # `proton` script, which is what every Proton build ships.
        return (
            _PROTON_PATTERN.search(self.internal_name) is not None
            or self.binary_path.name == "proton"
        )

    @classmethod
    def from_toolmanifest_vdf(
        cls,
        manifest_vdf: Path,
        internal_name: str = "",
        display_name: str = "",
    ) -> CompatibilityTool:
        install_path = manifest_vdf.parent
        if not internal_name:
            internal_name = install_path.name.lower().replace(" ", "_")
        if not display_name:
            display_name = install_path.name
        manifest = Section.from_text_file(manifest_vdf).section("manifest")
        manifest_version = manifest.string("version")
        if manifest_version != "2":
            msg = f"Only supports manifest v2, but found v{manifest_version}"
            raise NotImplementedError(msg)
        command_line_template = shlex.split(manifest.string("commandline"))
        if not command_line_template:
            msg = "Couldn't retrieve command line!"
            raise ValueError(msg)
        binary = command_line_template[0]
        if binary.startswith("/"):
            binary = str(install_path / binary[1:])
        else:
            which_path = shutil.which(binary)
            if not which_path:
                msg = f"Couldn't find binary in PATH: {binary}"
                raise ValueError(msg)
            binary = which_path
        return CompatibilityTool(
            internal_name=internal_name,
            install_path=install_path,
            manifest_vdf=manifest_vdf,
            display_name=display_name,
            binary_path=Path(binary),
            binary_argument_template=command_line_template[1:],
        )

    @classmethod
    def from_compatibilitytool_vdf(
        cls, tool_vdf: Path
    ) -> list[CompatibilityTool]:
        tools: list[CompatibilityTool] = []
        compat_tools = Section.from_text_file(tool_vdf).section(
            "compatibilitytools", "compat_tools"
        )
        vdf_dir = tool_vdf.parent
        for internal_name, tool_section in compat_tools.subsections():
            install_path = vdf_dir / tool_section.string("install_path")
            manifest_vdf = install_path / "toolmanifest.vdf"
            tools.append(
                cls.from_toolmanifest_vdf(
                    manifest_vdf,
                    internal_name=internal_name,
                    display_name=tool_section.find_string("display_name")
                    or "",
                )
            )
        return tools

    @property
    def manifest_path(self) -> Path:
        return self.install_path / "toolmanifest.vdf"

    def command_line(self) -> list[str]:
        """What runs a program with this tool, up to the program."""
        return [str(self.binary_path)] + [
            tool_arg if tool_arg != "%verb%" else "run"
            for tool_arg in self.binary_argument_template
        ]

    @property
    def wine_binary(self) -> Path | None:
        """The `wine` that runs a process in a prefix without Proton's script
        (None for a tool that has none)."""
        return _wine_beside(self.binary_path)

    def launch_command(
        self, command_line: list[str]
    ) -> tuple[list[str], list[str]]:
        """What to run, and what to run `reg` with, to start `command_line` in
        a prefix with this tool: Proton's bare `wine`, or the tool's own
        command for a tool that has none."""
        proton_wine = self.wine_binary
        if proton_wine is not None:
            prefix_runner = [str(proton_wine)]
            return prefix_runner, [str(proton_wine), *command_line]
        prefix_runner = self.command_line()
        return prefix_runner, prefix_runner + command_line

    def add_environment(self, env: dict[str, str], prefix: WinePrefix) -> None:
        """The variables a process needs to run in `prefix` with this tool."""
        env["PROTON_DIR"] = str(self.binary_path.parent)
        existing = env.get("WINEDLLOVERRIDES", "")
        env["WINEDLLOVERRIDES"] = ";".join(
            part for part in (prefix.dll_overrides(), existing) if part
        )


@dataclass(frozen=True)
class CompatSession:
    """What Steam tells a program that wraps a game's launch command (the
    launch option `program %command%`): the prefix of the shortcut being
    launched, and the compatibility tool it runs with.

    Steam sets these in the environment of the wrapper, which runs natively,
    before the command that starts Proton in Steam's runtime container.
    """

    compat_data_path: Path
    tool_directory: Path

    @property
    def prefix(self) -> Path:
        return self.compat_data_path / "pfx"

    @property
    def wine_prefix(self) -> WinePrefix:
        return WinePrefix(self.prefix)

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> CompatSession | None:
        data_path = env.get("STEAM_COMPAT_DATA_PATH")
        # The tool, then the runtime it runs in, separated like a PATH.
        tool_paths = env.get("STEAM_COMPAT_TOOL_PATHS", "")
        tool_directory = tool_paths.split(":")[0]
        if not data_path or not tool_directory:
            return None
        return cls(Path(data_path), Path(tool_directory))


@dataclass
class SteamLocation:
    root: Path
    system_config_dirs: list[Path]

    @classmethod
    def from_detection(cls, host: Host | None = None) -> SteamLocation:
        host = host or Host.current()
        return SteamLocation(
            root=host.steam_root,
            system_config_dirs=host.system_steam_config_dirs,
        )

    @property
    def config_dirs(self) -> list[Path]:
        return [self.root, *self.system_config_dirs]

    @property
    def steamapps(self) -> Path:
        return self.root / "steamapps"

    @property
    def compatibility_tools_dir(self) -> Path:
        """Where tools installed by the user go; Steam lists them after a
        restart."""
        return self.root / "compatibilitytools.d"

    @property
    def config_vdf(self) -> Path:
        return self.root / "config" / "config.vdf"

    @property
    def library_folders_vdf(self) -> Path:
        return self.steamapps / "libraryfolders.vdf"

    def account_steam_id(self) -> int:
        """The 64 bit Steam id of the account in use: the one that last signed
        in (or the only one there is)."""
        try:
            users = Section.from_text_file(
                self.root / "config" / "loginusers.vdf"
            ).section("users")
        except (OSError, KeyValuesError) as error:
            msg = f"Could not read Steam's accounts: {error}"
            raise SteamError(msg) from error
        recent = [
            steam_id
            for steam_id, user in users.subsections()
            if any(
                key.lower() == "mostrecent" and value == "1"
                for key, value in user.strings()
            )
        ]
        candidates = recent or [
            steam_id for steam_id, _ in users.subsections()
        ]
        if len(candidates) != 1:
            msg = (
                "Could not tell which Steam account is in use; sign in to the"
                " one you want in Steam, then try again."
            )
            raise SteamError(msg)
        return int(candidates[0])

    def account_directory(self) -> Path:
        """`userdata/<account>` of the Steam account in use."""
        account = self.account_steam_id() - _STEAM_ID64_BASE
        return self.root / "userdata" / str(account)

    @property
    def library_paths(self) -> list[Path]:
        paths = [self.root]
        vdf_path = self.library_folders_vdf
        if not vdf_path.exists():
            return paths
        try:
            libraryfolders = Section.from_text_file(vdf_path).section(
                "libraryfolders"
            )
        except (KeyValuesError, OSError):
            logger.warning(
                f"Exception when loading {vdf_path}:", exc_info=True
            )
            return paths
        for _, folder in libraryfolders.subsections():
            folder_path = folder.find_string("path")
            if folder_path is None:
                continue
            path = Path(folder_path)
            if path not in paths:
                paths.append(path)
        return paths


class SteamClient:
    """The running Steam client of one installation: whether it is running, and
    closing, starting and having it start a game."""

    def __init__(self, location: SteamLocation, host: Host) -> None:
        self.location = location
        self.host = host

    def _command(self) -> list[str]:
        return self.host.steam_client_command(self.location.root)

    def is_running(self) -> bool:
        """Whether this user has a Steam client running."""
        return self.host.processes.is_running(self.host.steam_process_name)

    def shut_down(self) -> None:
        """Asks Steam to exit and waits until it has."""
        logger.info("Closing Steam...")
        try:
            subprocess.run(
                [*self._command(), "-shutdown"], check=False, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            msg = f"Could not tell Steam to close: {error}"
            raise SteamError(msg) from error
        deadline = time.monotonic() + _SHUTDOWN_WAIT_SECONDS
        while self.is_running():
            if time.monotonic() > deadline:
                msg = "Steam did not close; close it yourself and try again."
                raise SteamError(msg)
            time.sleep(_SHUTDOWN_POLL_SECONDS)

    def close_for_change(
        self, ask: Callable[[str], str], *, assume_yes: bool, why: str
    ) -> bool:
        """Gets Steam out of the way of a change to its files, if it is running
        (Steam saves its settings when it exits, which would undo the change).
        It is closed after asking, unless `assume_yes`. Returns whether it was
        closed."""
        if not self.is_running():
            return False
        if not assume_yes:
            try:
                answer = ask(
                    f"Steam has to be closed to {why} (it saves its settings"
                    " when it exits, which would undo the change). Close"
                    " Steam now? [y/N] "
                )
            except EOFError:
                answer = ""
            if answer.strip().lower() not in {"y", "yes"}:
                msg = f"Close Steam, then run this again to {why}."
                raise SteamError(msg)
        self.shut_down()
        return True

    def start(self) -> None:
        """Starts Steam, which runs on its own, not as a child of this
        program."""
        try:
            self.host.spawn_detached(self._command())
        except OSError as error:
            msg = f"Could not start Steam: {error}"
            raise SteamError(msg) from error

    def launch_game(self, app_id: int) -> None:
        """Has Steam start the non-Steam game, starting Steam first if it
        isn't running (the game then starts once it is up). Steam runs on its
        own, not as a child of this program."""
        game_id = (app_id << 32) | _NON_STEAM_GAME_FLAG
        try:
            self.host.spawn_detached(
                [*self._command(), f"steam://rungameid/{game_id}"]
            )
        except OSError as error:
            msg = f"Could not start the game through Steam: {error}"
            raise SteamError(msg) from error


@dataclass
class Steam:
    location: SteamLocation
    compatibility_tools: list[CompatibilityTool]

    @staticmethod
    def running_proton_game_id(host: Host | None = None) -> str | None:
        """The Steam app id of the game running in Proton, from the
        environment of its wineserver."""
        return (host or Host.current()).processes.environment_variable_of(
            "wineserver", "SteamAppId"
        )

    @classmethod
    def from_location(cls, location: SteamLocation) -> Steam:
        compatibility_tools = [
            CompatibilityTool.from_toolmanifest_vdf(manifest_vdf)
            for manifest_vdf in (location.steamapps / "common").glob(
                "*/toolmanifest.vdf"
            )
        ]

        for config_dir in location.config_dirs:
            for vdf_path in config_dir.glob(
                "compatibilitytools.d/*/compatibilitytool.vdf"
            ):
                try:
                    tools = CompatibilityTool.from_compatibilitytool_vdf(
                        vdf_path
                    )
                except (KeyError, ValueError, OSError):
                    logger.warning(
                        f"Exception when loading {vdf_path}:", exc_info=True
                    )
                    continue
                compatibility_tools.extend(tools)
        return Steam(
            location=location, compatibility_tools=compatibility_tools
        )

    @classmethod
    def from_detection(cls, host: Host | None = None) -> Steam:
        return cls.from_location(SteamLocation.from_detection(host))

    @staticmethod
    def require(steam: Steam | None) -> Steam:
        """`steam`, which every utility that runs a game's process in a prefix
        is given on Linux (where Steam is), as an error if it was not."""
        if not steam:
            msg = "steam context must be provided on linux!"
            raise AssertionError(msg)
        return steam

    def prefixes(self) -> list[WinePrefix]:
        """The prefix of every game in Steam's libraries (Steam's own, and the
        non-Steam games Steam makes one for), by the id it was made for."""
        found: list[WinePrefix] = []
        for library in self.location.library_paths:
            try:
                found.extend(
                    WinePrefix(path)
                    for path in sorted(
                        (library / "steamapps" / "compatdata").glob("*/pfx")
                    )
                )
            except OSError:
                continue
        return found

    def game_compatibility_tool(self, game_id: str) -> str:
        steam_section = Section.from_text_file(
            self.location.config_vdf
        ).section("InstallConfigStore", "Software", "Valve", "Steam")
        tool_mapping = steam_section.find_section("CompatToolMapping")
        if tool_mapping is None:
            return ""
        # The game's own choice, else the default one (the mapping for "0").
        name = tool_mapping.find_string(game_id, "name")
        if name is None:
            name = tool_mapping.find_string("0", "name")
        return name or ""

    def game_library_path(self, *, game_id: str) -> Path:
        for library in self.location.library_paths:
            manifest = library / "steamapps" / f"appmanifest_{game_id}.acf"
            if manifest.exists():
                return library
        return self.location.root

    def game_compatdata_path(self, *, game_id: str) -> Path:
        return (
            self.game_library_path(game_id=game_id)
            / "steamapps"
            / "compatdata"
            / game_id
        )

    def game_wine_prefix(self, *, game_id: str) -> Path:
        return self.game_compatdata_path(game_id=game_id) / "pfx"

    def game_prefix(self, *, game_id: str) -> WinePrefix:
        return WinePrefix(self.game_wine_prefix(game_id=game_id))

    def tool_for_session(
        self, session: CompatSession
    ) -> CompatibilityTool | None:
        """The installed tool Steam says the launch runs with."""
        wanted = session.tool_directory.resolve()
        for tool in self.compatibility_tools:
            if tool.install_path.resolve() == wanted:
                return tool
        return None

    def matching_compatibility_tools(
        self, tool_name: str
    ) -> list[CompatibilityTool]:
        """The installed tools a name from Steam's configuration refers to:
        an exact match, else one that matches ignoring case and punctuation,
        else one whose name starts with it."""
        matched_tools = [
            tool
            for tool in self.compatibility_tools
            if tool.internal_name == tool_name
        ]
        if not matched_tools:
            normalized_tool_name = _normalize_tool_name(tool_name)
            matched_tools = [
                tool
                for tool in self.compatibility_tools
                if _normalize_tool_name(tool.internal_name)
                == normalized_tool_name
            ]
        if not matched_tools:
            matched_tools = [
                tool
                for tool in self.compatibility_tools
                if _normalize_tool_name(tool.internal_name).startswith(
                    _normalize_tool_name(tool_name)
                )
            ]
        return matched_tools

    def game_tool(self, game_id: str) -> CompatibilityTool | None:
        """The tool Steam runs the game with, if it names exactly one tool
        that is installed."""
        tool_name = self.game_compatibility_tool(game_id)
        if not tool_name:
            return None
        matched_tools = self.matching_compatibility_tools(tool_name)
        return matched_tools[0] if len(matched_tools) == 1 else None

    def process_in_prefix(
        self,
        command_line: list[str],
        *,
        game_id: str,
        cwd: Path | None = None,
        session: CompatSession | None = None,
    ) -> Process:
        """Prepares a process to run in a Proton prefix: the game's, or, given
        a `session`, the one Steam made for the launch that is being wrapped,
        with the tool it runs with (`game_id` is then only what the process
        tells Steam it is, and the library the S: drive is made to show).

        Proton's bare `wine` is used, which suits utilities that only need
        the prefix (and this adds the DLL overrides a game needs). Something
        that _is_ the game is run by Steam's own command instead (see
        `ShortcutLaunch`).
        """
        if not command_line:
            msg = "No command line provided."
            raise RuntimeError(msg)
        utility_binary_name = command_line[0]
        env = os.environ.copy()
        is_wine = False
        prefix_runner: list[str] | None = None
        if session is not None:
            prefix = session.wine_prefix
            compat_data_path = session.compat_data_path
        else:
            prefix = self.game_prefix(game_id=game_id)
            compat_data_path = self.game_compatdata_path(game_id=game_id)
        tool = self._tool_to_run_with(game_id=game_id, session=session)
        if tool is not None and tool.is_proton():
            is_wine = True
            prefix_runner, command_line = tool.launch_command(command_line)
            tool.add_environment(env, prefix)
        if is_wine:
            if session is None:  # Proton drops the S: drive of a shortcut
                prefix.map_drive("s", self.game_library_path(game_id=game_id))
            env.update(
                {
                    "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(
                        self.location.root
                    ),
                    "WINEPREFIX": str(prefix.path),
                    "STEAM_COMPAT_DATA_PATH": str(compat_data_path),
                    "SteamAppId": game_id,
                    "SteamGameId": game_id,
                    "WINEFSYNC": "1",
                    "WINESYNC": "1",
                    "WINEESYNC": "1",
                }
            )
            logger.info(f"Process will run in prefix: {command_line}")
        else:
            logger.warning(
                f"Process will run directly (no prefix): {command_line}"
            )
        return Process(
            utility_binary_name=utility_binary_name,
            command_line=command_line,
            env=env,
            cwd=cwd,
            prefix_runner=prefix_runner,
        )

    def _tool_to_run_with(
        self, *, game_id: str, session: CompatSession | None
    ) -> CompatibilityTool | None:
        """The tool to run a process in the prefix with: the one the session
        says, or else the one Steam is set to run the game with."""
        if session is not None:
            tool = self.tool_for_session(session)
            if tool is None:
                msg = f"No compatibility tool at: {session.tool_directory}"
                raise RuntimeError(msg)
            return tool
        tool_name = self.game_compatibility_tool(game_id)
        if not tool_name:
            return None
        matched_tools = self.matching_compatibility_tools(tool_name)
        if not matched_tools:
            msg = f"No compatibility tools matched: {tool_name}"
            raise RuntimeError(msg)
        if len(matched_tools) > 1:
            msg = f"Multiple compatibility tools matched: {tool_name}"
            raise RuntimeError(msg)
        return matched_tools[0]
