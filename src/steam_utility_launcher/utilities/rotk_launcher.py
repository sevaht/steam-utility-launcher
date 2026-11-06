from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from steam_utility_launcher import (
    asar,
    hwid_access,
    powershell_log,
    svt_ge_proton,
    tpm_setup,
    update_check,
    vc_runtime,
)
from steam_utility_launcher.games import Z1_BATTLE_ROYALE_ID
from steam_utility_launcher.github_release_updater import (
    XDG_DATA_ROOT,
    ApplicationUpdater,
    GitHubRepository,
    Release,
    https_get,
)
from steam_utility_launcher.programs import NativeWindows, WindowsFileSystem
from steam_utility_launcher.steam import (
    CompatSession,
    Process,
    Steam,
    SteamClient,
    SteamLocation,
)
from steam_utility_launcher.steam_shortcuts import (
    LaunchStyle,
    ShortcutManager,
    ShortcutSpec,
)
from steam_utility_launcher.wine_prefix import wine_path
from steam_utility_launcher.wine_prefix_patches import (
    POWERSHELL_STAND_IN_PATH,
    ensure_wine_patches,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from steam_utility_launcher.host import Host
    from steam_utility_launcher.wine_prefix import WinePrefix

logger = logging.getLogger(__name__)

# Z1 Battle Royale, formerly H1Z1: the game ROTK Launcher starts. Its files are
# what ROTK Launcher copies (from the Steam library the game is in), and what
# the processes tell Steam they are, so the game finds the Steam it is
# running under. ROTK Launcher itself has a prefix of its own: the one Steam
# makes for the shortcut that runs this program (see launch()).
GAME_ID = Z1_BATTLE_ROYALE_ID
# The name of the non-Steam game this program adds to Steam's library.
SHORTCUT_NAME = "Z1 Battle Royale (ROTK)"
# The launcher's own mark, in its app.asar: what the Steam shortcut shows. It
# is copied out of the installed launcher (nothing of it is shipped here) to a
# fixed place, so the shortcut can name it before the launcher is installed.
_ICON_IN_ASAR = "dist/branding/rotk-mark.png"
# Waives the requirement that the Proton ROTK Launcher runs with be SVT-GE-Proton.
ALLOW_ANY_PROTON_FLAG = "--allow-any-proton"
# What Steam's command for the shortcut has before the program to run
# (<proton> <verb> <program> <arguments>): Proton's script and its verb.
_PROTON_COMMAND_LENGTH = 2
# The program that has Proton make the prefix and does nothing else.
_PREFIX_MAKER = ["cmd", "/c", "exit"]
_ASSET_PATTERN = re.compile(r"ROTK-Launcher-[0-9]+(\.[0-9]+)*-x64\.exe")
_CHECKSUMS_ASSET_NAME = Path("SHA256SUMS.txt")
# Where the installer puts the launcher, below the Program Files folder.
APP_RELATIVE_PATH = Path("ROTK Launcher") / "ROTK Launcher.exe"
_CHECKSUM_LINE_PART_COUNT = 2


# Where the stand-in keeps the TPM-wrapped key blobs (see resources/tpm2.h).
# On the host, so a Proton prefix reset doesn't change the keys the server
# knows; the blobs only load on this machine's TPM.
_TPM_DIR_ENV_VAR = "SUL_TPM_DIR"
_TPM_ENV_VAR = "SUL_TPM"
# Opt-in (--tpm-endorsement): without it the stand-in sends no EK certificates
# and declines the credential activation.
_TPM_ENDORSEMENT_ENV_VAR = "SUL_TPM_ENDORSEMENT"


# Steam's overlay is left as Steam set it up for the shortcut: enable it
# there (Properties > Overlay) and it works in the launcher and the game;
# disable it and nothing is passed on. --no-steam-overlay takes it away
# regardless, for a start-up stall that has been blamed on its preloaded
# library: the layer is switched off and the library is dropped from
# LD_PRELOAD (see _without_steam_overlay).
_STEAM_OVERLAY_OFF = {"DISABLE_VK_LAYER_VALVE_steam_overlay_1": "1"}
_OVERLAY_LIBRARY = "gameoverlayrenderer"

# ROTK's patch library (rotkc.dll, loaded into the game by the ROTK launcher's
# game files) sometimes suspends the game's main thread while Wine is in the
# one-time window setup, and the game then hangs for good. SVT-GE-Proton (a
# Proton build with a small Wine change) does that setup at process start for
# the program(s) named here; other Proton builds ignore the setting.
_GAME_EXECUTABLE = "H1Z1.exe"
_EAGER_DESKTOP = {"PROTON_EAGER_DESKTOP": _GAME_EXECUTABLE}

# Settings for DXVK in the game are passed in DXVK_CONFIG, in a section for the
# game's executable: every DXVK process gets the variable (the game inherits
# the launcher's environment), but DXVK only applies a section to the program
# it names, so the launcher's own window is left alone.
_DXVK_CONFIG_ENV_VAR = "DXVK_CONFIG"

# DXVK's own overlay stands in for Steam's (see above); it is only shown when
# asked for with --hud or --default-hud. The value is what DXVK_HUD takes: a
# comma-separated list of items (fps, gpuload, frametimes, memory, ...) and of
# options written name=value (scale=1.5, opacity=0.8). (A DXVK_HUD in the
# environment would show it in every window, and takes precedence over this.)
# DXVK splits DXVK_CONFIG at ';', and ends a value at whitespace or a quote.
_HUD_FORBIDDEN = re.compile(r'[\s;"\[\]]')
# The overlay this tool's author prefers, for --default-hud.
DEFAULT_HUD = "fps,gpuload,scale=0.8,opacity=1.0"


def hud_value(text: str) -> str:
    """Checks an overlay setting for --hud, which DXVK_CONFIG can't carry if
    it has a space, quote, bracket or ';' in it."""
    if not text or _HUD_FORBIDDEN.search(text):
        msg = (
            "expected DXVK_HUD items without spaces, quotes, brackets or"
            f" ';', such as {DEFAULT_HUD},scale=1.5,opacity=0.8"
        )
        raise ValueError(msg)
    return text


def max_fps_value(text: str) -> int:
    """Checks a frame rate limit for --max-fps: DXVK's dxgi.maxFrameRate also
    takes 0 (no limit) and negative numbers (special modes for its own game
    profiles), which have no use here."""
    try:
        frames_per_second = int(text)
    except ValueError:
        frames_per_second = 0
    if frames_per_second < 1:
        msg = "expected a whole number of frames per second, such as 144"
        raise ValueError(msg)
    return frames_per_second


# DXGI refuses a sync interval above 4, so DXVK does too.
_VSYNC_MAXIMUM = 4


def vsync_value(text: str) -> int:
    """Checks a sync interval for --vsync: the number of monitor refreshes
    between frames, 0 being no vsync at all."""
    try:
        interval = int(text)
    except ValueError:
        interval = -1
    if not 0 <= interval <= _VSYNC_MAXIMUM:
        msg = (
            f"expected 0 (vsync off) to {_VSYNC_MAXIMUM}: the number of"
            " monitor refreshes between frames, 1 being every refresh"
        )
        raise ValueError(msg)
    return interval


def _game_config(
    *, hud: str | None, max_fps: int | None, vsync: int | None, existing: str
) -> str:
    """The DXVK_CONFIG value that applies these settings in the game only,
    after whatever DXVK_CONFIG the environment already had."""
    options = []
    if hud:
        options.append(f"dxvk.hud={hud}")
    if max_fps is not None:
        options.append(f"dxgi.maxFrameRate={max_fps}")
    if vsync is not None:
        options.append(f"dxgi.syncInterval={vsync}")
    if not options:
        return existing
    section = ";".join([f"[{_GAME_EXECUTABLE}]", *options])
    return f"{existing};{section}" if existing else section


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


def _tpm_directory() -> Path:
    directory = XDG_DATA_ROOT / "tpm"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


_PREPARE_FLAG = "--sul-tpm-prepare"


def _tpm_environment(*, tpm: bool, endorsement: bool) -> dict[str, str]:
    """Nothing is set unless asked for, so the stand-in declines the TPM
    commands as a PC without a TPM would."""
    if not tpm:
        return {}
    environment = {
        _TPM_ENV_VAR: "1",
        _TPM_DIR_ENV_VAR: wine_path(_tpm_directory()),
    }
    if endorsement:
        environment[_TPM_ENDORSEMENT_ENV_VAR] = "1"
    return environment


@dataclass(frozen=True, kw_only=True)
class LaunchOptions:
    """What `rotk-launcher` was asked for on the command line."""

    force: bool = False
    tpm: bool = False
    tpm_endorsement: bool = False
    allow_any_proton: bool = False
    hud: str | None = None
    max_fps: int | None = None
    vsync: int | None = None
    no_steam_overlay: bool = False
    # What goes in the launch options of the shortcut that is added to Steam
    shortcut_options: list[str] = field(default_factory=list)
    assume_yes: bool = False
    start_shortcut: bool = True
    # Steam started this program as the shortcut (on Windows, where nothing
    # else says so).
    run_from_steam: bool = False

    @property
    def uses_tpm(self) -> bool:
        """The endorsement extras need the TPM itself, so they imply it."""
        return self.tpm or self.tpm_endorsement

    def game_environment(self, ps_log: Path) -> dict[str, str]:
        """What the game is started with besides Steam's own environment."""
        environment = {
            powershell_log.ENV_VAR: wine_path(ps_log),
            **(_STEAM_OVERLAY_OFF if self.no_steam_overlay else {}),
            **_EAGER_DESKTOP,
        }
        if self.hud or self.max_fps is not None or self.vsync is not None:
            environment[_DXVK_CONFIG_ENV_VAR] = _game_config(
                hud=self.hud,
                max_fps=self.max_fps,
                vsync=self.vsync,
                existing=os.environ.get(_DXVK_CONFIG_ENV_VAR, ""),
            )
        environment.update(
            _tpm_environment(
                tpm=self.uses_tpm, endorsement=self.tpm_endorsement
            )
        )
        return environment


@dataclass(frozen=True)
class LauncherInstaller:
    """Installs ROTK Launcher (and keeps it current) with `run`, which starts a
    command wherever the launcher is to be installed: in the shortcut's prefix
    on Linux, directly on Windows."""

    app_path: Path
    run: Callable[[list[str]], Process]

    def ensure(self, *, force: bool) -> None:
        install_directory = XDG_DATA_ROOT / "ROTK-Launcher"
        install_directory.mkdir(parents=True, exist_ok=True)
        tag_file = install_directory / ApplicationUpdater.TAG_FILE_NAME
        installed_tag = (
            tag_file.read_text().strip() if tag_file.exists() else ""
        )
        already_installed = self.app_path.exists()

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
                'ROTK Launcher update available ("%s" -> "%s"). ROTK'
                " Launcher updates itself automatically once running; pass"
                " --force to this command to install it immediately instead.",
                installed_tag or "an untracked version",
                release.tag,
            )

        if already_installed and not force:
            return

        logger.info('Installing ROTK Launcher "%s"...', release.tag)
        with TemporaryDirectory() as staging_dir:
            installer_path = Path(staging_dir) / asset_name.name
            _download_installer(release, asset_name, installer_path)
            return_code = self.run([str(installer_path), "/S"]).start().wait()
        if return_code != 0:
            msg = f"ROTK Launcher installer exited with status {return_code}"
            raise RuntimeError(msg)
        if not self.app_path.exists():
            msg = (
                f"ROTK Launcher was not found after installing:"
                f" {self.app_path}"
            )
            raise RuntimeError(msg)
        tag_file.write_text(release.tag)
        logger.info('Installed ROTK Launcher "%s".', release.tag)


@dataclass(frozen=True)
class ShortcutLaunch:
    """A launch of ROTK Launcher as the Steam shortcut that runs this program
    (`steam-utility-launcher rotk-launcher -- %command%`): Steam makes the
    prefix and builds the command that runs Proton in its runtime container
    (`steam_command`), and everything here prepares that prefix, then has
    Steam's command run ROTK Launcher in place of the shortcut's target.

    The prefix is the session's, and only the session's: it is where every
    operation goes, whatever id the shortcut has (it gets a new one, and a new
    prefix, if it is added again). Z1 Battle Royale's own prefix is never
    touched; its id is only what the game tells Steam it is.
    """

    steam: Steam
    session: CompatSession
    steam_command: list[str]

    @property
    def prefix(self) -> WinePrefix:
        return self.session.wine_prefix

    @property
    def app_path(self) -> Path:
        return self.prefix.program_files / APP_RELATIVE_PATH

    def checks_pass(self, options: LaunchOptions) -> bool:
        """Whether the launch may go on, before anything is downloaded,
        installed or started. Says why not if it may not."""
        # The tool has to be SVT-GE-Proton, which has the Wine fixes the game
        # needs to start reliably, unless that is waived.
        if not svt_ge_proton.ensure_session_uses(
            self.steam,
            self.session,
            override_flag=ALLOW_ANY_PROTON_FLAG,
            allow_any=options.allow_any_proton,
        ):
            return False
        # If the hardware check can't be answered completely, don't go on.
        problem = hwid_access.access_problem()
        # TPM attestation is opt-in; once asked for it has to work.
        problem = problem or (
            hwid_access.tpm_problem() if options.uses_tpm else None
        )
        if problem is not None:
            logger.error(problem)
            return False
        return True

    def command_with(self, program: list[str]) -> list[str]:
        """Steam's command for the shortcut (everything the launch option
        `%command%` stands for: Steam's own wrappers, the runtime's entry
        point, Proton's script and its verb, and the program with its
        arguments), with `program` to run instead of the shortcut's target.
        Everything before the program is Steam's, so ROTK Launcher is started
        exactly as Steam would start the game, in the runtime's container."""
        tool_directory = self.session.tool_directory.resolve()
        for index, argument in enumerate(self.steam_command):
            path = Path(argument)
            if (
                path.name == "proton"
                and path.parent.resolve() == tool_directory
            ):
                return [
                    *self.steam_command[: index + _PROTON_COMMAND_LENGTH],
                    *program,
                ]
        msg = (
            f"Steam's command doesn't run Proton from {tool_directory}:"
            f" {self.steam_command}"
        )
        raise RuntimeError(msg)

    def make_prefix(self) -> bool:
        """Has Proton make the shortcut's prefix, if it is not made yet, by
        running Steam's command for a program that does nothing; so the prefix
        is made by the Proton the game runs with, in the container, as Steam
        makes one."""
        if self.prefix.is_made():
            return True
        logger.info("Making the prefix with Proton: %s", self.prefix.path)
        code = (
            Process(
                command_line=self.command_with(_PREFIX_MAKER),
                env=os.environ.copy(),
            )
            .start()
            .wait()
        )
        if code != 0 or not self.prefix.is_made():
            logger.error(
                "Proton did not make the prefix %s (exit status %d; the"
                " reason is above).",
                self.prefix.path,
                code,
            )
            return False
        return True

    def publish_game_library(self) -> None:
        """Has a Steam folder in the prefix list the library Z1 Battle Royale
        is in, so ROTK Launcher (which looks for Steam in the registry and in
        the Program Files folders) finds the game's files by itself. The
        library is named by its Z: path, which the container shows (see
        `_mount_game_library`). It is in `Program Files`, not where Proton
        makes its own Steam folder: Proton rewrites that one's library list on
        every start, and drops the S: drive of a shortcut."""
        try:
            self.prefix.list_steam_library(
                self.steam.game_library_path(game_id=GAME_ID), [GAME_ID]
            )
        except OSError:
            logger.warning(
                "Could not list the Z1 Battle Royale library in the prefix;"
                " pick the game's folder in ROTK Launcher yourself.",
                exc_info=True,
            )

    def _mount_game_library(self, env: dict[str, str]) -> None:
        """Has the container show the Steam library the game is in (Steam only
        names its own), as ROTK Launcher is to copy the game's files."""
        library = str(self.steam.game_library_path(game_id=GAME_ID))
        mounts = [
            m for m in env.get("STEAM_COMPAT_MOUNTS", "").split(":") if m
        ]
        if library not in mounts:
            env["STEAM_COMPAT_MOUNTS"] = ":".join([*mounts, library])

    def game_process(self, *, extra_env: dict[str, str]) -> Process:
        """ROTK Launcher as the game: Steam's own command, run with the
        environment the program needs. It is the shortcut's own app as far as
        Steam is concerned (Steam's ids are left as it gave them)."""
        env = os.environ.copy()
        self._mount_game_library(env)
        env.update(extra_env)
        return Process(
            command_line=self.command_with([str(self.app_path)]), env=env
        )

    def process(
        self,
        command_line: list[str],
        *,
        disable_powershell: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> Process:
        """A process that needs the prefix (an installer, the TPM set-up) and
        not the game's complete environment: it runs with Proton's bare wine,
        in the session's prefix and in no other."""
        process = self.steam.process_in_prefix(
            command_line, game_id=GAME_ID, session=self.session
        )
        env = process.env
        if env is None or env.get("WINEPREFIX") != str(self.prefix.path):
            msg = (
                f"Not running {command_line[0]} in the prefix of the launch"
                f" ({self.prefix.path})."
            )
            raise RuntimeError(msg)
        # Not Z1's ids, which `process_in_prefix` gives a process: it is the
        # shortcut's own app, so it carries the ids Steam gave this program.
        for name in ("SteamAppId", "SteamGameId"):
            if name in os.environ:
                env[name] = os.environ[name]
            else:
                env.pop(name, None)
        if extra_env:
            env.update(extra_env)
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
        if disable_powershell:
            existing = env.get("WINEDLLOVERRIDES", "")
            override = "powershell.exe=d"
            env["WINEDLLOVERRIDES"] = (
                f"{existing};{override}" if existing else override
            )
        return process

    def install_launcher(self, *, force: bool) -> None:
        LauncherInstaller(self.app_path, self.process).ensure(force=force)

    def ensure_runtime(self) -> None:
        """ROTK's anti-cheat needs the real Visual C++ runtime, which Wine's
        builtin stand-ins (all a new prefix has) and the 2016 one that Steam's
        redistributable installer leaves in a game's prefix are not."""
        vc_runtime.ensure_current(
            self.prefix,
            run=lambda command: self.process(
                command, disable_powershell=False
            ),
        )

    def ensure_patches(self) -> None:
        """The app itself needs a working answer to its hardware fingerprint
        query (PowerShell/WMI), which Wine's stub can't give; see
        wine_prefix_patches. The stand-in is left in the prefix for good (see
        there), so this only has to confirm on each launch that it is still
        present and current. It also answers the installer checks the app's
        in-app updater makes, so the DLL override that disables PowerShell
        isn't used here."""
        ensure_wine_patches(self.prefix)

    def prepare_tpm(self, *, endorsement: bool) -> bool:
        """Has the stand-in create the TPM keys once and, for AMD TPMs with
        `--tpm-endorsement`, saves the endorsement key certificate chain AMD
        publishes. TPM attestation was asked for, so False (with the reason
        logged) means the TPM doesn't work and the launch must not go on."""
        directory = _tpm_directory()
        try:
            if tpm_setup.needs_preparing(directory):
                code = (
                    self.process(
                        [POWERSHELL_STAND_IN_PATH, _PREPARE_FLAG],
                        disable_powershell=False,
                        extra_env={_TPM_DIR_ENV_VAR: wine_path(directory)},
                    )
                    .start()
                    .wait()
                )
                if code != 0:
                    logger.error(
                        "Refusing to start with --tpm: the TPM could not be"
                        " set up (exit status %d; the reason is printed just"
                        " above). Run without --tpm to behave like a PC"
                        " without a TPM.",
                        code,
                    )
                    return False
            if endorsement:
                tpm_setup.ensure_certificates(directory)
        except OSError:
            logger.exception("Refusing to start with --tpm")
            return False
        return True

    def run(self, options: LaunchOptions) -> int:
        """Prepares the prefix and runs ROTK Launcher in it."""
        if not self.checks_pass(options):
            return 1
        ps_log = powershell_log.path()
        print(
            f"PowerShell commands the app runs are logged to: {ps_log}",
            file=sys.stderr,
        )
        if not self.make_prefix():
            return 1
        self.publish_game_library()
        self.install_launcher(force=options.force)
        _save_icon(self.app_path)
        self.ensure_runtime()
        self.ensure_patches()
        if options.uses_tpm and not self.prepare_tpm(
            endorsement=options.tpm_endorsement
        ):
            return 1
        logged_before = powershell_log.size(ps_log)
        # ROTK Launcher is the game itself (it starts H1Z1.exe), so it runs
        # exactly as Steam would run the game: Steam's own command, in the
        # runtime's container, with the environment it needs added.
        process = self.game_process(extra_env=options.game_environment(ps_log))
        if options.no_steam_overlay and process.env is not None:
            _without_steam_overlay(process.env)
        return_code = process.start().wait()
        # The stand-in refuses (and logs) any hardware-ID command it doesn't
        # recognize; say so here too, where the terminal output is.
        powershell_log.report_unrecognized(ps_log, logged_before)
        return return_code


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


def _launcher_command() -> list[str]:
    """How to run this program again, as a command."""
    script = Path(sys.argv[0])
    if script.name == "__main__.py" or not script.is_file():
        return [sys.executable, "-m", "steam_utility_launcher"]
    return [str(script.absolute())]


def icon_path() -> Path:
    return XDG_DATA_ROOT / "ROTK-Launcher" / "icon.png"


def _save_icon(app_path: Path) -> bool:
    """Copies the launcher's mark out of the installed launcher to `icon_path`.
    Best effort: whether it was saved. Never an error."""
    archive = app_path.parent / "resources" / "app.asar"
    try:
        content = asar.read_file(archive, _ICON_IN_ASAR)
        destination = icon_path()
        if destination.exists() and destination.read_bytes() == content:
            return True
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_suffix(".png.part")
        staging.write_bytes(content)
        staging.replace(destination)
    except (OSError, asar.AsarError):
        logger.debug("Could not save the launcher's icon", exc_info=True)
        return False
    return True


def _save_icon_from_any_prefix(steam: Steam) -> None:
    """Saves the icon from a ROTK Launcher installed in one of Steam's
    prefixes, if there is one (the wrapper does it itself after installing).
    Whichever prefix the shortcut has, as it is looked for in all of them."""
    for prefix in steam.prefixes():
        candidate = prefix.program_files / APP_RELATIVE_PATH
        if candidate.is_file() and _save_icon(candidate):
            return


def _add_to_steam(
    steam: Steam | None, host: Host, options: LaunchOptions
) -> int:
    """Adds ROTK Launcher to Steam as a non-Steam game that runs this program
    with the shortcut options (see launch), or changes the options of the one
    that is there, and has Steam start it."""
    location = (
        steam.location
        if steam is not None
        else SteamLocation.from_detection(host)
    )
    if steam is not None:
        _save_icon_from_any_prefix(steam)
    manager = ShortcutManager(location, SteamClient(location, host))
    manager.ensure(
        ShortcutSpec(
            name=SHORTCUT_NAME,
            program=_launcher_command(),
            mode="rotk-launcher",
            options=options.shortcut_options,
            style=LaunchStyle.for_host(host, steam),
            icon=icon_path(),
            # The banner Steam shows for Z1 Battle Royale (if it has ever
            # shown it)
            artwork=location.root / "appcache" / "librarycache" / GAME_ID,
        ),
        assume_yes=options.assume_yes,
        launch=options.start_shortcut,
    )
    return 0


@dataclass(frozen=True)
class WindowsLaunch:
    """A launch of ROTK Launcher as the Steam shortcut that runs this program
    on Windows (`steam-utility-launcher rotk-launcher --run-from-steam`):
    nothing is wrapped, so ROTK Launcher is installed if needed and run."""

    filesystem: WindowsFileSystem

    @property
    def app_path(self) -> Path:
        return self.filesystem.program_files / APP_RELATIVE_PATH

    def run(self, options: LaunchOptions) -> int:
        LauncherInstaller(self.app_path, Process).ensure(force=options.force)
        return Process([str(self.app_path)]).start().wait()


class RotkLauncher:
    """Installs ROTK Launcher if needed and runs it, as the Steam shortcut this
    program adds for it.

    Run on its own, it adds that shortcut to Steam, with
    `options.shortcut_options` in its launch options, or updates the options
    of the one that is there, then has Steam start it (unless
    `options.start_shortcut` is off). Steam then runs this program again, as
    the shortcut:

    - On Linux it is a wrapper in the shortcut's launch options
      (`steam-utility-launcher rotk-launcher -- %command%`) for a shortcut
      that has a compatibility tool: Steam makes the prefix and builds the
      command that runs Proton in its runtime container (`steam_command`), and
      this prepares the prefix, then has Steam's command run ROTK Launcher in
      place of the shortcut's target (see ShortcutLaunch). `session` is what
      Steam tells it of the launch.
    - On Windows Steam runs this program with `--run-from-steam` (see
      WindowsLaunch).
    """

    def __init__(self, host: Host, steam: Steam | None = None) -> None:
        self.host = host
        self.steam = steam

    def launch(
        self,
        options: LaunchOptions | None = None,
        *,
        steam_command: list[str] | None = None,
        session: CompatSession | None = None,
    ) -> int:
        options = options or LaunchOptions()
        if self.host.supports_proton:
            if steam_command and session:
                steam = Steam.require(self.steam)
                return ShortcutLaunch(steam, session, steam_command).run(
                    options
                )
        elif options.run_from_steam:
            return WindowsLaunch(NativeWindows()).run(options)
        return _add_to_steam(self.steam, self.host, options)
