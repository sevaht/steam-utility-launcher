from __future__ import annotations

import hashlib
import logging
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from steam_utility_launcher.github_release_updater import (
    XDG_DATA_ROOT,
    https_get,
)

if TYPE_CHECKING:
    from pathlib import Path

    from steam_utility_launcher.steam import Steam

logger = logging.getLogger(__name__)

# Software built with a recent MSVC (17.10+) uses a std::mutex that needs no
# initialization call. Older msvcp140.dll builds (such as the 2016 one that
# Steam's redistributable installer puts in a game's prefix) dereference a null
# pointer when locking such a mutex, which crashes ROTK's anti-cheat about
# three seconds after it loads. 14.40 is the first release that handles it.
MINIMUM_VERSION = (14, 40)

_CACHE_DIRECTORY_NAME = "VC-Redist"
_VS_FIXEDFILEINFO_SIGNATURE = b"\xbd\x04\xef\xfe"
_ACCEPTED_EXIT_CODES = frozenset({0, 3010})  # 3010: success, restart pending
_HTTP_OK = 200


@dataclass(frozen=True)
class _Redistributable:
    architecture: str
    url: str
    sha256: str
    # The directory under <prefix>/drive_c/windows this build installs into.
    system_directory: str


# Pinned, as winetricks does for its vcrun2022 verb (2025-07-14 x64,
# 2025-04-14 x86). aka.ms serves whatever Microsoft currently publishes, so
# when Microsoft updates it these hashes stop matching and the download is
# refused; bump them (and check the versions) from a build you trust.
_REDISTRIBUTABLES = (
    _Redistributable(
        "x64",
        "https://aka.ms/vs/17/release/vc_redist.x64.exe",
        "cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b",
        "system32",
    ),
    _Redistributable(
        "x86",
        "https://aka.ms/vs/17/release/vc_redist.x86.exe",
        "0c09f2611660441084ce0df425c51c11e147e6447963c3690f97e0b25c55ed64",
        "syswow64",
    ),
)


def file_version(path: Path) -> tuple[int, int, int, int] | None:
    """The FileVersion of a Windows binary, or None if it has none.

    Reads the VS_FIXEDFILEINFO block out of the version resource, so no
    Windows or Wine tooling is needed.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    index = data.find(_VS_FIXEDFILEINFO_SIGNATURE)
    if index < 0 or index + 16 > len(data):
        return None
    _, _, most, least = struct.unpack_from("<IIII", data, index)
    return (most >> 16, most & 0xFFFF, least >> 16, least & 0xFFFF)


def _msvcp140(prefix: Path, redist: _Redistributable) -> Path:
    return (
        prefix
        / "drive_c"
        / "windows"
        / redist.system_directory
        / "msvcp140.dll"
    )


def _is_outdated(prefix: Path, redist: _Redistributable) -> bool:
    version = file_version(_msvcp140(prefix, redist))
    return version is None or version[:2] < MINIMUM_VERSION


def outdated_redistributables(prefix: Path) -> list[_Redistributable]:
    return [r for r in _REDISTRIBUTABLES if _is_outdated(prefix, r)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cached_installer(redist: _Redistributable) -> Path:
    """The pinned installer from the local cache, downloading it if needed.

    Anything that doesn't match the pinned SHA-256 is never run.
    """
    directory = XDG_DATA_ROOT / _CACHE_DIRECTORY_NAME
    directory.mkdir(parents=True, exist_ok=True)
    installer = directory / f"vc_redist.{redist.architecture}.exe"
    if installer.exists() and _sha256(installer) == redist.sha256:
        return installer
    logger.info("Downloading %s...", redist.url)
    status, content = https_get(redist.url)
    if status != _HTTP_OK:
        msg = f"Downloading {redist.url} failed with HTTP status {status}"
        raise RuntimeError(msg)
    actual = hashlib.sha256(content).hexdigest()
    if actual != redist.sha256:
        msg = (
            f"{redist.url} does not match the pinned checksum (expected"
            f" {redist.sha256}, got {actual}). Microsoft may have published a"
            " newer build; the pinned hashes in vc_runtime.py need updating."
        )
        raise RuntimeError(msg)
    staging = installer.with_suffix(".part")
    staging.write_bytes(content)
    staging.replace(installer)
    return installer


def _run_installer(
    steam: Steam,
    game_id: str,
    redist: _Redistributable,
    installer: Path,
    mode: str,
) -> None:
    process = steam.process_in_prefix(
        [str(installer), mode, "/quiet", "/norestart"], game_id=game_id
    )
    return_code = process.start().wait()
    if return_code not in _ACCEPTED_EXIT_CODES:
        logger.warning(
            "The %s installer (%s) exited with status %s.",
            redist.architecture,
            mode,
            return_code,
        )


def ensure_current(*, steam: Steam, prefix: Path, game_id: str) -> None:
    """Installs Microsoft's Visual C++ runtime into the game's prefix, but only
    if its msvcp140.dll is missing or older than MINIMUM_VERSION.

    Runs Microsoft's own installer inside the prefix, the same thing
    `winetricks vcrun2022` does (without its manual msvcp140.dll extraction,
    which isn't needed when the existing file is an older native one). If the
    plain install leaves a file old, which happens when the bundle is already
    registered but its files were replaced behind its back, a repair pass
    follows. Cheap when nothing is needed: two file reads.
    """
    for mode in ("/install", "/repair"):
        stale = outdated_redistributables(prefix)
        if not stale:
            break
        for redist in stale:
            installer = _cached_installer(redist)
            logger.warning(
                "Updating the %s Visual C++ runtime in the prefix (%s, found"
                " %s)...",
                redist.architecture,
                mode,
                file_version(_msvcp140(prefix, redist)) or "none",
            )
            _run_installer(steam, game_id, redist, installer, mode)
    remaining = outdated_redistributables(prefix)
    if remaining:
        details = ", ".join(
            f"{r.system_directory}: "
            f"{file_version(_msvcp140(prefix, r)) or 'missing'}"
            for r in remaining
        )
        msg = (
            "The Visual C++ runtime in the prefix is still older than"
            f" {'.'.join(map(str, MINIMUM_VERSION))} after installing"
            f" ({details}). ROTK's anti-cheat needs a newer msvcp140.dll."
        )
        raise RuntimeError(msg)
    logger.debug("Visual C++ runtime is current.")
