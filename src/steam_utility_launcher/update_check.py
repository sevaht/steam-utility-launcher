from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Apps that update themselves (ROTK Launcher, ZEmu Launcher) are only checked
# for a newer release this often, so launching doesn't hit the network every
# time. The tool then just warns; the app's own updater does the update.
CHECK_INTERVAL = timedelta(days=1)
MARKER_FILE_NAME = ".last_update_check"


def checked_recently(marker: Path) -> bool:
    if not marker.exists():
        return False
    try:
        last_checked = datetime.fromisoformat(marker.read_text().strip())
    except ValueError:
        return False
    return datetime.now(UTC) - last_checked < CHECK_INTERVAL


def record_checked_now(marker: Path) -> None:
    marker.write_text(datetime.now(UTC).isoformat())
