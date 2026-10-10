from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

# Kept in sync with resources/hwid_access_root.py (a test compares them).
DMI_DIR = Path("/sys/class/dmi/id")
DMI_FILES = (
    "product_uuid",
    "product_serial",
    "board_serial",
    "chassis_serial",
)

# The TPM resource manager devices (kept in sync with the root script).
DEV_DIR = Path("/dev")
TPM_DEVICE_PATTERN = "tpmrm[0-9]*"

_ROOT_SCRIPT = "hwid_access_root.py"
_SYSTEM_PYTHONS = (
    "/usr/bin/python3",
    "/bin/python3",
    "/usr/local/bin/python3",
)


def _readable(path: Path) -> bool:
    """Whether this user can really read the file (not just what its
    permission bits say)."""
    try:
        with path.open("rb") as file:
            file.read(1)
    except OSError:
        return False
    return True


def restricted_files(directory: Path = DMI_DIR) -> list[Path]:
    """The DMI serial files that exist but this user can't read."""
    return [
        path
        for path in (directory / name for name in DMI_FILES)
        if path.exists() and not _readable(path)
    ]


def inaccessible_tpm_devices(directory: Path = DEV_DIR) -> list[Path]:
    """The TPM resource manager devices that exist but this user can't use."""
    return [
        path
        for path in sorted(directory.glob(TPM_DEVICE_PATTERN))
        if not os.access(path, os.R_OK | os.W_OK)
    ]


def has_usable_tpm(directory: Path = DEV_DIR) -> bool:
    return any(
        os.access(path, os.R_OK | os.W_OK)
        for path in directory.glob(TPM_DEVICE_PATTERN)
    )


def tpm_problem() -> str | None:
    """An error message if `--tpm` was asked for but this account can't use a
    TPM, or None if it can.

    TPM attestation is opt-in; once asked for it must work, so (unlike a
    missing TPM on a PC that never asked) this is a reason not to start.
    """
    if has_usable_tpm():
        return None
    devices = inaccessible_tpm_devices()
    if devices:
        return (
            f"Refusing to start with --tpm: the TPM ({', '.join(path.name for path in devices)})"
            " can't be used by your user. Run `steam-utility-launcher"
            " enable-hwid-access` once to allow it, then try again."
        )
    return (
        "Refusing to start with --tpm: no TPM was found"
        f" ({DEV_DIR / TPM_DEVICE_PATTERN}). Run without --tpm to behave"
        " like a PC without a TPM."
    )


def access_problem() -> str | None:
    """An error message if ROTK's hardware check can't be answered completely
    from this account, or None if it can.

    Sending an incomplete fingerprint would be sending a wrong one, so this is
    a reason not to start at all, never something to warn about and carry on.
    """
    restricted = restricted_files()
    if not restricted:
        return None
    return (
        "Refusing to start: the firmware serial numbers ROTK's hardware check"
        " reads can't be read by your user"
        f" ({', '.join(path.name for path in restricted)}), so the check could"
        " not be answered completely. Run `steam-utility-launcher"
        " enable-hwid-access` once, then try again."
    )


def root_script_text() -> str:
    source = resources.files(__package__) / "resources" / _ROOT_SCRIPT
    return source.read_text(encoding="utf-8")


def _system_python() -> str:
    """A python outside any virtual environment: a venv's interpreter and
    imports belong to the user, which root must not trust."""
    venv_prefix = Path(sys.prefix).resolve()
    in_venv = sys.prefix != sys.base_prefix
    for candidate in _SYSTEM_PYTHONS:
        path = Path(candidate)
        if not (path.exists() and os.access(path, os.X_OK)):
            continue
        if not in_venv or venv_prefix not in path.resolve().parents:
            return candidate
    msg = f"No system python3 found in: {', '.join(_SYSTEM_PYTHONS)}"
    raise RuntimeError(msg)


def _confirm(script: str, action: str) -> bool:
    print(
        "About to run the script below as root, via sudo, as"
        f" `python3 -I -B - {action}`. Nothing of this tool runs as root and"
        " no files of it are written as root.\n"
    )
    print(script)
    answer = input(f"Run it with '{action}'? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def run(*, disable: bool = False, assume_yes: bool = False) -> int:
    if os.geteuid() == 0:
        logger.error(
            "Run this as your normal user, not root; it uses sudo itself."
        )
        return 1
    action = "disable" if disable else "enable"
    if (
        not disable
        and not restricted_files()
        and not inaccessible_tpm_devices()
    ):
        print(
            "Hardware identifiers and the TPM are already usable; nothing to do."
        )
        return 0
    script = root_script_text()
    if not assume_yes and not _confirm(script, action):
        print("Cancelled.")
        return 1
    # The script goes over stdin; sudo prompts for a password on the
    # terminal, not stdin, so this works interactively. -I ignores the
    # environment and user site, -B stops any .pyc being written as root.
    sudo = shutil.which("sudo") or "sudo"
    result = subprocess.run(  # noqa: S603
        [sudo, "--", _system_python(), "-I", "-B", "-", action],
        input=script,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.error(
            "The privileged step failed (status %s).", result.returncode
        )
        return result.returncode
    remaining = [*restricted_files(), *inaccessible_tpm_devices()]
    if disable or not remaining:
        print("Done.")
        return 0
    logger.error(
        "Still unusable: %s", ", ".join(path.name for path in remaining)
    )
    return 1
