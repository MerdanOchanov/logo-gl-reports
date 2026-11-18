"""Многолетние отчёты: сопоставление кодов счетов между годами (account_links), автоподбор связей,
приведение кода к коду целевого года (CodeMapper) и отчёт сравнения лет type="compare_years".

Таблица связей (settings.db): account_links(code_from, year_from, code_to, year_to, method, confidence, note).
Связь направленная («счёт code_from плана year_from = счёт code_to плана year_to»), но используется в обе стороны."""
from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import date

from . import accounts as acc_mod
from . import books, cache, plan, store
from .engine import common as C
from .engine.common import Context, ReportError

AUTO_MIN_CONFIDENCE = 0.7


class LinkError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------
# нормализация названий
# ---------------------------------------------------------------------------------------------

_TR = str.maketrans({"İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g", "Ü": "u", "ü": "u", "Ö": "o", "ö": "o",
                     "Ç": "c", "ç": "c", "Ä": "a", "ä": "a", "Ž": "z", "ž": "z", "Ň": "n", "ň": "n", "Ý": "y", "ý": "y"})


_ABBR = {"konf": "konfeksiyon", "malz": "malzeme", "isl": "isletme", "gid": "gider", "yans": "yansitma", "hes": "hesabi", "fab": "fabrikasi",
         "tah": "tahakkuk", "urt": "uretim", "ilgili": "", "imal": "imalat"}
_NOISE = {"ltd", "limited", "kont", "kontrat", "co", "inc", "ve", "ic", "dis", "kdv", "disi", "no"}
_STEMS = ("stok", "stog", "sarf", "satis", "mamul", "gider", "alacak", "borc", "kredi", "vergi", "avans", "iscilik", "amortis", "yansit")


def norm_name(s: str | None) -> str:
    """Название для сравнения: без диакритики и регистра, без пунктуации; «?» (утраченные символы) выбрасываются;
    раскрыты частые сокращения («Y.MAMULU» → «yari mamulu»), отброшены шумовые слова (ltd, kont…), слова сведены к основам (stoku/stoklari → stok)."""
    t = (s or "").translate(_TR)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    t = t.replace("?", "")
    t = re.sub(r"[^0-9a-zа-я]+", " ", t)
    toks = t.split()
    out: list[str] = []
    for i, w in enumerate(toks):
        if w == "y" and i + 1 < len(toks) and toks[i + 1].startswith("mamul"):
            w = "yari"
        w = _ABBR.get(w, w)
        for st in _STEMS:
            if w.startswith(st):
                w = {"stog": "stok"}.get(st, st)
                break
        if w and w not in _NOISE:
            out.append(w)
    return " ".join(out)


# ---------------------------------------------------------------------------------------------
# CRUD связей
# ---------------------------------------------------------------------------------------------

def _row(r) -> dict:
    return {"id": r["id"], "code_from": r["code_from"], "year_from": r["year_from"], "code_to": r["code_to"], "year_to": r["year_to"],
            "method": r["method"], "confidence": r["confidence"], "note": r["note"] or ""}


def list_links(year_from: int | None = None, year_to: int | None = None, q: str = "", limit: int = 500, offset: int = 0) -> dict:
    where, params = [], []
    if year_from is not None:
        where.append("year_from=?")
        params.append(year_from)
    if year_to is not None:
        where.append("year_to=?")
        params.append(year_to)
    if q:
        where.append("(code_from LIKE ? OR code_to LIKE ?)")
        params += [f"{q}%", f"{q}%"]
    w = (" WHERE " + " AND ".join(where)) if where else ""
    conn = store.connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM account_links" + w, params).fetchone()[0]
        rows = conn.execute("SELECT * FROM account_links" + w + " ORDER BY year_from, year_to, code_from LIMIT ? OFFSET ?",
                            [*params, int(limit), int(offset)]).fetchall()
        return {"total": total, "items": [_row(r) for r in rows]}
    finally:
        conn.close()


def _check_item(it: dict) -> tuple[str, int, str, int]:
    try:
        yf, yt = int(it.get("year_from")), int(it.get("year_to"))
    except (TypeError, ValueError):
        raise LinkError("Укажите год, из которого переносятся счета, и год, в который они переносятся (целые числа)")
    cf, ct = str(it.get("code_from") or "").strip(), str(it.get("code_to") or "").strip()
    if not cf or not ct:
        raise LinkError("Укажите коды счетов в обоих годах")
    if yf == yt:
        raise LinkError("Связь строится между разными годами")
    for code, y in ((cf, yf), (ct, yt)):
        plan = acc_mod.get_plan(y)
        if plan and code not in {r["code"] for r in plan}:
            raise LinkError(f"Счёт {code} не найден в плане счетов {y} года")
    return cf, yf, ct, yt


def upsert_links(items: list[dict]) -> list[dict]:
    out = []
    conn = store.connect()
    try:
        for it in items:
            cf, yf, ct, yt = _check_item(it)
            conn.execute("DELETE FROM account_links WHERE code_from=? AND year_from=? AND year_to=?", (cf, yf, yt))
            cur = conn.execute("INSERT INTO account_links(code_from, year_from, code_to, year_to, method, confidence, note) VALUES(?,?,?,?,?,?,?)",
                               (cf, yf, ct, yt, "manual", 1.0, str(it.get("note") or "")))
            out.append(_row(conn.execute("SELECT * FROM account_links WHERE id=?", (cur.lastrowid,)).fetchone()))
        conn.commit()
    finally:
        conn.close()
    return out


def replace_links(items: list[dict], year_from: int | None = None, year_to: int | None = None) -> list[dict]:
    """Полная замена: удаляются все связи затронутых пар лет, затем записываются items (как ручные, если method не указан)."""
    if not isinstance(items, list):
        raise LinkError("Ожидается список связей")
    pairs = {(year_from, year_to)} if (year_from is not None and year_to is not None) else set()
    checked = []
    for it in items:
        cf, yf, ct, yt = _check_item(it)
        if pairs and (yf, yt) not in pairs:
            raise LinkError(f"Связь {cf}→{ct} относится к паре лет {yf}→{yt}, а заменяется пара {year_from}→{year_to}")
        pairs.add((yf, yt))
        checked.append((cf, yf, ct, yt, it))
    conn = store.connect()
    try:
        for yf, yt in pairs:
            conn.execute("DELETE FROM account_links WHERE year_from=? AND year_to=?", (yf, yt))
        for cf, yf, ct, yt, it in checked:
            method = str(it.get("method") or it.get("kind") or "manual")
            conn.execute("INSERT OR REPLACE INTO account_links(code_from, year_from, code_to, year_to, method, confidence, note) VALUES(?,?,?,?,?,?,?)",
                         (cf, yf, ct, yt, method, it.get("confidence", 1.0 if method == "manual" else None), str(it.get("note") or "")))
        conn.commit()
    finally:
        conn.close()
    r = list_links(limit=100000)["items"]
    return [x for x in r if (x["year_from"], x["year_to"]) in pairs]


def update_link(lid: int, body: dict) -> dict | None:
    conn = store.connect()
    try:
        r = conn.execute("SELECT * FROM account_links WHERE id=?", (lid,)).fetchone()
        if not r:
            return None
        item = {"code_from": body.get("code_from", r["code_from"]), "year_from": body.get("year_from", r["year_from"]),
                "code_to": body.get("code_to", r["code_to"]), "year_to": body.get("year_to", r["year_to"])}
        cf, yf, ct, yt = _check_item(item)
        conn.execute("UPDATE account_links SET code_from=?, year_from=?, code_to=?, year_to=?, method='manual', confidence=1.0, note=? WHERE id=?",
                     (cf, yf, ct, yt, str(body.get("note", r["note"]) or ""), lid))
        conn.commit()
        return _row(conn.execute("SELECT * FROM account_links WHERE id=?", (lid,)).fetchone())
    except Exception as e:
        if "UNIQUE" in str(e):
            raise LinkError("Для этого счёта и пары лет связь уже есть")
        raise
    finally:
        conn.close()


def delete_link(lid: int) -> bool:
    conn = store.connect()
    try:
        n = conn.execute("DELETE FROM account_links WHERE id=?", (lid,)).rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def delete_links(year_from: int, year_to: int, method: str | None = None) -> int:
    conn = store.connect()
    try:
        if method:
            n = conn.execute("DELETE FROM account_links WHERE year_from=? AND year_to=? AND method=?", (year_from, year_to, method)).rowcount
        else:
            n = conn.execute("DELETE FROM account_links WHERE year_from=? AND year_to=?", (year_from, year_to)).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------
# автоподбор
# ---------------------------------------------------------------------------------------------

def _root(code: str) -> str:
    return plan.current().root_of(code)


def _turnover(year: int) -> dict[str, int]:
    """код → оборот Дт+Кт (копейки, до деноминации) не-açılış за год: для долей в отчёте."""
    fm = acc_mod.firm_for_year(year)
    if not fm:
        return {}
    conn = cache.connect()
    try:
        return {r[0]: r[1] or 0 for r in conn.execute(
            "SELECT account, SUM(dt+kt) FROM lines WHERE firm=? AND is_opening=0 GROUP BY account", (fm["firm"],))}
    finally:
        conn.close()


def auto_match(year_from: int, year_to: int, min_confidence: float = AUTO_MIN_CONFIDENCE, list_limit: int = 300) -> dict:
    """Предложения связей year_from → year_to по совпадению кода+названия и по названию.
    Возвращает отчёт {summary, proposals (спорные и сопоставленные — выборка), ...}; в БД ничего не пишет."""
    if year_from == year_to:
        raise LinkError("Годы должны различаться")
    A, B = acc_mod.get_plan(year_from), acc_mod.get_plan(year_to)
    if not A:
        raise LinkError(f"Нет плана счетов за {year_from} год (фирма не загружена)")
    if not B:
        raise LinkError(f"Нет плана счетов за {year_to} год (фирма не загружена)")
    Bd = {r["code"]: r for r in B}
    Ad = {r["code"]: r for r in A}
    b_norm = {c: norm_name(r["name"]) for c, r in Bd.items()}
    by_name: dict[str, list[str]] = {}
    for c, n in b_norm.items():
        if n:
            by_name.setdefault(n, []).append(c)
    by_root: dict[str, list[str]] = {}
    for c in Bd:
        by_root.setdefault(_root(c), []).append(c)

    def parent_name(plan: dict, code: str) -> str:
        p = plan[code].get("parent")
        return norm_name(plan[p]["name"]) if p and p in plan else ""

    tov = _turnover(year_from)
    results: list[dict] = []
    for a in A:
        ca, na = a["code"], norm_name(a["name"])
        rec = {"code_from": ca, "name_from": a["name"], "is_leaf": bool(a["is_leaf"]), "turnover": tov.get(ca, 0),
               "code_to": None, "name_to": None, "method": None, "confidence": 0.0, "status": "unmatched", "candidates": []}
        results.append(rec)
        if not na:
            rec["reason"] = "нет названия"
            continue
        same = Bd.get(ca)
        if same is not None and b_norm[ca] == na:
            rec.update(code_to=ca, name_to=same["name"], method="code+name", confidence=1.0, status="matched")
            continue
        # кандидаты по названию
        cands = by_name.get(na, [])
        if cands:
            scored = []
            for cb in cands:
                s = 0.6
                if _root(cb) == _root(ca):
                    s += 0.2
                if Bd[cb]["level"] == a["level"]:
                    s += 0.1
                pa, pb = parent_name(Ad, ca), parent_name(Bd, cb)
                if pa and pa == pb:
                    s += 0.1
                if cb == ca:
                    s += 0.05
                scored.append((round(s, 3), cb))
            scored.sort(key=lambda x: (-x[0], x[1]))
            best = scored[0]
            if len(scored) == 1 or best[0] - scored[1][0] >= 0.05:
                rec.update(code_to=best[1], name_to=Bd[best[1]]["name"], method="name", confidence=min(best[0], 0.95),
                           status="matched" if best[0] >= min_confidence else "disputed")
                if rec["status"] == "disputed":
                    rec["reason"] = "название совпало, но корень/уровень счёта другие"
                    rec["candidates"] = [{"code": c, "name": Bd[c]["name"], "score": s} for s, c in scored[:5]]
                continue
            rec.update(status="disputed", method="name", confidence=best[0], reason="несколько счетов с таким названием",
                       candidates=[{"code": c, "name": Bd[c]["name"], "score": s} for s, c in scored[:5]])
            continue
        # тот же код, другое название
        if same is not None:
            ratio = difflib.SequenceMatcher(None, na, b_norm[ca]).ratio() if b_norm[ca] else 0.0
            if ratio >= 0.8:
                rec.update(code_to=ca, name_to=same["name"], method="code+similar", confidence=round(0.7 + 0.2 * ratio, 3), status="matched")
            else:
                rec.update(code_to=ca, name_to=same["name"], method="code", confidence=0.3, status="disputed",
                           reason="код совпал, названия различаются (код мог быть переназначен)",
                           candidates=[{"code": ca, "name": same["name"], "score": round(ratio, 3)}])
            if rec["status"] == "matched" or rec["candidates"]:
                continue
        # похожее название в том же корне (лист к листу, группа к группе)
        pool = [c for c in by_root.get(_root(ca), []) if b_norm[c] and Bd[c]["is_leaf"] == a["is_leaf"]]
        best_c, best_r, second = None, 0.0, 0.0
        sm = difflib.SequenceMatcher()
        sm.set_seq2(na)
        for cb in pool:
            nb = b_norm[cb]
            if abs(len(nb) - len(na)) > max(6, len(na) // 4):
                continue
            sm.set_seq1(nb)
            if sm.real_quick_ratio() < 0.85 or sm.quick_ratio() < 0.85:
                continue
            r = sm.ratio()
            if r > best_r:
                best_c, best_r, second = cb, r, best_r
            elif r > second:
                second = r
        if best_c and best_r >= 0.88 and best_r - second >= 0.03:
            rec.update(code_to=best_c, name_to=Bd[best_c]["name"], method="name~", confidence=round(best_r * 0.85, 3),
                       status="matched" if best_r * 0.85 >= min_confidence else "disputed")
            if rec["status"] == "disputed":
                rec["candidates"] = [{"code": best_c, "name": Bd[best_c]["name"], "score": round(best_r, 3)}]
        elif rec["status"] == "unmatched":
            rec["reason"] = "нет счёта с таким названием в целевом плане"

    # сводка
    def agg(rs: list[dict]) -> dict:
        n = len(rs)
        t = sum(r["turnover"] for r in rs)
        out = {"total": n}
        for st in ("matched", "disputed", "unmatched"):
            sub = [r for r in rs if r["status"] == st]
            out[st] = len(sub)
            out[st + "_turnover_share"] = round(sum(r["turnover"] for r in sub) / t, 4) if t else None
        return out

    leaves = [r for r in results if r["is_leaf"]]
    with_turn = [r for r in leaves if r["turnover"]]
    methods: dict[str, int] = {}
    for r in results:
        if r["status"] == "matched":
            methods[r["method"]] = methods.get(r["method"], 0) + 1
    same_code = sum(1 for a in A if a["code"] in Bd)
    summary = {"year_from": year_from, "year_to": year_to, "accounts_from": len(A), "accounts_to": len(B),
               "codes_in_both": same_code, "codes_differ": len(A) - same_code, "all": agg(results), "leaves": agg(leaves),
               "leaves_with_turnover": agg(with_turn), "by_method": methods, "min_confidence": min_confidence}
    order = {"disputed": 0, "unmatched": 1, "matched": 2}

    def pub(r):
        return {k: v for k, v in r.items() if k != "is_leaf"} | {"leaf": r["is_leaf"]}

    interesting = sorted((r for r in results if r["status"] != "matched" and r["turnover"]), key=lambda r: (order[r["status"]], -r["turnover"]))
    disputed = [pub(r) for r in interesting if r["status"] == "disputed"][:list_limit]
    unmatched = [pub(r) for r in interesting if r["status"] == "unmatched"][:list_limit]
    sample = [pub(r) for r in results if r["status"] == "matched" and r["method"] != "code+name"][:list_limit]
    return {"summary": summary, "disputed": disputed, "unmatched": unmatched, "matched_sample": sample,
            "_results": results}


def public_report(rep: dict) -> dict:
    return {k: v for k, v in rep.items() if not k.startswith("_")}


def apply_auto(year_from: int, year_to: int, min_confidence: float = AUTO_MIN_CONFIDENCE, replace: bool = True) -> dict:
    """Записывает уверенные предложения (status=matched); ручные связи (method=manual) не затрагиваются."""
    rep = auto_match(year_from, year_to, min_confidence)
    conn = store.connect()
    try:
        if replace:
            conn.execute("DELETE FROM account_links WHERE year_from=? AND year_to=? AND method<>'manual'", (year_from, year_to))
        manual = {r[0] for r in conn.execute("SELECT code_from FROM account_links WHERE year_from=? AND year_to=?", (year_from, year_to))}
        n = 0
        for r in rep["_results"]:
            if r["status"] != "matched" or r["code_from"] in manual or r["confidence"] < min_confidence:
                continue
            conn.execute("INSERT OR REPLACE INTO account_links(code_from, year_from, code_to, year_to, method, confidence, note) VALUES(?,?,?,?,?,?,?)",
                         (r["code_from"], year_from, r["code_to"], year_to, "auto:" + r["method"], r["confidence"], ""))
            n += 1
        conn.commit()
    finally:
        conn.close()
    out = public_report(rep)
    out["applied"] = n
    return out


# ---------------------------------------------------------------------------------------------
# приведение кода к целевому году (интерфейс для движков)
# ---------------------------------------------------------------------------------------------

class CodeMapper:
    """Приводит код счёта года `year` к коду плана `target_year`.

        m = CodeMapper(target_year)               # один раз на отчёт (читает account_links)
        m.map(code, year) -> str                  # код в плане target_year; нет связи — код без изменений
        m.map_ex(code, year) -> (code, how)       # how: "same" | "link" | "chain" | "identity"

    Правила: year == target → "same". Есть прямая связь (year→target или обратная target→year) → "link". Иначе цепочка через
    соседние годы по связям (y→y±1; на шаге без связи код остаётся прежним) → "chain" (хоть раз была связь) или "identity"
    (связей нет совсем — код считается неизменным; движок может предупредить, что названия могут различаться)."""

    def __init__(self, target_year: int):
        self.target = int(target_year)
        self.fwd: dict[tuple[int, int], dict[str, str]] = {}
        conn = store.connect()
        try:
            for r in conn.execute("SELECT code_from, year_from, code_to, year_to FROM account_links ORDER BY id"):
                self.fwd.setdefault((r["year_from"], r["year_to"]), {})[r["code_from"]] = r["code_to"]
        finally:
            conn.close()
        self.rev: dict[tuple[int, int], dict[str, str]] = {}
        for (yf, yt), m in self.fwd.items():
            d = self.rev.setdefault((yt, yf), {})
            for cf, ct in m.items():
                d.setdefault(ct, cf)

    def _step(self, code: str, y_from: int, y_to: int) -> str | None:
        m = self.fwd.get((y_from, y_to))
        if m and code in m:
            return m[code]
        m = self.rev.get((y_from, y_to))
        if m and code in m:
            return m[code]
        return None

    def map_ex(self, code: str, year: int) -> tuple[str, str]:
        if year == self.target:
            return code, "same"
        hit = self._step(code, year, self.target)
        if hit is not None:
            return hit, "link"
        y, cur, used = year, code, False
        direction = 1 if self.target > year else -1
        while y != self.target:
            nxt = self._step(cur, y, y + direction)
            if nxt is not None:
                cur, used = nxt, True
            y += direction
        return cur, ("chain" if used else "identity")

    def map(self, code: str, year: int) -> str:
        return self.map_ex(code, year)[0]


def map_code(code: str, year: int, target_year: int) -> str:
    """Одноразовое приведение (читает связи при каждом вызове; для массовых — CodeMapper)."""
    return CodeMapper(target_year).map(code, year)


# ---------------------------------------------------------------------------------------------
# отчёт «Сравнение лет»
# ---------------------------------------------------------------------------------------------

MEASURES = {"net_turn": "Чистый оборот (Дт − Кт)", "open": "Сальдо на начало (Дт − Кт)", "turn_dt": "Оборот Дт", "turn_kt": "Оборот Кт", "close": "Сальдо на конец (Дт − Кт)",
            "close_dt": "Сальдо на конец, Дт", "close_kt": "Сальдо на конец, Кт"}
# вектор года: tdt, tkt, open, close, close_pos, close_neg
_BLK = 6


def _mmdd(s: str, what: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", str(s or ""))
    if not m:
        raise ReportError(f"{what}: ожидается формат ММ-ДД, например 01-01")
    mo, dd = int(m.group(1)), int(m.group(2))
    try:
        date(2001, mo, dd)
    except ValueError:
        raise ReportError(f"{what}: некорректная дата «{s}»")
    return mo, dd


def _year_dates(y: int, m1: tuple[int, int], m2: tuple[int, int]) -> tuple[date, date]:
    rng = books.label_range(y)
    if rng and m1 == (1, 1) and m2 == (12, 31):
        return rng       # весь период метки (для календарного года - 01.01..31.12)
    if rng and (rng[0], rng[1]) != (date(y, 1, 1), date(y, 12, 31)):
        raise ReportError(f"Для года {y} ({C.fmt_date(rng[0])}–{C.fmt_date(rng[1])}) частичный период не поддерживается: оставьте весь год")
    def mk(mo, dd):
        try:
            return date(y, mo, dd)
        except ValueError:      # 29 февраля
            return date(y, mo, 28)
    return mk(*m1), mk(*m2)


def run_compare(defn: dict) -> dict:
    defn = dict(defn)
    blk = defn.get("compare")
    delta = bool(defn.get("delta", False))
    if isinstance(blk, dict):          # форма, которую присылает интерфейс: compare: {years, metric | measures, ...}
        defn["years"] = blk.get("years", defn.get("years"))
        for k in ("target_year", "mmdd_from", "mmdd_to", "use_links"):
            if k in blk:
                defn[k] = blk[k]
        if blk.get("measures"):
            defn["measures"] = blk["measures"]
        elif blk.get("metric"):
            defn["measures"] = [blk["metric"]]
        delta = bool(blk.get("delta", True))
    years = defn.get("years")
    if not isinstance(years, list) or len(years) < 2:
        raise ReportError("Для сравнения лет укажите не менее двух годов, например 2024 и 2025")
    try:
        years = sorted({int(y) for y in years})
    except (TypeError, ValueError):
        raise ReportError("Годы сравнения должны быть целыми числами")
    if len(years) > 10:
        raise ReportError("Сравнивать можно не более 10 лет")
    target = int(defn.get("target_year") or years[-1])
    if target not in years:
        raise ReportError("Год, к плану которого приводятся счета, должен входить в список сравниваемых лет")
    m1, m2 = _mmdd(defn.get("mmdd_from", "01-01"), "Начало части года"), _mmdd(defn.get("mmdd_to", "12-31"), "Конец части года")
    if m1 > m2:
        raise ReportError("Часть года задана неверно: начало (%02d.%02d) позже конца (%02d.%02d). Переход через новый год в сравнении лет не поддерживается — "
                          "задайте начало не позже конца" % (m1[1], m1[0], m2[1], m2[0]))
    measures = defn.get("measures") or ["turn_dt", "turn_kt", "close"]
    for m in measures:
        if m not in MEASURES:
            raise ReportError(f"Неизвестный показатель «{m}»: допустимы " + ", ".join("«%s»" % v for v in MEASURES.values()))
    use_links = bool(defn.get("use_links", True))

    base = dict(defn)
    base["period"] = {"date_from": f"{min(years)}-01-01", "date_to": f"{max(years)}-12-31"}
    d = C.normalize(base)
    ctx_t = None
    warnings: list[str] = []
    usd = bool(d.get("_report"))

    # --- листья по годам ---
    per_year: dict[int, dict] = {}
    for y in years:
        d1, d2 = _year_dates(y, m1, m2)
        dy = dict(d)
        dy["_d1"], dy["_d2"] = d1, d2
        try:
            cy = Context(dy)
        except ReportError as e:
            raise ReportError(f"Год {y}: {e}")
        warnings += [f"{y}: {w}" for w in cy.warnings]
        per_year[y] = {"ctx": cy, "leaves": C.aggregate_leaves(cy), "d1": d1, "d2": d2}
        if y == target:
            ctx_t = cy
    dt_ = dict(d)
    dt_["_d1"], dt_["_d2"] = per_year[target]["d1"], per_year[target]["d2"]
    ctx_t = Context(dt_, sync=False)

    mapper = CodeMapper(target) if use_links else None
    tplan = ctx_t.plan(ctx_t.key_of(target))
    tnames = {c: norm_name(r.get("name")) for c, r in tplan.items()}

    # --- приведение кодов и слияние ---
    merged: dict[tuple[int, str], dict[int, list[float]]] = {}
    raw_tot: dict[int, list[float]] = {y: [0.0, 0.0, 0.0, 0.0] for y in years}
    stat = {"linked": 0, "identity_same_name": 0, "identity_diff_name": [], "foreign": set()}
    for yi, y in enumerate(years):
        cy = per_year[y]["ctx"]
        for (_, code), a in per_year[y]["leaves"].items():
            arr = a.usd if usd else a.tmt
            for i in range(4):
                raw_tot[y][i] += arr[i]
            res_y, res_c = y, code
            if y == target:
                res_y, res_c = target, code
            elif mapper is not None:
                new, how = mapper.map_ex(code, y)
                if new in tplan:
                    res_y, res_c = target, new
                    if how in ("link", "chain"):
                        stat["linked"] += 1
                    else:
                        nm_old = norm_name(cy.plan(cy.key_of(y)).get(code, {}).get("name"))
                        if nm_old and tnames.get(new) and nm_old != tnames[new]:
                            stat["identity_diff_name"].append((code, y))
                        else:
                            stat["identity_same_name"] += 1
                else:
                    stat["foreign"].add((code, y))
            else:
                res_y, res_c = (target, code) if code in tplan else (y, code)
                if res_y != target:
                    stat["foreign"].add((code, y))
            cell = merged.setdefault((res_y, res_c), {}).setdefault(yi, [0.0, 0.0, 0.0, 0.0])
            for i in range(4):
                cell[i] += arr[i]
    if mapper is not None:
        if stat["identity_diff_name"]:
            ex = ", ".join(f"{c} ({y})" for c, y in stat["identity_diff_name"][:4])
            warnings.append(f"Без связи, по совпадению кода, сведено счетов с различающимся названием: {len(stat['identity_diff_name'])} "
                            f"(например {ex}); проверьте сопоставление в разделе «Связи счетов»")
        if stat["foreign"]:
            warnings.append(f"Счетов прошлых лет без пары в плане {target} года: {len(stat['foreign'])}; они показаны отдельными строками")
    elif stat["foreign"]:
        warnings.append(f"Счетов, отсутствующих в плане {target} года: {len(stat['foreign'])}; они показаны отдельными строками")
    warnings.append("Счета разных лет сведены к плану %d года по таблице связей счетов; коды без связи сводятся по совпадению кода" % target)

    # --- фильтры по ключам ---
    f = d["filters"]
    keys = list(merged)
    if f["accounts"]:
        keys = [k for k in keys if acc_mod.matches_any(k[1], f["accounts"])]
    if f["groups"]:
        allowed: set = set()
        by_year: dict[int, list[str]] = {}
        for (y, c) in keys:
            by_year.setdefault(y, []).append(c)
        for g in f["groups"]:
            cid, gid = g.get("classifier"), g.get("group")
            rs = ctx_t.resolver(cid)
            ids = rs.descendants(gid) if gid not in (None, -1) else set()
            for y, codes in by_year.items():
                res = ctx_t.resolve(cid, ctx_t.key_of(y), codes)
                for c in codes:
                    r = res[c]
                    if (gid is None and r.kind == "none") or (gid == -1 and r.kind == "conflict") or (r.kind == "ok" and r.group in ids):
                        allowed.add((y, c))
        keys = [k for k in keys if k in allowed]

    ny = len(years)
    vec_len = _BLK * ny
    items = []
    for k in sorted(keys, key=lambda k: (k[1], k[0])):
        v = [0.0] * vec_len
        for yi, (op, tdt, tkt, cl) in merged[k].items():
            b = yi * _BLK
            v[b:b + _BLK] = [tdt, tkt, op, cl, max(cl, 0.0), max(-cl, 0.0)]
        items.append((ctx_t.key_of(k[0]), k[1], v))

    roots, stats = C.build_tree(ctx_t, items, vec_len)
    expanded = d["columns"]["balance"] == "expanded"

    colspec = []     # (key, group, title, fn, drill, period)
    for yi, y in enumerate(years):
        b = yi * _BLK
        for m in measures:
            if m == "net_turn":
                fn = (lambda b: lambda v: v[b] - v[b + 1])(b)
            elif m == "turn_dt":
                fn = (lambda b: lambda v: v[b])(b)
            elif m == "turn_kt":
                fn = (lambda b: lambda v: v[b + 1])(b)
            elif m == "open":
                fn = (lambda b: lambda v: v[b + 2])(b)
            elif m == "close":
                fn = (lambda b: lambda v: v[b + 3])(b)
            elif m == "close_dt":
                fn = (lambda b: (lambda v: v[b + 4]) if expanded else (lambda v: max(v[b + 3], 0.0)))(b)
            else:
                fn = (lambda b: (lambda v: v[b + 5]) if expanded else (lambda v: max(-v[b + 3], 0.0)))(b)
            drill = ("dt" if m == "turn_dt" else "kt" if m == "turn_kt" else None) if y == target else None
            colspec.append((f"y{y}_{m}", str(y), MEASURES[m], fn, drill,
                            {"date_from": per_year[y]["d1"].isoformat(), "date_to": per_year[y]["d2"].isoformat()}))
    columns = [{"key": k, "title": t, "group": g, "type": "money", "drill": dr, "period": p} for k, g, t, _, dr, p in colspec]
    fns = {k: fn for k, _, _, fn, _, _ in colspec}
    deltas = []          # (ключ, функция первого года, функция последнего года)
    if delta:
        for m in measures:
            k0, k1 = f"y{years[0]}_{m}", f"y{years[-1]}_{m}"
            deltas.append((f"delta_{m}", fns[k0], fns[k1]))
            columns.append({"key": f"delta_{m}", "title": "Изменение, %" if len(measures) == 1 else f"{MEASURES[m]}: изменение, %",
                            "group": f"Изменение {years[0]} → {years[-1]}", "type": "money", "drill": None, "period": None})

    def pct(f0, f1, vec):
        a, b = f0(vec), f1(vec)
        return round((b - a) / abs(a) * 100.0, 2) if abs(a) >= C.TOL else None

    sort = d["options"]["sort"]
    hide = d["options"]["hide_zero"]

    def colval(n, key):
        fn = fns.get(key)
        return fn(n.vec) if fn else 0.0

    vis: dict[str, bool] = {}

    def visible(n) -> bool:
        if not hide:
            return True
        if n.id not in vis:
            vis[n.id] = any(abs(fn(n.vec)) >= C.TOL for fn in fns.values()) or any(visible(c) for c in n.children.values())
        return vis[n.id]

    ctx_t.scale = d["scale"]
    out_rows: list[dict] = []

    def emit(nodes, parent_id):
        for n in C.sort_children(nodes, sort, colval):
            if not visible(n):
                continue
            out_rows.append({"id": n.id, "parent": parent_id, "level": n.level, "kind": n.kind, "code": n.code, "name": n.name,
                             "values": {**{k: ctx_t.out(fn(n.vec)) for k, fn in fns.items()}, **{k: pct(f0, f1, n.vec) for k, f0, f1 in deltas}},
                             "bold": bool(n.children), "drill": C.drill_of(n)})
            if len(out_rows) > 20000:
                raise ReportError("Слишком много строк (более 20 000) — уменьшите глубину или добавьте фильтры")
            if n.children:
                emit(list(n.children.values()), n.id)

    emit(roots, None)
    totals_raw = {k: sum(fn(r.vec) for r in roots) for k, fn in fns.items()}
    totals = {k: ctx_t.out(v) for k, v in totals_raw.items()}
    for k, f0, f1 in deltas:
        a, b = totals_raw[f"y{years[0]}_{k[6:]}"], totals_raw[f"y{years[-1]}_{k[6:]}"]
        totals[k] = round((b - a) / abs(a) * 100.0, 2) if abs(a) >= C.TOL else None
    out_rows.append({"id": "total", "parent": None, "level": 0, "kind": "total", "code": "", "name": "ИТОГО", "values": dict(totals),
                     "bold": True, "drill": {}})

    # --- контроли ---
    checks = []
    filtered = C.any_filters_active(d)
    for y in years:
        t = raw_tot[y]
        if not filtered:
            checks.append({"name": f"{y}: оборот Дт = оборот Кт", "ok": abs(t[1] - t[2]) < C.TOL,
                           "detail": f"Дт {C.fmt_money(ctx_t.out(t[1]))}, Кт {C.fmt_money(ctx_t.out(t[2]))}"})
            if not usd:
                checks.append(C.net_zero_check(ctx_t, f"{y}: сальдо на конец, Σ нетто = 0", t[3], per_year[y]["ctx"].segments[-1]))
    if not filtered:
        hidden_unmapped = any(r["kind"] == "classifier" for r in d["rows"]) and not d["options"]["unmapped"]
        for yi, y in enumerate(years):
            b0 = yi * _BLK
            # строки несут блок года [t_dt, t_kt, open, close, ...]; сверяем оборот Дт, оборот Кт и сальдо на конец (а не только Дт)
            pl = (sum(r.vec[b0] for r in roots), sum(r.vec[b0 + 1] for r in roots), sum(r.vec[b0 + 3] for r in roots))
            raw = (raw_tot[y][1], raw_tot[y][2], raw_tot[y][3])
            ok = all(abs(a - c) < C.TOL for a, c in zip(pl, raw))
            ch = {"name": f"{y}: все счета размещены в строках (оборот Дт, Кт, сальдо)", "ok": ok, "level": "ok" if ok else "error",
                  "detail": f"в строках оборот Дт {C.fmt_money(ctx_t.out(pl[0]))} из {C.fmt_money(ctx_t.out(raw[0]))}"}
            if not ok and hidden_unmapped:
                ch["level"] = "warn"
                ch["detail"] += "; строка «не сопоставлено» скрыта опцией - не вошедшие в классификатор счета не показаны"
            checks.append(ch)
    bad = 0

    def walk_(n):
        nonlocal bad
        if n.children:
            for i in range(vec_len):
                if abs(sum(c.vec[i] for c in n.children.values()) + (n.own[i] if n.own else 0.0) - n.vec[i]) >= C.TOL:
                    bad += 1
                    break
            for c in n.children.values():
                walk_(c)
    for r in roots:
        walk_(r)
    checks.append({"name": "Родитель = сумма детей", "ok": bad == 0, "detail": "по всем узлам иерархии" if not bad else f"нарушений: {bad}"})
    if any(r["kind"] == "classifier" for r in d["rows"]) and stats["unmapped"]:
        warnings.append(f"Не сопоставлено с классификатором: {len({c for _, c in stats['unmapped']})} сч.")
    dws = [x for x in (C.denom_warning_for([s for yy in years for s in per_year[yy]["ctx"].segments], d["denominate"], usd),) if x]
    warnings += dws
    info = C.attr_filter_check(ctx_t)
    if info:
        checks.append(info)
    unit = {1: "", 1000: ", в тысячах", 1000000: ", в миллионах"}[d["scale"]]
    sub = f"Годы: {', '.join(map(str, years))} · период внутри года: {m1[1]:02d}.{m1[0]:02d} – {m2[1]:02d}.{m2[0]:02d} · " \
          f"к плану счетов {target} г. · Валюта: {C.report_label(d)}{unit}" + (" · " + C.attr_filter_text(ctx_t) if C.attr_filters_active(d) else "")
    return C.finalize_checks({"title": d.get("title") or "Сравнение лет", "subtitle": sub, "columns": columns, "rows": out_rows, "totals": totals,
                              "checks": checks, "warnings": list(dict.fromkeys(warnings))})



