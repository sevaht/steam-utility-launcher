from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import (
    hwid_access,
    powershell_log,
    svt_proton,
    vc_runtime,
)
from steam_utility_launcher.utilities import rotk_launcher

if TYPE_CHECKING:
    from pathlib import Path


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
        lambda _steam: (prefix, tmp_path / "ROTK Launcher.exe"),
    )
    monkeypatch.setattr(rotk_launcher, "_install_if_needed", lambda **_: None)
    monkeypatch.setattr(vc_runtime, "ensure_current", lambda **_: None)
    monkeypatch.setattr(hwid_access, "access_problem", lambda: None)
    monkeypatch.setattr(hwid_access, "tpm_problem", lambda: None)

    proton_checks: list[dict[str, object]] = []
    seen["proton_checks"] = proton_checks

    def fake_ensure_game_uses(
        _steam: object, game_id: str, **kwargs: object
    ) -> bool:
        proton_checks.append({"game_id": game_id, **kwargs})
        return bool(seen.get("proton_ok", True))

    monkeypatch.setattr(svt_proton, "ensure_game_uses", fake_ensure_game_uses)

    monkeypatch.setattr(rotk_launcher, "ensure_wine_patches", lambda _p: None)
    monkeypatch.setattr(rotk_launcher, "_prepare_tpm", lambda **_: True)

    def fake_run(
        _steam: object, _prefix: object, _command: list[str], **kwargs: object
    ) -> _FakeProcess:
        seen.update(kwargs)
        return _FakeProcess(log_record)

    monkeypatch.setattr(rotk_launcher, "_run_in_context", fake_run)
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
        assert rotk_launcher.launch(steam=object()) == 1  # type: ignore[arg-type]
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
    assert rotk_launcher.launch(steam=object()) == 7  # type: ignore[arg-type]
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
        assert rotk_launcher.launch(steam=object()) == 7  # type: ignore[arg-type]
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
        rotk_launcher.launch(steam=object())  # type: ignore[arg-type]
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
    monkeypatch.setattr(rotk_launcher, "_run_in_context", run)
    rotk_launcher.launch(steam=object())  # type: ignore[arg-type]
    assert events == ["ensure", "start"]  # nothing is undone afterward
    assert ensured == [tmp_path / "pfx"]


def test_tpm_key_blobs_are_kept_in_a_private_host_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    assert rotk_launcher.launch(steam=object(), tpm=True) == 7  # type: ignore[arg-type]
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
    rotk_launcher.launch(steam=object())  # type: ignore[arg-type]
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert not {"SUL_TPM", "SUL_TPM_DIR", "SUL_TPM_ENDORSEMENT"} & set(extra)
    rotk_launcher.launch(steam=object(), tpm=True)  # type: ignore[arg-type]
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert "SUL_TPM_ENDORSEMENT" not in extra
    rotk_launcher.launch(steam=object(), tpm_endorsement=True)  # type: ignore[arg-type]
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["SUL_TPM_ENDORSEMENT"] == "1"
    assert extra["SUL_TPM"] == "1"  # the extras imply the TPM itself


def test_steam_overlay_layer_is_off_for_the_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    rotk_launcher.launch(steam=object())  # type: ignore[arg-type]
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["DISABLE_VK_LAYER_VALVE_steam_overlay_1"] == "1"


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
    rotk_launcher.launch(steam=object())  # type: ignore[arg-type]
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["PROTON_SVT_EAGER_DESKTOP"] == "H1Z1.exe"
    assert "PROTON_ROTK_EAGER_DESKTOP" not in extra


def test_no_overlay_is_asked_for_unless_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    rotk_launcher.launch(steam=object())  # type: ignore[arg-type]
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
    rotk_launcher.launch(steam=object(), hud=value)  # type: ignore[arg-type]
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
    rotk_launcher.launch(steam=object(), hud="fps")  # type: ignore[arg-type]
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
    rotk_launcher.launch(steam=object(), max_fps=144)  # type: ignore[arg-type]
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert extra["DXVK_CONFIG"] == "[H1Z1.exe];dxgi.maxFrameRate=144"


def test_the_overlay_and_a_frame_rate_limit_share_the_games_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.setenv("DXVK_CONFIG", "dxvk.maxFrameLatency=1")
    rotk_launcher.launch(
        steam=object(), hud="fps", max_fps=60  # type: ignore[arg-type]
    )
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
    rotk_launcher.launch(
        steam=object(), vsync=interval  # type: ignore[arg-type]
    )
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    # Including 0, which turns vsync off: it must not be mistaken for unset.
    assert extra["DXVK_CONFIG"] == f"[H1Z1.exe];dxgi.syncInterval={interval}"


def test_vsync_is_left_to_the_game_unless_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    rotk_launcher.launch(steam=object())  # type: ignore[arg-type]
    extra = seen["extra_env"]
    assert isinstance(extra, dict)
    assert "DXVK_CONFIG" not in extra


def test_everything_asked_for_shares_the_games_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DXVK_CONFIG", raising=False)
    rotk_launcher.launch(
        steam=object(),  # type: ignore[arg-type]
        hud="fps",
        max_fps=144,
        vsync=0,
    )
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
    rotk_launcher.launch(steam=object())  # type: ignore[arg-type]
    assert seen["proton_checks"] == [
        {
            "game_id": rotk_launcher.GAME_ID,
            "game_name": "Z1 Battle Royale",
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
    assert rotk_launcher.launch(steam=object()) == 1  # type: ignore[arg-type]


def test_allowing_any_proton_is_passed_to_the_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _wire(monkeypatch, tmp_path, "")
    rotk_launcher.launch(
        steam=object(), allow_any_proton=True  # type: ignore[arg-type]
    )
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
        assert (
            rotk_launcher.launch(steam=object(), tpm=True) == 1  # type: ignore[arg-type]
        )
        assert (
            rotk_launcher.launch(steam=object(), tpm_endorsement=True) == 1  # type: ignore[arg-type]
        )
    assert "--tpm" in caplog.text


def test_the_tpm_is_not_checked_unless_it_was_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    monkeypatch.setattr(
        hwid_access, "tpm_problem", lambda: pytest.fail("not asked for")
    )
    assert rotk_launcher.launch(steam=object()) == 7  # type: ignore[arg-type]


def test_a_tpm_that_fails_to_set_up_stops_the_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire(monkeypatch, tmp_path, "")
    monkeypatch.setattr(rotk_launcher, "_prepare_tpm", lambda **_: False)

    def must_not_start(*_: object, **__: object) -> None:
        pytest.fail("the launcher must not start without a working TPM")

    monkeypatch.setattr(rotk_launcher, "_run_in_context", must_not_start)
    assert rotk_launcher.launch(steam=object(), tpm=True) == 1  # type: ignore[arg-type]


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
            steam=None, prefix=tmp_path, endorsement=False
        )
    assert ok is False
    assert "--tpm" in caplog.text
    assert "exit status 3" in caplog.text
