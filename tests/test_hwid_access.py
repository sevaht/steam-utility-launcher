from __future__ import annotations

import re
from typing import TYPE_CHECKING

from steam_utility_launcher import hwid_access

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_file_list_matches_the_root_script() -> None:
    script = hwid_access.root_script_text()
    match = re.search(r"_FILES = \(([^)]*)\)", script)
    assert match
    names = tuple(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert names == hwid_access.DMI_FILES
    assert 'Path("/sys/class/dmi/id")' in script
    assert str(hwid_access.DMI_DIR) == "/sys/class/dmi/id"


def test_restricted_files_lists_only_existing_unreadable_ones(
    tmp_path: Path,
) -> None:
    readable = tmp_path / "board_serial"
    readable.write_text("x")
    locked = tmp_path / "product_uuid"
    locked.write_text("y")
    locked.chmod(0)
    try:
        enforced = not hwid_access._readable(locked)
        result = hwid_access.restricted_files(tmp_path)
    finally:
        locked.chmod(0o600)
    assert readable not in result
    # Running as root can read anything; only check the list when the
    # permission bits are actually enforced.
    if enforced:
        assert result == [locked]


def test_no_problem_when_everything_is_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hwid_access, "restricted_files", list)
    assert hwid_access.access_problem() is None


def test_problem_names_the_files_and_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hwid_access,
        "restricted_files",
        lambda: [
            hwid_access.DMI_DIR / "product_uuid",
            hwid_access.DMI_DIR / "board_serial",
        ],
    )
    problem = hwid_access.access_problem()
    assert problem is not None
    assert "product_uuid" in problem
    assert "board_serial" in problem
    assert "enable-hwid-access" in problem
    assert "Refusing to start" in problem


def test_nothing_is_run_when_already_readable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(hwid_access, "restricted_files", list)
    monkeypatch.setattr(
        "steam_utility_launcher.hwid_access.os.geteuid", lambda: 1000
    )

    def fail(*_: object, **__: object) -> None:
        raise AssertionError

    monkeypatch.setattr(
        "steam_utility_launcher.hwid_access.subprocess.run", fail
    )
    assert hwid_access.run() == 0
    assert "already readable" in capsys.readouterr().out


def test_refuses_to_run_as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "steam_utility_launcher.hwid_access.os.geteuid", lambda: 0
    )
    assert hwid_access.run() == 1


def test_sudo_runs_a_system_python_isolated_with_the_script_on_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str]] = []

    class Result:
        returncode = 0

    def fake_run(command: list[str], **kwargs: str) -> Result:
        calls.append((command, kwargs["input"]))
        return Result()

    monkeypatch.setattr(
        "steam_utility_launcher.hwid_access.os.geteuid", lambda: 1000
    )
    monkeypatch.setattr(hwid_access, "restricted_files", list)
    monkeypatch.setattr(
        "steam_utility_launcher.hwid_access.subprocess.run", fake_run
    )
    monkeypatch.setattr(
        hwid_access, "_system_python", lambda: "/usr/bin/python3"
    )
    assert hwid_access.run(disable=True, assume_yes=True) == 0
    command, stdin = calls[0]
    assert command[0].endswith("sudo")
    assert command[1:] == [
        "--",
        "/usr/bin/python3",
        "-I",
        "-B",
        "-",
        "disable",
    ]
    assert stdin == hwid_access.root_script_text()


def test_declining_the_prompt_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "steam_utility_launcher.hwid_access.os.geteuid", lambda: 1000
    )
    monkeypatch.setattr(
        hwid_access, "restricted_files", lambda: [hwid_access.DMI_DIR]
    )
    monkeypatch.setattr("builtins.input", lambda _: "n")

    def fail(*_: object, **__: object) -> None:
        raise AssertionError

    monkeypatch.setattr(
        "steam_utility_launcher.hwid_access.subprocess.run", fail
    )
    assert hwid_access.run() == 1
