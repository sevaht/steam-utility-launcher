"""The Dark Souls II death lag patch, on stand-in executables."""

from __future__ import annotations

import json
import logging
from importlib import resources
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import application, ds2_deadzone, ds2_death_lag
from steam_utility_launcher.ds2_death_lag import (
    DLL_NAME,
    FIXED_CODE,
    ORIGINAL_CODE,
    PATCH,
    PATCH_OFFSET,
)
from steam_utility_launcher.patching import PatchState

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


def _main(
    tmp_path: Path, *words: str, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    """Runs the command on the stand-in executable; its exit status and
    standard output."""
    capsys.readouterr()
    status = application.main(
        [
            "ds2",
            "death-lag-fix",
            "--executable",
            str(tmp_path / "DarkSoulsII.exe"),
            *words,
        ]
    )
    return status, capsys.readouterr().out


def test_the_patch_recognizes_each_version_and_nothing_else() -> None:
    assert PATCH.state_of(_executable(ORIGINAL_CODE)) is PatchState.ORIGINAL
    assert PATCH.state_of(_executable(FIXED_CODE)) is PatchState.PATCHED
    assert PATCH.state_of(_executable(b"\0" * 45)) is PatchState.UNRECOGNIZED
    assert PATCH.state_of(b"short") is PatchState.UNRECOGNIZED


@pytest.mark.parametrize("group", ["ds2", "dark-souls-2"])
def test_status_patch_and_restore_through_either_name(
    group: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    arguments = [group, "death-lag-fix", "--executable", str(path)]
    assert application.main(arguments) == 0  # status is the default
    assert capsys.readouterr().out == "original\n"
    assert application.main([*arguments, "patch"]) == 0
    assert capsys.readouterr().out == "patched\n"
    assert path.read_bytes() == _executable(FIXED_CODE)
    assert application.main([*arguments, "restore"]) == 0
    assert capsys.readouterr().out == "original\n"
    assert path.read_bytes() == _executable(ORIGINAL_CODE)
    assert [entry.name for entry in tmp_path.iterdir()] == [path.name]


def test_json_says_where_the_fix_is_made(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    status, out = _main(tmp_path, "patch", "--json", capsys=capsys)
    assert status == 0
    assert json.loads(out) == {
        "fix": "death-lag-fix",
        "path": str(path),
        "state": "patched",
        "method": "executable",
        "changed": True,
        "dry_run": False,
    }
    status, out = _main(tmp_path, "--json", capsys=capsys)
    assert json.loads(out)["method"] == "executable"
    status, out = _main(tmp_path, "restore", "--json", capsys=capsys)
    assert json.loads(out)["method"] is None


def test_a_dry_run_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    assert _main(tmp_path, "patch", "--dry-run", capsys=capsys) == (
        0,
        "patched\n",
    )
    assert path.read_bytes() == _executable(ORIGINAL_CODE)


def test_an_unknown_executable_is_refused_and_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, b"\0" * 45)
    for action in ("patch", "restore"):
        assert _main(tmp_path, action, capsys=capsys) == (1, "")
    assert path.read_bytes() == _executable(b"\0" * 45)
    assert _main(tmp_path, capsys=capsys) == (0, "unrecognized\n")


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
        application.main(["ds2", "death-lag-fix", *arguments[1:], "patch"])
        == 0
    )
    assert path.read_bytes() == both(ds2_deadzone.ORIGINAL_CODE, FIXED_CODE)
    assert (
        application.main(["ds2", "deadzone-fix", *arguments[1:], "patch"]) == 0
    )
    assert path.read_bytes() == both(ds2_deadzone.CENTERED_CODE, FIXED_CODE)
    assert (
        application.main(["ds2", "death-lag-fix", *arguments[1:], "restore"])
        == 0
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
    assert _main(tmp_path, "patch", "--method", "dll", capsys=capsys) == (
        0,
        "patched\n",
    )
    assert (tmp_path / DLL_NAME).read_bytes() == ds2_death_lag.bundled_dll()
    assert path.read_bytes() == _executable(ORIGINAL_CODE)  # file untouched
    status, out = _main(tmp_path, "--json", capsys=capsys)
    assert (status, json.loads(out)["state"], json.loads(out)["method"]) == (
        0,
        "patched",
        "dll",
    )


def test_dll_after_the_file_patch_restores_the_file_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, FIXED_CODE)
    assert _main(tmp_path, "patch", "--method", "dll", capsys=capsys)[0] == 0
    assert path.read_bytes() == _executable(ORIGINAL_CODE)
    assert (tmp_path / DLL_NAME).exists()


def test_the_file_patch_and_restore_remove_the_dll(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, ORIGINAL_CODE)
    for action, code in (("patch", FIXED_CODE), ("restore", ORIGINAL_CODE)):
        _main(tmp_path, "patch", "--method", "dll", capsys=capsys)
        assert _main(tmp_path, action, capsys=capsys)[0] == 0
        assert not (tmp_path / DLL_NAME).exists()
        assert path.read_bytes() == _executable(code)


def test_the_dll_already_there_is_already_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, ORIGINAL_CODE)
    _main(tmp_path, "patch", "--method", "dll", capsys=capsys)
    status, out = _main(
        tmp_path, "patch", "--method", "dll", "--json", capsys=capsys
    )
    assert (status, json.loads(out)["changed"]) == (0, False)


def test_a_dll_of_another_make_is_never_replaced_or_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, ORIGINAL_CODE)
    other = tmp_path / DLL_NAME
    other.write_bytes(b"MZ another mod")
    assert _main(tmp_path, "patch", "--method", "dll", capsys=capsys) == (
        1,
        "",
    )
    assert _main(tmp_path, "patch", capsys=capsys)[0] == 0
    assert _main(tmp_path, "restore", capsys=capsys)[0] == 0
    assert other.read_bytes() == b"MZ another mod"


def test_a_dry_run_of_the_dll_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, ORIGINAL_CODE)
    assert _main(
        tmp_path, "patch", "--method", "dll", "--dry-run", capsys=capsys
    ) == (0, "patched\n")
    assert [p.name for p in tmp_path.iterdir()] == ["DarkSoulsII.exe"]


def test_the_dll_over_an_unknown_executable_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, b"\0" * 45)
    assert _main(tmp_path, "patch", "--method", "dll", capsys=capsys) == (
        1,
        "",
    )
    assert [p.name for p in tmp_path.iterdir()] == ["DarkSoulsII.exe"]
