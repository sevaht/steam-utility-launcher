from __future__ import annotations

import hashlib
import json
import logging
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from steam_utility_launcher import (
    application,
    github_release_updater,
    svt_proton,
)
from steam_utility_launcher.github_release_updater import Release
from steam_utility_launcher.steam import Steam
from steam_utility_launcher.utilities import rotk_launcher

if TYPE_CHECKING:
    from collections.abc import Callable

TAG = "svt-11.0-2e"
ARCHIVE = "SVT-Proton-11.0-2e-x86_64.tar.xz"
FLAG = "--allow-any-proton"


def _build_tool(directory: Path, marker: str = "") -> Path:
    tool = directory / svt_proton.TOOL_NAME
    (tool / "files" / "lib").mkdir(parents=True)
    (tool / "compatibilitytool.vdf").write_text("tool")
    (tool / "proton").write_text("#!/usr/bin/env python3\n")
    (tool / "proton").chmod(0o755)
    (tool / "version").write_text("1791757024 proton-11.0-2e\n")
    (tool / "files" / "lib" / "wine").write_text("wine")
    (tool / "files" / "wine-link").symlink_to("lib/wine")
    if marker:
        (tool / svt_proton.TAG_FILE_NAME).write_text(marker)
    return tool


def _archive(path: Path, source: Path, *extra: tuple[str, bytes]) -> str:
    """Packs the folder; returns the archive's SHA-256."""
    with tarfile.open(path, "w:xz") as package:
        package.add(source, arcname=source.name)
        for name, content in extra:
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            import io

            package.addfile(entry, io.BytesIO(content))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _FakeSteam:
    def __init__(
        self,
        tools_directory: Path,
        *,
        chosen: str = "",
        tools: list[SimpleNamespace] | None = None,
    ) -> None:
        self.location = SimpleNamespace(
            compatibility_tools_dir=tools_directory
        )
        self.compatibility_tools = tools or []
        self._chosen = chosen

    def game_compatibility_tool(self, _game_id: str) -> str:
        return self._chosen

    def game_tool(self, _game_id: str) -> SimpleNamespace | None:
        for tool in self.compatibility_tools:
            if tool.internal_name == self._chosen:
                return tool
        return None


def _tool_entry(name: str, directory: Path) -> SimpleNamespace:
    return SimpleNamespace(internal_name=name, install_path=directory)


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    archive_path: Path,
    checksum: str,
    *,
    tag: str = TAG,
) -> list[str]:
    """Makes the latest release this archive; returns what gets downloaded."""
    downloads: list[str] = []
    release = Release(
        tag=tag,
        asset_urls={
            Path(ARCHIVE): "https://example.test/archive",
            Path("SHA256SUMS.txt"): "https://example.test/sums",
        },
    )
    monkeypatch.setattr(svt_proton, "latest_release", lambda: release)
    monkeypatch.setattr(svt_proton, "_architecture", lambda: "x86_64")
    monkeypatch.setattr(
        svt_proton,
        "https_get",
        lambda _url: (200, f"{checksum}  {ARCHIVE}\n".encode()),
    )

    def download(url: str, destination: Path) -> str:
        downloads.append(url)
        destination.write_bytes(archive_path.read_bytes())
        return hashlib.sha256(destination.read_bytes()).hexdigest()

    monkeypatch.setattr(svt_proton, "https_download", download)
    return downloads


def _listing(
    *releases: dict[str, object]
) -> Callable[[str], tuple[int, bytes]]:
    return lambda _url: (200, json.dumps(list(releases)).encode())


def _release_json(
    tag: str, *, draft: bool = False, prerelease: bool = False
) -> dict[str, object]:
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": [
            {"name": f"{tag}.txt", "browser_download_url": "https://x/a"}
        ],
    }


# Choosing the release


def test_the_archive_is_named_for_the_release_and_the_architecture() -> None:
    assert svt_proton.archive_name(TAG, "x86_64") == ARCHIVE
    assert (
        svt_proton.archive_name("svt-11.0-2e-r2", "x86_64")
        == "SVT-Proton-11.0-2e-r2-x86_64.tar.xz"
    )


def test_the_newest_release_of_the_series_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        github_release_updater,
        "https_get",
        _listing(
            _release_json("svt-10.0-4b"),  # another series, though newer
            _release_json("svt-11.0-3a", draft=True),
            _release_json("svt-11.0-2f", prerelease=True),
            _release_json("svt-11.0-2e-r2"),
            _release_json("svt-11.0-2e"),
        ),
    )
    assert svt_proton.latest_release().tag == "svt-11.0-2e-r2"


def test_no_release_of_the_series_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        github_release_updater,
        "https_get",
        _listing(_release_json("svt-10.0-4b")),
    )
    with pytest.raises(RuntimeError, match=r"no svt-11\.0-"):
        svt_proton.latest_release()


def test_only_x86_64_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.machine", lambda: "aarch64")
    with pytest.raises(RuntimeError, match="only built for x86_64"):
        svt_proton._architecture()


# What is installed


def test_the_recorded_release_is_compared_exactly(tmp_path: Path) -> None:
    tool = _build_tool(tmp_path, marker="svt-11.0-2e")
    assert svt_proton.installed_tag(tool) == "svt-11.0-2e"
    assert svt_proton.is_current(tool, "svt-11.0-2e")
    assert not svt_proton.is_current(tool, "svt-11.0-2e-r2")
    assert not svt_proton.is_current(tool, "svt-11.0-2f")


def test_a_hand_installed_tool_is_known_by_its_version_file(
    tmp_path: Path,
) -> None:
    tool = _build_tool(tmp_path)
    assert svt_proton.installed_tag(tool) == "svt-11.0-2e"
    assert svt_proton.is_current(tool, "svt-11.0-2e")
    assert svt_proton.is_current(tool, "svt-11.0-2e-r2")  # rebuilds unknown
    assert not svt_proton.is_current(tool, "svt-11.0-2f")


def test_a_folder_without_version_information_is_not_current(
    tmp_path: Path,
) -> None:
    (tmp_path / "empty").mkdir()
    assert svt_proton.installed_tag(tmp_path / "empty") == ""
    assert not svt_proton.is_current(tmp_path / "empty", TAG)


# Installing and updating


def test_it_installs_into_steams_tool_folder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _build_tool(tmp_path / "source")
    archive = tmp_path / ARCHIVE
    downloads = _serve(monkeypatch, archive, _archive(archive, source))
    tools = tmp_path / "steam" / "compatibilitytools.d"

    assert svt_proton.install(_FakeSteam(tools)) is True  # type: ignore[arg-type]

    installed = tools / svt_proton.TOOL_NAME
    assert (installed / "compatibilitytool.vdf").read_text() == "tool"
    assert (installed / svt_proton.TAG_FILE_NAME).read_text() == TAG
    assert (installed / "proton").stat().st_mode & 0o111  # still executable
    assert (installed / "files" / "wine-link").is_symlink()
    assert (installed / "files" / "wine-link").read_text() == "wine"
    assert downloads == ["https://example.test/archive"]
    assert [path.name for path in tools.iterdir()] == [svt_proton.TOOL_NAME]
    assert "Restart Steam" in capsys.readouterr().out


def test_an_install_that_is_current_downloads_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _build_tool(tmp_path / "source")
    archive = tmp_path / ARCHIVE
    downloads = _serve(monkeypatch, archive, _archive(archive, source))
    tools = tmp_path / "tools"
    steam = _FakeSteam(tools)

    svt_proton.install(steam)  # type: ignore[arg-type]
    downloads.clear()
    assert svt_proton.install(steam) is False  # type: ignore[arg-type]
    assert downloads == []
    assert "up to date" in capsys.readouterr().out
    assert svt_proton.install(steam, force=True) is True  # type: ignore[arg-type]
    assert len(downloads) == 1


def test_an_update_replaces_the_old_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tools = tmp_path / "tools"
    old = _build_tool(tools, marker="svt-11.0-2d")
    (old / "left-over-from-2d").write_text("old")
    source = _build_tool(tmp_path / "source")
    archive = tmp_path / ARCHIVE
    _serve(monkeypatch, archive, _archive(archive, source))

    assert svt_proton.install(_FakeSteam(tools)) is True  # type: ignore[arg-type]

    installed = tools / svt_proton.TOOL_NAME
    assert not (installed / "left-over-from-2d").exists()
    assert (installed / svt_proton.TAG_FILE_NAME).read_text() == TAG
    assert [path.name for path in tools.iterdir()] == [svt_proton.TOOL_NAME]


def test_a_bad_checksum_leaves_the_installed_tool_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tools = tmp_path / "tools"
    old = _build_tool(tools, marker="svt-11.0-2d")
    source = _build_tool(tmp_path / "source")
    archive = tmp_path / ARCHIVE
    _archive(archive, source)
    _serve(monkeypatch, archive, "0" * 64)

    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        svt_proton.install(_FakeSteam(tools))  # type: ignore[arg-type]

    assert (old / svt_proton.TAG_FILE_NAME).read_text() == "svt-11.0-2d"
    assert [path.name for path in tools.iterdir()] == [svt_proton.TOOL_NAME]


def test_an_archive_with_anything_else_in_it_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _build_tool(tmp_path / "source")
    archive = tmp_path / ARCHIVE
    checksum = _archive(archive, source, ("stray-file", b"x"))
    _serve(monkeypatch, archive, checksum)
    tools = tmp_path / "tools"

    with pytest.raises(RuntimeError, match="Unexpected file"):
        svt_proton.install(_FakeSteam(tools))  # type: ignore[arg-type]
    assert not (tools / svt_proton.TOOL_NAME).exists()
    assert list(tools.iterdir()) == []


def test_an_archive_cannot_write_outside_its_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _build_tool(tmp_path / "source")
    archive = tmp_path / ARCHIVE
    checksum = _archive(
        archive, source, (f"{svt_proton.TOOL_NAME}/../../escaped", b"x")
    )
    _serve(monkeypatch, archive, checksum)
    tools = tmp_path / "steam" / "tools"

    with pytest.raises(tarfile.TarError):
        svt_proton.install(_FakeSteam(tools))  # type: ignore[arg-type]
    assert not (tmp_path / "steam" / "escaped").exists()
    assert not (tools / svt_proton.TOOL_NAME).exists()


def test_a_release_without_checksums_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = Release(tag=TAG, asset_urls={Path(ARCHIVE): "https://x/a"})
    monkeypatch.setattr(svt_proton, "latest_release", lambda: release)
    monkeypatch.setattr(svt_proton, "_architecture", lambda: "x86_64")
    with pytest.raises(RuntimeError, match=r"has no SHA256SUMS\.txt"):
        svt_proton.install(_FakeSteam(tmp_path))  # type: ignore[arg-type]


def test_a_release_without_this_architectures_archive_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = Release(tag=TAG, asset_urls={})
    monkeypatch.setattr(svt_proton, "latest_release", lambda: release)
    monkeypatch.setattr(svt_proton, "_architecture", lambda: "x86_64")
    with pytest.raises(RuntimeError, match=ARCHIVE):
        svt_proton.install(_FakeSteam(tmp_path))  # type: ignore[arg-type]


# Whether the game uses it


def _ensure(steam: _FakeSteam, *, allow_any: bool = False) -> bool:
    return svt_proton.ensure_game_uses(
        steam,  # type: ignore[arg-type]
        "433850",
        game_name="Z1 Battle Royale",
        override_flag=FLAG,
        allow_any=allow_any,
    )


def _no_update_check(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    checked: list[object] = []
    monkeypatch.setattr(svt_proton, "warn_if_update_available", checked.append)
    return checked


def test_a_game_on_svt_proton_may_go_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checked = _no_update_check(monkeypatch)
    tool = _tool_entry("SVT-Proton-11.0", tmp_path)
    steam = _FakeSteam(tmp_path, chosen="SVT-Proton-11.0", tools=[tool])
    assert _ensure(steam) is True
    assert checked == [tool]


def test_a_game_on_another_proton_is_refused_and_told_how_to_fix_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _no_update_check(monkeypatch)
    steam = _FakeSteam(
        tmp_path,
        chosen="proton_hotfix",
        tools=[_tool_entry("proton_hotfix", tmp_path)],
    )
    with caplog.at_level(logging.ERROR):
        assert _ensure(steam) is False
    message = caplog.text
    assert "needs SVT-Proton" in message
    assert "steam-utility-launcher svt-proton" in message
    assert "restart Steam" in message
    assert "proton_hotfix anyway" in message
    assert FLAG in message


def test_it_says_to_choose_it_when_it_is_installed_but_not_chosen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _no_update_check(monkeypatch)
    steam = _FakeSteam(
        tmp_path,
        chosen="proton_hotfix",
        tools=[
            _tool_entry("proton_hotfix", tmp_path),
            _tool_entry("SVT-Proton-11.0", tmp_path),
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert _ensure(steam) is False
    assert "is set to proton_hotfix, not SVT-Proton" in caplog.text
    assert "svt-proton`" not in caplog.text  # nothing to install


def test_it_says_to_install_it_when_steam_names_it_but_it_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _no_update_check(monkeypatch)
    steam = _FakeSteam(tmp_path, chosen="SVT-Proton-11.0", tools=[])
    with caplog.at_level(logging.ERROR):
        assert _ensure(steam) is False
    assert "set to SVT-Proton-11.0 in Steam, but that isn't installed" in (
        caplog.text
    )
    assert "steam-utility-launcher svt-proton" in caplog.text
    assert "anyway" not in caplog.text  # nothing to allow: it isn't there


def test_a_chosen_tool_that_is_gone_cannot_be_allowed_either(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _no_update_check(monkeypatch)
    steam = _FakeSteam(
        tmp_path,
        chosen="proton_old",
        tools=[_tool_entry("SVT-Proton-11.0", tmp_path)],
    )
    with caplog.at_level(logging.ERROR):
        assert _ensure(steam) is False
    assert "set to proton_old in Steam, but that isn't installed" in (
        caplog.text
    )
    assert "Choose SVT-Proton-11.0 for Z1 Battle Royale" in caplog.text
    assert "anyway" not in caplog.text


def test_a_game_with_no_tool_chosen_names_steams_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _no_update_check(monkeypatch)
    with caplog.at_level(logging.ERROR):
        assert _ensure(_FakeSteam(tmp_path)) is False
    assert "SVT-Proton isn't installed" in caplog.text
    assert "Steam's default anyway" in caplog.text


def test_any_proton_can_be_allowed_with_a_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    checked = _no_update_check(monkeypatch)
    steam = _FakeSteam(
        tmp_path,
        chosen="proton_hotfix",
        tools=[_tool_entry("proton_hotfix", tmp_path)],
    )
    with caplog.at_level(logging.WARNING):
        assert _ensure(steam, allow_any=True) is True
    assert "not using SVT-Proton" in caplog.text
    assert FLAG in caplog.text
    assert checked == []


# The update hint


def _tool_at(tmp_path: Path, marker: str) -> SimpleNamespace:
    return _tool_entry(
        "SVT-Proton-11.0", _build_tool(tmp_path / "installed", marker=marker)
    )


def test_a_newer_release_is_mentioned_once_a_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(svt_proton, "XDG_DATA_ROOT", tmp_path / "data")
    calls: list[int] = []

    def latest() -> Release:
        calls.append(1)
        return Release(tag="svt-11.0-2f")

    monkeypatch.setattr(svt_proton, "latest_release", latest)
    tool = _tool_at(tmp_path, "svt-11.0-2e")

    with caplog.at_level(logging.WARNING):
        svt_proton.warn_if_update_available(tool)  # type: ignore[arg-type]
        svt_proton.warn_if_update_available(tool)  # type: ignore[arg-type]
    assert calls == [1]  # the second one was within the day
    assert caplog.text.count("update is available") == 1
    assert '"svt-11.0-2e" -> "svt-11.0-2f"' in caplog.text


def test_no_update_hint_when_it_is_the_latest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(svt_proton, "XDG_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(
        svt_proton, "latest_release", lambda: Release(tag="svt-11.0-2e")
    )
    with caplog.at_level(logging.WARNING):
        svt_proton.warn_if_update_available(
            _tool_at(tmp_path, "svt-11.0-2e")  # type: ignore[arg-type]
        )
    assert caplog.text == ""


def test_a_failed_update_check_does_not_stop_anything(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(svt_proton, "XDG_DATA_ROOT", tmp_path / "data")

    def offline() -> Release:
        msg = "offline"
        raise OSError(msg)

    monkeypatch.setattr(svt_proton, "latest_release", offline)
    with caplog.at_level(logging.WARNING):
        svt_proton.warn_if_update_available(
            _tool_at(tmp_path, "svt-11.0-2e")  # type: ignore[arg-type]
        )
    assert caplog.text == ""


# The command line


def test_the_install_command_runs_the_installer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls: object())
    )
    monkeypatch.setattr(
        svt_proton,
        "install",
        lambda _steam, *, force=False: calls.append(force),
    )
    assert application.main(["svt-proton"]) == 0
    assert application.main(["svt-proton", "--force"]) == 0
    assert calls == [False, True]


def test_a_failed_install_is_an_error_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls: object())
    )

    def fail(_steam: object, *, force: bool = False) -> bool:
        del force
        msg = "Checksum mismatch"
        raise RuntimeError(msg)

    monkeypatch.setattr(svt_proton, "install", fail)
    assert application.main(["svt-proton"]) == 1
    assert (
        "ERROR: Could not install SVT-Proton: Checksum mismatch"
        in capsys.readouterr().err
    )


def test_the_flag_that_allows_any_proton_reaches_the_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls: object())
    )

    def launch(**kwargs: bool) -> int:
        seen.append(kwargs["allow_any_proton"])
        return 0

    monkeypatch.setattr(rotk_launcher, "launch", launch)
    application.main(["rotk-launcher"])
    application.main(["rotk-launcher", FLAG])
    assert seen == [False, True]
