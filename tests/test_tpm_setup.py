from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from steam_utility_launcher import tpm_setup

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_MODULUS = bytes(range(256))
_POLICY = bytes(32)


def _ek_public(exponent: int = 0) -> bytes:
    body = (
        b"\x00\x01\x00\x0b\x00\x03\x00\xb2"
        + len(_POLICY).to_bytes(2, "big")
        + _POLICY
        + b"\x00\x06\x00\x80\x00\x43"  # symmetric
        + b"\x00\x10"  # scheme
        + b"\x08\x00"  # key bits
        + exponent.to_bytes(4, "big")
        + len(_MODULUS).to_bytes(2, "big")
        + _MODULUS
    )
    return len(body).to_bytes(2, "big") + body


def _certificate(modulus: bytes, issuer: str | None) -> bytes:
    aia = b""
    if issuer:
        encoded = issuer.encode()
        aia = (
            b"\x06\x08\x2b\x06\x01\x05\x05\x07\x30\x02\x86"
            + bytes([len(encoded)])
            + encoded
        )
    return b"\x30\x82" + modulus + aia


def test_exponent_and_modulus_are_read_with_the_default_exponent() -> None:
    assert tpm_setup.rsa_exponent_and_modulus(_ek_public()) == (
        65537,
        _MODULUS,
    )
    assert tpm_setup.rsa_exponent_and_modulus(_ek_public(3)) == (3, _MODULUS)


def test_malformed_keys_are_rejected() -> None:
    good = _ek_public()
    assert tpm_setup.rsa_exponent_and_modulus(good[:-1]) is None
    assert tpm_setup.rsa_exponent_and_modulus(b"") is None
    ecc = bytearray(good)
    ecc[3] = 0x23
    assert tpm_setup.rsa_exponent_and_modulus(bytes(ecc)) is None


def test_url_is_a_hash_of_the_key() -> None:
    digest = hashlib.sha256(
        bytes.fromhex("00002222") + (65537).to_bytes(4, "big") + _MODULUS
    ).hexdigest()[:32]
    assert (
        tpm_setup.amd_certificate_url(65537, _MODULUS)
        == f"https://ftpm.amd.com/pki/aia/{digest}"
    )


def test_issuer_urls_are_read_from_the_certificate() -> None:
    cert = _certificate(_MODULUS, "https://ftpm.amd.com/pki/aia/ABC")
    assert tpm_setup.issuer_urls(cert) == ["https://ftpm.amd.com/pki/aia/ABC"]
    assert tpm_setup.issuer_urls(_certificate(_MODULUS, None)) == []


def _prepared(directory: Path, maker: str = "AMD") -> None:
    (directory / "ek.pub").write_bytes(_ek_public())
    (directory / "manufacturer.txt").write_text(maker)


def test_the_chain_is_followed_and_saved(tmp_path: Path) -> None:
    _prepared(tmp_path)
    leaf_url = tpm_setup.amd_certificate_url(65537, _MODULUS)
    chain = {
        leaf_url: _certificate(_MODULUS, "https://ftpm.amd.com/pki/a"),
        "https://ftpm.amd.com/pki/a": _certificate(
            b"A", "https://ftpm.amd.com/pki/b"
        ),
        "https://ftpm.amd.com/pki/b": _certificate(b"B", None),
    }
    requested: list[str] = []

    def getter(url: str) -> bytes | None:
        requested.append(url)
        return chain.get(url)

    assert tpm_setup.ensure_certificates(tmp_path, getter) == 3
    assert requested == list(chain)
    assert (tmp_path / "ek-cert-1.der").read_bytes() == chain[leaf_url]
    assert (tmp_path / "ek-cert-3.der").is_file()
    assert not (tmp_path / "ek-cert-4.der").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_a_certificate_for_another_key_is_not_kept(tmp_path: Path) -> None:
    _prepared(tmp_path)
    assert (
        tpm_setup.ensure_certificates(tmp_path, lambda _u: b"\x30unrelated")
        == 0
    )
    assert not (tmp_path / "ek-cert-1.der").exists()


def test_failed_fetches_save_nothing(tmp_path: Path) -> None:
    _prepared(tmp_path)
    assert tpm_setup.ensure_certificates(tmp_path, lambda _u: None) == 0
    assert not list(tmp_path.glob("ek-cert-*"))


def test_other_makers_and_saved_chains_cause_no_requests(
    tmp_path: Path,
) -> None:
    def must_not_fetch(_url: str) -> bytes | None:
        raise AssertionError

    _prepared(tmp_path, maker="INTC")
    assert tpm_setup.ensure_certificates(tmp_path, must_not_fetch) == 0
    _prepared(tmp_path)
    (tmp_path / "ek-cert-1.der").write_bytes(b"x")
    assert tpm_setup.ensure_certificates(tmp_path, must_not_fetch) == 1


def test_a_chain_is_capped_at_four(tmp_path: Path) -> None:
    _prepared(tmp_path)

    def getter(_url: str) -> bytes | None:
        return _certificate(_MODULUS, "https://ftpm.amd.com/pki/loop")

    assert tpm_setup.ensure_certificates(tmp_path, getter) == 4


def test_only_amds_server_is_ever_contacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_connect(*_a: object, **_k: object) -> None:
        raise AssertionError

    monkeypatch.setattr(tpm_setup, "HTTPSConnection", must_not_connect)
    for url in (
        "http://ftpm.amd.com/x",
        "https://example.com/x",
        "https://ftpm.amd.com.evil.test/x",
        "https://ftpm.amd.com:8443/x",
    ):
        assert tpm_setup.fetch(url) is None


def test_preparing_is_needed_until_both_files_exist(tmp_path: Path) -> None:
    assert tpm_setup.needs_preparing(tmp_path)
    _prepared(tmp_path)
    assert not tpm_setup.needs_preparing(tmp_path)
