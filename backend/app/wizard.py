"""Мастер «Книги и годы»: скан источников Logo (только SELECT) и предложение разметки книг.

scan()            - «дешёвая статистика» по всем таблицам LG_<FFF>_<PP>_EMFLINE (один агрегат на источник).
transition_stats  - статистики переходов между соседними книгами (масштаб/делитель и разрыв), два запроса на источник.
propose()         - ЧИСТАЯ функция: по результату скана (и необязательным transitions) строит предложение разметки.
build_proposal()  - scan + transition_stats + propose.
В модуле нет номеров фирм, годов, делителей и названий конкретной базы: всё выводится из данных.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import charset, logo, plan

# ---- пороги (docs §4.2) ----
GAP_DAYS = 30                 # допуск начала/конца периода для обрыва и диапазона
IN_WORK_DAYS = 45             # период «в работе»: последняя дата не старше стольки дней
OPEN_IMBALANCE = 0.001        # |Дт-Кт| открытия > 0,1% большей суммы -> несбалансировано
TURN_IMBALANCE = 0.0001       # обороты: расхождение > 0,01% -> учебная/копия
TRAINING_OPEN_SHARE = 0.9     # доля строк открытия для «учебной копии»
MAX_BOOK_DAYS = 396           # период длиннее ~13 месяцев разбивается на двенадцатимесячные книги
SLOT_OVERLAP = 0.5            # пересечение > 50% меньшего диапазона -> один слот
CONTIG_DAYS = 7               # начало следующей книги = конец предыдущей + не более стольких дней
SNAP_FACTORS = (1, 10, 100, 1000, 5000, 10000, 100000, 1000000)
SNAP_TOL = 0.1                # |ln(ratio/f)| < 0.1
R3_MIN_DIFF = 1e-4            # разница относительных разрывов, достаточная для правила R3
GAP_WARN = 1e-4               # относительный разрыв переноса, выше которого пишется предупреждение
OEM_SAMPLE_ROWS = 20000       # строк выборки текстов на источник
OEM_MIN_ROWS = 3              # минимум строк с признаками OEM
OEM_MIN_SHARE = 0.01          # и доля от строк с не-ASCII
PROFILE_SOURCES = 3           # сколько последних источников идёт на профиль плана
OFFICIAL_RX = re.compile(r"resmi|official|oficial|ресми|официал", re.I)

_RX_LINE = re.compile(r"^LG_(\d{3})_(\d{2})_EMFLINE$", re.I)
_RX_FICHE = re.compile(r"^LG_(\d{3})_(\d{2})_EMFICHE$", re.I)
_RX_ACC = re.compile(r"^LG_(\d{3})_EMUHACC$", re.I)


# ---- ключи и мелочи ----
def src_key(firm: int, period: int) -> int:
    return int(firm) if int(period) == 1 else int(period) * 1000 + int(firm)


def src_parts(src: int) -> Tuple[int, int]:
    return (src % 1000, src // 1000) if src >= 1000 else (src, 1)


def _tbl(firm: int, period: int, name: str) -> str:
    return "LG_%03d_%02d_%s" % (firm, period, name)


def _iso(v) -> Optional[str]:
    """'YYYYMMDD' / datetime -> 'YYYY-MM-DD'."""
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
    return s[:10] or None


def _d(s) -> Optional[date]:
    if not s:
        return None
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def _ru(d: Optional[date]) -> str:
    return d.strftime("%d.%m.%Y") if d else "?"


def _money(kop) -> str:
    """Целые копейки -> «1 234 567,00»."""
    kop = int(kop or 0)
    sign = "-" if kop < 0 else ""
    kop = abs(kop)
    s = "{:,}".format(kop // 100).replace(",", " ")
    return "%s%s,%02d" % (sign, s, kop % 100)


def _s(v) -> str:
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v).strip())


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    day = d.day
    while True:
        try:
            return date(y, m, day)
        except ValueError:
            day -= 1


def snap_divisor(ratio: Optional[float]) -> Optional[int]:
    if not ratio or ratio <= 0:
        return None
    best = None
    for f in SNAP_FACTORS:
        e = abs(math.log(ratio / f))
        if e < SNAP_TOL and (best is None or e < best[0]):
            best = (e, f)
    return best[1] if best else None


# ---- чтение Logo ----
def list_tables(lc=None, conn=None) -> set:
    """Имена таблиц LG_*_EMFLINE / LG_*_EMFICHE (для проверки разметки)."""
    rows = logo.query("SELECT name FROM sys.tables WHERE name LIKE 'LG[_]%[_]EMFLINE' OR name LIKE 'LG[_]%[_]EMFICHE' OR name LIKE 'LG[_]%[_]EMUHACC'", (), conn=conn, lc=lc)
    return {_s(r[0]).upper() for r in rows}


def _all_tables(conn, lc) -> List[str]:
    rows = logo.query("SELECT name FROM sys.tables WHERE name LIKE 'LG[_]%'", (), conn=conn, lc=lc)
    return [_s(r[0]).upper() for r in rows]


def _detect_charset(conn, lc) -> dict:
    """Кодировка по COLLATION колонок таблиц LG_* (мода), а не сервера."""
    rows = logo.query(
        "SELECT c.collation_name, COUNT(*) FROM sys.columns c JOIN sys.tables t ON t.object_id=c.object_id "
        "WHERE c.collation_name IS NOT NULL AND (t.name LIKE 'LG[_]%[_]EMFLINE' OR t.name LIKE 'LG[_]%[_]EMFICHE' OR t.name LIKE 'LG[_]%[_]EMUHACC') "
        "GROUP BY c.collation_name", (), conn=conn, lc=lc)
    out = {"collation": "", "codepage": 0, "ansi": "", "oem": ""}
    if not rows:
        return out
    coll = _s(max(rows, key=lambda r: r[1])[0])
    cp = 0
    if re.match(r"^[A-Za-z0-9_]+$", coll):
        try:
            cp = int(logo.query("SELECT CAST(COLLATIONPROPERTY('%s', 'CodePage') AS int)" % coll, (), conn=conn, lc=lc)[0][0] or 0)
        except Exception:
            pass
    ansi = charset.ansi_from_codepage(cp)
    out.update({"collation": coll, "codepage": cp, "ansi": ansi, "oem": charset.default_oem(ansi)})
    return out


def _currency(conn, lc, firm: int, slot: Optional[int]) -> Optional[dict]:
    if slot is None:
        return None
    rows = logo.query("SELECT FIRMNR, CURCODE, CURNAME, CURSYMBOL FROM L_CURRENCYLIST WHERE CURTYPE=? AND FIRMNR IN (?, 0, 10) ORDER BY CASE WHEN FIRMNR=? THEN 0 ELSE 1 END",
                      (slot, firm, firm), conn=conn, lc=lc)
    if not rows:
        rows = logo.query("SELECT TOP 1 FIRMNR, CURCODE, CURNAME, CURSYMBOL FROM L_CURRENCYLIST WHERE CURTYPE=?", (slot,), conn=conn, lc=lc)
    if not rows:
        return None
    r = rows[0]
    return {"slot": int(slot), "code": _s(r[1]), "name": _s(r[2]), "symbol": _s(r[3])}


def _scan_source(conn, lc, firm: int, period: int, has_fiche: bool, ansi: str, oem: str) -> dict:
    t = _tbl(firm, period, "EMFLINE")
    rows = logo.query(
        "SELECT YEAR(DATE_), CASE WHEN TRCODE=1 THEN 1 ELSE 0 END, COUNT(*), "
        "SUM(CAST(ROUND(DEBIT*100,0) AS bigint)), SUM(CAST(ROUND(CREDIT*100,0) AS bigint)), "
        "SUM(CASE WHEN REPORTNET<>0 THEN 1 ELSE 0 END), CONVERT(varchar(8),MIN(DATE_),112), CONVERT(varchar(8),MAX(DATE_),112) "
        "FROM %s WHERE CANCELLED=0 GROUP BY YEAR(DATE_), CASE WHEN TRCODE=1 THEN 1 ELSE 0 END" % t, (), conn=conn, lc=lc)
    res = {"lines": 0, "open_lines": 0, "turn_dt": 0, "turn_kt": 0, "open_dt": 0, "open_kt": 0, "usd_rows": 0}
    years = {}      # type: Dict[str, dict]
    dmin = dmax = tmin = tmax = None
    for y, is_open, n, dt, kt, usd, a, b in rows:
        n, dt, kt, usd = int(n or 0), int(dt or 0), int(kt or 0), int(usd or 0)
        yy = years.setdefault(str(y), {"lines": 0, "open_lines": 0, "turn_dt": 0, "turn_kt": 0, "open_dt": 0, "open_kt": 0})
        res["lines"] += n
        res["usd_rows"] += usd
        yy["lines"] += n
        pre = "open" if is_open else "turn"
        res[pre + "_dt"] += dt
        res[pre + "_kt"] += kt
        yy[pre + "_dt"] += dt
        yy[pre + "_kt"] += kt
        if is_open:
            res["open_lines"] += n
            yy["open_lines"] += n
        else:
            tmin = a if tmin is None or a < tmin else tmin
            tmax = b if tmax is None or b > tmax else tmax
        dmin = a if dmin is None or a < dmin else dmin
        dmax = b if dmax is None or b > dmax else dmax
    res.update({"years": years, "dmin": _iso(dmin), "dmax": _iso(dmax), "turn_dmin": _iso(tmin), "turn_dmax": _iso(tmax),
                "turn_lines": res["lines"] - res["open_lines"]})
    fiches = fiches_cancelled = 0
    oem_rows = oem_hi = 0
    if has_fiche:
        r = logo.query("SELECT SUM(CASE WHEN CANCELLED=0 THEN 1 ELSE 0 END), SUM(CASE WHEN CANCELLED<>0 THEN 1 ELSE 0 END) FROM %s" % _tbl(firm, period, "EMFICHE"),
                       (), conn=conn, lc=lc)[0]
        fiches, fiches_cancelled = int(r[0] or 0), int(r[1] or 0)
    res.update({"fiches": fiches, "fiches_cancelled": fiches_cancelled})
    # признак OEM: выборка текстов (каждая ceil(n/20000)-я строка)
    if oem and ansi and res["lines"] > 0:
        for tab, col, n in ((_tbl(firm, period, "EMFICHE"), "GENEXP1", fiches), (t, "LINEEXP", res["lines"])):
            if tab.endswith("EMFICHE") and not has_fiche:
                continue
            k = max(1, int(math.ceil(n / float(OEM_SAMPLE_ROWS))))
            try:
                smp = logo.query("SELECT CAST(%s AS varbinary(255)) FROM %s WHERE CANCELLED=0 AND LOGICALREF %% %d = 0 AND %s IS NOT NULL AND %s<>''"
                                 % (col, tab, k, col, col), (), conn=conn, lc=lc)
            except Exception:
                continue
            a, b = charset.has_oem_rows((r[0] for r in smp), ansi, oem)
            oem_rows += a
            oem_hi += b
    res["oem_rows"] = oem_rows
    res["oem_sample"] = oem_hi
    res["oem"] = bool(oem_rows >= OEM_MIN_ROWS and oem_hi and oem_rows / float(oem_hi) >= OEM_MIN_SHARE)
    return res


def scan(lc=None, firms: Optional[Iterable[int]] = None, progress: Optional[Callable] = None) -> dict:
    """Дешёвая статистика по всем источникам (§4.1). Только SELECT. progress(done, total, current, message)."""
    def rep(done, total, cur, msg):
        if progress:
            progress(done, total, cur, msg)

    want = set(int(f) for f in firms) if firms else None
    conn = logo.connect(lc=lc)
    try:
        rep(0, 1, "", "Чтение справочников Logo")
        names = _all_tables(conn, lc)
        line_pairs, fiche_pairs, acc_firms = set(), set(), set()
        for n in names:
            m = _RX_LINE.match(n)
            if m:
                line_pairs.add((int(m.group(1)), int(m.group(2))))
            m = _RX_FICHE.match(n)
            if m:
                fiche_pairs.add((int(m.group(1)), int(m.group(2))))
            m = _RX_ACC.match(n)
            if m:
                acc_firms.add(int(m.group(1)))
        firm_rows = {}
        for r in logo.query("SELECT NR, NAME, TITLE, YTLSOURCEFIRM, LOCALCTYP, FIRMREPCURR FROM L_CAPIFIRM", (), conn=conn, lc=lc):
            firm_rows[int(r[0])] = {"name": _s(r[1]), "title": _s(r[2]), "ytl": int(r[3] or 0), "localctyp": r[4], "repcurr": r[5]}
        per_rows = {}
        for r in logo.query("SELECT FIRMNR, NR, CONVERT(varchar(8),BEGDATE,112), CONVERT(varchar(8),ENDDATE,112), ACTIVE FROM L_CAPIPERIOD", (), conn=conn, lc=lc):
            per_rows[(int(r[0]), int(r[1]))] = {"beg": _iso(r[2]), "end": _iso(r[3]), "active": bool(r[4])}
        ch = _detect_charset(conn, lc)
        pairs = sorted(p for p in line_pairs if want is None or p[0] in want)
        out = []        # type: List[dict]
        total = len(pairs)
        for i, (f, p) in enumerate(pairs):
            rep(i, total, "%03d_%02d" % (f, p), "Чтение источника: фирма %d, период %d" % (f, p))
            fr, pr = firm_rows.get(f), per_rows.get((f, p))
            s = {"src": src_key(f, p), "firm": f, "period": p, "in_logo": bool(fr and pr), "firm_in_logo": bool(fr),
                 "firm_name": fr["name"] if fr else "", "title": fr["title"] if fr else "",
                 "begdate": pr["beg"] if pr else None, "enddate": pr["end"] if pr else None, "active": pr["active"] if pr else None,
                 "ytl_source_firm": fr["ytl"] if fr else 0, "localctyp": fr["localctyp"] if fr else None, "repcurr": fr["repcurr"] if fr else None,
                 "has_tables": (f, p) in fiche_pairs, "has_fiche": (f, p) in fiche_pairs, "has_acc": f in acc_firms}
            s.update(_scan_source(conn, lc, f, p, (f, p) in fiche_pairs, ch["ansi"], ch["oem"]))
            out.append(s)
        rep(total, total, "", "Определение параметров набора")
        params = {"charset": ch}
        # валюты: берём фирму с самыми поздними оборотами
        live = [s for s in out if s["in_logo"] and s["turn_lines"] > 0]
        ref = max(live, key=lambda s: (s["turn_dmax"] or "", s["src"])) if live else None
        loc = _currency(conn, lc, ref["firm"], ref["localctyp"]) if ref and ref["localctyp"] is not None else None
        rep_c = _currency(conn, lc, ref["firm"], ref["repcurr"]) if ref and ref["repcurr"] is not None else None
        enabled = any(s["usd_rows"] > 0 for s in out if s["in_logo"])
        if loc:
            loc["note"] = ("В Logo местная валюта записана как «%s — %s» (слот %d): слот может быть переименован в Logo, проверьте название"
                           % (loc["code"], loc["name"], loc["slot"]))
        if rep_c:
            rep_c["enabled"] = bool(enabled)
        params["currency_local"] = loc
        params["currency_report"] = rep_c
        # профиль плана счетов по нескольким последним источникам
        last = sorted(live, key=lambda s: (s["turn_dmax"] or "", s["src"]), reverse=True)
        pf, seen = [], set()
        for s in last:
            if s["has_acc"] and s["firm"] not in seen:
                seen.add(s["firm"])
                pf.append(s["firm"])
            if len(pf) >= PROFILE_SOURCES:
                break
        codes = []
        for f in pf:
            codes += [_s(r[0]) for r in logo.query("SELECT CODE FROM LG_%03d_EMUHACC" % f, (), conn=conn, lc=lc)]
        params["plan_profile"] = plan.detect_profile(codes)
        params["plan_sample_firms"] = pf
        return {"scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "sources": out, "params_detected": params,
                "tables_total": len(names)}
    finally:
        conn.close()


# ---- переходы между книгами ----
def transition_stats(pairs, lc=None, root_len: int = 3, balance_roots: Optional[List[str]] = None, conn=None) -> dict:
    """pairs - [(srcA, srcB)]. Закрытие A = все проводки, открытие B = TRCODE=1; суммы по LEFT(ACCOUNTCODE, root_len) классов balance_roots.
    -> {"<A>-<B>": {"ratio", "gap", "gap_roots", "b_total", "roots"}}; деньги в копейках масштаба B (после деления A на snap(ratio))."""
    roots = [x for x in (balance_roots or []) if re.match(r"^[0-9A-Za-z]+$", str(x))]
    cond = (" AND (" + " OR ".join("ACCOUNTCODE LIKE '%s%%'" % x for x in roots) + ")") if roots else ""
    srcs = sorted({s for p in pairs for s in p})
    own = conn is None
    if own:
        conn = logo.connect(lc=lc)
    cl, op = {}, {}
    try:
        for s in srcs:
            f, p = src_parts(s)
            t = _tbl(f, p, "EMFLINE")
            for store, extra in ((cl, ""), (op, " AND TRCODE=1")):
                rows = logo.query("SELECT LEFT(ACCOUNTCODE,%d), SUM(CAST(ROUND(DEBIT*100,0) AS bigint)-CAST(ROUND(CREDIT*100,0) AS bigint)) "
                                  "FROM %s WHERE CANCELLED=0%s%s GROUP BY LEFT(ACCOUNTCODE,%d)" % (root_len, t, cond, extra, root_len), (), conn=conn, lc=lc)
                store[s] = {_s(r[0]): int(r[1] or 0) for r in rows}
    finally:
        if own:
            conn.close()
    out = {}
    for a, b in pairs:
        out["%d-%d" % (a, b)] = _pair_stat(cl.get(a, {}), op.get(b, {}))
    return out


def _pair_stat(close_a: dict, open_b: dict) -> dict:
    common = [r for r in close_a if r in open_b]
    sa = sum(abs(close_a[r]) for r in common)
    sb = sum(abs(open_b[r]) for r in common)
    ratio = (sa / float(sb)) if sb else None
    f = snap_divisor(ratio) or 1
    gap = roots = 0
    for r in set(close_a) | set(open_b):
        d = abs(open_b.get(r, 0) - int(round(close_a.get(r, 0) / float(f))))
        if d > max(1, f):
            gap += d
            roots += 1
    return {"ratio": round(ratio, 6) if ratio is not None else None, "gap": int(gap), "gap_roots": roots,
            "b_total": int(sum(abs(v) for v in open_b.values())), "roots": len(common)}


# ---- предложение разметки (чистая часть) ----
def _g(s: dict, key: str, default=None):
    v = s.get(key)
    return default if v is None else v


def _off_reasons(s: dict, today: date) -> List[str]:
    r = []     # type: List[str]
    firm, period = s["firm"], s["period"]
    if not s.get("in_logo", True):
        if s.get("firm_in_logo", False):
            r.append("таблицы есть, а периода %d фирмы %d нет в справочнике Logo (L_CAPIPERIOD)" % (period, firm))
        else:
            r.append("таблицы есть, а фирмы %d нет в справочнике Logo (L_CAPIFIRM)" % firm)
    if not s.get("has_tables", True):
        r.append("нет таблиц проводок или ордеров")
        return r
    n = int(s.get("lines") or 0)
    ol = int(s.get("open_lines") or 0)
    if n == 0:
        r.append("нет проводок")
        return r
    if ol >= n:
        r.append("только перенесённое открытие следующего года (%d строк открытия, оборотов нет)" % ol)
        return r
    # обрыв
    beg, end = _d(s.get("begdate")), _d(s.get("enddate"))
    tmin, tmax = _d(_g(s, "turn_dmin", s.get("dmin"))), _d(_g(s, "turn_dmax", s.get("dmax")))
    if beg and end and tmin and tmax:
        in_work = tmax >= today - timedelta(days=IN_WORK_DAYS) and end >= today - timedelta(days=IN_WORK_DAYS)
        head = tmin > beg + timedelta(days=GAP_DAYS)
        tail = (not in_work) and tmax < end - timedelta(days=GAP_DAYS)
        if head or tail:
            r.append("обрыв: проводки только %s–%s при периоде %s–%s" % (_ru(tmin), _ru(tmax), _ru(beg), _ru(end)))
    od, ok = int(s.get("open_dt") or 0), int(s.get("open_kt") or 0)
    if ol and abs(od - ok) > OPEN_IMBALANCE * max(od, ok, 1):
        r.append("открытие не сбалансировано: Дт %s, Кт %s" % (_money(od), _money(ok)))
    td, tk = int(s.get("turn_dt") or 0), int(s.get("turn_kt") or 0)
    if ol / float(n) >= TRAINING_OPEN_SHARE:
        r.append("открытие — %d%% строк (%d из %d): учебная копия" % (int(round(100.0 * ol / n)), ol, n))
    if abs(td - tk) > TURN_IMBALANCE * max(td, tk, 1):
        r.append("обороты не сбалансированы: Дт %s ≠ Кт %s" % (_money(td), _money(tk)))
    return r


def _book_ranges(s: dict) -> Tuple[List[Tuple[date, date]], List[str]]:
    """Диапазоны книг источника (§4.2 п.2) и пометки «проверьте»."""
    beg, end = _d(s.get("begdate")), _d(s.get("enddate"))
    dmin, dmax = _d(s.get("dmin")), _d(s.get("dmax"))
    notes = []     # type: List[str]
    tol = timedelta(days=GAP_DAYS)
    if not beg or not end:
        beg, end = dmin, dmax
        if not beg or not end:
            return [], notes
    elif dmin and dmax:
        if dmax < beg or dmin > end:
            beg, end = dmin, dmax
            notes.append("диапазон книги взят по датам проводок, а не по периоду Logo: проверьте")
        elif dmin < beg - tol or dmax > end + tol:
            beg, end = min(beg, dmin), max(end, dmax)
            notes.append("проводки выходят за период Logo: диапазон расширен по данным, проверьте")
    if (end - beg).days + 1 <= MAX_BOOK_DAYS:
        return [(beg, end)], notes
    out, cur = [], beg
    while cur <= end:
        nxt = _add_months(cur, 12) - timedelta(days=1)
        if nxt >= end or (end - nxt).days < 31:
            nxt = end
        out.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    notes.append("период длиннее 13 месяцев: предложено разбиение на двенадцатимесячные книги, проверьте")
    return out, notes


def _overlap(a: Tuple[date, date], b: Tuple[date, date]) -> float:
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    inter = (hi - lo).days + 1
    if inter <= 0:
        return 0.0
    small = min((a[1] - a[0]).days + 1, (b[1] - b[0]).days + 1)
    return inter / float(small)


def _prepare(scan_result: dict, today: date) -> dict:
    off = []       # type: List[dict]
    books = []     # type: List[dict]
    off_srcs = {}
    for s in scan_result.get("sources", []):
        reasons = _off_reasons(s, today)
        if reasons:
            off_srcs[s["src"]] = s
            off.append({"src": s["src"], "firm": s["firm"], "period": s["period"], "reasons": reasons})
            continue
        ranges, notes = _book_ranges(s)
        if not ranges:
            off.append({"src": s["src"], "firm": s["firm"], "period": s["period"], "reasons": ["нет проводок"]})
            off_srcs[s["src"]] = s
            continue
        for k, (a, b) in enumerate(ranges):
            books.append({"src": s["src"], "firm": s["firm"], "period": s["period"], "name": s.get("firm_name") or "", "title": s.get("title") or "",
                          "d_from": a, "d_to": b, "s": s, "notes": list(notes), "split": len(ranges) > 1})
    # слоты по пересечению
    books.sort(key=lambda x: (x["d_from"], x["d_to"], x["src"]))
    slots = []     # type: List[List[dict]]
    for bk in books:
        for sl in slots:
            if any(_overlap((bk["d_from"], bk["d_to"]), (m["d_from"], m["d_to"])) > SLOT_OVERLAP for m in sl):
                sl.append(bk)
                break
        else:
            slots.append([bk])
    for sl in slots:
        longest = max(sl, key=lambda m: ((m["d_to"] - m["d_from"]).days, -m["src"]))
        for m in sl:
            m["label"] = longest["d_to"].year
    slots.sort(key=lambda sl: (min(m["d_from"] for m in sl), sl[0]["src"]))
    cnt = Counter()   # type: Counter
    for sl in slots:
        lab = sl[0]["label"]
        cnt[lab] += 1
        for m in sl:
            m["part"] = cnt[lab]
    return {"off": off, "slots": slots, "off_srcs": off_srcs}


def _contig(a: dict, b: dict) -> bool:
    return 0 <= (b["d_from"] - a["d_to"]).days <= CONTIG_DAYS


def _slot_range(sl: List[dict]) -> dict:
    return {"d_from": min(m["d_from"] for m in sl), "d_to": max(m["d_to"] for m in sl)}


def candidate_pairs(scan_result: dict, today: Optional[date] = None) -> List[Tuple[int, int]]:
    """Пары (srcA, srcB) книг-кандидатов в соседних слотах: для них нужны transition_stats."""
    prep = _prepare(scan_result, today or date.today())
    slots = prep["slots"]
    out = []
    for i in range(len(slots) - 1):
        if not _contig(_slot_range(slots[i]), _slot_range(slots[i + 1])):
            continue
        for a in slots[i]:
            for b in slots[i + 1]:
                if a["src"] != b["src"] and (a["src"], b["src"]) not in out:
                    out.append((a["src"], b["src"]))
    return out


def _is_official(b: dict) -> bool:
    return bool(OFFICIAL_RX.search("%s %s" % (b["name"], b["title"])))


def _rel_gap(trans: dict, a: int, b: int) -> Optional[float]:
    t = trans.get("%d-%d" % (a, b))
    if not t:
        return None
    return t["gap"] / float(max(t.get("b_total") or 0, 1))


def _choose_main(i: int, slots: List[List[dict]], all_firms: List[Tuple[int, int]], trans: dict) -> Tuple[dict, str, str, List[str]]:
    """-> (основная, правило, причина, предупреждения)."""
    sl = slots[i]
    if len(sl) == 1:
        return sl[0], "single", "Единственная книга слота.", []
    firms_in = set(m["firm"] for m in sl)
    # R3 - заранее (нужна и для предупреждений)
    neigh = []
    if i > 0 and _contig(_slot_range(slots[i - 1]), _slot_range(sl)):
        neigh.append((-1, slots[i - 1]))
    if i + 1 < len(slots) and _contig(_slot_range(sl), _slot_range(slots[i + 1])):
        neigh.append((1, slots[i + 1]))
    scores = {}
    for m in sl:
        tot, ok = 0.0, bool(neigh)
        for d, nb in neigh:
            vals = [(_rel_gap(trans, n["src"], m["src"]) if d < 0 else _rel_gap(trans, m["src"], n["src"])) for n in nb if n["src"] != m["src"]]
            vals = [v for v in vals if v is not None]
            if not vals:
                ok = False
                break
            tot += min(vals)
        if ok:
            scores[m["src"]] = tot
    r3 = None
    if len(scores) == len(sl):
        ordered = sorted(sl, key=lambda m: scores[m["src"]])
        if scores[ordered[1]["src"]] - scores[ordered[0]["src"]] > R3_MIN_DIFF:
            r3 = ordered[0]
    # R2: на книгу ссылается фирма вне слота, а сама она не ссылается на фирму слота
    r2c = []
    for m in sl:
        ytl = m["s"].get("ytl_source_firm") or 0
        refd = any(f not in firms_in and y == m["firm"] for f, y in all_firms)
        if refd and ytl not in firms_in:
            r2c.append(m)
    r2 = r2c[0] if len(r2c) == 1 else None
    off = [m for m in sl if _is_official(m)]
    r1 = off[0] if len(off) == 1 else None
    warns = []     # type: List[str]
    if r1 is not None:
        for other, nm in ((r2, "источник переноса остатков"), (r3, "меньший разрыв переноса")):
            if other is not None and other["src"] != r1["src"]:
                warns.append("выбор по слову RESMI расходится с правилом «%s»: основной по нему была бы книга %d" % (nm, other["src"]))
        return r1, "R1", "Основная: в названии фирмы есть признак официальной версии (RESMI). Остальные книги слота дублируют её диапазон.", warns
    if r2 is not None:
        cp = [m["src"] for m in sl if m is not r2 and (m["s"].get("ytl_source_firm") or 0) == r2["firm"]]
        txt = "Основная: на неё ссылается перенос остатков другой фирмы (источник остатков = %d)" % r2["firm"]
        if cp:
            txt += "; книги %s — копии (источник остатков = %d)" % (", ".join(str(x) for x in cp), r2["firm"])
        return r2, "R2", txt + ".", warns
    if r3 is not None:
        return r3, "R3", "Основная: перенос остатков с соседними книгами сходится лучше (меньший разрыв).", warns
    best = sorted(sl, key=lambda m: (-int(m["s"].get("lines") or 0), m["firm"], m["period"]))[0]
    return best, "R4", "Основная: больше проводок (равноправные кандидаты, проверьте выбор).", warns


def _kop(v) -> float:
    return round(int(v or 0) / 100.0, 2)


def src_stats(s: dict) -> dict:
    """Статистика источника из данных скана (без новых запросов к Logo): название фирмы, число проводок, даты, обороты, признак открытия."""
    return {"firm_name": s.get("firm_name") or "", "title": s.get("title") or "", "begdate": s.get("begdate"), "enddate": s.get("enddate"),
            "lines": int(s.get("lines") or 0), "open_lines": int(s.get("open_lines") or 0), "turn_lines": int(s.get("turn_lines") or 0),
            "date_min": s.get("dmin"), "date_max": s.get("dmax"), "turn_date_min": s.get("turn_dmin"), "turn_date_max": s.get("turn_dmax"),
            "turn_dt": _kop(s.get("turn_dt")), "turn_kt": _kop(s.get("turn_kt")), "open_dt": _kop(s.get("open_dt")), "open_kt": _kop(s.get("open_kt")),
            "has_open": int(s.get("open_lines") or 0) > 0, "fiches": int(s.get("fiches") or 0), "oem_rows": int(s.get("oem_rows") or 0)}


def propose(scan_result: dict, today: Optional[date] = None) -> dict:
    """Предложение разметки по результату скана (§4.2, формат §3.4). Не обращается к Logo."""
    today = today or date.today()
    prep = _prepare(scan_result, today)
    slots, off = prep["slots"], prep["off"]
    trans = scan_result.get("transitions")
    has_trans = trans is not None
    trans = trans or {}
    srcs = scan_result.get("sources", [])
    all_firms = [(s["firm"], s.get("ytl_source_firm") or 0) for s in srcs if s.get("in_logo", True)]
    mains = []     # type: List[dict]
    info = {}      # id(book) -> (rule, reason, warns)
    for i, sl in enumerate(slots):
        m, rule, reason, warns = _choose_main(i, slots, all_firms, trans)
        info[id(m)] = (rule, reason, list(warns))
        mains.append(m)
    # делитель по цепочке основных книг (с конца)
    div = {}       # type: Dict[int, int]
    ordered = sorted(mains, key=lambda m: (m["d_from"], m["src"]))
    extra = {}     # type: Dict[int, List[str]]
    unknown = {}   # type: Dict[int, bool]
    denom = []
    for k in range(len(ordered) - 1, -1, -1):
        b = ordered[k]
        nxt = ordered[k + 1] if k + 1 < len(ordered) and _contig(b, ordered[k + 1]) else None
        w = extra.setdefault(id(b), [])
        if nxt is None:
            div[id(b)] = 1
            continue
        if nxt["src"] == b["src"]:
            f = 1
        else:
            t = trans.get("%d-%d" % (b["src"], nxt["src"]))
            f = snap_divisor(t["ratio"]) if t else None
            if f is None:
                f = 1
                if has_trans:
                    unknown[id(b)] = True
                    w.append("Переход %d→%d: делитель не определён%s, взят делитель следующей книги" % (
                        b["label"], nxt["label"], (" (отношение %.4g)" % t["ratio"]) if t and t.get("ratio") else ""))
            if t and t.get("gap") and t["gap"] / float(max(t.get("b_total") or 0, 1)) > GAP_WARN:
                w.append("Переход %d→%d: расхождение %s (%d сч., классы баланса)%s" % (
                    b["label"], nxt["label"], _money(t["gap"]), t.get("gap_roots", 0),
                    ("; масштаб ÷%d" % f) if f > 1 else ""))
        div[id(b)] = f * div[id(nxt)]
        if div[id(b)] > div[id(nxt)]:
            denom.append({"before": nxt["d_from"].strftime("%Y-%m-%d"), "divisor": div[id(b)] // div[id(nxt)]})
    out_books = []
    for si, sl in enumerate(slots):
        main = mains[si]
        rule, reason, warns = info[id(main)]
        for m in sl:
            is_main = m is main
            w = list(m["notes"])
            if is_main:
                w += warns + extra.get(id(m), [])
            if m["s"].get("active") and m["s"].get("turn_dmax") and _d(m["s"]["turn_dmax"]) < m["d_to"] - timedelta(days=GAP_DAYS):
                w.append("период в работе: проводки по %s" % _ru(_d(m["s"]["turn_dmax"])))
            conf = "high"
            if rule in ("R3", "R4") or m["split"]:
                conf = "medium"
            if unknown.get(id(main)):
                conf = "low"
            row = {"slot": m["label"], "label": m["label"], "part": m["part"], "src": m["src"], "firm": m["firm"], "period": m["period"],
                   "date_from": m["d_from"].strftime("%Y-%m-%d"), "date_to": m["d_to"].strftime("%Y-%m-%d"),
                   "role": "main" if is_main else "alt", "divisor": div[id(main)], "oem": bool(m["s"].get("oem")), "name": m["name"],
                   "confidence": conf, "warnings": w}
            if is_main:
                row["reason"] = reason
            else:
                row["reason"] = "Запасная: дублирует диапазон основной книги %d. %s" % (main["src"], reason.replace("Основная: ", "Основная — ").rstrip("."))
            out_books.append(row)
    out_books.sort(key=lambda r: (r["date_from"], 0 if r["role"] == "main" else 1, r["src"]))
    for o in off:     # обрыв: чем перекрыт
        if any(x.startswith("обрыв") for x in o["reasons"]):
            s = prep["off_srcs"][o["src"]]
            a, b = _d(s.get("turn_dmin") or s.get("dmin")), _d(s.get("turn_dmax") or s.get("dmax"))
            for m in sorted(mains, key=lambda m: m["d_from"]):
                if a and b and _overlap((a, b), (m["d_from"], m["d_to"])) > SLOT_OVERLAP:
                    o["reasons"].append("перекрывается более полной книгой %d" % m["src"])
                    break
    # разрывы между метками основных книг
    gaps = []
    mm = sorted([r for r in out_books if r["role"] == "main"], key=lambda r: r["date_from"])
    for a, b in zip(mm, mm[1:]):
        days = (_d(b["date_from"]) - _d(a["date_to"])).days - 1
        if days > 0:
            gaps.append({"after": a["label"], "before": b["label"], "days": days})
    unassigned = []
    by_src = {s["src"]: s for s in srcs}
    for o in off:
        s = by_src[o["src"]]
        if int(s.get("lines") or 0) - int(s.get("open_lines") or 0) > 0:
            unassigned.append({"src": o["src"], "firm": o["firm"], "period": o["period"], "lines": int(s["lines"]),
                               "date_from": s.get("dmin"), "date_to": s.get("dmax"), "reasons": o["reasons"]})
    # параметры набора
    pd = scan_result.get("params_detected") or {}
    ch = pd.get("charset") or {}
    prof = (pd.get("plan_profile") or {}).get("profile")
    seen = set()
    den = []
    for d in sorted(denom, key=lambda x: x["before"]):
        if d["before"] not in seen:
            seen.add(d["before"])
            den.append(d)
    # статистика по ВСЕМ источникам: у элементов books[], off[], unassigned_data[] и в общем списке sources[] (в т.ч. «осиротевшие» таблицы)
    for lst in (out_books, off, unassigned):
        for it in lst:
            it.update(src_stats(by_src[it["src"]]))
    in_books = {r["src"] for r in out_books}
    off_by = {o["src"]: o for o in off}
    all_sources = []
    for s0 in sorted(srcs, key=lambda x: (x["firm"], x["period"])):
        has_t = bool(s0.get("has_tables"))
        why = ""
        if not has_t:
            why = "нет таблиц проводок и ордеров"
        elif not s0.get("has_acc"):
            why = "нет плана счетов фирмы (таблица LG_%03d_EMUHACC)" % s0["firm"]
        elif int(s0.get("lines") or 0) == 0:
            why = "в источнике нет проводок"
        v = {"src": s0["src"], "firm": s0["firm"], "period": s0["period"], "in_logo": bool(s0.get("in_logo", True)),
             "has_tables": has_t, "has_plan": bool(s0.get("has_acc")), "assigned": s0["src"] in in_books,
             "can_be_book": not why, "cannot_reason": why, "reasons": (off_by.get(s0["src"]) or {}).get("reasons", [])}
        v.update(src_stats(s0))
        all_sources.append(v)
    params = {"currency_local": pd.get("currency_local"), "currency_report": pd.get("currency_report"),
              "charset": {"ansi": ch.get("ansi") or "", "oem": ch.get("oem") or ""}, "denomination": den, "plan_profile": prof}
    return {"scanned_at": scan_result.get("scanned_at"), "sources_total": len(srcs),
            "sources_in_logo": sum(1 for s in srcs if s.get("in_logo", True)),
            "params": params, "books": out_books, "off": sorted(off, key=lambda o: (o["firm"], o["period"])),
            "gaps_between_labels": gaps, "unassigned_data": unassigned, "sources": all_sources}


def build_proposal(lc=None, progress: Optional[Callable] = None, today: Optional[date] = None) -> dict:
    """scan + transition_stats + propose."""
    sc = scan(lc=lc, progress=progress)
    prof = (sc.get("params_detected") or {}).get("plan_profile", {}).get("profile") or {}
    pairs = candidate_pairs(sc, today)
    if progress:
        progress(1, 1, "", "Проверка переноса остатков между книгами")
    sc["transitions"] = transition_stats(pairs, lc=lc, root_len=int(prof.get("root_len") or 3), balance_roots=list(prof.get("balance_roots") or []))
    return propose(sc, today)
