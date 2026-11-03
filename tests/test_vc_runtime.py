from __future__ import annotations

import hashlib
import struct
from typing import TYPE_CHECKING, cast

import pytest

from steam_utility_launcher import vc_runtime
from steam_utility_launcher.wine_prefix import WinePrefix

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from steam_utility_launcher.steam import Process

_SIGNATURE = b"\xbd\x04\xef\xfe"


def _dll_bytes(major: int, minor: int, build: int = 0, rev: int = 0) -> bytes:
    info = struct.pack(
        "<IIII",
        0xFEEF04BD,
        0x10000,
        (major << 16) | minor,
        (build << 16) | rev,
    )
    return b"MZ" + bytes(64) + info + bytes(32)


def _builtin_bytes(major: int, minor: int) -> bytes:
    """What Wine ships: a marker in the DOS header, and a version resource
    that claims to be a real runtime."""
    info = _dll_bytes(major, minor)[2 + 64 :]
    return b"MZ" + bytes(62) + b"Wine builtin DLL" + bytes(32) + info


def _names(directory: str) -> list[str]:
    redist = next(
        r
        for r in vc_runtime._REDISTRIBUTABLES
        if r.system_directory == directory
    )
    return list(redist.dlls)


def _dll(prefix: Path, directory: str, name: str) -> Path:
    return prefix / "drive_c" / "windows" / directory / f"{name}.dll"


def _write_dll(prefix: Path, directory: str, version: tuple[int, int]) -> Path:
    """Every runtime DLL of that directory, as real Microsoft files."""
    for name in _names(directory):
        path = _dll(prefix, directory, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_dll_bytes(*version))
    return _dll(prefix, directory, "msvcp140")


def _write_builtin_dll(
    prefix: Path, directory: str, version: tuple[int, int] = (14, 42)
) -> None:
    """Every runtime DLL of that directory, as Wine's builtin stand-ins."""
    for name in _names(directory):
        path = _dll(prefix, directory, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_builtin_bytes(*version))


def test_file_version_reads_the_version_resource(tmp_path: Path) -> None:
    dll = tmp_path / "x.dll"
    dll.write_bytes(_dll_bytes(14, 44, 35211, 0))
    assert vc_runtime.file_version(dll) == (14, 44, 35211, 0)


def test_file_version_is_none_without_a_version_block(tmp_path: Path) -> None:
    dll = tmp_path / "x.dll"
    dll.write_bytes(b"MZ" + bytes(100))
    assert vc_runtime.file_version(dll) is None
    assert vc_runtime.file_version(tmp_path / "missing.dll") is None


@pytest.mark.parametrize(
    ("version", "outdated"),
    [
        ((14, 0), True),
        ((14, 39), True),
        ((14, 40), False),
        ((14, 44), False),
        ((15, 0), False),
    ],
)
def test_minimum_version_boundary(
    tmp_path: Path, version: tuple[int, int], outdated: bool
) -> None:
    _write_dll(tmp_path, "system32", version)
    _write_dll(tmp_path, "syswow64", version)
    assert (
        bool(vc_runtime.outdated_redistributables(WinePrefix(tmp_path)))
        is outdated
    )


def test_missing_runtime_counts_as_outdated(tmp_path: Path) -> None:
    _write_dll(tmp_path, "system32", (14, 44))
    stale = vc_runtime.outdated_redistributables(WinePrefix(tmp_path))
    assert [r.architecture for r in stale] == ["x86"]


def test_wine_builtins_that_claim_a_current_version_are_not_current(
    tmp_path: Path,
) -> None:
    # A fresh Proton prefix: msvcp140.dll says 14.42, but it is Wine's.
    _write_builtin_dll(tmp_path, "system32")
    _write_builtin_dll(tmp_path, "syswow64")
    assert vc_runtime.file_version(_dll(tmp_path, "system32", "msvcp140")) == (
        14,
        42,
        0,
        0,
    )
    stale = vc_runtime.outdated_redistributables(WinePrefix(tmp_path))
    assert [r.architecture for r in stale] == ["x64", "x86"]
    problems = vc_runtime._problems(WinePrefix(tmp_path), stale[0])
    assert r"system32\msvcp140.dll: Wine builtin" in problems
    assert len(problems) == len(stale[0].dlls)


def test_one_builtin_among_real_files_is_enough_to_be_stale(
    tmp_path: Path,
) -> None:
    _write_dll(tmp_path, "system32", (14, 44))
    _write_dll(tmp_path, "syswow64", (14, 44))
    assert not vc_runtime.outdated_redistributables(WinePrefix(tmp_path))
    _dll(tmp_path, "system32", "vcruntime140").write_bytes(
        _builtin_bytes(14, 44)
    )
    stale = vc_runtime.outdated_redistributables(WinePrefix(tmp_path))
    assert [r.architecture for r in stale] == ["x64"]
    assert vc_runtime._problems(WinePrefix(tmp_path), stale[0]) == [
        r"system32\vcruntime140.dll: Wine builtin"
    ]


def test_a_missing_dll_of_the_set_is_stale(tmp_path: Path) -> None:
    _write_dll(tmp_path, "system32", (14, 44))
    _write_dll(tmp_path, "syswow64", (14, 44))
    _dll(tmp_path, "syswow64", "vcomp140").unlink()
    [stale] = vc_runtime.outdated_redistributables(WinePrefix(tmp_path))
    assert vc_runtime._problems(WinePrefix(tmp_path), stale) == [
        r"syswow64\vcomp140.dll: missing"
    ]


def test_only_the_x64_runtime_has_vcruntime140_1(tmp_path: Path) -> None:
    _write_dll(tmp_path, "system32", (14, 44))
    _write_dll(tmp_path, "syswow64", (14, 44))
    assert _dll(tmp_path, "system32", "vcruntime140_1").exists()
    assert not _dll(tmp_path, "syswow64", "vcruntime140_1").exists()
    assert not vc_runtime.outdated_redistributables(WinePrefix(tmp_path))


def test_pinned_hashes_look_like_sha256() -> None:
    for redist in vc_runtime._REDISTRIBUTABLES:
        assert len(redist.sha256) == 64
        assert redist.url.startswith("https://")


class _Runner:
    """Starts commands in the prefix, recording them."""

    def __init__(self, on_run: Callable[[list[str]], int]) -> None:
        self.on_run = on_run
        self.commands: list[list[str]] = []

    def __call__(self, command_line: list[str]) -> Process:
        self.commands.append(command_line)
        return cast("Process", _FakeProcess(lambda: self.on_run(command_line)))


class _FakeProcess:
    def __init__(self, run: Callable[[], int]) -> None:
        self.run = run

    def start(self) -> _FakeProcess:
        return self

    def wait(self) -> int:
        return int(self.run())


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(vc_runtime, "XDG_DATA_ROOT", tmp_path / "data")
    return tmp_path / "data" / "VC-Redist"


def _pin(monkeypatch: pytest.MonkeyPatch, payloads: dict[str, bytes]) -> None:
    pinned = tuple(
        vc_runtime._Redistributable(
            r.architecture,
            r.url,
            hashlib.sha256(payloads[r.architecture]).hexdigest(),
            r.system_directory,
        )
        for r in vc_runtime._REDISTRIBUTABLES
    )
    monkeypatch.setattr(vc_runtime, "_REDISTRIBUTABLES", pinned)

    def fake_get(url: str) -> tuple[int, bytes]:
        for redist in pinned:
            if redist.url == url:
                return 200, payloads[redist.architecture]
        return 404, b""

    monkeypatch.setattr(vc_runtime, "https_get", fake_get)


def test_nothing_happens_when_the_runtime_is_current(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "pfx"
    _write_dll(prefix, "system32", (14, 44))
    _write_dll(prefix, "syswow64", (14, 44))

    def no_network(url: str) -> tuple[int, bytes]:
        raise AssertionError(url)

    monkeypatch.setattr(vc_runtime, "https_get", no_network)
    runner = _Runner(lambda _: pytest.fail("must not run an installer"))
    vc_runtime.ensure_current(WinePrefix(prefix), run=runner)
    assert runner.commands == []
    assert not cache.exists()


def test_installs_only_the_stale_architecture_and_caches_it(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "pfx"
    _write_dll(prefix, "system32", (14, 44))
    _write_dll(prefix, "syswow64", (14, 0))
    _pin(monkeypatch, {"x64": b"x64-installer", "x86": b"x86-installer"})

    def run(command: list[str]) -> int:
        assert command[1:] == ["/install", "/quiet", "/norestart"]
        _write_dll(prefix, "syswow64", (14, 44))
        return 0

    runner = _Runner(run)
    vc_runtime.ensure_current(WinePrefix(prefix), run=runner)
    assert len(runner.commands) == 1
    assert runner.commands[0][0].endswith("vc_redist.x86.exe")
    assert (cache / "vc_redist.x86.exe").read_bytes() == b"x86-installer"
    assert not (cache / "vc_redist.x64.exe").exists()

    # A later run finds it current and needs nothing (and no network).
    monkeypatch.setattr(vc_runtime, "https_get", lambda _: pytest.fail("net"))
    vc_runtime.ensure_current(WinePrefix(prefix), run=runner)
    assert len(runner.commands) == 1


def test_cached_installer_is_reused_without_downloading(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin(monkeypatch, {"x64": b"x64-installer", "x86": b"x86-installer"})
    cache.mkdir(parents=True)
    (cache / "vc_redist.x86.exe").write_bytes(b"x86-installer")
    monkeypatch.setattr(vc_runtime, "https_get", lambda _: pytest.fail("net"))
    redist = vc_runtime._REDISTRIBUTABLES[1]
    assert vc_runtime._cached_installer(redist) == cache / "vc_redist.x86.exe"


def test_corrupt_cache_is_replaced(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin(monkeypatch, {"x64": b"x64-installer", "x86": b"x86-installer"})
    cache.mkdir(parents=True)
    (cache / "vc_redist.x86.exe").write_bytes(b"tampered")
    redist = vc_runtime._REDISTRIBUTABLES[1]
    path = vc_runtime._cached_installer(redist)
    assert path.read_bytes() == b"x86-installer"


def test_download_that_does_not_match_the_pin_is_never_run(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "pfx"
    _write_dll(prefix, "system32", (14, 0))
    _write_dll(prefix, "syswow64", (14, 44))
    _pin(monkeypatch, {"x64": b"expected", "x86": b"expected"})
    monkeypatch.setattr(vc_runtime, "https_get", lambda _: (200, b"evil"))
    runner = _Runner(lambda _: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="pinned checksum"):
        vc_runtime.ensure_current(WinePrefix(prefix), run=runner)
    assert runner.commands == []
    assert not (cache / "vc_redist.x64.exe").exists()


@pytest.mark.usefixtures("cache")
def test_http_error_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "pfx"
    _write_dll(prefix, "system32", (14, 0))
    _write_dll(prefix, "syswow64", (14, 44))
    monkeypatch.setattr(vc_runtime, "https_get", lambda _: (503, b""))
    runner = _Runner(lambda _: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="HTTP status 503"):
        vc_runtime.ensure_current(WinePrefix(prefix), run=runner)


@pytest.mark.usefixtures("cache")
def test_the_installer_replaces_wines_builtins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "pfx"
    _write_builtin_dll(prefix, "system32")
    _write_builtin_dll(prefix, "syswow64")
    _pin(monkeypatch, {"x64": b"x64-installer", "x86": b"x86-installer"})

    def run(command: list[str]) -> int:
        directory = "system32" if "x64" in command[0] else "syswow64"
        _write_dll(prefix, directory, (14, 44))
        return 0

    runner = _Runner(run)
    vc_runtime.ensure_current(WinePrefix(prefix), run=runner)
    assert [c[1] for c in runner.commands] == ["/install", "/install"]
    assert not vc_runtime.outdated_redistributables(WinePrefix(prefix))


@pytest.mark.usefixtures("cache")
def test_repair_pass_fixes_a_registered_but_old_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "pfx"
    _write_dll(prefix, "system32", (14, 0))
    _write_dll(prefix, "syswow64", (14, 44))
    _pin(monkeypatch, {"x64": b"x64-installer", "x86": b"x86-installer"})

    def run(command: list[str]) -> int:
        if command[1] == "/repair":
            _write_dll(prefix, "system32", (14, 44))
        return 0

    runner = _Runner(run)
    vc_runtime.ensure_current(WinePrefix(prefix), run=runner)
    assert [c[1] for c in runner.commands] == ["/install", "/repair"]


def test_fails_loudly_if_the_installer_did_not_update_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "pfx"
    _write_dll(prefix, "system32", (14, 0))
    _write_dll(prefix, "syswow64", (14, 44))
    _pin(monkeypatch, {"x64": b"x64-installer", "x86": b"x86-installer"})
    runner = _Runner(lambda _: 1)  # installer "runs" but changes nothing
    with pytest.raises(RuntimeError, match=r"still not Microsoft's") as error:
        vc_runtime.ensure_current(WinePrefix(prefix), run=runner)
    assert r"system32\msvcp140.dll: version 14.0.0.0" in str(error.value)
    assert "graphical session" in str(error.value)


@pytest.mark.usefixtures("cache")
def test_it_fails_loudly_if_builtins_are_still_there_afterwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "pfx"
    _write_builtin_dll(prefix, "system32")
    _write_dll(prefix, "syswow64", (14, 44))
    _pin(monkeypatch, {"x64": b"x64-installer", "x86": b"x86-installer"})
    runner = _Runner(lambda _: 2)  # a silent installer without a display
    with pytest.raises(RuntimeError, match="Wine builtin") as error:
        vc_runtime.ensure_current(WinePrefix(prefix), run=runner)
    assert r"system32\vcruntime140_1.dll: Wine builtin" in str(error.value)
