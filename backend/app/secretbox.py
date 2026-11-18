"""Хранение пароля подключения: DPAPI через ctypes (без внешних зависимостей), запасной вариант — открытый текст.

Форматы строки: ``''`` (пароля нет), ``dpapi1:u:<base64>`` (область пользователя), ``dpapi1:m:<base64>`` (область машины),
``plain1:<текст>`` (DPAPI недоступен). Пароль никогда не попадает в журнал и в тексты исключений.
"""
from __future__ import annotations

import base64
import binascii
import logging
from typing import Dict

_log = logging.getLogger("buhlogo")

_ENTROPY = b"buhlogo-conn-v1"
_UI_FORBIDDEN = 0x1
_LOCAL_MACHINE = 0x4

MSG_FOREIGN = ("Не удалось расшифровать пароль подключения: он сохранён под другой учётной записью Windows. "
               "Введите пароль заново")


class SecretError(ValueError):
    pass


def _dpapi_available() -> bool:
    """Есть ли DPAPI (Windows + ctypes.windll). Отдельная функция — чтобы в тестах подменять."""
    try:
        import ctypes
        return hasattr(ctypes, "windll") and hasattr(ctypes.windll, "crypt32")
    except Exception:
        return False


def _dpapi(data: bytes, encrypt: bool, machine: bool = False) -> bytes:
    """Вызов CryptProtectData / CryptUnprotectData. При любой ошибке бросает OSError."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return DATA_BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    fn.restype = wintypes.BOOL
    fn.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    din, keep1 = blob(data)
    dent, keep2 = blob(_ENTROPY)
    dout = DATA_BLOB()
    flags = _UI_FORBIDDEN | (_LOCAL_MACHINE if (encrypt and machine) else 0)
    ok = fn(ctypes.byref(din), None, ctypes.byref(dent), None, None, flags, ctypes.byref(dout))
    del keep1, keep2
    if not ok:
        raise OSError("DPAPI: вызов не удался (код %s)" % ctypes.GetLastError())
    try:
        return ctypes.string_at(dout.pbData, dout.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(dout.pbData, ctypes.c_void_p))


def protect(plain: str, scope: str = "user") -> str:
    if not plain:
        return ""
    machine = scope == "machine"
    if _dpapi_available():
        try:
            enc = _dpapi(plain.encode("utf-8"), True, machine)
            return "dpapi1:%s:%s" % ("m" if machine else "u", base64.b64encode(enc).decode("ascii"))
        except Exception:
            _log.warning("Не удалось защитить пароль подключения средствами Windows (DPAPI): пароль сохранён открытым текстом")
            return "plain1:" + plain
    _log.warning("Защита пароля средствами Windows (DPAPI) недоступна: пароль сохранён открытым текстом")
    return "plain1:" + plain


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith("plain1:"):
        return stored[len("plain1:"):]
    if stored.startswith("dpapi1:u:") or stored.startswith("dpapi1:m:"):
        if not _dpapi_available():
            raise SecretError(MSG_FOREIGN)
        try:
            raw = base64.b64decode(stored[len("dpapi1:u:"):].encode("ascii"), validate=True)
            return _dpapi(raw, False).decode("utf-8")
        except (binascii.Error, ValueError, OSError, UnicodeError):
            raise SecretError(MSG_FOREIGN) from None
    raise SecretError("Неизвестный формат сохранённого пароля подключения. Введите пароль заново")


def is_plain(stored: str) -> bool:
    return bool(stored) and stored.startswith("plain1:")


def describe(stored: str) -> Dict[str, str]:
    if not stored:
        return {"kind": "none"}
    if stored.startswith("plain1:"):
        return {"kind": "plain"}
    if stored.startswith("dpapi1:m:"):
        return {"kind": "dpapi_machine"}
    if stored.startswith("dpapi1:u:"):
        return {"kind": "dpapi_user"}
    return {"kind": "plain"}
