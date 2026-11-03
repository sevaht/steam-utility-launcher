"""Proton builds published as GitHub releases in the format GE-Proton uses
(<tool>.tar.gz with one folder named for the tool in it, and a
<tool>.sha512sum), installed where Steam lists them: upstream GE-Proton and
the SVT fork of it."""

from __future__ import annotations

import logging
import platform
import re
import shutil
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
# Proton's own per-tool defaults for environment variables ("for all games run
# in this Proton version"). It is the user's, so an update has to keep it.
USER_SETTINGS_FILE_NAME = "user_settings.py"
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
    # Every user_settings.py this source has ever generated, oldest first; the
    # last is the one a new install gets when it has none to take over (see
    # _user_settings_donor). Empty for a source that makes none.
    user_settings_versions: tuple[str, ...] = ()

    @property
    def default_user_settings(self) -> str:
        return (
            self.user_settings_versions[-1]
            if self.user_settings_versions
            else ""
        )

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


# Run by Proton each time it starts a game, so what it decides follows the
# session the game is started from, not the one this was written in. Proton
# itself uses the Wayland driver only with a Wayland display, and a variable
# already in the environment wins over these.
_SVT_USER_SETTINGS_V1 = """\
# Defaults for every game run with this Proton. A value already in the
# environment (Steam's, or a game's launch options) wins over these.
#
# Made once by `steam-utility-launcher proton svt-ge`, and yours after that:
# it is kept across updates (and given to the next series), so edit it freely.
import os

user_settings = {}

# Checked each time Proton starts: only a Wayland session has this display.
if os.environ.get("WAYLAND_DISPLAY"):
    user_settings["PROTON_ENABLE_WAYLAND"] = "1"
"""

# EVERY version of the file the source has generated, oldest first, the one
# new installs get last. An installed file that is exactly an older one was
# never edited, so it is brought up to the last (_refresh_user_settings). So
# never change a version in place (a test pins their hashes): add the new one
# at the end and pin it there.
_SVT_USER_SETTINGS_VERSIONS = (_SVT_USER_SETTINGS_V1,)

SVT_GE_PROTON = ProtonSource(
    key="svt-ge",
    title="SVT-GE-Proton",
    repository=GitHubRepository("SVT-proton-ge-custom", organization="sevaht"),
    tag_pattern=_SVT_TAG,
    tool_name_for_tag=_svt_tool_name,
    tool_pattern=re.compile(r"SVT-GE-Proton-[0-9]+"),
    tag_for_version=lambda version: f"svt-{version}",
    user_settings_versions=_SVT_USER_SETTINGS_VERSIONS,
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


def _has_user_settings(tool: Path) -> bool:
    path = tool / USER_SETTINGS_FILE_NAME
    return path.is_symlink() or path.is_file()


def _version_key(name: str) -> list[int]:
    """GE-Proton10-3 comes after GE-Proton9-20 (numbers, not text)."""
    return [int(part) for part in re.findall(r"[0-9]+", name)]


def _user_settings_donor(
    source: ProtonSource, tools_directory: Path, target: Path
) -> Path | None:
    """The installed tool whose user_settings.py a new install at `target`
    takes over: the one it replaces, else the newest other tool of the same
    source (the previous GE release, or the previous series of ours)."""
    if target.is_dir() and _has_user_settings(target):
        return target
    others = sorted(
        (
            path
            for path in tools_directory.iterdir()
            if path != target
            and path.is_dir()
            and source.tool_pattern.fullmatch(path.name)
        ),
        key=lambda path: _version_key(path.name),
        reverse=True,
    )
    return next((path for path in others if _has_user_settings(path)), None)


def _keep_user_settings(donor: Path, new: Path) -> None:
    """Gives the new tool the user_settings.py of `donor` (as a link if it is
    one: Proton follows links)."""
    destination = new / USER_SETTINGS_FILE_NAME
    destination.unlink(missing_ok=True)
    shutil.copy2(
        donor / USER_SETTINGS_FILE_NAME, destination, follow_symlinks=False
    )
    logger.info(
        "Kept %s of %s in %s.", USER_SETTINGS_FILE_NAME, donor.name, new.name
    )


def _refresh_user_settings(source: ProtonSource, tool: Path) -> bool:
    """Brings a user_settings.py that is exactly one of the source's older
    generated versions up to the current one, and says whether it did. A file
    with any change in it (or a link, which is the user's way of managing it)
    is never touched."""
    path = tool / USER_SETTINGS_FILE_NAME
    current = source.default_user_settings
    if not current or path.is_symlink() or not path.is_file():
        return False
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return False
    if text == current or text not in source.user_settings_versions:
        return False
    path.write_text(current)
    logger.info("Updated %s of %s.", USER_SETTINGS_FILE_NAME, tool.name)
    return True


def _generate_user_settings(source: ProtonSource, new: Path) -> None:
    """The source's default user_settings.py, if the tool has none."""
    if source.default_user_settings and not _has_user_settings(new):
        (new / USER_SETTINGS_FILE_NAME).write_text(
            source.default_user_settings
        )
        logger.info("Made %s in %s.", USER_SETTINGS_FILE_NAME, new.name)


def _settings_for_new_tool(
    source: ProtonSource, tools_directory: Path, target: Path, new: Path
) -> None:
    """The user_settings.py of a tool being installed at `target`: the user's
    (see _user_settings_donor), else the default, and brought up to date if it
    is an untouched older default."""
    donor = _user_settings_donor(source, tools_directory, target)
    if donor is not None:
        _keep_user_settings(donor, new)
    _generate_user_settings(source, new)
    _refresh_user_settings(source, new)


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
        if _refresh_user_settings(source, target):
            print(f"Updated its {USER_SETTINGS_FILE_NAME} to the new default.")
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
        _settings_for_new_tool(source, tools_directory, target, new_tool)
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
