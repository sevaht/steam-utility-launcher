from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import hwid_access, powershell_log, vc_runtime
from steam_utility_launcher.utilities import rotk_launcher

if TYPE_CHECKING:
    from pathlib import Path


class _FakeProcess:
    def __init__(self, on_wait: str) -> None:
        self.on_wait = on_wait

    def start(self) -> _FakeProcess:
        return self

    def wait(self) -> int:
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

    monkeypatch.setattr(rotk_launcher, "ensure_wine_patches", lambda _p: None)

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
