from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest

from steam_utility_launcher import (
    hwid_access,
    powershell_log,
    svt_ge_proton,
    update_check,
    vc_runtime,
)
from steam_utility_launcher.github_release_updater import (
    ApplicationUpdater,
    Release,
)
from steam_utility_launcher.host import LinuxHost, WindowsHost
from steam_utility_launcher.programs import NativeWindows
from steam_utility_launcher.steam import (
    CompatibilityTool,
    CompatSession,
    Process,
    Steam,
    SteamError,
    SteamLocation,
)
from steam_utility_launcher.steam_shortcuts import (
    DirectStyle,
    ShortcutManager,
    ShortcutSpec,
    WrapperStyle,
)
from steam_utility_launcher.utilities import rotk_launcher
from steam_utility_launcher.utilities.rotk_launcher import (
    LaunchOptions,
    ShortcutLaunch,
    WindowsLaunch,
)
from steam_utility_launcher.wine_prefix import wine_path
from tests.asar_helper import make_asar
from tests.process_helper import FakeProcess
from tests.steam_helper import bare_steam, make_location

if TYPE_CHECKING:
    from collections.abc import Callable

    from steam_utility_launcher.wine_prefix import WinePrefix


class _FakeProcess(FakeProcess):
    """The game, which exits with status 7 after the stand-in has logged
    `log_record` while it ran."""

    def __init__(self, log_record: str) -> None:
        super().__init__(on_wait=self._run)
        self.log_record = log_record

    def _run(self) -> int:
        if self.log_record:
            with powershell_log.path().open("a") as log:
                log.write(self.log_record)
        return 7


_STEAM_COMMAND = ["steam-launch-wrapper", "--", "proton", "waitforexitandrun"]


_SESSION = CompatSession(Path("/compat/1"), Path("/tools/SVT"))


def _launch(options: LaunchOptions | None = None) -> int:
    """launch() as a Steam shortcut's launch options would run it."""
    return rotk_launcher.RotkLauncher(LinuxHost(), bare_steam()).launch(
        options, steam_command=_STEAM_COMMAND, session=_SESSION
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch, name: str, replacement: object
) -> None:
    """Replaces one step of a launch (a method of ShortcutLaunch)."""
    monkeypatch.setattr(ShortcutLaunch, name, replacement)


def _wire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, log_record: str
) -> dict[str, object]:
    seen: dict[str, object] = {}
    monkeypatch.setattr(powershell_log, "XDG_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(rotk_launcher, "XDG_DATA_ROOT", tmp_path / "data")
    _patch(
        monkeypatch,
        "app_path",
        property(lambda _self: tmp_path / "ROTK Launcher.exe"),
    )
    _patch(monkeypatch, "install_launcher", lambda _self, **_: None)
    _patch(monkeypatch, "ensure_runtime", lambda _self: None)
    monkeypatch.setattr(hwid_access, "access_problem", lambda: None)
    monkeypatch.setattr(hwid_access, "tpm_problem", lambda: None)

    proton_checks: list[dict[str, object]] = []
    seen["proton_checks"] = proton_checks

    def fake_ensure_session_uses(
        _steam: object, session: object, **kwargs: object
    ) -> bool:
        proton_checks.append({"session": session, **kwargs})
        return bool(seen.get("proton_ok", True))

    monkeypatch.setattr(
        svt_ge_proton, "ensure_session_uses", fake_ensure_session_uses
    )
    _patch(monkeypatch, "make_prefix", lambda _self: True)
    monkeypatch.setattr(
        rotk_launcher, "_save_icon_from_any_prefix", lambda _s: None
    )
    _patch(monkeypatch, "publish_game_library", lambda _self: None)
    _patch(monkeypatch, "ensure_patches", lambda _self: None)
    _patch(monkeypatch, "prepare_tpm", lambda _self, **_: True)

    def fake_run(_self: object, **kwargs: object) -> _FakeProcess:
        seen.update(kwargs)
        process = _FakeProcess(log_record)
        initial_env = seen.get("process_env")
        if isinstance(initial_env, dict):
            process.env = initial_env  # what Steam handed the tool
        seen["process"] = process
        return process

    _patch(monkeypatch, "game_process", fake_run)
    return seen


def test_launch_refuses_to_do_anything_if_the_hardware_files_are_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _wire(monkeypatch, tmp_path, "")
    monkeypatch.setattr(
        hwid_access,
        "access_problem",
        lambda: "Refusing to start: enable-hwid-access",
    )

    def must_not_run(*_: object, **__: object) -> None:
        pytest.fail("nothing may happen before the access check passes")

    _patch(monkeypatch, "install_launcher", must_not_run)
    _patch(monkeypatch, "ensure_runtime", must_not_run)
    _patch(monkeypatch, "process", must_not_run)
    _patch(monkeypatch, "ensure_patches", must_not_run)
    with caplog.at_level("ERROR"):
        assert _launch() == 1
    assert "Refusing to start" in caplog.text
    assert not (tmp_path / "data").exists()  # not even the log directory
    assert capsys.readouterr().err == ""


def test_launch_announces_the_log_and_passes_it_to_the_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    assert _launch() == 7
    log = tmp_path / "data" / "powershell-commands.log"
    assert str(log) in capsys.readouterr().err
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra[powershell_log.ENV_VAR] == wine_path(log)
    assert not caplog.records  # nothing was refused


def test_launch_reports_a_refused_hardware_id_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = (
        "==== 2026-09-30T14:00:00Z pid=1 verdict=HWID-UNRECOGNIZED ====\n"
        "command text:\nx\n\n"
    )
    _wire(monkeypatch, tmp_path, record)
    with caplog.at_level("ERROR"):
        assert _launch() == 7
    assert "powershell-commands.log" in caplog.text
    assert "hardware" in caplog.text


def test_older_refusals_are_not_repeated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _wire(monkeypatch, tmp_path, "")
    old = powershell_log.path()
    old.write_text("==== x verdict=HWID-UNRECOGNIZED ====\n")
    with caplog.at_level("ERROR"):
        _launch()
    assert not caplog.records


def test_stand_in_is_confirmed_before_the_app_starts_and_left_in_place(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    events: list[str] = []
    ensured: list[WinePrefix] = []

    def ensure(self: ShortcutLaunch) -> None:
        ensured.append(self.prefix)
        events.append("ensure")

    def run(*_a: object, **_k: object) -> _FakeProcess:
        events.append("start")
        return _FakeProcess("")

    _patch(monkeypatch, "ensure_patches", ensure)
    _patch(monkeypatch, "game_process", run)
    _launch()
    assert events == ["ensure", "start"]  # nothing is undone afterward
    assert ensured == [_SESSION.wine_prefix]


def test_tpm_key_blobs_are_kept_in_a_private_host_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    assert _launch(LaunchOptions(tpm=True)) == 7
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["SUL_TPM"] == "1"
    directory = tmp_path / "data" / "tpm"
    assert extra["SUL_TPM_DIR"] == wine_path(directory)
    assert directory.is_dir()
    assert directory.stat().st_mode & 0o077 == 0  # only the user


def test_endorsement_extras_are_off_unless_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    _launch()
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert not {"SUL_TPM", "SUL_TPM_DIR", "SUL_TPM_ENDORSEMENT"} & set(extra)
    _launch(LaunchOptions(tpm=True))
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert "SUL_TPM_ENDORSEMENT" not in extra
    _launch(LaunchOptions(tpm_endorsement=True))
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["SUL_TPM_ENDORSEMENT"] == "1"
    assert extra["SUL_TPM"] == "1"  # the extras imply the TPM itself


def test_steams_overlay_is_left_as_steam_set_it_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    preload = "/lib/gameoverlayrenderer.so:/lib/other.so"
    seen["process_env"] = {"LD_PRELOAD": preload}
    _launch()
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert "DISABLE_VK_LAYER_VALVE_steam_overlay_1" not in extra
    process = seen["process"]
    assert isinstance(process, _FakeProcess)
    assert process.env == {"LD_PRELOAD": preload}  # untouched


def test_the_steam_overlay_can_be_taken_away(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    seen["process_env"] = {
        "LD_PRELOAD": "/lib/gameoverlayrenderer.so:/lib/other.so"
    }
    _launch(LaunchOptions(no_steam_overlay=True))
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["DISABLE_VK_LAYER_VALVE_steam_overlay_1"] == "1"
    process = seen["process"]
    assert isinstance(process, _FakeProcess)
    assert process.env == {"LD_PRELOAD": "/lib/other.so"}  # only it removed


def test_only_the_steam_overlay_is_removed_from_ld_preload() -> None:
    env = {
        "LD_PRELOAD": ":/steam/ubuntu12_32/gameoverlayrenderer.so:/x/other.so"
        ":/steam/ubuntu12_64/gameoverlayrenderer.so"
    }
    rotk_launcher._without_steam_overlay(env)
    assert env == {"LD_PRELOAD": "/x/other.so"}
    only_overlay = {"LD_PRELOAD": "/steam/ubuntu12_64/gameoverlayrenderer.so"}
    rotk_launcher._without_steam_overlay(only_overlay)
    assert only_overlay == {}
    unset: dict[str, str] = {}
    rotk_launcher._without_steam_overlay(unset)
    assert unset == {}


def test_the_app_asks_svt_ge_proton_for_the_early_window_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    _launch()
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["PROTON_EAGER_DESKTOP"] == "H1Z1.exe"
    assert "PROTON_SVT_EAGER_DESKTOP" not in extra
    assert "PROTON_ROTK_EAGER_DESKTOP" not in extra


def test_no_overlay_is_asked_for_unless_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    _launch()
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert "DXVK_CONFIG" not in extra
    assert "DXVK_HUD" not in extra


def test_the_overlay_is_turned_on_for_the_game_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    value = "fps,gpuload,scale=1.5,opacity=0.8"
    _launch(LaunchOptions(hud=value))
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    # A section for the game's executable, so DXVK applies the setting to
    # that program and not to the launcher's window.
    assert extra["DXVK_CONFIG"] == f"[H1Z1.exe];dxvk.hud={value}"
    assert "DXVK_HUD" not in extra  # which would show it everywhere


def test_an_existing_dxvk_config_is_kept_ahead_of_the_overlay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.setenv("DXVK_CONFIG", "dxvk.maxFrameLatency=1")
    _launch(LaunchOptions(hud="fps"))
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["DXVK_CONFIG"] == (
        "dxvk.maxFrameLatency=1;[H1Z1.exe];dxvk.hud=fps"
    )


def test_a_frame_rate_limit_is_set_for_the_game_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    _launch(LaunchOptions(max_fps=144))
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["DXVK_CONFIG"] == "[H1Z1.exe];dxgi.maxFrameRate=144"


def test_the_overlay_and_a_frame_rate_limit_share_the_games_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.setenv("DXVK_CONFIG", "dxvk.maxFrameLatency=1")
    _launch(LaunchOptions(hud="fps", max_fps=60))
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["DXVK_CONFIG"] == (
        "dxvk.maxFrameLatency=1;[H1Z1.exe];dxvk.hud=fps;dxgi.maxFrameRate=60"
    )


@pytest.mark.parametrize("interval", [0, 1, 2, 4])
def test_vsync_is_overridden_for_the_game_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, interval: int
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    _launch(LaunchOptions(vsync=interval))
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    # Including 0, which turns vsync off: it must not be mistaken for unset.
    assert extra["DXVK_CONFIG"] == f"[H1Z1.exe];dxgi.syncInterval={interval}"


def test_vsync_is_left_to_the_game_unless_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    _launch()
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert "DXVK_CONFIG" not in extra


def test_everything_asked_for_shares_the_games_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    _launch(LaunchOptions(hud="fps", max_fps=144, vsync=0))
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["DXVK_CONFIG"] == (
        "[H1Z1.exe];dxvk.hud=fps;dxgi.maxFrameRate=144;dxgi.syncInterval=0"
    )


@pytest.mark.parametrize("value", ["0", "1", "2", "3", "4"])
def test_sync_intervals_dxgi_allows_are_accepted(value: str) -> None:
    assert rotk_launcher.vsync_value(value) == int(value)


@pytest.mark.parametrize("value", ["5", "-1", "", "on", "1.5", "99"])
def test_sync_intervals_dxgi_would_refuse_are_refused(value: str) -> None:
    with pytest.raises(ValueError, match=r"0 \(vsync off\) to 4"):
        rotk_launcher.vsync_value(value)


@pytest.mark.parametrize("value", ["1", "60", "144", "240"])
def test_frame_rate_limits_dxvk_can_use_are_accepted(value: str) -> None:
    assert rotk_launcher.max_fps_value(value) == int(value)


@pytest.mark.parametrize("value", ["0", "-1", "-60", "", "abc", "60.5", "1e3"])
def test_frame_rate_limits_that_mean_something_else_are_refused(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="whole number"):
        rotk_launcher.max_fps_value(value)


@pytest.mark.parametrize(
    "value", ["fps,gpuload", "scale=1.5", "fps,gpuload,scale=1.5,opacity=0.8"]
)
def test_overlay_settings_dxvk_can_read_are_accepted(value: str) -> None:
    assert rotk_launcher.hud_value(value) == value


@pytest.mark.parametrize(
    "value", ["", "fps, gpuload", "fps;gpuload", 'fps"', "[fps]", "fps\t"]
)
def test_overlay_settings_dxvk_could_not_read_are_refused(value: str) -> None:
    with pytest.raises(ValueError, match="without spaces"):
        rotk_launcher.hud_value(value)


def test_the_preferred_overlay() -> None:
    assert rotk_launcher.DEFAULT_HUD == "fps,gpuload,scale=0.8,opacity=1.0"
    # ... and it is something the option accepts.
    assert rotk_launcher.hud_value(rotk_launcher.DEFAULT_HUD)


def test_the_games_proton_is_checked_before_anything_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    _launch()
    assert seen["proton_checks"] == [
        {
            "session": CompatSession(Path("/compat/1"), Path("/tools/SVT")),
            "override_flag": "--allow-any-proton",
            "allow_any": False,
        }
    ]


def test_nothing_is_started_when_the_game_does_not_use_svt_ge_proton(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    seen["proton_ok"] = False

    def fail(*_args: object, **_kwargs: object) -> None:
        pytest.fail("went on after the Proton check refused")

    _patch(monkeypatch, "install_launcher", fail)
    _patch(monkeypatch, "process", fail)
    monkeypatch.setattr(hwid_access, "access_problem", fail)
    assert _launch() == 1


def test_allowing_any_proton_is_passed_to_the_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    _launch(LaunchOptions(allow_any_proton=True))
    checks = seen["proton_checks"]
    assert isinstance(checks, list)
    assert checks[0]["allow_any"] is True


def test_tpm_that_cannot_be_used_stops_the_launch_before_anything_else(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _wire(monkeypatch, tmp_path, "")
    monkeypatch.setattr(
        hwid_access, "tpm_problem", lambda: "Refusing to start with --tpm: x"
    )

    def must_not_run(*_: object, **__: object) -> None:
        pytest.fail("nothing may happen before the TPM check passes")

    _patch(monkeypatch, "install_launcher", must_not_run)
    _patch(monkeypatch, "process", must_not_run)
    with caplog.at_level("ERROR"):
        assert _launch(LaunchOptions(tpm=True)) == 1
        assert _launch(LaunchOptions(tpm_endorsement=True)) == 1
    assert "--tpm" in caplog.text


def test_the_tpm_is_not_checked_unless_it_was_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    monkeypatch.setattr(
        hwid_access, "tpm_problem", lambda: pytest.fail("not asked for")
    )
    assert _launch() == 7


def test_a_tpm_that_fails_to_set_up_stops_the_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    _patch(monkeypatch, "prepare_tpm", lambda _self, **_: False)

    def must_not_start(*_: object, **__: object) -> None:
        pytest.fail("the launcher must not start without a working TPM")

    _patch(monkeypatch, "game_process", must_not_start)
    assert _launch(LaunchOptions(tpm=True)) == 1


def test_prepare_reports_a_failed_set_up_as_an_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Failing:
        def start(self) -> Failing:
            return self

        def wait(self) -> int:
            return 3

    monkeypatch.setattr(rotk_launcher, "XDG_DATA_ROOT", tmp_path / "data")
    _patch(monkeypatch, "process", lambda *_a, **_k: Failing())
    with caplog.at_level("ERROR"):
        ok = ShortcutLaunch(
            bare_steam(), _SESSION, _STEAM_COMMAND
        ).prepare_tpm(endorsement=False)
    assert ok is False
    assert "--tpm" in caplog.text
    assert "exit status 3" in caplog.text


def _shortcut(
    session: CompatSession, command: list[str], steam: Steam | None = None
) -> ShortcutLaunch:
    return ShortcutLaunch(steam or bare_steam(), session, command)


def _steam_launch(tmp_path: Path) -> tuple[CompatSession, list[str]]:
    tool = tmp_path / "tools" / "SVT-GE-Proton-11"
    tool.mkdir(parents=True)
    session = CompatSession(tmp_path / "compatdata" / "9", tool)
    command = [
        "steam-launch-wrapper",
        "--",
        "reaper",
        "SteamLaunch",
        "AppId=9",
        "--",
        "/runtime/_v2-entry-point",
        "--verb=waitforexitandrun",
        "--",
        str(tool / "proton"),
        "waitforexitandrun",
        "/shortcut/target.exe",
        "--shortcut-argument",
    ]
    return session, command


def test_the_shortcuts_target_is_replaced_in_steams_command(
    tmp_path: Path,
) -> None:
    session, command = _steam_launch(tmp_path)
    result = _shortcut(session, command).command_with(["C:/ROTK Launcher.exe"])
    assert result[:-1] == command[:11]
    assert result[-1] == "C:/ROTK Launcher.exe"


def test_a_command_that_does_not_run_the_sessions_proton_is_refused(
    tmp_path: Path,
) -> None:
    session, command = _steam_launch(tmp_path)
    other = CompatSession(session.compat_data_path, tmp_path / "other")
    with pytest.raises(RuntimeError, match="doesn't run Proton"):
        _shortcut(other, command).command_with(["x.exe"])


def test_the_prefix_is_made_by_running_steams_command_for_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session, command = _steam_launch(tmp_path)
    session.compat_data_path.mkdir(parents=True)
    started: list[list[str]] = []

    class Maker:
        def __init__(self, command_line: list[str], env: object) -> None:
            del env
            started.append(command_line)

        def start(self) -> Maker:
            return self

        def wait(self) -> int:
            (session.compat_data_path / "version").write_text("x")
            return 0

    monkeypatch.setattr(rotk_launcher, "Process", Maker)
    assert _shortcut(session, command).make_prefix()
    assert started[0][-3:] == ["cmd", "/c", "exit"]
    assert started[0][:11] == command[:11]
    started.clear()
    assert _shortcut(session, command).make_prefix()  # already made
    assert not started


def test_a_prefix_that_proton_did_not_make_stops_the_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, command = _steam_launch(tmp_path)

    class Failing:
        def __init__(self, **_: object) -> None:
            pass

        def start(self) -> Failing:
            return self

        def wait(self) -> int:
            return 1

    monkeypatch.setattr(rotk_launcher, "Process", Failing)
    with caplog.at_level("ERROR"):
        assert not _shortcut(session, command).make_prefix()
    assert "did not make the prefix" in caplog.text


def test_the_game_runs_as_z1_with_its_library_mounted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session, command = _steam_launch(tmp_path)
    library = tmp_path / "library"

    class WithLibrary(Steam):
        def game_library_path(self, *, game_id: str) -> Path:
            assert game_id == rotk_launcher.GAME_ID
            return library

    steam = WithLibrary(
        location=make_location(tmp_path), compatibility_tools=[]
    )
    monkeypatch.setenv("STEAM_COMPAT_MOUNTS", "/already")
    launch = ShortcutLaunch(steam, session, command)
    process = launch.game_process(extra_env={"EXTRA": "1"})
    assert process.env is not None
    assert process.env["STEAM_COMPAT_MOUNTS"] == f"/already:{library}"
    assert process.env["EXTRA"] == "1"
    assert process.command_line[-1] == str(launch.app_path)


@dataclass
class _Ensured:
    """A call of `ShortcutManager.ensure`."""

    spec: ShortcutSpec
    assume_yes: bool
    launch: bool


def _record_ensures(monkeypatch: pytest.MonkeyPatch) -> list[_Ensured]:
    """Has the shortcut manager only record what it is asked for."""
    calls: list[_Ensured] = []

    def fake_ensure(
        _self: ShortcutManager,
        spec: ShortcutSpec,
        *,
        assume_yes: bool = False,
        launch: bool = True,
    ) -> int:
        calls.append(_Ensured(spec, assume_yes, launch))
        return 1

    monkeypatch.setattr(ShortcutManager, "ensure", fake_ensure)
    return calls


def test_without_steams_command_the_launch_adds_the_shortcut_to_steam(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    calls = _record_ensures(monkeypatch)
    monkeypatch.setattr(
        rotk_launcher, "_launcher_command", lambda: ["/bin/sul"]
    )
    steam = bare_steam(tmp_path)
    assert (
        rotk_launcher.RotkLauncher(LinuxHost(), steam).launch(
            LaunchOptions(
                shortcut_options=["--max-fps", "150"], assume_yes=True
            )
        )
        == 0
    )
    (call,) = calls
    spec = call.spec
    assert spec.name == rotk_launcher.SHORTCUT_NAME
    assert spec.program == ["/bin/sul"]
    assert spec.mode == "rotk-launcher"
    assert spec.options == ["--max-fps", "150"]
    assert isinstance(spec.style, WrapperStyle)
    assert spec.icon == rotk_launcher.icon_path()
    assert call.launch is True
    assert call.assume_yes is True


def test_the_shortcut_is_not_started_if_that_is_declined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    calls = _record_ensures(monkeypatch)
    steam = bare_steam(tmp_path)
    assert (
        rotk_launcher.RotkLauncher(LinuxHost(), steam).launch(
            LaunchOptions(start_shortcut=False)
        )
        == 0
    )
    spec = calls[0].spec
    assert calls[0].launch is False
    assert spec.name == rotk_launcher.SHORTCUT_NAME
    # The banner of Z1 Battle Royale, if Steam has one
    assert spec.artwork == (
        tmp_path / "appcache" / "librarycache" / rotk_launcher.GAME_ID
    )


def test_a_shortcut_that_could_not_be_added_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")

    def refuse(*_a: object, **_k: object) -> int:
        msg = "Steam says no"
        raise SteamError(msg)

    monkeypatch.setattr(ShortcutManager, "ensure", refuse)
    steam = bare_steam(tmp_path)
    with pytest.raises(SteamError, match="Steam says no"):
        rotk_launcher.RotkLauncher(LinuxHost(), steam).launch()


def _windows_shortcut_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[_Ensured]:
    monkeypatch.setattr(
        WindowsHost, "steam_root", property(lambda _s: tmp_path)
    )
    monkeypatch.setattr(rotk_launcher, "XDG_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(
        rotk_launcher, "_launcher_command", lambda: ["C:\\sul\\sul.exe"]
    )
    return _record_ensures(monkeypatch)


def test_on_windows_run_on_its_own_it_adds_a_shortcut_that_runs_it_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _windows_shortcut_calls(monkeypatch, tmp_path)
    assert rotk_launcher.RotkLauncher(WindowsHost()).launch() == 0
    spec = calls[0].spec
    assert isinstance(spec.style, DirectStyle)
    assert spec.style.compat_tool() is None
    assert spec.program == ["C:\\sul\\sul.exe"]


def test_on_windows_started_by_steam_it_installs_and_runs_the_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[bool] = []

    def run(_self: object, options: LaunchOptions) -> int:
        ran.append(options.run_from_steam)
        return 0

    monkeypatch.setattr(WindowsLaunch, "run", run)
    code = rotk_launcher.RotkLauncher(WindowsHost()).launch(
        LaunchOptions(run_from_steam=True)
    )
    assert code == 0
    assert ran == [True]


def test_the_windows_launcher_is_where_the_installer_puts_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    launch = WindowsLaunch(NativeWindows())
    assert launch.app_path == tmp_path / "ROTK Launcher" / "ROTK Launcher.exe"


def test_the_launchers_mark_is_saved_for_the_shortcut(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rotk_launcher, "XDG_DATA_ROOT", tmp_path / "data")
    app = tmp_path / "ROTK Launcher" / "ROTK Launcher.exe"
    (app.parent / "resources").mkdir(parents=True)
    make_asar(
        app.parent / "resources",
        {"dist/branding/rotk-mark.png": b"PNG!", "dist/other.txt": b"x"},
    )
    assert rotk_launcher._save_icon(app)
    assert rotk_launcher.icon_path().read_bytes() == b"PNG!"
    assert rotk_launcher._save_icon(app)  # already there: still fine


def test_a_launcher_without_the_mark_saves_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rotk_launcher, "XDG_DATA_ROOT", tmp_path / "data")
    app = tmp_path / "ROTK Launcher" / "ROTK Launcher.exe"
    (app.parent / "resources").mkdir(parents=True)
    make_asar(app.parent / "resources", {"dist/other.txt": b"x"})
    assert not rotk_launcher._save_icon(app)
    assert not rotk_launcher.icon_path().exists()
    assert not rotk_launcher._save_icon(tmp_path / "missing" / "x.exe")


# What belongs to the shortcut's prefix


def _real_steam(root: Path) -> tuple[Steam, CompatibilityTool]:
    """A real Steam with a Proton tool installed, and a library in `root`."""
    install = root / "compatibilitytools.d" / "SVT-GE-Proton-11"
    (install / "files" / "bin").mkdir(parents=True)
    (install / "files" / "bin" / "wine").write_text("#!/bin/sh\n")
    (install / "proton").write_text("#!/usr/bin/env python3\n")
    tool = CompatibilityTool(
        internal_name="SVT-GE-Proton-11",
        manifest_vdf=install / "toolmanifest.vdf",
        install_path=install,
        display_name="SVT-GE-Proton-11",
        binary_path=install / "proton",
        binary_argument_template=["%verb%"],
    )
    steam = Steam(
        location=SteamLocation(root=root, system_config_dirs=[]),
        compatibility_tools=[tool],
    )
    return steam, tool


@pytest.mark.parametrize("shortcut_id", ["3149906526", "3574495053"])
def test_everything_goes_to_the_prefix_of_the_shortcut_whatever_its_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shortcut_id: str
) -> None:
    """The shortcut gets a new id (and a new prefix) when it is added again,
    and Z1 Battle Royale has a prefix of its own: nothing is done to that one.
    """
    steam, tool = _real_steam(tmp_path)
    compatdata = tmp_path / "steamapps" / "compatdata"
    session = CompatSession(compatdata / shortcut_id, tool.install_path)
    launch = ShortcutLaunch(steam, session, _STEAM_COMMAND)
    prefix = session.prefix

    # An installer, or the TPM set-up
    process = launch.process(["C:\\setup.exe"])
    assert process.env is not None
    assert process.env["WINEPREFIX"] == str(prefix)
    assert process.env["STEAM_COMPAT_DATA_PATH"] == str(
        compatdata / shortcut_id
    )

    # What is put in the prefix
    launch.publish_game_library()
    launch.ensure_patches()
    assert (
        prefix / "drive_c/Program Files/Steam/steamapps/libraryfolders.vdf"
    ).is_file()
    assert prefix.joinpath(
        "drive_c/windows/system32/WindowsPowerShell/v1.0/powershell.exe"
    ).is_file()

    # The Visual C++ runtime is installed with processes of that prefix too
    started: list[Process] = []

    def ensure_current(
        wine_prefix: WinePrefix, *, run: Callable[[list[str]], Process]
    ) -> None:
        assert wine_prefix == session.wine_prefix
        started.append(run(["vc_redist.x64.exe"]))

    monkeypatch.setattr(vc_runtime, "ensure_current", ensure_current)
    launch.ensure_runtime()
    (runtime_process,) = started
    assert runtime_process.env is not None
    assert runtime_process.env["WINEPREFIX"] == str(prefix)
    assert "powershell.exe=d" not in runtime_process.env.get(
        "WINEDLLOVERRIDES", ""
    )

    # Nothing was made for the game itself, or for any other id
    assert [path.name for path in compatdata.iterdir()] == [shortcut_id]
    assert not (compatdata / rotk_launcher.GAME_ID).exists()


class _Elsewhere(Steam):
    """A Steam that makes processes for a prefix that is not the launch's."""

    @override
    def process_in_prefix(
        self,
        command_line: list[str],
        *,
        game_id: str,
        cwd: Path | None = None,
        session: CompatSession | None = None,
    ) -> Process:
        return Process(
            command_line=command_line, env={"WINEPREFIX": "/somewhere/pfx"}
        )


class _NoPrefix(Steam):
    """A Steam that makes processes that have no prefix at all."""

    @override
    def process_in_prefix(
        self,
        command_line: list[str],
        *,
        game_id: str,
        cwd: Path | None = None,
        session: CompatSession | None = None,
    ) -> Process:
        return Process(command_line=command_line, env=None)


def test_a_process_that_would_not_run_in_the_prefix_is_never_started() -> None:
    steam = _Elsewhere(
        location=make_location(Path("/steam-for-the-test")),
        compatibility_tools=[],
    )
    launch = ShortcutLaunch(steam, _SESSION, _STEAM_COMMAND)
    with pytest.raises(RuntimeError, match=r"/compat/1/pfx"):
        launch.process(["C:\\setup.exe"])


def test_a_process_without_the_prefix_at_all_is_refused_too() -> None:
    steam = _NoPrefix(
        location=make_location(Path("/steam-for-the-test")),
        compatibility_tools=[],
    )
    launch = ShortcutLaunch(steam, _SESSION, _STEAM_COMMAND)
    with pytest.raises(RuntimeError, match="in the prefix of the launch"):
        launch.process(["x.exe"])


def test_the_launcher_app_is_in_the_program_files_of_the_prefix() -> None:
    launch = ShortcutLaunch(bare_steam(), _SESSION, _STEAM_COMMAND)
    assert launch.app_path == Path(
        "/compat/1/pfx/drive_c/Program Files/ROTK Launcher/ROTK Launcher.exe"
    )


def test_the_installer_runs_with_the_powershell_stub_out_of_the_way(
    tmp_path: Path,
) -> None:
    steam, tool = _real_steam(tmp_path)
    session = CompatSession(tmp_path / "compatdata" / "7", tool.install_path)
    launch = ShortcutLaunch(steam, session, _STEAM_COMMAND)
    env = launch.process(["setup.exe", "/S"]).env
    assert env is not None
    assert env["WINEDLLOVERRIDES"].endswith("powershell.exe=d")
    env = launch.process(["x.exe"], disable_powershell=False).env
    assert env is not None
    assert "powershell.exe=d" not in env["WINEDLLOVERRIDES"]
    env = launch.process(["x.exe"], extra_env={"EXTRA": "1"}).env
    assert env is not None
    assert env["EXTRA"] == "1"


# The options


def test_the_endorsement_extras_imply_the_tpm() -> None:
    assert not LaunchOptions().uses_tpm
    assert LaunchOptions(tpm=True).uses_tpm
    assert LaunchOptions(tpm_endorsement=True).uses_tpm


def test_the_game_environment_follows_the_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    monkeypatch.setattr(rotk_launcher, "XDG_DATA_ROOT", tmp_path / "data")
    log = tmp_path / "ps.log"
    plain = LaunchOptions().game_environment(log)
    assert plain == {
        powershell_log.ENV_VAR: wine_path(log),
        "PROTON_EAGER_DESKTOP": "H1Z1.exe",
    }
    full = LaunchOptions(
        hud="fps", max_fps=60, vsync=0, no_steam_overlay=True, tpm=True
    ).game_environment(log)
    assert full["DISABLE_VK_LAYER_VALVE_steam_overlay_1"] == "1"
    assert full["DXVK_CONFIG"] == (
        "[H1Z1.exe];dxvk.hud=fps;dxgi.maxFrameRate=60;dxgi.syncInterval=0"
    )
    assert full["SUL_TPM"] == "1"


# The installer


class _InstallRun:
    """Stands in for the way a command is started where the launcher goes."""

    def __init__(
        self, app: Path, code: int = 0, *, creates: bool = True
    ) -> None:
        self.app, self.code, self.creates = app, code, creates
        self.commands: list[list[str]] = []

    def __call__(self, command_line: list[str]) -> Process:
        self.commands.append(command_line)
        return FakeProcess(self._finish)

    def _finish(self) -> int:
        if self.creates:
            self.app.write_text("launcher")
        return self.code


def _installer_wiring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(rotk_launcher, "XDG_DATA_ROOT", tmp_path / "data")
    release = Release(tag="v1.2")
    monkeypatch.setattr(
        rotk_launcher,
        "_fetch_release_and_asset",
        lambda: (release, Path("ROTK-Launcher-1.2-x64.exe")),
    )
    monkeypatch.setattr(
        rotk_launcher,
        "_download_installer",
        lambda _release, _asset, destination: destination.write_bytes(b"x"),
    )
    return tmp_path / "ROTK Launcher.exe"


def test_the_launcher_is_installed_with_the_runner_it_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _installer_wiring(monkeypatch, tmp_path)
    run = _InstallRun(app)
    rotk_launcher.LauncherInstaller(app, run).ensure(force=False)
    (command,) = run.commands
    assert command[1:] == ["/S"]
    tag = (
        tmp_path / "data" / "ROTK-Launcher" / ApplicationUpdater.TAG_FILE_NAME
    )
    assert tag.read_text() == "v1.2"


def test_an_installer_that_fails_or_installs_nothing_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _installer_wiring(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="exited with status 3"):
        rotk_launcher.LauncherInstaller(app, _InstallRun(app, 3)).ensure(
            force=False
        )
    app.unlink()  # (the failing installer still left one)
    with pytest.raises(RuntimeError, match="was not found after installing"):
        rotk_launcher.LauncherInstaller(
            app, _InstallRun(app, 0, creates=False)
        ).ensure(force=False)


def test_a_launcher_that_is_there_and_was_checked_lately_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _installer_wiring(monkeypatch, tmp_path)
    app.write_text("launcher")
    directory = tmp_path / "data" / "ROTK-Launcher"
    directory.mkdir(parents=True)
    update_check.record_checked_now(directory / update_check.MARKER_FILE_NAME)
    monkeypatch.setattr(
        rotk_launcher,
        "_fetch_release_and_asset",
        lambda: pytest.fail("looked for a release"),
    )
    run = _InstallRun(app)
    rotk_launcher.LauncherInstaller(app, run).ensure(force=False)
    assert run.commands == []


# Which app Steam is told the game is

_SHORTCUT_APP = "3574495053"
_SHORTCUT_GAME = "15353327787965079552"


def test_the_ids_steam_gave_the_program_are_left_as_they_are(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SteamAppId", _SHORTCUT_APP)
    monkeypatch.setenv("SteamGameId", _SHORTCUT_GAME)
    steam, tool = _real_steam(tmp_path)
    session = CompatSession(tmp_path / "compatdata" / "7", tool.install_path)
    command = ["wrapper", "--", str(tool.binary_path), "waitforexitandrun"]
    launch = ShortcutLaunch(steam, session, command)

    game = launch.game_process(extra_env={}).env
    helper = launch.process(["setup.exe"]).env
    for env in (game, helper):
        assert env is not None
        assert (env["SteamAppId"], env["SteamGameId"]) == (
            _SHORTCUT_APP,
            _SHORTCUT_GAME,
        )
