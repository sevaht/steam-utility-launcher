from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypedDict, Unpack

import pytest

from steam_utility_launcher import application, ds2_controller
from steam_utility_launcher.host import LinuxHost
from steam_utility_launcher.steam import (
    Steam,
    SteamClient,
    SteamError,
    SteamLocation,
)
from steam_utility_launcher.steam_input import (
    CONTROLLER_TYPES,
    ControllerConfigs,
    ControllerLayout,
    ControllerType,
    parse_controller_type,
    plan_layouts,
    selected_template,
)

from .steam_helper import make_location

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

CREATOR = 76561197969290206
ACCOUNT = 9024478
APP = 335300
LOGIN_USERS = (
    '"users"\n{\n'
    f'\t"{CREATOR}"\n\t{{\n\t\t"AccountName"\t\t"someone"\n'
    '\t\t"MostRecent"\t\t"1"\n\t}\n}\n'
)


class _Calls:
    def __init__(self) -> None:
        self.events: list[str] = []


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
def calls() -> _Calls:
    return _Calls()


@pytest.fixture
def steam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: _Calls
) -> Steam:
    config = tmp_path / "config"
    config.mkdir()
    (config / "loginusers.vdf").write_text(LOGIN_USERS, encoding="utf-8")
    monkeypatch.setattr(SteamClient, "is_running", staticmethod(lambda: False))
    monkeypatch.setattr(
        SteamClient, "shut_down", lambda _self: calls.events.append("closed")
    )
    monkeypatch.setattr(
        SteamClient, "start", lambda _self: calls.events.append("started")
    )
    return Steam(location=make_location(tmp_path), compatibility_tools=[])


class _Asked(TypedDict, total=False):
    """What a test asks `ds2 controller` for besides the Steam it is run on."""

    layout_file: Path | None
    controller_types: Sequence[ControllerType]
    keep_device_choices: bool
    assume_yes: bool
    dry_run: bool
    restart: bool
    ask: Callable[[str], str]


def _run(steam: Steam, **asked: Unpack[_Asked]) -> None:
    request = ds2_controller.ControllerRequest(
        location=steam.location,
        client=SteamClient(steam.location, LinuxHost()),
        **asked,
    )
    ds2_controller.LayoutInstaller(request).run()


def _running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SteamClient, "is_running", staticmethod(lambda: True))


def _configs(steam: Steam) -> ControllerConfigs:
    return ControllerConfigs(steam.location, ACCOUNT)


def _selected(steam: Steam) -> str | None:
    path = _configs(steam).selection_path("controller_generic")
    return selected_template(path.read_text(encoding="utf-8"), APP)


def _no_input(_prompt: str) -> str:
    pytest.fail("it should not have asked")


def test_sets_up_a_layout_for_a_game_that_has_none(
    steam: Steam, calls: _Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(steam, ask=_no_input)
    layout = ControllerLayout.bundled()
    assert _selected(steam) == f"CLOUD_{APP}/{layout.name}"
    path = _configs(steam).layout_path(APP, layout)
    assert f'"creator"\t\t"{CREATOR}"' in path.read_text(encoding="utf-8")
    assert calls.events == []  # Steam was not running: left alone
    output = capsys.readouterr().out
    assert layout.title in output
    assert "controller_generic" in output


def test_leaves_it_be_when_steam_already_uses_it(
    steam: Steam,
    calls: _Calls,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run(steam, ask=_no_input)
    capsys.readouterr()
    _running(monkeypatch)  # even with Steam up, nothing needs closing
    _run(steam, ask=_no_input)
    assert calls.events == []
    assert "already uses" in capsys.readouterr().out
    assert not list(_configs(steam).directory.rglob("*.sul-backup-*"))


def test_closes_steam_to_change_it_and_starts_it_again(
    steam: Steam, calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(monkeypatch)
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    _run(steam, ask=ask)
    assert len(asked) == 1
    assert "controller layout" in asked[0]
    assert calls.events == ["closed", "started"]
    assert _selected(steam) is not None


def test_a_yes_closes_steam_without_asking(
    steam: Steam, calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(monkeypatch)
    _run(steam, assume_yes=True, ask=_no_input)
    assert calls.events == ["closed", "started"]


def test_steam_can_be_left_closed(
    steam: Steam,
    calls: _Calls,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _running(monkeypatch)
    _run(steam, assume_yes=True, restart=False, ask=_no_input)
    assert calls.events == ["closed"]
    assert "Start Steam again" in capsys.readouterr().out


def test_changes_nothing_if_steam_is_not_allowed_to_close(
    steam: Steam, calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(monkeypatch)
    with pytest.raises(SteamError, match="Close Steam"):
        _run(steam, ask=lambda _p: "n")
    assert calls.events == []
    assert not _configs(steam).directory.exists()


def test_a_dry_run_changes_nothing_and_leaves_steam_up(
    steam: Steam,
    calls: _Calls,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _running(monkeypatch)
    _run(steam, dry_run=True, ask=_no_input)
    assert calls.events == []
    assert not _configs(steam).directory.exists()
    output = capsys.readouterr().out
    assert "dry run" in output
    assert "controller_generic" in output


def test_says_what_it_was_choosing_instead(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    selection = _configs(steam).selection_path("controller_generic")
    selection.parent.mkdir(parents=True)
    selection.write_text(
        '"controller_config"\n{\n\t"335300"\n\t{\n'
        '\t\t"template"\t\t"CLOUD_335300/other_0"\n\t}\n}\n',
        encoding="utf-8",
    )
    _run(steam, ask=_no_input)
    output = capsys.readouterr().out
    assert "was CLOUD_335300/other_0" in output
    assert "Backed up: configset_controller_generic.vdf.sul-backup-" in output


def test_uses_a_layout_file_of_the_users_instead(
    steam: Steam, tmp_path: Path
) -> None:
    mine = tmp_path / "mine.vdf"
    mine.write_text(
        ControllerLayout.bundled().text.replace(
            "DS2 SOTFS (sevaht)", "My Own Layout"
        ),
        encoding="utf-8",
    )
    _run(steam, layout_file=mine, ask=_no_input)
    assert _selected(steam) == "CLOUD_335300/my own layout_0"


def test_a_layout_file_that_is_not_one_is_an_error(
    steam: Steam, tmp_path: Path
) -> None:
    junk = tmp_path / "junk.vdf"
    junk.write_text("not a layout", encoding="utf-8")
    with pytest.raises(SteamError, match="controller_type"):
        _run(steam, layout_file=junk)
    assert not _configs(steam).directory.exists()


def test_steam_with_no_accounts_is_an_error(
    steam: Steam, tmp_path: Path
) -> None:
    (tmp_path / "config" / "loginusers.vdf").unlink()
    with pytest.raises(SteamError, match="Could not read Steam's accounts"):
        _run(steam)


def test_what_it_writes_is_what_a_second_run_finds_done(steam: Steam) -> None:
    _run(steam, ask=_no_input)
    base = ControllerLayout.bundled()
    assert (
        plan_layouts(
            _configs(steam),
            APP,
            [base.for_type(kind) for kind in CONTROLLER_TYPES],
            creator=CREATOR,
        )
        == []
    )


class _UnknownAccount(SteamLocation):
    """A Steam that can't say which account is in use."""

    def account_steam_id(self) -> int:
        msg = "Could not tell which Steam account is in use"
        raise SteamError(msg)


def test_a_steam_that_cannot_say_the_account_is_an_error(steam: Steam) -> None:
    broken = Steam(
        location=_UnknownAccount(
            root=steam.location.root, system_config_dirs=[]
        ),
        compatibility_tools=[],
    )
    with pytest.raises(SteamError, match="which Steam account"):
        _run(broken)


# --- the command line


def test_the_command_is_wired_into_the_launcher(
    steam: Steam,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        SteamLocation,
        "from_detection",
        staticmethod(lambda _host=None: steam.location),
    )
    assert application.main(["ds2", "controller", "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out
    assert application.main(["ds2", "controller", "-y"]) == 0
    assert _selected(steam) is not None


# --- every kind of controller


def _selected_for(steam: Steam, controller_type: str) -> str | None:
    path = _configs(steam).selection_path(controller_type)
    if not path.exists():
        return None
    return selected_template(path.read_text(encoding="utf-8"), APP)


def test_sets_the_layout_for_every_kind_of_controller_by_default(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(steam, ask=_no_input)
    base = ControllerLayout.bundled()
    for kind in CONTROLLER_TYPES:
        layout = base.for_type(kind)
        assert _selected_for(steam, kind.name) == f"CLOUD_{APP}/{layout.name}"
        assert _configs(steam).layout_path(APP, layout).is_file()
    output = capsys.readouterr().out
    for kind in CONTROLLER_TYPES:
        assert kind.name in output


def test_the_layouts_for_the_kinds_are_the_same_buttons(steam: Steam) -> None:
    _run(steam, ask=_no_input)
    base = ControllerLayout.bundled()
    reference = base.text.count("key_press")
    for kind in CONTROLLER_TYPES:
        path = _configs(steam).layout_path(APP, base.for_type(kind))
        text = path.read_text(encoding="utf-8")
        assert text.count("key_press") == reference
        assert f'"controller_type"\t\t"{kind.name}"' in text


def test_it_can_be_limited_to_some_kinds(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    kinds = [parse_controller_type("ps5"), parse_controller_type("generic")]
    _run(steam, controller_types=kinds, ask=_no_input)
    assert _selected_for(steam, "controller_ps5") is not None
    assert _selected_for(steam, "controller_generic") is not None
    assert _selected_for(steam, "controller_xboxone") is None
    assert not _configs(steam).selection_path("controller_xboxone").exists()
    assert "controller_xboxone" not in capsys.readouterr().out


def test_a_second_run_for_more_kinds_adds_only_those(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    first = [parse_controller_type("generic")]
    _run(steam, controller_types=first, ask=_no_input)
    capsys.readouterr()
    _run(steam, ask=_no_input)
    output = capsys.readouterr().out
    assert "controller_generic" not in output.split("Steam uses")[0]
    assert "controller_ps5" in output
    assert not list(_configs(steam).directory.rglob("*.sul-backup-*"))


# --- choices for particular controllers


def _device_choice(steam: Steam, template: str) -> None:
    configs = _configs(steam)
    configs.directory.mkdir(parents=True, exist_ok=True)
    (configs.directory / "configset_2dc8-310a-89b3de.vdf").write_text(
        '"controller_config"\n{\n\t"335300"\n\t{\n'
        f'\t\t"template"\t\t"{template}"\n\t}}\n}}\n',
        encoding="utf-8",
    )


def test_a_choice_for_one_controller_that_could_win_is_pointed_out(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    _device_choice(steam, "CLOUD_335300/someone elses_0")
    _run(steam, ask=_no_input)
    output = capsys.readouterr().out
    assert "configset_2dc8-310a-89b3de.vdf" in output
    assert "CLOUD_335300/someone elses_0" in output
    assert "may take priority" in output


def test_a_choice_for_one_controller_of_this_layout_is_not_a_concern(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = ControllerLayout.bundled()
    _device_choice(steam, f"CLOUD_{APP}/{layout.name}")
    _run(steam, ask=_no_input)
    assert "particular controllers" not in capsys.readouterr().out


def test_the_choice_for_one_controller_is_pointed_out_in_a_dry_run_too(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    _device_choice(steam, "CLOUD_335300/other_0")
    _run(steam, dry_run=True)
    assert "may take priority" in capsys.readouterr().out


def test_the_choice_for_one_controller_is_pointed_out_when_all_is_set(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(steam, ask=_no_input)
    _device_choice(steam, "CLOUD_335300/other_0")
    capsys.readouterr()
    _run(steam, ask=_no_input)
    output = capsys.readouterr().out
    assert "already uses" in output
    assert "may take priority" in output


# --- the command line, kinds of controller


def test_the_command_line_limits_the_kinds(
    steam: Steam, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        SteamLocation,
        "from_detection",
        staticmethod(lambda _host=None: steam.location),
    )
    arguments = [
        "ds2",
        "controller",
        "-y",
        "--controller-type",
        "ps5",
        "--controller-type",
        "Switch Pro",
    ]
    assert application.main(arguments) == 0
    assert _selected_for(steam, "controller_ps5") is not None
    assert _selected_for(steam, "controller_switch_pro") is not None
    assert _selected_for(steam, "controller_generic") is None


def test_the_command_line_refuses_a_kind_there_is_not(
    steam: Steam, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        SteamLocation,
        "from_detection",
        staticmethod(lambda _host=None: steam.location),
    )
    with pytest.raises(SystemExit):
        application.main(["ds2", "controller", "--controller-type", "atari"])


# --- choices for particular controllers are changed too


def _saved_layout(steam: Steam, name: str, kind: str) -> None:
    folder = _configs(steam).directory / str(APP)
    folder.mkdir(parents=True, exist_ok=True)
    text = ControllerLayout.bundled().text
    (folder / f"{name}.vdf").write_text(
        text.replace("controller_generic", kind), encoding="utf-8"
    )


def test_a_choice_for_one_controller_is_changed_to_this_layout(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    _saved_layout(steam, "old layout_0", "controller_generic")
    _device_choice(steam, "CLOUD_335300/old layout_0")
    _run(steam, ask=_no_input)
    device = _configs(steam).directory / "configset_2dc8-310a-89b3de.vdf"
    layout = ControllerLayout.bundled()
    assert selected_template(device.read_text(encoding="utf-8"), APP) == (
        f"CLOUD_{APP}/{layout.name}"
    )
    output = capsys.readouterr().out
    assert "configset_2dc8-310a-89b3de.vdf" in output
    assert "(was CLOUD_335300/old layout_0)" in output
    assert "may take priority" not in output
    assert list(device.parent.glob(f"{device.name}.sul-backup-*"))
    # The old layout itself is left where it was.
    assert (_configs(steam).directory / "335300" / "old layout_0.vdf").exists()


def test_a_second_run_finds_the_controller_done_too(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    _saved_layout(steam, "old layout_0", "controller_generic")
    _device_choice(steam, "CLOUD_335300/old layout_0")
    _run(steam, ask=_no_input)
    capsys.readouterr()
    _run(steam, ask=_no_input)
    assert "already uses" in capsys.readouterr().out


def test_a_choice_for_one_controller_can_be_left_as_it_is(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    _saved_layout(steam, "old layout_0", "controller_generic")
    _device_choice(steam, "CLOUD_335300/old layout_0")
    device = _configs(steam).directory / "configset_2dc8-310a-89b3de.vdf"
    before = device.read_text(encoding="utf-8")
    _run(steam, keep_device_choices=True, ask=_no_input)
    assert device.read_text(encoding="utf-8") == before
    output = capsys.readouterr().out
    assert (
        "configset_2dc8-310a-89b3de.vdf chooses CLOUD_335300/old layout_0"
        in output
    )
    assert "may take priority" in output


def test_a_dry_run_says_which_controller_would_change(
    steam: Steam, capsys: pytest.CaptureFixture[str]
) -> None:
    _saved_layout(steam, "old layout_0", "controller_generic")
    _device_choice(steam, "CLOUD_335300/old layout_0")
    device = _configs(steam).directory / "configset_2dc8-310a-89b3de.vdf"
    before = device.read_text(encoding="utf-8")
    _run(steam, dry_run=True)
    assert device.read_text(encoding="utf-8") == before
    assert "for the controller in configset_2dc8-310a-89b3de.vdf" in (
        capsys.readouterr().out
    )


def test_only_a_controller_choice_to_change_still_closes_steam(
    steam: Steam, calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(steam, ask=_no_input)
    _saved_layout(steam, "old layout_0", "controller_generic")
    _device_choice(steam, "CLOUD_335300/old layout_0")
    _running(monkeypatch)
    calls.events.clear()
    _run(steam, assume_yes=True)
    assert calls.events == ["closed", "started"]


def test_the_command_line_can_leave_controller_choices(
    steam: Steam, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        SteamLocation,
        "from_detection",
        staticmethod(lambda _host=None: steam.location),
    )
    _saved_layout(steam, "old layout_0", "controller_generic")
    _device_choice(steam, "CLOUD_335300/old layout_0")
    device = _configs(steam).directory / "configset_2dc8-310a-89b3de.vdf"
    before = device.read_text(encoding="utf-8")
    assert (
        application.main(["ds2", "controller", "-y", "--keep-device-choices"])
        == 0
    )
    assert device.read_text(encoding="utf-8") == before
    assert application.main(["ds2", "controller", "-y"]) == 0
    assert device.read_text(encoding="utf-8") != before
