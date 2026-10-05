"""API keys for AI provider profiles, encrypted with Windows DPAPI (CurrentUser scope).

    from ac.ai import keystore as KS
    KS.set_key("gemini", "AIza...")      # encrypt + store
    KS.has_key("gemini")                 # True (no decryption)
    KS.get_key("gemini")                 # plaintext, engine-internal only (registered for log redaction)
    KS.delete_key("gemini")

File: %APPDATA%\\Klipora\\ai_keys.bin = {"v": 1, "keys": {profile_id: base64(DPAPI ciphertext)}}.
Only the same Windows user on the same machine can decrypt (CryptProtectData without LOCAL_MACHINE, plus an
app-specific entropy). Keys are never returned to the panel, printed, logged or written anywhere else; every
key decrypted in this process is added to `revealed()` so `ac.util.redact()` scrubs it from logs and errors.
Non-Windows: KeystoreError (no plaintext fallback).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading

from ..i18n import tr

__all__ = ["KeystoreError", "set_key", "get_key", "has_key", "key_error", "delete_key", "ids", "revealed", "path",
           "blob_tag", "KEY_ERROR"]

_ENTROPY = b"AutoCutBOT.ai.v1"
_LOCK = threading.Lock()
_CACHE = {"mtime": None, "keys": None, "path": None}
_REVEALED: set[str] = set()
_BLOB_STATE: dict[str, str] = {}    # stored ciphertext -> "" (decrypts) or an error message; never plaintext
KEY_ERROR = "undecryptable"       # _BLOB_STATE marker; key_error() returns the translated message


class KeystoreError(Exception):
    """Encryption/decryption or file problem (message is safe to show: never contains a key)."""


def path():
    from ..util import appdata_dir
    return appdata_dir() / "ai_keys.bin"


# ---------------------------------------------------------------- DPAPI (ctypes)

def _dpapi():
    if sys.platform != "win32":
        raise KeystoreError(tr("ai.keystoreWindowsOnly"))
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sig = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
           wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptProtectData.argtypes = sig
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.POINTER(wintypes.LPWSTR)] + sig[2:]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return ctypes, Blob, crypt32, kernel32


_UI_FORBIDDEN = 0x1


def _call(fn_name, data):
    ctypes, Blob, crypt32, kernel32 = _dpapi()
    buf = ctypes.create_string_buffer(data, len(data))
    ent = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    b_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    b_ent = Blob(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    b_out = Blob()
    if fn_name == "protect":
        ok = crypt32.CryptProtectData(ctypes.byref(b_in), "Klipora AI key", ctypes.byref(b_ent), None, None,
                                      _UI_FORBIDDEN, ctypes.byref(b_out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(b_in), None, ctypes.byref(b_ent), None, None,
                                        _UI_FORBIDDEN, ctypes.byref(b_out))
    if not ok:
        err = ctypes.get_last_error()
        raise KeystoreError(tr("ai.dpapiProtectFailed" if fn_name == "protect" else "ai.dpapiUnprotectFailed",
                               code=str(err)))
    try:
        return ctypes.string_at(b_out.pbData, b_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(b_out.pbData, ctypes.c_void_p))
        ctypes.memset(buf, 0, len(data))


def protect(data: bytes) -> bytes:
    return _call("protect", data)


def unprotect(blob: bytes) -> bytes:
    return _call("unprotect", blob)


# ---------------------------------------------------------------- file

def _load():
    p = path()
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        mt = None
    if _CACHE["keys"] is None or mt != _CACHE["mtime"] or _CACHE["path"] != str(p):
        keys = {}
        if mt is not None:
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(doc, dict) and isinstance(doc.get("keys"), dict):
                    keys = {str(k): str(v) for k, v in doc["keys"].items() if isinstance(v, str) and v}
            except (OSError, ValueError):
                keys = {}
        _CACHE.update(mtime=mt, keys=keys, path=str(p))
    return dict(_CACHE["keys"])


def _save(keys):
    p = path()
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"v": 1, "keys": keys}, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    _CACHE.update(mtime=None, keys=None)


def _check_id(pid):
    if not isinstance(pid, str) or not pid or len(pid) > 40:
        raise KeystoreError(tr("ai.badProfileId"))


def set_key(pid, key):
    """Encrypt and store `key` for profile `pid` (replaces an older one)."""
    _check_id(pid)
    key = (key or "").strip()
    if len(key) < 8 or any(c.isspace() for c in key) or len(key) > 4096:
        raise KeystoreError(tr("ai.badKey"))
    blob = base64.b64encode(protect(key.encode("utf-8"))).decode("ascii")
    with _LOCK:
        keys = _load()
        keys[pid] = blob
        _save(keys)
    _REVEALED.add(key)
    return True


def get_key(pid):
    """Plaintext key or "" (missing / cannot decrypt). Engine-internal: never send it anywhere but the provider."""
    blob = _load().get(pid)
    if not blob or _BLOB_STATE.get(blob):
        return ""
    try:
        key = unprotect(base64.b64decode(blob)).decode("utf-8")
    except (KeystoreError, ValueError, UnicodeDecodeError):
        # other Windows user / migrated profile / roaming copy / password reset by an admin
        _BLOB_STATE[blob] = KEY_ERROR
        return ""
    _BLOB_STATE[blob] = "" if key else KEY_ERROR
    if key:
        _REVEALED.add(key)
    return key


def has_key(pid):
    """A key blob is stored (no decryption). Use key_error() to know whether it can be used."""
    return bool(_load().get(pid))


def key_error(pid):
    """"" when there is no stored key or it decrypts for this Windows user, else a message safe to show."""
    blob = _load().get(pid)
    if not blob:
        return ""
    if blob not in _BLOB_STATE:
        get_key(pid)
    return tr("ai.keyUndecryptable") if _BLOB_STATE.get(blob) else ""


def delete_key(pid):
    with _LOCK:
        keys = _load()
        existed = keys.pop(pid, None) is not None
        if existed:
            _save(keys)
    return existed


def blob_tag(pid):
    """Short hash of the stored CIPHERTEXT (not of the key): changes when the key is replaced or deleted."""
    import hashlib
    return hashlib.sha1(_load().get(pid, "").encode("ascii", "replace")).hexdigest()[:10]


def ids():
    return sorted(_load())


def revealed():
    """Keys decrypted (or stored) by this process; ac.util.redact() removes them from any text."""
    return [k for k in _REVEALED if len(k) >= 6]


def _selftest():  # pragma: no cover - manual check: python -m ac.ai.keystore
    blob = protect(b"secret-value")
    assert unprotect(blob) == b"secret-value" and b"secret-value" not in blob
    print("dpapi ok", len(blob))


if __name__ == "__main__":  # pragma: no cover
    _selftest()

