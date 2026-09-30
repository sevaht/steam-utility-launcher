from __future__ import annotations

import os
from importlib import resources
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher.wine_prefix_patches import temporary_wine_patches

if TYPE_CHECKING:
    from pathlib import Path


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


@pytest.mark.parametrize(
    "name", ["powershell_hwid_x86_64.exe", "powershell_hwid_i686.exe"]
)
def test_prebuilt_binaries_are_windows_executables(name: str) -> None:
    assert _resource_bytes(name).startswith(b"MZ")


def test_each_directory_gets_its_own_architecture_then_is_restored(
    tmp_path: Path,
) -> None:
    system32 = _powershell(tmp_path, "system32")
    syswow64 = _powershell(tmp_path, "syswow64")
    builtin = tmp_path / "builtin.exe"
    builtin.write_text("builtin")
    system32.parent.mkdir(parents=True)
    system32.symlink_to(builtin)

    with temporary_wine_patches(tmp_path):
        for path, name in (
            (system32, "powershell_hwid_x86_64.exe"),
            (syswow64, "powershell_hwid_i686.exe"),
        ):
            assert not path.is_symlink()
            assert path.read_bytes() == _resource_bytes(name)
            assert os.access(path, os.X_OK)

    assert system32.is_symlink()
    assert system32.readlink() == builtin
    assert not syswow64.exists()


def test_original_is_restored_after_an_error(tmp_path: Path) -> None:
    target = _powershell(tmp_path, "system32")
    target.parent.mkdir(parents=True)
    target.write_text("original")
    with pytest.raises(RuntimeError), temporary_wine_patches(tmp_path):
        raise RuntimeError
    assert target.read_text() == "original"
