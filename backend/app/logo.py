"""Подключение к LOGO_DB. ТОЛЬКО ЧТЕНИЕ: допускаются лишь SELECT / WITH."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import pyodbc

from . import charset, config
from .charset import decode_auto  # noqa: F401  (совместимость: logo.decode_auto)


@dataclass
class LogoConn:
    """Параметры подключения к SQL Server Logo (пароль уже расшифрован; в журнал и API не попадает)."""
    server: str
    database: str
    login: str
    password: str = ""
    driver: str = ""
    trust_cert: bool = True
    timeout: int = 10
    name: str = ""
    id: Optional[int] = None


def current_conn() -> LogoConn:
    from . import dsctx, registry
    return registry.logo_conn(dsctx.current().conn_id)


def current_charset() -> tuple:
    """(ansi, oem) набора; без настроек - значения по умолчанию."""
    try:
        from . import dsparams
        c = dsparams.get("charset")
        return c["ansi"], c.get("oem") or ""
    except Exception:
        from . import dsparams
        c = dsparams.DEFAULTS["charset"]      # значения по умолчанию шаблона «турецкий план»
        return c["ansi"], c["oem"]


_FORBID = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|INTO|GRANT|REVOKE|BACKUP|RESTORE|SHUTDOWN|OPENROWSET|OPENQUERY|XP_CMDSHELL)\b",
    re.I)


def _strip(sql: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    s = re.sub(r"--[^\r\n]*", " ", s)
    return re.sub(r"N?'(?:[^']|'')*'", "''", s)


def check_readonly(sql: str) -> None:
    probe = _strip(sql).strip()
    if not re.match(r"^(SELECT|WITH|SET\s+NOCOUNT)\b", probe, re.I):
        raise ValueError("Разрешены только SELECT-запросы к Logo")
    m = _FORBID.search(probe)
    if m:
        raise ValueError(f"Запрещённое слово в запросе к Logo: {m.group(0)}")


# Порядок предпочтения драйверов ODBC (если LOGO_DRIVER / [logo] driver не заданы). Драйвер 18 по умолчанию шифрует соединение (TLS 1.2),
# старые SQL Server (2008 R2) его не принимают - он берётся последним и с Encrypt=no.
DRIVER_PREFERENCE = ("ODBC Driver 17 for SQL Server", "ODBC Driver 13 for SQL Server", "SQL Server Native Client 11.0",
                     "SQL Server Native Client 10.0", "SQL Server")
_driver_cache: Optional[str] = None


def pick_driver(explicit: str = "") -> str:
    """Явно заданный драйвер, иначе первый найденный из DRIVER_PREFERENCE, иначе любой «ODBC Driver N for SQL Server» (например, 18)."""
    global _driver_cache
    explicit = explicit or config.LOGO_DRIVER      # драйвер, заданный в окружении/config.ini, - явный выбор для всей машины
    if explicit:
        return explicit
    if _driver_cache:
        return _driver_cache
    have = list(pyodbc.drivers())
    low = {d.lower(): d for d in have}
    for d in DRIVER_PREFERENCE:
        if d.lower() in low:
            _driver_cache = low[d.lower()]
            return _driver_cache
    for d in sorted(have, reverse=True):
        if re.match(r"^ODBC Driver \d+ for SQL Server$", d, re.I):
            _driver_cache = d
            return d
    raise RuntimeError("Не найден драйвер ODBC для SQL Server. Установите «ODBC Driver 17 for SQL Server» "
                       "(или «SQL Server Native Client 11.0») либо укажите имя установленного драйвера в config.ini, раздел [logo], параметр driver"
                       + (" (найдено: %s)" % ", ".join(have) if have else ""))


def _conn_string(lc: Optional[LogoConn] = None) -> str:
    lc = lc or current_conn()
    if not (lc.server or "").strip():
        raise RuntimeError("Подключение к Logo не настроено: укажите сервер, базу, логин и пароль на экране настройки подключения (или в config.ini)")
    drv = pick_driver(lc.driver)
    extra = ""
    m = re.match(r"^ODBC Driver (\d+) for SQL Server$", drv, re.I)
    if m:   # эти ключевые слова понимают только «ODBC Driver N»; встроенный «SQL Server» и Native Client их не знают
        extra = ("TrustServerCertificate=yes;" if lc.trust_cert else "") + "ApplicationIntent=ReadOnly;"
        if int(m.group(1)) >= 18:
            extra += "Encrypt=no;"
    # AutoTranslate=no: иначе драйвер сам переводит ANSI-страницу сервера в ANSI клиента с потерей букв
    return (f"DRIVER={{{drv}}};SERVER={lc.server};DATABASE={lc.database};"
            f"UID={lc.login};PWD={{{lc.password.replace(chr(125), chr(125) * 2)}}};{extra}AutoTranslate=no;")   # пароль в фигурных скобках: допустимы ; и =


def connect(oem_fallback: bool = False, lc: Optional[LogoConn] = None, ansi: Optional[str] = None, oem_cp: Optional[str] = None) -> pyodbc.Connection:
    """oem_fallback=True - построчный выбор OEM/ANSI (charset.decode_auto) для книг с признаком oem. lc - подключение (по умолчанию - набора),
    ansi/oem_cp - кодовые страницы (по умолчанию - параметры набора)."""
    lc = lc or current_conn()
    if ansi is None:
        ansi, o2 = current_charset()
        oem_cp = o2 if oem_cp is None else oem_cp
    cs = _conn_string(lc)
    conn = pyodbc.connect(cs, timeout=lc.timeout or 10)
    use_auto = oem_fallback and bool(oem_cp)
    conn.setdecoding(pyodbc.SQL_CHAR, encoding=charset.auto_name(ansi, oem_cp) if use_auto else charset.tolerant_name(ansi))
    conn.setdecoding(pyodbc.SQL_WCHAR, encoding="utf-16le")
    conn.setencoding(encoding="utf-16le")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET DATEFORMAT ymd; SET LANGUAGE us_english;")
    cur.close()
    return conn


def query(sql: str, params: tuple = (), conn: Optional[pyodbc.Connection] = None, lc: Optional[LogoConn] = None) -> list:
    check_readonly(sql)
    own = conn is None
    if own:
        conn = connect(lc=lc)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = [tuple(r) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        if own:
            conn.close()


def ping(lc: Optional[LogoConn] = None) -> bool:
    try:
        return query("SELECT 1", lc=lc)[0][0] == 1
    except Exception:
        return False
