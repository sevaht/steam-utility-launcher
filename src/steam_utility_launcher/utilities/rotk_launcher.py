from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from steam_utility_launcher import (
    game_watchdog,
    hwid_access,
    powershell_log,
    tpm_setup,
    update_check,
    vc_runtime,
)
from steam_utility_launcher.github_release_updater import (
    XDG_DATA_ROOT,
    ApplicationUpdater,
    GitHubRepository,
    Release,
    https_get,
)
from steam_utility_launcher.steam import Process, Steam
from steam_utility_launcher.wine_prefix_patches import ensure_wine_patches

logger = logging.getLogger(__name__)

# Z1 Battle Royale, formerly H1Z1; ROTK Launcher is run in its Proton prefix.
GAME_ID = "433850"
_ASSET_PATTERN = re.compile(r"ROTK-Launcher-[0-9]+(\.[0-9]+)*-x64\.exe")
_CHECKSUMS_ASSET_NAME = Path("SHA256SUMS.txt")
_CHECKSUM_LINE_PART_COUNT = 2


# Where the stand-in keeps the TPM-wrapped key blobs (see resources/tpm2.h).
# On the host, so a Proton prefix reset doesn't change the keys the server
# knows; the blobs only load on this machine's TPM.
_TPM_DIR_ENV_VAR = "SUL_TPM_DIR"
_TPM_ENV_VAR = "SUL_TPM"
# Opt-in (--tpm-endorsement): without it the stand-in sends no EK certificates
# and declines the credential activation.
_TPM_ENDORSEMENT_ENV_VAR = "SUL_TPM_ENDORSEMENT"


# Steam's overlay doesn't work with DXVK (which is what makes the game run
# well): its preloaded library stalls the game's start-up, and its Vulkan
# layer draws an FPS counter over the launcher's own window. So neither is
# passed on: the layer is switched off and the library is dropped from
# LD_PRELOAD (see _without_steam_overlay).
_STEAM_OVERLAY_OFF = {"DISABLE_VK_LAYER_VALVE_steam_overlay_1": "1"}
_OVERLAY_LIBRARY = "gameoverlayrenderer"

# The game's own patch library sometimes suspends its main thread while Wine is
# in the one-time window setup, and the game then hangs for good. Proton-ROTK
# (a Proton build with a small Wine change) does that setup at process start
# for the one program named here; other Proton builds ignore the setting.
_EAGER_DESKTOP = {"PROTON_ROTK_EAGER_DESKTOP": "H1Z1.exe"}

# Where the launcher keeps the game, and the FPS counter that stands in for the
# overlay's. DXVK reads dxvk.conf from the working directory of each process,
# so putting it with the game shows the counter in the game and not in the
# launcher. An existing file is never touched, so it can be edited (or its
# `dxvk.hud` emptied to turn the counter off).
_GAME_DIRECTORY = ("drive_c", "Games", "ROTK")
_DXVK_CONFIG_NAME = "dxvk.conf"
_DXVK_CONFIG = "dxvk.hud = fps\n"


def _ensure_fps_counter(prefix: Path) -> None:
    game_directory = prefix.joinpath(*_GAME_DIRECTORY)
    config = game_directory / _DXVK_CONFIG_NAME
    if not game_directory.is_dir() or config.exists():
        return
    try:
        config.write_text(_DXVK_CONFIG)
    except OSError as error:
        logger.warning("Could not set up the FPS counter: %s", error)
        return
    logger.info("Added an FPS counter for the game: %s", config)


def _without_steam_overlay(env: dict[str, str]) -> None:
    """Drops Steam's overlay library from LD_PRELOAD, keeping anything else."""
    preload = env.get("LD_PRELOAD")
    if preload is None:
        return
    kept = [
        entry
        for entry in re.split(r"[\s:]+", preload)
        if entry and _OVERLAY_LIBRARY not in entry
    ]
    if kept:
        env["LD_PRELOAD"] = ":".join(kept)
    else:
        del env["LD_PRELOAD"]


def _wait_for_game(child: subprocess.Popen[bytes], prefix: Path) -> int:
    """Waits for the launcher, stopping a game that hangs while starting."""
    watchdog = game_watchdog.StartupWatchdog(prefix)
    while True:
        try:
            return child.wait(timeout=game_watchdog.POLL_SECONDS)
        except subprocess.TimeoutExpired:
            watchdog.check()


def _tpm_directory() -> Path:
    directory = XDG_DATA_ROOT / "tpm"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


_STAND_IN_WINE_PATH = (
    r"C:\windows\system32\WindowsPowerShell\v1.0\powershell.exe"
)
_PREPARE_FLAG = "--sul-tpm-prepare"


def _tpm_environment(*, tpm: bool, endorsement: bool) -> dict[str, str]:
    """Nothing is set unless asked for, so the stand-in declines the TPM
    commands as a PC without a TPM would."""
    if not tpm:
        return {}
    environment = {
        _TPM_ENV_VAR: "1",
        _TPM_DIR_ENV_VAR: powershell_log.wine_path(_tpm_directory()),
    }
    if endorsement:
        environment[_TPM_ENDORSEMENT_ENV_VAR] = "1"
    return environment


def _prepare_tpm(
    *, steam: Steam | None, prefix: Path, endorsement: bool
) -> bool:
    """Has the stand-in create the TPM keys once and, for AMD TPMs with
    `--tpm-endorsement`, saves the endorsement key certificate chain AMD
    publishes. TPM attestation was asked for, so False (with the reason
    logged) means the TPM doesn't work and the launch must not go on."""
    directory = _tpm_directory()
    try:
        if tpm_setup.needs_preparing(directory):
            code = (
                _run_in_context(
                    steam,
                    prefix,
                    [_STAND_IN_WINE_PATH, _PREPARE_FLAG],
                    disable_powershell=False,
                    extra_env={
                        _TPM_DIR_ENV_VAR: powershell_log.wine_path(directory)
                    },
                )
                .start()
                .wait()
            )
            if code != 0:
                logger.error(
                    "Refusing to start with --tpm: the TPM could not be set"
                    " up (exit status %d; the reason is printed just above)."
                    " Run without --tpm to behave like a PC without a TPM.",
                    code,
                )
                return False
        if endorsement:
            tpm_setup.ensure_certificates(directory)
    except OSError:
        logger.exception("Refusing to start with --tpm")
        return False
    return True


def _require_steam(steam: Steam | None) -> Steam:
    if not steam:
        msg = "steam context must be provided on linux!"
        raise AssertionError(msg)
    return steam


def _resolve_app_path(steam: Steam | None) -> tuple[Path | None, Path]:
    if sys.platform.startswith("linux"):
        prefix = _require_steam(steam).game_wine_prefix(game_id=GAME_ID)
        program_files = prefix / "drive_c" / "Program Files"
        return prefix, program_files / "ROTK Launcher" / "ROTK Launcher.exe"
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    return None, program_files / "ROTK Launcher" / "ROTK Launcher.exe"


def _run_in_context(  # noqa: PLR0913
    steam: Steam | None,
    prefix: Path | None,
    command_line: list[str],
    *,
    disable_powershell: bool = True,
    extra_env: dict[str, str] | None = None,
    as_game: bool = False,
) -> Process:
    if prefix is not None:
        process = _require_steam(steam).process_in_prefix(
            command_line, game_id=GAME_ID, via_proton_script=as_game
        )
        if extra_env and process.env is not None:
            process.env.update(extra_env)
        # Wine's powershell.exe is an unimplemented stub: it never runs the
        # script it's given and always exits 0. ROTK Launcher's NSIS
        # installer (and its own in-app auto-updater, which invokes the
        # same installer) uses a PowerShell one-liner to check whether an
        # old instance of itself is running before installing, treating
        # exit 0 as "yes, it's running" - since the stub always exits 0, it
        # would otherwise conclude the app is permanently running and get
        # stuck forever on a "cannot be closed" dialog. Disabling
        # powershell.exe for just this process makes it fail to launch
        # instead, which the installer already handles: its bundled
        # allowOnlyOneInstallerInstance.nsh template falls back to
        # tasklist/findstr/taskkill whenever PowerShell isn't available.
        # Nothing is written to the prefix.
        if disable_powershell and process.env is not None:
            existing = process.env.get("WINEDLLOVERRIDES", "")
            override = "powershell.exe=d"
            process.env["WINEDLLOVERRIDES"] = (
                f"{existing};{override}" if existing else override
            )
        return process
    return Process(command_line)


def _fetch_release_and_asset() -> tuple[Release, Path]:
    repository = GitHubRepository("rotk-launcher", organization="h1z1rotk")
    release = repository.get_release()
    if not release:
        msg = f"No release found for {repository}"
        raise RuntimeError(msg)
    asset_name = release.single_matching_asset(_ASSET_PATTERN)
    if not asset_name:
        msg = (
            f"Release {release.tag} has no installer matching"
            f" {_ASSET_PATTERN.pattern!r}"
        )
        raise RuntimeError(msg)
    return release, asset_name


def _verify_checksum(
    content: bytes, *, checksums: bytes, asset_name: str
) -> None:
    expected = ""
    for line in checksums.decode().splitlines():
        parts = line.split()
        if (
            len(parts) == _CHECKSUM_LINE_PART_COUNT
            and parts[1].lstrip("*") == asset_name
        ):
            expected = parts[0]
            break
    if not expected:
        msg = f"No checksum entry found for: {asset_name}"
        raise RuntimeError(msg)
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        msg = (
            f"Checksum mismatch for {asset_name}:"
            f" expected {expected}, got {actual}"
        )
        raise RuntimeError(msg)


def _download_installer(
    release: Release, asset_name: Path, destination: Path
) -> None:
    _, content = https_get(release.asset_urls[asset_name])
    if _CHECKSUMS_ASSET_NAME in release.asset_urls:
        _, checksums = https_get(release.asset_urls[_CHECKSUMS_ASSET_NAME])
        _verify_checksum(
            content, checksums=checksums, asset_name=asset_name.name
        )
    else:
        logger.warning(
            "Release %s has no %s; skipping checksum verification.",
            release.tag,
            _CHECKSUMS_ASSET_NAME,
        )
    destination.write_bytes(content)


def _install_if_needed(
    *, steam: Steam | None, prefix: Path | None, app_path: Path, force: bool
) -> None:
    install_directory = XDG_DATA_ROOT / "ROTK-Launcher"
    install_directory.mkdir(parents=True, exist_ok=True)
    tag_file = install_directory / ApplicationUpdater.TAG_FILE_NAME
    installed_tag = tag_file.read_text().strip() if tag_file.exists() else ""
    already_installed = app_path.exists()

    last_checked_file = install_directory / update_check.MARKER_FILE_NAME
    if (
        already_installed
        and not force
        and update_check.checked_recently(last_checked_file)
    ):
        return

    release, asset_name = _fetch_release_and_asset()
    update_check.record_checked_now(last_checked_file)
    update_available = already_installed and installed_tag != release.tag

    if update_available:
        logger.warning(
            'ROTK Launcher update available ("%s" -> "%s"). ROTK Launcher'
            " updates itself automatically once running; pass --force to"
            " this command to install it immediately instead.",
            installed_tag or "an untracked version",
            release.tag,
        )

    if already_installed and not force:
        return

    logger.info('Installing ROTK Launcher "%s"...', release.tag)
    with TemporaryDirectory() as staging_dir:
        installer_path = Path(staging_dir) / asset_name.name
        _download_installer(release, asset_name, installer_path)
        installer_process = _run_in_context(
            steam, prefix, [str(installer_path), "/S"]
        )
        return_code = installer_process.start().wait()
    if return_code != 0:
        msg = f"ROTK Launcher installer exited with status {return_code}"
        raise RuntimeError(msg)
    if not app_path.exists():
        msg = f"ROTK Launcher was not found after installing: {app_path}"
        raise RuntimeError(msg)
    tag_file.write_text(release.tag)
    logger.info('Installed ROTK Launcher "%s".', release.tag)


def launch(
    *,
    steam: Steam | None = None,
    force: bool = False,
    tpm: bool = False,
    tpm_endorsement: bool = False,
) -> int:
    tpm = tpm or tpm_endorsement
    prefix, app_path = _resolve_app_path(steam)
    if prefix is not None:
        # First, before anything is downloaded, installed or started: if the
        # hardware check can't be answered completely, don't go on at all.
        problem = hwid_access.access_problem()
        if problem is not None:
            logger.error(problem)
            return 1
        # TPM attestation is opt-in; once asked for it has to work.
        tpm_problem = hwid_access.tpm_problem() if tpm else None
        if tpm_problem is not None:
            logger.error(tpm_problem)
            return 1
    ps_log = powershell_log.path() if prefix is not None else None
    if ps_log is not None:
        print(
            f"PowerShell commands the app runs are logged to: {ps_log}",
            file=sys.stderr,
        )
    _install_if_needed(
        steam=steam, prefix=prefix, app_path=app_path, force=force
    )
    if prefix is None or ps_log is None:
        return _run_in_context(steam, prefix, [str(app_path)]).start().wait()
    # ROTK's anti-cheat needs a Visual C++ runtime newer than the 2016 one that
    # Steam's redistributable installer leaves in the game's prefix.
    vc_runtime.ensure_current(
        steam=_require_steam(steam), prefix=prefix, game_id=GAME_ID
    )
    # The app itself needs a working answer to its hardware fingerprint query
    # (PowerShell/WMI), which Wine's stub can't give; see wine_prefix_patches.
    # The stand-in is left in the prefix for good (see there), so this only
    # has to confirm on each launch that it is still present and current. It
    # also answers the installer checks the app's in-app updater makes, so
    # the DLL override that disables PowerShell isn't used here.
    ensure_wine_patches(prefix)
    _ensure_fps_counter(prefix)
    if tpm and not _prepare_tpm(
        steam=steam, prefix=prefix, endorsement=tpm_endorsement
    ):
        return 1
    logged_before = powershell_log.size(ps_log)
    process = _run_in_context(
        steam,
        prefix,
        [str(app_path)],
        disable_powershell=False,
        # ROTK Launcher is the game itself (it starts H1Z1.exe), so it runs
        # exactly as Steam would run the game: through Proton's own script.
        as_game=True,
        extra_env={
            powershell_log.ENV_VAR: powershell_log.wine_path(ps_log),
            **_STEAM_OVERLAY_OFF,
            **_EAGER_DESKTOP,
            **_tpm_environment(tpm=tpm, endorsement=tpm_endorsement),
        },
    )
    if process.env is not None:
        _without_steam_overlay(process.env)
    return_code = _wait_for_game(process.start(), prefix)
    # The stand-in refuses (and logs) any hardware-ID command it doesn't
    # recognize; say so here too, where the terminal output is.
    powershell_log.report_unrecognized(ps_log, logged_before)
    return return_code
