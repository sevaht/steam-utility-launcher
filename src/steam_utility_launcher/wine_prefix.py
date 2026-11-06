"""A Wine prefix on disk (a Proton `pfx` directory): where its files are, and
the operations that change them.

Whatever is done to a prefix is done to the one object it was given, so a fix
cannot end up in another game's prefix.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import PurePath, PureWindowsPath
from typing import TYPE_CHECKING

from steam_utility_launcher.keyvalues import Section

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

logger = logging.getLogger(__name__)

_DXVK_DLLS = ("d3d11", "d3d10core", "d3d9", "dxgi")
_NVAPI_DLLS = ("nvapi64", "nvofapi64", "nvapi")

# Wine marks its own DLLs in the DOS header (at 0x40).
_BUILTIN_MARKERS = (b"Wine builtin DLL", b"Wine placeholder DLL")
_BUILTIN_MARKER_SPAN = 128


def wine_path(host_path: PurePath) -> str:
    """The same file as Wine sees it (the host's root is drive Z:)."""
    return str(PureWindowsPath("Z:" + host_path.as_posix()))


def is_wine_builtin(path: Path) -> bool:
    """Whether the file is Wine's own stand-in and not a Microsoft binary.

    Symlinks are followed, so a link to a builtin counts as one.
    """
    try:
        with path.open("rb") as stream:
            head = stream.read(_BUILTIN_MARKER_SPAN)
    except OSError:
        return False
    return any(marker in head for marker in _BUILTIN_MARKERS)


@dataclass(frozen=True)
class WinePrefix:
    path: Path  # the `pfx` directory

    @property
    def compat_data_path(self) -> Path:
        """The directory Proton keeps the prefix, and its `version` file, in."""
        return self.path.parent

    @property
    def windows(self) -> Path:
        return self.path / "drive_c" / "windows"

    @property
    def program_files(self) -> Path:
        return self.path / "drive_c" / "Program Files"

    @property
    def _user_profile(self) -> Path:
        return self.path / "drive_c" / "users" / "steamuser"

    @property
    def roaming_appdata(self) -> Path:
        return self._user_profile / "AppData" / "Roaming"

    @property
    def local_appdata(self) -> Path:
        return self._user_profile / "AppData" / "Local"

    def windows_file(self, *parts: str) -> Path:
        """A file below drive_c/windows, as the parts of its path."""
        return self.windows.joinpath(*parts)

    def system_dll(self, directory: str, name: str) -> Path:
        """`name`.dll in system32 (64-bit) or syswow64 (32-bit)."""
        return self.windows_file(directory, f"{name}.dll")

    def is_made(self) -> bool:
        """Whether Proton has set the prefix up (it writes `version` last)."""
        return (self.compat_data_path / "version").exists()

    def _is_dxvk(self, name: str) -> bool:
        try:
            content = self.system_dll("system32", name).read_bytes()
        except OSError:
            return False
        return b"dxvk" in content.lower()

    def dll_overrides(self) -> str:
        """The DLL overrides Proton's `proton` script would set for the prefix.

        A process run with Proton's bare `wine` and not through the script
        needs these, or Wine silently uses its own (OpenGL) Direct3D instead of
        the DXVK that Proton put in the prefix, which is far slower. Only DLLs
        that are actually in the prefix are overridden, so a prefix Proton
        hasn't set up yet (or one set to use WineD3D) is left alone.
        """
        parts = []
        # d3d10core is only a thin forwarder to d3d11 and has no marker.
        dxvk = [
            name
            for name in _DXVK_DLLS
            if self._is_dxvk("d3d11" if name == "d3d10core" else name)
        ]
        if dxvk:
            parts.append(f"{','.join(dxvk)}=n")
        if any(
            self.system_dll("system32", name).is_file() for name in _NVAPI_DLLS
        ):
            parts.append("nvapi64,nvofapi64,nvapi=n;nvcuda=b")
        # As Proton always does: BattlEye's client, and a driver that crashes.
        parts.extend(("beclient,beclient_x64=b,n", "winebth.sys=d"))
        return ";".join(parts)

    def list_steam_library(
        self, library: PurePath, app_ids: Sequence[str]
    ) -> None:
        """Has the prefix's own Steam folder (`Program Files/Steam`, which is
        where a Windows program that looks for Steam looks) list the Steam
        library `library`, with `app_ids` installed in it, so such a program
        finds those games there. The library is named by its Z: path. It is
        not in the folder Proton makes for the prefix: Proton rewrites that
        list on every start. Raises OSError if the list can't be written."""
        steamapps = self.program_files / "Steam" / "steamapps"
        apps = Section(dict.fromkeys(app_ids, "0"))
        folder = Section([("path", wine_path(library)), ("apps", apps)])
        content = Section(
            [("libraryfolders", Section([("0", folder)]))]
        ).to_text()
        target = steamapps / "libraryfolders.vdf"
        steamapps.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text() != content:
            target.write_text(content)

    def map_drive(self, letter: str, target: Path) -> None:
        """Makes the drive `letter`: of the prefix show `target`."""
        dosdevices = self.path / "dosdevices"
        dosdevices.mkdir(parents=True, exist_ok=True)
        drive = dosdevices / f"{letter}:"
        if drive.exists() or drive.is_symlink():
            try:
                if drive.resolve() == target.resolve():
                    return
            except OSError:
                pass
            drive.unlink()
        drive.symlink_to(target)

    def install_file(
        self,
        relative: Sequence[str],
        content: bytes,
        *,
        executable: bool = False,
    ) -> bool:
        """Puts the file at `relative` (below drive_c/windows) in the prefix,
        and says whether it had to. One that is already there with exactly
        this content (and is executable, if asked) is left alone; anything
        else, a symlink included, is replaced. A symlink is only unlinked: what
        it points to (Proton's own file, say) is not touched."""
        target = self.windows_file(*relative)
        if (
            not target.is_symlink()
            and target.is_file()
            and (not executable or os.access(target, os.X_OK))
            and target.read_bytes() == content
        ):
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        target.write_bytes(content)
        if executable:
            target.chmod(0o755)
        logger.info(f"Installed {target.name} at: {target}")
        return True
