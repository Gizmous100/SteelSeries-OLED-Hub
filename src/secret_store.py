"""Windows DPAPI helpers for storing secrets at rest.

Sensitive preference values are written as ``dpapi:<base64>`` inside
``config.json`` / ``credentials.json``. The value is transparently decrypted
again when the configuration is loaded, so the rest of the application keeps
working with plain strings.

If DPAPI is unavailable (or a value was produced on a different Windows
account), the helper returns the original value unchanged. Losing access to a
token is always preferable to blocking the application from starting.
"""

import base64
import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger("OLED Customizer.SecretStore")

_PREFIX = "dpapi:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
# Additional entropy binds the ciphertext to this application.
_ENTROPY = b"SteelSeries-OLED-Hub"

# Keys containing any of these fragments are encrypted before hitting disk.
_SENSITIVE_FRAGMENTS = ("secret", "token", "password", "api_key")


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def is_sensitive(key) -> bool:
    """Return True when a preference key is expected to hold a secret."""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS)


def _make_blob(data: bytes):
    """Build a DATA_BLOB plus the backing buffer that must stay alive."""
    # create_string_buffer rejects a zero length; DPAPI accepts a 1-byte blob
    # only when the declared length is 0, so keep an extra byte around.
    buffer = ctypes.create_string_buffer(data, max(1, len(data)))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _configure_dpapi():
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]

    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    return crypt32, kernel32


def _run_dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = _configure_dpapi()
    blob_in, _buf_in = _make_blob(data)
    blob_entropy, _buf_entropy = _make_blob(_ENTROPY)
    blob_out = _DataBlob()

    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            "SteelSeries OLED Hub",
            ctypes.byref(blob_entropy),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            ctypes.byref(blob_entropy),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )

    if not ok:
        raise OSError("DPAPI call failed")

    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(blob_out.pbData)


def protect(value):
    """Encrypt a string for disk storage. Non-strings and empty values pass through."""
    if not isinstance(value, str) or not value or value.startswith(_PREFIX):
        return value
    try:
        encoded = base64.b64encode(_run_dpapi(value.encode("utf-8"), protect=True))
        return _PREFIX + encoded.decode("ascii")
    except Exception as exc:
        logger.warning("Could not encrypt a secret with DPAPI (storing plaintext): %s", exc)
        return value


def unprotect(value):
    """Decrypt a value previously produced by :func:`protect`."""
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        return value
    try:
        encrypted = base64.b64decode(value[len(_PREFIX):].encode("ascii"))
        return _run_dpapi(encrypted, protect=False).decode("utf-8")
    except Exception as exc:
        logger.warning("Could not decrypt a stored secret with DPAPI: %s", exc)
        return ""


def protect_mapping(mapping: dict) -> dict:
    """Return a shallow copy with every sensitive value encrypted."""
    result = dict(mapping)
    for key, value in result.items():
        if is_sensitive(key):
            result[key] = protect(value)
    return result


def unprotect_mapping(mapping: dict) -> dict:
    """Decrypt every sensitive value in place and return the mapping."""
    for key, value in list(mapping.items()):
        if is_sensitive(key):
            mapping[key] = unprotect(value)
    return mapping
