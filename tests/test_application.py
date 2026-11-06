from __future__ import annotations

from typing import Any

import pytest

from steam_utility_launcher import application
from steam_utility_launcher.commands.utility_commands import (
    RotkLauncherCommand,
)
from steam_utility_launcher.steam import Steam
from steam_utility_launcher.utilities import rotk_launcher


def _launches(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Runs rotk-launcher without launching anything; returns what it was
    called with."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls, _host=None: object())
    )

    def launch(_self: object, options: object = None, **kwargs: object) -> int:
        calls.append({"options": options, **kwargs})
        return 0

    monkeypatch.setattr(rotk_launcher.RotkLauncher, "launch", launch)
    return calls


def test_no_overlay_is_requested_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher"])
    assert calls[0]["options"].hud is None


def test_the_overlay_value_is_forwarded_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(
        ["rotk-launcher", "--hud", "fps,gpuload,scale=1.5,opacity=0.8"]
    )
    assert calls[0]["options"].hud == "fps,gpuload,scale=1.5,opacity=0.8"


def test_the_default_overlay_is_the_preferred_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher", "--default-hud"])
    assert calls[0]["options"].hud == rotk_launcher.DEFAULT_HUD


def test_an_overlay_cannot_be_both_given_and_the_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _launches(monkeypatch)
    with pytest.raises(SystemExit):
        application.main(["rotk-launcher", "--hud", "fps", "--default-hud"])
    assert calls == []
    assert "not allowed with" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["fps, gpuload", "fps;gpuload", ""])
def test_an_overlay_value_dxvk_could_not_read_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    calls = _launches(monkeypatch)
    with pytest.raises(SystemExit):
        application.main(["rotk-launcher", "--hud", value])
    assert calls == []
    assert "without spaces, quotes, brackets" in capsys.readouterr().err


def test_no_frame_rate_limit_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher"])
    assert calls[0]["options"].max_fps is None


def test_the_frame_rate_limit_is_forwarded_as_a_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher", "--max-fps", "144"])
    assert calls[0]["options"].max_fps == 144


@pytest.mark.parametrize("value", ["0", "-5", "fast", "60.5"])
def test_a_frame_rate_limit_that_makes_no_sense_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    calls = _launches(monkeypatch)
    with pytest.raises(SystemExit):
        application.main(["rotk-launcher", f"--max-fps={value}"])
    assert calls == []
    assert "whole number of frames per second" in capsys.readouterr().err


def test_vsync_is_not_overridden_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher"])
    assert calls[0]["options"].vsync is None


@pytest.mark.parametrize("value", [0, 1, 2, 4])
def test_the_vsync_interval_is_forwarded_as_a_number(
    monkeypatch: pytest.MonkeyPatch, value: int
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher", "--vsync", str(value)])
    assert calls[0]["options"].vsync == value  # 0 stays 0, not "unset"


@pytest.mark.parametrize("value", ["5", "-1", "on", "0.5"])
def test_a_vsync_interval_dxgi_would_refuse_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    calls = _launches(monkeypatch)
    with pytest.raises(SystemExit):
        application.main(["rotk-launcher", f"--vsync={value}"])
    assert calls == []
    assert "monitor refreshes between frames" in capsys.readouterr().err


def test_the_steam_overlay_is_not_taken_away_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher"])
    assert calls[0]["options"].no_steam_overlay is False


def test_taking_the_steam_overlay_away_is_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher", "--no-steam-overlay"])
    assert calls[0]["options"].no_steam_overlay is True


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["rotk-launcher"], []),
        (["rotk-launcher", "--max-fps", "150"], ["--max-fps", "150"]),
        (["--log-file", "x", "rotk-launcher", "--tpm"], ["--tpm"]),
        (["rotk-launcher", "-y", "--default-hud"], ["--default-hud"]),
        (["rotk-launcher", "--yes", "--vsync", "0"], ["--vsync", "0"]),
        (["rotk-launcher", "--no-launch", "--tpm"], ["--tpm"]),
        (["rotk-launcher", "--tpm", "--", "steam", "cmd"], ["--tpm"]),
    ],
)
def test_only_the_launch_options_go_into_the_shortcut(
    argv: list[str], expected: list[str]
) -> None:
    assert (
        RotkLauncherCommand.shortcut_options(argv, "rotk-launcher") == expected
    )
