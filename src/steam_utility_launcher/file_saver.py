"""Writing files whole or not at all, after backing up what they replace."""

from __future__ import annotations

import shutil
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class FileSaver:
    """Saves files; the backups made by one saver are named alike."""

    def __init__(self) -> None:
        self.stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")

    def back_up(self, path: Path) -> Path | None:
        """Copies the file beside itself, if there is one; the copy's path."""
        if not path.exists():
            return None
        backup = path.with_name(f"{path.name}.sul-backup-{self.stamp}")
        shutil.copy2(path, backup)
        return backup

    def write(self, path: Path, content: bytes) -> None:
        """Writes the file whole or not at all, with the mode it had if it
        existed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(path.name + ".sul-new")
        staging.write_bytes(content)
        if path.exists():
            shutil.copymode(path, staging)
        staging.replace(path)
