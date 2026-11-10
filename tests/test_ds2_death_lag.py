"""The Dark Souls II death lag patch, on stand-in executables."""

from __future__ import annotations

import logging
from importlib import resources
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import (
    application,
    ds2_bindings,
    ds2_deadzone,
    ds2_death_lag,
)
from steam_utility_launcher.ds2_death_lag import (
    DLL_NAME,
    FIXED_CODE,
    ORIGINAL_CODE,
    PATCH_OFFSET,
    DeathLag,
    DeathLagPatcher,
    DeathLagRequest,
)
from steam_utility_launcher.errors import UserError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# Where the patched range starts in the running game, and the end of the
# handler that every one of its branches leads to.
PATCH_ADDRESS = 0x140B5E82C
SKIP_ADDRESS = 0x140B5E8EE
# Where each version has its conditional near jumps (`0f 8x` and a 32 bit
# displacement), as offsets into the code.
ORIGINAL_JUMPS = (4, 13, 35)
FIXED_JUMPS = (4, 21, 39)


def _executable(code: bytes) -> bytes:
    return b"\x11" * PATCH_OFFSET + code + b"\x22" * 100


def _write(tmp_path: Path, code: bytes) -> Path:
    path = tmp_path / "DarkSoulsII.exe"
    path.write_bytes(_executable(code))
    return path


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """application.main sets up logging on the stderr pytest is capturing."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture(autouse=True)
def _game_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds2_bindings, "game_is_running", lambda _host: False)


def _jump_targets(code: bytes, jumps: tuple[int, ...]) -> list[int]:
    targets = []
    for at in jumps:
        assert code[at] == 0x0F
        assert 0x80 <= code[at + 1] <= 0x8F
        displacement = int.from_bytes(code[at + 2 : at + 6], "little")
        targets.append(PATCH_ADDRESS + at + 6 + displacement)
    return targets


def test_the_two_versions_are_the_same_size_and_differ() -> None:
    assert len(ORIGINAL_CODE) == len(FIXED_CODE) == 45
    assert ORIGINAL_CODE != FIXED_CODE


def test_every_branch_of_both_versions_ends_the_same_handler() -> None:
    assert _jump_targets(ORIGINAL_CODE, ORIGINAL_JUMPS) == [SKIP_ADDRESS] * 3
    assert _jump_targets(FIXED_CODE, FIXED_JUMPS) == [SKIP_ADDRESS] * 3


def test_death_lag_of_recognizes_each_and_nothing_else() -> None:
    assert ds2_death_lag.death_lag_of(_executable(ORIGINAL_CODE)) is (
        DeathLag.ORIGINAL
    )
    assert ds2_death_lag.death_lag_of(_executable(FIXED_CODE)) is (
        DeathLag.FIXED
    )
    assert ds2_death_lag.death_lag_of(_executable(b"\0" * 45)) is (
        DeathLag.UNKNOWN
    )
    assert ds2_death_lag.death_lag_of(b"short") is DeathLag.UNKNOWN


def test_on_and_off_switch_only_the_code(tmp_path: Path) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    DeathLagPatcher(DeathLagRequest(path, DeathLag.FIXED)).run()
    assert path.read_bytes() == _executable(FIXED_CODE)
    DeathLagPatcher(DeathLagRequest(path, DeathLag.ORIGINAL)).run()
    assert path.read_bytes() == _executable(ORIGINAL_CODE)
    assert [p.name for p in tmp_path.iterdir()] == ["DarkSoulsII.exe"]


def test_status_and_dry_run_change_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    DeathLagPatcher(DeathLagRequest(path)).run()
    DeathLagPatcher(DeathLagRequest(path, DeathLag.FIXED, dry_run=True)).run()
    assert path.read_bytes() == _executable(ORIGINAL_CODE)
    assert "Would switch" in capsys.readouterr().out


def test_it_is_already_so_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, FIXED_CODE)
    DeathLagPatcher(DeathLagRequest(path, DeathLag.FIXED)).run()
    assert "Already so" in capsys.readouterr().out


def test_an_unknown_executable_is_never_written(tmp_path: Path) -> None:
    path = _write(tmp_path, b"\0" * 45)
    with pytest.raises(UserError, match="not a game version"):
        DeathLagPatcher(DeathLagRequest(path, DeathLag.FIXED)).run()
    assert path.read_bytes() == _executable(b"\0" * 45)
    DeathLagPatcher(DeathLagRequest(path)).run()  # just asking is fine


def test_a_running_game_is_not_touched(tmp_path: Path) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    request = DeathLagRequest(path, DeathLag.FIXED, game_running=True)
    with pytest.raises(UserError, match="Close Dark Souls II"):
        DeathLagPatcher(request).run()
    assert path.read_bytes() == _executable(ORIGINAL_CODE)


def test_a_missing_file_is_a_user_error(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="Could not read"):
        DeathLagPatcher(DeathLagRequest(tmp_path / "nope.exe")).run()


@pytest.mark.parametrize("group", ["ds2", "dark-souls-2"])
def test_both_names_of_the_group_work(
    group: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    arguments = [group, "death-lag-fix", "--executable", str(path)]
    assert application.main([*arguments, "status"]) == 0
    assert "game's own achievement handling" in capsys.readouterr().out
    assert application.main([*arguments, "on"]) == 0
    assert path.read_bytes() == _executable(FIXED_CODE)
    assert application.main([*arguments, "off"]) == 0
    assert path.read_bytes() == _executable(ORIGINAL_CODE)


def test_it_and_the_deadzone_patch_leave_each_other_alone(
    tmp_path: Path,
) -> None:
    """Both change the same file, at places that don't touch."""
    deadzone_end = ds2_deadzone.PATCH_OFFSET + len(ds2_deadzone.ORIGINAL_CODE)
    assert deadzone_end <= PATCH_OFFSET

    def both(deadzone: bytes, death_lag: bytes) -> bytes:
        return (
            b"\x11" * ds2_deadzone.PATCH_OFFSET
            + deadzone
            + b"\x33" * (PATCH_OFFSET - deadzone_end)
            + death_lag
            + b"\x22" * 100
        )

    path = tmp_path / "DarkSoulsII.exe"
    path.write_bytes(both(ds2_deadzone.ORIGINAL_CODE, ORIGINAL_CODE))
    arguments = ["ds2", "--executable", str(path)]
    assert (
        application.main(["ds2", "death-lag-fix", *arguments[1:], "on"]) == 0
    )
    assert path.read_bytes() == both(ds2_deadzone.ORIGINAL_CODE, FIXED_CODE)
    assert application.main(["ds2", "deadzone-fix", *arguments[1:], "on"]) == 0
    assert path.read_bytes() == both(ds2_deadzone.CENTERED_CODE, FIXED_CODE)
    assert (
        application.main(["ds2", "death-lag-fix", *arguments[1:], "off"]) == 0
    )
    assert path.read_bytes() == both(ds2_deadzone.CENTERED_CODE, ORIGINAL_CODE)


# --- the DLL method ---------------------------------------------------------


def test_the_bundled_dll_is_a_small_windows_library() -> None:
    dll = ds2_death_lag.bundled_dll()
    assert dll.startswith(b"MZ")
    assert len(dll) < 8192
    pe = int.from_bytes(dll[0x3C:0x40], "little")
    assert dll[pe : pe + 4] == b"PE\0\0"
    assert int.from_bytes(dll[pe + 4 : pe + 6], "little") == 0x8664  # x86-64
    assert int.from_bytes(dll[pe + 22 : pe + 24], "little") & 0x2000  # a DLL


def test_the_dll_has_the_bytes_this_module_has_and_forwards_dinput8() -> None:
    dll = ds2_death_lag.bundled_dll()
    assert dll.count(ORIGINAL_CODE) == 1
    assert dll.count(FIXED_CODE) == 1
    assert b"DirectInput8Create" in dll
    # It patches where this module does: the file offset's virtual address.
    assert PATCH_ADDRESS - 0x140000000 == 0xB5E82C
    source = (
        resources.files("steam_utility_launcher")
        / "resources"
        / "ds2_death_lag_dinput8.c"
    ).read_text()
    assert "0xB5E82CUL" in source


def test_dll_installs_beside_the_executable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    DeathLagPatcher(DeathLagRequest(path, DeathLag.DLL)).run()
    assert (tmp_path / DLL_NAME).read_bytes() == ds2_death_lag.bundled_dll()
    assert path.read_bytes() == _executable(ORIGINAL_CODE)  # file untouched
    DeathLagPatcher(DeathLagRequest(path, None)).run()
    assert "dinput8.dll in the game's folder" in capsys.readouterr().out


def test_dll_after_the_file_patch_restores_the_file_first(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, FIXED_CODE)
    DeathLagPatcher(DeathLagRequest(path, DeathLag.DLL)).run()
    assert path.read_bytes() == _executable(ORIGINAL_CODE)
    assert (tmp_path / DLL_NAME).exists()


@pytest.mark.parametrize(
    ("want", "code"),
    [(DeathLag.FIXED, FIXED_CODE), (DeathLag.ORIGINAL, ORIGINAL_CODE)],
)
def test_on_and_off_remove_the_dll(
    want: DeathLag, code: bytes, tmp_path: Path
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    DeathLagPatcher(DeathLagRequest(path, DeathLag.DLL)).run()
    DeathLagPatcher(DeathLagRequest(path, want)).run()
    assert not (tmp_path / DLL_NAME).exists()
    assert path.read_bytes() == _executable(code)


def test_a_dll_of_another_make_is_never_replaced_or_removed(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    other = tmp_path / DLL_NAME
    other.write_bytes(b"MZ another mod")
    with pytest.raises(UserError, match="another make"):
        DeathLagPatcher(DeathLagRequest(path, DeathLag.DLL)).run()
    DeathLagPatcher(DeathLagRequest(path, DeathLag.FIXED)).run()
    assert other.read_bytes() == b"MZ another mod"


def test_dll_dry_run_and_a_running_game_change_nothing(tmp_path: Path) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    DeathLagPatcher(DeathLagRequest(path, DeathLag.DLL, dry_run=True)).run()
    with pytest.raises(UserError, match="Close Dark Souls II"):
        DeathLagPatcher(
            DeathLagRequest(path, DeathLag.DLL, game_running=True)
        ).run()
    assert [p.name for p in tmp_path.iterdir()] == ["DarkSoulsII.exe"]


def test_the_dll_over_an_unknown_executable_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, b"\0" * 45)
    with pytest.raises(UserError, match="not a game version"):
        DeathLagPatcher(DeathLagRequest(path, DeathLag.DLL)).run()
    assert [p.name for p in tmp_path.iterdir()] == ["DarkSoulsII.exe"]


def test_the_command_takes_dll(tmp_path: Path) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    arguments = ["ds2", "death-lag-fix", "--executable", str(path)]
    assert application.main([*arguments, "dll"]) == 0
    assert (tmp_path / DLL_NAME).exists()
    assert application.main([*arguments, "on"]) == 0
    assert not (tmp_path / DLL_NAME).exists()
    assert path.read_bytes() == _executable(FIXED_CODE)
