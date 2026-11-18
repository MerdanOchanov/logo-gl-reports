"""Реестр установки data/registry.db: подключения к Logo, наборы данных, пользователи и их права на наборы (этап B). Пароль подключения хранится
зашифрованным (secretbox: DPAPI либо plain1 с предупреждением) и никогда не отдаётся наружу. Первое подключение берётся из окружения/config.ini."""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Optional

from . import config, secretbox

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, server TEXT NOT NULL, database TEXT NOT NULL, login TEXT NOT NULL,
  password_enc TEXT NOT NULL DEFAULT '', driver TEXT NOT NULL DEFAULT '', trust_cert INTEGER NOT NULL DEFAULT 1, timeout_sec INTEGER NOT NULL DEFAULT 10,
  origin TEXT NOT NULL DEFAULT 'ui', last_ok_at TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS datasets(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, org_name TEXT NOT NULL DEFAULT '', connection_id INTEGER NOT NULL REFERENCES connections(id),
  path TEXT NOT NULL DEFAULT '', is_default INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'new',
  note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_datasets_default ON datasets(is_default) WHERE is_default=1;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, login TEXT NOT NULL UNIQUE, pass_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('viewer','editor','admin')));
CREATE TABLE IF NOT EXISTS user_datasets(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE, role TEXT NOT NULL CHECK(role IN ('viewer','editor','admin')),
  PRIMARY KEY(user_id, dataset_id));
"""

ENV_MAP = {"server": "LOGO_SERVER", "database": "LOGO_DB", "login": "LOGO_USER", "password": "LOGO_PASSWORD", "driver": "LOGO_DRIVER"}


def path():
    return config.DATA_DIR / "registry.db"


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    """Идемпотентно: схема, подключение №1 из окружения/config.ini, набор №1 (path='' - файлы в корне data)."""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA user_version=2")
        if conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0] == 0:
            enc = secretbox.protect(config.LOGO_PASSWORD, os.environ.get("SECRET_SCOPE") or "user")
            conn.execute("INSERT INTO connections(name, server, database, login, password_enc, driver, trust_cert, timeout_sec, origin, created_at) "
                         "VALUES(?,?,?,?,?,?,1,10,'config',?)",
                         ("Основное подключение", config.LOGO_SERVER, config.LOGO_DB, config.LOGO_USER, enc, config.LOGO_DRIVER, _now()))
        if conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0:
            from . import legacy_gap
            cid = conn.execute("SELECT id FROM connections ORDER BY id LIMIT 1").fetchone()[0]
            legacy = _looks_legacy()
            conn.execute("INSERT INTO datasets(name, org_name, connection_id, path, is_default, archived, state, created_at, created_by) VALUES(?,?,?,?,1,0,?,?,?)",
                         (legacy_gap.LEGACY_DATASET_NAME if legacy else config.LOGO_DB, "", cid, "", "ready" if legacy else "new", _now(), "migration"))
        conn.commit()
        migrate_legacy_users(conn)
        conn.commit()
    finally:
        conn.close()


def _looks_legacy() -> bool:
    try:
        from . import dataset_migrate
        return bool(dataset_migrate._legacy_firms())
    except Exception:
        return False


def _ensure() -> None:
    if not path().exists():
        init()
        return
    conn = connect()
    try:
        ok = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('datasets','users','user_datasets')").fetchone()[0] == 3
    finally:
        conn.close()
    if not ok:      # реестр этапа A (без users/user_datasets) или пустой файл - достраивается (только добавление)
        init()


# ---- подключения ----------------------------------------------------------------------------------------

def _public(r: sqlite3.Row) -> dict:
    env = []
    if r["origin"] == "config":
        env = [k for k, v in ENV_MAP.items() if os.environ.get(v)]
    return {"id": r["id"], "name": r["name"], "server": r["server"], "database": r["database"], "login": r["login"],
            "has_password": bool(r["password_enc"]) or ("password" in env), "driver": r["driver"], "trust_cert": bool(r["trust_cert"]),
            "timeout_sec": r["timeout_sec"], "origin": r["origin"], "last_ok_at": r["last_ok_at"], "last_error": r["last_error"], "env_fields": env,
            "password_plain": bool(r["password_enc"]) and secretbox.is_plain(r["password_enc"]),
            "warnings": (["Пароль подключения хранится без шифрования (шифрование Windows недоступно)"]
                         if (r["password_enc"] and secretbox.is_plain(r["password_enc"])) else [])}


def list_connections() -> List[dict]:
    _ensure()
    conn = connect()
    try:
        return [_public(r) for r in conn.execute("SELECT * FROM connections ORDER BY id")]
    finally:
        conn.close()


def get_connection(cid: int) -> Optional[dict]:
    _ensure()
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM connections WHERE id=?", (cid,)).fetchone()
        return _public(r) if r else None
    finally:
        conn.close()


def _fields(body: dict, old: Optional[sqlite3.Row] = None) -> dict:
    def pick(k, default=""):
        v = body.get(k)
        return default if v is None else v
    name = str(pick("name", old["name"] if old else "")).strip()
    server = str(pick("server", old["server"] if old else "")).strip()
    database = str(pick("database", old["database"] if old else "")).strip()
    login = str(pick("login", old["login"] if old else "")).strip()
    if not name or not server or not database or not login:
        raise ValueError("Заполните название, сервер, базу и логин подключения")
    return {"name": name, "server": server, "database": database, "login": login, "driver": str(pick("driver", old["driver"] if old else "")).strip(),
            "trust_cert": 1 if body.get("trust_cert", old["trust_cert"] if old else True) else 0,
            "timeout_sec": max(1, min(120, int(body.get("timeout_sec") or (old["timeout_sec"] if old else 10))))}


def create_connection(body: dict) -> dict:
    _ensure()
    f = _fields(body)
    conn = connect()
    try:
        try:
            cur = conn.execute("INSERT INTO connections(name, server, database, login, password_enc, driver, trust_cert, timeout_sec, origin, created_at) "
                               "VALUES(?,?,?,?,?,?,?,?,'ui',?)",
                               (f["name"], f["server"], f["database"], f["login"], secretbox.protect(body.get("password") or ""), f["driver"],
                                f["trust_cert"], f["timeout_sec"], _now()))
        except sqlite3.IntegrityError:
            raise ValueError("Подключение с названием «%s» уже есть" % f["name"])
        conn.commit()
        cid = cur.lastrowid
    finally:
        conn.close()
    return get_connection(cid)


def update_connection(cid: int, body: dict) -> dict:
    _ensure()
    conn = connect()
    try:
        old = conn.execute("SELECT * FROM connections WHERE id=?", (cid,)).fetchone()
        if old is None:
            raise KeyError("Подключение не найдено")
        f = _fields(body, old)
        enc = old["password_enc"]
        if body.get("password") is not None:      # не передан/null -> не менять; "" -> очистить
            enc = secretbox.protect(body["password"])
        origin = old["origin"]
        if origin == "config" and any(body.get(k) not in (None, "") for k in ("server", "database", "login")) and \
                (f["server"], f["database"], f["login"]) != (old["server"], old["database"], old["login"]):
            origin = "ui"
        try:
            conn.execute("UPDATE connections SET name=?, server=?, database=?, login=?, password_enc=?, driver=?, trust_cert=?, timeout_sec=?, origin=?, updated_at=? WHERE id=?",
                         (f["name"], f["server"], f["database"], f["login"], enc, f["driver"], f["trust_cert"], f["timeout_sec"], origin, _now(), cid))
        except sqlite3.IntegrityError:
            raise ValueError("Подключение с названием «%s» уже есть" % f["name"])
        conn.commit()
    finally:
        conn.close()
    return get_connection(cid)


def delete_connection(cid: int) -> None:
    _ensure()
    conn = connect()
    try:
        used = [r["name"] for r in conn.execute("SELECT name FROM datasets WHERE connection_id=?", (cid,))]
        if used:
            raise ValueError("Подключение используется наборами: " + ", ".join(used))
        if conn.execute("DELETE FROM connections WHERE id=?", (cid,)).rowcount == 0:
            raise KeyError("Подключение не найдено")
        conn.commit()
    finally:
        conn.close()


def set_status(cid: int, ok: bool, error: str = "") -> None:
    try:
        conn = connect()
        try:
            if ok:
                conn.execute("UPDATE connections SET last_ok_at=?, last_error=NULL WHERE id=?", (_now(), cid))
            else:
                conn.execute("UPDATE connections SET last_error=? WHERE id=?", (error[:500], cid))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def logo_conn(cid: Optional[int] = None):
    """Параметры подключения (logo.LogoConn) с расшифрованным паролем. cid=None - подключение набора по умолчанию."""
    from . import logo
    try:
        _ensure()
        conn = connect()
        try:
            if cid is None:
                # вне запроса (тесты, скрипты) работает набор №1 (файлы в корне data): его подключение, а не подключение «набора по умолчанию»
                r = conn.execute("SELECT c.* FROM datasets d JOIN connections c ON c.id=d.connection_id ORDER BY (d.path='') DESC, d.id LIMIT 1").fetchone()
            else:
                r = conn.execute("SELECT * FROM connections WHERE id=?", (cid,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        r = None
    if r is None:
        return logo.LogoConn(config.LOGO_SERVER, config.LOGO_DB, config.LOGO_USER, config.LOGO_PASSWORD, config.LOGO_DRIVER)
    server, database, login, driver = r["server"], r["database"], r["login"], r["driver"]
    pwd = secretbox.unprotect(r["password_enc"])
    if r["origin"] == "config":     # переменные окружения главнее сохранённых значений
        server = os.environ.get("LOGO_SERVER") or server
        database = os.environ.get("LOGO_DB") or database
        login = os.environ.get("LOGO_USER") or login
        pwd = os.environ.get("LOGO_PASSWORD") or pwd
        driver = os.environ.get("LOGO_DRIVER") or driver
    return logo.LogoConn(server, database, login, pwd, driver, bool(r["trust_cert"]), int(r["timeout_sec"]), r["name"], r["id"])


# ---- наборы ---------------------------------------------------------------------------------------------

def list_datasets() -> List[dict]:
    _ensure()
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT d.*, c.name AS conn_name, c.server AS conn_server, c.database AS conn_database FROM datasets d JOIN connections c ON c.id=d.connection_id ORDER BY d.id")]
    finally:
        conn.close()


def default_dataset() -> dict:
    ds = list_datasets()
    for d in ds:
        if d["is_default"]:
            return d
    return ds[0]


def root_dataset() -> Optional[dict]:
    """Набор №1: файлы в корне data (path=''); он же рантайм dsctx.default()."""
    for d in list_datasets():
        if not d["path"]:
            return d
    return None


def update_dataset(did: int, body: dict) -> dict:
    _ensure()
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM datasets WHERE id=?", (did,)).fetchone()
        if r is None:
            raise KeyError("Набор данных не найден")
        name = str(body.get("name", r["name"])).strip() or r["name"]
        org = str(body.get("org_name", r["org_name"]) if body.get("org_name") is not None else r["org_name"]).strip()
        cid = int(body.get("connection_id") or r["connection_id"])
        if conn.execute("SELECT 1 FROM connections WHERE id=?", (cid,)).fetchone() is None:
            raise ValueError("Подключение не найдено")
        note = body.get("note", r["note"]) if body.get("note") is not None else r["note"]
        archived = int(bool(body["archived"])) if body.get("archived") is not None else r["archived"]
        will_default = bool(body.get("is_default")) or (bool(r["is_default"]) and body.get("is_default") is not False)
        if archived and will_default:
            raise ValueError("Набор данных по умолчанию нельзя перевести в архив: сначала назначьте набором по умолчанию другой")
        if body.get("is_default") is False and r["is_default"]:
            raise ValueError("Набор по умолчанию должен быть всегда: назначьте набором по умолчанию другой набор")
        try:
            conn.execute("UPDATE datasets SET name=?, org_name=?, connection_id=?, note=?, archived=? WHERE id=?", (name, org, cid, note, archived, did))
        except sqlite3.IntegrityError:
            raise ValueError("Набор данных с названием «%s» уже есть" % name)
        if body.get("is_default"):
            conn.execute("UPDATE datasets SET is_default=0")
            conn.execute("UPDATE datasets SET is_default=1 WHERE id=?", (did,))
        conn.commit()
    finally:
        conn.close()
    return next(d for d in list_datasets() if d["id"] == did)


def set_state(did: int, state: str) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE datasets SET state=? WHERE id=?", (state, did))
        conn.commit()
    finally:
        conn.close()


def get_dataset(did: int) -> Optional[dict]:
    _ensure()
    conn = connect()
    try:
        r = conn.execute("SELECT d.*, c.name AS conn_name, c.server AS conn_server, c.database AS conn_database FROM datasets d "
                         "JOIN connections c ON c.id=d.connection_id WHERE d.id=?", (did,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def create_dataset(name: str, connection_id: int, org_name: str = "", created_by: str = "") -> dict:
    """Строка набора: path='ds/<id>', state='new'. Каталог и файлы создаёт dsmgr."""
    _ensure()
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название набора данных")
    conn = connect()
    try:
        if conn.execute("SELECT 1 FROM connections WHERE id=?", (connection_id,)).fetchone() is None:
            raise ValueError("Подключение не найдено")
        try:
            cur = conn.execute("INSERT INTO datasets(name, org_name, connection_id, path, is_default, archived, state, created_at, created_by) "
                               "VALUES(?,?,?,?,0,0,'new',?,?)", (name, (org_name or "").strip(), connection_id, "pending", _now(), created_by or None))
        except sqlite3.IntegrityError:
            raise ValueError("Набор данных с названием «%s» уже есть" % name)
        did = cur.lastrowid
        conn.execute("UPDATE datasets SET path=? WHERE id=?", ("ds/%d" % did, did))
        conn.commit()
    finally:
        conn.close()
    return get_dataset(did)


def delete_dataset_row(did: int) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM user_datasets WHERE dataset_id=?", (did,))
        conn.execute("DELETE FROM datasets WHERE id=?", (did,))
        conn.commit()
    finally:
        conn.close()


# ---- пользователи и права на наборы (этап B) ----------------------------------------------------------------

ROLES = ("viewer", "editor", "admin")


def migrate_legacy_users(conn: sqlite3.Connection) -> int:
    """Единожды (meta.legacy_users_copied): пользователи settings.db набора №1 копируются сюда с теми же id и хэшами паролей (cookie «session» остаётся
    действительной); не-администраторы получают доступ к набору №1 с прежней ролью. Перед копированием - резервная копия settings.db в data/backup/.
    Таблица users в settings.db не изменяется (откат на прежнюю версию)."""
    if conn.execute("SELECT 1 FROM meta WHERE key='legacy_users_copied'").fetchone():
        return 0
    from . import dsctx, store
    src = dsctx.default().settings_path or store.DB_PATH
    n = 0
    try:
        if src.exists():
            old = sqlite3.connect("file:%s?mode=ro" % src.as_posix(), uri=True, timeout=30)
            try:
                old.row_factory = sqlite3.Row
                if old.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
                    rows = [dict(r) for r in old.execute("SELECT id, login, pass_hash, role FROM users WHERE login IS NOT NULL AND pass_hash IS NOT NULL")]
                else:
                    rows = []
                if rows:
                    import time as _t
                    bk = config.DATA_DIR / "backup"
                    bk.mkdir(parents=True, exist_ok=True)
                    dst = sqlite3.connect(bk / ("settings-before-users-%s.db" % _t.strftime("%Y%m%d-%H%M%S")))
                    try:
                        old.backup(dst)
                    finally:
                        dst.close()
            finally:
                old.close()
            ds1 = conn.execute("SELECT id FROM datasets WHERE path='' ORDER BY id LIMIT 1").fetchone()
            for r in rows:
                role = r["role"] if r["role"] in ROLES else "viewer"
                cur = conn.execute("INSERT OR IGNORE INTO users(id, login, pass_hash, role) VALUES(?,?,?,?)", (r["id"], r["login"], r["pass_hash"], role))
                n += cur.rowcount
                if role != "admin" and ds1 and cur.rowcount:
                    conn.execute("INSERT OR IGNORE INTO user_datasets(user_id, dataset_id, role) VALUES(?,?,?)", (r["id"], ds1[0], role))
    except sqlite3.Error:
        return 0       # не помечаем: повторим при следующем запуске
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('legacy_users_copied', ?)", (str(n),))
    return n


def _user_row(r) -> Optional[dict]:
    return dict(r) if r else None


def get_user(uid: int) -> Optional[dict]:
    _ensure()
    conn = connect()
    try:
        return _user_row(conn.execute("SELECT id, login, pass_hash, role FROM users WHERE id=?", (uid,)).fetchone())
    finally:
        conn.close()


def get_user_by_login(login: str) -> Optional[dict]:
    _ensure()
    conn = connect()
    try:
        return _user_row(conn.execute("SELECT id, login, pass_hash, role FROM users WHERE login=?", (login,)).fetchone())
    finally:
        conn.close()


def list_users() -> List[dict]:
    _ensure()
    conn = connect()
    try:
        grants: Dict[int, list] = {}
        for g in conn.execute("SELECT u.user_id, u.dataset_id, u.role, d.name FROM user_datasets u LEFT JOIN datasets d ON d.id=u.dataset_id ORDER BY u.dataset_id"):
            grants.setdefault(g["user_id"], []).append({"id": g["dataset_id"], "dataset_id": g["dataset_id"], "name": g["name"] or "", "role": g["role"]})
        return [{"id": r["id"], "login": r["login"], "role": r["role"], "datasets": grants.get(r["id"], [])}
                for r in conn.execute("SELECT id, login, role FROM users ORDER BY login")]
    finally:
        conn.close()


def insert_user(login: str, pass_hash: str, role: str) -> int:
    """sqlite3.IntegrityError, если логин занят."""
    _ensure()
    conn = connect()
    try:
        cur = conn.execute("INSERT INTO users(login, pass_hash, role) VALUES(?,?,?)", (login, pass_hash, role))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_user(uid: int, role: str, pass_hash: str) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE users SET role=?, pass_hash=? WHERE id=?", (role, pass_hash, uid))
        conn.commit()
    finally:
        conn.close()


def delete_user(uid: int) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM user_datasets WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
    finally:
        conn.close()


def count_users() -> int:
    _ensure()
    conn = connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def count_admins() -> int:
    conn = connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
    finally:
        conn.close()


def user_grants(uid: int) -> Dict[int, str]:
    """{набор: роль} из user_datasets (для администратора не используется - ему доступны все)."""
    _ensure()
    conn = connect()
    try:
        return {r["dataset_id"]: r["role"] for r in conn.execute("SELECT dataset_id, role FROM user_datasets WHERE user_id=?", (uid,))}
    finally:
        conn.close()


def set_user_grants(uid: int, items: List[dict]) -> List[dict]:
    """Полная замена прав пользователя: [{dataset_id, role}]; пустой список - доступа нет. ValueError - по-русски."""
    _ensure()
    conn = connect()
    try:
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM datasets")}
        have = set(names)
        clean: Dict[int, str] = {}
        for it in items:
            if not isinstance(it, dict):
                raise ValueError("Права заданы неверно: ожидается список, где у каждой записи указан набор и роль")
            try:
                did = int(it.get("dataset_id", it.get("id")))
            except (TypeError, ValueError):
                raise ValueError("Права заданы неверно: у каждой записи нужно указать набор данных")
            if did not in have:
                raise ValueError("Набор данных №%d не найден" % did)
            if it.get("role") not in ROLES:
                raise ValueError("Роль в наборе данных: просмотр, редактор или администратор")
            clean[did] = it["role"]
        conn.execute("DELETE FROM user_datasets WHERE user_id=?", (uid,))
        for did, role in clean.items():
            conn.execute("INSERT INTO user_datasets(user_id, dataset_id, role) VALUES(?,?,?)", (uid, did, role))
        conn.commit()
    finally:
        conn.close()
    return [{"dataset_id": d, "name": names.get(d, ""), "role": r} for d, r in sorted(clean.items())]


def grant_default(uid: int, role: str) -> None:
    """Совместимость со старым API: пользователь без явных прав получает доступ к набору по умолчанию с указанной ролью."""
    conn = connect()
    try:
        r = conn.execute("SELECT id FROM datasets WHERE is_default=1 AND archived=0 LIMIT 1").fetchone() or conn.execute("SELECT id FROM datasets ORDER BY id LIMIT 1").fetchone()
        if r:
            conn.execute("INSERT OR REPLACE INTO user_datasets(user_id, dataset_id, role) VALUES(?,?,?)", (uid, r["id"], role))
            conn.commit()
    finally:
        conn.close()
