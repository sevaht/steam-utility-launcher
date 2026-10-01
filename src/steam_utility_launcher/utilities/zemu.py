"""ZEmu: King of the Kill (https://zemu.uk), launched through its own launcher.

ZEmu is a community server for the 2017 Pre-Season 3 H1Z1 client. Its official
launcher (ZEmu Launcher, https://zemu.uk) has a native Linux build that
downloads the game client and starts it through Wine or Proton itself, and a
Windows build. On Linux this installs and runs the AppImage and points it at a
Proton install; on Windows it installs and runs the official installer's app,
with nothing to configure. The launcher is downloaded from ZEmu's own releases when needed, never bundled
here, and none of its code is used: its licence allows using and studying it
but not redistributing, modifying or deriving from it.

Not affiliated with ZEmu. ZEmu Launcher: https://zemu.uk
"""

from __future__ import annotations

import ctypes.util
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from steam_utility_launcher import minisign, update_check
from steam_utility_launcher.github_release_updater import (
    XDG_DATA_ROOT,
    ApplicationUpdater,
    GitHubRepository,
    Release,
    https_get,
)
from steam_utility_launcher.steam import Process

if TYPE_CHECKING:
    from steam_utility_launcher.steam import Steam

logger = logging.getLogger(__name__)

_REPOSITORY = GitHubRepository(
    "zemu-launcher-releases", organization="Splicho"
)
_LINUX_ASSET_PATTERN = re.compile(
    r"ZEmu\.Launcher_[0-9]+(\.[0-9]+)*_amd64\.AppImage"
)
_WINDOWS_ASSET_PATTERN = re.compile(
    r"ZEmu\.Launcher_[0-9]+(\.[0-9]+)*_x64-setup\.exe"
)
_SIGNATURE_SUFFIX = ".sig"
# ZEmu's updater signing key: the base64 of its minisign public key file, from
# the `plugins.updater.pubkey` entry of its tauri.conf.json (key id
# D2471E036E921AD2). If ZEmu ever changes keys, verification fails closed and
# this has to be updated from a source you trust.
_PUBLIC_KEY = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEQyNDcxRTAzNkU5MjFB"
    "RDIKUldUU0dwSnVBeDVIMGhjNVQ2QVBsdTNsNDNpNGc4MGNTL0hyeHRkSU4vZUJXZG85bmJq"
    "U29TVXYK"
)
_INSTALL_DIRECTORY_NAME = "ZEmu-Launcher"
_APPIMAGE_NAME = "ZEmu-Launcher.AppImage"
# Windows: the installer is told to put the app here, inside the install
# directory, so that it is always found in the same place.
_WINDOWS_APP_DIRECTORY_NAME = "app"
_WINDOWS_UNINSTALLER_NAME = "uninstall.exe"
# Where ZEmu Launcher keeps its settings (its Tauri app identifier).
_APP_IDENTIFIER = "uk.zemu.launcher"
_CONFIG_FILE_NAME = "launcher-config.json"
# ZEmu's client is Steam app 433850, the same one as Z1 Battle Royale, so the
# Proton Steam already uses for that game is a known-good choice.
_Z1_GAME_ID = "433850"
_STABLE_PROTON_NAME = re.compile(r"^proton_(\d+)(?:\.(\d+))?$")
# What ZEmu Launcher itself uses when it creates the Wine settings.
_DEFAULT_WINE_ENV = (
    {"key": "DXVK_ASYNC", "value": "1"},
    {"key": "WINEDEBUG", "value": "-all"},
)


class InstallError(RuntimeError):
    """ZEmu Launcher couldn't be obtained or verified."""


def _is_windows() -> bool:
    return sys.platform == "win32"


def _asset_pattern() -> re.Pattern[str]:
    return _WINDOWS_ASSET_PATTERN if _is_windows() else _LINUX_ASSET_PATTERN


def install_directory() -> Path:
    return XDG_DATA_ROOT / _INSTALL_DIRECTORY_NAME


def windows_app_directory() -> Path:
    return install_directory() / _WINDOWS_APP_DIRECTORY_NAME


def installed_executable() -> Path | None:
    """The installed launcher, or None if it isn't installed."""
    if not _is_windows():
        appimage = install_directory() / _APPIMAGE_NAME
        return appimage if appimage.is_file() else None
    directory = windows_app_directory()
    if not directory.is_dir():
        return None
    # The app's own file name isn't assumed: it is the one program there
    # besides the uninstaller.
    candidates = [
        entry
        for entry in directory.glob("*.exe")
        if entry.name.lower() != _WINDOWS_UNINSTALLER_NAME
    ]
    return candidates[0] if len(candidates) == 1 else None


def app_data_directory() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME") or str(
        Path.home() / ".local" / "share"
    )
    return Path(data_home) / _APP_IDENTIFIER


def _verify(
    content: bytes, signature_text: str, asset_name: str, public_key: str
) -> None:
    try:
        signature = minisign.parse_signature(signature_text)
        minisign.verify(
            minisign.parse_public_key(public_key), signature, content
        )
    except minisign.SignatureError as error:
        msg = f"{asset_name} failed signature verification: {error}"
        raise InstallError(msg) from error
    # The signed comment names the file; GitHub turns its spaces into dots.
    signed_name = signature.trusted_file_name
    if signed_name is None or signed_name.replace(" ", ".") != asset_name:
        msg = (
            f"{asset_name} is signed as {signed_name!r}; refusing a signature"
            " that belongs to a different file."
        )
        raise InstallError(msg)


def _get(url: str) -> bytes:
    status, body = https_get(url)
    if status != 200:  # noqa: PLR2004
        msg = f"Downloading {url} failed with HTTP status {status}"
        raise InstallError(msg)
    return body


def _download_verified(
    release: Release, asset: Path, *, public_key: str
) -> bytes:
    sig_asset = Path(asset.name + _SIGNATURE_SUFFIX)
    if sig_asset not in release.asset_urls:
        msg = f"Release {release.tag} has no signature for {asset.name}."
        raise InstallError(msg)
    logger.info("Downloading %s...", asset.name)
    content = _get(release.asset_urls[asset])
    signature_text = _get(release.asset_urls[sig_asset]).decode(
        "utf-8", errors="replace"
    )
    # Verified before anything is written, let alone made executable or run.
    _verify(content, signature_text, asset.name, public_key)
    return content


def _run_windows_installer(installer: Path, target: Path) -> int:
    # NSIS wants /D= last and unquoted even when the path has spaces, which a
    # list of arguments can't express, so the command line is built as text.
    command_line = f'"{installer}" /S /D={target}'
    return subprocess.run(command_line, check=False).returncode  # noqa: S603


def _install(release: Release, asset: Path, *, public_key: str) -> None:
    content = _download_verified(release, asset, public_key=public_key)
    directory = install_directory()
    directory.mkdir(parents=True, exist_ok=True)
    if _is_windows():
        target = windows_app_directory()
        with TemporaryDirectory() as staging_dir:
            installer = Path(staging_dir) / asset.name
            installer.write_bytes(content)
            return_code = _run_windows_installer(installer, target)
        if return_code != 0:
            msg = (
                f"The ZEmu Launcher installer exited with status {return_code}"
            )
            raise InstallError(msg)
        if installed_executable() is None:
            msg = (
                f"ZEmu Launcher's app wasn't found in {target} after"
                " installing."
            )
            raise InstallError(msg)
    else:
        target = directory / _APPIMAGE_NAME
        staging = target.with_suffix(".part")
        staging.write_bytes(content)
        staging.chmod(0o755)
        staging.replace(target)
    (directory / ApplicationUpdater.TAG_FILE_NAME).write_text(release.tag)
    logger.info('Installed ZEmu Launcher "%s".', release.tag)


def _install_if_needed(*, force: bool) -> None:
    directory = install_directory()
    directory.mkdir(parents=True, exist_ok=True)
    tag_file = directory / ApplicationUpdater.TAG_FILE_NAME
    installed_tag = tag_file.read_text().strip() if tag_file.exists() else ""
    already_installed = installed_executable() is not None
    marker = directory / update_check.MARKER_FILE_NAME
    if (
        already_installed
        and not force
        and update_check.checked_recently(marker)
    ):
        return

    release = _REPOSITORY.get_release()
    if not release:
        msg = f"No release found for {_REPOSITORY}"
        raise InstallError(msg)
    update_check.record_checked_now(marker)
    pattern = _asset_pattern()
    asset = release.single_matching_asset(pattern)
    if not asset:
        msg = (
            f"Release {release.tag} has no ZEmu Launcher download matching"
            f" {pattern.pattern!r}"
        )
        raise InstallError(msg)
    if already_installed and installed_tag != release.tag:
        logger.warning(
            'ZEmu Launcher update available ("%s" -> "%s"). ZEmu Launcher'
            " updates itself once running; pass --force to this command to"
            " install it immediately instead.",
            installed_tag or "an untracked version",
            release.tag,
        )
    if already_installed and not force:
        return
    _install(release, asset, public_key=_PUBLIC_KEY)


def _normalized(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def pick_proton(steam: Steam, explicit: Path | None = None) -> Path | None:
    """The Proton to give ZEmu Launcher: the one asked for, else the one Steam
    uses for Z1 Battle Royale (same game client, known to work here), else the
    newest stable one installed. None if there isn't any."""
    if explicit is not None:
        candidate = explicit / "proton" if explicit.is_dir() else explicit
        return candidate if candidate.is_file() else None

    tools = [tool for tool in steam.compatibility_tools if tool.is_proton()]
    wanted = steam.game_compatibility_tool(_Z1_GAME_ID)
    if wanted:
        chosen = next(
            (tool for tool in tools if tool.internal_name == wanted), None
        ) or next(
            (
                tool
                for tool in tools
                if _normalized(tool.internal_name).startswith(
                    _normalized(wanted)
                )
            ),
            None,
        )
        if chosen is not None and chosen.binary_path.is_file():
            return chosen.binary_path

    def version(tool_name: str) -> tuple[int, int] | None:
        match = _STABLE_PROTON_NAME.match(tool_name)
        return (int(match[1]), int(match[2] or 0)) if match else None

    stable = [
        (found, tool)
        for tool in tools
        if (found := version(tool.internal_name)) is not None
        and tool.binary_path.is_file()
    ]
    return (
        max(stable, key=lambda pair: pair[0])[1].binary_path
        if stable
        else None
    )


def configure_wine(config_path: Path, proton: Path) -> bool:
    """Points ZEmu Launcher at `proton`, in its own settings file.

    Only fills in what hasn't been set: a runtime you chose in ZEmu's own
    Properties screen is never replaced, and every other setting (the file also
    holds your auth key and game directory) is kept exactly as it is. The
    prefix is left unset, so ZEmu uses its own dedicated one. Returns whether
    the file changed.
    """
    try:
        data = (
            json.loads(config_path.read_text(encoding="utf-8"))
            if config_path.exists()
            else {}
        )
    except (OSError, ValueError) as error:
        logger.warning(
            "Not touching %s, which can't be read as JSON (%s).",
            config_path,
            error,
        )
        return False
    if not isinstance(data, dict):
        logger.warning("Not touching %s: it isn't a JSON object.", config_path)
        return False

    changed = False
    wine = data.get("wine")
    if not isinstance(wine, dict):
        # ZEmu's own defaults for a fresh section (a partial section would
        # otherwise load with no environment variables at all).
        wine = {"env": [dict(entry) for entry in _DEFAULT_WINE_ENV]}
        data["wine"] = wine
        changed = True
    if "enabled" not in wine:
        wine["enabled"] = True
        changed = True
    if not wine.get("runtimeId") and not wine.get("customRuntimePath"):
        wine["runtimeId"] = "custom"
        wine["customRuntimePath"] = str(proton)
        changed = True

    if changed:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        staging = config_path.with_suffix(".json.tmp")
        staging.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        staging.replace(config_path)
        logger.info("Pointed ZEmu Launcher at %s", proton)
    return changed


def _fuse2_available() -> bool:
    return ctypes.util.find_library("fuse") is not None


def launch_environment() -> dict[str, str]:
    env = os.environ.copy()
    # A blank window under NVIDIA is a known WebKitGTK problem; this avoids it.
    env.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    if not _fuse2_available():
        # AppImages mount themselves with FUSE 2; without it, unpack and run.
        env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")
    return env


def launch(
    *,
    steam: Steam | None = None,
    force: bool = False,
    configure: bool = True,
    proton: Path | None = None,
) -> int:
    if not (_is_windows() or sys.platform.startswith("linux")):
        logger.error("The zemu command only supports Linux and Windows.")
        return 1
    print(
        "Starting ZEmu Launcher (https://zemu.uk). Unofficial wrapper; not"
        " affiliated with ZEmu.",
        file=sys.stderr,
    )
    try:
        _install_if_needed(force=force)
    except InstallError as error:
        # A clear one-line message is wanted here, not a traceback.
        logger.error("%s", error)  # noqa: TRY400
        return 1

    if configure and not _is_windows():
        proton_binary = pick_proton(steam, proton) if steam else None
        if proton_binary is None:
            logger.warning(
                "No Proton install found to configure ZEmu Launcher with;"
                " choose a Wine/Proton runtime in its Properties screen."
            )
        else:
            configure_wine(
                app_data_directory() / _CONFIG_FILE_NAME, proton_binary
            )

    executable = installed_executable()
    if executable is None:
        logger.error("ZEmu Launcher isn't installed; try --force.")
        return 1
    if _is_windows():
        # Natively on Windows: no Proton to set up, and no environment to fix.
        return Process([str(executable)], cwd=executable.parent).start().wait()
    child = Process(
        [str(executable)], env=launch_environment(), cwd=install_directory()
    ).start()
    return child.wait()
