from __future__ import annotations

import hashlib
import io
import json
import logging
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from steam_utility_launcher import application, proton_tools, svt_ge_proton
from steam_utility_launcher.github_release_updater import Release
from steam_utility_launcher.steam import CompatibilityTool, Steam

if TYPE_CHECKING:
    from collections.abc import Iterator

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


class _FakeSteam:
    def __init__(self, tools_directory: Path) -> None:
        self.location = SimpleNamespace(
            compatibility_tools_dir=tools_directory
        )
        self.compatibility_tools: list[SimpleNamespace] = []


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


def _steam(tools_directory: Path) -> Steam:
    return cast("Steam", _FakeSteam(tools_directory))


def _tool(name: str) -> CompatibilityTool:
    return cast("CompatibilityTool", SimpleNamespace(internal_name=name))


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
    entry = SimpleNamespace(internal_name=SVT_TOOL, install_path=tool)
    with caplog.at_level(logging.WARNING):
        svt_ge_proton.warn_if_update_available(entry)  # type: ignore[arg-type]
    assert "svt-GE-Proton11-12" in caplog.text
    assert "proton svt-ge" in caplog.text


# The command line


def test_the_command_installs_the_named_proton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, bool]] = []
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls: object())
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
        Steam, "from_detection", classmethod(lambda _cls: object())
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
    steam = _FakeSteam(tmp_path)
    steam.compatibility_tools = [SimpleNamespace(internal_name=SVT_TOOL)]
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls: steam)
    )
    assert application.main(["proton"]) == 0
    output = capsys.readouterr().out
    assert "ge " in output
    assert f"installed: {SVT_TOOL}" in output
