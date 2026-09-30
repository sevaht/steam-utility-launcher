from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from steam_utility_launcher.github_release_updater import XDG_DATA_ROOT

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# The PowerShell stand-in appends every command it is asked to run to the file
# named by this variable; see resources/wine_powershell_hwid.c.
ENV_VAR = "SUL_POWERSHELL_LOG"
_LOG_NAME = "powershell-commands.log"
_UNRECOGNIZED_VERDICT = "verdict=HWID-UNRECOGNIZED"


def path() -> Path:
    """Where the stand-in logs, creating the directory it lives in."""
    XDG_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return XDG_DATA_ROOT / _LOG_NAME


def wine_path(host_path: Path) -> str:
    """The same file as Wine sees it (the host's root is drive Z:)."""
    return "Z:" + str(host_path).replace("/", "\\")


def size(log: Path) -> int:
    try:
        return log.stat().st_size
    except OSError:
        return 0


def unrecognized_since(log: Path, offset: int) -> int:
    """How many hardware-ID commands the stand-in refused since `offset`."""
    try:
        with log.open("rb") as file:
            # The log rotates when it gets large; a smaller file than before
            # means it was replaced, so everything in it is new.
            file.seek(offset if offset <= size(log) else 0)
            added = file.read().decode("utf-8", errors="replace")
    except OSError:
        return 0
    return added.count(_UNRECOGNIZED_VERDICT)


def report_unrecognized(log: Path, offset: int) -> int:
    """Says so, loudly, if the stand-in refused any hardware-ID command."""
    count = unrecognized_since(log, offset)
    if count:
        logger.error(
            "The PowerShell stand-in refused %d hardware-ID command(s) it does"
            " not recognise. ROTK probably changed how its launcher collects"
            " hardware information, and the stand-in"
            " (resources/wine_powershell_hwid.c) has to be updated to match;"
            " until then the game will likely reject the launch."
            " The commands are in: %s",
            count,
            log,
        )
    return count
