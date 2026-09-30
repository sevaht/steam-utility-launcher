from __future__ import annotations

from typing import TYPE_CHECKING

from steam_utility_launcher import powershell_log

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _record(verdict: str) -> str:
    return (
        f"==== 2026-09-30T14:00:00Z pid=1 verdict={verdict} ====\n"
        "command line: powershell.exe -C x\ncommand text:\nx\n\n"
    )


def test_wine_path_uses_the_z_drive() -> None:
    from pathlib import PurePosixPath

    assert (
        powershell_log.wine_path(PurePosixPath("/home/me/.local/x.log"))  # type: ignore[arg-type]
        == "Z:\\home\\me\\.local\\x.log"
    )


def test_size_of_a_missing_log_is_zero(tmp_path: Path) -> None:
    assert powershell_log.size(tmp_path / "nope.log") == 0


def test_only_hardware_id_refusals_are_counted(tmp_path: Path) -> None:
    log = tmp_path / "ps.log"
    log.write_text(
        _record("answered")
        + _record("declined")
        + _record("HWID-UNRECOGNIZED")
        + _record("declined")
        + _record("HWID-UNRECOGNIZED")
    )
    assert powershell_log.unrecognized_since(log, 0) == 2


def test_only_new_records_are_counted(tmp_path: Path) -> None:
    log = tmp_path / "ps.log"
    log.write_text(_record("HWID-UNRECOGNIZED"))
    offset = powershell_log.size(log)
    assert powershell_log.unrecognized_since(log, offset) == 0
    with log.open("a") as file:
        file.write(_record("declined") + _record("HWID-UNRECOGNIZED"))
    assert powershell_log.unrecognized_since(log, offset) == 1


def test_a_rotated_log_is_read_from_the_start(tmp_path: Path) -> None:
    log = tmp_path / "ps.log"
    log.write_text(_record("declined") * 50)
    offset = powershell_log.size(log)
    log.write_text(_record("HWID-UNRECOGNIZED"))  # smaller than before
    assert powershell_log.unrecognized_since(log, offset) == 1


def test_missing_log_counts_nothing(tmp_path: Path) -> None:
    assert powershell_log.unrecognized_since(tmp_path / "nope.log", 0) == 0


def test_report_is_silent_when_nothing_was_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log = tmp_path / "ps.log"
    log.write_text(_record("answered") + _record("declined"))
    assert powershell_log.report_unrecognized(log, 0) == 0
    assert not caplog.records


def test_report_names_the_file_when_something_was_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log = tmp_path / "ps.log"
    log.write_text(_record("HWID-UNRECOGNIZED"))
    with caplog.at_level("ERROR"):
        assert powershell_log.report_unrecognized(log, 0) == 1
    assert str(log) in caplog.text
    assert "hardware" in caplog.text


def test_log_path_is_under_the_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(powershell_log, "XDG_DATA_ROOT", tmp_path / "data")
    log = powershell_log.path()
    assert log.parent == tmp_path / "data"
    assert log.parent.is_dir()
