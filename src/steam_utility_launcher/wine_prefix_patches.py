from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from steam_utility_launcher.wine_prefix import WinePrefix


@dataclass(frozen=True)
class _Patch:
    resource: str
    # Relative to <prefix>/drive_c/windows
    target: tuple[str, ...]


_POWERSHELL = ("WindowsPowerShell", "v1.0", "powershell.exe")
# Where the stand-in is, as a Windows program in the prefix names it.
POWERSHELL_STAND_IN_PATH = str(
    PureWindowsPath("C:/windows/system32", *_POWERSHELL)
)
_PATCHES = (
    # ROTK Launcher's hardware fingerprint query and the installer's "is the
    # app running" checks; see resources/wine_powershell_hwid.c. 32-bit
    # processes (such as NSIS installers) resolve the syswow64 copy.
    _Patch("powershell_hwid_x86_64.exe", ("system32", *_POWERSHELL)),
    _Patch("powershell_hwid_i686.exe", ("syswow64", *_POWERSHELL)),
)


def ensure_wine_patches(prefix: WinePrefix) -> None:
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
        source = resources.files(__package__) / "resources" / patch.resource
        prefix.install_file(patch.target, source.read_bytes(), executable=True)
