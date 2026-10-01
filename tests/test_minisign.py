from __future__ import annotations

import base64

import pytest

from steam_utility_launcher import minisign

from .signing import make_keys, sign, wrap

# ZEmu's real updater public key and the real signature of its v0.1.52
# AppImage, to make sure the format handling matches what is actually published.
_ZEMU_PUBLIC_KEY = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEQyNDcxRTAzNkU5MjFB"
    "RDIKUldUU0dwSnVBeDVIMGhjNVQ2QVBsdTNsNDNpNGc4MGNTL0hyeHRkSU4vZUJXZG85bmJq"
    "U29TVXYK"
)
_ZEMU_SIGNATURE = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVU"
    "U0dwSnVBeDVIMG84bHd3citldXFYOWkvemVXVUo0L1R2b3dxNDlzOVM0SFFRaDhNK3pGanJQ"
    "UnpzSHJ4WXMrTjkzMmlHQnlBSGdhTGNIeDNUcVJlZE4xcFdIdWxXeXc4PQp0cnVzdGVkIGNv"
    "bW1lbnQ6IHRpbWVzdGFtcDoxNzkwMTcwMzM1CWZpbGU6WkVtdSBMYXVuY2hlcl8wLjEuNTJf"
    "YW1kNjQuQXBwSW1hZ2UKWUgrTHhCZ3RYN3JwN2hCSVM3VlcwVUJQMXk0Rzdsc21pWFVFZVBq"
    "VUNpZktUZzhiVk1SV1JqeUpjdVdUSHlMaHJUaFNPdzZ4Y1hPdmNrQnoxMFJnRGc9PQo="
)


@pytest.mark.parametrize("prehashed", [True, False])
def test_valid_signature_verifies(prehashed: bool) -> None:
    private, public = make_keys()
    content = b"the quick brown fox" * 1000
    signature = sign(private, content, prehashed=prehashed)
    minisign.verify(
        minisign.parse_public_key(public),
        minisign.parse_signature(signature),
        content,
    )


def test_tampered_content_is_rejected() -> None:
    private, public = make_keys()
    signature = sign(private, b"original")
    with pytest.raises(minisign.SignatureError):
        minisign.verify(
            minisign.parse_public_key(public),
            minisign.parse_signature(signature),
            b"originaL",
        )


def test_a_different_key_is_rejected_even_with_the_same_id() -> None:
    signer, _ = make_keys()
    _, other_public = make_keys()
    signature = sign(signer, b"data")
    with pytest.raises(minisign.SignatureError):
        minisign.verify(
            minisign.parse_public_key(other_public),
            minisign.parse_signature(signature),
            b"data",
        )


def test_a_different_key_id_is_rejected() -> None:
    private, public = make_keys()
    signature = sign(private, b"data", key_id=bytes(range(10, 18)))
    with pytest.raises(minisign.SignatureError, match="expected key"):
        minisign.verify(
            minisign.parse_public_key(public),
            minisign.parse_signature(signature),
            b"data",
        )


def test_a_swapped_trusted_comment_is_rejected() -> None:
    private, public = make_keys()
    good = sign(private, b"data")
    parsed = minisign.parse_signature(good)
    forged = minisign.Signature(
        algorithm=parsed.algorithm,
        key_id=parsed.key_id,
        signature=parsed.signature,
        trusted_comment="timestamp:1\tfile:something else",
        global_signature=parsed.global_signature,
    )
    with pytest.raises(minisign.SignatureError):
        minisign.verify(minisign.parse_public_key(public), forged, b"data")


def test_a_flipped_signature_byte_is_rejected() -> None:
    private, public = make_keys()
    parsed = minisign.parse_signature(sign(private, b"data"))
    damaged = bytearray(parsed.signature)
    damaged[0] ^= 1
    forged = minisign.Signature(
        algorithm=parsed.algorithm,
        key_id=parsed.key_id,
        signature=bytes(damaged),
        trusted_comment=parsed.trusted_comment,
        global_signature=parsed.global_signature,
    )
    with pytest.raises(minisign.SignatureError):
        minisign.verify(minisign.parse_public_key(public), forged, b"data")


def test_trusted_file_name_is_exposed() -> None:
    private, _ = make_keys()
    parsed = minisign.parse_signature(sign(private, b"data"))
    assert parsed.trusted_file_name == "ZEmu Launcher_9.9.9_amd64.AppImage"


@pytest.mark.parametrize(
    "wrapped",
    [
        "not base64!!",
        wrap("only one line"),
        wrap("a\nb\nc"),
        wrap("a\n!!!\nc\nd"),
        wrap("a\n" + base64.b64encode(b"short").decode() + "\nc\nd"),
    ],
)
def test_malformed_signatures_are_rejected(wrapped: str) -> None:
    with pytest.raises(minisign.SignatureError):
        minisign.parse_signature(wrapped)


@pytest.mark.parametrize(
    "wrapped",
    [
        "not base64!!",
        wrap("one line"),
        wrap("a\n" + base64.b64encode(b"short").decode()),
    ],
)
def test_malformed_public_keys_are_rejected(wrapped: str) -> None:
    with pytest.raises(minisign.SignatureError):
        minisign.parse_public_key(wrapped)


def test_zemus_real_public_key_and_signature_parse() -> None:
    public = minisign.parse_public_key(_ZEMU_PUBLIC_KEY)
    signature = minisign.parse_signature(_ZEMU_SIGNATURE)
    assert public.key_id.hex() == "d21a926e031e47d2"
    assert signature.key_id == public.key_id
    assert signature.algorithm == b"ED"
    assert signature.trusted_file_name == "ZEmu Launcher_0.1.52_amd64.AppImage"


def test_zemus_real_signature_does_not_verify_other_content() -> None:
    with pytest.raises(minisign.SignatureError):
        minisign.verify(
            minisign.parse_public_key(_ZEMU_PUBLIC_KEY),
            minisign.parse_signature(_ZEMU_SIGNATURE),
            b"not the AppImage",
        )
