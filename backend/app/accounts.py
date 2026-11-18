"""План счетов: загрузка из кэша, названия с переопределением, маски счетов."""
from __future__ import annotations

from . import books, cache, plan, store


def code_matches(code: str, mask: str) -> bool:
    """Маска: `329.01*` — префикс; без звёздочки — точный код и все потомки (`100` не захватывает `1000`);
    `150..157` — диапазон по префиксу кода (включительно). Разбор кода — по профилю плана счетов набора (plan.PlanProfile)."""
    return plan.current().code_matches(code, mask)


def matches_any(code: str, masks: list[str]) -> bool:
    prof = plan.current()
    return any(prof.code_matches(code, m) for m in masks)


def mask_specificity(code: str, mask: str) -> int:
    return len(mask.rstrip("*").split("..")[0])


def firm_for_year(year: int) -> dict | None:
    return cache.year_firm_map().get(year)


_PLAN_SQL = ("SELECT code, name, level, parent, root, is_leaf, specode, cyphcode, center_ctrl, units, full_name, note, created, created_by, inactive, "
             "specode_name FROM accounts WHERE firm=? ORDER BY code")


def _prepare(rows: list, label: int) -> list:
    over = store.name_overrides(label)
    for r in rows:
        if r["code"] in over:
            r["name"] = over[r["code"]]
        r["units"] = r["units"] or ""
        r["is_currency"] = "$" in r["units"]
        fn = (r["full_name"] or "").strip()   # CLDEF; не дублирует название: если совпадает с названием счёта (в т.ч. восстановленным) - null
        r["full_name"] = fn if fn and " ".join(fn.split()).lower() != " ".join((r["name"] or "").split()).lower() else None
        r["note"] = r["note"] or None
        r["inactive"] = bool(r["inactive"])
        r["center_ctrl"] = r["center_ctrl"] or 0
    return rows


def get_plan_srcs(srcs: list, label: int) -> list[dict]:
    """План счетов объединения источников (поздний источник главнее по коду), отсортирован по коду; названия — с переопределением для метки."""
    conn = cache.connect()
    try:
        merged: dict[str, dict] = {}
        for src in srcs:
            for r in conn.execute(_PLAN_SQL, (src,)):
                merged[r["code"]] = dict(r)
    finally:
        conn.close()
    return _prepare([merged[c] for c in sorted(merged)], label)


def get_plan_book(book_id: int) -> list[dict]:
    """План счетов одной книги (ключ книги segment.key)."""
    b = books.get(book_id)
    return get_plan_srcs([b.src], b.label) if b else get_plan(book_id)


def get_plan(year: int) -> list[dict]:
    """План счетов метки года (объединение основных книг метки), отсортирован по коду: [{code, name, level, parent, root, is_leaf, specode, cyphcode,
    center_ctrl, units, is_currency, full_name, note, created, created_by, inactive, specode_name}] (поля — docs/ARCHITECTURE.md, волна 6)."""
    srcs = [b.src for b in books.mains_of_label(year)]
    return get_plan_srcs(srcs, year) if srcs else []


def get_centers(year: int) -> list[dict]:
    srcs = [b.src for b in books.mains_of_label(year)]
    if not srcs:
        return []
    conn = cache.connect()
    try:
        merged: dict[str, str] = {}
        for src in srcs:
            for r in conn.execute("SELECT code, name FROM centers WHERE firm=? ORDER BY code", (src,)):
                merged[r["code"]] = r["name"]
        return [{"code": c, "name": merged[c]} for c in sorted(merged)]
    finally:
        conn.close()
