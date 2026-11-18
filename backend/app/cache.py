"""SQLite-кэш проводок (data/cache.db) и синхронизация с Logo (только чтение из Logo)."""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from . import books, config, dsctx, dsparams, logo, plan, store

DB_PATH = config.DATA_DIR / "cache.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS firms(firm INTEGER PRIMARY KEY, year INT, name TEXT, period TEXT, enabled INT DEFAULT 0, divisor INT DEFAULT 1,
      fingerprint TEXT, synced_at TEXT, line_count INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS sources(src INTEGER PRIMARY KEY, firm INT NOT NULL, period INT NOT NULL DEFAULT 1, name TEXT, fingerprint TEXT, synced_at TEXT,
      line_count INT DEFAULT 0, plan_ver INT DEFAULT 0, attr_ver INT DEFAULT 0, checked_ts REAL, open_imbalance INT, oem INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS accounts(firm INT, code TEXT, name TEXT, level INT, parent TEXT, root TEXT, is_leaf INT,
      specode TEXT, cyphcode TEXT, center_ctrl INT, units TEXT, def_name TEXT, full_name TEXT, note TEXT, created TEXT, created_by TEXT,
      inactive INT DEFAULT 0, specode_name TEXT, name_restored INT DEFAULT 0, PRIMARY KEY(firm, code));
CREATE TABLE IF NOT EXISTS specodes(firm INT, specode TEXT, name TEXT, PRIMARY KEY(firm, specode));
CREATE TABLE IF NOT EXISTS lines(firm INT, year INT, id INT, fiche_id INT, fiche_no TEXT, date TEXT, trcode INT, line_no INT,
      account TEXT, dt INT, kt INT, usd_dt INT, usd_kt INT, cross_code TEXT, center TEXT,
      text TEXT, fiche_text TEXT, is_opening INT, is_closing INT DEFAULT 0,
      branch INT, dept INT, qty_dt REAL, qty_kt REAL, PRIMARY KEY(firm, id));
CREATE INDEX IF NOT EXISTS ix_lines_date ON lines(firm, date);
CREATE INDEX IF NOT EXISTS ix_lines_acc ON lines(firm, account, date);
CREATE INDEX IF NOT EXISTS ix_lines_fiche ON lines(firm, fiche_id);
CREATE TABLE IF NOT EXISTS pairs(firm INT, year INT, fiche_id INT, date TEXT, dt_account TEXT, kt_account TEXT, amount INT, usd_amount INT,
      method TEXT, disputed INT, is_closing INT, dt_line INT, kt_line INT, usd_kt_amount INT);
CREATE TABLE IF NOT EXISTS centers(firm INT, code TEXT, name TEXT, PRIMARY KEY(firm, code));
CREATE TABLE IF NOT EXISTS fiches(firm INT, fiche_id INT, module INT, doc_no TEXT, created_by INT, created_at TEXT, text_ext TEXT,
      PRIMARY KEY(firm, fiche_id));
CREATE TABLE IF NOT EXISTS branches(firm INT, id INT, name TEXT, PRIMARY KEY(firm, id));
CREATE TABLE IF NOT EXISTS departments(firm INT, id INT, name TEXT, PRIMARY KEY(firm, id));
CREATE TABLE IF NOT EXISTS logo_users(id INT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS modules(id INT PRIMARY KEY, name TEXT, confirmed INT);
"""


def cache_path():
    return dsctx.current().cache_path or DB_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(cache_path(), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-%d" % (dsctx.current().cache_kb or config.CACHE_PAGES_KB))
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


ACCOUNT_NEW_COLUMNS = (("units", "TEXT"), ("def_name", "TEXT"), ("full_name", "TEXT"), ("note", "TEXT"), ("created", "TEXT"), ("created_by", "TEXT"),
                       ("inactive", "INT DEFAULT 0"), ("specode_name", "TEXT"), ("name_restored", "INT DEFAULT 0"))
PLAN_VER = 2
ATTR_VER = 1             # версия атрибутов проводок/ордеров в кэше (волна 7: подразделение, отдел, количество; источник, автор, дата ввода, шифр, GENEXP2-4)
LINE_NEW_COLUMNS = (("branch", "INT"), ("dept", "INT"), ("qty_dt", "REAL"), ("qty_kt", "REAL"))

PAIRS_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_pairs_fiche ON pairs(firm, fiche_id);
CREATE INDEX IF NOT EXISTS ix_pairs_date ON pairs(firm, date);
CREATE INDEX IF NOT EXISTS ix_pairs_dtacc ON pairs(firm, dt_account, date);
CREATE INDEX IF NOT EXISTS ix_pairs_ktacc ON pairs(firm, kt_account, date);
CREATE INDEX IF NOT EXISTS ix_pairs_dtline ON pairs(firm, dt_line);
CREATE INDEX IF NOT EXISTS ix_pairs_ktline ON pairs(firm, kt_line);
"""


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pairs)")}
        if "dt_line" not in cols:   # схема волны 1 (пустая pairs) -> строчные пары волны 2
            conn.execute("DROP TABLE pairs")
            conn.execute("""CREATE TABLE pairs(firm INT, year INT, fiche_id INT, date TEXT, dt_account TEXT, kt_account TEXT, amount INT,
                usd_amount INT, method TEXT, disputed INT, is_closing INT, dt_line INT, kt_line INT, usd_kt_amount INT)""")
        conn.executescript(PAIRS_INDEXES)
        acols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
        for col, typ in ACCOUNT_NEW_COLUMNS:   # волна 6: доп. поля плана счетов; значения заполняет refresh_plans (без перезагрузки проводок)
            if col not in acols:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {typ}")
        lcols = {r[1] for r in conn.execute("PRAGMA table_info(lines)")}
        for col, typ in LINE_NEW_COLUMNS:   # волна 7: значения заполняет refresh_attrs / sync_firm; до этого attr_ver=0
            if col not in lcols:
                conn.execute(f"ALTER TABLE lines ADD COLUMN {col} {typ}")
        _seed_sources(conn)
        conn.execute("UPDATE sources SET open_imbalance=(SELECT COALESCE(SUM(dt-kt),0) FROM lines l WHERE l.firm=sources.src AND l.is_opening=1) "
                     "WHERE open_imbalance IS NULL AND synced_at IS NOT NULL")
        from . import attrs
        conn.execute("DELETE FROM modules")
        conn.executemany("INSERT INTO modules VALUES(?,?,?)", [(i, n, 1 if c else 0) for i, (n, c) in attrs.MODULES.items()])
        conn.commit()
    finally:
        conn.close()


def _seed_sources(conn: sqlite3.Connection) -> None:
    """Версия кэша 2: техническое состояние источников. Первое открытие существующего кэша: переносится из прежней таблицы firms (она не меняется
    и новым кодом не читается; для периода 01 src = номер фирмы, строки lines/pairs/accounts остаются верными)."""
    fcols = {r[1] for r in conn.execute("PRAGMA table_info(firms)")}
    need = {"firm", "name", "fingerprint", "synced_at", "line_count", "plan_ver", "attr_ver", "checked_ts", "open_imbalance", "year"}
    if not need <= fcols:
        return
    from . import legacy_gap
    conn.execute("INSERT OR IGNORE INTO sources(src, firm, period, name, fingerprint, synced_at, line_count, plan_ver, attr_ver, checked_ts, open_imbalance, oem) "
                 "SELECT firm, firm, 1, name, fingerprint, synced_at, line_count, plan_ver, COALESCE(attr_ver,0), checked_ts, open_imbalance, "
                 "CASE WHEN year < ? THEN 1 ELSE 0 END FROM firms WHERE synced_at IS NOT NULL", (legacy_gap.LEGACY_OEM_BEFORE_YEAR,))


# ---------------------------------------------------------------------------------------------
# Источники и книги (совместимый слой старого API «год → фирма» поверх books)
# ---------------------------------------------------------------------------------------------

def _rt():
    return dsctx.current()


def ensure_source(conn: sqlite3.Connection, src: int) -> None:
    firm, period = books.src_parts(src)
    if conn.execute("SELECT 1 FROM sources WHERE src=?", (src,)).fetchone() is None:
        nm = ""
        for b in books.of_src(src):
            nm = nm or b.name
        conn.execute("INSERT INTO sources(src, firm, period, name, oem) VALUES(?,?,?,?,0)", (src, firm, period, nm or f"Фирма {firm}"))


def source_rows(conn: Optional[sqlite3.Connection] = None) -> Dict[int, dict]:
    own = conn is None
    conn = conn or connect()
    try:
        return {r["src"]: dict(r) for r in conn.execute("SELECT * FROM sources")}
    finally:
        if own:
            conn.close()


def label_of_src(src: int) -> int:
    """Метка книги источника (основной, иначе любой); 0 - если книг нет."""
    bs = books.of_src(src)
    for b in bs:
        if b.role == "main":
            return b.label
    return bs[0].label if bs else 0


def oem_for_src(src: int) -> bool:
    return any(b.oem for b in books.of_src(src))


def divisor_of_src(src: int) -> int:
    bs = books.of_src(src)
    return bs[0].divisor if bs else 1


def _slot_of(label: int):
    return [b for b in books.all_books() if b.label == label]


def list_firms() -> list:
    """Строка на книгу любой роли + источники последнего скана без книг (enabled=false)."""
    bks = books.all_books()
    rows = source_rows()
    by_slot: Dict[int, list] = {}
    for b in bks:
        by_slot.setdefault(b.slot, []).append(b)
    out = []
    for b in sorted(bks, key=lambda b: (b.label, b.date_from, b.src_firm, b.id)):
        r = rows.get(b.src) or {}
        sl = by_slot[b.slot]
        dflt = min(sl, key=lambda x: x.id)
        d = {"firm": b.src_firm, "year": b.label, "name": r.get("name") or b.name or f"Фирма {b.src_firm}", "period": "%02d" % b.src_period,
             "enabled": b.role == "main", "divisor": b.divisor, "synced_at": r.get("synced_at"), "line_count": r.get("line_count") or 0,
             "open_imbalance": r.get("open_imbalance"), "role": b.role, "book_id": b.id, "src": b.src,
             "date_from": b.date_from.isoformat(), "date_to": b.date_to.isoformat(),
             "alternatives": [{"firm": x.src_firm, "name": (rows.get(x.src) or {}).get("name") or x.name} for x in sl if x.id != b.id and x.role != "off"],
             "is_default": b.id == dflt.id}
        out.append(d)
    have = {b.src for b in bks}
    for src, r in sorted(rows.items()):
        if src not in have:
            out.append({"firm": r["firm"], "year": None, "name": r["name"], "period": "%02d" % r["period"], "enabled": False, "divisor": 1,
                        "synced_at": r["synced_at"], "line_count": r["line_count"] or 0, "open_imbalance": r["open_imbalance"], "role": "off",
                        "book_id": None, "src": src, "alternatives": [], "is_default": False})
    return out


def list_years() -> list:
    """Метки с основной книгой: [{year, firm, name, enabled, synced_at, line_count, divisor, default_firm, alternatives, book_id, date_from, date_to, parts}]."""
    rows = source_rows()
    bks = books.all_books()
    out = []
    for y in books.labels():
        parts = books.mains_of_label(y)
        cur = parts[-1]
        r = rows.get(cur.src) or {}
        slot_books = [b for b in bks if b.slot == cur.slot]
        dflt = min(slot_books, key=lambda x: x.id)
        alts = [b for b in slot_books if b.id != cur.id and b.role == "alt"]
        out.append({"year": y, "firm": cur.src_firm, "name": r.get("name") or cur.name or f"Фирма {cur.src_firm}", "enabled": True,
                    "synced_at": r.get("synced_at"), "line_count": r.get("line_count") or 0, "divisor": cur.divisor,
                    "default_firm": dflt.src_firm,
                    "alternatives": [{"firm": b.src_firm, "name": (rows.get(b.src) or {}).get("name") or b.name,
                                      "synced_at": (rows.get(b.src) or {}).get("synced_at"), "line_count": (rows.get(b.src) or {}).get("line_count") or 0}
                                     for b in alts],
                    "book_id": cur.id, "date_from": parts[0].date_from.isoformat(), "date_to": parts[-1].date_to.isoformat(),
                    "parts": [{"book_id": b.id, "firm": b.src_firm, "date_from": b.date_from.isoformat(), "date_to": b.date_to.isoformat()} for b in parts]})
    return out


def _reset_source(conn: sqlite3.Connection, src: int) -> None:
    ensure_source(conn, src)
    conn.execute("UPDATE sources SET fingerprint=NULL, checked_ts=NULL, synced_at=NULL WHERE src=?", (src,))
    _rt().last_check.pop(src, None)


def set_firm_enabled(firm: int, enabled: bool) -> bool:
    """PUT /api/firms/{firm}: true - книга основная в своём слоте (остальные слота - запасные), false - не использовать."""
    bs = [b for b in books.all_books() if b.src == firm or (b.src_firm == firm and b.src_period == 1)]
    if not bs:
        return False
    b = bs[0]
    if enabled:
        books.make_main(b.id)
    else:
        conn = store.connect()
        try:
            conn.execute("UPDATE books SET role='off', updated_at=? WHERE id=?", (store.now(), b.id))
            conn.commit()
        finally:
            conn.close()
        _rt().books_ver += 1
    return True


def set_year_book(year: int, firm: int) -> dict:
    """Переключает основную книгу метки года на книгу фирмы firm (того же слота). У новой книги сбрасывается отпечаток (следующая проверка/синхронизация
    перезагрузит её целиком вместе с пересчётом пар корреспонденции). Возвращает {year, firm, previous, changed}. ValueError - по-русски."""
    cands = [b for b in books.all_books() if b.label == year and b.role != "off"]
    if not cands:
        raise ValueError(f"Для {year} года нет альтернативных книг")
    allowed = sorted({b.src_firm for b in cands})
    pick = [b for b in cands if b.src_firm == firm]
    if not pick:
        raise ValueError(f"Фирма {firm} не является книгой {year} года (допустимы: {', '.join(map(str, allowed))})")
    new = sorted(pick, key=lambda b: (b.role != "main", b.src_period))[0]
    cur = books.book_for_label(year)
    prev = cur.src_firm if cur else None
    changed = not (new.role == "main" and prev == firm)
    if changed:
        books.make_main(new.id)
        conn = connect()
        try:
            _reset_source(conn, new.src)
            conn.commit()
        finally:
            conn.close()
    store.set_year_book(year, firm)
    return {"year": year, "firm": firm, "previous": prev, "changed": changed}


def year_firm_map(conn: Optional[sqlite3.Connection] = None) -> Dict[int, dict]:
    """метка -> {firm (= src последней основной части), divisor, synced, book}: совместимый слой над books."""
    own = conn is None
    conn = conn or connect()
    try:
        rows = {r["src"]: r["synced_at"] for r in conn.execute("SELECT src, synced_at FROM sources")}
    finally:
        if own:
            conn.close()
    out: Dict[int, dict] = {}
    for y in books.labels():
        b = books.book_for_label(y)
        out[y] = {"firm": b.src, "divisor": b.divisor or 1, "synced": rows.get(b.src) is not None, "book": b.id}
    return out


# ---------------------------------------------------------------------------------------------
# Закрывающие ордера
# ---------------------------------------------------------------------------------------------

def _account_expr(masks: list[str]) -> tuple[str, list]:
    return plan.current().account_sql(masks, "account")


def apply_closing(conn: sqlite3.Connection, firms: list[int] | None = None) -> dict[int, int]:
    """Пересчитывает lines.is_closing по включённым правилам (OR). Ордер закрывающий целиком.
    kind=accounts: ВСЕ строки ордера лежат на счетах из списка масок; kind=text: подстрока (без регистра) в тексте ордера/строки.
    Не делает commit. Возвращает {firm: число помеченных строк}."""
    rules = [r for r in store.get_closing_rules() if r["enabled"]]
    if firms is None:
        firms = [r[0] for r in conn.execute("SELECT DISTINCT firm FROM lines")]
    res: dict[int, int] = {}
    for firm in firms:
        conn.execute("UPDATE lines SET is_closing=0 WHERE firm=? AND is_closing<>0", (firm,))
        for r in rules:
            if r["kind"] == "accounts":
                masks = [x for x in r["value"].replace(";", ",").split(",") if x.strip()]
                expr, params = _account_expr(masks)
                conn.execute(
                    f"UPDATE lines SET is_closing=1 WHERE firm=? AND is_opening=0 AND fiche_id IN ("
                    f"SELECT fiche_id FROM lines WHERE firm=? AND is_opening=0 GROUP BY fiche_id "
                    f"HAVING SUM(CASE WHEN ({expr}) THEN 0 ELSE 1 END)=0)", [firm, firm, *params])
            elif r["kind"] == "text":
                s = r["value"].lower()
                conn.execute(
                    "UPDATE lines SET is_closing=1 WHERE firm=? AND is_opening=0 AND fiche_id IN ("
                    "SELECT fiche_id FROM lines WHERE firm=? AND (instr(lower(fiche_text),?)>0 OR instr(lower(text),?)>0))",
                    (firm, firm, s, s))
        conn.execute("UPDATE pairs SET is_closing=COALESCE((SELECT MAX(l.is_closing) FROM lines l "
                     "WHERE l.firm=pairs.firm AND l.fiche_id=pairs.fiche_id),0) WHERE firm=?", (firm,))
        res[firm] = conn.execute("SELECT COUNT(*) FROM lines WHERE firm=? AND is_closing=1", (firm,)).fetchone()[0]
    return res


def recompute_closing() -> dict[int, int]:
    conn = connect()
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            res = apply_closing(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return res
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------
# Синхронизация
# ---------------------------------------------------------------------------------------------

def _tables_exist(src: int) -> bool:
    t, ft = books.table_prefix(src), books.firm_table_prefix(src)
    rows = logo.query("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME IN (?,?,?)",
                      (t + "EMFLINE", t + "EMFICHE", ft + "EMUHACC"))
    return rows[0][0] == 3


def fingerprint(src: int, lconn=None) -> str:
    """Отпечаток фирмы для проверки перед расчётом. Читает мало страниц БД: число строк и MAX(LOGICALREF) EMFLINE (идёт по самому узкому
    индексу, ~100 страниц), агрегат EMFICHE (число, MAX(LOGICALREF), ΣTOTALACTIVE/ΣTOTALPASSIVE как decimal - детерминировано, число отменённых,
    MAX(CAPIBLOCK_MODIFIEDDATE); ~300 страниц) и EMUHACC (число, MAX(CAPIBLOCK_MODIFIEDDATE)). Итоги ордеров равны суммам их строк
    (проверено: ΣTOTALACTIVE = ΣDEBIT по EMFLINE на нескольких фирмах), правка суммы меняет и итог ордера, и дату изменения.
    Причина прежних 13-28 с на фирму (замер, 2026-11-17): не SUM(ROUND(...)) и не соединение, а пять полных сканов кластерного индекса EMFLINE
    (~2400 страниц, 19 МБ) в одном запросе, при «холодном» кэше SQL Server читающем страницы с диска по одной (~4 мс на страницу); один и тот же запрос
    то 0,03 с, то 15 с в зависимости от состояния кэша, MAXDOP на это не влияет. Новый запрос читает ~450 страниц вместо ~12 000."""
    t, ft = books.table_prefix(src), books.firm_table_prefix(src)
    sql = (f"SELECT (SELECT COUNT(*) FROM {t}EMFLINE), (SELECT ISNULL(MAX(LOGICALREF),0) FROM {t}EMFLINE), "
           f"fi.n, fi.mx, fi.sa, fi.sp, fi.canc, fi.md, ac.n, ac.md, fi.ma, fi.br, fi.tx FROM "
           f"(SELECT COUNT(*) n, ISNULL(MAX(LOGICALREF),0) mx, ISNULL(SUM(CAST(TOTALACTIVE AS decimal(28,4))),0) sa, "
           f"ISNULL(SUM(CAST(TOTALPASSIVE AS decimal(28,4))),0) sp, ISNULL(SUM(CAST(CANCELLED AS int)),0) canc, "
           f"CONVERT(varchar(30), MAX(CAPIBLOCK_MODIFIEDDATE), 121) md, "
           # волна 7: источник/автор, подразделение/отдел ордера (строки наследуют BRANCH ордера), длина шифра и GENEXP2-4 - тот же скан EMFICHE
           f"ISNULL(SUM(CAST(MODULENR AS bigint)*3 + CAST(CAPIBLOCK_CREATEDBY AS bigint)),0) ma, "
           f"ISNULL(SUM(CAST(BRANCH AS bigint)*5 + CAST(DEPARTMENT AS bigint)),0) br, "
           f"ISNULL(SUM(CAST(ISNULL(LEN(DOCODE),0) + ISNULL(LEN(GENEXP2),0) + ISNULL(LEN(GENEXP3),0) + ISNULL(LEN(GENEXP4),0) AS bigint)),0) tx "
           f"FROM {t}EMFICHE) fi, "
           f"(SELECT COUNT(*) n, CONVERT(varchar(30), MAX(CAPIBLOCK_MODIFIEDDATE), 121) md FROM {ft}EMUHACC) ac "
           f"OPTION (MAXDOP 1)")
    r = logo.query(sql, conn=lconn)[0]
    return "v4|" + "|".join(str(x) for x in r)


def _cents(x) -> int:
    return int(round((x or 0) * 100))


def _norm_name(x: str) -> str:
    return " ".join((x or "").split()).lower()


def restore_name(definition: str, cldef: str) -> str:
    """Название счёта: CLDEF, если он непустой, длиннее DEFINITION_ и начинается с него (после trim, без регистра и повторных пробелов) - иначе DEFINITION_.
    DEFINITION_ в Logo обрезано до 50-51 символа, CLDEF хранит название целиком."""
    d, c = (definition or "").strip(), (cldef or "").strip()
    nd, nc = _norm_name(d), _norm_name(c)
    if nc and len(nc) > len(nd) and nc.startswith(nd):
        return c
    return d


def _created_date(v) -> str | None:
    """Дата создания счёта; в книгах до 2008 года там шаблонный мусор (1988-01-01, 2099-01-28) - значения вне 1995..текущий год дают None."""
    if v is None or not hasattr(v, "year"):
        return None
    if v.year < 1995 or v.year > datetime.now().year:
        return None
    return "%04d-%02d-%02d" % (v.year, v.month, v.day)


def load_plan_from_logo(src: int, lconn) -> tuple[list[tuple], list[tuple]]:
    """Читает из Logo план счетов фирмы источника, метки (SPECODES) и пользователей: (accounts-строки, specodes-строки). Только SELECT."""
    ft = books.firm_table_prefix(src)
    acc_rows = logo.query(f"SELECT CODE, DEFINITION_, SPECODE, CYPHCODE, CENTERCTRL, UNITS, CLDEF, EXTNAME, CAPIBLOCK_CREADEDDATE, CAPIBLOCK_CREATEDBY, ACTIVE "
                          f"FROM {ft}EMUHACC", conn=lconn)
    try:
        spec_rows = logo.query(f"SELECT SPECODE, DEFINITION_ FROM {ft}SPECODES WHERE SPECODETYPE = 35 AND CODETYPE = 1", conn=lconn)
    except Exception:
        spec_rows = []
    try:
        users = {int(n): (nm or "").strip() for n, nm in logo.query("SELECT NR, NAME FROM L_CAPIUSER", conn=lconn)}
    except Exception:
        users = {}
    specs = {}
    for sc, nm in spec_rows:
        sc, nm = (sc or "").strip(), (nm or "").strip()
        if sc and nm:
            specs.setdefault(sc, nm)
    return _build_accounts(src, acc_rows, users, specs), [(src, k, v) for k, v in specs.items()]


def _build_accounts(firm: int, rows: list[tuple], users: dict | None = None, specs: dict | None = None, prof=None) -> list[tuple]:
    users, specs = users or {}, specs or {}
    prof = prof or plan.current()
    codes = sorted({(r[0] or "").strip() for r in rows})
    code_set = set(codes)
    leaves = prof.leaf_set(codes)
    out = []
    seen = set()
    for code, name, spec, cyph, cc, units, cldef, extname, cdate, cby, active in rows:
        code = (code or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        parent = prof.parent_of(code, code_set)
        definition = (name or "").strip()
        shown = restore_name(definition, cldef)
        spec = (spec or "").strip()
        out.append((firm, code, shown, prof.level_of(code), parent, prof.root_of(code), 1 if code in leaves else 0,
                    spec, (cyph or "").strip(), 1 if cc else 0,
                    (units or "").strip(), definition, (cldef or "").strip(), (extname or "").strip(), _created_date(cdate),
                    (users.get(int(cby)) or None) if cby else None, 1 if active else 0, specs.get(spec) if spec else None,
                    1 if shown != definition else 0))
    return out


ACC_INSERT = ("INSERT OR REPLACE INTO accounts(firm, code, name, level, parent, root, is_leaf, specode, cyphcode, center_ctrl, units, def_name, full_name, "
              "note, created, created_by, inactive, specode_name, name_restored) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def _logo_connect(src: int):
    """Подключение к Logo для чтения источника: признак OEM-текстов берётся из книг источника."""
    return logo.connect(oem_fallback=oem_for_src(src))


def _src_row(conn: sqlite3.Connection, src: int):
    ensure_source(conn, src)
    return conn.execute("SELECT * FROM sources WHERE src=?", (src,)).fetchone()


def refresh_plan(src: int) -> dict:
    """Перечитывает ТОЛЬКО план счетов и метки фирмы источника из Logo (проводки, пары, отпечаток не трогаются). {firm, accounts, restored}."""
    if not books.of_src(src):
        raise ValueError(f"Источник {src} не входит ни в одну книгу набора")
    lconn = _logo_connect(src)
    try:
        accs, specs = load_plan_from_logo(src, lconn)
    finally:
        lconn.close()
    cconn = connect()
    try:
        with cconn:
            ensure_source(cconn, src)
            cconn.execute("DELETE FROM accounts WHERE firm=?", (src,))
            cconn.execute("DELETE FROM specodes WHERE firm=?", (src,))
            cconn.executemany(ACC_INSERT, accs)
            cconn.executemany("INSERT OR REPLACE INTO specodes VALUES(?,?,?)", specs)
            cconn.execute("UPDATE sources SET plan_ver=? WHERE src=?", (PLAN_VER, src))
    finally:
        cconn.close()
    return {"firm": src, "accounts": len(accs), "restored": sum(1 for a in accs if a[-1])}


def refresh_plans(force: bool = False) -> list:
    """План счетов всех загруженных книг, у которых версия плана устарела (или всех при force). Запускается при старте сервера в фоне."""
    cconn = connect()
    try:
        if force:
            todo = [r["src"] for r in cconn.execute("SELECT src FROM sources WHERE synced_at IS NOT NULL")]
        else:
            todo = [r["src"] for r in cconn.execute("SELECT src FROM sources WHERE synced_at IS NOT NULL AND COALESCE(plan_ver,0) < ?", (PLAN_VER,))]
    finally:
        cconn.close()
    out = []
    for fm in todo:
        try:
            with _lock(fm):
                out.append(refresh_plan(fm))
        except Exception as e:   # Logo недоступна и т.п. - повторим при следующем запуске
            out.append({"firm": fm, "error": str(e)})
    return out


LINE_INSERT = ("INSERT INTO lines(firm, year, id, fiche_id, fiche_no, date, trcode, line_no, account, dt, kt, usd_dt, usd_kt, cross_code, center, "
               "text, fiche_text, is_opening, is_closing, branch, dept, qty_dt, qty_kt) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def _entered_at(dt_, hour, minute):
    """Дата и время ввода ордера: CAPIBLOCK_CREADEDDATE (дата) + CREATEDHOUR/CREATEDMIN; None, если даты нет."""
    if dt_ is None or not hasattr(dt_, "year"):
        return None
    return "%04d-%02d-%02d %02d:%02d" % (dt_.year, dt_.month, dt_.day, int(hour or 0), int(minute or 0))


def load_attrs_dicts(src: int, lconn, cconn) -> int:
    """Читает из Logo атрибуты ордеров (EMFICHE: источник MODULENR, автор, дата/время ввода, DOCODE, GENEXP2-4) и справочники
    подразделений/отделов (L_CAPIDIV/L_CAPIDEPT по номеру фирмы) и пользователей (L_CAPIUSER). Пишет в кэш (не делает commit; строки источника
    в fiches/branches/departments должны быть удалены заранее). Возвращает число ордеров. Только SELECT."""
    t = books.table_prefix(src)
    firm = books.src_parts(src)[0]
    rows = logo.query(f"SELECT LOGICALREF, MODULENR, DOCODE, CAPIBLOCK_CREATEDBY, CAPIBLOCK_CREADEDDATE, CAPIBLOCK_CREATEDHOUR, CAPIBLOCK_CREATEDMIN, "
                      f"GENEXP2, GENEXP3, GENEXP4 FROM {t}EMFICHE", conn=lconn)
    batch = []
    for ref, mod, doc, by, cd, ch, cm, g2, g3, g4 in rows:
        ext = ((g2 or "") + (g3 or "") + (g4 or "")).strip()   # GENEXP1..4 - один текст, нарезанный по 50 знаков: склеивается без разделителя
        batch.append((src, ref, mod, (doc or "").strip(), by or None, _entered_at(cd, ch, cm), ext))
    cconn.executemany("INSERT OR REPLACE INTO fiches VALUES(?,?,?,?,?,?,?)", batch)
    for table, tbl in (("branches", "L_CAPIDIV"), ("departments", "L_CAPIDEPT")):
        try:
            nm = logo.query(f"SELECT NR, NAME FROM {tbl} WHERE FIRMNR = ?", (firm,), conn=lconn)
        except Exception:
            nm = []
        cconn.executemany(f"INSERT OR REPLACE INTO {table} VALUES(?,?,?)", [(src, int(n), (x or "").strip()) for n, x in nm])
    try:
        for n, nm in logo.query("SELECT NR, NAME FROM L_CAPIUSER", conn=lconn):
            cconn.execute("INSERT OR REPLACE INTO logo_users VALUES(?,?)", (int(n), (nm or "").strip()))
    except Exception:
        pass
    return len(batch)


def refresh_attrs(src: int) -> dict:
    """Дочитывает из Logo ТОЛЬКО атрибуты (подразделение, отдел, количество проводок; атрибуты ордеров; справочники) в уже загруженный источник,
    не трогая суммы, пары и отпечаток. Для книг, загруженных до волны 7 (attr_ver < ATTR_VER). Если набор строк Logo уже
    изменился (число строк не совпало) - полная синхронизация. {firm, lines, fiches, seconds, full}."""
    t0 = time.time()
    t = books.table_prefix(src)
    cconn = connect()
    row = _src_row(cconn, src)
    row = dict(row)
    cconn.commit()
    cconn.close()
    lconn = _logo_connect(src)
    try:
        # данные Logo не должны были измениться с загрузки: первые 10 полей отпечатка (v3 и v4 совпадают по ним) те же; иначе - полная синхронизация
        fp_new = fingerprint(src, lconn)
        old_fp = (row["fingerprint"] or "").split("|")
        if old_fp[1:11] != fp_new.split("|")[1:11]:
            return dict(sync_firm(src), full=True)
        sql = f"SELECT LOGICALREF, BRANCH, DEPARTMENT, AMNT, SIGN FROM {t}EMFLINE WHERE CANCELLED = 0"
        logo.check_readonly(sql)
        cur = lconn.cursor()
        cur.execute(sql)
        upd = []
        while True:
            chunk = cur.fetchmany(20000)
            if not chunk:
                break
            upd += [(br, dp, (am or 0.0) if not sg else 0.0, (am or 0.0) if sg else 0.0, src, lid) for lid, br, dp, am, sg in chunk]
        cur.close()
        if len(upd) != row["line_count"]:
            return dict(sync_firm(src), full=True)
        cconn = connect()
        cconn.isolation_level = None
        cconn.execute("BEGIN IMMEDIATE")
        try:
            for tb in ("fiches", "branches", "departments"):
                cconn.execute(f"DELETE FROM {tb} WHERE firm=?", (src,))
            nf = load_attrs_dicts(src, lconn, cconn)
            cconn.executemany("UPDATE lines SET branch=?, dept=?, qty_dt=?, qty_kt=? WHERE firm=? AND id=?", upd)
            cconn.execute("UPDATE sources SET attr_ver=?, fingerprint=? WHERE src=?", (ATTR_VER, fp_new, src))
            cconn.execute("COMMIT")
        except Exception:
            cconn.execute("ROLLBACK")
            raise
        finally:
            cconn.close()
    finally:
        lconn.close()
    return {"firm": src, "lines": len(upd), "fiches": nf, "seconds": round(time.time() - t0, 1), "full": False}


def stale_attr_firms(firms: list[int] | None = None) -> list[int]:
    conn = connect()
    try:
        rows = conn.execute("SELECT src FROM sources WHERE synced_at IS NOT NULL AND COALESCE(attr_ver,0) < ?", (ATTR_VER,)).fetchall()
    finally:
        conn.close()
    have = [r[0] for r in rows]
    return [x for x in have if firms is None or x in firms]


def refresh_attrs_all() -> list:
    """Запускается при старте сервера в фоне: атрибуты всех загруженных книг со старой версией (проводки/пары/суммы не трогаются)."""
    out = []
    for fm in stale_attr_firms():
        try:
            with _lock(fm):
                if fm in stale_attr_firms([fm]):
                    out.append(refresh_attrs(fm))
        except Exception as e:   # Logo недоступна - повторим при следующем запуске/запросе
            out.append({"firm": fm, "error": str(e)})
    return out


def _src_title(src: int) -> str:
    bs = books.of_src(src)
    return bs[0].title() if bs else f"источник {src}"


def ensure_attrs(firms: list[int]) -> list[str]:
    """Перед отчётом, использующим атрибуты: книги со старой версией атрибутов дочитываются из Logo синхронно (секунды) - отчёт не
    отдаёт пустые подразделения/источники молча. Ошибка чтения Logo -> ValueError (по-русски). Возвращает предупреждения."""
    warns: list[str] = []
    for fm in stale_attr_firms(firms):
        with _lock(fm):
            if fm not in stale_attr_firms([fm]):
                continue     # другой поток уже обновил, пока ждали замок
            try:
                res = refresh_attrs(fm)
            except Exception as e:
                raise ValueError(f"Атрибуты ордеров и проводок книги {books.src_parts(fm)[0]} ещё не загружены из Logo, а Logo недоступно: {e}")
            warns.append(f"Атрибуты ордеров и проводок книги {books.src_parts(fm)[0]} (подразделения, источники, авторы, количество) загружены из Logo ({res['seconds']} с)")
    return warns


def sync_firm(src: int, progress=None) -> dict:
    """Полная загрузка источника с ожиданием места в общей очереди: одновременно не более config.SYNC_PARALLEL загрузок на весь процесс (все наборы);
    пока ждём, job.queued=True и сообщение «в очереди» (интерфейс показывает статус)."""
    rt = _rt()

    def waiting():
        if progress:
            progress(f"Источник {src}: в очереди на загрузку")
    with dsctx.sync_turn(rt, waiting):
        return _sync_firm(src, progress)


def _sync_firm(src: int, progress=None) -> dict:
    """Полностью перезагружает источник (весь период Logo, независимо от диапазонов книг) в одной транзакции кэша. Возвращает {firm, lines, seconds}."""
    t0 = time.time()
    t, ft = books.table_prefix(src), books.firm_table_prefix(src)
    firm = books.src_parts(src)[0]
    if not books.of_src(src):
        raise ValueError(f"Источник {src} не входит ни в одну книгу набора")
    label = label_of_src(src)
    if not _tables_exist(src):
        raise ValueError(f"В Logo нет таблиц проводок фирмы {firm}")
    oem = oem_for_src(src)
    lconn = _logo_connect(src)
    try:
        fp = fingerprint(src, lconn)
        acc_list, spec_list = load_plan_from_logo(src, lconn)
        cen_rows = logo.query(f"SELECT CODE, DEFINITION_ FROM {ft}EMCENTER", conn=lconn)
        try:
            fname = ((logo.query("SELECT NAME FROM L_CAPIFIRM WHERE NR=?", (firm,), conn=lconn) or [("",)])[0][0] or "").strip()
        except Exception:
            fname = ""
        sql = (f"SELECT l.LOGICALREF, l.ACCFICHEREF, f.FICHENO, l.DATE_, l.TRCODE, l.LINENO_, l.ACCOUNTCODE, l.DEBIT, l.CREDIT, l.SIGN, l.REPORTNET, "
               f"l.CROSSCODE, c.CODE, l.LINEEXP, f.GENEXP1, l.BRANCH, l.DEPARTMENT, l.AMNT "
               f"FROM {t}EMFLINE l LEFT JOIN {t}EMFICHE f ON f.LOGICALREF = l.ACCFICHEREF "
               f"LEFT JOIN {ft}EMCENTER c ON c.LOGICALREF = l.CENTERREF WHERE l.CANCELLED = 0")
        logo.check_readonly(sql)
        cconn = connect()
        cconn.isolation_level = None
        cconn.execute("BEGIN IMMEDIATE")
        try:
            ensure_source(cconn, src)
            for tb in ("accounts", "specodes", "lines", "centers", "pairs", "fiches", "branches", "departments"):
                cconn.execute(f"DELETE FROM {tb} WHERE firm=?", (src,))
            cconn.executemany(ACC_INSERT, acc_list)
            load_attrs_dicts(src, lconn, cconn)
            cconn.executemany("INSERT OR REPLACE INTO specodes VALUES(?,?,?)", spec_list)
            cconn.executemany("INSERT OR REPLACE INTO centers VALUES(?,?,?)",
                              [(src, (c or "").strip(), (n or "").strip()) for c, n in cen_rows if (c or "").strip()])
            cur = lconn.cursor()
            cur.execute(sql)
            total = 0
            while True:
                chunk = cur.fetchmany(20000)
                if not chunk:
                    break
                batch = []
                for (lid, fid, fno, dt_, trc, lno, acc, deb, cred, sign, rnet, cross, cen, lexp, gexp, br, dp, amnt) in chunk:
                    deb_c, cred_c = _cents(deb), _cents(cred)
                    rn = _cents(rnet)
                    batch.append((src, label, lid, fid, (fno or "").strip(), dt_.strftime("%Y-%m-%d"), trc, lno, (acc or "").strip(),
                                  deb_c, cred_c, rn if not sign else 0, rn if sign else 0,
                                  (cross or "").strip(), (cen or "").strip(), (lexp or "").strip(), (gexp or "").strip(),
                                  1 if trc == 1 else 0, 0, br, dp, (amnt or 0.0) if not sign else 0.0, (amnt or 0.0) if sign else 0.0))
                cconn.executemany(LINE_INSERT, batch)
                total += len(batch)
                if progress:
                    progress(f"Фирма {firm}: загружено {total} строк")
            cur.close()
            apply_closing(cconn, [src])
            from . import corr  # точка расчёта корреспонденции (волна 2)
            corr.rebuild_firm(cconn, src, label)
            imb = cconn.execute("SELECT COALESCE(SUM(dt-kt),0) FROM lines WHERE firm=? AND is_opening=1", (src,)).fetchone()[0]
            cconn.execute("UPDATE sources SET fingerprint=?, synced_at=?, line_count=?, checked_ts=?, open_imbalance=?, plan_ver=?, attr_ver=?, oem=?, "
                          "name=COALESCE(NULLIF(?,''), name) WHERE src=?",
                          (fp, store.now(), total, time.time(), imb, PLAN_VER, ATTR_VER, 1 if oem else 0, fname, src))
            cconn.execute("COMMIT")
        except Exception:
            cconn.execute("ROLLBACK")
            raise
        finally:
            cconn.close()
    finally:
        lconn.close()
    _rt().last_check[src] = time.time()
    return {"firm": src, "lines": total, "seconds": round(time.time() - t0, 1)}


# ---- блокировки, проверка свежести --------------------------------------------------------------

# Совместимые псевдонимы состояния набора по умолчанию (старый код и тесты читают cache.job / cache.refresh / cache._last_check);
# внутри модуля состояние берётся из dsctx.current()
job = dsctx.default().job
refresh = dsctx.default().refresh
_last_check = dsctx.default().last_check


def _lock(src: int) -> threading.Lock:
    return _rt().lock(src)


CHECK_TIMEOUT = 5          # сек. на запрос отпечатка одного источника
CHECK_BUDGET = 8.0         # сек. на все проверки одного расчёта; дальше - работаем по кэшу с предупреждением


def _ttl_for_date_to(d_to: date) -> float:
    """Книги, закончившиеся не раньше чем old_after_days назад (или идущие), проверяются не чаще ttl_current, более старые - ttl_old."""
    p = dsparams.get("sync")
    cur = max(p["ttl_current"], config.FINGERPRINT_TTL)
    if d_to >= date.today() - timedelta(days=p["old_after_days"]):
        return cur
    return max(cur, p["ttl_old"], config.FINGERPRINT_TTL_OLD)


def check_ttl(label) -> float:
    """TTL проверки отпечатка для метки года (по дате окончания книги, см. _ttl_for_date_to)."""
    if label is None:
        return _ttl_for_date_to(date.today())
    rng = books.label_range(label)
    return _ttl_for_date_to(rng[1] if rng else date(int(label), 12, 31))


def _ttl_src(src: int) -> float:
    ends = [b.date_to for b in books.of_src(src) if b.role == "main"] or [b.date_to for b in books.of_src(src)]
    return _ttl_for_date_to(max(ends) if ends else date.today())


def _mark_checked(src: int) -> None:
    now = time.time()
    _rt().last_check[src] = now
    try:
        c = connect()
        c.execute("UPDATE sources SET checked_ts=? WHERE src=?", (now, src))
        c.commit()
        c.close()
    except Exception:
        pass


def ensure_fresh(firms: list[int]) -> list[str]:
    """Перед расчётом отчёта: сверяет отпечатки (политика check_ttl) и перезагружает изменившиеся источники; отчёт строится после
    пересинхронизации. Проверка ограничена по времени (CHECK_TIMEOUT на источник, CHECK_BUDGET на расчёт) - при сбое или медленном Logo
    расчёт идёт по ранее загруженным данным с предупреждением. Возвращает предупреждения/сообщения для Result.warnings."""
    rt = _rt()
    warnings: list[str] = []
    conn = connect()
    try:
        for src in firms:
            ensure_source(conn, src)
        conn.commit()
        info = {r["src"]: dict(r) for r in conn.execute("SELECT src, fingerprint, synced_at, checked_ts FROM sources")}
    finally:
        conn.close()
    t_start = time.time()
    lconn = None
    try:
        for firm in firms:
            row = info.get(firm)
            if row is None:
                continue
            label, fno = label_of_src(firm), books.src_parts(firm)[0]
            never = row["synced_at"] is None
            last = max(rt.last_check.get(firm, 0), row["checked_ts"] or 0)
            if not never and time.time() - last < _ttl_src(firm):
                continue
            if not never and time.time() - t_start > CHECK_BUDGET:
                warnings.append(f"Проверка актуальности данных Logo занимает слишком долго - фирма {fno} (год {label}) не проверена, использованы загруженные данные")
                continue
            try:
                if lconn is None:
                    lconn = _logo_connect(firm)
                lconn.timeout = 0 if never else CHECK_TIMEOUT
                fp = fingerprint(firm, lconn)
            except Exception as e:  # Logo недоступен или не ответил за отведённое время
                if never:
                    raise ValueError(f"Данные фирмы {fno} (год {label}) ещё не загружены, а Logo недоступно: {e}")
                warnings.append(f"Logo недоступно или не ответило вовремя - использованы ранее загруженные данные (фирма {fno}, год {label})")
                _mark_checked(firm)
                lconn = None
                continue
            _mark_checked(firm)
            if never or fp != row["fingerprint"]:
                with _lock(firm):
                    # возможно, пока ждали замок, другой поток уже обновил
                    c2 = connect()
                    cur_fp = c2.execute("SELECT fingerprint FROM sources WHERE src=?", (firm,)).fetchone()[0]
                    c2.close()
                    if cur_fp != fp:
                        rt.refresh.update(active=True, firm=firm, year=label, started_at=store.now(),
                                          message=f"Данные {label} г. (фирма {fno}) изменились в Logo - перезагрузка")
                        try:
                            res = sync_firm(firm, progress=lambda m: rt.refresh.__setitem__("message", m))
                        finally:
                            rt.refresh.update(active=False, message="")
                        warnings.append(f"Данные {label} г. (фирма {fno}) изменились в Logo и были перезагружены "
                                        f"({res['lines']} строк, {res['seconds']} с) - отчёт построен по свежим данным")
    finally:
        if lconn is not None:
            try:
                lconn.close()
            except Exception:
                pass
    return warnings


# ---- фоновая синхронизация -------------------------------------------------------------------------

def main_srcs_ordered() -> list[int]:
    out, seen = [], set()
    for b in sorted(books.main_books(), key=lambda b: (b.label, b.date_from, b.src_firm)):
        if b.src not in seen:
            seen.add(b.src)
            out.append(b.src)
    return out


def start_sync(firms: list[int] | None = None, force: bool = False) -> bool:
    """Запускает фоновую синхронизацию (firms - список src; по умолчанию - источники основных книг); False, если уже идёт."""
    rt = _rt()
    with rt.job_lock:
        if rt.job["syncing"]:
            return False
        if firms is None:
            firms = main_srcs_ordered()
        rt.job.update(syncing=True, queued=False, done=0, total=len(firms), current=None, message="Запуск", started_at=store.now(),
                      finished_at=None, errors=[], results=[])
    threading.Thread(target=dsctx.run_in, args=(rt, _run_job, list(firms), force), daemon=True).start()
    return True


def _run_job(firms: list[int], force: bool) -> None:
    job = _rt().job
    try:
        for firm in firms:
            job["current"] = firm
            job["message"] = f"Фирма {firm}: проверка"
            try:
                with _lock(firm):
                    conn = connect()
                    row = conn.execute("SELECT fingerprint, synced_at FROM sources WHERE src=?", (firm,)).fetchone()
                    conn.close()
                    if not force and row and row["synced_at"]:
                        try:
                            if fingerprint(firm) == row["fingerprint"]:
                                job["results"].append({"firm": firm, "skipped": True})
                                job["done"] += 1
                                continue
                        except Exception:
                            pass
                    res = sync_firm(firm, progress=lambda m: job.__setitem__("message", m))
                    job["results"].append(res)
            except Exception as e:
                job["errors"].append(f"Фирма {firm}: {e}")
            job["done"] += 1
    finally:
        job.update(syncing=False, queued=False, current=None, message="Готово", finished_at=store.now())


def status() -> dict:
    rt = _rt()
    srcs = main_srcs_ordered()
    conn = connect()
    try:
        synced_set = {r[0] for r in conn.execute("SELECT src FROM sources WHERE synced_at IS NOT NULL")}
    finally:
        conn.close()
    job = rt.job
    return {"logo_ok": _logo_ok(), "firms_synced": len([s for s in srcs if s in synced_set]), "firms_total": len(srcs), "syncing": job["syncing"],
            "queued": bool(job.get("queued")),
            "progress": {k: job[k] for k in ("done", "total", "current", "message", "started_at", "finished_at", "errors")},
            "refresh": dict(rt.refresh)}


def _logo_ok() -> bool:
    ping = _rt().ping
    if time.time() - ping["t"] > 15:
        ping["ok"] = logo.ping()
        ping["t"] = time.time()
    return ping["ok"]


def cache_size_bytes() -> int:
    n = 0
    p0 = cache_path()
    for suffix in ("", "-wal", "-shm"):
        p = p0.parent / (p0.name + suffix)
        if p.exists():
            n += p.stat().st_size
    return n
