"""Кодировки текстов Logo: ANSI-страница набора и OEM-страница (часть текстов старых книг записана в OEM), построчный выбор, признак OEM для книги.
Кодеки регистрируются по имени: «<ansi>tolerant» (недекодируемые байты -> «�») и «<ansi>auto_<oem>» (построчный выбор ANSI/OEM)."""
from __future__ import annotations

import codecs
import re
from typing import Dict, Iterable, List, Optional, Tuple

# ANSI -> OEM (Windows): 1250->852, 1251->866, 1252->850, 1253->737, 1254->857, 1255->862, 1256->720, 1257->775; 1258 - OEM-страницы нет
ANSI_TO_OEM = {"cp1250": "cp852", "cp1251": "cp866", "cp1252": "cp850", "cp1253": "cp737", "cp1254": "cp857", "cp1255": "cp862",
               "cp1256": "cp720", "cp1257": "cp775", "cp1258": "cp1258"}
# Признаки для пары cp1254/cp857: подобраны по данным и проверены (общий критерий «буква/не буква» даёт лишнее и пропускает 0x9A/0x9F)
_M_OEM = {("cp1254", "cp857"): frozenset((0x8D, 0x98, 0x9E, 0x9F, 0xA7, 0xA6, 0x80, 0x81, 0x87, 0x94, 0x99, 0x9A))}
_M_ANSI = {("cp1254", "cp857"): frozenset((0xFD, 0xDD, 0xFE, 0xDE, 0xF0, 0xD0, 0xFC, 0xDC, 0xF6, 0xD6, 0xE7, 0xC7))}


def ansi_from_codepage(cp: int) -> str:
    return "cp%d" % cp if cp else ""


def default_oem(ansi: str) -> str:
    return ANSI_TO_OEM.get(ansi, "")


def valid_codec(name: str) -> bool:
    try:
        codecs.lookup(name)
        return True
    except LookupError:
        return False


def tolerant_name(ansi: str) -> str:
    return "%stolerant" % ansi


def auto_name(ansi: str, oem: str) -> str:
    return "%sauto_%s" % (ansi, oem)


def _penalty(text: str) -> float:
    """Доля «подозрительных» символов среди ≥0x80: не буквы и заглавные буквы сразу после строчной (признак неверной кодировки)."""
    hi = [c for c in text if ord(c) >= 0x80]
    if not hi:
        return 0.0
    bad = sum(1 for c in hi if not c.isalpha())
    bad += sum(1 for k in range(1, len(text)) if text[k].isupper() and text[k - 1].islower())
    return bad / len(hi)


def decode_auto(data, ansi: str = "cp1254", oem: str = "cp857") -> str:
    """Строка целиком: OEM, если в ней есть признаки OEM и нет признаков ANSI; иначе ANSI (с заменой недекодируемых байтов)."""
    b = bytes(data)
    if not any(c >= 0x80 for c in b):
        return b.decode("ascii")
    if not oem:
        return b.decode(ansi, "replace")
    key = (ansi, oem)
    if key in _M_OEM:
        s = set(b)
        if (s & _M_OEM[key]) and not (s & _M_ANSI[key]):
            return b.decode(oem, "replace")
        return b.decode(ansi, "replace")
    a, o = b.decode(ansi, "replace"), b.decode(oem, "replace")
    nhi = sum(1 for c in b if c >= 0x80)
    if nhi >= 2 and _penalty(a) - _penalty(o) >= 0.3:
        return o
    return a


def is_oem_bytes(data, ansi: str, oem: str) -> bool:
    """True, если строка по признакам написана в OEM-кодировке (см. decode_auto)."""
    b = bytes(data)
    if not oem or not any(c >= 0x80 for c in b):
        return False
    return decode_auto(b, ansi, oem) == b.decode(oem, "replace") and b.decode(oem, "replace") != b.decode(ansi, "replace")


def has_oem_rows(samples: Iterable[bytes], ansi: str, oem: str) -> Tuple[int, int]:
    """(строк с признаками OEM, строк с не-ASCII) по выборке байтовых строк."""
    n_oem = n_hi = 0
    for s in samples:
        if s is None:
            continue
        b = bytes(s)
        if not any(c >= 0x80 for c in b):
            continue
        n_hi += 1
        if is_oem_bytes(b, ansi, oem):
            n_oem += 1
    return n_oem, n_hi


_RX_TOL = re.compile(r"^(cp\d+)tolerant$")
_RX_AUTO = re.compile(r"^(cp\d+)auto(?:_(cp\d+))?$")


def _search(name: str):
    m = _RX_TOL.match(name)
    if m:
        ansi = m.group(1)
        try:
            base = codecs.lookup(ansi)
        except LookupError:
            return None
        return codecs.CodecInfo(name=name, encode=base.encode, decode=lambda b, errors="strict": base.decode(b, "replace"))
    m = _RX_AUTO.match(name)
    if m:
        ansi = m.group(1)
        oem = m.group(2) or ANSI_TO_OEM.get(ansi, "")
        try:
            base = codecs.lookup(ansi)
        except LookupError:
            return None
        return codecs.CodecInfo(name=name, encode=base.encode, decode=lambda b, errors="strict": (decode_auto(b, ansi, oem), len(b)))
    return None


codecs.register(_search)
