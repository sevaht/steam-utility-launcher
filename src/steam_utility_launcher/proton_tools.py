"""Proton builds published as GitHub releases in the format GE-Proton uses
(<tool>.tar.gz with one folder named for the tool in it, and a
<tool>.sha512sum), installed where Steam lists them: upstream GE-Proton and
the SVT fork of it."""

from __future__ import annotations

import logging
import platform
import re
import tarfile
from dataclasses import dataclass
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
    from collections.abc import Callable

    from .github_release_updater import Release
    from .steam import CompatibilityTool, Steam

logger = logging.getLogger(__name__)

# Written into the tool's folder so an update can tell what is installed.
TAG_FILE_NAME = ".release_tag"
_ARCHITECTURES = frozenset({"x86_64", "AMD64"})
_CHECKSUM_LINE_PART_COUNT = 2
_REVISION_SUFFIX = re.compile(r"-r[0-9]+$")
_SVT_TAG = re.compile(r"svt-GE-Proton(?P<series>[0-9]+)-[0-9]+(?:-r[0-9]+)?")


@dataclass(frozen=True)
class ProtonSource:
    key: str
    title: str
    repository: GitHubRepository
    # The releases that are builds of this (a repository may hold others).
    tag_pattern: re.Pattern[str]
    # The folder the tool unpacks to, which is also the name of its archive
    # and of the archive's checksums (<folder>.tar.gz, <folder>.sha512sum).
    tool_name_for_tag: Callable[[str], str]
    # The names of the tools that come from it.
    tool_pattern: re.Pattern[str]
    # A release's tag from the version file in an installed tool ("<time>
    # <version>"), for a tool that was unpacked by hand.
    tag_for_version: Callable[[str], str]

    def owns(self, tool: CompatibilityTool) -> bool:
        return self.tool_pattern.fullmatch(tool.internal_name) is not None


GE_PROTON = ProtonSource(
    key="ge",
    title="GE-Proton",
    repository=GitHubRepository(
        "proton-ge-custom", organization="GloriousEggroll"
    ),
    tag_pattern=re.compile(r"GE-Proton[0-9]+-[0-9]+"),
    # Each release is a tool of its own, so games keep the one they chose.
    # GE publishes an archive per architecture, named for it, with a folder
    # of the same name in it.
    tool_name_for_tag=lambda tag: f"{tag}-x86_64",
    tool_pattern=re.compile(r"GE-Proton[0-9]+-[0-9]+(?:-x86_64)?"),
    tag_for_version=lambda version: version,
)


def _svt_tool_name(tag: str) -> str:
    """svt-GE-Proton11-11[-r2] -> SVT-GE-Proton-11. It follows the series, so
    the tool a game is set to keeps working from one release to the next."""
    match = _SVT_TAG.fullmatch(tag)
    if match is None:
        msg = f"Not a release of SVT-GE-Proton: {tag}"
        raise RuntimeError(msg)
    return f"SVT-GE-Proton-{match['series']}"


SVT_GE_PROTON = ProtonSource(
    key="svt-ge",
    title="SVT-GE-Proton",
    repository=GitHubRepository("SVT-proton-ge-custom", organization="sevaht"),
    tag_pattern=_SVT_TAG,
    tool_name_for_tag=_svt_tool_name,
    tool_pattern=re.compile(r"SVT-GE-Proton-[0-9]+"),
    tag_for_version=lambda version: f"svt-{version}",
)

SOURCES = (GE_PROTON, SVT_GE_PROTON)


def source_for_tool(tool: CompatibilityTool) -> ProtonSource | None:
    return next((source for source in SOURCES if source.owns(tool)), None)


def source_for_key(key: str) -> ProtonSource:
    for source in SOURCES:
        if source.key == key:
            return source
    msg = f"Unknown Proton: {key}"
    raise ValueError(msg)


def _check_architecture() -> None:
    machine = platform.machine()
    if machine not in _ARCHITECTURES:
        msg = f"These Protons are only built for x86_64, not for {machine}."
        raise RuntimeError(msg)


def find_release(source: ProtonSource, tag: str = "") -> Release:
    """The newest release of it (or the one with this tag)."""
    if tag:
        if source.tag_pattern.fullmatch(tag) is None:
            msg = f"{tag} is not a release of {source.title}."
            raise RuntimeError(msg)
        release = source.repository.get_release(tag)
        if release is None:
            msg = f"{source.repository} has no release {tag}."
            raise RuntimeError(msg)
        return release
    for release in source.repository.list_releases():
        if source.tag_pattern.fullmatch(release.tag):
            return release
    msg = f"{source.repository} has no {source.title} release."
    raise RuntimeError(msg)


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


def _tag_from_version_file(source: ProtonSource, tool_directory: Path) -> str:
    try:
        fields = (tool_directory / "version").read_text().split()
    except OSError:
        return ""
    return source.tag_for_version(fields[-1]) if fields else ""


def installed_tag(source: ProtonSource, tool_directory: Path) -> str:
    """The release an installed tool came from, as far as is known."""
    return _recorded_tag(tool_directory) or _tag_from_version_file(
        source, tool_directory
    )


def is_current(
    source: ProtonSource, tool_directory: Path, latest_tag: str
) -> bool:
    """Whether the installed tool is the latest release."""
    recorded = _recorded_tag(tool_directory)
    if recorded:
        return recorded == latest_tag
    # Unpacked by hand: only the version is known, so a rebuild of that
    # version doesn't count as newer.
    from_version = _tag_from_version_file(source, tool_directory)
    return bool(from_version) and from_version == _REVISION_SUFFIX.sub(
        "", latest_tag
    )


def _extract(archive: Path, destination: Path, tool_name: str) -> Path:
    """Unpacks the archive and returns the tool folder in it. Only the one
    folder is accepted, and nothing in it may reach outside it."""
    with tarfile.open(archive, "r:gz") as package:
        for name in package.getnames():
            if name != tool_name and not name.startswith(f"{tool_name}/"):
                msg = f"Unexpected file in the {tool_name} archive: {name}"
                raise RuntimeError(msg)
        package.extractall(destination, filter="data")
    tool = destination / tool_name
    if not (tool / "compatibilitytool.vdf").is_file():
        msg = f"The {tool_name} archive has no tool in it."
        raise RuntimeError(msg)
    return tool


def install(
    steam: Steam, source: ProtonSource, *, tag: str = "", force: bool = False
) -> bool:
    """Installs the newest release (or the one with this tag), or updates to
    it, in Steam's folder for tools you add yourself. Returns False if it was
    already installed."""
    _check_architecture()
    release = find_release(source, tag)
    tool_name = source.tool_name_for_tag(release.tag)
    tools_directory = steam.location.compatibility_tools_dir
    target = tools_directory / tool_name
    if (
        not force
        and target.is_dir()
        and is_current(source, target, release.tag)
    ):
        print(f"{tool_name} is up to date ({release.tag}).")
        return False
    archive_asset = Path(f"{tool_name}.tar.gz")
    checksums_asset = Path(f"{tool_name}.sha512sum")
    for asset in (archive_asset, checksums_asset):
        if asset not in release.asset_urls:
            msg = f"Release {release.tag} has no {asset}."
            raise RuntimeError(msg)
    status, checksums = https_get(release.asset_urls[checksums_asset])
    if status != HTTP_STATUS_OK:
        msg = f"Could not download {checksums_asset} (status {status})."
        raise RuntimeError(msg)
    expected = _expected_checksum(checksums, archive_asset.name)

    tools_directory.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {archive_asset} ({release.tag})...")
    # Beside the destination, so the final step is a rename on one filesystem.
    with TemporaryDirectory(
        dir=tools_directory, prefix=f".{source.key}-"
    ) as staging_name:
        staging = Path(staging_name)
        archive = staging / archive_asset
        actual = https_download(
            release.asset_urls[archive_asset], archive, "sha512"
        )
        if actual != expected:
            msg = (
                f"Checksum mismatch for {archive_asset}:"
                f" expected {expected}, got {actual}"
            )
            raise RuntimeError(msg)
        print("Unpacking...")
        new_tool = _extract(archive, staging / "unpacked", tool_name)
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
        f"Installed {tool_name} ({release.tag}) in {tools_directory}.\n"
        "Restart Steam so it lists it, then choose it for a game under"
        " Properties > Compatibility."
    )
    return True


def installed_in(
    steam: Steam, source: ProtonSource
) -> list[CompatibilityTool]:
    return [tool for tool in steam.compatibility_tools if source.owns(tool)]


def warn_if_update_available(
    source: ProtonSource, tool: CompatibilityTool
) -> None:
    """Says so, at most once a day, if a newer release has been published.
    Never stops anything: a failed check is only logged."""
    directory = XDG_DATA_ROOT / "proton-tools" / source.key
    marker = directory / update_check.MARKER_FILE_NAME
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if update_check.checked_recently(marker):
            return
        release = find_release(source)
        update_check.record_checked_now(marker)
    except (OSError, RuntimeError, ValueError, KeyError):
        logger.debug(
            "Could not check for a newer %s", source.title, exc_info=True
        )
        return
    # Another series (GE-Proton10-4 beside 11) is a tool of its own.
    if source.tool_name_for_tag(release.tag) != tool.internal_name:
        return
    if not is_current(source, tool.install_path, release.tag):
        logger.warning(
            'A %s update is available ("%s" -> "%s"). Run'
            " `steam-utility-launcher proton %s` to install it, then"
            " restart Steam.",
            source.title,
            installed_tag(source, tool.install_path) or "an untracked version",
            release.tag,
            source.key,
        )
