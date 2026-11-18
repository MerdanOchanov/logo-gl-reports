"""SQLite настроек (data/settings.db): классификаторы, отчёты, правила закрытия, названия счетов.
Миграции — по PRAGMA user_version (список MIGRATIONS)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from . import config, dsctx

DB_PATH = config.DATA_DIR / "settings.db"

MIGRATIONS = [
    # 1
    """
    CREATE TABLE classifiers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '', updated_at TEXT);
    CREATE TABLE cls_groups(id INTEGER PRIMARY KEY, classifier_id INT NOT NULL, parent_id INT NULL, code TEXT NOT NULL DEFAULT '',
                            name TEXT NOT NULL DEFAULT '', sort INT NOT NULL DEFAULT 0);
    CREATE INDEX ix_cls_groups_c ON cls_groups(classifier_id);
    CREATE TABLE cls_rules(id INTEGER PRIMARY KEY, classifier_id INT NOT NULL, group_id INT NOT NULL, kind TEXT NOT NULL,
                           value TEXT NOT NULL, year_from INT NULL, year_to INT NULL, priority INT NOT NULL DEFAULT 0);
    CREATE INDEX ix_cls_rules_c ON cls_rules(classifier_id);
    CREATE TABLE reports(id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, folder TEXT DEFAULT '',
                         definition TEXT NOT NULL, updated_at TEXT);
    CREATE TABLE report_versions(id INTEGER PRIMARY KEY, report_id INT NOT NULL, definition TEXT NOT NULL, saved_at TEXT, comment TEXT DEFAULT '');
    CREATE INDEX ix_report_versions_r ON report_versions(report_id);
    CREATE TABLE account_names(code TEXT NOT NULL, year_from INT NULL, year_to INT NULL, name TEXT NOT NULL);
    CREATE TABLE closing_rules(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL, enabled INT NOT NULL DEFAULT 1);
    CREATE TABLE templates(id INTEGER PRIMARY KEY, name TEXT, filename TEXT, uploaded_at TEXT);
    CREATE TABLE pair_overrides(firm INT, fiche_id INT, payload TEXT, PRIMARY KEY(firm, fiche_id));
    CREATE TABLE users(id INTEGER PRIMARY KEY, login TEXT UNIQUE, pass_hash TEXT, role TEXT DEFAULT 'viewer');
    """,
    # 2 (волна 2B): связи кодов счетов между годами
    """
    CREATE TABLE IF NOT EXISTS account_links(id INTEGER PRIMARY KEY, code_from TEXT NOT NULL, year_from INT NOT NULL,
                               code_to TEXT NOT NULL, year_to INT NOT NULL, method TEXT NOT NULL DEFAULT 'manual',
                               confidence REAL, note TEXT DEFAULT '', UNIQUE(code_from, year_from, year_to));
    CREATE INDEX IF NOT EXISTS ix_account_links_y ON account_links(year_from, year_to);
    """,
    # 3: правило закрытия по умолчанию расширено (580 - прошлые убытки, 591 - убыток года), чтобы пометить закрытия прошлых лет
    """
    UPDATE closing_rules SET value='570*,580*,590*,591*,690*,691*,692*' WHERE kind='accounts' AND value='570*,590*,690*,691*,692*';
    """,
    # 4: выбранная книга (фирма) года, когда на год есть несколько фирм-конкурентов
    """
    CREATE TABLE IF NOT EXISTS year_books(year INTEGER PRIMARY KEY, firm INT NOT NULL, changed_at TEXT);
    """,
    # 5 (универсальность, этап A): книги (источник Logo + диапазон дат + метка года), параметры набора, результат последнего скана Logo.
    # Только добавление таблиц; откат: старая версия их не читает (см. docs/ARCHITECTURE.md, волна 8)
    """
    CREATE TABLE IF NOT EXISTS books(
      id INTEGER PRIMARY KEY, slot INTEGER NOT NULL, label INTEGER NOT NULL CHECK(label BETWEEN 1900 AND 2200),
      part INTEGER NOT NULL DEFAULT 1, src_firm INTEGER NOT NULL, src_period INTEGER NOT NULL DEFAULT 1,
      date_from TEXT NOT NULL, date_to TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'main' CHECK(role IN ('main','alt','off')),
      name TEXT NOT NULL DEFAULT '', divisor INTEGER NOT NULL DEFAULT 1 CHECK(divisor >= 1), oem INTEGER NOT NULL DEFAULT 0,
      reason TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT, updated_at TEXT, CHECK(date_from <= date_to));
    CREATE INDEX IF NOT EXISTS ix_books_slot ON books(slot);
    CREATE INDEX IF NOT EXISTS ix_books_src ON books(src_firm, src_period);
    CREATE TABLE IF NOT EXISTS dataset_params(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS scan_cache(id INTEGER PRIMARY KEY CHECK(id=1), scanned_at TEXT, payload TEXT);
    """,
]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def settings_path():
    return dsctx.current().settings_path or DB_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate() -> None:
    conn = connect()
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        for i in range(ver, len(MIGRATIONS)):
            conn.executescript(MIGRATIONS[i])
            conn.execute(f"PRAGMA user_version={i + 1}")
            conn.commit()
        from . import dataset_migrate
        dataset_migrate.run(conn)    # данные набора №1 (книги, параметры, правило закрытия по умолчанию) - только для существующей установки
        conn.commit()
    finally:
        conn.close()


# ---- выбранная книга года ---------------------------------------------------------------

def get_year_books() -> dict[int, int]:
    conn = connect()
    try:
        return {r["year"]: r["firm"] for r in conn.execute("SELECT year, firm FROM year_books")}
    finally:
        conn.close()


def set_year_book(year: int, firm: int) -> None:
    conn = connect()
    try:
        conn.execute("INSERT OR REPLACE INTO year_books(year, firm, changed_at) VALUES(?,?,?)", (year, firm, now()))
        conn.commit()
    finally:
        conn.close()


# ---- правила закрытия и названия счетов -------------------------------------------------

def get_closing_rules() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT id, kind, value, enabled FROM closing_rules ORDER BY id")]
    finally:
        conn.close()


def set_closing_rules(rules: list[dict]) -> None:
    for r in rules:
        if r.get("kind") not in ("text", "accounts"):
            raise ValueError("Вид правила закрытия: text или accounts")
    conn = connect()
    try:
        conn.execute("DELETE FROM closing_rules")
        for r in rules:
            value = str(r.get("value", "")).strip()
            if not value:
                continue
            conn.execute("INSERT INTO closing_rules(kind, value, enabled) VALUES(?,?,?)",
                         (r["kind"], value, 1 if r.get("enabled", True) else 0))
        conn.commit()
    finally:
        conn.close()


def get_account_names() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT code, year_from, year_to, name FROM account_names ORDER BY code, year_from")]
    finally:
        conn.close()


def set_account_names(items: list[dict]) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM account_names")
        for it in items:
            code = str(it.get("code", "")).strip()
            name = str(it.get("name", "")).strip()
            if not code or not name:
                continue
            conn.execute("INSERT INTO account_names(code, year_from, year_to, name) VALUES(?,?,?,?)",
                         (code, it.get("year_from"), it.get("year_to"), name))
        conn.commit()
    finally:
        conn.close()


def name_overrides(year: int | None) -> dict[str, str]:
    """code -> переопределённое название, действующее в году (при нескольких — более узкий диапазон, затем последнее)."""
    conn = connect()
    try:
        rows = conn.execute("SELECT code, year_from, year_to, name FROM account_names ORDER BY rowid").fetchall()
    finally:
        conn.close()
    out: dict[str, tuple[int, str]] = {}
    for r in rows:
        yf, yt = r["year_from"], r["year_to"]
        if year is not None and ((yf is not None and year < yf) or (yt is not None and year > yt)):
            continue
        span = (yt if yt is not None else 9999) - (yf if yf is not None else 0)
        cur = out.get(r["code"])
        if cur is None or span <= cur[0]:
            out[r["code"]] = (span, r["name"])
    return {k: v[1] for k, v in out.items()}


# ---- сохранённые отчёты -------------------------------------------------------------------

def _default_period() -> dict:
    """Период по умолчанию для описания без периода: весь последний год основных книг набора (иначе текущий год)."""
    y = None
    try:
        from . import books
        ls = books.labels()
        y = int(ls[-1]) if ls else None
    except Exception:
        y = None
    if not y:
        y = datetime.now().year
    return {"date_from": "%d-01-01" % y, "date_to": "%d-12-31" % y}


def normalize_definition(definition, type_: str | None = None):
    """Описание отчёта с обязательными разделами (period, filters, columns, options, rows) — значения по умолчанию для своего типа.
    Существующие значения не меняются; разделы неверного вида (не объект / не список) заменяются значениями по умолчанию."""
    if not isinstance(definition, dict):
        definition = {}
    d = dict(definition)
    t = d.get("type") or type_ or "osv"
    d["type"] = t
    d.setdefault("title", "")
    p = d.get("period")
    if not isinstance(p, dict) or not p.get("date_from") or not p.get("date_to"):
        dp = _default_period()
        p = dict(p) if isinstance(p, dict) else {}
        p.setdefault("date_from", dp["date_from"])
        p.setdefault("date_to", dp["date_to"])
        if not p["date_from"]:
            p["date_from"] = dp["date_from"]
        if not p["date_to"]:
            p["date_to"] = dp["date_to"]
        d["period"] = p
    for k, v in (("currency", None), ("scale", 1), ("denominate", True), ("include_closing", True)):
        if d.get(k) is None:
            if k == "currency":
                try:
                    from . import dsparams
                    v = dsparams.get("currency_local")["code"]
                except Exception:
                    v = "TMT"
            d[k] = v
    f = d.get("filters")
    f = dict(f) if isinstance(f, dict) else {}
    for k, v in (("accounts", []), ("groups", []), ("centers", []), ("trcodes", []), ("text", "")):
        if f.get(k) is None:
            f[k] = v
    d["filters"] = f
    c = d.get("columns")
    c = dict(c) if isinstance(c, dict) else {}
    for k, v in (("balance", "net"), ("by_month", False), ("show_usd", False)):
        if c.get(k) is None:
            c[k] = v
    d["columns"] = c
    o = d.get("options")
    o = dict(o) if isinstance(o, dict) else {}
    for k, v in (("hide_zero", True), ("unmapped", True)):
        if o.get(k) is None:
            o[k] = v
    if not isinstance(o.get("sort"), dict):
        o["sort"] = {"by": "code", "dir": "asc"}
    d["options"] = o
    if not isinstance(d.get("rows"), list):
        d["rows"] = [{"kind": "account", "max_level": 1}] if t == "osv" else []
    return d


def _rep_row(r: sqlite3.Row, full: bool = False) -> dict:
    d = {"id": r["id"], "name": r["name"], "type": r["type"], "folder": r["folder"] or "", "updated_at": r["updated_at"]}
    if full:
        d["definition"] = normalize_definition(json.loads(r["definition"]), r["type"])
    return d


def list_reports() -> list[dict]:
    conn = connect()
    try:
        return [_rep_row(r) for r in conn.execute("SELECT * FROM reports ORDER BY folder, name")]
    finally:
        conn.close()


def get_report(rid: int) -> dict | None:
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        return _rep_row(r, True) if r else None
    finally:
        conn.close()


def create_report(name: str, type_: str, folder: str, definition: dict, comment: str = "") -> int:
    conn = connect()
    try:
        ts = now()
        dj = json.dumps(definition, ensure_ascii=False)
        cur = conn.execute("INSERT INTO reports(name, type, folder, definition, updated_at) VALUES(?,?,?,?,?)",
                           (name, type_, folder or "", dj, ts))
        rid = cur.lastrowid
        conn.execute("INSERT INTO report_versions(report_id, definition, saved_at, comment) VALUES(?,?,?,?)",
                     (rid, dj, ts, comment or "Создан"))
        conn.commit()
        return rid
    finally:
        conn.close()


def update_report(rid: int, body: dict) -> bool:
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        if not r:
            return False
        name = body.get("name", r["name"])
        type_ = body.get("type", r["type"])
        folder = body.get("folder", r["folder"])
        definition = body.get("definition")
        defn_json = json.dumps(definition, ensure_ascii=False) if definition is not None else r["definition"]
        ts = now()
        conn.execute("UPDATE reports SET name=?, type=?, folder=?, definition=?, updated_at=? WHERE id=?",
                     (name, type_, folder or "", defn_json, ts, rid))
        conn.execute("INSERT INTO report_versions(report_id, definition, saved_at, comment) VALUES(?,?,?,?)",
                     (rid, defn_json, ts, body.get("comment") or ""))
        conn.commit()
        return True
    finally:
        conn.close()


def delete_report(rid: int) -> bool:
    conn = connect()
    try:
        n = conn.execute("DELETE FROM reports WHERE id=?", (rid,)).rowcount
        conn.execute("DELETE FROM report_versions WHERE report_id=?", (rid,))
        conn.commit()
        return n > 0
    finally:
        conn.close()


def report_versions(rid: int) -> list[dict]:
    conn = connect()
    try:
        return [{"id": r["id"], "saved_at": r["saved_at"], "comment": r["comment"], "definition": normalize_definition(json.loads(r["definition"]))}
                for r in conn.execute("SELECT * FROM report_versions WHERE report_id=? ORDER BY id DESC", (rid,))]
    finally:
        conn.close()


def copy_report(rid: int) -> int | None:
    r = get_report(rid)
    if not r:
        return None
    return create_report(f"{r['name']} (копия)", r["type"], r["folder"], r["definition"], "Копия отчёта")
