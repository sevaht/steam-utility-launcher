"""Builds minisign-format keys and signatures for tests, the way Tauri wraps
them (base64 of the minisign text files)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

KEY_ID = bytes(range(1, 9))
COMMENT = "timestamp:1790170335\tfile:ZEmu Launcher_9.9.9_amd64.AppImage"


def wrap(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def make_keys(key_id: bytes = KEY_ID) -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    body = base64.b64encode(b"Ed" + key_id + raw).decode()
    return private, wrap(f"untrusted comment: minisign public key\n{body}\n")


def sign(
    private: Ed25519PrivateKey,
    content: bytes,
    *,
    prehashed: bool = True,
    key_id: bytes = KEY_ID,
    comment: str = COMMENT,
) -> str:
    message = (
        hashlib.blake2b(content, digest_size=64).digest()
        if prehashed
        else content
    )
    signature = private.sign(message)
    global_signature = private.sign(signature + comment.encode())
    algorithm = b"ED" if prehashed else b"Ed"
    sig_line = base64.b64encode(algorithm + key_id + signature).decode()
    global_line = base64.b64encode(global_signature).decode()
    return wrap(
        "untrusted comment: signature\n"
        f"{sig_line}\ntrusted comment: {comment}\n{global_line}\n"
    )
