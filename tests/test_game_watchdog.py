from __future__ import annotations

import signal
from pathlib import Path

from steam_utility_launcher import game_watchdog

PREFIX_DIR = "/games/pfx"
Kills = list[tuple[int, int]]


def _process(
    proc: Path, pid: int, *, name: str, threads: int, prefix: str = PREFIX_DIR
) -> None:
    directory = proc / str(pid)
    (directory / "task").mkdir(parents=True)
    for tid in range(threads):
        (directory / "task" / str(pid + tid)).mkdir()
    (directory / "comm").write_text(name + "\n")
    (directory / "environ").write_bytes(
        b"A=1\0WINEPREFIX=" + prefix.encode() + b"\0B=2\0"
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _watchdog(
    proc: Path, clock: _Clock, killed: Kills
) -> game_watchdog.StartupWatchdog:
    return game_watchdog.StartupWatchdog(
        Path(PREFIX_DIR),
        timeout=30,
        proc=proc,
        clock=clock,
        kill=lambda pid, sig: killed.append((pid, sig)),
    )


def test_only_the_games_in_this_prefix_are_found(tmp_path: Path) -> None:
    _process(tmp_path, 10, name="H1Z1.exe", threads=2)
    _process(tmp_path, 20, name="H1Z1.exe", threads=3, prefix="/other/pfx")
    _process(tmp_path, 30, name="steam.exe", threads=2)
    (tmp_path / "self").mkdir()
    found = game_watchdog.game_processes(Path(PREFIX_DIR), tmp_path)
    assert found == {10: 2}


def test_a_game_stuck_at_two_threads_is_stopped_after_the_timeout(
    tmp_path: Path,
) -> None:
    _process(tmp_path, 10, name="H1Z1.exe", threads=2)
    clock, killed = _Clock(), Kills()
    watchdog = _watchdog(tmp_path, clock, killed)
    watchdog.check()
    clock.now += 29
    watchdog.check()
    assert killed == []
    clock.now += 2
    watchdog.check()
    assert killed == [(10, signal.SIGKILL)]


def test_a_game_that_starts_properly_is_never_touched(tmp_path: Path) -> None:
    _process(tmp_path, 10, name="H1Z1.exe", threads=2)
    clock, killed = _Clock(), Kills()
    watchdog = _watchdog(tmp_path, clock, killed)
    watchdog.check()
    for tid in range(100, 140):  # it grows its worker threads
        (tmp_path / "10" / "task" / str(tid)).mkdir()
    clock.now += 10
    watchdog.check()
    clock.now += 600
    watchdog.check()
    assert killed == []


def test_a_game_that_exits_is_forgotten(tmp_path: Path) -> None:
    _process(tmp_path, 10, name="H1Z1.exe", threads=2)
    clock, killed = _Clock(), Kills()
    watchdog = _watchdog(tmp_path, clock, killed)
    watchdog.check()
    clock.now += 20
    for path in sorted((tmp_path / "10").rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    (tmp_path / "10").rmdir()
    watchdog.check()
    _process(tmp_path, 10, name="H1Z1.exe", threads=2)  # a new game, same pid
    clock.now += 20
    watchdog.check()
    assert killed == []  # its 30 seconds start over
