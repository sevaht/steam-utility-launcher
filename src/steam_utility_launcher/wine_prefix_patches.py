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


@dataclass(frozen=True)
class _Patch:
    resource: str
    # Relative to <prefix>/drive_c/windows
    target: tuple[str, ...]


_POWERSHELL = ("WindowsPowerShell", "v1.0", "powershell.exe")
_PATCHES = (
    # ROTK Launcher's hardware fingerprint query; see resources/
    # wine_powershell_hwid.c. 32-bit processes (such as NSIS installers)
    # resolve the syswow64 copy.
    _Patch("powershell_hwid_x86_64.exe", ("system32", *_POWERSHELL)),
    _Patch("powershell_hwid_i686.exe", ("syswow64", *_POWERSHELL)),
)


def _target_path(prefix: Path, patch: _Patch) -> Path:
    return prefix.joinpath("drive_c", "windows", *patch.target)


@dataclass
class _PreviousState:
    target: Path
    symlink_to: Path | None
    backup: Path | None


def _deploy(patch: _Patch, target: Path) -> _PreviousState:
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
    source_ref = resources.files(__package__) / "resources" / patch.resource
    with resources.as_file(source_ref) as source:
        shutil.copyfile(source, target)
    target.chmod(0o755)
    logger.info(f"Deployed {patch.resource} to: {target}")
    return _PreviousState(target=target, symlink_to=symlink_to, backup=backup)


def _restore(state: _PreviousState) -> None:
    if state.target.exists() or state.target.is_symlink():
        state.target.unlink()
    if state.symlink_to is not None:
        state.target.symlink_to(state.symlink_to)
    elif state.backup is not None:
        state.backup.rename(state.target)
    logger.info(f"Restored original file at: {state.target}")


@contextmanager
def temporary_wine_patches(prefix: Path) -> Iterator[None]:
    """Puts small compatibility replacements into a Proton prefix for the life
    of this context manager, then puts back exactly whatever was there before
    (a symlink, a real file, or nothing), even on error. Only the given prefix
    is touched; the shared Proton installation and other prefixes are not.

    powershell.exe: Wine's is an unimplemented stub, so ROTK Launcher's
    hardware fingerprint query (run through PowerShell/WMI) comes back empty
    and the ROTK account service refuses the launch with `hwid_required`. The
    stand-in (a small native Windows program built from
    resources/wine_powershell_hwid.c) answers that one query with this
    machine's real hardware values and fails every other invocation, as a
    missing PowerShell would. It must be a real .exe: Wine can't connect a
    Windows process's pipes to a native Unix program, which crashes the
    launcher.
    """
    states: list[_PreviousState] = []
    try:
        for patch in _PATCHES:
            # One at a time, so a failure part-way still restores the ones
            # already deployed.
            state = _deploy(patch, _target_path(prefix, patch))
            states.append(state)
        yield
    finally:
        for state in reversed(states):
            _restore(state)
