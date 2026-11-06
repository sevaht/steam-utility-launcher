from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import logging
import runpy
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import application, proton_tools, svt_ge_proton
from steam_utility_launcher.github_release_updater import Release
from steam_utility_launcher.steam import (
    CompatibilityTool,
    Steam,
    SteamLocation,
)

from .steam_helper import make_tool

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

GE_TAG = "GE-Proton10-4"
GE_TOOL = "GE-Proton10-4-x86_64"
SVT_TAG = "svt-GE-Proton11-11"
SVT_TOOL = "SVT-GE-Proton-11"


def _build_tool(
    directory: Path, name: str, version: str, marker: str = ""
) -> Path:
    tool = directory / name
    tool.mkdir(parents=True)
    (tool / "compatibilitytool.vdf").write_text("tool")
    (tool / "proton").write_text("#!/usr/bin/env python3\n")
    (tool / "version").write_text(f"1793340273 {version}\n")
    if marker:
        (tool / proton_tools.TAG_FILE_NAME).write_text(marker)
    return tool


def _archive(path: Path, source: Path, *extra: tuple[str, bytes]) -> str:
    """Packs the folder; returns the archive's SHA-512."""
    with tarfile.open(path, "w:gz") as package:
        package.add(source, arcname=source.name)
        for name, content in extra:
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            package.addfile(entry, io.BytesIO(content))
    return hashlib.sha512(path.read_bytes()).hexdigest()


class _LocationOfTools(SteamLocation):
    """A Steam installation whose tools folder is wherever a test put it."""

    def __init__(self, tools_directory: Path) -> None:
        super().__init__(root=tools_directory.parent, system_config_dirs=[])
        self._tools_directory = tools_directory

    @property
    def compatibility_tools_dir(self) -> Path:
        return self._tools_directory


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    source: proton_tools.ProtonSource,
    tag: str,
    archive_path: Path,
    checksum: str,
) -> list[str]:
    """Makes the newest release this archive; returns what gets downloaded."""
    downloads: list[str] = []
    name = source.tool_name_for_tag(tag)
    release = Release(
        tag=tag,
        asset_urls={
            Path(f"{name}.tar.gz"): "https://example.test/archive",
            Path(f"{name}.sha512sum"): "https://example.test/sums",
        },
    )
    monkeypatch.setattr(
        proton_tools, "find_release", lambda _source, _tag="": release
    )
    monkeypatch.setattr(proton_tools, "_check_architecture", lambda: None)
    monkeypatch.setattr(
        proton_tools,
        "https_get",
        lambda _url: (200, f"{checksum}  {name}.tar.gz\n".encode()),
    )

    def download(url: str, destination: Path, algorithm: str) -> str:
        assert algorithm == "sha512"
        downloads.append(url)
        destination.write_bytes(archive_path.read_bytes())
        return hashlib.sha512(destination.read_bytes()).hexdigest()

    monkeypatch.setattr(proton_tools, "https_download", download)
    return downloads


def _packed(tmp_path: Path, name: str, version: str) -> tuple[Path, str]:
    built = _build_tool(tmp_path / "built", name, version)
    archive = tmp_path / f"{name}.tar.gz"
    return archive, _archive(archive, built)


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """application.main sets up logging on the stderr pytest is capturing,
    which is closed by the next test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _steam(
    tools_directory: Path, tools: Sequence[CompatibilityTool] = ()
) -> Steam:
    return Steam(
        location=_LocationOfTools(tools_directory),
        compatibility_tools=list(tools),
    )


def _tool(name: str) -> CompatibilityTool:
    return make_tool(Path("/tools") / name, name)


# Naming


def test_ge_proton_is_a_tool_per_release() -> None:
    assert proton_tools.GE_PROTON.tool_name_for_tag(GE_TAG) == GE_TOOL


@pytest.mark.parametrize("tag", [SVT_TAG, f"{SVT_TAG}-r2"])
def test_svt_ge_is_a_tool_per_series(tag: str) -> None:
    assert proton_tools.SVT_GE_PROTON.tool_name_for_tag(tag) == SVT_TOOL


def test_a_tag_of_something_else_is_not_svt_ge() -> None:
    with pytest.raises(RuntimeError, match="Not a release"):
        proton_tools.SVT_GE_PROTON.tool_name_for_tag("svt-11.0-2e")


def test_tools_belong_to_the_source_they_are_named_for() -> None:
    assert (
        proton_tools.source_for_tool(_tool(GE_TOOL)) is proton_tools.GE_PROTON
    )
    assert proton_tools.source_for_tool(_tool(SVT_TOOL)) is (
        proton_tools.SVT_GE_PROTON
    )
    assert (
        proton_tools.source_for_tool(_tool("SVT-Proton-11.0")) is None
    )  # old

    assert proton_tools.source_for_tool(_tool("Proton-ROTK")) is None


# Choosing the release


def _listing(*tags: str, draft: bool = False) -> object:
    releases = [
        {"tag_name": tag, "draft": draft, "prerelease": False, "assets": []}
        for tag in tags
    ]
    return lambda _url: (200, json.dumps(releases).encode())


def test_the_newest_matching_release_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "steam_utility_launcher.github_release_updater.https_get",
        _listing("not-a-proton", GE_TAG, "GE-Proton10-3"),
    )
    release = proton_tools.find_release(proton_tools.GE_PROTON)
    assert release.tag == GE_TAG


def test_no_matching_release_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "steam_utility_launcher.github_release_updater.https_get",
        _listing("svt-11.0-2e"),
    )
    with pytest.raises(RuntimeError, match="no SVT-GE-Proton release"):
        proton_tools.find_release(proton_tools.SVT_GE_PROTON)


def test_a_tag_of_another_proton_is_refused() -> None:
    with pytest.raises(RuntimeError, match="not a release of GE-Proton"):
        proton_tools.find_release(proton_tools.GE_PROTON, SVT_TAG)


# What is installed


def test_the_recorded_release_is_compared_exactly(tmp_path: Path) -> None:
    tool = _build_tool(tmp_path, SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG)
    source = proton_tools.SVT_GE_PROTON
    assert proton_tools.is_current(source, tool, SVT_TAG)
    assert not proton_tools.is_current(source, tool, f"{SVT_TAG}-r2")


def test_a_hand_unpacked_tool_is_known_by_its_version_file(
    tmp_path: Path,
) -> None:
    tool = _build_tool(tmp_path, SVT_TOOL, "GE-Proton11-11")
    source = proton_tools.SVT_GE_PROTON
    assert proton_tools.installed_tag(source, tool) == SVT_TAG
    assert proton_tools.is_current(source, tool, SVT_TAG)
    assert proton_tools.is_current(source, tool, f"{SVT_TAG}-r2")
    assert not proton_tools.is_current(source, tool, "svt-GE-Proton11-12")


def test_a_folder_without_version_information_is_not_current(
    tmp_path: Path,
) -> None:
    tool = tmp_path / SVT_TOOL
    tool.mkdir()
    assert not proton_tools.is_current(
        proton_tools.SVT_GE_PROTON, tool, SVT_TAG
    )


# Installing


def test_it_installs_into_steams_tool_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-11")
    _serve(monkeypatch, proton_tools.SVT_GE_PROTON, SVT_TAG, archive, checksum)
    tools = tmp_path / "compatibilitytools.d"
    steam = _steam(tools)
    assert proton_tools.install(steam, proton_tools.SVT_GE_PROTON)
    installed = tools / SVT_TOOL
    assert (installed / "compatibilitytool.vdf").is_file()
    assert (installed / proton_tools.TAG_FILE_NAME).read_text() == SVT_TAG
    assert [path.name for path in tools.iterdir()] == [SVT_TOOL]


def test_upstream_ge_installs_under_its_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, checksum = _packed(tmp_path, GE_TOOL, GE_TAG)
    _serve(monkeypatch, proton_tools.GE_PROTON, GE_TAG, archive, checksum)
    tools = tmp_path / "tools"
    proton_tools.install(_steam(tools), proton_tools.GE_PROTON)
    assert (tools / GE_TOOL / "compatibilitytool.vdf").is_file()


def test_an_install_that_is_current_downloads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-11")
    downloads = _serve(
        monkeypatch, proton_tools.SVT_GE_PROTON, SVT_TAG, archive, checksum
    )
    tools = tmp_path / "tools"
    _build_tool(tools, SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG)
    steam = _steam(tools)
    assert not proton_tools.install(steam, proton_tools.SVT_GE_PROTON)
    assert downloads == []
    assert proton_tools.install(steam, proton_tools.SVT_GE_PROTON, force=True)
    assert len(downloads) == 1


def test_an_update_replaces_the_old_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-12")
    new_tag = "svt-GE-Proton11-12"
    _serve(monkeypatch, proton_tools.SVT_GE_PROTON, new_tag, archive, checksum)
    tools = tmp_path / "tools"
    old = _build_tool(tools, SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG)
    (old / "stale").write_text("old")
    proton_tools.install(_steam(tools), proton_tools.SVT_GE_PROTON)
    assert not (tools / SVT_TOOL / "stale").exists()
    assert (
        tools / SVT_TOOL / proton_tools.TAG_FILE_NAME
    ).read_text() == new_tag


def _update_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old: Path
) -> Path:
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-12")
    _serve(
        monkeypatch,
        proton_tools.SVT_GE_PROTON,
        "svt-GE-Proton11-12",
        archive,
        checksum,
    )
    proton_tools.install(_steam(old.parent), proton_tools.SVT_GE_PROTON)
    return old.parent / SVT_TOOL


def test_an_update_keeps_the_users_proton_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _build_tool(
        tmp_path / "tools", SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG
    )
    settings = '{"user_settings": {"PROTON_ENABLE_WAYLAND": "1"}}\n'
    (old / "user_settings.py").write_text(settings)
    (old / "user_settings.sample.py").write_text("# not the user's")

    new = _update_over(tmp_path, monkeypatch, old)
    assert (new / "user_settings.py").read_text() == settings
    assert (new / proton_tools.TAG_FILE_NAME).read_text() == (
        "svt-GE-Proton11-12"
    )
    # Only that file: the rest is the new release's
    assert not (new / "user_settings.sample.py").exists()


def test_a_linked_settings_file_stays_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _build_tool(
        tmp_path / "tools", SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG
    )
    elsewhere = tmp_path / "my_settings.py"
    elsewhere.write_text("user_settings = {}\n")
    (old / "user_settings.py").symlink_to(elsewhere)

    new = _update_over(tmp_path, monkeypatch, old)
    assert (new / "user_settings.py").is_symlink()
    assert (new / "user_settings.py").resolve() == elsewhere.resolve()


def _settings_of(
    path: Path, monkeypatch: pytest.MonkeyPatch, *, display: str | None
) -> dict[str, str]:
    """What Proton would get from the file, started in such a session."""
    if display is None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    else:
        monkeypatch.setenv("WAYLAND_DISPLAY", display)
    settings = runpy.run_path(str(path))["user_settings"]
    assert isinstance(settings, dict)
    return settings


def test_svt_ge_makes_default_settings_that_follow_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-11")
    _serve(monkeypatch, proton_tools.SVT_GE_PROTON, SVT_TAG, archive, checksum)
    tools = tmp_path / "tools"
    proton_tools.install(_steam(tools), proton_tools.SVT_GE_PROTON)

    made = tools / SVT_TOOL / "user_settings.py"
    # Decided when Proton starts, not when this was installed
    assert _settings_of(made, monkeypatch, display="wayland-0") == {
        "PROTON_ENABLE_WAYLAND": "1"
    }
    assert _settings_of(made, monkeypatch, display=None) == {}
    assert _settings_of(made, monkeypatch, display="") == {}


def test_an_update_without_settings_makes_the_default_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _build_tool(
        tmp_path / "tools", SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG
    )
    new = _update_over(tmp_path, monkeypatch, old)
    assert _settings_of(
        new / "user_settings.py", monkeypatch, display="wayland-1"
    ) == {"PROTON_ENABLE_WAYLAND": "1"}


def test_a_new_series_takes_the_settings_of_the_previous_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools"
    previous = _build_tool(tools, "SVT-GE-Proton-10", "GE-Proton10-9")
    (previous / "user_settings.py").write_text("user_settings = {'A': '1'}\n")
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-11")
    _serve(monkeypatch, proton_tools.SVT_GE_PROTON, SVT_TAG, archive, checksum)
    proton_tools.install(_steam(tools), proton_tools.SVT_GE_PROTON)
    assert (tools / SVT_TOOL / "user_settings.py").read_text() == (
        "user_settings = {'A': '1'}\n"
    )


def test_ge_takes_the_settings_of_the_newest_previous_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools"
    # By number: 10-3 is newer than 9-20, which text order would put last
    for name, version, content in (
        ("GE-Proton9-20-x86_64", "GE-Proton9-20", "'nine'"),
        ("GE-Proton10-3-x86_64", "GE-Proton10-3", "'ten'"),
        ("GE-Proton10-2-x86_64", "GE-Proton10-2", None),
    ):
        tool = _build_tool(tools, name, version)
        if content:
            (tool / "user_settings.py").write_text(f"x = {content}\n")
    archive, checksum = _packed(tmp_path, GE_TOOL, GE_TAG)
    _serve(monkeypatch, proton_tools.GE_PROTON, GE_TAG, archive, checksum)
    proton_tools.install(_steam(tools), proton_tools.GE_PROTON)
    assert (tools / GE_TOOL / "user_settings.py").read_text() == "x = 'ten'\n"


def test_ge_makes_no_settings_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, checksum = _packed(tmp_path, GE_TOOL, GE_TAG)
    _serve(monkeypatch, proton_tools.GE_PROTON, GE_TAG, archive, checksum)
    tools = tmp_path / "tools"
    proton_tools.install(_steam(tools), proton_tools.GE_PROTON)
    assert not (tools / GE_TOOL / "user_settings.py").exists()


def test_settings_are_not_taken_from_another_proton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools"
    other = _build_tool(tools, "proton_experimental", "x")
    (other / "user_settings.py").write_text("user_settings = {'B': '2'}\n")
    archive, checksum = _packed(tmp_path, GE_TOOL, GE_TAG)
    _serve(monkeypatch, proton_tools.GE_PROTON, GE_TAG, archive, checksum)
    proton_tools.install(_steam(tools), proton_tools.GE_PROTON)
    assert not (tools / GE_TOOL / "user_settings.py").exists()


# The hash of every version of the settings file SVT-GE-Proton has generated.
# Changing one in place would make a file this tool made look edited (and so
# never updated): add the new version at the end of
# proton_tools._SVT_USER_SETTINGS_VERSIONS, and its hash here.
_PINNED_SVT_SETTINGS = (
    "a753ca46f4d04f2aba24c1a881c29935313901e1dd9e9b7de53db1d12e0db92b",
)


def test_the_generated_settings_versions_are_pinned() -> None:
    source = proton_tools.SVT_GE_PROTON
    versions = source.user_settings_versions
    assert (
        tuple(hashlib.sha256(v.encode()).hexdigest() for v in versions)
        == _PINNED_SVT_SETTINGS
    )
    assert source.default_user_settings == versions[-1]
    assert proton_tools.GE_PROTON.default_user_settings == ""


OLD_SETTINGS = "# an older generated file\nuser_settings = {}\n"
NEW_SETTINGS = "# a newer generated file\nuser_settings = {'X': '1'}\n"


def _two_versions() -> proton_tools.ProtonSource:
    return dataclasses.replace(
        proton_tools.SVT_GE_PROTON,
        user_settings_versions=(OLD_SETTINGS, NEW_SETTINGS),
    )


def _install_new_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: str | None
) -> Path:
    """SVT-GE-Proton-11 (release 11-11, with `settings` in it) is updated to
    release 11-12, with a source that has two versions of the file."""
    tools = tmp_path / "tools"
    old = _build_tool(tools, SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG)
    if settings is not None:
        (old / "user_settings.py").write_text(settings)
    source = _two_versions()
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-12")
    _serve(monkeypatch, source, "svt-GE-Proton11-12", archive, checksum)
    proton_tools.install(_steam(tools), source)
    return tools / SVT_TOOL / "user_settings.py"


def test_an_untouched_older_generated_file_is_brought_up_to_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _install_new_release(tmp_path, monkeypatch, OLD_SETTINGS)
    assert result.read_text() == NEW_SETTINGS


@pytest.mark.parametrize(
    "content",
    [
        OLD_SETTINGS + "# my change\n",  # an older one, edited
        OLD_SETTINGS.replace("older", "OLDER"),
        NEW_SETTINGS,  # already current
        "user_settings = {'MINE': '1'}\n",  # the user's own
    ],
)
def test_a_file_that_is_not_exactly_an_older_version_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    result = _install_new_release(tmp_path, monkeypatch, content)
    assert result.read_text() == content


def test_a_linked_older_file_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools"
    old = _build_tool(tools, SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG)
    mine = tmp_path / "mine.py"
    mine.write_text(OLD_SETTINGS)
    (old / "user_settings.py").symlink_to(mine)
    source = _two_versions()
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-12")
    _serve(monkeypatch, source, "svt-GE-Proton11-12", archive, checksum)
    proton_tools.install(_steam(tools), source)
    assert (tools / SVT_TOOL / "user_settings.py").is_symlink()
    assert mine.read_text() == OLD_SETTINGS


def test_it_is_brought_up_to_date_even_when_the_tool_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools"
    tool = _build_tool(tools, SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG)
    (tool / "user_settings.py").write_text(OLD_SETTINGS)
    source = _two_versions()
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-11")
    downloads = _serve(monkeypatch, source, SVT_TAG, archive, checksum)

    assert not proton_tools.install(_steam(tools), source)
    assert downloads == []
    assert (tool / "user_settings.py").read_text() == NEW_SETTINGS


def test_a_current_tool_without_settings_gets_none_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nobody asked for it: it may have been deleted on purpose
    tools = tmp_path / "tools"
    tool = _build_tool(tools, SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG)
    source = _two_versions()
    archive, checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-11")
    _serve(monkeypatch, source, SVT_TAG, archive, checksum)
    assert not proton_tools.install(_steam(tools), source)
    assert not (tool / "user_settings.py").exists()


def test_a_bad_checksum_leaves_the_installed_tool_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, _checksum = _packed(tmp_path, SVT_TOOL, "GE-Proton11-12")
    _serve(
        monkeypatch,
        proton_tools.SVT_GE_PROTON,
        "svt-GE-Proton11-12",
        archive,
        "0" * 128,
    )
    tools = tmp_path / "tools"
    old = _build_tool(tools, SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG)
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        proton_tools.install(_steam(tools), proton_tools.SVT_GE_PROTON)
    assert (old / proton_tools.TAG_FILE_NAME).read_text() == SVT_TAG
    assert [path.name for path in tools.iterdir()] == [SVT_TOOL]


def test_an_archive_with_anything_else_in_it_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _build_tool(tmp_path / "built", SVT_TOOL, "GE-Proton11-11")
    archive = tmp_path / "a.tar.gz"
    checksum = _archive(archive, built, ("elsewhere/file", b"x"))
    _serve(monkeypatch, proton_tools.SVT_GE_PROTON, SVT_TAG, archive, checksum)
    tools = tmp_path / "tools"
    with pytest.raises(RuntimeError, match="Unexpected file"):
        proton_tools.install(_steam(tools), proton_tools.SVT_GE_PROTON)
    assert list(tools.iterdir()) == []


def test_an_archive_cannot_write_outside_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _build_tool(tmp_path / "built", SVT_TOOL, "GE-Proton11-11")
    archive = tmp_path / "a.tar.gz"
    checksum = _archive(archive, built, (f"{SVT_TOOL}/../../escape", b"x"))
    _serve(monkeypatch, proton_tools.SVT_GE_PROTON, SVT_TAG, archive, checksum)
    tools = tmp_path / "tools"
    with pytest.raises((RuntimeError, tarfile.TarError)):
        proton_tools.install(_steam(tools), proton_tools.SVT_GE_PROTON)
    assert not (tmp_path / "escape").exists()


def test_a_release_without_checksums_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Release(
        tag=GE_TAG, asset_urls={Path(f"{GE_TOOL}.tar.gz"): "https://x/a"}
    )
    monkeypatch.setattr(
        proton_tools, "find_release", lambda _source, _tag="": release
    )
    monkeypatch.setattr(proton_tools, "_check_architecture", lambda: None)
    with pytest.raises(
        RuntimeError, match=r"no GE-Proton10-4-x86_64\.sha512sum"
    ):
        proton_tools.install(_steam(tmp_path), proton_tools.GE_PROTON)


# Telling of an update


def test_a_newer_svt_ge_release_is_mentioned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(proton_tools, "XDG_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(
        proton_tools,
        "find_release",
        lambda _source, _tag="": Release(tag="svt-GE-Proton11-12"),
    )
    tool = _build_tool(tmp_path, SVT_TOOL, "GE-Proton11-11", marker=SVT_TAG)
    entry = make_tool(tool, SVT_TOOL)
    with caplog.at_level(logging.WARNING):
        svt_ge_proton.warn_if_update_available(entry)
    assert "svt-GE-Proton11-12" in caplog.text
    assert "proton svt-ge" in caplog.text


# The command line


def test_the_command_installs_the_named_proton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, bool]] = []
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls, _host=None: object())
    )
    monkeypatch.setattr(
        proton_tools,
        "install",
        lambda _steam, source, *, tag="", force=False: calls.append(
            (source.key, tag, force)
        ),
    )
    assert application.main(["proton", "svt-ge"]) == 0
    assert application.main(["proton", "ge", "--tag", GE_TAG, "--force"]) == 0
    assert calls == [("svt-ge", "", False), ("ge", GE_TAG, True)]


def test_a_failed_install_is_an_error_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls, _host=None: object())
    )

    def fail(*_args: object, **_kwargs: object) -> bool:
        msg = "Checksum mismatch"
        raise RuntimeError(msg)

    monkeypatch.setattr(proton_tools, "install", fail)
    assert application.main(["proton", "ge"]) == 1
    assert (
        "ERROR: Could not install Proton: Checksum mismatch"
        in capsys.readouterr().err
    )


def test_without_a_name_it_lists_what_can_be_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    steam = _steam(tmp_path, [_tool(SVT_TOOL)])
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls, _host=None: steam)
    )
    assert application.main(["proton"]) == 0
    output = capsys.readouterr().out
    assert "ge " in output
    assert f"installed: {SVT_TOOL}" in output
