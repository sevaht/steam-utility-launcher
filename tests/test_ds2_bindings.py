from __future__ import annotations

import logging
import os
from importlib import resources
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from steam_utility_launcher import application, ds2_bindings
from steam_utility_launcher.ds2_controls import (
    Action,
    Controls,
    KeyBinding,
    MouseBinding,
)
from steam_utility_launcher.ds2_inputs import KEYS, MODIFIERS, MOUSE_BUTTONS
from steam_utility_launcher.ds2_save import SaveFile
from steam_utility_launcher.steam import SteamError

from .ds2_save_helper import build_save

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from steam_utility_launcher.steam import Steam

SHIFT = MODIFIERS.parse("Shift")
CTRL = MODIFIERS.parse("Ctrl")
LEFT_CLICK = MOUSE_BUTTONS.parse("Left click")
RIGHT_CLICK = MOUSE_BUTTONS.parse("Right click")

# What a fresh save has: Toggle menu is Shift plus an arrow, Function 1 is E and
# Function 2 is N. Action 41 (Walk) has a code that has no name.
START_KEYBOARD = {
    int(Action.TOGGLE_MENU_LEFT): KeyBinding(KEYS.parse("Left"), SHIFT),
    int(Action.TOGGLE_MENU_RIGHT): KeyBinding(KEYS.parse("Right"), SHIFT),
    int(Action.FUNCTION_1): KeyBinding(KEYS.parse("E")),
    int(Action.FUNCTION_2): KeyBinding(KEYS.parse("N")),
    int(Action.WALK): KeyBinding(0x7D),
}
START_MOUSE = {
    int(Action.ATTACK_RIGHT_HAND): MouseBinding(RIGHT_CLICK),
    int(Action.JUMP): MouseBinding(2, SHIFT),
}
CONFIG_TEXT = """\
[keyboard]
toggle_menu_left = "C"
toggle_menu_right = "V"
function_1 = "P"
function_2 = "B"
"""
WANTED_KEYBOARD = {
    **START_KEYBOARD,
    int(Action.TOGGLE_MENU_LEFT): KeyBinding(KEYS.parse("C")),
    int(Action.TOGGLE_MENU_RIGHT): KeyBinding(KEYS.parse("V")),
    int(Action.FUNCTION_1): KeyBinding(KEYS.parse("P")),
    int(Action.FUNCTION_2): KeyBinding(KEYS.parse("B")),
}


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """application.main sets up logging on the stderr pytest is capturing,
    which is closed by the next test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def save(tmp_path: Path) -> Path:
    path = tmp_path / ds2_bindings.SAVE_FILE_NAME
    path.write_bytes(build_save(START_KEYBOARD.items(), START_MOUSE.items()))
    path.chmod(0o640)
    return path


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "bindings.toml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def game_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds2_bindings, "game_is_running", lambda: False)


def _backups(save: Path) -> list[Path]:
    return sorted(save.parent.glob(f"{save.name}.sul-backup-*"))


def _controls(path: Path) -> Controls:
    return SaveFile.read(path).controls


def _config(tmp_path: Path, text: str, name: str = "other.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- the config file


def test_a_config_names_actions_and_what_to_set_them_to() -> None:
    controls = ds2_bindings.parse_config("""\
[keyboard]
run_forward = "W"
walk = 0x7d
toggle_menu_left = { key = "Left", modifier = "Shift" }
function_1 = { key = "P" }

[mouse]
jump = { button = "Click mouse wheel", modifier = "Shift" }
attack_right_hand = "Right click"
interact = 0
""")
    assert controls == Controls(
        keyboard={
            int(Action.RUN_FORWARD): KeyBinding(KEYS.parse("W")),
            int(Action.WALK): KeyBinding(0x7D),
            int(Action.TOGGLE_MENU_LEFT): KeyBinding(
                KEYS.parse("Left"), SHIFT
            ),
            int(Action.FUNCTION_1): KeyBinding(KEYS.parse("P")),
        },
        mouse={
            int(Action.JUMP): MouseBinding(2, SHIFT),
            int(Action.ATTACK_RIGHT_HAND): MouseBinding(RIGHT_CLICK),
            int(Action.INTERACT): MouseBinding(0),
        },
    )


def test_a_config_may_have_only_one_of_the_sections() -> None:
    assert not ds2_bindings.parse_config("").keyboard
    only_mouse = ds2_bindings.parse_config('[mouse]\njump = "Nothing"\n')
    assert only_mouse.mouse
    assert not only_mouse.keyboard


def test_actions_can_be_given_by_id_when_they_have_no_name() -> None:
    controls = ds2_bindings.parse_config('[keyboard]\naction_99 = "A"\n')
    assert controls.keyboard == {99: KeyBinding(KEYS.parse("A"))}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[keyboard\n", "not valid TOML"),
        ('[keys]\nrun_forward = "W"\n', "unknown section"),
        ('[keyboard]\nrun_forwards = "W"\n', "run_forwards.*unknown action"),
        ('[keyboard]\nrun_forward = "Nope"\n', "run_forward.*unknown key"),
        ('[mouse]\njump = "Left"\n', "jump.*unknown mouse button"),
        ("[keyboard]\nrun_forward = 300\n", "out of range"),
        ("[keyboard]\nrun_forward = true\n", "name or a number"),
        ("[keyboard]\nrun_forward = 1.5\n", "name or a number"),
        ('[keyboard]\nrun_forward = ["W"]\n', "name or a number"),
        ('[keyboard]\nrun_forward = { modifier = "Shift" }\n', "no 'key'"),
        (
            '[keyboard]\nrun_forward = { key = "W", button = "Left click" }\n',
            "unknown field",
        ),
        ('[mouse]\njump = { key = "W" }\n', r"unknown field.*'button'"),
        (
            '[keyboard]\nrun_forward = { key = "W", modifier = "Alt" }\n',
            "unknown modifier",
        ),
        ('[keyboard]\nrun_forward = "W"\nRUN_FORWARD = "S"\n', "twice"),
        ("keyboard = 1\n", "must be a table"),
    ],
)
def test_a_config_that_is_wrong_says_where(text: str, message: str) -> None:
    with pytest.raises(ds2_bindings.ConfigError, match=message):
        ds2_bindings.parse_config(text)


def test_a_config_file_is_loaded_from_a_path(tmp_path: Path) -> None:
    path = _config(tmp_path, '[keyboard]\njump = "F"\n')
    assert ds2_bindings.load_config(path).keyboard == {
        int(Action.JUMP): KeyBinding(KEYS.parse("F"))
    }


def test_a_bad_config_file_names_the_problem(tmp_path: Path) -> None:
    path = _config(tmp_path, '[keyboard]\njump = "Nope"\n')
    with pytest.raises(ds2_bindings.ConfigError, match=r"not valid.*jump"):
        ds2_bindings.load_config(path)


def test_the_bundled_config_sets_every_action_of_both_tables() -> None:
    controls = ds2_bindings.load_config()
    assert {*controls.keyboard} == {int(action) for action in Action}
    assert len(controls.mouse) == 16
    assert {*controls.mouse} <= {int(action) for action in Action}


def test_the_bundled_config_is_the_documented_scheme() -> None:
    keyboard = ds2_bindings.load_config().keyboard
    chosen = {
        action: keyboard[int(action)]
        for action in (
            Action.TOGGLE_MENU_LEFT,
            Action.TOGGLE_MENU_RIGHT,
            Action.FUNCTION_1,
            Action.FUNCTION_2,
        )
    }
    assert chosen == {
        Action.TOGGLE_MENU_LEFT: KeyBinding(KEYS.parse("C")),
        Action.TOGGLE_MENU_RIGHT: KeyBinding(KEYS.parse("V")),
        Action.FUNCTION_1: KeyBinding(KEYS.parse("P")),
        Action.FUNCTION_2: KeyBinding(KEYS.parse("B")),
    }
    assert keyboard[int(Action.RUN_FORWARD)] == KeyBinding(KEYS.parse("W"))
    assert keyboard[int(Action.SWITCH_SPELLS)] == KeyBinding(KEYS.parse("Up"))


def test_controls_are_written_as_a_config_that_reads_back() -> None:
    controls = Controls(
        keyboard=START_KEYBOARD
        | {int(Action.INTERACT): KeyBinding(KEYS.parse("Enter"))},
        mouse=START_MOUSE | {99: MouseBinding(0x03)},
    )
    text = ds2_bindings.config_text(controls)
    assert ds2_bindings.parse_config(text) == controls
    assert 'toggle_menu_left = { key = "Left", modifier = "Shift" }' in text
    assert "walk = 0x7d" in text
    assert (
        'jump = { button = "Click mouse wheel", modifier = "Shift" }' in text
    )
    assert "action_99 = 0x03" in text
    assert text.index("[keyboard]") < text.index("[mouse]")
    assert text.endswith("\n")


def test_a_config_of_only_one_table_is_written_without_the_other() -> None:
    text = ds2_bindings.config_text(Controls(mouse=START_MOUSE))
    assert text.startswith("[mouse]\n")
    assert "[keyboard]" not in text
    assert ds2_bindings.config_text(Controls(keyboard=START_KEYBOARD))


def test_the_bundled_config_is_what_it_would_be_written_as() -> None:
    # So that it is in the order and form the program writes.
    controls = ds2_bindings.load_config()
    written = ds2_bindings.config_text(controls)
    assert ds2_bindings.parse_config(written) == controls
    bundled = (
        resources.files("steam_utility_launcher")
        / "resources"
        / "ds2_key_bindings.toml"
    )
    assert bundled.read_text(encoding="utf-8").endswith(written)


# --- running it


@pytest.mark.usefixtures("game_closed")
def test_sets_the_controls_and_backs_up_the_save(
    save: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = save.read_bytes()
    assert ds2_bindings.run(save=save, config=config) == 0
    assert _controls(save).keyboard == WANTED_KEYBOARD
    assert _controls(save).mouse == START_MOUSE
    (backup,) = _backups(save)
    assert backup.read_bytes() == original
    assert save.stat().st_mode & 0o777 == 0o640
    assert not list(save.parent.glob("*.sul-new"))
    output = capsys.readouterr().out
    assert "keyboard toggle_menu_left: Shift+Left -> C" in output
    assert "keyboard function_1: E -> P" in output
    assert "keyboard function_2: N -> B" in output
    assert "walk" not in output
    assert backup.name in output


@pytest.mark.usefixtures("game_closed")
def test_sets_mouse_controls_too(save: Path, tmp_path: Path) -> None:
    path = _config(
        tmp_path,
        '[mouse]\nattack_right_hand = { button = "Left click", modifier = "Ctrl" }\n',
    )
    assert ds2_bindings.run(save=save, config=path) == 0
    assert _controls(save).mouse[
        int(Action.ATTACK_RIGHT_HAND)
    ] == MouseBinding(LEFT_CLICK, CTRL)
    assert _controls(save).keyboard == START_KEYBOARD


@pytest.mark.usefixtures("game_closed")
def test_leaves_what_the_config_does_not_list_alone(
    save: Path, tmp_path: Path
) -> None:
    path = _config(tmp_path, '[keyboard]\nfunction_1 = "P"\n')
    assert ds2_bindings.run(save=save, config=path) == 0
    assert _controls(save).keyboard == {
        **START_KEYBOARD,
        int(Action.FUNCTION_1): KeyBinding(KEYS.parse("P")),
    }


@pytest.mark.usefixtures("game_closed")
def test_setting_a_plain_key_over_a_shifted_default_clears_the_shift(
    save: Path, tmp_path: Path
) -> None:
    path = _config(tmp_path, '[keyboard]\ntoggle_menu_left = "C"\n')
    assert ds2_bindings.run(save=save, config=path) == 0
    keyboard = _controls(save).keyboard
    assert keyboard[int(Action.TOGGLE_MENU_LEFT)] == KeyBinding(
        KEYS.parse("C")
    )
    assert (
        keyboard[int(Action.TOGGLE_MENU_RIGHT)]
        == START_KEYBOARD[int(Action.TOGGLE_MENU_RIGHT)]
    )


@pytest.mark.usefixtures("game_closed")
def test_does_nothing_when_the_controls_are_already_set(
    save: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ds2_bindings.run(save=save, config=config) == 0
    configured = save.read_bytes()
    capsys.readouterr()
    assert ds2_bindings.run(save=save, config=config) == 0
    assert save.read_bytes() == configured
    assert len(_backups(save)) == 1
    assert "already" in capsys.readouterr().out


def test_a_dry_run_says_what_would_change_and_changes_nothing(
    save: Path,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Not even the game running stops it: it only looks.
    monkeypatch.setattr(ds2_bindings, "game_is_running", lambda: True)
    original = save.read_bytes()
    assert ds2_bindings.run(save=save, config=config, dry_run=True) == 0
    assert save.read_bytes() == original
    assert not _backups(save)
    output = capsys.readouterr().out
    assert "function_1: E -> P" in output
    assert "dry run" in output


def test_refuses_while_the_game_is_running(
    save: Path,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(ds2_bindings, "game_is_running", lambda: True)
    original = save.read_bytes()
    with caplog.at_level(logging.ERROR):
        assert ds2_bindings.run(save=save, config=config) == 1
    assert save.read_bytes() == original
    assert not _backups(save)
    assert "Close Dark Souls II" in caplog.text


@pytest.mark.usefixtures("game_closed")
def test_an_action_the_save_lacks_changes_nothing(
    save: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _config(
        tmp_path, '[keyboard]\nfunction_1 = "P"\nrun_forward = "S"\n'
    )
    original = save.read_bytes()
    with caplog.at_level(logging.ERROR):
        assert ds2_bindings.run(save=save, config=path) == 1
    assert save.read_bytes() == original
    assert not _backups(save)
    assert "run_forward" in caplog.text


@pytest.mark.usefixtures("game_closed")
def test_a_bad_config_changes_nothing(
    save: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _config(tmp_path, '[keyboard]\nfunction_1 = "Nope"\n')
    original = save.read_bytes()
    with caplog.at_level(logging.ERROR):
        assert ds2_bindings.run(save=save, config=path) == 1
    assert save.read_bytes() == original
    assert not _backups(save)
    assert "Nope" in caplog.text


@pytest.mark.usefixtures("game_closed")
def test_a_file_that_is_not_a_save_is_left_alone(
    tmp_path: Path, config: Path, caplog: pytest.LogCaptureFixture
) -> None:
    junk = tmp_path / "junk.sl2"
    junk.write_bytes(b"not a save")
    with caplog.at_level(logging.ERROR):
        assert ds2_bindings.run(save=junk, config=config) == 1
    assert junk.read_bytes() == b"not a save"
    assert not _backups(junk)


def test_show_prints_the_controls_as_a_config_that_sets_them_back(
    save: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = save.read_bytes()
    assert ds2_bindings.run(save=save, show=True) == 0
    output = capsys.readouterr().out
    assert 'toggle_menu_left = { key = "Left", modifier = "Shift" }' in output
    assert "walk = 0x7d" in output
    assert save.read_bytes() == original
    assert not _backups(save)
    # What it prints is a config for exactly this save.
    assert ds2_bindings.parse_config(output) == SaveFile(original).controls


@pytest.mark.usefixtures("game_closed")
def test_what_show_printed_restores_the_controls(
    save: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ds2_bindings.run(save=save, show=True)
    saved = _config(tmp_path, capsys.readouterr().out, "saved.toml")
    original = _controls(save)
    ds2_bindings.run(save=save, config=_config(tmp_path, CONFIG_TEXT))
    assert _controls(save) != original
    assert ds2_bindings.run(save=save, config=saved) == 0
    assert _controls(save) == original


def test_a_missing_save_is_an_error(
    tmp_path: Path, config: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        assert ds2_bindings.run(save=tmp_path / "nope.sl2", config=config) == 1
    assert "Could not set the keys" in caplog.text


# --- finding the save and the game


def _steam(prefix: Path) -> Steam:
    return cast(
        "Steam",
        SimpleNamespace(game_wine_prefix=lambda *, game_id: prefix / game_id),
    )


def _save_in_prefix(prefix: Path, account: str) -> Path:
    directory = (
        prefix
        / "335300"
        / "drive_c"
        / "users"
        / "steamuser"
        / "AppData"
        / "Roaming"
        / "DarkSoulsII"
        / account
    )
    directory.mkdir(parents=True)
    path = directory / ds2_bindings.SAVE_FILE_NAME
    path.write_bytes(b"x")
    return path


def test_finds_the_save_in_the_games_prefix(tmp_path: Path) -> None:
    expected = _save_in_prefix(tmp_path, "011000010089b3de")
    assert ds2_bindings.find_save(_steam(tmp_path)) == expected


def test_finding_no_save_says_how_to_make_one(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="--create"):
        ds2_bindings.find_save(_steam(tmp_path))


def test_finding_several_saves_asks_which(tmp_path: Path) -> None:
    _save_in_prefix(tmp_path, "011000010089b3de")
    _save_in_prefix(tmp_path, "0110000100000001")
    with pytest.raises(FileNotFoundError, match="--save"):
        ds2_bindings.find_save(_steam(tmp_path))


def _fake_proc(root: Path, entries: dict[str, bytes]) -> Callable[[], bool]:
    for name, command_line in entries.items():
        (root / name).mkdir()
        (root / name / "cmdline").write_bytes(command_line)
    return ds2_bindings.game_is_running


def test_sees_the_game_in_the_process_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ds2_bindings, "_PROCESSES", tmp_path)
    check = _fake_proc(
        tmp_path,
        {
            "1": b"/usr/bin/python3\x00proton\x00run",
            "self": b"DarkSoulsII.exe",
            "2": b"Z:\\game\\DarkSoulsII.exe\x00",
        },
    )
    assert check()
    (tmp_path / "2" / "cmdline").write_bytes(b"/usr/bin/other")
    assert not check()


def test_ignores_its_own_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ds2_bindings, "_PROCESSES", tmp_path)
    check = _fake_proc(tmp_path, {str(os.getpid()): b"DarkSoulsII.exe"})
    assert not check()


def test_cannot_tell_without_a_process_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ds2_bindings, "_PROCESSES", tmp_path / "missing")
    with pytest.raises(OSError, match="cannot tell"):
        ds2_bindings.game_is_running()


# --- the command line


@pytest.mark.usefixtures("game_closed")
def test_the_command_is_wired_into_the_launcher(
    save: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = ["ds2-bindings", "--save", str(save), "--config", str(config)]
    assert application.main([*arguments, "--dry-run"]) == 0
    assert _controls(save).keyboard == START_KEYBOARD
    assert application.main(arguments) == 0
    assert _controls(save).keyboard == WANTED_KEYBOARD
    capsys.readouterr()
    assert (
        application.main(["ds2-bindings", "--save", str(save), "--show"]) == 0
    )
    assert 'function_1 = "P"' in capsys.readouterr().out


# --- making a save

STEAM_ID = 76561197969290206
FOLDER = "011000010089b3de"


def _steam_with_account(prefix: Path, account: int = STEAM_ID) -> Steam:
    return cast(
        "Steam",
        SimpleNamespace(
            game_wine_prefix=lambda *, game_id: prefix / game_id,
            location=SimpleNamespace(account_steam_id=lambda: account),
        ),
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("76561197969290206", STEAM_ID),
        (" 76561197969290206 ", STEAM_ID),
        ("011000010089b3de", STEAM_ID),
        ("011000010089B3DE", STEAM_ID),
        ("0110000100123456", 0x0110000100123456),  # hex with no letters
    ],
)
def test_a_steam_id_is_the_digits_or_the_folders_hex(
    text: str, expected: int
) -> None:
    assert ds2_bindings.parse_steam_id(text) == expected


@pytest.mark.parametrize(
    "bad", ["", "123", "765611979692902060", "011000010089b3d", "xyz", "0x1"]
)
def test_something_that_is_not_a_steam_id_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="not a Steam id"):
        ds2_bindings.parse_steam_id(bad)


@pytest.mark.usefixtures("game_closed")
def test_makes_a_new_save_with_the_controls_and_the_terms_accepted(
    tmp_path: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "new" / "deeper" / ds2_bindings.SAVE_FILE_NAME
    assert (
        ds2_bindings.run(
            save=path, config=config, create=True, steam_id=STEAM_ID
        )
        == 0
    )
    save = SaveFile.read(path)
    assert save.terms_accepted
    keyboard = save.controls.keyboard
    assert keyboard[int(Action.FUNCTION_1)] == KeyBinding(KEYS.parse("P"))
    assert keyboard[int(Action.TOGGLE_MENU_LEFT)] == KeyBinding(
        KEYS.parse("C")
    )
    assert keyboard[int(Action.RUN_FORWARD)] == KeyBinding(KEYS.parse("W"))
    output = capsys.readouterr().out
    assert FOLDER in output
    assert "terms and conditions accepted" in output
    assert "function_1: E -> P" in output
    assert "Made " in output
    assert not list(path.parent.glob("*.sul-*"))  # no backup, no leftovers


@pytest.mark.usefixtures("game_closed")
def test_a_new_save_can_leave_the_terms_for_the_game_to_ask(
    tmp_path: Path, config: Path
) -> None:
    path = tmp_path / ds2_bindings.SAVE_FILE_NAME
    assert (
        ds2_bindings.run(
            save=path,
            config=config,
            create=True,
            accept_terms=False,
            steam_id=STEAM_ID,
        )
        == 0
    )
    assert not SaveFile.read(path).terms_accepted


@pytest.mark.usefixtures("game_closed")
def test_a_new_save_is_not_made_over_one_that_is_there(
    save: Path, config: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original = save.read_bytes()
    with caplog.at_level(logging.ERROR):
        assert (
            ds2_bindings.run(
                save=save, config=config, create=True, steam_id=STEAM_ID
            )
            == 1
        )
    assert save.read_bytes() == original
    assert "already exists" in caplog.text
    assert not _backups(save)


@pytest.mark.usefixtures("game_closed")
def test_a_new_save_goes_in_the_account_folder_of_the_games_prefix(
    tmp_path: Path, config: Path
) -> None:
    steam = _steam_with_account(tmp_path)
    assert ds2_bindings.run(config=config, create=True, steam=steam) == 0
    expected = (
        ds2_bindings.saves_directory(steam)
        / FOLDER
        / ds2_bindings.SAVE_FILE_NAME
    )
    assert expected.is_file()
    # Where the game, and so find_save, looks for it.
    assert ds2_bindings.find_save(steam) == expected


@pytest.mark.usefixtures("game_closed")
def test_a_new_save_is_for_the_account_asked_for(
    tmp_path: Path, config: Path
) -> None:
    steam = _steam_with_account(tmp_path, account=76561197960265729)
    other = 76561197969290206
    assert (
        ds2_bindings.run(
            config=config, create=True, steam=steam, steam_id=other
        )
        == 0
    )
    assert (ds2_bindings.saves_directory(steam) / FOLDER).is_dir()


def test_a_new_save_needs_to_know_the_account(
    tmp_path: Path, config: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / ds2_bindings.SAVE_FILE_NAME
    with caplog.at_level(logging.ERROR):
        assert ds2_bindings.run(save=path, config=config, create=True) == 1
    assert "--steam-id" in caplog.text
    assert not path.exists()


def test_a_steam_that_cannot_say_the_account_is_an_error(
    tmp_path: Path, config: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def fail() -> int:
        msg = "Could not tell which Steam account is in use"
        raise SteamError(msg)

    steam = cast(
        "Steam",
        SimpleNamespace(
            game_wine_prefix=lambda *, game_id: tmp_path / game_id,
            location=SimpleNamespace(account_steam_id=fail),
        ),
    )
    with caplog.at_level(logging.ERROR):
        assert ds2_bindings.run(config=config, create=True, steam=steam) == 1
    assert "which Steam account" in caplog.text


def test_a_dry_run_of_making_a_save_writes_nothing(
    tmp_path: Path,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ds2_bindings, "game_is_running", lambda: True)
    path = tmp_path / "new" / ds2_bindings.SAVE_FILE_NAME
    assert (
        ds2_bindings.run(
            save=path,
            config=config,
            create=True,
            dry_run=True,
            steam_id=STEAM_ID,
        )
        == 0
    )
    assert not path.parent.exists()
    assert "dry run" in capsys.readouterr().out


def test_a_save_is_not_made_while_the_game_is_running(
    tmp_path: Path,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(ds2_bindings, "game_is_running", lambda: True)
    path = tmp_path / ds2_bindings.SAVE_FILE_NAME
    with caplog.at_level(logging.ERROR):
        assert (
            ds2_bindings.run(
                save=path, config=config, create=True, steam_id=STEAM_ID
            )
            == 1
        )
    assert not path.exists()
    assert "Close Dark Souls II" in caplog.text


@pytest.mark.usefixtures("game_closed")
def test_a_save_made_with_no_config_has_the_bundled_scheme(
    tmp_path: Path,
) -> None:
    path = tmp_path / ds2_bindings.SAVE_FILE_NAME
    assert ds2_bindings.run(save=path, create=True, steam_id=STEAM_ID) == 0
    assert SaveFile.read(path).controls == ds2_bindings.load_config()


# --- the command line, making a save


@pytest.mark.usefixtures("game_closed")
def test_the_command_line_makes_a_save(tmp_path: Path, config: Path) -> None:
    path = tmp_path / ds2_bindings.SAVE_FILE_NAME
    arguments = [
        "ds2-bindings",
        "--create",
        "--save",
        str(path),
        "--steam-id",
        FOLDER,
        "--config",
        str(config),
    ]
    assert application.main([*arguments, "--no-accept-terms"]) == 0
    assert not SaveFile.read(path).terms_accepted
    path.unlink()
    assert application.main(arguments) == 0
    assert SaveFile.read(path).terms_accepted


@pytest.mark.parametrize(
    "arguments",
    [
        ["--show", "--create"],
        ["--steam-id", FOLDER],
        ["--create", "--steam-id", "nonsense"],
    ],
)
def test_the_command_line_refuses_options_that_make_no_sense(
    arguments: list[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit):
        application.main(
            ["ds2-bindings", "--save", str(tmp_path / "x.sl2"), *arguments]
        )


def test_off_linux_the_save_and_account_have_to_be_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    with pytest.raises(SystemExit):
        application.main(["ds2-bindings", "--create"])
    with pytest.raises(SystemExit):
        application.main(
            ["ds2-bindings", "--create", "--save", str(tmp_path / "x.sl2")]
        )
