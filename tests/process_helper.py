"""A process that runs nothing, for tests of what runs one."""

from __future__ import annotations

from typing import TYPE_CHECKING

from steam_utility_launcher.steam import Process

if TYPE_CHECKING:
    from collections.abc import Callable

    from steam_utility_launcher.steam import RunningProcess


class FakeRunning:
    """A started program that has already finished, with an exit status."""

    pid = 1

    def __init__(self, on_wait: Callable[[], int]) -> None:
        self._on_wait = on_wait

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self._on_wait()

    def poll(self) -> int | None:
        return self._on_wait()

    def terminate(self) -> None:
        return None


class FakeProcess(Process):
    """A `Process` whose start runs nothing: waiting for it calls `on_wait`,
    and what that returns is its exit status."""

    def __init__(
        self,
        on_wait: Callable[[], int] = lambda: 0,
        command_line: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        super().__init__(command_line=command_line or ["fake"], env=env)
        self._on_wait = on_wait

    def start(self) -> RunningProcess:
        return FakeRunning(self._on_wait)
