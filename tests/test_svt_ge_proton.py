from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

from steam_utility_launcher import application, proton_tools, svt_ge_proton
from steam_utility_launcher.github_release_updater import Release
from steam_utility_launcher.steam import Steam
from steam_utility_launcher.utilities import rotk_launcher

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

FLAG = "--allow-any-proton"
TOOL = "SVT-GE-Proton-11"
TAG = "svt-GE-Proton11-11"


def _build_tool(directory: Path, marker: str = "") -> Path:
    tool = directory / TOOL
    tool.mkdir(parents=True)
    (tool / "compatibilitytool.vdf").write_text("tool")
    (tool / "version").write_text("1793340273 GE-Proton11-11\n")
    if marker:
        (tool / proton_tools.TAG_FILE_NAME).write_text(marker)
    return tool


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


# Whether the game uses it


def _ensure(steam: _FakeSteam, *, allow_any: bool = False) -> bool:
    return svt_ge_proton.ensure_game_uses(
        steam,  # type: ignore[arg-type]
        "433850",
        game_name="Z1 Battle Royale",
        override_flag=FLAG,
        allow_any=allow_any,
    )


def _no_update_check(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    checked: list[object] = []
    monkeypatch.setattr(
        svt_ge_proton, "warn_if_update_available", checked.append
    )
    return checked


def test_a_game_on_svt_ge_proton_may_go_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checked = _no_update_check(monkeypatch)
    tool = _tool_entry(TOOL, tmp_path)
    steam = _FakeSteam(tmp_path, chosen=TOOL, tools=[tool])
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
    assert "needs SVT-GE-Proton" in message
    assert "steam-utility-launcher proton svt-ge" in message
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
            _tool_entry(TOOL, tmp_path),
        ],
    )
    with caplog.at_level(logging.ERROR):
        assert _ensure(steam) is False
    assert "is set to proton_hotfix, not SVT-GE-Proton" in caplog.text
    assert "svt-ge`" not in caplog.text  # nothing to install


def test_it_says_to_install_it_when_steam_names_it_but_it_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _no_update_check(monkeypatch)
    steam = _FakeSteam(tmp_path, chosen=TOOL, tools=[])
    with caplog.at_level(logging.ERROR):
        assert _ensure(steam) is False
    assert "set to SVT-GE-Proton-11 in Steam, but that isn't installed" in (
        caplog.text
    )
    assert "steam-utility-launcher proton svt-ge" in caplog.text
    assert "anyway" not in caplog.text  # nothing to allow: it isn't there


def test_a_chosen_tool_that_is_gone_cannot_be_allowed_either(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _no_update_check(monkeypatch)
    steam = _FakeSteam(
        tmp_path, chosen="proton_old", tools=[_tool_entry(TOOL, tmp_path)]
    )
    with caplog.at_level(logging.ERROR):
        assert _ensure(steam) is False
    assert "set to proton_old in Steam, but that isn't installed" in (
        caplog.text
    )
    assert "Choose SVT-GE-Proton-11 for Z1 Battle Royale" in caplog.text
    assert "anyway" not in caplog.text


def test_a_game_with_no_tool_chosen_names_steams_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _no_update_check(monkeypatch)
    with caplog.at_level(logging.ERROR):
        assert _ensure(_FakeSteam(tmp_path)) is False
    assert "SVT-GE-Proton isn't installed" in caplog.text
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
    assert "not using SVT-GE-Proton" in caplog.text
    assert FLAG in caplog.text
    assert checked == []


# The update hint


def _tool_at(tmp_path: Path, marker: str) -> SimpleNamespace:
    return _tool_entry(TOOL, _build_tool(tmp_path / "installed", marker))


def test_a_newer_release_is_mentioned_once_a_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(proton_tools, "XDG_DATA_ROOT", tmp_path / "data")
    calls: list[int] = []

    def latest(_source: object, _tag: str = "") -> Release:
        calls.append(1)
        return Release(tag="svt-GE-Proton11-12")

    monkeypatch.setattr(proton_tools, "find_release", latest)
    tool = _tool_at(tmp_path, TAG)

    with caplog.at_level(logging.WARNING):
        svt_ge_proton.warn_if_update_available(tool)  # type: ignore[arg-type]
        svt_ge_proton.warn_if_update_available(tool)  # type: ignore[arg-type]
    assert calls == [1]  # the second one was within the day
    assert caplog.text.count("update is available") == 1
    assert f'"{TAG}" -> "svt-GE-Proton11-12"' in caplog.text
    assert "proton svt-ge" in caplog.text


def test_no_update_hint_when_it_is_the_latest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(proton_tools, "XDG_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(
        proton_tools, "find_release", lambda _s, _t="": Release(tag=TAG)
    )
    with caplog.at_level(logging.WARNING):
        svt_ge_proton.warn_if_update_available(
            _tool_at(tmp_path, TAG)  # type: ignore[arg-type]
        )
    assert caplog.text == ""


def test_a_failed_update_check_does_not_stop_anything(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(proton_tools, "XDG_DATA_ROOT", tmp_path / "data")

    def offline(_source: object, _tag: str = "") -> Release:
        msg = "offline"
        raise OSError(msg)

    monkeypatch.setattr(proton_tools, "find_release", offline)
    with caplog.at_level(logging.WARNING):
        svt_ge_proton.warn_if_update_available(
            _tool_at(tmp_path, TAG)  # type: ignore[arg-type]
        )
    assert caplog.text == ""


# The command line


def test_the_flag_that_allows_any_proton_reaches_the_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        Steam, "from_detection", classmethod(lambda _cls: object())
    )

    def launch(**kwargs: rotk_launcher.LaunchOptions) -> int:
        seen.append(kwargs["options"].allow_any_proton)
        return 0

    monkeypatch.setattr(rotk_launcher, "launch", launch)
    application.main(["rotk-launcher"])
    application.main(["rotk-launcher", FLAG])
    assert seen == [False, True]
