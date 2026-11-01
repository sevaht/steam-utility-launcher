"""SVT-GE-Proton: GE-Proton plus the fixes ROTK Launcher and the game need
(https://github.com/sevaht/SVT-proton-ge-custom). What this checks is that a
launch uses it; `proton svt-ge` (proton_tools) installs it."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import proton_tools

if TYPE_CHECKING:
    from .steam import CompatibilityTool, CompatSession, Steam

logger = logging.getLogger(__name__)

SOURCE = proton_tools.SVT_GE_PROTON


def is_svt_ge_proton(tool: CompatibilityTool) -> bool:
    return SOURCE.owns(tool)


def ensure_game_uses(
    steam: Steam,
    game_id: str,
    *,
    game_name: str,
    override_flag: str,
    allow_any: bool,
) -> bool:
    """Whether to go on: the game's Proton is SVT-GE-Proton, or `allow_any` says
    to use whatever it is. Otherwise says why not (and how to fix it)."""
    tool = steam.game_tool(game_id)
    if tool is not None and is_svt_ge_proton(tool):
        warn_if_update_available(tool)
        return True
    issue = _problem(
        steam, game_id, game_name=game_name, override_flag=override_flag
    )
    if not allow_any:
        logger.error(issue)
        return False
    logger.warning(
        "%s is not using SVT-GE-Proton (%s was passed); the game may hang while"
        " starting.",
        game_name,
        override_flag,
    )
    return True


def _problem(
    steam: Steam, game_id: str, *, game_name: str, override_flag: str
) -> str:
    chosen = steam.game_compatibility_tool(game_id)
    current = chosen or "Steam's default"
    # Steam names a tool that isn't there (any more): the override can't help.
    missing = bool(chosen) and steam.game_tool(game_id) is None
    svt_names = [
        known.internal_name
        for known in steam.compatibility_tools
        if is_svt_ge_proton(known)
    ]
    svt_installed = bool(svt_names)
    selection = (
        f"{' or '.join(svt_names) if svt_installed else 'it'} for"
        f" {game_name} under Properties > Compatibility"
    )
    install = (
        "Install it with `steam-utility-launcher proton svt-ge`, restart Steam,"
        f" then choose {selection}."
    )
    if missing:
        detail = (
            f"{game_name} is set to {chosen} in Steam, but that isn't"
            f" installed. {f'Choose {selection}.' if svt_installed else install}"
        )
    elif svt_installed:
        detail = (
            f"{game_name} is set to {current}, not SVT-GE-Proton."
            f" Choose {selection}."
        )
    else:
        detail = f"SVT-GE-Proton isn't installed. {install}"
    override = (
        ""
        if missing
        else (
            f" To use {current} anyway (the game may hang while starting),"
            f" pass {override_flag}."
        )
    )
    return (
        "ROTK Launcher needs SVT-GE-Proton, a Proton build with fixes the game"
        f" needs to start reliably. {detail}{override}"
    )


def ensure_session_uses(
    steam: Steam,
    session: CompatSession,
    *,
    override_flag: str,
    allow_any: bool,
) -> bool:
    """Whether to go on, for a launch Steam is running with the tool in
    `session`: it is SVT-GE-Proton, or `allow_any` says to use whatever it is.
    Otherwise says why not (and how to fix it)."""
    tool = steam.tool_for_session(session)
    if tool is not None and is_svt_ge_proton(tool):
        # No update hint on this path: ensure_game_uses has it.
        return True
    current = (
        tool.internal_name if tool is not None else session.tool_directory.name
    )
    svt_names = [
        known.internal_name
        for known in steam.compatibility_tools
        if is_svt_ge_proton(known)
    ]
    detail = (
        f"It is set to run with {current}, not SVT-GE-Proton. Choose"
        f" {' or '.join(svt_names)} under the shortcut's Properties >"
        " Compatibility."
        if svt_names
        else f"It is set to run with {current}, and SVT-GE-Proton isn't installed."
    )
    issue = (
        "ROTK Launcher needs SVT-GE-Proton, a Proton build with fixes the game"
        f" needs to start reliably. {detail} To use {current} anyway (the"
        f" game may hang while starting), pass {override_flag}."
    )
    if not allow_any:
        logger.error(issue)
        return False
    logger.warning(
        "This is not running with SVT-GE-Proton (%s was passed): the game may"
        " hang while starting.",
        override_flag,
    )
    return True


def warn_if_update_available(tool: CompatibilityTool) -> None:
    """Says so, at most once a day, if a newer SVT-GE-Proton has been
    released. Never stops anything: a failed check is only logged."""
    proton_tools.warn_if_update_available(SOURCE, tool)
