"""Privileged half of `steam-utility-launcher enable-hwid-access`.

Run by the unprivileged tool as `sudo python3 -I -B - <enable|disable>` with
this text on stdin, so that nothing of the tool is ever imported by, or
written (.pyc files) as, root. It touches nothing but the fixed list of files
below, one systemd-tmpfiles drop-in and one udev rule, and accepts no paths
from anyone.

  enable   make the DMI serial/UUID files readable by every local user, now
           and on every boot (via a tmpfiles.d rule), and let the logged-in
           desktop user use the TPM's kernel resource manager (/dev/tpmrm*)
           via a udev "uaccess" rule, as the ROTK launcher's TPM attestation
           does on Windows
  disable  remove both rules and make everything root-only again

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
# The TPM resource manager devices; keep in sync with hwid_access.py.
_TPM_DEVICES = "/dev/tpmrm[0-9]*"
# Before 73-seat-late.rules, which is what turns the tag into an ACL.
_TPM_RULE = Path("/etc/udev/rules.d/70-steam-utility-launcher-tpm.rules")
_TPM_RULE_LINES = (
    "# Managed by steam-utility-launcher (enable-hwid-access).",
    "# Lets the logged-in desktop user use the TPM through the kernel resource",
    "# manager, as the ROTK launcher does on Windows. Remove with",
    "# `steam-utility-launcher enable-hwid-access --disable`.",
    'KERNEL=="tpmrm[0-9]*", SUBSYSTEM=="tpmrm", TAG+="uaccess"',
    "",
)
_ROOT_ONLY = 0o400
_WORLD_READABLE = 0o444
_ARGC = 2  # program name and the action


def _existing() -> list[Path]:
    return [_DMI_DIR / name for name in _FILES if (_DMI_DIR / name).exists()]


def _tpm_present() -> bool:
    return any(Path("/dev").glob(_TPM_DEVICES.removeprefix("/dev/")))


def _write_atomically(
    target: Path, lines: list[str] | tuple[str, ...]
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace, so a crash never leaves a half-written rule.
    with tempfile.NamedTemporaryFile(
        "w", dir=target.parent, delete=False, encoding="utf-8"
    ) as staged:
        staged.write("\n".join(lines))
    Path(staged.name).chmod(0o644)
    Path(staged.name).replace(target)


def _reload_udev() -> None:
    udevadm = shutil.which("udevadm")
    if not udevadm:
        print("udevadm not found; the TPM rule applies at the next boot.")
        return
    for args in (
        ["control", "--reload"],
        ["trigger", "--action=change", "--subsystem-match=tpmrm"],
        ["settle"],
    ):
        subprocess.run([udevadm, *args], check=False)  # noqa: S603


def _write_conf(paths: list[Path]) -> None:
    _write_atomically(
        _CONF,
        [
            "# Managed by steam-utility-launcher (enable-hwid-access).",
            "# Makes firmware serial numbers readable to local users, as WMI"
            " does",
            "# on Windows. Remove with `steam-utility-launcher"
            " enable-hwid-access --disable`.",
            *(f"z {path} {_WORLD_READABLE:04o} - - -" for path in paths),
            "",
        ],
    )


def _enable_dmi() -> None:
    paths = _existing()
    if not paths:
        print("No DMI serial files exist on this machine; nothing to do.")
        return
    _write_conf(paths)
    print(f"Wrote {_CONF}")
    tmpfiles = shutil.which("systemd-tmpfiles")
    if tmpfiles:
        result = subprocess.run(  # noqa: S603
            [tmpfiles, "--create", str(_CONF)], check=False
        )
        if result.returncode == 0:
            print("Applied now; it will also be re-applied at every boot.")
            return
        print("systemd-tmpfiles failed; changing permissions directly.")
    else:
        print(
            "systemd-tmpfiles not found; changing permissions directly."
            " This will NOT survive a reboot."
        )
    for path in paths:
        path.chmod(_WORLD_READABLE)


def _enable_tpm() -> None:
    if not _tpm_present():
        print("No TPM resource manager device on this machine; skipping it.")
        return
    _write_atomically(_TPM_RULE, _TPM_RULE_LINES)
    print(f"Wrote {_TPM_RULE}")
    _reload_udev()
    print("The TPM is usable by the logged-in desktop user.")


def _enable() -> int:
    _enable_dmi()
    _enable_tpm()
    return 0


def _disable() -> int:
    _CONF.unlink(missing_ok=True)
    print(f"Removed {_CONF}")
    for path in _existing():
        path.chmod(_ROOT_ONLY)
    print("DMI serial files are root-only again.")
    if _TPM_RULE.exists():
        _TPM_RULE.unlink()
        print(f"Removed {_TPM_RULE}")
        _reload_udev()
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
