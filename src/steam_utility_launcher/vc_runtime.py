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

    from steam_utility_launcher.steam import CompatSession, Steam

logger = logging.getLogger(__name__)

# Software built with a recent MSVC (17.10+) uses a std::mutex that needs no
# initialization call. Older msvcp140.dll builds (such as the 2016 one that
# Steam's redistributable installer puts in a game's prefix) dereference a null
# pointer when locking such a mutex, which crashes ROTK's anti-cheat about
# three seconds after it loads. 14.40 is the first release that handles it.
MINIMUM_VERSION = (14, 40)

# Without the real runtime the game dies a few minutes into a match: Wine's
# builtin vcruntime140.dll only has a stub for __C_specific_handler_noexcept,
# and ROTK's anti-cheat aborts with "Call from ... to unimplemented function
# vcruntime140.dll.__C_specific_handler_noexcept, aborting".
#
# A fresh Proton prefix has no Microsoft runtime at all: these DLLs are Wine's
# own builtin stand-ins, and msvcp140.dll's even carries a version resource
# claiming 14.42, so a version check alone calls them current. The real
# runtime must replace every one of them: a real msvcp140.dll next to a
# builtin vcruntime140.dll is a broken combination.
_DLLS = (
    "concrt140",
    "msvcp140",
    "msvcp140_1",
    "msvcp140_2",
    "msvcp140_atomic_wait",
    "msvcp140_codecvt_ids",
    "vccorlib140",
    "vcomp140",
    "vcruntime140",
)
_X64_ONLY_DLLS = ("vcruntime140_1",)
# The ones whose version is checked (a builtin vcruntime140.dll has none).
_VERSIONED_DLLS = frozenset({"msvcp140", "vcruntime140"})
_BUILTIN_MARKERS = (b"Wine builtin DLL", b"Wine placeholder DLL")
_BUILTIN_MARKER_SPAN = 128  # the marker sits in the DOS header, at 0x40

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

    @property
    def dlls(self) -> tuple[str, ...]:
        """The runtime DLLs this build provides, without the extension."""
        return _DLLS + (_X64_ONLY_DLLS if self.architecture == "x64" else ())


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


def is_wine_builtin(path: Path) -> bool:
    """Whether the file is Wine's own stand-in and not a Microsoft binary.

    Wine marks its builtin DLLs in the DOS header. Symlinks are followed, so
    a link to a builtin counts as one.
    """
    try:
        with path.open("rb") as stream:
            head = stream.read(_BUILTIN_MARKER_SPAN)
    except OSError:
        return False
    return any(marker in head for marker in _BUILTIN_MARKERS)


def _dll_path(prefix: Path, redist: _Redistributable, name: str) -> Path:
    return (
        prefix
        / "drive_c"
        / "windows"
        / redist.system_directory
        / f"{name}.dll"
    )


def _problems(prefix: Path, redist: _Redistributable) -> list[str]:
    """What is wrong with this build's DLLs in the prefix, one entry per file.

    Empty when every one is a real Microsoft binary (and msvcp140.dll and
    vcruntime140.dll are at least MINIMUM_VERSION).
    """
    problems = []
    for name in redist.dlls:
        path = _dll_path(prefix, redist, name)
        label = f"{redist.system_directory}\\{name}.dll"
        if not path.is_file():
            problems.append(f"{label}: missing")
        elif is_wine_builtin(path):
            problems.append(f"{label}: Wine builtin")
        elif name in _VERSIONED_DLLS:
            version = file_version(path)
            if version is None or version[:2] < MINIMUM_VERSION:
                shown = ".".join(map(str, version)) if version else "none"
                problems.append(f"{label}: version {shown}")
    return problems


def outdated_redistributables(prefix: Path) -> list[_Redistributable]:
    return [r for r in _REDISTRIBUTABLES if _problems(prefix, r)]


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


def _run_installer(  # noqa: PLR0913
    steam: Steam,
    game_id: str,
    redist: _Redistributable,
    installer: Path,
    mode: str,
    session: CompatSession | None,
) -> None:
    process = steam.process_in_prefix(
        [str(installer), mode, "/quiet", "/norestart"],
        game_id=game_id,
        session=session,
    )
    return_code = process.start().wait()
    if return_code not in _ACCEPTED_EXIT_CODES:
        logger.warning(
            "The %s installer (%s) exited with status %s.",
            redist.architecture,
            mode,
            return_code,
        )


def ensure_current(
    *,
    steam: Steam,
    prefix: Path,
    game_id: str,
    session: CompatSession | None = None,
) -> None:
    """Installs Microsoft's Visual C++ runtime into the prefix (the game's, or
    the one of `session`), unless every runtime DLL in it is already a real
    Microsoft file (and msvcp140.dll and vcruntime140.dll are at least
    MINIMUM_VERSION).

    A new Proton prefix only has Wine's builtin stand-ins, which claim to be
    14.42 and so must be told apart from the real thing by their marker.
    Runs Microsoft's own installer inside the prefix, the same thing
    `winetricks vcrun2022` does (without its manual msvcp140.dll extraction,
    which isn't needed: it replaces the builtins, as it does an older native
    file). If the plain install leaves a file wrong, which happens when the
    bundle is already registered but its files were replaced behind its back,
    a repair pass follows. Cheap when nothing is needed: a few file reads.
    """
    for mode in ("/install", "/repair"):
        stale = outdated_redistributables(prefix)
        if not stale:
            break
        for redist in stale:
            installer = _cached_installer(redist)
            logger.warning(
                "Updating the %s Visual C++ runtime in the prefix (%s): %s...",
                redist.architecture,
                mode,
                "; ".join(_problems(prefix, redist)[:3]),
            )
            _run_installer(steam, game_id, redist, installer, mode, session)
    remaining = [
        problem
        for redist in _REDISTRIBUTABLES
        for problem in _problems(prefix, redist)
    ]
    if remaining:
        msg = (
            "The Visual C++ runtime in the prefix is still not Microsoft's"
            f" after installing ({'; '.join(remaining)}). ROTK's anti-cheat"
            " needs the real msvcp140.dll, version"
            f" {'.'.join(map(str, MINIMUM_VERSION))} or newer, not Wine's"
            " builtin one. The installer needs a graphical session to run,"
            " even though it is silent."
        )
        raise RuntimeError(msg)
