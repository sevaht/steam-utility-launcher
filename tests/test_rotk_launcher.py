from __future__ import annotations

from pathlib import Path

import pytest

from steam_utility_launcher import (
    hwid_access,
    powershell_log,
    steam_shortcuts,
    svt_proton,
    vc_runtime,
)
from steam_utility_launcher.steam import CompatSession
from steam_utility_launcher.utilities import rotk_launcher
from tests.asar_helper import make_asar


class _FakeProcess:
    def __init__(self, on_wait: str) -> None:
        self.on_wait = on_wait
        self.env: dict[str, str] | None = None

    def start(self) -> _FakeProcess:
        return self

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.on_wait:
            # What the stand-in does while the app runs.
            with powershell_log.path().open("a") as log:
                log.write(self.on_wait)
        return 7


_STEAM_COMMAND = ["steam-launch-wrapper", "--", "proton", "waitforexitandrun"]


def _launch(**kwargs: object) -> int:
    """launch() as a Steam shortcut's launch options would run it."""
    return rotk_launcher.launch(
        steam=object(),  # type: ignore[arg-type]
        steam_command=_STEAM_COMMAND,
        session=CompatSession(Path("/compat/1"), Path("/tools/SVT")),
        **kwargs,  # type: ignore[arg-type]
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, log_record: str
) -> dict[str, object]:
    seen: dict[str, object] = {}
    monkeypatch.setattr(powershell_log, "XDG_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(rotk_launcher, "XDG_DATA_ROOT", tmp_path / "data")
    prefix = tmp_path / "pfx"
    prefix.mkdir()
    monkeypatch.setattr(
        rotk_launcher,
        "_resolve_app_path",
        lambda _session: (prefix, tmp_path / "ROTK Launcher.exe"),
    )
    monkeypatch.setattr(rotk_launcher, "_install_if_needed", lambda **_: None)
    monkeypatch.setattr(vc_runtime, "ensure_current", lambda **_: None)
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
        svt_proton, "ensure_session_uses", fake_ensure_session_uses
    )
    monkeypatch.setattr(rotk_launcher, "_make_prefix", lambda *_: True)
    monkeypatch.setattr(
        rotk_launcher, "_save_icon_from_any_prefix", lambda _s: None
    )
    monkeypatch.setattr(
        rotk_launcher, "_publish_game_library", lambda *_: None
    )

    monkeypatch.setattr(rotk_launcher, "ensure_wine_patches", lambda _p: None)
    monkeypatch.setattr(rotk_launcher, "_prepare_tpm", lambda **_: True)

    def fake_run(
        _steam: object,
        _session: object,
        _command: list[str],
        _app_path: object,
        **kwargs: object,
    ) -> _FakeProcess:
        seen.update(kwargs)
        process = _FakeProcess(log_record)
        initial_env = seen.get("process_env")
        if isinstance(initial_env, dict):
            process.env = initial_env  # what Steam handed the tool
        seen["process"] = process
        return process

    monkeypatch.setattr(rotk_launcher, "_game_process", fake_run)
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

    monkeypatch.setattr(rotk_launcher, "_install_if_needed", must_not_run)
    monkeypatch.setattr(vc_runtime, "ensure_current", must_not_run)
    monkeypatch.setattr(rotk_launcher, "_run_in_context", must_not_run)
    monkeypatch.setattr(rotk_launcher, "ensure_wine_patches", must_not_run)
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
    assert extra[powershell_log.ENV_VAR] == powershell_log.wine_path(log)
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
    ensured: list[Path] = []

    def ensure(prefix: Path) -> None:
        ensured.append(prefix)
        events.append("ensure")

    def run(*_a: object, **_k: object) -> _FakeProcess:
        events.append("start")
        return _FakeProcess("")

    monkeypatch.setattr(rotk_launcher, "ensure_wine_patches", ensure)
    monkeypatch.setattr(rotk_launcher, "_game_process", run)
    _launch()
    assert events == ["ensure", "start"]  # nothing is undone afterward
    assert ensured == [tmp_path / "pfx"]


def test_tpm_key_blobs_are_kept_in_a_private_host_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    assert _launch(tpm=True) == 7
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["SUL_TPM"] == "1"
    directory = tmp_path / "data" / "tpm"
    assert extra["SUL_TPM_DIR"] == powershell_log.wine_path(directory)
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
    _launch(tpm=True)
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert "SUL_TPM_ENDORSEMENT" not in extra
    _launch(tpm_endorsement=True)
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
    _launch(no_steam_overlay=True)
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


def test_the_app_asks_svt_proton_for_the_early_window_setup(
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
    _launch(hud=value)
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
    _launch(hud="fps")
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
    _launch(max_fps=144)
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["DXVK_CONFIG"] == "[H1Z1.exe];dxgi.maxFrameRate=144"


def test_the_overlay_and_a_frame_rate_limit_share_the_games_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.setenv("DXVK_CONFIG", "dxvk.maxFrameLatency=1")
    _launch(hud="fps", max_fps=60)
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
    _launch(vsync=interval)
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
    _launch(hud="fps", max_fps=144, vsync=0)
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


def test_nothing_is_started_when_the_game_does_not_use_svt_proton(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    seen["proton_ok"] = False

    def fail(*_args: object, **_kwargs: object) -> None:
        pytest.fail("went on after the Proton check refused")

    monkeypatch.setattr(rotk_launcher, "_install_if_needed", fail)
    monkeypatch.setattr(rotk_launcher, "_run_in_context", fail)
    monkeypatch.setattr(hwid_access, "access_problem", fail)
    assert _launch() == 1


def test_allowing_any_proton_is_passed_to_the_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    _launch(allow_any_proton=True)
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

    monkeypatch.setattr(rotk_launcher, "_install_if_needed", must_not_run)
    monkeypatch.setattr(rotk_launcher, "_run_in_context", must_not_run)
    with caplog.at_level("ERROR"):
        assert _launch(tpm=True) == 1
        assert _launch(tpm_endorsement=True) == 1
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
    monkeypatch.setattr(rotk_launcher, "_prepare_tpm", lambda **_: False)

    def must_not_start(*_: object, **__: object) -> None:
        pytest.fail("the launcher must not start without a working TPM")

    monkeypatch.setattr(rotk_launcher, "_run_in_context", must_not_start)
    assert _launch(tpm=True) == 1


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
    monkeypatch.setattr(
        rotk_launcher, "_run_in_context", lambda *_a, **_k: Failing()
    )
    with caplog.at_level("ERROR"):
        ok = rotk_launcher._prepare_tpm(
            steam=None, prefix=tmp_path, session=None, endorsement=False
        )
    assert ok is False
    assert "--tpm" in caplog.text
    assert "exit status 3" in caplog.text


def _steam_launch(tmp_path: Path) -> tuple[CompatSession, list[str]]:
    tool = tmp_path / "tools" / "SVT-Proton-GE11"
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
    result = rotk_launcher._command_with_program(
        command, session, ["C:/ROTK Launcher.exe"]
    )
    assert result[:-1] == command[:11]
    assert result[-1] == "C:/ROTK Launcher.exe"


def test_a_command_that_does_not_run_the_sessions_proton_is_refused(
    tmp_path: Path,
) -> None:
    session, command = _steam_launch(tmp_path)
    other = CompatSession(session.compat_data_path, tmp_path / "other")
    with pytest.raises(RuntimeError, match="doesn't run Proton"):
        rotk_launcher._command_with_program(command, other, ["x.exe"])


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
    assert rotk_launcher._make_prefix(command, session)
    assert started[0][-3:] == ["cmd", "/c", "exit"]
    assert started[0][:11] == command[:11]
    started.clear()
    assert rotk_launcher._make_prefix(command, session)  # already made
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
        assert not rotk_launcher._make_prefix(command, session)
    assert "did not make the prefix" in caplog.text


def test_the_game_runs_as_z1_with_its_library_mounted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session, command = _steam_launch(tmp_path)
    library = tmp_path / "library"

    class FakeSteam:
        def game_library_path(self, game_id: str) -> Path:
            assert game_id == rotk_launcher.GAME_ID
            return library

    monkeypatch.setenv("STEAM_COMPAT_MOUNTS", "/already")
    process = rotk_launcher._game_process(
        FakeSteam(),  # type: ignore[arg-type]
        session,
        command,
        Path("C:/ROTK Launcher.exe"),
        extra_env={"EXTRA": "1"},
    )
    assert process.env is not None
    assert process.env["SteamAppId"] == rotk_launcher.GAME_ID
    assert process.env["SteamGameId"] == rotk_launcher.GAME_ID
    assert process.env["STEAM_COMPAT_MOUNTS"] == f"/already:{library}"
    assert process.env["EXTRA"] == "1"
    assert process.command_line[-1] == str(Path("C:/ROTK Launcher.exe"))


def test_without_steams_command_the_launch_adds_the_shortcut_to_steam(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    calls: list[dict[str, object]] = []

    def fake_ensure(steam: object, **kwargs: object) -> int:
        calls.append({"steam": steam, **kwargs})
        return 1

    monkeypatch.setattr(steam_shortcuts, "ensure_shortcut", fake_ensure)
    monkeypatch.setattr(
        rotk_launcher, "_launcher_command", lambda: ["/bin/sul"]
    )
    steam = object()
    assert (
        rotk_launcher.launch(
            steam=steam,  # type: ignore[arg-type]
            shortcut_options=["--max-fps", "150"],
            assume_yes=True,
        )
        == 0
    )
    (call,) = calls
    assert call["steam"] is steam
    assert call["name"] == "ROTK Launcher"
    assert call["program"] == ["/bin/sul"]
    assert call["mode"] == "rotk-launcher"
    assert call["options"] == ["--max-fps", "150"]
    assert call["launch"] is True
    assert call["icon"] == rotk_launcher.icon_path()
    assert call["assume_yes"] is True


def test_the_shortcut_is_not_started_if_that_is_declined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    calls: list[dict[str, object]] = []

    def fake_ensure(_steam: object, **kwargs: object) -> int:
        calls.append(kwargs)
        return 1

    monkeypatch.setattr(steam_shortcuts, "ensure_shortcut", fake_ensure)
    assert (
        rotk_launcher.launch(
            steam=object(), start_shortcut=False  # type: ignore[arg-type]
        )
        == 0
    )
    assert calls[0]["launch"] is False


def test_a_shortcut_that_could_not_be_added_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    monkeypatch.setattr(
        steam_shortcuts, "ensure_shortcut", lambda *_a, **_k: None
    )
    assert rotk_launcher.launch(steam=object()) == 1  # type: ignore[arg-type]


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
