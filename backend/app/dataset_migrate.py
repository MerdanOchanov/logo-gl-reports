"""Миграция данных набора №1 (существующая установка с прежней картой «год → фирма») в таблицы книг и параметров. Идемпотентна: выполняется, только если книг ещё нет
и в кэше/настройках есть следы прежней установки (cache.firms, year_books). Для чистой установки ничего не делает (разметку создаёт мастер).
Откат: удалить таблицы books/dataset_params/scan_cache (или вернуть резервную копию settings.db из data/backup/); прежняя версия их не читает."""
from __future__ import annotations

import json
import sqlite3
from typing import Dict, List

from . import legacy_gap as L
from . import store


def _legacy_firms() -> List[dict]:
    """Строки прежней таблицы cache.firms (читаются напрямую, только чтение); [] если кэша нет."""
    from . import cache, dsctx
    path = dsctx.current().cache_path or cache.DB_PATH
    try:
        if not path.exists():
            return []
        c = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=30)
    except Exception:
        return []
    try:
        c.row_factory = sqlite3.Row
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='firms'").fetchone() is None:
            return []
        return [dict(r) for r in c.execute("SELECT firm, year, name, enabled, divisor, synced_at FROM firms")]
    except Exception:
        return []
    finally:
        c.close()


def run(conn: sqlite3.Connection) -> bool:
    if conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] > 0:
        return False
    if conn.execute("SELECT 1 FROM dataset_params WHERE key='plan_profile'").fetchone() is not None:
        return False
    firms: Dict[int, dict] = {r["firm"]: r for r in _legacy_firms()}
    chosen = {r[0]: r[1] for r in conn.execute("SELECT year, firm FROM year_books")}
    known = set(L.LEGACY_YEAR_FIRM.values()) | {f for fs in L.LEGACY_ALT_YEAR_FIRM.values() for f in fs}
    if not chosen and not (set(firms) & known):
        return False
    ts = store.now()
    for y in sorted(L.LEGACY_YEAR_FIRM):
        default = L.LEGACY_YEAR_FIRM[y]
        alts = [f for f in L.LEGACY_ALT_YEAR_FIRM.get(y, []) if f != default]
        members = [f for f in [default] + alts if f in firms or f == default]
        main = chosen.get(y) if chosen.get(y) in members else None
        if main is None:
            en = [f for f in members if f in firms and firms[f]["enabled"]]
            main = default if default in en or not en else en[0]
        for f in members:
            fr = firms.get(f) or {}
            div = fr.get("divisor") or 1
            conn.execute("INSERT INTO books(id, slot, label, part, src_firm, src_period, date_from, date_to, role, name, divisor, oem, reason, created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (y if f == default else 10000 + f, y, y, 1, f, 1, "%04d-01-01" % y, "%04d-12-31" % y, "main" if f == main else "alt",
                          fr.get("name") or "", div, 1 if y < L.LEGACY_OEM_BEFORE_YEAR else 0,
                          "Перенесено из прежней настройки (карта «год → фирма»)", ts))
    pr = dict(L.LEGACY_PARAMS)
    pr["plan_profile"] = {"template": "turkish", "mode": "sep", "sep": ".", "root_len": 3, "widths": [], "balance_roots": ["1", "2", "3", "4", "5"],
                          "pnl_roots": ["6", "7"], "noncarried": ["6*", "7*", "590"]}
    for k, v in pr.items():
        conn.execute("INSERT OR REPLACE INTO dataset_params(key, value) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
    if conn.execute("SELECT COUNT(*) FROM closing_rules").fetchone()[0] == 0:
        conn.execute("INSERT INTO closing_rules(kind, value, enabled) VALUES('accounts', ?, 1)", (L.TURKISH_CLOSING_ACCOUNTS,))
    return True
