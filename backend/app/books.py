"""Книги набора: источник Logo (фирма + период) + диапазон дат + метка отчётного года. Чтение/запись таблицы books (settings.db), поиск книг по дате и метке,
сегменты отчёта, проверка разметки. src = firm для периода 01 (так записан существующий кэш), иначе period*1000 + firm."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import store

ROLES = ("main", "alt", "off")


def src_of(firm: int, period: int = 1) -> int:
    return int(firm) if int(period) <= 1 else int(period) * 1000 + int(firm)


def src_parts(src: int) -> Tuple[int, int]:
    """src -> (фирма, период Logo)."""
    return int(src) % 1000, (int(src) // 1000) or 1


def table_prefix(src: int) -> str:
    f, p = src_parts(src)
    return "LG_%03d_%02d_" % (f, p)


def firm_table_prefix(src: int) -> str:
    """Таблицы, общие на фирму (план счетов, центры): LG_<FFF>_."""
    return "LG_%03d_" % src_parts(src)[0]


def d2s(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _d(s) -> date:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


@dataclass
class Book:
    id: int
    slot: int
    label: int
    part: int
    src_firm: int
    src_period: int
    date_from: date
    date_to: date
    role: str
    name: str
    divisor: int
    oem: int
    reason: str = ""
    note: str = ""

    @property
    def src(self) -> int:
        return src_of(self.src_firm, self.src_period)

    def title(self) -> str:
        return "%d · фирма %d%s" % (self.label, self.src_firm, (", период %d" % self.src_period) if self.src_period > 1 else "")

    def to_dict(self) -> dict:
        return {"id": self.id, "slot": self.slot, "label": self.label, "part": self.part, "src": self.src, "src_firm": self.src_firm,
                "src_period": self.src_period, "date_from": self.date_from.isoformat(), "date_to": self.date_to.isoformat(), "role": self.role,
                "name": self.name, "divisor": self.divisor, "oem": bool(self.oem), "reason": self.reason, "note": self.note}


def _row(r) -> Book:
    return Book(r["id"], r["slot"], r["label"], r["part"], r["src_firm"], r["src_period"], _d(r["date_from"]), _d(r["date_to"]), r["role"],
                r["name"] or "", r["divisor"], r["oem"], r["reason"] or "", r["note"] or "")


def all_books(conn: Optional[sqlite3.Connection] = None) -> List[Book]:
    own = conn is None
    conn = conn or store.connect()
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM books ORDER BY date_from, id")]
    finally:
        if own:
            conn.close()


def main_books() -> List[Book]:
    return [b for b in all_books() if b.role == "main"]


def get(book_id: int) -> Optional[Book]:
    for b in all_books():
        if b.id == book_id:
            return b
    return None


def of_src(src: int) -> List[Book]:
    return [b for b in all_books() if b.src == src]


def main_srcs() -> List[int]:
    return sorted({b.src for b in main_books()})


def mains_of_label(label: int) -> List[Book]:
    return sorted([b for b in main_books() if b.label == label], key=lambda b: (b.date_from, b.id))


def book_for_label(label: int) -> Optional[Book]:
    """Последняя по датам основная часть метки."""
    m = mains_of_label(label)
    return m[-1] if m else None


def labels() -> List[int]:
    return sorted({b.label for b in main_books()})


def label_range(label: int) -> Optional[Tuple[date, date]]:
    m = mains_of_label(label)
    return (m[0].date_from, m[-1].date_to) if m else None


def label_of_key(key: int) -> int:
    b = get(key)
    return b.label if b else key


def segments(d1: date, d2: date) -> List[Tuple[Book, date, date]]:
    """Основные книги, пересекающиеся с [d1, d2]: [(книга, с, по)] по возрастанию дат."""
    out = []
    for b in sorted(main_books(), key=lambda b: (b.date_from, b.id)):
        if b.date_from <= d2 and b.date_to >= d1:
            out.append((b, max(d1, b.date_from), min(d2, b.date_to)))
    return out


def gaps(d1: date, d2: date) -> List[Tuple[date, date]]:
    """Участки [d1, d2], не покрытые основными книгами."""
    out, cur = [], d1
    for b, f, t in segments(d1, d2):
        if f > cur:
            out.append((cur, f - timedelta(days=1)))
        cur = max(cur, t + timedelta(days=1))
    if cur <= d2:
        out.append((cur, d2))
    return out


# ---- запись и проверка -------------------------------------------------------------------------------

def _norm(b: dict) -> dict:
    try:
        n = {"id": int(b["id"]) if b.get("id") not in (None, "", 0) else None, "label": int(b["label"]), "part": int(b.get("part") or 1),
             "src_firm": int(b.get("src_firm", b.get("firm"))), "src_period": int(b.get("src_period", b.get("period")) or 1),
             "date_from": str(b["date_from"])[:10], "date_to": str(b["date_to"])[:10], "role": b.get("role") or "main",
             "name": str(b.get("name") or "").strip(), "divisor": int(b.get("divisor") or 1), "oem": 1 if b.get("oem") else 0,
             "reason": str(b.get("reason") or ""), "note": str(b.get("note") or "").strip(),
             "slot": int(b["slot"]) if b.get("slot") not in (None, "") else None}
        _d(n["date_from"])
        _d(n["date_to"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Книга описана неверно: нужны метка года, фирма, период Logo, даты начала и окончания (ГГГГ-ММ-ДД), делитель")
    if n["role"] not in ROLES:
        raise ValueError("Роль книги: main, alt или off")
    if n["divisor"] < 1:
        raise ValueError("Делитель книги должен быть не меньше 1")
    if n["slot"] is None:
        n["slot"] = n["label"]
    return n


def _title(n: dict) -> str:
    return "%d · фирма %d%s" % (n["label"], n["src_firm"], (", период %d" % n["src_period"]) if n["src_period"] > 1 else "")


def validate(items: List[dict], existing_tables=None, denomination=None) -> dict:
    """Проверка разметки: {ok, errors[], warnings[]}. existing_tables — множество имён таблиц Logo (None = не проверять)."""
    errors: List[str] = []
    warns: List[str] = []
    ns = []
    for b in items:
        try:
            ns.append(_norm(b))
        except ValueError as e:
            errors.append(str(e))
    for n in ns:
        if n["date_from"] > n["date_to"]:
            errors.append("Книга «%s»: дата начала позже даты окончания" % _title(n))
    mains = [n for n in ns if n["role"] == "main" and n["date_from"] <= n["date_to"]]
    for i in range(len(mains)):
        for j in range(i + 1, len(mains)):
            a, c = mains[i], mains[j]
            lo, hi = max(a["date_from"], c["date_from"]), min(a["date_to"], c["date_to"])
            if lo <= hi:
                if a["slot"] == c["slot"]:
                    errors.append("Книги «%s» и «%s» пересекаются по датам %s–%s: основной в слоте может быть только одна" % (_title(a), _title(c), d2s(_d(lo)), d2s(_d(hi))))
                else:
                    errors.append("Основные книги «%s» и «%s» пересекаются по датам %s–%s" % (_title(a), _title(c), d2s(_d(lo)), d2s(_d(hi))))
    slots: Dict[int, List[dict]] = {}
    for n in ns:
        slots.setdefault(n["slot"], []).append(n)
    for s, lst in sorted(slots.items()):
        if not any(n["role"] == "main" for n in lst):
            warns.append("В слоте %d нет основной книги: этот период не покрыт" % s)
    div: Dict[Tuple[int, int], int] = {}
    for n in ns:
        if n["role"] == "off":
            continue
        k = (n["src_firm"], n["src_period"])
        if k in div and div[k] != n["divisor"]:
            errors.append("Книги одного периода фирмы %d должны иметь одинаковый делитель (деноминация посреди периода не поддерживается)" % n["src_firm"])
        div.setdefault(k, n["divisor"])
    if existing_tables is not None:
        seen = set()
        for n in ns:
            k = (n["src_firm"], n["src_period"])
            if k in seen or n["role"] == "off":
                continue
            seen.add(k)
            if ("LG_%03d_%02d_EMFLINE" % k) not in existing_tables or ("LG_%03d_%02d_EMFICHE" % k) not in existing_tables:
                errors.append("Книга «%s»: в Logo нет таблиц проводок фирмы %d, период %02d" % (_title(n), n["src_firm"], n["src_period"]))
    if existing_tables is not None and any(t.endswith("_EMUHACC") for t in existing_tables):      # план счетов фирмы нужен для синхронизации
        seenf = set()
        for n in ns:
            if n["role"] == "off" or n["src_firm"] in seenf:
                continue
            seenf.add(n["src_firm"])
            if ("LG_%03d_EMUHACC" % n["src_firm"]) not in existing_tables:
                errors.append("Книга «%s»: в Logo нет плана счетов фирмы %d" % (_title(n), n["src_firm"]))
    for dn in (denomination or []):
        for n in ns:
            if n["role"] != "off" and n["date_from"] < dn["before"] <= n["date_to"]:
                warns.append("Книга «%s» пересекает дату деноминации %s: разделите её по этой дате или задайте один делитель" % (_title(n), d2s(_d(dn["before"]))))
    ms = sorted(mains, key=lambda n: n["date_from"])
    for a, c in zip(ms, ms[1:]):
        gap = (_d(c["date_from"]) - _d(a["date_to"])).days - 1
        if gap > 0:
            warns.append("Между книгами %d и %d нет книги за %s–%s" % (a["label"], c["label"], d2s(_d(a["date_to"]) + timedelta(days=1)), d2s(_d(c["date_from"]) - timedelta(days=1))))
    return {"ok": not errors, "errors": errors, "warnings": warns}


def replace_all(items: List[dict]) -> List[Book]:
    """Полная замена разметки. Идентификаторы: переданные сохраняются, новые >= max(100000, max id + 1)."""
    ns = [_norm(b) for b in items]
    ts = store.now()
    conn = store.connect()
    try:
        have = {r["id"]: r for r in conn.execute("SELECT * FROM books")}
        nxt = max([100000] + [i + 1 for i in have])
        used = set()
        for n in ns:
            if n["id"] is None or n["id"] in used:
                n["id"] = nxt
                nxt += 1
            used.add(n["id"])
        conn.execute("DELETE FROM books")
        for n in ns:
            created = have[n["id"]]["created_at"] if n["id"] in have else ts
            conn.execute("INSERT INTO books(id, slot, label, part, src_firm, src_period, date_from, date_to, role, name, divisor, oem, reason, note, created_at, updated_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (n["id"], n["slot"], n["label"], n["part"], n["src_firm"], n["src_period"], n["date_from"], n["date_to"], n["role"], n["name"],
                          n["divisor"], n["oem"], n["reason"], n["note"], created, ts))
        conn.commit()
    finally:
        conn.close()
    from . import dsctx
    dsctx.current().books_ver += 1
    return all_books()


def make_main(book_id: int) -> dict:
    """Книга становится основной в своём слоте; прежняя основная -> alt. {book, previous: [Book], changed}."""
    b = get(book_id)
    if b is None:
        raise ValueError("Книга не найдена")
    prev = [x for x in all_books() if x.slot == b.slot and x.role == "main" and x.id != b.id]
    changed = b.role != "main" or bool(prev)
    conn = store.connect()
    try:
        for x in prev:
            conn.execute("UPDATE books SET role='alt', updated_at=? WHERE id=?", (store.now(), x.id))
        conn.execute("UPDATE books SET role='main', updated_at=? WHERE id=?", (store.now(), b.id))
        conn.commit()
    finally:
        conn.close()
    from . import dsctx
    dsctx.current().books_ver += 1
    return {"book": get(book_id), "previous": prev, "changed": changed}
