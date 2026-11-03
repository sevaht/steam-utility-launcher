from __future__ import annotations

import collections
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, cast

import pytest
import vdf  # type: ignore[import-untyped]

from steam_utility_launcher import steam_shortcuts
from steam_utility_launcher.steam import (
    CompatibilityTool,
    Steam,
    SteamError,
    SteamLocation,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

STEAM_ID64 = 76561197969290206
ACCOUNT = STEAM_ID64 - 76561197960265728  # 9024478

CONFIG = (
    '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n'
    '\t\t\t"Steam"\n\t\t\t{\n\t\t\t\t"CompatToolMapping"\n\t\t\t\t{\n'
    '\t\t\t\t\t"367520"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"proton_hotfix"\n'
    '\t\t\t\t\t\t"config"\t\t""\n\t\t\t\t\t\t"priority"\t\t"250"\n'
    '\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t\t"depots"\n\t\t\t\t{\n\t\t\t\t}\n'
    "\t\t\t}\n\t\t}\n\t}\n}\n"
)


def _tool(root: Path, name: str) -> CompatibilityTool:
    install_path = root / "compatibilitytools.d" / name
    install_path.mkdir(parents=True)
    return CompatibilityTool(
        internal_name=name,
        manifest_vdf=install_path / "toolmanifest.vdf",
        install_path=install_path,
        display_name=name,
        binary_path=install_path / "proton",
        binary_argument_template=["%verb%"],
    )


def _steam(root: Path, *tool_names: str) -> Steam:
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "config.vdf").write_text(CONFIG)
    (config / "loginusers.vdf").write_text(
        f'"users"\n{{\n\t"{STEAM_ID64}"\n\t{{\n\t\t"AccountName"\t\t"x"\n'
        '\t\t"MostRecent"\t\t"1"\n\t}\n}\n'
    )
    return Steam(
        location=SteamLocation(root=root, system_config_dirs=[]),
        compatibility_tools=[_tool(root, name) for name in tool_names],
    )


def _shortcuts_path(root: Path) -> Path:
    return root / "userdata" / str(ACCOUNT) / "config" / "shortcuts.vdf"


def _write_shortcuts(root: Path, *entries: dict[str, object]) -> None:
    path = _shortcuts_path(root)
    path.parent.mkdir(parents=True)
    data = collections.OrderedDict(
        shortcuts=collections.OrderedDict(
            (str(index), collections.OrderedDict(entry))
            for index, entry in enumerate(entries)
        )
    )
    path.write_bytes(vdf.binary_dumps(data))


LAUNCHED: list[int] = []
_REAL_LAUNCH = SteamLocation.launch_game


def _client_running(
    monkeypatch: pytest.MonkeyPatch, running: Callable[[], bool]
) -> None:
    monkeypatch.setattr(
        SteamLocation, "is_client_running", staticmethod(running)
    )


@pytest.fixture(autouse=True)
def _steam_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    _client_running(monkeypatch, lambda: False)
    LAUNCHED.clear()
    monkeypatch.setattr(
        SteamLocation,
        "launch_game",
        lambda _location, app_id: LAUNCHED.append(app_id),
    )


def _ensure(steam: Steam, **kwargs: object) -> int | None:
    options: dict[str, object] = {
        "name": "ROTK Launcher",
        "program": ["sul"],
        "mode": "rotk-launcher",
        "options": [],
        "launch": False,
    }
    options.update(kwargs)
    return steam_shortcuts.ensure_shortcut(steam, **options)  # type: ignore[arg-type]


def test_app_ids_have_the_high_bit_set_and_depend_on_the_name() -> None:
    first = steam_shortcuts.shortcut_app_id("", "ROTK Launcher")
    second = steam_shortcuts.shortcut_app_id("", "Another")
    assert first & 0x80000000
    assert first == steam_shortcuts.shortcut_app_id("", "ROTK Launcher")
    assert first != second


def test_app_ids_are_stored_signed_and_read_back_unsigned() -> None:
    app_id = steam_shortcuts.shortcut_app_id("", "ROTK Launcher")
    assert steam_shortcuts._unsigned(steam_shortcuts._signed(app_id)) == app_id
    assert steam_shortcuts._signed(app_id) < 0


def test_the_account_is_the_one_that_signed_in_last(tmp_path: Path) -> None:
    steam = _steam(tmp_path)
    account = steam.location.account_directory()
    assert account == tmp_path / "userdata" / str(ACCOUNT)


def test_several_accounts_none_the_latest_is_refused(tmp_path: Path) -> None:
    steam = _steam(tmp_path)
    (tmp_path / "config" / "loginusers.vdf").write_text(
        '"users"\n{\n\t"76561197969290206"\n\t{\n\t}\n'
        '\t"76561197969290207"\n\t{\n\t}\n}\n'
    )
    with pytest.raises(SteamError, match="which Steam"):
        steam.location.account_directory()


def test_a_compat_tool_is_added_without_touching_the_rest() -> None:
    changed = steam_shortcuts.with_compat_tool(CONFIG, 2288878336, "SVT-GE")
    assert steam_shortcuts.compat_tool_of(changed, 2288878336) == "SVT-GE"
    assert steam_shortcuts.compat_tool_of(changed, 367520) == "proton_hotfix"
    expected = vdf.loads(CONFIG)
    got = vdf.loads(changed)
    mapping = got["InstallConfigStore"]["Software"]["Valve"]["Steam"]
    del mapping["CompatToolMapping"]["2288878336"]
    assert got == expected
    # Only added lines: every original line is still there, in order.
    original = CONFIG.splitlines()
    remaining = iter(changed.splitlines())
    assert all(line in remaining for line in original)


def test_a_config_without_a_mapping_section_is_refused() -> None:
    with pytest.raises(steam_shortcuts.ShortcutError, match="CompatTool"):
        steam_shortcuts.with_compat_tool('"A"\n{\n}\n', 1, "t")


def test_adding_a_shortcut_chooses_the_tool_for_it_alone(
    tmp_path: Path,
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    default = (
        '\t\t\t\t\t"0"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"proton_experimental"\n'
        '\t\t\t\t\t\t"config"\t\t""\n\t\t\t\t\t\t"priority"\t\t"75"\n'
        "\t\t\t\t\t}\n"
    )
    config_path = tmp_path / "config" / "config.vdf"
    mapping = '\t\t\t\t"CompatToolMapping"\n\t\t\t\t{\n'
    config_path.write_text(CONFIG.replace(mapping, mapping + default))
    assert _ensure(steam)
    app_id = steam_shortcuts.shortcut_app_id("/usr/bin/true", "ROTK Launcher")
    config = config_path.read_text()
    assert steam_shortcuts.compat_tool_of(config, 0) == "proton_experimental"
    assert steam_shortcuts.compat_tool_of(config, 367520) == "proton_hotfix"
    assert steam_shortcuts.compat_tool_of(config, app_id) == "SVT-GE-Proton-11"


def test_a_new_shortcut_is_added_with_the_tool_and_a_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-9", "SVT-GE-Proton-11")
    other = steam_shortcuts.new_entry(
        name="Other", launch_options="x", icon=None
    )
    _write_shortcuts(tmp_path, other)
    assert _ensure(steam)
    data = vdf.binary_loads(_shortcuts_path(tmp_path).read_bytes())
    entries = list(data["shortcuts"].values())
    assert [entry["AppName"] for entry in entries] == [
        "Other",
        "ROTK Launcher",
    ]
    added = entries[1]
    assert added["LaunchOptions"] == "sul rotk-launcher -- %command%"
    app_id = added["appid"] & 0xFFFFFFFF
    config = (tmp_path / "config" / "config.vdf").read_text()
    # The newest SVT-GE-Proton is chosen (by number: 11 is newer than 9).
    assert steam_shortcuts.compat_tool_of(config, app_id) == "SVT-GE-Proton-11"
    assert (
        len(list((tmp_path / "config").glob("config.vdf.sul-backup-*"))) == 1
    )
    assert (
        len(list(_shortcuts_path(tmp_path).parent.glob("*.sul-backup-*"))) == 1
    )
    assert "Added" in capsys.readouterr().out


def test_an_existing_shortcut_is_left_alone_and_steam_is_not_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    existing = steam_shortcuts.new_entry(
        name="ROTK Launcher", launch_options="mine --max-fps 150", icon=None
    )
    _write_shortcuts(tmp_path, existing)
    before = _shortcuts_path(tmp_path).read_bytes()
    config_before = (tmp_path / "config" / "config.vdf").read_text()
    _client_running(monkeypatch, lambda: True)

    def fail(*_a: object, **_k: object) -> None:
        pytest.fail("Steam must not be closed for an entry that is there")

    monkeypatch.setattr(SteamLocation, "shut_down_client", fail)
    assert _ensure(steam, ask=fail)
    assert _shortcuts_path(tmp_path).read_bytes() == before
    assert (tmp_path / "config" / "config.vdf").read_text() == config_before
    assert "already in your Steam library" in capsys.readouterr().out


def test_an_entry_with_another_name_is_found_by_its_launch_options_and_renamed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    entry = steam_shortcuts.new_entry(
        name="Z1 mod", launch_options="/x/sul rotk-launcher -- ", icon=None
    )
    _write_shortcuts(tmp_path, entry)
    _client_running(monkeypatch, lambda: False)
    app_id = entry["appid"]
    assert _ensure(steam)
    saved = steam_shortcuts.load_shortcuts(_shortcuts_path(tmp_path))
    entries = list(cast("dict[str, Any]", saved["shortcuts"]).values())
    assert len(entries) == 1
    assert entries[0]["AppName"] == "ROTK Launcher"
    assert entries[0]["appid"] == app_id  # so it keeps its prefix


def test_steam_is_closed_after_asking_and_only_then_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    running = [True]
    _client_running(monkeypatch, lambda: running[0])
    closed: list[bool] = []

    def shut_down(_location: object) -> None:
        closed.append(True)
        running[0] = False

    monkeypatch.setattr(SteamLocation, "shut_down_client", shut_down)
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        assert not _shortcuts_path(tmp_path).exists()  # nothing yet
        return "y"

    assert _ensure(steam, ask=ask)
    assert closed == [True]
    assert len(prompts) == 1
    assert _shortcuts_path(tmp_path).exists()


def test_declining_to_close_steam_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    _client_running(monkeypatch, lambda: True)
    with caplog.at_level("ERROR"):
        assert not _ensure(steam, ask=lambda _prompt: "n")
    assert "Close Steam" in caplog.text
    assert not _shortcuts_path(tmp_path).exists()


def test_no_answer_available_counts_as_no(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    _client_running(monkeypatch, lambda: True)

    def no_input(_prompt: str) -> str:
        raise EOFError

    assert not _ensure(steam, ask=no_input)
    assert not _shortcuts_path(tmp_path).exists()


def test_yes_closes_steam_without_asking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    running = [True]
    _client_running(monkeypatch, lambda: running[0])
    monkeypatch.setattr(
        SteamLocation,
        "shut_down_client",
        lambda _location: running.__setitem__(0, False),
    )

    def no_prompt(_prompt: str) -> str:
        pytest.fail("--yes means no question")

    assert _ensure(steam, assume_yes=True, ask=no_prompt)
    assert _shortcuts_path(tmp_path).exists()


def test_without_svt_ge_proton_nothing_is_changed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    steam = _steam(tmp_path, "proton_experimental")
    with caplog.at_level("ERROR"):
        assert not _ensure(steam)
    assert "SVT-GE-Proton isn't installed" in caplog.text
    assert not _shortcuts_path(tmp_path).exists()


def test_the_shortcut_names_the_icon_even_before_it_exists(
    tmp_path: Path,
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    icon = tmp_path / "data" / "icon.png"
    assert _ensure(steam, icon=icon)
    data = vdf.binary_loads(_shortcuts_path(tmp_path).read_bytes())
    assert data["shortcuts"]["0"]["icon"] == str(icon)


def test_a_shortcut_without_an_icon_gets_none(tmp_path: Path) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    assert _ensure(steam)
    data = vdf.binary_loads(_shortcuts_path(tmp_path).read_bytes())
    assert data["shortcuts"]["0"]["icon"] == ""


def test_a_different_icon_updates_just_the_icon(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steam = _existing(tmp_path, "sul rotk-launcher --tpm -- %command%")
    icon = tmp_path / "icon.png"
    assert _ensure(steam, icon=icon)
    data = vdf.binary_loads(_shortcuts_path(tmp_path).read_bytes())
    entry = data["shortcuts"]["0"]
    assert entry["icon"] == str(icon)
    assert entry["LaunchOptions"] == "sul rotk-launcher --tpm -- %command%"
    assert "Updated the icon" in capsys.readouterr().out


def test_the_same_icon_changes_nothing(tmp_path: Path) -> None:
    steam = _existing(tmp_path, "sul rotk-launcher -- %command%")
    icon = tmp_path / "icon.png"
    assert _ensure(steam, icon=icon)  # sets it
    before = _shortcuts_path(tmp_path).read_bytes()
    assert _ensure(steam, icon=icon)
    assert _shortcuts_path(tmp_path).read_bytes() == before


def _launch_options_of(root: Path) -> list[str]:
    data = vdf.binary_loads(_shortcuts_path(root).read_bytes())
    return [e["LaunchOptions"] for e in data["shortcuts"].values()]


def test_the_launch_options_are_built_around_steams_command() -> None:
    assert (
        steam_shortcuts.launch_options_for(
            ["/a b/sul"], "rotk-launcher", ["--max-fps", "150"]
        )
        == "'/a b/sul' rotk-launcher --max-fps 150 -- %command%"
    )


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("sul rotk-launcher -- %command%", []),
        ("sul rotk-launcher --max-fps 150 -- %command%", ["--max-fps", "150"]),
        ("FOO=1 sul rotk-launcher --tpm -- %command% --x", ["--tpm"]),
        ("sul rotk-launcher --tpm", None),  # no --: not our form
        ("something else", None),
        ("sul rotk-launcher 'unbalanced -- %command%", None),
    ],
)
def test_the_options_in_launch_options_are_read(
    current: str, expected: list[str] | None
) -> None:
    assert steam_shortcuts.options_in(current, "rotk-launcher") == expected


def test_replacing_options_keeps_everything_else_in_the_launch_options() -> (
    None
):
    result = steam_shortcuts.with_options(
        "FOO=1 /old/sul rotk-launcher --tpm -- %command% --x",
        ["/new/sul"],
        "rotk-launcher",
        ["--max-fps", "60"],
    )
    assert (
        result == "FOO=1 /old/sul rotk-launcher --max-fps 60 -- %command% --x"
    )


def test_launch_options_in_another_form_are_written_afresh() -> None:
    assert (
        steam_shortcuts.with_options(
            "something else", ["/new/sul"], "rotk-launcher", ["--tpm"]
        )
        == "/new/sul rotk-launcher --tpm -- %command%"
    )


def _existing(root: Path, launch_options: str) -> Steam:
    steam = _steam(root, "SVT-GE-Proton-11")
    _write_shortcuts(
        root,
        steam_shortcuts.new_entry(
            name="ROTK Launcher", launch_options=launch_options, icon=None
        ),
    )
    return steam


def test_rerunning_with_other_options_updates_the_shortcut(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steam = _existing(tmp_path, "/old/sul rotk-launcher --tpm -- %command%")
    config_before = (tmp_path / "config" / "config.vdf").read_text()
    assert _ensure(steam, options=["--max-fps", "60"])
    assert _launch_options_of(tmp_path) == [
        "/old/sul rotk-launcher --max-fps 60 -- %command%"
    ]
    # Only the options change: the config and the id are as they were.
    assert (tmp_path / "config" / "config.vdf").read_text() == config_before
    out = capsys.readouterr().out
    assert "Updated the launch options" in out
    assert "--max-fps 60" in out
    assert (
        len(list(_shortcuts_path(tmp_path).parent.glob("*.sul-backup-*"))) == 1
    )


def test_rerunning_with_the_same_options_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steam = _existing(tmp_path, "sul rotk-launcher --tpm -- %command%")
    before = _shortcuts_path(tmp_path).read_bytes()
    assert _ensure(steam, options=["--tpm"])
    assert _shortcuts_path(tmp_path).read_bytes() == before
    assert "already in your Steam library" in capsys.readouterr().out


def test_rerunning_without_options_keeps_what_was_set_in_steam(
    tmp_path: Path,
) -> None:
    steam = _existing(tmp_path, "sul rotk-launcher --tpm -- %command%")
    before = _shortcuts_path(tmp_path).read_bytes()
    assert _ensure(steam)
    assert _shortcuts_path(tmp_path).read_bytes() == before


def test_an_update_closes_steam_after_asking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steam = _existing(tmp_path, "sul rotk-launcher -- %command%")
    running = [True]
    _client_running(monkeypatch, lambda: running[0])
    monkeypatch.setattr(
        SteamLocation,
        "shut_down_client",
        lambda _location: running.__setitem__(0, False),
    )
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return "y"

    assert _ensure(steam, options=["--tpm"], ask=ask)
    assert "update the launch options" in prompts[0]
    assert _launch_options_of(tmp_path) == [
        "sul rotk-launcher --tpm -- %command%"
    ]


def test_an_update_that_steam_being_open_prevents_says_so(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    steam = _existing(tmp_path, "sul rotk-launcher -- %command%")
    before = _shortcuts_path(tmp_path).read_bytes()
    _client_running(monkeypatch, lambda: True)
    with caplog.at_level("ERROR"):
        assert not _ensure(steam, options=["--tpm"], ask=lambda _p: "n")
    assert "update the launch options" in caplog.text
    assert _shortcuts_path(tmp_path).read_bytes() == before


def test_the_shortcut_is_started_through_steam_after_it_is_added(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    app_id = _ensure(steam, launch=True)
    data = vdf.binary_loads(_shortcuts_path(tmp_path).read_bytes())
    assert app_id == data["shortcuts"]["0"]["appid"] & 0xFFFFFFFF
    assert [app_id] == LAUNCHED
    assert "Starting it through Steam" in capsys.readouterr().out


def test_an_existing_shortcut_is_started_through_steam_untouched(
    tmp_path: Path,
) -> None:
    steam = _existing(tmp_path, "sul rotk-launcher -- %command%")
    before = _shortcuts_path(tmp_path).read_bytes()
    app_id = _ensure(steam, launch=True)
    assert [app_id] == LAUNCHED
    assert _shortcuts_path(tmp_path).read_bytes() == before


def test_an_updated_shortcut_is_started_through_steam(tmp_path: Path) -> None:
    steam = _existing(tmp_path, "sul rotk-launcher -- %command%")
    app_id = _ensure(steam, options=["--tpm"], launch=True)
    assert [app_id] == LAUNCHED
    assert _launch_options_of(tmp_path) == [
        "sul rotk-launcher --tpm -- %command%"
    ]


def test_nothing_is_started_if_the_shortcut_could_not_be_set_up(
    tmp_path: Path,
) -> None:
    steam = _steam(tmp_path, "proton_experimental")  # no SVT-GE-Proton
    assert _ensure(steam, launch=True) is None
    assert LAUNCHED == []


def test_the_game_id_steam_is_given_is_the_app_id_shifted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr(shutil, "which", lambda _name: "/bin/steam")
    monkeypatch.setattr(
        subprocess, "Popen", lambda command, **_kwargs: started.append(command)
    )
    _REAL_LAUNCH(
        SteamLocation(root=tmp_path, system_config_dirs=[]), 3149906526
    )
    assert started == [
        ["/bin/steam", "steam://rungameid/13528745514660528128"]
    ]


def test_a_new_shortcut_has_a_target_that_does_nothing(tmp_path: Path) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    assert _ensure(steam)
    data = vdf.binary_loads(_shortcuts_path(tmp_path).read_bytes())
    entry = data["shortcuts"]["0"]
    # Steam shows no icon for a shortcut with no target.
    assert entry["Exe"] == '"/usr/bin/true"'
    assert entry["StartDir"] == '"/usr/bin/"'
    expected = steam_shortcuts.shortcut_app_id(
        "/usr/bin/true", "ROTK Launcher"
    )
    assert entry["appid"] & 0xFFFFFFFF == expected


def test_a_shortcut_without_a_target_is_given_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steam = _existing(tmp_path, "sul rotk-launcher --tpm -- %command%")
    data = vdf.binary_loads(_shortcuts_path(tmp_path).read_bytes())
    data["shortcuts"]["0"]["Exe"] = ""
    data["shortcuts"]["0"]["StartDir"] = ""
    _shortcuts_path(tmp_path).write_bytes(vdf.binary_dumps(data))
    app_id = data["shortcuts"]["0"]["appid"]
    assert _ensure(steam)
    after = vdf.binary_loads(_shortcuts_path(tmp_path).read_bytes())
    entry = after["shortcuts"]["0"]
    assert entry["Exe"] == '"/usr/bin/true"'
    assert entry["appid"] == app_id  # the id never changes
    assert entry["LaunchOptions"] == "sul rotk-launcher --tpm -- %command%"
    assert "Updated the target" in capsys.readouterr().out


def _librarycache(root: Path, *names: str) -> Path:
    source = root / "appcache" / "librarycache" / "433850"
    source.mkdir(parents=True)
    for name in names:
        (source / name).write_bytes(name.encode())
    return source


def test_the_banner_and_capsules_of_a_game_become_the_shortcuts_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    source = _librarycache(
        tmp_path,
        "library_hero.jpg",
        "library_600x900.jpg",
        "header.jpg",
        "logo.png",
        "library_hero_blur.jpg",
    )
    _client_running(monkeypatch, lambda: False)
    app_id = _ensure(steam, artwork=source)
    assert app_id is not None
    grid = tmp_path / "userdata" / str(ACCOUNT) / "config" / "grid"
    assert sorted(path.name for path in grid.iterdir()) == sorted(
        f"{app_id}{suffix}"
        for suffix in ("_hero.jpg", "p.jpg", ".jpg", "_logo.png")
    )
    assert (grid / f"{app_id}_hero.jpg").read_bytes() == b"library_hero.jpg"


def test_images_the_shortcut_already_has_are_not_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    source = _librarycache(tmp_path, "library_hero.jpg", "logo.png")
    _client_running(monkeypatch, lambda: False)
    app_id = _ensure(steam, artwork=source)
    grid = tmp_path / "userdata" / str(ACCOUNT) / "config" / "grid"
    (grid / f"{app_id}_hero.jpg").write_bytes(b"mine")
    (grid / f"{app_id}_logo.png").unlink()
    assert _ensure(steam, artwork=source) == app_id
    assert (grid / f"{app_id}_hero.jpg").read_bytes() == b"mine"
    assert (grid / f"{app_id}_logo.png").read_bytes() == b"logo.png"


def test_a_game_steam_has_no_images_for_gives_the_shortcut_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steam = _steam(tmp_path, "SVT-GE-Proton-11")
    _client_running(monkeypatch, lambda: False)
    assert _ensure(steam, artwork=tmp_path / "appcache" / "nothing")
    assert not (
        tmp_path / "userdata" / str(ACCOUNT) / "config" / "grid"
    ).exists()
