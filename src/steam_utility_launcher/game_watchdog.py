from __future__ import annotations

import contextlib
import logging
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# H1Z1.exe sometimes hangs at start-up with only two threads, because a thread
# in the game's own patch library (rotkc.dll) suspends the main thread while it
# holds Wine's loader lock and then waits for that lock itself. A healthy start
# has well over a hundred threads within seconds, so a game still at two
# threads long after launching is stuck for good, and the launcher keeps
# reporting that it is in game. Stopping it lets the launcher go back to
# "ready" so the player can simply press Play again.
GAME_PROCESS_NAME = "H1Z1.exe"
POLL_SECONDS = 3.0
STARTUP_TIMEOUT_SECONDS = 40.0
HEALTHY_THREAD_COUNT = 10
_PROC = Path("/proc")
_COMM_LENGTH = 15  # the kernel keeps this many characters of a process name


def _wine_prefix(process_directory: Path) -> Path | None:
    try:
        environment = (process_directory / "environ").read_bytes()
    except OSError:
        return None
    for entry in environment.split(b"\0"):
        if entry.startswith(b"WINEPREFIX="):
            return Path(entry.split(b"=", 1)[1].decode(errors="replace"))
    return None


def game_processes(prefix: Path, proc: Path = _PROC) -> dict[int, int]:
    """H1Z1.exe processes of this Wine prefix, as {pid: thread count}."""
    found: dict[int, int] = {}
    for directory in proc.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            name = (directory / "comm").read_text().strip()
            if name != GAME_PROCESS_NAME[:_COMM_LENGTH]:
                continue
            if _wine_prefix(directory) != prefix:
                continue
            found[int(directory.name)] = len(
                list((directory / "task").iterdir())
            )
        except OSError:  # it exited while we looked
            continue
    return found


class StartupWatchdog:
    """Stops an H1Z1.exe that never gets past the start-up hang."""

    def __init__(
        self,
        prefix: Path,
        *,
        timeout: float = STARTUP_TIMEOUT_SECONDS,
        proc: Path = _PROC,
        clock: Callable[[], float] = time.monotonic,
        kill: Callable[[int, int], None] = os.kill,
    ) -> None:
        self._prefix = prefix
        self._timeout = timeout
        self._proc = proc
        self._clock = clock
        self._kill = kill
        self._first_seen: dict[int, float] = {}
        self._healthy: set[int] = set()

    def check(self) -> None:
        now = self._clock()
        seen = game_processes(self._prefix, self._proc)
        for pid, threads in seen.items():
            if pid in self._healthy:
                continue
            if threads >= HEALTHY_THREAD_COUNT:
                self._healthy.add(pid)
                continue
            first = self._first_seen.setdefault(pid, now)
            if now - first >= self._timeout:
                logger.warning(
                    "%s (pid %d) is stuck at start-up, a known race in the"
                    " game's own patcher; stopping it. Press Play again.",
                    GAME_PROCESS_NAME,
                    pid,
                )
                with contextlib.suppress(ProcessLookupError):
                    self._kill(pid, signal.SIGKILL)
                self._first_seen.pop(pid, None)
        for pid in set(self._first_seen) - seen.keys():
            del self._first_seen[pid]
        self._healthy &= seen.keys()
