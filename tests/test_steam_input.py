from __future__ import annotations

import stat
import sys
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher.steam import SteamError, SteamLocation
from steam_utility_launcher.steam_input import (
    CONTROLLER_TYPES,
    ControllerConfigs,
    ControllerLayout,
    FileChange,
    device_selections,
    parse_controller_type,
    plan_device_choices,
    plan_layouts,
    read_text,
    selected_template,
    with_selection,
    write_changes,
)

if TYPE_CHECKING:
    from pathlib import Path

APP = 335300
CREATOR = 76561197969290206
ACCOUNT = 9024478

# What Steam writes: tabs, two between a name and its value.
SELECTIONS = """\
"controller_config"
{
\t"1030300"
\t{
\t\t"template"\t\t"controller_generic_gamepad_joystick.vdf"
\t}
\t"113400"
\t{
\t\t"workshop"\t\t"938344701"
\t}
\t"335300"
\t{
\t\t"template"\t\t"CLOUD_335300/other_0"
\t}
\t"570940"
\t{
\t\t"template"\t\t"CLOUD_570940/controller_generic"
\t}
}
"""
TEMPLATE = "CLOUD_335300/gamepad (sevaht)_0"
LAYOUT = """\
"controller_mappings"
{
\t"version"\t\t"3"
\t"title"\t\t"My Layout"
\t"creator"\t\t"@CREATOR@"
\t"url"\t\t"@URL@"
\t"controller_type"\t\t"controller_generic"
\t"controller_caps"\t\t"1590271"
\t"localization"
\t{
\t\t"english"
\t\t{
\t\t\t"title"\t\t"Gamepad"
\t\t}
\t}
}
"""


def _location(root: Path) -> SteamLocation:
    return SteamLocation(root=root, system_config_dirs=[])


def _configs(root: Path) -> ControllerConfigs:
    return ControllerConfigs(_location(root), ACCOUNT)


# --- the layout


def test_the_bundled_layout_is_for_generic_controllers() -> None:
    layout = ControllerLayout.bundled()
    assert layout.controller_type == "controller_generic"
    assert layout.name == f"{layout.title.lower()}_0"
    assert layout.text.startswith('"controller_mappings"')


def test_the_bundled_layout_holds_nothing_of_whose_it_is() -> None:
    text = ControllerLayout.bundled().text
    assert '"@CREATOR@"' in text
    assert '"@URL@"' in text
    assert "/home" not in text
    assert str(CREATOR) not in text
    assert str(ACCOUNT) not in text


def test_the_bundled_layout_binds_the_keys_the_controls_use() -> None:
    text = ControllerLayout.bundled().text
    for binding in ("key_press RETURN", "key_press E", "key_press ESCAPE"):
        assert binding in text


def test_a_layout_names_its_title_and_kind_of_controller() -> None:
    layout = ControllerLayout(LAYOUT)
    assert layout.title == "My Layout"
    assert layout.controller_type == "controller_generic"
    assert layout.name == "my layout_0"


def test_a_layouts_translations_are_not_its_title() -> None:
    assert ControllerLayout(LAYOUT).title != "Gamepad"


@pytest.mark.parametrize("title", ["", "a/b", "a\0b"])
def test_a_title_that_cannot_name_a_file_is_refused(title: str) -> None:
    layout = ControllerLayout(LAYOUT.replace("My Layout", title))
    with pytest.raises(SteamError, match="title"):
        _ = layout.name


def test_a_layout_missing_what_steam_writes_is_refused() -> None:
    layout = ControllerLayout('"controller_mappings"\n{\n}\n')
    with pytest.raises(SteamError, match="'title'"):
        _ = layout.title
    with pytest.raises(SteamError, match="'creator'"):
        layout.prepared(creator=1, url="x")


def test_preparing_a_layout_says_who_made_it_and_where_it_was_saved() -> None:
    prepared = ControllerLayout(LAYOUT).prepared(
        creator=CREATOR, url="autosave:///some/where.vdf"
    )
    assert f'\t"creator"\t\t"{CREATOR}"' in prepared.text
    assert '\t"url"\t\t"autosave:///some/where.vdf"' in prepared.text
    changed = [
        (a, b)
        for a, b in zip(
            LAYOUT.splitlines(), prepared.text.splitlines(), strict=True
        )
        if a != b
    ]
    assert len(changed) == 2  # those two lines, and nothing else


def test_preparing_replaces_what_is_there_too() -> None:
    once = ControllerLayout(LAYOUT).prepared(creator=1, url="a")
    twice = once.prepared(creator=2, url="b")
    assert '"creator"\t\t"2"' in twice.text
    assert '"url"\t\t"b"' in twice.text
    assert twice.text.count("creator") == 1


def test_a_layout_is_read_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "layout.vdf"
    path.write_text(LAYOUT, encoding="utf-8")
    assert ControllerLayout.from_file(path).text == LAYOUT


# --- choosing a layout for a game


def test_the_chosen_layout_is_read_back() -> None:
    assert selected_template(SELECTIONS, APP) == "CLOUD_335300/other_0"
    assert selected_template(SELECTIONS, 570940) == (
        "CLOUD_570940/controller_generic"
    )


def test_a_game_with_no_chosen_layout_by_name_has_none() -> None:
    assert selected_template(SELECTIONS, 113400) is None  # a workshop one
    assert selected_template(SELECTIONS, 1) is None
    assert selected_template("", APP) is None


def test_an_unreadable_selection_file_is_an_error() -> None:
    with pytest.raises(SteamError, match="not valid"):
        selected_template('"controller_config"\n{\n', APP)


def test_choosing_replaces_only_that_games_entry() -> None:
    result = with_selection(SELECTIONS, APP, TEMPLATE)
    assert result == SELECTIONS.replace("CLOUD_335300/other_0", TEMPLATE)
    assert selected_template(result, APP) == TEMPLATE


def test_choosing_replaces_an_entry_of_another_kind() -> None:
    text = SELECTIONS.replace(
        '\t\t"template"\t\t"CLOUD_335300/other_0"\n',
        '\t\t"workshop"\t\t"123"\n\t\t"autosave"\t\t"1"\n',
    )
    result = with_selection(text, APP, TEMPLATE)
    assert selected_template(result, APP) == TEMPLATE
    assert '"workshop"\t\t"123"' not in result
    assert '"workshop"\t\t"938344701"' in result  # another game's, kept


def test_choosing_for_a_game_without_an_entry_adds_one() -> None:
    text = SELECTIONS.replace(
        '\t"335300"\n\t{\n\t\t"template"\t\t"CLOUD_335300/other_0"\n\t}\n', ""
    )
    result = with_selection(text, APP, TEMPLATE)
    assert selected_template(result, APP) == TEMPLATE
    assert result.startswith(text.rstrip()[:-1])  # what was there, unchanged
    assert result.endswith("}\n")
    assert '\t\t"template"\t\t"CLOUD_570940/controller_generic"' in result


def test_choosing_starts_a_selection_file_that_is_not_there() -> None:
    for existing in (None, "", "\n"):
        result = with_selection(existing, APP, TEMPLATE)
        assert result == (
            '"controller_config"\n{\n\t"335300"\n\t{\n'
            f'\t\t"template"\t\t"{TEMPLATE}"\n\t}}\n}}\n'
        )


def test_choosing_twice_is_choosing_once() -> None:
    once = with_selection(SELECTIONS, APP, TEMPLATE)
    assert with_selection(once, APP, TEMPLATE) == once


@pytest.mark.parametrize("text", ["nonsense", '"something"\n{\n}\n', "}"])
def test_a_selection_file_not_in_the_form_steam_writes_is_refused(
    text: str,
) -> None:
    with pytest.raises(SteamError, match="not in the form"):
        with_selection(text, APP, TEMPLATE)


# --- where things are


def test_layouts_are_kept_in_the_accounts_folder(tmp_path: Path) -> None:
    configs = _configs(tmp_path)
    layout = ControllerLayout(LAYOUT)
    base = (
        tmp_path
        / "steamapps"
        / "common"
        / "Steam Controller Configs"
        / str(ACCOUNT)
        / "config"
    )
    assert configs.directory == base
    assert (
        configs.layout_path(APP, layout) == base / "335300" / "my layout_0.vdf"
    )
    assert configs.selection_path("controller_generic") == (
        base / "configset_controller_generic.vdf"
    )
    assert configs.autosave_url(APP, "controller_generic") == (
        f"autosave://{base / '335300' / 'controller_generic.vdf'}"
    )


# --- planning and writing


def _plan(root: Path) -> list[FileChange]:
    return plan_layouts(
        _configs(root), APP, [ControllerLayout(LAYOUT)], creator=CREATOR
    )


def test_a_game_with_nothing_set_up_gets_a_layout_and_a_choice(
    tmp_path: Path,
) -> None:
    changes = _plan(tmp_path)
    assert [change.path.name for change in changes] == [
        "my layout_0.vdf",
        "configset_controller_generic.vdf",
    ]
    assert f'"creator"\t\t"{CREATOR}"' in changes[0].content
    assert (
        selected_template(changes[1].content, APP)
        == "CLOUD_335300/my layout_0"
    )


def test_planning_is_read_only(tmp_path: Path) -> None:
    _plan(tmp_path)
    assert not list(tmp_path.rglob("*"))


def test_once_written_nothing_is_left_to_change(tmp_path: Path) -> None:
    write_changes(_plan(tmp_path))
    assert _plan(tmp_path) == []


def test_only_what_differs_is_planned(tmp_path: Path) -> None:
    write_changes(_plan(tmp_path))
    configs = _configs(tmp_path)
    selection = configs.selection_path("controller_generic")
    selection.write_text(
        selection.read_text().replace("my layout_0", "elsewhere_1"),
        encoding="utf-8",
    )
    assert [c.path for c in _plan(tmp_path)] == [selection]
    layout = configs.layout_path(APP, ControllerLayout(LAYOUT))
    layout.write_text("edited", encoding="utf-8")
    assert [c.path for c in _plan(tmp_path)] == [layout, selection]


def test_writing_makes_the_folders_and_leaves_no_staging_files(
    tmp_path: Path,
) -> None:
    backups = write_changes(_plan(tmp_path))
    assert backups == []  # nothing was there to back up
    assert not list(tmp_path.rglob("*.sul-*"))
    assert (
        _configs(tmp_path)
        .layout_path(APP, ControllerLayout(LAYOUT))
        .read_text()
        .startswith('"controller_mappings"')
    )


def test_writing_backs_up_what_it_replaces_first(tmp_path: Path) -> None:
    configs = _configs(tmp_path)
    selection = configs.selection_path("controller_generic")
    selection.parent.mkdir(parents=True)
    selection.write_text(SELECTIONS, encoding="utf-8")
    selection.chmod(0o640)
    backups = write_changes(_plan(tmp_path))
    assert [b.name.split(".sul-backup-")[0] for b in backups] == [
        "configset_controller_generic.vdf"
    ]
    assert backups[0].read_text() == SELECTIONS
    if sys.platform != "win32":  # Windows has no permission bits
        assert stat.S_IMODE(selection.stat().st_mode) == 0o640
    assert selected_template(selection.read_text(), 570940) is not None


def test_a_change_is_needed_when_the_file_differs_or_is_missing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "f"
    assert FileChange(path, "x").needed
    path.write_text("x", encoding="utf-8")
    assert not FileChange(path, "x").needed
    assert FileChange(path, "y").needed


def test_a_file_that_cannot_be_read_is_an_error(tmp_path: Path) -> None:
    assert read_text(tmp_path / "missing") is None
    with pytest.raises(SteamError, match="Could not read"):
        read_text(tmp_path)  # a folder


# --- kinds of controller


def test_the_kinds_of_controller_are_named_as_steam_names_them() -> None:
    names = [kind.name for kind in CONTROLLER_TYPES]
    assert len(set(names)) == len(names)
    assert all(name.startswith("controller_") for name in names)
    assert {
        "controller_generic",
        "controller_xbox360",
        "controller_xboxone",
        "controller_ps4",
        "controller_ps5",
        "controller_switch_pro",
    } == set(names)


def test_the_bundled_layout_is_already_for_the_generic_kind() -> None:
    bundled = ControllerLayout.bundled()
    generic = CONTROLLER_TYPES[0]
    assert generic.name == bundled.controller_type
    assert bundled.for_type(generic) is bundled
    assert f'"controller_caps"\t\t"{generic.caps}"' in bundled.text


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("generic", "controller_generic"),
        ("controller_ps5", "controller_ps5"),
        ("PS5", "controller_ps5"),
        ("Controller_PS5", "controller_ps5"),
        ("ps4", "controller_ps4"),
        ("xbox360", "controller_xbox360"),
        ("Xbox 360", "controller_xbox360"),
        ("xboxone", "controller_xboxone"),
        ("xbox one", "controller_xboxone"),
        ("switch_pro", "controller_switch_pro"),
        ("Switch Pro", "controller_switch_pro"),
        ("switch-pro", "controller_switch_pro"),
        ("  ps5  ", "controller_ps5"),
    ],
)
def test_a_kind_is_found_by_name_or_label(text: str, name: str) -> None:
    assert parse_controller_type(text).name == name


@pytest.mark.parametrize("text", ["", "nope", "controller_", "xbox"])
def test_an_unknown_kind_lists_the_known_ones(text: str) -> None:
    with pytest.raises(ValueError, match="use one of: generic, xbox360"):
        parse_controller_type(text)


def test_a_layout_for_another_kind_changes_only_what_says_what_it_is() -> None:
    base = ControllerLayout(LAYOUT)
    ps5 = parse_controller_type("ps5")
    other = base.for_type(ps5)
    assert other.controller_type == "controller_ps5"
    assert other.title == "My Layout - PS5"
    assert other.name == "my layout - ps5_0"
    changed = [
        a
        for a, b in zip(
            base.text.splitlines(), other.text.splitlines(), strict=True
        )
        if a != b
    ]
    assert [line.split('"')[1] for line in changed] == [
        "title",
        "controller_type",
        "controller_caps",
    ]


def test_caps_are_the_kinds_not_the_layouts_own() -> None:
    switch = ControllerLayout(LAYOUT).for_type(
        parse_controller_type("switch_pro")
    )
    assert '\t"controller_caps"\t\t"68963327"' in switch.text
    assert '"1590271"' not in switch.text


def test_a_layout_with_no_caps_cannot_be_made_for_another_kind() -> None:
    # Steam writes caps in every layout it saves; a template it ships has none.
    bare = ControllerLayout(
        LAYOUT.replace('\t"controller_caps"\t\t"1590271"\n', "")
    )
    with pytest.raises(SteamError, match="'controller_caps'"):
        bare.for_type(parse_controller_type("ps5"))


def test_each_kind_gets_a_layout_file_of_its_own() -> None:
    base = ControllerLayout.bundled()
    names = {base.for_type(kind).name for kind in CONTROLLER_TYPES}
    assert len(names) == len(CONTROLLER_TYPES)


def test_the_layouts_for_the_kinds_differ_in_their_metadata_only() -> None:
    base = ControllerLayout.bundled()
    for kind in CONTROLLER_TYPES:
        other = base.for_type(kind)
        changed = [
            line.split('"')[1]
            for line, original in zip(
                other.text.splitlines(), base.text.splitlines(), strict=True
            )
            if line != original
        ]
        assert set(changed) <= {"title", "controller_type", "controller_caps"}


def test_a_layout_for_several_kinds_is_planned_for_each(
    tmp_path: Path,
) -> None:
    kinds = [parse_controller_type(k) for k in ("generic", "ps5", "xboxone")]
    layouts = [ControllerLayout(LAYOUT).for_type(kind) for kind in kinds]
    configs = _configs(tmp_path)
    changes = plan_layouts(configs, APP, layouts, creator=CREATOR)
    assert [change.path.name for change in changes] == [
        "my layout_0.vdf",
        "configset_controller_generic.vdf",
        "my layout - ps5_0.vdf",
        "configset_controller_ps5.vdf",
        "my layout - xbox one_0.vdf",
        "configset_controller_xboxone.vdf",
    ]
    write_changes(changes)
    for layout in layouts:
        selection = configs.selection_path(layout.controller_type)
        assert selected_template(read_text(selection) or "", APP) == (
            f"CLOUD_335300/{layout.name}"
        )
        url_path = (
            configs.directory / "335300" / f"{layout.controller_type}.vdf"
        )
        url = f"autosave://{url_path}"
        assert f'"url"\t\t"{url}"' in (
            configs.layout_path(APP, layout).read_text()
        )
    assert plan_layouts(configs, APP, layouts, creator=CREATOR) == []


def test_planning_for_no_kinds_plans_nothing(tmp_path: Path) -> None:
    assert plan_layouts(_configs(tmp_path), APP, [], creator=CREATOR) == []


# --- choices for particular controllers


def _device_file(configs: ControllerConfigs, name: str, body: str) -> None:
    configs.directory.mkdir(parents=True, exist_ok=True)
    (configs.directory / name).write_text(body, encoding="utf-8")


def test_choices_for_particular_controllers_are_found(tmp_path: Path) -> None:
    configs = _configs(tmp_path)
    _device_file(configs, "configset_2dc8-310a-89b3de.vdf", SELECTIONS)
    _device_file(
        configs,
        "configset_57e-2009-89b3de.vdf",
        '"controller_config"\n{\n\t"1"\n\t{\n\t\t"template"\t\t"x"\n\t}\n}\n',
    )
    # The kinds' own files, and one with only a workshop choice, don't count.
    _device_file(configs, "configset_controller_generic.vdf", SELECTIONS)
    _device_file(
        configs,
        "configset_NSP98b6e9a7f57c.vdf",
        '"controller_config"\n{\n\t"335300"\n\t{\n\t\t"workshop"\t\t"1"\n\t}\n}\n',
    )
    assert device_selections(configs, APP) == {
        "configset_2dc8-310a-89b3de.vdf": "CLOUD_335300/other_0"
    }


def test_no_choices_for_particular_controllers_when_there_is_no_folder(
    tmp_path: Path,
) -> None:
    assert device_selections(_configs(tmp_path), APP) == {}


# --- changing the choices for particular controllers


def _saved_layout(configs: ControllerConfigs, name: str, kind: str) -> None:
    folder = configs.directory / str(APP)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.vdf").write_text(
        LAYOUT.replace("controller_generic", kind), encoding="utf-8"
    )


def _choice(configs: ControllerConfigs, file: str, template: str) -> None:
    _device_file(
        configs,
        file,
        '"controller_config"\n{\n\t"570940"\n\t{\n\t\t"workshop"\t\t"7"\n'
        f'\t}}\n\t"335300"\n\t{{\n\t\t"template"\t\t"{template}"\n\t}}\n}}\n',
    )


def _ours(*kinds: str) -> list[ControllerLayout]:
    base = ControllerLayout(LAYOUT)
    return [base.for_type(parse_controller_type(kind)) for kind in kinds]


def test_a_choice_for_a_controller_becomes_this_layout_for_its_kind(
    tmp_path: Path,
) -> None:
    configs = _configs(tmp_path)
    _saved_layout(configs, "old_0", "controller_ps5")
    _choice(configs, "configset_54c-ce6-89b3de.vdf", "CLOUD_335300/old_0")
    changes, unresolved = plan_device_choices(
        configs, APP, _ours("generic", "ps5")
    )
    assert unresolved == {}
    ((change, layout),) = changes
    assert layout.controller_type == "controller_ps5"
    assert change.path.name == "configset_54c-ce6-89b3de.vdf"
    assert selected_template(change.content, APP) == (
        "CLOUD_335300/my layout - ps5_0"
    )
    assert selected_template(change.content, 570940) is None  # its own kind
    assert '"workshop"\t\t"7"' in change.content  # another game's, kept


def test_planning_the_choices_changes_nothing_on_disk(tmp_path: Path) -> None:
    configs = _configs(tmp_path)
    _saved_layout(configs, "old_0", "controller_generic")
    _choice(configs, "configset_a-b-c.vdf", "CLOUD_335300/old_0")
    before = (configs.directory / "configset_a-b-c.vdf").read_text()
    plan_device_choices(configs, APP, _ours("generic"))
    assert (configs.directory / "configset_a-b-c.vdf").read_text() == before


def test_a_choice_that_is_already_this_layout_is_left_alone(
    tmp_path: Path,
) -> None:
    configs = _configs(tmp_path)
    layouts = _ours("generic")
    _choice(configs, "configset_a-b-c.vdf", f"CLOUD_335300/{layouts[0].name}")
    assert plan_device_choices(configs, APP, layouts) == ([], {})


@pytest.mark.parametrize(
    "template",
    [
        "CLOUD_335300/missing_0",  # no such layout
        "controller_generic_gamepad_joystick.vdf",  # one of Steam's templates
        "CLOUD_999/other_0",  # another game's
    ],
)
def test_a_choice_that_cannot_be_placed_is_left_as_it_is(
    tmp_path: Path, template: str
) -> None:
    configs = _configs(tmp_path)
    _choice(configs, "configset_a-b-c.vdf", template)
    changes, unresolved = plan_device_choices(configs, APP, _ours("generic"))
    assert changes == []
    assert unresolved == {"configset_a-b-c.vdf": template}


def test_a_choice_for_a_kind_there_is_no_layout_for_is_left_as_it_is(
    tmp_path: Path,
) -> None:
    configs = _configs(tmp_path)
    _saved_layout(configs, "elite_0", "controller_xboxelite")
    _choice(configs, "configset_a-b-c.vdf", "CLOUD_335300/elite_0")
    changes, unresolved = plan_device_choices(
        configs, APP, _ours("generic", "xboxone")
    )
    assert changes == []
    assert unresolved == {"configset_a-b-c.vdf": "CLOUD_335300/elite_0"}


def test_a_chosen_layout_that_is_not_a_layout_is_left_as_it_is(
    tmp_path: Path,
) -> None:
    configs = _configs(tmp_path)
    folder = configs.directory / str(APP)
    folder.mkdir(parents=True)
    (folder / "junk_0.vdf").write_text("not a layout", encoding="utf-8")
    _choice(configs, "configset_a-b-c.vdf", "CLOUD_335300/junk_0")
    changes, unresolved = plan_device_choices(configs, APP, _ours("generic"))
    assert changes == []
    assert unresolved == {"configset_a-b-c.vdf": "CLOUD_335300/junk_0"}


def test_each_controllers_choice_gets_the_layout_of_its_own_kind(
    tmp_path: Path,
) -> None:
    configs = _configs(tmp_path)
    _saved_layout(configs, "a_0", "controller_generic")
    _saved_layout(configs, "b_0", "controller_switch_pro")
    _choice(configs, "configset_1.vdf", "CLOUD_335300/a_0")
    _choice(configs, "configset_2.vdf", "CLOUD_335300/b_0")
    changes, _ = plan_device_choices(
        configs, APP, _ours("generic", "switch_pro")
    )
    assert {c.path.name: layout.controller_type for c, layout in changes} == {
        "configset_1.vdf": "controller_generic",
        "configset_2.vdf": "controller_switch_pro",
    }


def test_no_controller_choices_means_nothing_to_change(tmp_path: Path) -> None:
    assert plan_device_choices(_configs(tmp_path), APP, _ours("generic")) == (
        [],
        {},
    )
