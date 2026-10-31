from __future__ import annotations

import json
import struct
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def make_asar(directory: Path, files: dict[str, bytes]) -> Path:
    """An archive like Electron's, with files at the given paths."""
    root: dict[str, Any] = {"files": {}}
    body = b""
    for name, content in files.items():
        node = root
        *folders, leaf = name.split("/")
        for folder in folders:
            node = node["files"].setdefault(folder, {"files": {}})
        node["files"][leaf] = {"size": len(content), "offset": str(len(body))}
        body += content
    text = json.dumps(root).encode()
    padded = text + b"\0" * (-len(text) % 4)
    header = struct.pack(
        "<IIII", 4, len(padded) + 8, len(padded) + 4, len(text)
    )
    path = directory / "app.asar"
    path.write_bytes(header + padded + body)
    return path
