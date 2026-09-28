from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from steam_utility_launcher.github_release_updater import (
    XDG_DATA_ROOT,
    ApplicationUpdater,
    GitHubRepository,
    Release,
    https_get,
)
from steam_utility_launcher.steam import Process, Steam

logger = logging.getLogger(__name__)

# Z1 Battle Royale, formerly H1Z1; ROTK Launcher is run in its Proton prefix.
GAME_ID = "433850"
_ASSET_PATTERN = re.compile(r"ROTK-Launcher-[0-9]+(\.[0-9]+)*-x64\.exe")
_CHECKSUMS_ASSET_NAME = Path("SHA256SUMS.txt")
_CHECKSUM_LINE_PART_COUNT = 2
_LAST_CHECKED_FILE_NAME = ".last_update_check"
_UPDATE_CHECK_INTERVAL = timedelta(days=1)


def _checked_recently(marker: Path) -> bool:
    if not marker.exists():
        return False
    try:
        last_checked = datetime.fromisoformat(marker.read_text().strip())
    except ValueError:
        return False
    return datetime.now(UTC) - last_checked < _UPDATE_CHECK_INTERVAL


def _record_checked_now(marker: Path) -> None:
    marker.write_text(datetime.now(UTC).isoformat())


def _require_steam(steam: Steam | None) -> Steam:
    if not steam:
        msg = "steam context must be provided on linux!"
        raise AssertionError(msg)
    return steam


def _resolve_app_path(steam: Steam | None) -> tuple[Path | None, Path]:
    if sys.platform.startswith("linux"):
        prefix = _require_steam(steam).game_wine_prefix(game_id=GAME_ID)
        program_files = prefix / "drive_c" / "Program Files"
        return prefix, program_files / "ROTK Launcher" / "ROTK Launcher.exe"
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    return None, program_files / "ROTK Launcher" / "ROTK Launcher.exe"


def _run_in_context(
    steam: Steam | None, prefix: Path | None, command_line: list[str]
) -> Process:
    if prefix is not None:
        process = _require_steam(steam).process_in_prefix(
            command_line, game_id=GAME_ID
        )
        # Wine's powershell.exe is an unimplemented stub: it never runs the
        # script it's given and always exits 0. ROTK Launcher's NSIS
        # installer (and its own in-app auto-updater, which invokes the
        # same installer) uses a PowerShell one-liner to check whether an
        # old instance of itself is running before installing, treating
        # exit 0 as "yes, it's running" - since the stub always exits 0, it
        # would otherwise conclude the app is permanently running and get
        # stuck forever on a "cannot be closed" dialog. Disabling
        # powershell.exe for just this process makes it fail to launch
        # instead, which the installer already handles: its bundled
        # allowOnlyOneInstallerInstance.nsh template falls back to
        # tasklist/findstr/taskkill whenever PowerShell isn't available.
        # Nothing is written to the prefix.
        assert process.env is not None  # noqa: S101 - set by process_in_prefix
        existing = process.env.get("WINEDLLOVERRIDES", "")
        override = "powershell.exe=d"
        process.env["WINEDLLOVERRIDES"] = (
            f"{existing};{override}" if existing else override
        )
        return process
    return Process(command_line)


def _fetch_release_and_asset() -> tuple[Release, Path]:
    repository = GitHubRepository("rotk-launcher", organization="h1z1rotk")
    release = repository.get_release()
    if not release:
        msg = f"No release found for {repository}"
        raise RuntimeError(msg)
    asset_name = release.single_matching_asset(_ASSET_PATTERN)
    if not asset_name:
        msg = (
            f"Release {release.tag} has no installer matching"
            f" {_ASSET_PATTERN.pattern!r}"
        )
        raise RuntimeError(msg)
    return release, asset_name


def _verify_checksum(
    content: bytes, *, checksums: bytes, asset_name: str
) -> None:
    expected = ""
    for line in checksums.decode().splitlines():
        parts = line.split()
        if (
            len(parts) == _CHECKSUM_LINE_PART_COUNT
            and parts[1].lstrip("*") == asset_name
        ):
            expected = parts[0]
            break
    if not expected:
        msg = f"No checksum entry found for: {asset_name}"
        raise RuntimeError(msg)
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        msg = (
            f"Checksum mismatch for {asset_name}:"
            f" expected {expected}, got {actual}"
        )
        raise RuntimeError(msg)


def _download_installer(
    release: Release, asset_name: Path, destination: Path
) -> None:
    _, content = https_get(release.asset_urls[asset_name])
    if _CHECKSUMS_ASSET_NAME in release.asset_urls:
        _, checksums = https_get(release.asset_urls[_CHECKSUMS_ASSET_NAME])
        _verify_checksum(
            content, checksums=checksums, asset_name=asset_name.name
        )
    else:
        logger.warning(
            "Release %s has no %s; skipping checksum verification.",
            release.tag,
            _CHECKSUMS_ASSET_NAME,
        )
    destination.write_bytes(content)


def _install_if_needed(
    *, steam: Steam | None, prefix: Path | None, app_path: Path, force: bool
) -> None:
    install_directory = XDG_DATA_ROOT / "ROTK-Launcher"
    install_directory.mkdir(parents=True, exist_ok=True)
    tag_file = install_directory / ApplicationUpdater.TAG_FILE_NAME
    installed_tag = tag_file.read_text().strip() if tag_file.exists() else ""
    already_installed = app_path.exists()

    last_checked_file = install_directory / _LAST_CHECKED_FILE_NAME
    if (
        already_installed
        and not force
        and _checked_recently(last_checked_file)
    ):
        return

    release, asset_name = _fetch_release_and_asset()
    _record_checked_now(last_checked_file)
    update_available = already_installed and installed_tag != release.tag

    if update_available:
        logger.warning(
            'ROTK Launcher update available ("%s" -> "%s"). ROTK Launcher'
            " updates itself automatically once running; pass --force to"
            " this command to install it immediately instead.",
            installed_tag or "an untracked version",
            release.tag,
        )

    if already_installed and not force:
        return

    logger.info('Installing ROTK Launcher "%s"...', release.tag)
    with TemporaryDirectory() as staging_dir:
        installer_path = Path(staging_dir) / asset_name.name
        _download_installer(release, asset_name, installer_path)
        installer_process = _run_in_context(
            steam, prefix, [str(installer_path), "/S"]
        )
        return_code = installer_process.start().wait()
    if return_code != 0:
        msg = f"ROTK Launcher installer exited with status {return_code}"
        raise RuntimeError(msg)
    if not app_path.exists():
        msg = f"ROTK Launcher was not found after installing: {app_path}"
        raise RuntimeError(msg)
    tag_file.write_text(release.tag)
    logger.info('Installed ROTK Launcher "%s".', release.tag)


def launch(*, steam: Steam | None = None, force: bool = False) -> int:
    prefix, app_path = _resolve_app_path(steam)
    _install_if_needed(
        steam=steam, prefix=prefix, app_path=app_path, force=force
    )
    child = _run_in_context(steam, prefix, [str(app_path)]).start()
    return child.wait()
