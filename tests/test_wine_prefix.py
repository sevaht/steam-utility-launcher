from __future__ import annotations

import os
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import vdf  # type: ignore[import-untyped]

from steam_utility_launcher.wine_prefix import (
    WinePrefix,
    is_wine_builtin,
    wine_path,
)
from steam_utility_launcher.wine_prefix_patches import POWERSHELL_STAND_IN_PATH

if TYPE_CHECKING:
    from pathlib import Path


def _builtin_bytes() -> bytes:
    return b"MZ" + bytes(62) + b"Wine builtin DLL" + bytes(64)


def test_the_places_of_a_prefix_follow_from_its_path(tmp_path: Path) -> None:
    prefix = WinePrefix(tmp_path / "compatdata" / "42" / "pfx")
    assert prefix.compat_data_path == tmp_path / "compatdata" / "42"
    assert prefix.windows == prefix.path / "drive_c" / "windows"
    assert prefix.program_files == prefix.path / "drive_c" / "Program Files"
    assert prefix.windows_file("system32", "x.exe") == (
        prefix.windows / "system32" / "x.exe"
    )
    assert prefix.system_dll("syswow64", "msvcp140") == (
        prefix.windows / "syswow64" / "msvcp140.dll"
    )


def test_a_prefix_is_made_when_proton_has_written_its_version(
    tmp_path: Path,
) -> None:
    prefix = WinePrefix(tmp_path / "42" / "pfx")
    assert not prefix.is_made()
    prefix.compat_data_path.mkdir(parents=True)
    assert not prefix.is_made()
    (prefix.compat_data_path / "version").write_text("GE-Proton11-11")
    assert prefix.is_made()


def test_a_link_to_a_builtin_is_a_builtin_and_a_link_to_a_real_file_is_not(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin.dll"
    builtin.write_bytes(_builtin_bytes())
    real = tmp_path / "real.dll"
    real.write_bytes(b"MZ" + bytes(200))
    to_builtin = tmp_path / "to_builtin.dll"
    to_builtin.symlink_to(builtin)
    to_real = tmp_path / "to_real.dll"
    to_real.symlink_to(real)
    assert is_wine_builtin(to_builtin)
    assert not is_wine_builtin(to_real)
    assert not is_wine_builtin(tmp_path / "missing.dll")


def test_wine_placeholder_files_count_as_builtins(tmp_path: Path) -> None:
    placeholder = tmp_path / "x.dll"
    placeholder.write_bytes(b"MZ" + bytes(62) + b"Wine placeholder DLL")
    assert is_wine_builtin(placeholder)


def test_a_drive_is_mapped_and_a_wrong_mapping_is_replaced(
    tmp_path: Path,
) -> None:
    prefix = WinePrefix(tmp_path / "pfx")
    first = tmp_path / "library_a"
    second = tmp_path / "library_b"
    first.mkdir()
    second.mkdir()
    drive = prefix.path / "dosdevices" / "s:"

    prefix.map_drive("s", first)
    assert drive.is_symlink()
    assert drive.resolve() == first.resolve()

    before = drive.lstat().st_mtime_ns
    prefix.map_drive("s", first)  # already right: left alone
    assert drive.lstat().st_mtime_ns == before

    prefix.map_drive("s", second)
    assert drive.resolve() == second.resolve()


def test_a_file_is_installed_once_and_then_left_alone(tmp_path: Path) -> None:
    prefix = WinePrefix(tmp_path / "pfx")
    where = ("system32", "tool", "x.exe")
    assert prefix.install_file(where, b"one", executable=True)
    target = prefix.windows_file(*where)
    assert target.read_bytes() == b"one"
    assert os.access(target, os.X_OK)

    before = (target.stat().st_ino, target.stat().st_mtime_ns)
    assert not prefix.install_file(where, b"one", executable=True)
    assert (target.stat().st_ino, target.stat().st_mtime_ns) == before

    assert prefix.install_file(where, b"two", executable=True)
    assert target.read_bytes() == b"two"


def test_a_file_that_is_not_executable_is_fixed_only_when_asked(
    tmp_path: Path,
) -> None:
    prefix = WinePrefix(tmp_path / "pfx")
    where = ("system32", "x.exe")
    prefix.install_file(where, b"x", executable=True)
    target = prefix.windows_file(*where)
    target.chmod(0o644)
    assert not prefix.install_file(where, b"x")  # nothing was asked of it
    assert prefix.install_file(where, b"x", executable=True)
    assert os.access(target, os.X_OK)


def test_a_symlink_is_replaced_and_what_it_pointed_to_is_not_touched(
    tmp_path: Path,
) -> None:
    prefix = WinePrefix(tmp_path / "pfx")
    protons_own = tmp_path / "builtin.exe"
    protons_own.write_text("builtin")
    where = ("system32", "x.exe")
    link = prefix.windows_file(*where)
    link.parent.mkdir(parents=True)
    link.symlink_to(protons_own)

    assert prefix.install_file(where, b"ours")
    assert not link.is_symlink()
    assert link.read_bytes() == b"ours"
    assert protons_own.read_text() == "builtin"


def test_wine_path_uses_the_z_drive() -> None:
    assert (
        wine_path(PurePosixPath("/home/me/.local/x.log"))  # type: ignore[arg-type]
        == "Z:\\home\\me\\.local\\x.log"
    )
    assert wine_path(PurePosixPath("/")) == "Z:\\"  # type: ignore[arg-type]


def test_the_stand_in_is_named_as_a_windows_program_names_it() -> None:
    assert POWERSHELL_STAND_IN_PATH == (
        r"C:\windows\system32\WindowsPowerShell\v1.0\powershell.exe"
    )


def test_a_steam_library_is_listed_in_the_prefix_with_its_z_path(
    tmp_path: Path,
) -> None:
    prefix = WinePrefix(tmp_path / "pfx")
    library = PurePosixPath("/mnt/storage/SteamLibrary")
    prefix.list_steam_library(library, ["433850"])  # type: ignore[arg-type]
    listing = (
        prefix.program_files / "Steam" / "steamapps" / "libraryfolders.vdf"
    )
    entry = vdf.loads(listing.read_text())["libraryfolders"]["0"]
    assert entry["path"] == "Z:\\mnt\\storage\\SteamLibrary"
    assert entry["apps"] == {"433850": "0"}


def test_the_library_list_can_be_read_the_way_rotk_launcher_reads_it(
    tmp_path: Path,
) -> None:
    """ROTK Launcher takes the `"path" "..."` pairs out of the file with a
    regular expression and unescapes the backslashes."""
    prefix = WinePrefix(tmp_path / "pfx")
    library = PurePosixPath("/mnt/storage/SteamLibrary")
    prefix.list_steam_library(library, ["433850"])  # type: ignore[arg-type]
    text = (
        prefix.program_files / "Steam" / "steamapps" / "libraryfolders.vdf"
    ).read_text()
    paths = [
        re.sub(r"\\(.)", r"\1", raw).strip()
        for raw in re.findall(
            r'"path"\s*"((?:\\.|[^"\\])*)"', text, re.IGNORECASE
        )
    ]
    assert paths == ["Z:\\mnt\\storage\\SteamLibrary"]


def test_an_unchanged_library_list_is_left_alone(tmp_path: Path) -> None:
    prefix = WinePrefix(tmp_path / "pfx")
    library = PurePosixPath("/lib")
    prefix.list_steam_library(library, ["1"])  # type: ignore[arg-type]
    listing = (
        prefix.program_files / "Steam" / "steamapps" / "libraryfolders.vdf"
    )
    before = listing.stat().st_mtime_ns
    prefix.list_steam_library(library, ["1"])  # type: ignore[arg-type]
    assert listing.stat().st_mtime_ns == before
    prefix.list_steam_library(library, ["1", "2"])  # type: ignore[arg-type]
    assert vdf.loads(listing.read_text())["libraryfolders"]["0"]["apps"] == {
        "1": "0",
        "2": "0",
    }
