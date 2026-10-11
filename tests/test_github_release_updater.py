from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.error import HTTPError

import pytest

from steam_utility_launcher import github_release_updater

if TYPE_CHECKING:
    from pathlib import Path


class _Response:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self._headers = headers or {}

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self._body)
        chunk, self._body = self._body[:amount], self._body[amount:]
        return chunk


def _serve(
    monkeypatch: pytest.MonkeyPatch, pages: dict[tuple[str, str], _Response]
) -> tuple[list[tuple[str, str]], list[object]]:
    """Answers GETs from the pages. Returns the requests made and the
    connections still open (none, once the fetch is done)."""
    requests: list[tuple[str, str]] = []
    open_connections: list[object] = []

    class Connection:
        def __init__(self, host: str) -> None:
            self.host = host
            open_connections.append(self)

        def request(
            self, method: str, path: str, headers: dict[str, str]
        ) -> None:
            assert method == "GET"
            assert headers["User-Agent"] == "steam-utility-launcher"
            requests.append((self.host, path))

        def getresponse(self) -> _Response:
            return pages[requests[-1]]

        def close(self) -> None:
            open_connections.remove(self)

    monkeypatch.setattr(github_release_updater, "HTTPSConnection", Connection)
    return requests, open_connections


def test_a_get_returns_the_status_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests, still_open = _serve(
        monkeypatch, {("example.test", "/a?b=1"): _Response(200, b"hello")}
    )
    assert github_release_updater.https_get("https://example.test/a?b=1") == (
        200,
        b"hello",
    )
    assert requests == [("example.test", "/a?b=1")]
    assert still_open == []


def test_a_get_follows_redirects_to_other_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests, still_open = _serve(
        monkeypatch,
        {
            ("example.test", "/a"): _Response(
                302, headers={"Location": "https://cdn.test/file"}
            ),
            ("cdn.test", "/file"): _Response(200, b"content"),
        },
    )
    assert github_release_updater.https_get("https://example.test/a") == (
        200,
        b"content",
    )
    assert requests == [("example.test", "/a"), ("cdn.test", "/file")]
    assert still_open == []  # the redirected-from connection was closed too


def test_a_redirect_without_a_location_is_returned_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, {("example.test", "/a"): _Response(302, b"moved")})
    assert github_release_updater.https_get("https://example.test/a") == (
        302,
        b"moved",
    )


def test_a_redirect_loop_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Response(301, headers={"Location": "https://example.test/a"})
    _serve(monkeypatch, {("example.test", "/a"): loop})
    with pytest.raises(RuntimeError, match="Too many redirects"):
        github_release_updater.https_get("https://example.test/a")


def test_only_https_urls_are_fetched() -> None:
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        github_release_updater.https_get("http://example.test/a")


def test_a_download_is_saved_and_its_checksum_returned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = b"x" * (3 * 1024 * 1024 + 17)  # more than one chunk
    _serve(monkeypatch, {("example.test", "/big"): _Response(200, content)})
    destination = tmp_path / "big"
    checksum = github_release_updater.https_download(
        "https://example.test/big", destination
    )
    assert destination.read_bytes() == content
    assert checksum == hashlib.sha256(content).hexdigest()


def test_a_failed_download_is_an_error_and_saves_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _serve(monkeypatch, {("example.test", "/gone"): _Response(404)})
    destination = tmp_path / "gone"
    with pytest.raises(HTTPError):
        github_release_updater.https_download(
            "https://example.test/gone", destination
        )
    assert not destination.exists()
