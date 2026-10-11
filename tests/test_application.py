from __future__ import annotations

import pytest

from steam_utility_launcher import application
from steam_utility_launcher.steam import Steam
from steam_utility_launcher.utilities import rotk_launcher


def _launches(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Runs rotk-launcher without launching anything; returns what it was
    called with."""
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls: object())
    )

    def launch(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(rotk_launcher, "launch", launch)
    return calls


def test_no_overlay_is_requested_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher"])
    assert calls[0]["hud"] is None


def test_the_overlay_value_is_forwarded_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(
        ["rotk-launcher", "--hud", "fps,gpuload,scale=1.5,opacity=0.8"]
    )
    assert calls[0]["hud"] == "fps,gpuload,scale=1.5,opacity=0.8"


def test_the_default_overlay_is_the_preferred_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _launches(monkeypatch)
    application.main(["rotk-launcher", "--default-hud"])
    assert calls[0]["hud"] == rotk_launcher.DEFAULT_HUD


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
