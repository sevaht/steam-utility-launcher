"""SVT-Proton: Valve's Proton plus the fixes ROTK Launcher and the game need
(https://github.com/sevaht/SVT-Proton), installed where Steam lists it."""

from __future__ import annotations

import logging
import platform
import re
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from . import update_check
from .github_release_updater import (
    HTTP_STATUS_OK,
    XDG_DATA_ROOT,
    GitHubRepository,
    https_download,
    https_get,
)

if TYPE_CHECKING:
    from .github_release_updater import Release
    from .steam import CompatibilityTool, Steam

logger = logging.getLogger(__name__)

REPOSITORY = GitHubRepository("SVT-Proton", organization="sevaht")
# The Proton series followed. SVT-Proton keeps the upstream version number, so
# a release is svt-<series>-<n> and holds a tool folder named for the series.
SERIES = "11.0"
TOOL_NAME = f"SVT-Proton-{SERIES}"
# Any series of it counts as SVT-Proton (a newer one may be installed by hand).
_TOOL_NAME_PREFIX = "SVT-Proton"
_TAG_PREFIX = f"svt-{SERIES}-"
_CHECKSUMS_ASSET = Path("SHA256SUMS.txt")
# Written into the tool's folder so an update can tell what is installed.
TAG_FILE_NAME = ".svt_release_tag"
_ARCHITECTURES = {"x86_64": "x86_64", "AMD64": "x86_64"}
_CHECKSUM_LINE_PART_COUNT = 2
_VERSION_FILE_FIELDS = 2
_REVISION_SUFFIX = re.compile(r"-r[0-9]+$")


def is_svt_proton(tool: CompatibilityTool) -> bool:
    return tool.internal_name.startswith(_TOOL_NAME_PREFIX)


def _architecture() -> str:
    machine = platform.machine()
    try:
        return _ARCHITECTURES[machine]
    except KeyError:
        msg = f"SVT-Proton is only built for x86_64, not for {machine}."
        raise RuntimeError(msg) from None


def latest_release() -> Release:
    for release in REPOSITORY.list_releases():
        if release.tag.startswith(_TAG_PREFIX):
            return release
    msg = f"{REPOSITORY} has no {_TAG_PREFIX}* release."
    raise RuntimeError(msg)


def archive_name(tag: str, architecture: str) -> str:
    """svt-11.0-2e -> SVT-Proton-11.0-2e-x86_64.tar.xz"""
    return f"SVT-Proton-{tag.removeprefix('svt-')}-{architecture}.tar.xz"


def _expected_checksum(checksums: bytes, name: str) -> str:
    for line in checksums.decode().splitlines():
        parts = line.split()
        if (
            len(parts) == _CHECKSUM_LINE_PART_COUNT
            and parts[1].lstrip("*") == name
        ):
            return parts[0]
    msg = f"No checksum entry found for: {name}"
    raise RuntimeError(msg)


def _recorded_tag(tool_directory: Path) -> str:
    try:
        return (tool_directory / TAG_FILE_NAME).read_text().strip()
    except OSError:
        return ""


def _tag_from_version_file(tool_directory: Path) -> str:
    """The upstream version in Proton's own `version` file ("<time> proton-
    11.0-2e"), as a release tag. A rebuild (-rN) can't be told apart by it."""
    try:
        fields = (tool_directory / "version").read_text().split()
    except OSError:
        return ""
    if len(fields) < _VERSION_FILE_FIELDS:
        return ""
    return "svt-" + fields[1].removeprefix("proton-")


def installed_tag(tool_directory: Path) -> str:
    """The release an installed SVT-Proton came from, as far as is known."""
    return _recorded_tag(tool_directory) or _tag_from_version_file(
        tool_directory
    )


def is_current(tool_directory: Path, latest_tag: str) -> bool:
    """Whether the installed SVT-Proton is the latest release."""
    recorded = _recorded_tag(tool_directory)
    if recorded:
        return recorded == latest_tag
    # Installed by hand: only the upstream version is known, so a rebuild
    # of that version doesn't count as newer.
    from_version = _tag_from_version_file(tool_directory)
    return bool(from_version) and from_version == _REVISION_SUFFIX.sub(
        "", latest_tag
    )


def _extract(archive: Path, destination: Path) -> Path:
    """Unpacks the archive and returns the tool folder in it. Only the one
    folder is accepted, and nothing in it may reach outside it."""
    with tarfile.open(archive, "r:xz") as package:
        for name in package.getnames():
            if name != TOOL_NAME and not name.startswith(f"{TOOL_NAME}/"):
                msg = f"Unexpected file in the SVT-Proton archive: {name}"
                raise RuntimeError(msg)
        package.extractall(destination, filter="data")
    tool = destination / TOOL_NAME
    if not (tool / "compatibilitytool.vdf").is_file():
        msg = f"The SVT-Proton archive has no {TOOL_NAME} tool in it."
        raise RuntimeError(msg)
    return tool


def install(steam: Steam, *, force: bool = False) -> bool:
    """Installs the latest SVT-Proton, or updates to it, in Steam's folder for
    tools you add yourself. Returns False if it was already up to date."""
    architecture = _architecture()
    release = latest_release()
    tools_directory = steam.location.compatibility_tools_dir
    target = tools_directory / TOOL_NAME
    if not force and target.is_dir() and is_current(target, release.tag):
        print(f"{TOOL_NAME} is up to date ({installed_tag(target)}).")
        return False
    name = archive_name(release.tag, architecture)
    if Path(name) not in release.asset_urls:
        msg = f"Release {release.tag} has no {name}."
        raise RuntimeError(msg)
    if _CHECKSUMS_ASSET not in release.asset_urls:
        msg = f"Release {release.tag} has no {_CHECKSUMS_ASSET}."
        raise RuntimeError(msg)
    status, checksums = https_get(release.asset_urls[_CHECKSUMS_ASSET])
    if status != HTTP_STATUS_OK:
        msg = f"Could not download {_CHECKSUMS_ASSET} (status {status})."
        raise RuntimeError(msg)
    expected = _expected_checksum(checksums, name)

    tools_directory.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {name} ({release.tag})...")
    # Beside the destination, so the final step is a rename on one filesystem.
    with TemporaryDirectory(
        dir=tools_directory, prefix=".svt-proton-"
    ) as staging_name:
        staging = Path(staging_name)
        archive = staging / name
        actual = https_download(release.asset_urls[Path(name)], archive)
        if actual != expected:
            msg = (
                f"Checksum mismatch for {name}:"
                f" expected {expected}, got {actual}"
            )
            raise RuntimeError(msg)
        print("Unpacking...")
        new_tool = _extract(archive, staging / "unpacked")
        (new_tool / TAG_FILE_NAME).write_text(release.tag)
        previous = staging / "previous"
        if target.exists():
            target.rename(previous)
        try:
            new_tool.rename(target)
        except OSError:
            if previous.exists():
                previous.rename(target)
            raise
    print(
        f"Installed {TOOL_NAME} ({release.tag}) in {tools_directory}.\n"
        "Restart Steam so it lists it, then choose it for Z1 Battle Royale"
        " under Properties > Compatibility."
    )
    return True


def ensure_game_uses(
    steam: Steam,
    game_id: str,
    *,
    game_name: str,
    override_flag: str,
    allow_any: bool,
) -> bool:
    """Whether to go on: the game's Proton is SVT-Proton, or `allow_any` says
    to use whatever it is. Otherwise says why not (and how to fix it)."""
    tool = steam.game_tool(game_id)
    if tool is not None and is_svt_proton(tool):
        warn_if_update_available(tool)
        return True
    issue = _problem(
        steam, game_id, game_name=game_name, override_flag=override_flag
    )
    if not allow_any:
        logger.error(issue)
        return False
    logger.warning(
        "%s is not using SVT-Proton (%s was passed); the game may hang while"
        " starting.",
        game_name,
        override_flag,
    )
    return True


def _problem(
    steam: Steam, game_id: str, *, game_name: str, override_flag: str
) -> str:
    chosen = steam.game_compatibility_tool(game_id)
    current = chosen or "Steam's default"
    # Steam names a tool that isn't there (any more): the override can't help.
    missing = bool(chosen) and steam.game_tool(game_id) is None
    selection = f"{TOOL_NAME} for {game_name} under Properties > Compatibility"
    install = (
        "Install it with `steam-utility-launcher svt-proton`, restart Steam,"
        f" then choose {selection}."
    )
    svt_installed = any(
        is_svt_proton(known) for known in steam.compatibility_tools
    )
    if missing:
        detail = (
            f"{game_name} is set to {chosen} in Steam, but that isn't"
            f" installed. {f'Choose {selection}.' if svt_installed else install}"
        )
    elif svt_installed:
        detail = (
            f"{game_name} is set to {current}, not SVT-Proton."
            f" Choose {selection}."
        )
    else:
        detail = f"SVT-Proton isn't installed. {install}"
    override = (
        ""
        if missing
        else (
            f" To use {current} anyway (the game may hang while starting),"
            f" pass {override_flag}."
        )
    )
    return (
        "ROTK Launcher needs SVT-Proton, a Proton build with fixes the game"
        f" needs to start reliably. {detail}{override}"
    )


def warn_if_update_available(tool: CompatibilityTool) -> None:
    """Says so, at most once a day, if a newer SVT-Proton has been released.
    Never stops anything: a failed check is only logged."""
    directory = XDG_DATA_ROOT / "SVT-Proton"
    marker = directory / update_check.MARKER_FILE_NAME
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if update_check.checked_recently(marker):
            return
        release = latest_release()
        update_check.record_checked_now(marker)
    except (OSError, RuntimeError, ValueError, KeyError):
        logger.debug("Could not check for a newer SVT-Proton", exc_info=True)
        return
    if not is_current(tool.install_path, release.tag):
        logger.warning(
            'An SVT-Proton update is available ("%s" -> "%s"). Run'
            " `steam-utility-launcher svt-proton` to install it, then restart"
            " Steam.",
            installed_tag(tool.install_path) or "an untracked version",
            release.tag,
        )
