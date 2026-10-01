"""Verification of minisign signatures, the format Tauri's updater signs with.

Tauri wraps each minisign file in one more layer of base64: the updater public
key in `tauri.conf.json` and every `.sig` release asset are the base64 of the
two- and four-line minisign text files respectively.

  public key file          signature file
  ----------------         ---------------
  untrusted comment: ...   untrusted comment: ...
  <base64 key>             <base64 signature>
                           trusted comment: ...
                           <base64 global signature>

The key is `b"Ed"` + 8-byte key id + 32-byte Ed25519 key. The signature is
`b"ED"` (signs the BLAKE2b-512 hash of the file; what Tauri produces) or the
legacy `b"Ed"` (signs the file itself) + key id + 64-byte signature. The global
signature covers the file signature followed by the trusted comment, so the
comment (which carries the file name and a timestamp) can't be swapped.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_ALGORITHM_PLAIN = b"Ed"
_ALGORITHM_PREHASHED = b"ED"
_KEY_ID_SIZE = 8
_KEY_SIZE = 32
_SIGNATURE_SIZE = 64
_TRUSTED_COMMENT_PREFIX = "trusted comment: "


class SignatureError(Exception):
    """The signature is malformed, or doesn't match the key and content."""


@dataclass(frozen=True)
class PublicKey:
    key_id: bytes
    key: bytes


@dataclass(frozen=True)
class Signature:
    algorithm: bytes
    key_id: bytes
    signature: bytes
    trusted_comment: str
    global_signature: bytes

    @property
    def trusted_file_name(self) -> str | None:
        """The `file:` field of the trusted comment, if it has one."""
        for field in self.trusted_comment.split("\t"):
            if field.startswith("file:"):
                return field[len("file:") :]
        return None


def _unwrap(wrapped: str, *, minimum_lines: int, what: str) -> list[str]:
    try:
        text = base64.b64decode(wrapped.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as error:
        msg = f"The {what} is not valid base64-wrapped minisign text."
        raise SignatureError(msg) from error
    lines = text.splitlines()
    if len(lines) < minimum_lines:
        msg = f"The {what} has {len(lines)} lines; expected {minimum_lines}."
        raise SignatureError(msg)
    return lines


def _decode(line: str, *, size: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(line.strip(), validate=True)
    except binascii.Error as error:
        msg = f"The {what} is not valid base64."
        raise SignatureError(msg) from error
    if len(raw) != size:
        msg = f"The {what} is {len(raw)} bytes; expected {size}."
        raise SignatureError(msg)
    return raw


def parse_public_key(wrapped: str) -> PublicKey:
    lines = _unwrap(wrapped, minimum_lines=2, what="public key")
    raw = _decode(
        lines[1], size=2 + _KEY_ID_SIZE + _KEY_SIZE, what="public key data"
    )
    if raw[:2] != _ALGORITHM_PLAIN:
        msg = f"Unsupported public key algorithm: {raw[:2]!r}"
        raise SignatureError(msg)
    return PublicKey(
        key_id=raw[2 : 2 + _KEY_ID_SIZE], key=raw[2 + _KEY_ID_SIZE :]
    )


def parse_signature(wrapped: str) -> Signature:
    lines = _unwrap(wrapped, minimum_lines=4, what="signature")
    raw = _decode(
        lines[1],
        size=2 + _KEY_ID_SIZE + _SIGNATURE_SIZE,
        what="signature data",
    )
    if raw[:2] not in {_ALGORITHM_PLAIN, _ALGORITHM_PREHASHED}:
        msg = f"Unsupported signature algorithm: {raw[:2]!r}"
        raise SignatureError(msg)
    if not lines[2].startswith(_TRUSTED_COMMENT_PREFIX):
        msg = "The signature has no trusted comment."
        raise SignatureError(msg)
    return Signature(
        algorithm=raw[:2],
        key_id=raw[2 : 2 + _KEY_ID_SIZE],
        signature=raw[2 + _KEY_ID_SIZE :],
        trusted_comment=lines[2][len(_TRUSTED_COMMENT_PREFIX) :],
        global_signature=_decode(
            lines[3], size=_SIGNATURE_SIZE, what="global signature"
        ),
    )


def verify(
    public_key: PublicKey, signature: Signature, content: bytes
) -> None:
    """Raises SignatureError unless `signature` is a valid signature of
    `content` by `public_key`, trusted comment included. Fails closed."""
    if signature.key_id != public_key.key_id:
        msg = (
            f"Signed with key {signature.key_id.hex()}, but expected key"
            f" {public_key.key_id.hex()}."
        )
        raise SignatureError(msg)
    message = (
        hashlib.blake2b(content, digest_size=64).digest()
        if signature.algorithm == _ALGORITHM_PREHASHED
        else content
    )
    key = Ed25519PublicKey.from_public_bytes(public_key.key)
    try:
        key.verify(signature.signature, message)
        key.verify(
            signature.global_signature,
            signature.signature + signature.trusted_comment.encode("utf-8"),
        )
    except InvalidSignature as error:
        msg = "The signature does not match the content."
        raise SignatureError(msg) from error
