from __future__ import annotations

import os
from importlib import resources
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher.wine_prefix_patches import ensure_wine_patches

if TYPE_CHECKING:
    from pathlib import Path

_PREBUILT = ("powershell_hwid_x86_64.exe", "powershell_hwid_i686.exe")


def _resource_bytes(name: str) -> bytes:
    source = resources.files("steam_utility_launcher") / "resources" / name
    return source.read_bytes()


def _powershell(prefix: Path, subdir: str) -> Path:
    return (
        prefix
        / "drive_c"
        / "windows"
        / subdir
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )


@pytest.mark.parametrize("name", _PREBUILT)
def test_prebuilt_binaries_are_windows_executables(name: str) -> None:
    assert _resource_bytes(name).startswith(b"MZ")


def test_each_directory_gets_its_own_architecture(tmp_path: Path) -> None:
    ensure_wine_patches(tmp_path)
    for subdir, name in (
        ("system32", "powershell_hwid_x86_64.exe"),
        ("syswow64", "powershell_hwid_i686.exe"),
    ):
        path = _powershell(tmp_path, subdir)
        assert not path.is_symlink()
        assert path.read_bytes() == _resource_bytes(name)
        assert os.access(path, os.X_OK)


def test_wines_stub_symlink_is_replaced_but_the_stub_is_not_touched(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin.exe"
    builtin.write_text("builtin")
    system32 = _powershell(tmp_path, "system32")
    system32.parent.mkdir(parents=True)
    system32.symlink_to(builtin)
    ensure_wine_patches(tmp_path)
    assert not system32.is_symlink()
    assert system32.read_bytes() == _resource_bytes(_PREBUILT[0])
    assert builtin.read_text() == "builtin"


def test_a_different_file_is_replaced(tmp_path: Path) -> None:
    target = _powershell(tmp_path, "syswow64")
    target.parent.mkdir(parents=True)
    target.write_text("an older stand-in")
    ensure_wine_patches(tmp_path)
    assert target.read_bytes() == _resource_bytes(_PREBUILT[1])


def test_a_file_that_is_not_executable_is_fixed(tmp_path: Path) -> None:
    ensure_wine_patches(tmp_path)
    target = _powershell(tmp_path, "system32")
    target.chmod(0o644)
    ensure_wine_patches(tmp_path)
    assert os.access(target, os.X_OK)


def test_a_current_stand_in_is_left_alone(tmp_path: Path) -> None:
    ensure_wine_patches(tmp_path)
    paths = [_powershell(tmp_path, d) for d in ("system32", "syswow64")]
    before = [(p.stat().st_ino, p.stat().st_mtime_ns) for p in paths]
    ensure_wine_patches(tmp_path)
    assert [(p.stat().st_ino, p.stat().st_mtime_ns) for p in paths] == before
