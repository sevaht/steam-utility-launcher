"""Privileged half of `steam-utility-launcher enable-hwid-access`.

Run by the unprivileged tool as `sudo python3 -I -B - <enable|disable>` with
this text on stdin, so that nothing of the tool is ever imported by, or
written (.pyc files) as, root. It touches nothing but the fixed list of files
below and one systemd-tmpfiles drop-in, and accepts no paths from anyone.

  enable   make the DMI serial/UUID files readable by every local user, now
           and on every boot (via a tmpfiles.d rule)
  disable  remove that rule and make the files root-only again

Standard library only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The DMI files firmware exposes root-only. Fixed on purpose: keep in sync
# with hwid_access.py (a test compares them).
_DMI_DIR = Path("/sys/class/dmi/id")
_FILES = ("product_uuid", "product_serial", "board_serial", "chassis_serial")
_CONF = Path("/etc/tmpfiles.d/steam-utility-launcher-hwid.conf")
_ROOT_ONLY = 0o400
_WORLD_READABLE = 0o444
_ARGC = 2  # program name and the action


def _existing() -> list[Path]:
    return [_DMI_DIR / name for name in _FILES if (_DMI_DIR / name).exists()]


def _write_conf(paths: list[Path]) -> None:
    lines = [
        "# Managed by steam-utility-launcher (enable-hwid-access).",
        "# Makes firmware serial numbers readable to local users, as WMI does",
        "# on Windows. Remove with `steam-utility-launcher enable-hwid-access"
        " --disable`.",
        *(f"z {path} {_WORLD_READABLE:04o} - - -" for path in paths),
        "",
    ]
    _CONF.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace, so a crash never leaves a half-written rule.
    with tempfile.NamedTemporaryFile(
        "w", dir=_CONF.parent, delete=False, encoding="utf-8"
    ) as staged:
        staged.write("\n".join(lines))
    Path(staged.name).chmod(0o644)
    Path(staged.name).replace(_CONF)


def _enable() -> int:
    paths = _existing()
    if not paths:
        print("No DMI serial files exist on this machine; nothing to do.")
        return 0
    _write_conf(paths)
    print(f"Wrote {_CONF}")
    tmpfiles = shutil.which("systemd-tmpfiles")
    if tmpfiles:
        result = subprocess.run(  # noqa: S603
            [tmpfiles, "--create", str(_CONF)], check=False
        )
        if result.returncode == 0:
            print("Applied now; it will also be re-applied at every boot.")
            return 0
        print("systemd-tmpfiles failed; changing permissions directly.")
    else:
        print(
            "systemd-tmpfiles not found; changing permissions directly."
            " This will NOT survive a reboot."
        )
    for path in paths:
        path.chmod(_WORLD_READABLE)
    return 0


def _disable() -> int:
    _CONF.unlink(missing_ok=True)
    print(f"Removed {_CONF}")
    for path in _existing():
        path.chmod(_ROOT_ONLY)
    print("DMI serial files are root-only again.")
    return 0


def main() -> int:
    if os.geteuid() != 0:
        print("This must run as root (through sudo).", file=sys.stderr)
        return 1
    actions = {"enable": _enable, "disable": _disable}
    action = actions.get(sys.argv[1]) if len(sys.argv) == _ARGC else None
    if action is None:
        print("usage: - <enable|disable>", file=sys.stderr)
        return 2
    return action()


if __name__ == "__main__":
    raise SystemExit(main())
