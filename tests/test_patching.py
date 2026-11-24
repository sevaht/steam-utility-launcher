"""Byte patches and what the fix commands answer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steam_utility_launcher.errors import UserError
from steam_utility_launcher.patching import (
    ByteEdit,
    BytePatch,
    ExecutablePatcher,
    FixAction,
    FixReport,
    FixTarget,
    PatchMethod,
    PatchState,
)

PATCH = BytePatch((ByteEdit(2, b"ab", b"AB"), ByteEdit(6, b"c", b"C")))
ORIGINAL = b"..ab..c.."
PATCHED = b"..AB..C.."


def _target(
    tmp_path: Path, content: bytes, *, is_dry_run: bool = False
) -> tuple[FixTarget, Path]:
    path = tmp_path / "game"
    path.write_bytes(content)
    target = FixTarget(
        executable=path, game_name="The Game", is_dry_run=is_dry_run
    )
    return target, path


def test_an_edit_does_not_move_anything_or_change_nothing() -> None:
    with pytest.raises(ValueError, match="does not move"):
        ByteEdit(0, b"a", b"bc")
    with pytest.raises(ValueError, match="changes nothing"):
        ByteEdit(0, b"a", b"a")


def test_a_patch_needs_edits_that_do_not_overlap() -> None:
    with pytest.raises(ValueError, match="at least one"):
        BytePatch(())
    with pytest.raises(ValueError, match="overlap"):
        BytePatch((ByteEdit(0, b"ab", b"AB"), ByteEdit(1, b"b", b"X")))
    BytePatch((ByteEdit(0, b"ab", b"AB"), ByteEdit(2, b"c", b"C")))


@pytest.mark.parametrize(
    ("data", "state"),
    [
        (ORIGINAL, PatchState.ORIGINAL),
        (PATCHED, PatchState.PATCHED),
        (b"..AB..c..", PatchState.PARTIAL),
        (b"..ab..C..", PatchState.PARTIAL),
        (b"..xx..c..", PatchState.UNRECOGNIZED),
        (b"..AB..x..", PatchState.UNRECOGNIZED),
        (b"..ab", PatchState.UNRECOGNIZED),
        (b"", PatchState.UNRECOGNIZED),
    ],
    ids=[
        "original",
        "patched",
        "first-only",
        "second-only",
        "first-unknown",
        "second-unknown",
        "too-short",
        "empty",
    ],
)
def test_the_state_of_the_bytes(data: bytes, state: PatchState) -> None:
    assert PATCH.state_of(data) is state


def test_applying_and_reverting_change_only_the_edits() -> None:
    assert PATCH.applied_to(ORIGINAL) == PATCHED
    assert PATCH.reverted_in(PATCHED) == ORIGINAL
    assert PATCH.applied_to(b"..AB..c..") == PATCHED  # finishes a partial one
    assert PATCH.reverted_in(b"..AB..c..") == ORIGINAL


def test_the_state_words_are_the_ones_a_script_reads() -> None:
    assert [str(state) for state in PatchState] == [
        "original",
        "patched",
        "partial",
        "unrecognized",
    ]


def test_a_report_is_its_state_word_or_a_line_of_json() -> None:
    report = FixReport(
        fix="a-fix",
        path=Path("/game"),
        state=PatchState.PATCHED,
        method=PatchMethod.DLL,
        is_changed=True,
    )
    assert report.as_text() == "patched"
    assert json.loads(report.as_json()) == {
        "fix": "a-fix",
        "path": "/game",
        "state": "patched",
        "method": "dll",
        "changed": True,
        "dry_run": False,
    }
    assert "\n" not in report.as_json()


def test_patch_and_restore_are_exact_and_leave_no_other_file(
    tmp_path: Path,
) -> None:
    target, path = _target(tmp_path, ORIGINAL)
    fix = ExecutablePatcher("a-fix", PATCH, target)
    assert fix.status().state is PatchState.ORIGINAL
    report = fix.patch()
    assert (report.state, report.is_changed) == (PatchState.PATCHED, True)
    assert path.read_bytes() == PATCHED
    assert fix.status().state is PatchState.PATCHED
    report = fix.restore()
    assert (report.state, report.is_changed) == (PatchState.ORIGINAL, True)
    assert path.read_bytes() == ORIGINAL
    assert [entry.name for entry in tmp_path.iterdir()] == ["game"]


def test_doing_what_is_done_changes_nothing(tmp_path: Path) -> None:
    target, path = _target(tmp_path, PATCHED)
    report = ExecutablePatcher("a-fix", PATCH, target).patch()
    assert (report.state, report.is_changed) == (PatchState.PATCHED, False)
    assert path.read_bytes() == PATCHED


def test_a_partial_patch_is_finished_or_taken_out(tmp_path: Path) -> None:
    target, path = _target(tmp_path, b"..AB..c..")
    fix = ExecutablePatcher("a-fix", PATCH, target)
    assert fix.status().state is PatchState.PARTIAL
    fix.patch()
    assert path.read_bytes() == PATCHED
    path.write_bytes(b"..AB..c..")
    fix.restore()
    assert path.read_bytes() == ORIGINAL


def test_a_dry_run_says_what_would_be_and_writes_nothing(
    tmp_path: Path,
) -> None:
    target, path = _target(tmp_path, ORIGINAL, is_dry_run=True)
    report = ExecutablePatcher("a-fix", PATCH, target).patch()
    assert (report.state, report.is_changed, report.is_dry_run) == (
        PatchState.PATCHED,
        True,
        True,
    )
    assert path.read_bytes() == ORIGINAL


@pytest.mark.parametrize("action", [FixAction.PATCH, FixAction.RESTORE])
def test_an_unrecognized_executable_is_never_written(
    action: FixAction, tmp_path: Path
) -> None:
    target, path = _target(tmp_path, b"..xx..c..")
    fix = ExecutablePatcher("a-fix", PATCH, target)
    with pytest.raises(UserError, match="not a game version"):
        action.run(fix)
    assert path.read_bytes() == b"..xx..c.."
    assert fix.status().state is PatchState.UNRECOGNIZED  # asking is fine


def test_a_running_game_is_not_touched(tmp_path: Path) -> None:
    path = tmp_path / "game"
    path.write_bytes(ORIGINAL)
    target = FixTarget(
        executable=path, game_name="The Game", is_game_running=True
    )
    fix = ExecutablePatcher("a-fix", PATCH, target)
    with pytest.raises(UserError, match="Close The Game"):
        fix.patch()
    assert path.read_bytes() == ORIGINAL
    assert fix.status().state is PatchState.ORIGINAL


def test_a_missing_file_is_a_user_error(tmp_path: Path) -> None:
    target = FixTarget(executable=tmp_path / "nope", game_name="The Game")
    with pytest.raises(UserError, match="Could not read"):
        ExecutablePatcher("a-fix", PATCH, target).status()


def test_every_action_is_a_word_that_runs_its_operation(
    tmp_path: Path,
) -> None:
    target, path = _target(tmp_path, ORIGINAL)
    fix = ExecutablePatcher("a-fix", PATCH, target)
    assert FixAction("status").run(fix).state is PatchState.ORIGINAL
    assert FixAction("patch").run(fix).state is PatchState.PATCHED
    assert FixAction("restore").run(fix).state is PatchState.ORIGINAL
    assert path.read_bytes() == ORIGINAL
