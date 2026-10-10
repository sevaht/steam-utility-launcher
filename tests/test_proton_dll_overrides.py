from __future__ import annotations

from typing import TYPE_CHECKING

from steam_utility_launcher.steam import (
    CompatibilityTool,
    Steam,
    proton_dll_overrides,
)

if TYPE_CHECKING:
    from pathlib import Path


def _dll(prefix: Path, name: str, content: bytes) -> None:
    path = prefix / "drive_c" / "windows" / "system32" / f"{name}.dll"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_only_the_fixed_overrides_apply_to_a_prefix_without_dxvk(
    tmp_path: Path,
) -> None:
    _dll(tmp_path, "d3d11", b"MZ wine builtin stub")
    assert (
        proton_dll_overrides(tmp_path)
        == "beclient,beclient_x64=b,n;winebth.sys=d"
    )


def test_dxvk_dlls_in_the_prefix_are_made_to_load(tmp_path: Path) -> None:
    for name in ("d3d11", "dxgi"):
        _dll(tmp_path, name, b"..DxvkDevice..")
    _dll(tmp_path, "d3d9", b"MZ wine builtin")
    overrides = proton_dll_overrides(tmp_path).split(";")
    assert overrides[0] == "d3d11,d3d10core,dxgi=n"


def test_nvapi_is_overridden_when_it_is_installed(tmp_path: Path) -> None:
    _dll(tmp_path, "nvapi64", b"x")
    assert "nvapi64,nvofapi64,nvapi=n;nvcuda=b" in proton_dll_overrides(
        tmp_path
    )


def _proton(root: Path) -> CompatibilityTool:
    install = root / "Proton"
    (install / "files" / "bin").mkdir(parents=True)
    (install / "files" / "bin" / "wine").write_text("")
    return CompatibilityTool(
        internal_name="proton",
        manifest_vdf=install / "toolmanifest.vdf",
        install_path=install,
        display_name="Proton",
        binary_path=install / "proton",
        binary_argument_template=["%verb%"],
    )


def test_a_game_goes_through_the_proton_script(tmp_path: Path) -> None:
    tool = _proton(tmp_path)
    steam = Steam.__new__(Steam)
    runner, command = steam._proton_launch_command(
        tool=tool, command_line=["game.exe"], via_script=True
    )
    assert command == [str(tool.binary_path), "waitforexitandrun", "game.exe"]
    assert runner == [str(tmp_path / "Proton/files/bin/wine")]  # for `reg`


def test_utilities_still_use_the_bare_wine(tmp_path: Path) -> None:
    tool = _proton(tmp_path)
    steam = Steam.__new__(Steam)
    _, command = steam._proton_launch_command(
        tool=tool, command_line=["util.exe"]
    )
    assert command == [str(tmp_path / "Proton/files/bin/wine"), "util.exe"]
