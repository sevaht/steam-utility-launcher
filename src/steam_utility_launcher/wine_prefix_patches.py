from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Patch:
    resource: str
    # Relative to <prefix>/drive_c/windows
    target: tuple[str, ...]


_POWERSHELL = ("WindowsPowerShell", "v1.0", "powershell.exe")
_PATCHES = (
    # ROTK Launcher's hardware fingerprint query and the installer's "is the
    # app running" checks; see resources/wine_powershell_hwid.c. 32-bit
    # processes (such as NSIS installers) resolve the syswow64 copy.
    _Patch("powershell_hwid_x86_64.exe", ("system32", *_POWERSHELL)),
    _Patch("powershell_hwid_i686.exe", ("syswow64", *_POWERSHELL)),
)


def _target_path(prefix: Path, patch: _Patch) -> Path:
    return prefix.joinpath("drive_c", "windows", *patch.target)


def _is_current(target: Path, wanted: bytes) -> bool:
    return (
        not target.is_symlink()
        and target.is_file()
        and os.access(target, os.X_OK)
        and target.read_bytes() == wanted
    )


def ensure_wine_patches(prefix: Path) -> None:
    """Makes sure the compatibility replacements are in a Proton prefix,
    installing or refreshing whichever are missing or differ. They are left
    in place: nothing here is ever removed. Only the given prefix is touched;
    the shared Proton installation and other prefixes are not.

    powershell.exe: Wine's is an unimplemented stub, so ROTK Launcher's
    hardware fingerprint query (run through PowerShell/WMI) comes back empty
    and the ROTK account service refuses the launch with `hwid_required`. The
    stand-in (a small native Windows program built from
    resources/wine_powershell_hwid.c) answers that query with this machine's
    real hardware values, answers the installer's "is the app running / close
    it" checks, and fails every other invocation, as a missing PowerShell
    would. It must be a real .exe: Wine can't connect a Windows process's
    pipes to a native Unix program, which crashes the launcher.

    It stays put because the app starts work in other processes, and its
    in-app updater runs an installer after the app has quit, so there is no
    point at which it could safely be taken back out. A symlink to Wine's stub
    is replaced; the stub itself lives in the Proton installation and is not
    touched.
    """
    for patch in _PATCHES:
        target = _target_path(prefix, patch)
        source = resources.files(__package__) / "resources" / patch.resource
        wanted = source.read_bytes()
        if _is_current(target, wanted):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        target.write_bytes(wanted)
        target.chmod(0o755)
        logger.info(f"Installed {patch.resource} at: {target}")
