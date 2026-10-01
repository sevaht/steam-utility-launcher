from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from steam_utility_launcher import minisign, update_check
from steam_utility_launcher.github_release_updater import Release
from steam_utility_launcher.steam import CompatibilityTool
from steam_utility_launcher.utilities import zemu

from .signing import make_keys, sign

_ASSET = Path("ZEmu.Launcher_9.9.9_amd64.AppImage")
_SIG = Path(_ASSET.name + ".sig")
_CONTENT = b"#!/bin/sh\necho pretend this is a 100 MB AppImage\n"
_PROTON = Path("/steam/steamapps/common/Proton 10.0/proton")


# --------------------------------------------------------------- config merge


def _read(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text())
    assert isinstance(data, dict)
    return data


def test_a_fresh_config_gets_the_runtime_and_zemus_own_env_defaults(
    tmp_path: Path,
) -> None:
    config = tmp_path / "app" / "launcher-config.json"
    assert zemu.configure_wine(config, _PROTON) is True
    wine = _read(config)["wine"]
    assert isinstance(wine, dict)
    assert wine["enabled"] is True
    assert wine["runtimeId"] == "custom"
    assert wine["customRuntimePath"] == str(_PROTON)
    assert "winePrefix" not in wine  # ZEmu keeps its own dedicated prefix
    assert wine["env"] == [
        {"key": "DXVK_ASYNC", "value": "1"},
        {"key": "WINEDEBUG", "value": "-all"},
    ]
    assert not list(config.parent.glob("*.tmp"))


def test_every_other_setting_is_kept(tmp_path: Path) -> None:
    config = tmp_path / "launcher-config.json"
    config.write_text(
        json.dumps(
            {
                "authKey": "0xDEADBEEFDEADBEEF",
                "gameDirectory": "/games/h1z1",
                "theme": "dark",
                "someFutureSetting": {"x": [1, 2]},
                "wine": {"enabled": True, "winePrefix": "/my/prefix"},
            }
        )
    )
    assert zemu.configure_wine(config, _PROTON) is True
    data = _read(config)
    assert data["authKey"] == "0xDEADBEEFDEADBEEF"
    assert data["gameDirectory"] == "/games/h1z1"
    assert data["someFutureSetting"] == {"x": [1, 2]}
    wine = data["wine"]
    assert isinstance(wine, dict)
    assert wine["winePrefix"] == "/my/prefix"
    assert wine["runtimeId"] == "custom"
    assert "env" not in wine  # an existing section isn't given env it lacked


@pytest.mark.parametrize(
    "chosen",
    [
        {"runtimeId": "wine:/usr/bin/wine"},
        {"runtimeId": "custom", "customRuntimePath": "/mine/proton"},
        {"customRuntimePath": "/mine/proton"},
    ],
)
def test_a_runtime_you_chose_is_never_replaced(
    tmp_path: Path, chosen: dict[str, str]
) -> None:
    config = tmp_path / "launcher-config.json"
    config.write_text(json.dumps({"wine": {"enabled": True, **chosen}}))
    before = config.read_text()
    assert zemu.configure_wine(config, _PROTON) is False
    assert config.read_text() == before


def test_disabling_wine_is_respected(tmp_path: Path) -> None:
    config = tmp_path / "launcher-config.json"
    config.write_text(json.dumps({"wine": {"enabled": False}}))
    zemu.configure_wine(config, _PROTON)
    wine = _read(config)["wine"]
    assert isinstance(wine, dict)
    assert wine["enabled"] is False


def test_configuring_twice_changes_nothing_the_second_time(
    tmp_path: Path,
) -> None:
    config = tmp_path / "launcher-config.json"
    assert zemu.configure_wine(config, _PROTON) is True
    first = config.read_text()
    assert zemu.configure_wine(config, _PROTON) is False
    assert config.read_text() == first


@pytest.mark.parametrize("text", ["{not json", "[1, 2, 3]", '"a string"'])
def test_an_unreadable_config_is_left_alone(
    tmp_path: Path, text: str, caplog: pytest.LogCaptureFixture
) -> None:
    config = tmp_path / "launcher-config.json"
    config.write_text(text)
    with caplog.at_level("WARNING"):
        assert zemu.configure_wine(config, _PROTON) is False
    assert config.read_text() == text
    assert "Not touching" in caplog.text


# ------------------------------------------------------------ proton choice


def _tool(
    root: Path, directory: str, *, exists: bool = True
) -> CompatibilityTool:
    install = root / directory
    install.mkdir(parents=True, exist_ok=True)
    binary = install / "proton"
    if exists:
        binary.write_text("#!/bin/sh\n")
    return CompatibilityTool(
        internal_name=directory.lower().replace(" ", "_"),
        manifest_vdf=install / "toolmanifest.vdf",
        install_path=install,
        display_name=directory,
        binary_path=binary,
        binary_argument_template=[],
    )


class _FakeSteam:
    def __init__(
        self, tools: list[CompatibilityTool], z1_tool: str = ""
    ) -> None:
        self.compatibility_tools = tools
        self._z1_tool = z1_tool

    def game_compatibility_tool(self, game_id: str) -> str:
        assert game_id == "433850"
        return self._z1_tool


def test_an_explicit_proton_wins(tmp_path: Path) -> None:
    folder = tmp_path / "MyProton"
    folder.mkdir()
    (folder / "proton").write_text("x")
    steam = _FakeSteam([_tool(tmp_path, "Proton 10.0")], "proton_10")
    assert zemu.pick_proton(steam, folder) == folder / "proton"  # type: ignore[arg-type]
    assert zemu.pick_proton(steam, folder / "proton") == folder / "proton"  # type: ignore[arg-type]
    assert zemu.pick_proton(steam, tmp_path / "missing") is None  # type: ignore[arg-type]


def test_the_proton_steam_uses_for_z1_is_preferred(tmp_path: Path) -> None:
    tools = [
        _tool(tmp_path, "Proton 11.0"),
        _tool(tmp_path, "Proton 10.0"),
        _tool(tmp_path, "Proton 9.0 (Beta)"),
    ]
    # Steam's own config names it "proton_10"; the folder is "Proton 10.0".
    chosen = zemu.pick_proton(_FakeSteam(tools, "proton_10"), None)  # type: ignore[arg-type]
    assert chosen == tools[1].binary_path


def test_otherwise_the_newest_stable_proton_is_chosen(tmp_path: Path) -> None:
    tools = [
        _tool(tmp_path, "Proton 9.0"),
        _tool(tmp_path, "Proton 10.0"),
        _tool(tmp_path, "Proton - Experimental"),
        _tool(tmp_path, "Proton Hotfix"),
        _tool(tmp_path, "Proton 9.0 (Beta)"),
    ]
    chosen = zemu.pick_proton(_FakeSteam(tools), None)  # type: ignore[arg-type]
    assert chosen == tools[1].binary_path


def test_a_missing_binary_is_skipped(tmp_path: Path) -> None:
    broken = _tool(tmp_path, "Proton 11.0", exists=False)
    working = _tool(tmp_path, "Proton 10.0")
    chosen = zemu.pick_proton(
        _FakeSteam([broken, working], "proton_11"), None  # type: ignore[arg-type]
    )
    assert chosen == working.binary_path


def test_no_proton_means_none(tmp_path: Path) -> None:
    only_experimental = [_tool(tmp_path, "Proton - Experimental")]
    assert zemu.pick_proton(_FakeSteam(only_experimental), None) is None  # type: ignore[arg-type]
    assert zemu.pick_proton(_FakeSteam([]), None) is None  # type: ignore[arg-type]


# ----------------------------------------------------------- install + verify


class _World:
    """A fake GitHub release plus a fake network, signed with a test key."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self.private, self.public_key = make_keys()
        self.content = _CONTENT
        self.signature = sign(self.private, self.content)
        self.tag = "v9.9.9"
        self.downloads: list[str] = []
        self.release: Release | None = Release(
            tag=self.tag,
            asset_urls={_ASSET: "https://x/app", _SIG: "https://x/app.sig"},
        )
        monkeypatch.setattr(zemu, "XDG_DATA_ROOT", tmp_path / "data")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(zemu, "_PUBLIC_KEY", self.public_key)
        monkeypatch.setattr(zemu, "https_get", self._get)
        monkeypatch.setattr(zemu, "_REPOSITORY", self)

    def get_release(self, tag: str = "") -> Release | None:
        assert tag == ""
        return self.release

    def _get(self, url: str) -> tuple[int, bytes]:
        self.downloads.append(url)
        if url == "https://x/app":
            return 200, self.content
        if url == "https://x/app.sig":
            return 200, self.signature.encode()
        return 404, b""

    @property
    def appimage(self) -> Path:
        return zemu.install_directory() / "ZEmu-Launcher.AppImage"


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _World:
    return _World(monkeypatch, tmp_path)


def test_a_fresh_install_is_verified_executable_and_tagged(
    world: _World,
) -> None:
    zemu._install_if_needed(force=False)
    assert world.appimage.read_bytes() == _CONTENT
    assert world.appimage.stat().st_mode & stat.S_IXUSR
    tag = (zemu.install_directory() / ".github_release_tag").read_text()
    assert tag == "v9.9.9"
    assert not list(zemu.install_directory().glob("*.part"))


def test_tampered_content_is_never_installed(world: _World) -> None:
    world.content = _CONTENT + b"malware"
    with pytest.raises(zemu.InstallError, match="signature verification"):
        zemu._install_if_needed(force=False)
    assert not world.appimage.exists()
    assert not list(zemu.install_directory().glob("*.part"))


def test_a_signature_from_another_key_is_refused(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, other_public = make_keys()
    monkeypatch.setattr(zemu, "_PUBLIC_KEY", other_public)
    with pytest.raises(zemu.InstallError, match="signature verification"):
        zemu._install_if_needed(force=False)
    assert not world.appimage.exists()


def test_a_signature_for_a_different_file_is_refused(world: _World) -> None:
    world.signature = sign(
        world.private,
        world.content,
        comment="timestamp:1\tfile:ZEmu Launcher_1.0.0_amd64.AppImage",
    )
    with pytest.raises(zemu.InstallError, match="different file"):
        zemu._install_if_needed(force=False)
    assert not world.appimage.exists()


def test_a_release_without_a_signature_is_refused(world: _World) -> None:
    world.release = Release(tag="v9.9.9", asset_urls={_ASSET: "https://x/app"})
    with pytest.raises(zemu.InstallError, match="no signature"):
        zemu._install_if_needed(force=False)
    assert not world.appimage.exists()


@pytest.mark.usefixtures("world")
def test_an_http_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zemu, "https_get", lambda _url: (503, b""))
    with pytest.raises(zemu.InstallError, match="HTTP status 503"):
        zemu._install_if_needed(force=False)


def test_no_release_or_no_matching_asset_is_an_error(world: _World) -> None:
    world.release = None
    with pytest.raises(zemu.InstallError, match="No release"):
        zemu._install_if_needed(force=False)
    world.release = Release(tag="v1", asset_urls={Path("other.zip"): "u"})
    with pytest.raises(zemu.InstallError, match="no ZEmu Launcher download"):
        zemu._install_if_needed(force=False)


def test_an_installed_launcher_checked_today_does_no_network(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    zemu._install_if_needed(force=False)
    world.downloads.clear()
    monkeypatch.setattr(zemu, "_REPOSITORY", None)  # would crash if consulted
    zemu._install_if_needed(force=False)
    assert world.downloads == []


def test_a_newer_release_only_warns_unless_forced(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    zemu._install_if_needed(force=False)
    (zemu.install_directory() / update_check.MARKER_FILE_NAME).unlink()
    world.release = Release(
        tag="v10.0.0",
        asset_urls={_ASSET: "https://x/app", _SIG: "https://x/app.sig"},
    )
    world.downloads.clear()
    with caplog.at_level("WARNING"):
        zemu._install_if_needed(force=False)
    assert "update available" in caplog.text
    assert world.downloads == []  # warned, did not download
    tag_file = zemu.install_directory() / ".github_release_tag"
    assert tag_file.read_text() == "v9.9.9"

    zemu._install_if_needed(force=True)
    assert tag_file.read_text() == "v10.0.0"
    assert world.downloads  # --force did download


# --------------------------------------------------------------------- launch


class _FakeProcess:
    instances: list[_FakeProcess]

    def __init__(
        self,
        command_line: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: Path,
    ) -> None:
        self.command_line = command_line
        self.env = env
        self.cwd = cwd
        type(self).instances.append(self)

    def start(self) -> _FakeProcess:
        return self

    def wait(self) -> int:
        return 3


@pytest.fixture
def launched(
    world: _World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[_FakeProcess]:
    assert world.tag  # requested for its fake release and network
    _FakeProcess.instances = []
    monkeypatch.setattr(zemu, "Process", _FakeProcess)
    monkeypatch.setattr(
        "steam_utility_launcher.utilities.zemu.sys.platform", "linux"
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return _FakeProcess.instances


def test_launch_installs_configures_and_runs_the_appimage(
    launched: list[_FakeProcess],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tool = _tool(tmp_path / "steam", "Proton 10.0")
    steam = _FakeSteam([tool], "proton_10")
    assert zemu.launch(steam=steam) == 3  # type: ignore[arg-type]
    (process,) = launched
    assert process.command_line == [
        str(zemu.install_directory() / "ZEmu-Launcher.AppImage")
    ]
    assert process.cwd == zemu.install_directory()
    assert (
        "zemu.uk" in capsys.readouterr().err
    )  # the credit the licence asks for
    config = tmp_path / "xdg" / "uk.zemu.launcher" / "launcher-config.json"
    wine = _read(config)["wine"]
    assert isinstance(wine, dict)
    assert wine["customRuntimePath"] == str(tool.binary_path)


def test_no_configure_leaves_zemus_settings_alone(
    launched: list[_FakeProcess], tmp_path: Path
) -> None:
    steam = _FakeSteam([_tool(tmp_path / "steam", "Proton 10.0")], "proton_10")
    assert zemu.launch(steam=steam, configure=False) == 3  # type: ignore[arg-type]
    assert len(launched) == 1
    assert not (tmp_path / "xdg" / "uk.zemu.launcher").exists()


def test_without_any_proton_it_still_launches_with_a_warning(
    launched: list[_FakeProcess], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        assert zemu.launch(steam=_FakeSteam([])) == 3  # type: ignore[arg-type]
    assert "No Proton install found" in caplog.text
    assert len(launched) == 1


def test_a_failed_install_stops_before_anything_runs(
    launched: list[_FakeProcess],
    world: _World,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world.content = b"tampered"
    with caplog.at_level("ERROR"):
        assert zemu.launch(steam=_FakeSteam([])) == 1  # type: ignore[arg-type]
    assert "signature verification" in caplog.text
    assert launched == []


def test_other_platforms_are_refused(
    launched: list[_FakeProcess], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert zemu.launch(steam=None) == 1
    assert launched == []


# -------------------------------------------------------------------- windows

_WINDOWS_ASSET = Path("ZEmu.Launcher_9.9.9_x64-setup.exe")
_WINDOWS_SIG = Path(_WINDOWS_ASSET.name + ".sig")


class _Installer:
    """Stands in for running the NSIS installer."""

    def __init__(self, *, exit_code: int = 0, files: tuple[str, ...]) -> None:
        self.exit_code = exit_code
        self.files = files
        self.calls: list[tuple[bytes, Path]] = []

    def __call__(self, installer: Path, target: Path) -> int:
        self.calls.append((installer.read_bytes(), target))
        if self.exit_code == 0:
            target.mkdir(parents=True, exist_ok=True)
            for name in self.files:
                (target / name).write_bytes(b"MZ")
        return self.exit_code


@pytest.fixture
def windows(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> tuple[_World, _Installer]:
    monkeypatch.setattr(sys, "platform", "win32")
    world.release = Release(
        tag=world.tag,
        asset_urls={
            _WINDOWS_ASSET: "https://x/app",
            _WINDOWS_SIG: "https://x/app.sig",
            # The Linux download must be ignored on Windows.
            _ASSET: "https://x/linux",
            _SIG: "https://x/linux.sig",
        },
    )
    world.signature = sign(
        world.private,
        world.content,
        comment=f"timestamp:1\tfile:{_WINDOWS_ASSET.name}",
    )
    installer = _Installer(files=("ZEmu Launcher.exe", "uninstall.exe"))
    monkeypatch.setattr(zemu, "_run_windows_installer", installer)
    return world, installer


def test_windows_installs_the_verified_installer_into_the_app_directory(
    windows: tuple[_World, _Installer],
) -> None:
    _, installer = windows
    zemu._install_if_needed(force=False)
    assert installer.calls == [(_CONTENT, zemu.windows_app_directory())]
    executable = zemu.installed_executable()
    assert executable is not None
    assert executable.name == "ZEmu Launcher.exe"
    tag = (zemu.install_directory() / ".github_release_tag").read_text()
    assert tag == "v9.9.9"


def test_windows_never_runs_an_installer_that_fails_verification(
    windows: tuple[_World, _Installer],
) -> None:
    world, installer = windows
    world.content = _CONTENT + b"malware"
    with pytest.raises(zemu.InstallError, match="signature verification"):
        zemu._install_if_needed(force=False)
    assert installer.calls == []
    assert zemu.installed_executable() is None


@pytest.mark.usefixtures("windows")
def test_windows_installer_failure_is_reported_and_not_tagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        zemu, "_run_windows_installer", _Installer(exit_code=2, files=())
    )
    with pytest.raises(zemu.InstallError, match="status 2"):
        zemu._install_if_needed(force=False)
    assert not (zemu.install_directory() / ".github_release_tag").exists()


@pytest.mark.usefixtures("windows")
def test_windows_install_that_leaves_no_app_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        zemu, "_run_windows_installer", _Installer(files=("uninstall.exe",))
    )
    with pytest.raises(zemu.InstallError, match="wasn't found"):
        zemu._install_if_needed(force=False)
    assert not (zemu.install_directory() / ".github_release_tag").exists()


@pytest.mark.usefixtures("windows")
def test_windows_an_ambiguous_app_directory_is_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        zemu,
        "_run_windows_installer",
        _Installer(files=("a.exe", "b.exe", "uninstall.exe")),
    )
    with pytest.raises(zemu.InstallError, match="wasn't found"):
        zemu._install_if_needed(force=False)


def test_windows_without_a_windows_download_is_an_error(
    windows: tuple[_World, _Installer],
) -> None:
    world, _ = windows
    world.release = Release(tag="v1", asset_urls={_ASSET: "u", _SIG: "s"})
    with pytest.raises(zemu.InstallError, match="no ZEmu Launcher download"):
        zemu._install_if_needed(force=False)


@pytest.mark.usefixtures("windows")
def test_windows_launch_runs_the_app_without_proton_or_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _FakeProcess.instances = []
    monkeypatch.setattr(zemu, "Process", _FakeProcess)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert zemu.launch(steam=None) == 3
    (process,) = _FakeProcess.instances
    assert process.command_line == [
        str(zemu.windows_app_directory() / "ZEmu Launcher.exe")
    ]
    assert not (tmp_path / "xdg" / "uk.zemu.launcher").exists()


# ------------------------------------------------------------ launch environment


@pytest.mark.parametrize("fuse", [True, False])
def test_appimage_extract_and_run_only_when_fuse2_is_missing(
    monkeypatch: pytest.MonkeyPatch, fuse: bool
) -> None:
    monkeypatch.delenv("APPIMAGE_EXTRACT_AND_RUN", raising=False)
    monkeypatch.setattr(zemu, "_fuse2_available", lambda: fuse)
    env = zemu.launch_environment()
    assert ("APPIMAGE_EXTRACT_AND_RUN" in env) is (not fuse)


def test_your_own_webkit_setting_is_not_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(zemu, "_fuse2_available", lambda: True)
    monkeypatch.delenv("WEBKIT_DISABLE_DMABUF_RENDERER", raising=False)
    assert zemu.launch_environment()["WEBKIT_DISABLE_DMABUF_RENDERER"] == "1"
    monkeypatch.setenv("WEBKIT_DISABLE_DMABUF_RENDERER", "0")
    assert zemu.launch_environment()["WEBKIT_DISABLE_DMABUF_RENDERER"] == "0"


def test_the_pinned_public_key_is_zemus() -> None:
    key = minisign.parse_public_key(zemu._PUBLIC_KEY)
    assert key.key_id.hex() == "d21a926e031e47d2"
