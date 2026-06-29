from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import KW_ONLY, dataclass, field
from email.message import Message
from enum import Enum, auto, unique
from http.client import HTTPSConnection
from importlib.metadata import distribution
from itertools import chain
from os import sep
from os.path import commonpath, normpath, pardir
from pathlib import Path
from re import Pattern, fullmatch
from shutil import copytree, move, rmtree
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, ClassVar
from urllib.error import HTTPError
from urllib.parse import urlparse
from zipfile import ZipFile

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Iterable
    from types import TracebackType

    from .steam import Process

logger = logging.getLogger(__name__)

HTTP_STATUS_OK = 200
HTTP_STATUS_NOT_FOUND = 404


def _validated_https_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        msg = f"Unsupported URL scheme in URL: {url}"
        raise ValueError(msg)
    if not parsed.netloc:
        msg = f"Missing host in URL: {url}"
        raise ValueError(msg)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return parsed.netloc, path


def _https_get(url: str) -> tuple[int, bytes]:
    current_url = url
    for _ in range(5):
        host, path = _validated_https_url(current_url)
        connection = HTTPSConnection(host)
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "steam-utility-launcher",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response = connection.getresponse()
        try:
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    return response.status, response.read()
                current_url = location
                continue
            return response.status, response.read()
        finally:
            connection.close()
    msg = f"Too many redirects while fetching URL: {url}"
    raise RuntimeError(msg)


def _raise_http_status_error(url: str, status_code: int) -> None:
    raise HTTPError(
        url, status_code, "only status 200 is accepted.", Message(), None
    )


@dataclass
class Release:
    tag: str
    asset_urls: dict[Path, str] = field(default_factory=dict)

    def matching_assets(self, pattern: Pattern[str]) -> Iterable[Path]:
        yield from (
            name for name in self.asset_urls if fullmatch(pattern, str(name))
        )

    def single_matching_asset(self, pattern: Pattern[str]) -> Path | None:
        result = None
        for name in self.matching_assets(pattern):
            if result is not None:
                msg = f"Multiple matches for pattern: {pattern}"
                raise RuntimeError(msg)
            result = name
        if result is None:
            logger.warning(f"No files matching pattern: {pattern}")
        return result


@dataclass
class GitHubRepository:
    name: str
    _: KW_ONLY
    organization: str

    API_BASE_URL: ClassVar[str] = "https://api.github.com"
    RETRY_COUNT: ClassVar[int] = 3

    def __str__(self) -> str:
        return f"{self.organization}/{self.name}"

    def api_url(self) -> str:
        return f"{type(self).API_BASE_URL}/repos/{self}"

    def api_release_url(self, tag: str = "") -> str:
        endpoint = "latest" if not tag else f"tags/{tag}"
        return f"{self.api_url()}/releases/{endpoint}"

    def get_release(self, tag: str = "") -> Release | None:
        url = self.api_release_url(tag=tag)
        try:
            attempts = max(type(self).RETRY_COUNT, 0) + 1
            response_status = 0
            for attempt in range(attempts):
                response_status, body = _https_get(url)
                if response_status == HTTP_STATUS_OK:
                    data = json.loads(body.decode())
                    return Release(
                        tag=data["tag_name"],
                        asset_urls={
                            Path(asset["name"]): asset["browser_download_url"]
                            for asset in data.get("assets", [])
                        },
                    )
                logger.warning(
                    f"[attempt {attempt+1}/{attempts}]"
                    f" Status {response_status} from: {url}"
                )
            _raise_http_status_error(url, response_status)
        except Exception as ex:
            if isinstance(ex, HTTPError) and ex.code == HTTP_STATUS_NOT_FOUND:
                if tag:
                    logger.warning(f"{self} has no release named {tag}")
                else:
                    logger.warning(f"{self} has no releases.")
                return None
            logger.exception("Exception when querying GitHub API: %s", url)
            raise
        return None


class SecurityError(Exception):
    """Exception raised for attempts to breach security."""


# equivalent to 0o777; like stat.S_IMODE but without the "special" bits
_POSIX_PERMISSIONS_MASK: int = (
    stat.S_IRUSR
    | stat.S_IWUSR
    | stat.S_IXUSR
    | stat.S_IRGRP
    | stat.S_IWGRP
    | stat.S_IXGRP
    | stat.S_IROTH
    | stat.S_IWOTH
    | stat.S_IXOTH
)


@dataclass(frozen=True)
class Metadata:
    permissions: int
    is_directory: bool

    @staticmethod
    def from_path(path: Path) -> Metadata:
        return Metadata(
            permissions=path.stat().st_mode & _POSIX_PERMISSIONS_MASK,
            is_directory=path.is_dir(),
        )


@dataclass(frozen=True)
class ZipEntry:
    name: str
    metadata: Metadata


def delete_path(path: Path) -> None:
    if path.exists():
        if path.is_dir():
            logger.warning(f"Deleting directory: {path}")
            path.chmod(0o700)
            rmtree(path)
        else:
            logger.warning(f"Deleting file: {path}")
            path.chmod(0o600)
            path.unlink()


def merge_manifest(
    manifest: dict[Path, Metadata], destination: dict[Path, Metadata]
) -> None:
    overlap = destination.keys() & manifest.keys()
    for path in overlap:
        logger.warning(
            f"Path {path} was already installed by an earlier package but has"
            " been overwritten by a later one."
        )
    destination |= manifest


def print_manifest(manifest: dict[Path, Metadata]) -> None:
    for path, metadata in manifest.items():
        suffix = sep if metadata.is_directory else ""
        print(f"{metadata.permissions:03o} {path}{suffix}")


class ZipPackage:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.__error_prefix = f'Zip archive "{self.path}"'
        self._zip_file = ZipFile(self.path, "r")
        common_root: str | None = None
        entries: dict[Path, ZipEntry] = {}
        processed_paths: set[Path] = set()
        for name in self._zip_file.namelist():
            info = self._zip_file.getinfo(name)
            is_directory = info.is_dir()
            posix_attributes = info.external_attr >> 16
            permissions = posix_attributes & _POSIX_PERMISSIONS_MASK
            if stat.S_IFMT(posix_attributes) == stat.S_IFLNK:
                msg = (
                    f"{self.__error_prefix} has entry that is a symlink but"
                    " due to complexity with windows support in addition to"
                    " the fact that symlinks can point to other symlinks, they"
                    f" are explicitly not supported: {path}"
                )
                raise ValueError(msg)
            # GREATLY simplifying logic by ensuring basic access for owner
            permissions |= 0o700 if is_directory else 0o600
            path = Path(normpath(name))
            if path in processed_paths:
                msg = (
                    f"{self.__error_prefix} has more than one entry that"
                    f" refers to the same path: {path}"
                )
                raise AssertionError(msg)
            processed_paths.add(path)
            if path.is_absolute():
                msg = f"{self.__error_prefix} has entry with an absolute path: {path}"
                raise SecurityError(msg)
            if pardir in path.parts:
                msg = (
                    f"{self.__error_prefix} has maliciously crafted entry"
                    f' attempting the "Zip Slip" vulnerability: {path}'
                )
                raise SecurityError(msg)
            if common_root is None:
                common_root = str(path)
                if not is_directory:
                    common_root = str(Path(str(path)).parent)
                    if common_root == ".":
                        common_root = ""
            else:
                common_root = commonpath([common_root, str(path)])

            entries[path] = ZipEntry(
                name=name,
                metadata=Metadata(
                    permissions=permissions, is_directory=is_directory
                ),
            )
        # sorted so parents will come before their children
        self._entries = dict(sorted(entries.items(), key=lambda item: item[0]))
        self._common_root = Path(common_root or "")

    def entries(self) -> dict[Path, ZipEntry]:
        return dict(self._entries)

    @property
    def common_root(self) -> Path:
        return Path(self._common_root)

    @property
    def common_root_depth(self) -> int:
        return len(self._common_root.parts)

    def extract(
        self, destination: Path, *, strip_components: int = 0
    ) -> dict[Path, Metadata]:
        if strip_components > self.common_root_depth:
            msg = (
                f"{self.__error_prefix} only has {self.common_root_depth}"
                f" common root component(s), but {strip_components}"
                " component(s) were requested to be stripped."
            )
            raise ValueError(msg)
        destination = destination.resolve()
        manifest: dict[Path, Metadata] = {}
        for path, entry in self._entries.items():
            stripped_path = Path(*path.parts[strip_components:])
            if not stripped_path.parts:
                logger.debug(f"Skipping stripped component: {path}")
                continue
            manifest[stripped_path] = entry.metadata
            installed_path = destination / stripped_path
            with self._zip_file.open(entry.name) as entry_file:
                if installed_path.exists():
                    logger.warning(
                        f"Removing already existing path: {installed_path}"
                    )
                    delete_path(installed_path)
                if entry.metadata.is_directory:
                    installed_path.mkdir(
                        mode=entry.metadata.permissions, parents=True
                    )
                else:
                    # safety for if directories aren't distinct entries
                    # in the zip and only files exist (non-standard)
                    installed_path.parent.mkdir(parents=True, exist_ok=True)
                    with installed_path.open("wb") as output_file:
                        output_file.write(entry_file.read())
                    installed_path.chmod(entry.metadata.permissions)
        return manifest

    def __enter__(self) -> ZipPackage:
        self._zip_file.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: TracebackType | None,
    ) -> None:
        self._zip_file.__exit__(exc_type, exc_value, exc_traceback)

    def close(self) -> None:
        self._zip_file.close()


@unique
class ArchiveFormat(Enum):
    ZIP = auto()
    TGZ = auto()
    TAR = auto()


@dataclass(kw_only=True)
class Asset:
    pattern: Pattern[str]
    archive_format: ArchiveFormat | None  # no default; explicit is clearer
    destination: Path = Path()
    strip_archive_components: int = 0
    rename_file: str = ""

    def __post_init__(self) -> None:
        if self.destination.is_absolute():
            msg = (
                f"Asset matching pattern {self.pattern.pattern!r} has"
                f" absolute path for its destination: {self.destination}"
            )
            raise ValueError(msg)


XDG_DATA_ROOT = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    / distribution(__package__.split(".")[0]).metadata["Name"]
)


@dataclass(kw_only=True)
class ApplicationUpdater:
    name: str
    repository: GitHubRepository
    assets: list[Asset]
    preserved_paths: set[Path]
    launch_registry_set: dict[str, str] = field(default_factory=dict)
    launch_registry_unset: set[str] = field(default_factory=set)

    TAG_FILE_NAME: ClassVar[str] = ".github_release_tag"

    @property
    def default_install_directory(self) -> Path:
        return XDG_DATA_ROOT / self.name

    def __post_init__(self) -> None:
        for path in self.preserved_paths:
            if path.is_absolute():
                msg = f"User data path is an absolute path: {path}"
                raise ValueError(msg)

    def validate_release(self, release: Release) -> None:
        for asset in self.assets:
            name = release.single_matching_asset(asset.pattern)
            if not name:
                msg = (
                    f"Release {release.tag} does not contain an asset that"
                    f" matches the pattern: {asset.pattern.pattern!r}"
                )
                raise ValueError(msg)
            if asset.archive_format:
                if asset.rename_file:
                    msg = (
                        f"Release asset {name!r} is an archive, but"
                        " rename_file was specified."
                    )
                    raise ValueError(msg)
            elif asset.strip_archive_components:
                msg = (
                    f"Release asset {name!r} is not an archive, but"
                    " strip_archive_components was specified."
                )
                raise ValueError(msg)

    def _resolve_directories(
        self, install_directory: Path | None, staging_directory: Path | None
    ) -> tuple[Path, Path | None]:
        resolved_install = (
            install_directory or self.default_install_directory
        ).resolve()
        resolved_staging = (
            staging_directory.resolve() if staging_directory else None
        )
        if resolved_staging and resolved_staging == resolved_install:
            msg = (
                "staging_directory refers to the same path as"
                f" install_directory: {resolved_staging}"
            )
            raise ValueError(msg)
        return resolved_install, resolved_staging

    def update(
        self,
        install_directory: Path | None = None,
        *,
        tag: str = "",
        staging_directory: Path | None = None,
    ) -> dict[Path, Metadata]:
        install_directory, staging_directory = self._resolve_directories(
            install_directory, staging_directory
        )
        tag_file = install_directory / type(self).TAG_FILE_NAME
        release = self.repository.get_release(tag=tag)
        if not release:
            logger.info(f"No release found for: {self.repository}")
            return {}
        installed_tag = (
            tag_file.read_text().strip() if tag_file.exists() else ""
        )
        if installed_tag == release.tag:
            logger.info(f'Installed tag "{installed_tag}" is the latest.')
            return {}
        self.validate_release(release)  # ensure release has what we need
        logger.info(f'Updating tag from "{installed_tag}" to "{release.tag}"')
        full_manifest: dict[Path, Metadata] = {}
        with (
            TemporaryDirectory() as str_asset_directory,
            TemporaryDirectory(dir=staging_directory) as str_staging_directory,
        ):
            asset_directory = Path(str_asset_directory)
            staging_directory = Path(str_staging_directory)
            logger.debug(f"Downloading assets into: {asset_directory}")
            logger.debug(f"Staging into: {staging_directory}")
            asset_directory.mkdir(parents=True, exist_ok=True)
            self._download_assets_to_staging(
                release=release,
                asset_directory=asset_directory,
                staging_directory=staging_directory,
                full_manifest=full_manifest,
            )
            self._move_preserved_paths(
                install_directory=install_directory,
                staging_directory=staging_directory,
                full_manifest=full_manifest,
            )
            logger.debug("Deleting old installation...")
            delete_path(install_directory)
            install_directory.parent.mkdir(parents=True, exist_ok=True)
            logger.debug("Moving staging to the install directory...")
            copytree(staging_directory, install_directory)
            rmtree(staging_directory)
            tag_file.write_text(release.tag)
        logger.debug(f"Successfully updated to release: {release.tag}")
        # sorted so parents will come before their children
        return dict(sorted(full_manifest.items(), key=lambda item: item[0]))

    def _download_assets_to_staging(
        self,
        *,
        release: Release,
        asset_directory: Path,
        staging_directory: Path,
        full_manifest: dict[Path, Metadata],
    ) -> None:
        for asset in self.assets:
            manifest = self._install_asset(
                asset=asset,
                release=release,
                asset_directory=asset_directory,
                staging_directory=staging_directory,
            )
            merge_manifest(manifest, destination=full_manifest)

    def _install_asset(
        self,
        *,
        asset: Asset,
        release: Release,
        asset_directory: Path,
        staging_directory: Path,
    ) -> dict[Path, Metadata]:
        name = release.single_matching_asset(asset.pattern)
        if not name:
            msg = "Unreachable code: asset already verified to exist."
            raise AssertionError(msg)
        asset_url = release.asset_urls[name]
        staging_destination = staging_directory / asset.destination
        staging_destination.mkdir(parents=True, exist_ok=True)
        target_file = (
            asset_directory / name
            if asset.archive_format
            else staging_destination / (asset.rename_file or name)
        )
        logger.debug(f"Downloading {asset_url} to: {target_file}")
        _, content = _https_get(asset_url)
        target_file.write_bytes(content)
        if not asset.archive_format:
            return {
                target_file.relative_to(staging_directory): Metadata.from_path(
                    target_file
                )
            }
        if asset.archive_format == ArchiveFormat.ZIP:
            logger.debug(f"Extracting {target_file} to: {staging_destination}")
            with ZipPackage(target_file) as package:
                manifest = package.extract(
                    staging_destination,
                    strip_components=asset.strip_archive_components,
                )
            target_file.unlink()
            return manifest
        msg = f"Archive type {asset.archive_format}"
        raise NotImplementedError(msg)

    def _move_preserved_paths(
        self,
        *,
        install_directory: Path,
        staging_directory: Path,
        full_manifest: dict[Path, Metadata],
    ) -> None:
        logger.debug("Processing preserved paths...")
        for path in self.preserved_paths:
            preserved_path = install_directory / path
            if not preserved_path.exists():
                continue
            target_path = staging_directory / path
            delete_path(target_path)
            move(preserved_path, target_path)
            entries: Iterable[Path] = [target_path]
            if target_path.is_dir():
                entries = chain(entries, target_path.rglob("*"))
            manifest = {
                entry.relative_to(staging_directory): Metadata.from_path(entry)
                for entry in entries
            }
            merge_manifest(manifest, destination=full_manifest)

    def launch(self, processes: list[Process], *, wait: bool = False) -> None:
        for process in processes:
            process.registry_set |= self.launch_registry_set
            process.registry_unset |= self.launch_registry_unset
        child_processes: list[subprocess.Popen[bytes]] = [
            process.start() for process in processes
        ]
        for child in child_processes:
            logger.info("Started subprocess pid=%s", child.pid)
        if wait:
            logger.info("Waiting for all subprocesses to exit...")
            try:
                for child in child_processes:
                    return_code = child.wait()
                    if return_code == 0:
                        logger.info(
                            "Subprocess pid=%s exited successfully", child.pid
                        )
                    else:
                        logger.warning(
                            "Subprocess pid=%s exited with code %s",
                            child.pid,
                            return_code,
                        )
            except KeyboardInterrupt:
                logger.warning(
                    "Interrupted; terminating running subprocesses..."
                )
                for child in child_processes:
                    if child.poll() is None:
                        child.terminate()
                raise
            logger.info("All subprocesses have exited.")
