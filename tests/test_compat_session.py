from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher.steam import (
    CompatibilityTool,
    CompatSession,
    Steam,
    SteamLocation,
)

if TYPE_CHECKING:
    from pathlib import Path

GAME_ID = "433850"


def _tool(root: Path, name: str = "SVT-GE-Proton-11") -> CompatibilityTool:
    install_path = root / "compatibilitytools.d" / name
    (install_path / "files" / "bin").mkdir(parents=True)
    wine = install_path / "files" / "bin" / "wine"
    wine.write_text("#!/bin/sh\n")
    wine.chmod(0o755)
    (install_path / "proton").write_text("#!/usr/bin/env python3\n")
    return CompatibilityTool(
        internal_name=name,
        manifest_vdf=install_path / "toolmanifest.vdf",
        install_path=install_path,
        display_name=name,
        binary_path=install_path / "proton",
        binary_argument_template=["%verb%"],
    )


def _steam(root: Path, *tools: CompatibilityTool) -> Steam:
    return Steam(
        location=SteamLocation(root=root, system_config_dirs=[]),
        compatibility_tools=list(tools),
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Steam separates the tool paths with a colon on Linux",
)
def test_the_session_is_read_from_what_steam_sets(tmp_path: Path) -> None:
    session = CompatSession.from_environment(
        {
            "STEAM_COMPAT_DATA_PATH": str(tmp_path / "compatdata" / "42"),
            "STEAM_COMPAT_TOOL_PATHS": f"{tmp_path}/tool:{tmp_path}/runtime",
        }
    )
    assert session == CompatSession(
        compat_data_path=tmp_path / "compatdata" / "42",
        tool_directory=tmp_path / "tool",
    )
    assert session is not None
    assert session.prefix == tmp_path / "compatdata" / "42" / "pfx"


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"STEAM_COMPAT_DATA_PATH": "/data"},
        {"STEAM_COMPAT_TOOL_PATHS": "/tool"},
        {"STEAM_COMPAT_DATA_PATH": "", "STEAM_COMPAT_TOOL_PATHS": "/tool"},
        {"STEAM_COMPAT_DATA_PATH": "/data", "STEAM_COMPAT_TOOL_PATHS": ""},
    ],
)
def test_without_what_steam_sets_there_is_no_session(
    environment: dict[str, str],
) -> None:
    assert CompatSession.from_environment(environment) is None


def test_the_tool_of_a_session_is_found_by_its_folder(tmp_path: Path) -> None:
    mine = _tool(tmp_path, "SVT-GE-Proton-11")
    other = _tool(tmp_path, "GE-Proton10-1")
    steam = _steam(tmp_path, other, mine)
    session = CompatSession(tmp_path / "data", mine.install_path)
    assert steam.tool_for_session(session) is mine
    missing = CompatSession(tmp_path / "data", tmp_path / "nowhere")
    assert steam.tool_for_session(missing) is None


def test_a_process_runs_in_the_prefix_and_tool_of_the_session(
    tmp_path: Path,
) -> None:
    tool = _tool(tmp_path)
    steam = _steam(tmp_path, tool)
    session = CompatSession(tmp_path / "compatdata" / "42", tool.install_path)
    process = steam.process_in_prefix(
        ["C:\\setup.exe", "/S"], game_id=GAME_ID, session=session
    )
    assert process.command_line == [
        str(tool.install_path / "files" / "bin" / "wine"),
        "C:\\setup.exe",
        "/S",
    ]
    assert process.env is not None
    # The session's prefix, not one made from the game's id...
    assert process.env["WINEPREFIX"] == str(session.prefix)
    assert process.env["STEAM_COMPAT_DATA_PATH"] == str(
        session.compat_data_path
    )
    # ...while the process still tells Steam it is the game
    assert process.env["SteamAppId"] == GAME_ID
    assert process.env["SteamGameId"] == GAME_ID
    assert not (tmp_path / "steamapps" / "compatdata" / GAME_ID).exists()


def test_a_session_whose_tool_is_not_installed_is_an_error(
    tmp_path: Path,
) -> None:
    steam = _steam(tmp_path, _tool(tmp_path))
    session = CompatSession(tmp_path / "data", tmp_path / "gone")
    with pytest.raises(RuntimeError, match="No compatibility tool at"):
        steam.process_in_prefix(
            ["C:\\setup.exe"], game_id=GAME_ID, session=session
        )


def test_steam_is_required_where_a_process_runs_in_a_prefix(
    tmp_path: Path,
) -> None:
    steam = _steam(tmp_path)
    assert Steam.require(steam) is steam
    with pytest.raises(AssertionError, match="steam context"):
        Steam.require(None)


def test_every_prefix_in_the_libraries_is_found_by_the_id_it_was_made_for(
    tmp_path: Path,
) -> None:
    compatdata = tmp_path / "steamapps" / "compatdata"
    for name in ("42", "3574495053"):
        (compatdata / name / "pfx").mkdir(parents=True)
    (compatdata / "7").mkdir()  # a game that never got a prefix
    steam = _steam(tmp_path)
    assert [p.compat_data_path.name for p in steam.prefixes()] == [
        "3574495053",
        "42",
    ]
    assert _steam(tmp_path / "nowhere").prefixes() == []
