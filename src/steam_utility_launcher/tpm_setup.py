from __future__ import annotations

import hashlib
import logging
import re
from http.client import HTTPSConnection
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

# The stand-in (resources/tpm2.c) sends whatever ek-cert-1..4.der files are in
# the TPM directory as the EK certificate chain: the EK certificate first,
# then its issuers. AMD's firmware TPMs keep no certificate in the TPM; AMD
# publishes it, and this fetches it once.
EK_PUBLIC_FILE = "ek.pub"
MANUFACTURER_FILE = "manufacturer.txt"
MAX_CERTIFICATES = 4
_MAX_CERTIFICATE_SIZE = 3072  # the stand-in's per-file limit
_AMD_MANUFACTURER = "AMD"
_AMD_HOST = "ftpm.amd.com"
_AMD_URL = f"https://{_AMD_HOST}/pki/aia/"
_FETCH_TIMEOUT_SECONDS = 15
_HTTP_OK = 200
_DEFAULT_EXPONENT = 65537
_RSA_ALGORITHM = 0x0001
_EK_PREFIX = bytes.fromhex("00002222")
_URL_HASH_BYTES = 16
# DER: OID 1.3.6.1.5.5.7.48.2 (caIssuers) then a uniformResourceIdentifier.
_CA_ISSUERS = re.compile(
    rb"\x06\x08\x2b\x06\x01\x05\x05\x07\x30\x02\x86([\x01-\x7f])", re.DOTALL
)


def cert_file_name(index: int) -> str:
    return f"ek-cert-{index}.der"


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def rsa_exponent_and_modulus(tpm_public: bytes) -> tuple[int, bytes] | None:
    """Reads a size-prefixed TPM2B_PUBLIC holding an RSA key."""
    try:
        if _u16(tpm_public, 0) + 2 != len(tpm_public):
            return None
        if _u16(tpm_public, 2) != _RSA_ALGORITHM:
            return None
        offset = 2 + 2 + 2 + 4  # size, type, nameAlg, attributes
        offset += 2 + _u16(tpm_public, offset)  # authPolicy
        offset += 6 + 2 + 2  # symmetric, scheme, key bits
        exponent = int.from_bytes(tpm_public[offset : offset + 4], "big")
        offset += 4
        size = _u16(tpm_public, offset)
        modulus = tpm_public[offset + 2 : offset + 2 + size]
    except IndexError:
        return None
    if len(modulus) != size or not size:
        return None
    return exponent or _DEFAULT_EXPONENT, modulus


def amd_certificate_url(exponent: int, modulus: bytes) -> str:
    digest = hashlib.sha256(
        _EK_PREFIX + exponent.to_bytes(4, "big") + modulus
    ).digest()
    return _AMD_URL + digest[:_URL_HASH_BYTES].hex()


def issuer_urls(certificate: bytes) -> list[str]:
    """The CA Issuers locations from the certificate's AIA extension."""
    urls = []
    for match in _CA_ISSUERS.finditer(certificate):
        end = match.end() + match.group(1)[0]
        text = certificate[match.end() : end]
        if len(text) == match.group(1)[0]:
            urls.append(text.decode("ascii", errors="replace"))
    return urls


def _is_allowed(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname == _AMD_HOST
        and parts.port is None
    )


def fetch(url: str) -> bytes | None:
    """GETs a certificate from AMD's server; nothing else is ever requested,
    and no identifying data is sent beyond the hash in the URL."""
    if not _is_allowed(url):
        logger.warning("Not fetching a certificate from: %s", url)
        return None
    parts = urlsplit(url)
    connection = HTTPSConnection(_AMD_HOST, timeout=_FETCH_TIMEOUT_SECONDS)
    try:
        connection.request(
            "GET", parts.path, headers={"User-Agent": "steam-utility-launcher"}
        )
        response = connection.getresponse()
        content = response.read(_MAX_CERTIFICATE_SIZE + 1)
    except OSError as error:
        logger.warning("Could not fetch %s: %s", url, error)
        return None
    finally:
        connection.close()
    if response.status != _HTTP_OK or len(content) > _MAX_CERTIFICATE_SIZE:
        logger.warning(
            "Unusable answer from %s (status %d)", url, response.status
        )
        return None
    return content


def _write_atomically(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def needs_preparing(directory: Path) -> bool:
    return not (
        (directory / EK_PUBLIC_FILE).is_file()
        and (directory / MANUFACTURER_FILE).is_file()
    )


def is_amd(directory: Path) -> bool:
    try:
        text = (directory / MANUFACTURER_FILE).read_text()
    except OSError:
        return False
    return text.strip() == _AMD_MANUFACTURER


def ensure_certificates(
    directory: Path, getter: Callable[[str], bytes | None] = fetch
) -> int:
    """Saves the EK certificate chain (if this TPM's maker publishes it and it
    isn't saved yet). Returns how many certificates are in place. A failure
    only means the launcher sends no certificates, as it did before."""
    first = directory / cert_file_name(1)
    if first.is_file():
        return sum(
            (directory / cert_file_name(i)).is_file()
            for i in range(1, MAX_CERTIFICATES + 1)
        )
    if not is_amd(directory):
        return 0
    try:
        parsed = rsa_exponent_and_modulus(
            (directory / EK_PUBLIC_FILE).read_bytes()
        )
    except OSError:
        return 0
    if parsed is None:
        logger.warning("Unrecognized endorsement key; skipping certificates.")
        return 0
    exponent, modulus = parsed
    chain: list[bytes] = []
    url: str | None = amd_certificate_url(exponent, modulus)
    while url and len(chain) < MAX_CERTIFICATES:
        content = getter(url)
        if content is None:
            break
        if not chain and modulus not in content:
            logger.warning("The fetched certificate is not for this TPM.")
            return 0
        chain.append(content)
        found = issuer_urls(content)
        url = found[0] if found else None
    # The EK certificate itself goes last, so an interrupted run is retried.
    for index in range(len(chain), 0, -1):
        _write_atomically(directory / cert_file_name(index), chain[index - 1])
    if chain:
        logger.info("Saved %d EK certificate(s) in: %s", len(chain), directory)
    return len(chain)
