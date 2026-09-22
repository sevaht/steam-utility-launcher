from __future__ import annotations

import logging
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

# Maps each architecture to the prefix subdirectory whose "system"
# powershell.exe should be replaced with that architecture's shim binary.
# The binaries are prebuilt (see build-powershell-shim) from
# resources/powershell_shim.c so that using this doesn't require a
# mingw-w64 cross-compiler to be installed.
_ARCH_TO_SUBDIR: dict[str, str] = {"i686": "syswow64", "x86_64": "system32"}


def _target_path(prefix: Path, subdir: str) -> Path:
    return (
        prefix
        / "drive_c"
        / "windows"
        / subdir
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )


@dataclass
class _PreviousState:
    target: Path
    symlink_to: Path | None
    backup: Path | None


def _deploy(arch: str, target: Path) -> _PreviousState:
    target.parent.mkdir(parents=True, exist_ok=True)
    symlink_to: Path | None = None
    backup: Path | None = None
    if target.is_symlink():
        symlink_to = target.readlink()
        target.unlink()
    elif target.exists():
        backup = target.with_name(f"{target.name}.orig")
        if backup.exists():
            backup.unlink()
        target.rename(backup)
    source_ref = (
        resources.files(__package__) / "resources" / f"powershell_{arch}.exe"
    )
    with resources.as_file(source_ref) as source:
        shutil.copyfile(source, target)
    target.chmod(0o755)
    logger.info(f"Deployed {arch} powershell.exe shim to: {target}")
    return _PreviousState(target=target, symlink_to=symlink_to, backup=backup)


def _restore(state: _PreviousState) -> None:
    if state.target.exists() or state.target.is_symlink():
        state.target.unlink()
    if state.symlink_to is not None:
        state.target.symlink_to(state.symlink_to)
    elif state.backup is not None:
        state.backup.rename(state.target)
    logger.info(f"Restored original powershell.exe at: {state.target}")


@contextmanager
def temporary_powershell_shim(prefix: Path) -> Iterator[None]:
    """Replaces a Proton prefix's powershell.exe with a compatibility shim
    for the life of this context manager, then puts back exactly whatever
    was there before (a symlink, a real file, or nothing), even on error.

    Wine's real powershell.exe is an unimplemented stub: it never runs the
    script it's given and always exits 0. Some Windows installers (notably
    electron-builder/NSIS-based ones) use PowerShell one-liners to detect
    and close an already-running instance of the app before installing or
    updating, treating exit 0 as "yes, it's running". Since the stub always
    exits 0, those installers believe the app is permanently running and
    can never finish. The installer invokes powershell.exe by its fully
    qualified path, so this can't be worked around with a PATH override;
    CreateProcess only consults PATH for an unqualified name.

    This deploys a small native replacement (see resources/powershell_shim.c)
    that recognizes that narrow script family and answers using real Win32
    process enumeration instead, only for the duration of a single
    installer/updater invocation. It's applied only to the given prefix,
    leaving the shared Proton installation and every other prefix untouched.
    """
    states: list[_PreviousState] = []
    try:
        for arch, subdir in _ARCH_TO_SUBDIR.items():
            states.append(_deploy(arch, _target_path(prefix, subdir)))
        yield
    finally:
        for state in states:
            _restore(state)
