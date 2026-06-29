from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import KeysView, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Union

import vdf  # type: ignore[import-untyped]

VDFTree = Mapping[str, Union[str, "VDFTree"]]

logger = logging.getLogger(__name__)


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
        if len(self.prefix_runner) == 1 and self.prefix_runner[0] == "wine":
            return ["wine"]
        proton_binary = Path(self.prefix_runner[0])
        if proton_binary.name == "proton":
            wine_binary = proton_binary.parent / "files" / "bin" / "wine"
            if wine_binary.exists():
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
            result = subprocess.run(  # noqa: S603
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
                subprocess.run(  # noqa: S603
                    command, check=True, env=self.env, timeout=15
                )
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
                subprocess.run(  # noqa: S603
                    command, check=False, env=self.env, timeout=15
                )
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Timed out unsetting Wine override %s; continuing launch",
                    key,
                )

    def __post_init__(self) -> None:
        if not self.command_line:
            msg = "command_line must be a non-empty list."
            raise AssertionError(msg)

    def start(self) -> subprocess.Popen[bytes]:
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
        return subprocess.Popen(  # noqa: S603
            command_line,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            cwd=self.cwd,
            env=self.env,
        )


class Verb(StrEnum):
    RUN = "run"
    WAIT_FOR_EXIT_AND_RUN = "waitforexitandrun"


@dataclass
class VDFNode:
    tree: VDFTree

    @classmethod
    def from_path(cls, path: Path) -> VDFNode:
        with path.open(encoding="utf-8") as handle:
            return cls(tree=vdf.load(handle))

    @classmethod
    def from_path_binary(cls, path: Path) -> VDFNode:
        with path.open("rb") as handle:
            return cls(tree=vdf.binary_load(handle))

    def keys(self) -> KeysView[str]:
        return self.tree.keys()

    def section(self, keys: Sequence[str]) -> VDFNode:
        if isinstance(keys, str):
            keys = [keys]
        tree: VDFTree = self.tree
        for key in keys:
            inner_tree = tree[key]
            if isinstance(inner_tree, str):
                msg = f"Expected object, got str: {key}={inner_tree}"
                raise TypeError(msg)
            tree = inner_tree
        if tree is self.tree:
            return self
        return VDFNode(tree=tree)

    def __getitem__(self, keys: Sequence[str]) -> str:
        keys = [keys] if isinstance(keys, str) else list(keys)
        if not keys:
            msg = "no keys passed"
            raise ValueError(msg)
        leaf = keys.pop()
        section = self.section(keys=keys)
        value = section.tree[leaf]
        if not isinstance(value, str):
            msg = f"value expected to be str, but is {type(value)}"
            raise TypeError(msg)
        return value

    def get(self, keys: Sequence[str], default: str = "") -> str:
        try:
            return self[keys]
        except KeyError:
            return default


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
        return _PROTON_PATTERN.match(self.internal_name) is not None

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
        manifest = VDFNode.from_path(manifest_vdf).section("manifest")
        manifest_version = manifest["version"]
        if manifest_version != "2":
            msg = f"Only supports manifest v2, but found v{manifest_version}"
            raise NotImplementedError(msg)
        command_line_template = shlex.split(manifest["commandline"])
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
        compat_tools = VDFNode.from_path(tool_vdf).section(
            ["compatibilitytools", "compat_tools"]
        )
        vdf_dir = tool_vdf.parent
        for internal_name in compat_tools.tree:
            tool_section = compat_tools.section(internal_name)
            install_path = vdf_dir / tool_section["install_path"]
            manifest_vdf = install_path / "toolmanifest.vdf"
            tools.append(
                cls.from_toolmanifest_vdf(
                    manifest_vdf,
                    internal_name=internal_name,
                    display_name=tool_section.get("display_name"),
                )
            )
        return tools

    @property
    def manifest_path(self) -> Path:
        return self.install_path / "toolmanifest.vdf"

    def command_line(self, *, verb: Verb = Verb.RUN) -> list[str]:
        return [str(self.binary_path)] + [
            tool_arg if tool_arg != "%verb%" else str(verb)
            for tool_arg in self.binary_argument_template
        ]


@dataclass
class SteamLocation:
    root: Path
    system_config_dirs: list[Path]

    @classmethod
    def from_detection(cls) -> SteamLocation:
        if not sys.platform.startswith("linux"):
            msg = "currently only implemented for linux"
            raise NotImplementedError(msg)
        xdg_data_home = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
        root = xdg_data_home / "Steam"
        system_config_dirs = [
            Path(path) / "steam"
            for path in os.environ.get(
                "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
            ).split(":")
        ]
        return SteamLocation(root=root, system_config_dirs=system_config_dirs)

    @property
    def config_dirs(self) -> list[Path]:
        return [self.root, *self.system_config_dirs]

    @property
    def steamapps(self) -> Path:
        return self.root / "steamapps"

    @property
    def config_vdf(self) -> Path:
        return self.root / "config" / "config.vdf"


@dataclass
class Steam:
    location: SteamLocation
    compatibility_tools: list[CompatibilityTool]

    @classmethod
    def running_proton_game_id(cls) -> str | None:
        app_id_var_prefix = b"SteamAppId="
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                executable = (pid_dir / "exe").resolve()
                if executable.name == "wineserver":
                    for line in (
                        (pid_dir / "environ").read_bytes().split(b"\0")
                    ):
                        if line.startswith(app_id_var_prefix):
                            return line[len(app_id_var_prefix) :].decode(
                                "utf-8"
                            )
            except (FileNotFoundError, PermissionError, OSError):
                continue
        return None

    @staticmethod
    def _env_from_wineserver(
        game_id: str, keys: set[str]
    ) -> dict[str, str]:
        app_id_marker = f"SteamAppId={game_id}".encode()
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                if (pid_dir / "exe").resolve().name != "wineserver":
                    continue
                raw = (pid_dir / "environ").read_bytes()
                items = raw.split(b"\0")
                if not any(item == app_id_marker for item in items):
                    continue
                result: dict[str, str] = {}
                for item in items:
                    if b"=" not in item:
                        continue
                    k, _, v = item.partition(b"=")
                    key = k.decode("utf-8", errors="replace")
                    if key in keys:
                        result[key] = v.decode("utf-8", errors="replace")
                return result
            except (FileNotFoundError, PermissionError, OSError):
                continue
        return {}

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
    def from_detection(cls) -> Steam:
        return cls.from_location(SteamLocation.from_detection())

    def game_compatibility_tool(self, game_id: str) -> str:
        steam_section = VDFNode.from_path(self.location.config_vdf).section(
            ["InstallConfigStore", "Software", "Valve", "Steam"]
        )
        try:
            tool_mapping = steam_section.section("CompatToolMapping")
        except KeyError:
            return ""
        return tool_mapping.get(
            [game_id, "name"], tool_mapping.get(["0", "name"], "")
        )

    def game_compatdata_path(self, *, game_id: str) -> Path:
        return self.location.root / "steamapps" / "compatdata" / game_id

    def game_wine_prefix(self, *, game_id: str) -> Path:
        return self.game_compatdata_path(game_id=game_id) / "pfx"

    def _ensure_steamapps_mapping(self, *, game_id: str) -> None:
        dosdevices = self.game_wine_prefix(game_id=game_id) / "dosdevices"
        dosdevices.mkdir(parents=True, exist_ok=True)
        drive_s = dosdevices / "s:"
        target = self.location.steamapps
        if drive_s.exists() or drive_s.is_symlink():
            try:
                if drive_s.resolve() == target.resolve():
                    return
            except OSError:
                pass
            drive_s.unlink()
        drive_s.symlink_to(target)

    def process_in_prefix(
        self,
        command_line: list[str],
        *,
        game_id: str,
        cwd: Path | None = None,
        system_wine: bool = False,
    ) -> Process:
        if not command_line:
            msg = "No command line provided."
            raise RuntimeError(msg)
        utility_binary_name = command_line[0]
        env = os.environ.copy()
        is_wine = False
        prefix_runner: list[str] | None = None
        if system_wine:
            is_wine = True
            command_line = ["wine", *command_line]
            prefix_runner = ["wine"]
        else:
            tool_name = self.game_compatibility_tool(game_id)
            if tool_name:
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
                if not matched_tools:
                    msg = f"No compatibility tools matched: {tool_name}"
                    raise RuntimeError(msg)
                if len(matched_tools) > 1:
                    msg = f"Multiple compatibility tools matched: {tool_name}"
                    raise RuntimeError(msg)
                tool = matched_tools[0]
                if tool.is_proton():
                    is_wine = True
                    prefix_runner, command_line = self._proton_launch_command(
                        tool=tool, command_line=command_line
                    )
                    env.update({"PROTON_DIR": str(tool.binary_path.parent)})
        if is_wine:
            self._ensure_steamapps_mapping(game_id=game_id)
            compat_data_path = self.game_compatdata_path(game_id=game_id)
            wine_prefix = self.game_wine_prefix(game_id=game_id)
            env.update(
                {
                    "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(
                        self.location.root
                    ),
                    "WINEPREFIX": str(wine_prefix),
                    "STEAM_COMPAT_DATA_PATH": str(compat_data_path),
                    "SteamAppId": game_id,
                    "SteamGameId": game_id,
                }
            )
            env.update(
                self._env_from_wineserver(
                    game_id, {"WINEFSYNC", "WINESYNC", "WINEESYNC"}
                )
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

    def _proton_launch_command(
        self, *, tool: CompatibilityTool, command_line: list[str]
    ) -> tuple[list[str], list[str]]:
        proton_binary = Path(tool.command_line(verb=Verb.RUN)[0])
        proton_wine = proton_binary.parent / "files" / "bin" / "wine"
        if proton_wine.exists():
            prefix_runner = [str(proton_wine)]
            return prefix_runner, [str(proton_wine), *command_line]
        prefix_runner = tool.command_line(verb=Verb.RUN)
        return prefix_runner, prefix_runner + command_line
