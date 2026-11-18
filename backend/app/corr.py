"""Расчёт пар корреспонденции Дт/Кт (таблица pairs кэша) по §2 контракта.

Пара — на уровне проводок: (строка Дт, строка Кт, сумма). Это позволяет карточке счёта показывать корреспондента по каждой строке,
а любым агрегатам (шахматка, анализ) — просто группировать pairs по счетам.

Лестница (внутри ордера, açılış исключён, суммы — целые копейки как в Logo):
  SEQ   ордер режется на сбалансированные подгруппы по нулю нарастающего Σ(Дт−Кт) в порядке (line_no, id); подгруппа 1:1, 1:N, N:1
        разбирается однозначно.
  CROSS подгруппа N:M — по взаимному CROSSCODE (Дт-строка (счёт A, cross B) ↔ Кт-строка (счёт B, cross A)), группа берётся целиком при
        равных суммах, иначе — по равным суммам; остаток, если он 1:N/N:1 — однозначно (тоже CROSS).
  PROP  остаток N:M пропорционально d_i·c_j/T (целочисленное 2D-округление, суммы строк и столбцов сходятся точно) — «расчётная».
  disputed=1: SEQ противоречит заполненному CROSSCODE (у 1:1 — любой из двух, у 1:N/N:1 — у строк множественной стороны).
        В спорном ордере приоритет у CROSSCODE: если взаимные группы целиком покрывают ордер и сбалансированы — пары берутся из них
        (method=CROSS, весь ордер disputed=1), иначе остаётся SEQ, помечаются только спорные подгруппы.
  MANUAL ручное закрепление (settings.db: pair_overrides) — заменяет расчёт ордера.

rebuild_firm() вызывается из cache.sync_firm внутри транзакции фирмы (без commit)."""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from itertools import groupby

from . import dsparams

METHODS = ("SEQ", "CROSS", "PROP", "MANUAL")

PAIR_COLS = "firm,year,fiche_id,date,dt_account,kt_account,amount,usd_amount,method,disputed,is_closing,dt_line,kt_line,usd_kt_amount"
PAIR_INSERT = f"INSERT INTO pairs({PAIR_COLS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"


class Ln:
    __slots__ = ("id", "acc", "amt", "side", "cross", "usd")

    def __init__(self, id_, acc, amt, side, cross, usd):
        self.id, self.acc, self.amt, self.side, self.cross, self.usd = id_, acc, amt, side, cross, usd


# ---------------------------------------------------------------------------------------------
# ядро алгоритма (чистые функции, без БД)
# ---------------------------------------------------------------------------------------------

def _nw(ds: list[Ln], cs: list[Ln], method: str, disp: int, out: list) -> None:
    """Последовательное (северо-западный угол) покрытие: суммы строк и столбцов точны, суммы положительны."""
    i = j = 0
    rd = ds[0].amt if ds else 0
    rc = cs[0].amt if cs else 0
    while i < len(ds) and j < len(cs):
        x = min(rd, rc)
        if x:
            out.append([ds[i], cs[j], x, method, disp])
        rd -= x
        rc -= x
        if rd == 0:
            i += 1
            rd = ds[i].amt if i < len(ds) else 0
        if rc == 0:
            j += 1
            rc = cs[j].amt if j < len(cs) else 0


def _prop(ds: list[Ln], cs: list[Ln], method: str, disp: int, out: list) -> None:
    """Пропорциональное распределение d_i·c_j/T; целочисленное 2D-округление: F(i,j)=round(D_i·C_j/T), M=вторая разность.
    Σ_j M_ij = d_i и Σ_i M_ij = c_j точно. Если округление дало отрицательную копейку — северо-западный угол."""
    T = sum(d.amt for d in ds)
    if T == 0 or not ds or not cs:
        return
    prevF = [0] * (len(cs) + 1)
    D = 0
    res = []
    for d in ds:
        D += d.amt
        C = 0
        rowF = [0] * (len(cs) + 1)
        for j, c in enumerate(cs, 1):
            C += c.amt
            rowF[j] = (2 * D * C + T) // (2 * T)
            m = rowF[j] - prevF[j] - rowF[j - 1] + prevF[j - 1]
            if m < 0:
                _nw(ds, cs, method, disp, out)
                return
            if m:
                res.append([d, c, m, method, disp])
        prevF = rowF
    out.extend(res)


def _exact(ds: list[Ln], cs: list[Ln], method: str, disp: int, out: list) -> None:
    """1:1, 1:N, N:1 — однозначно."""
    if len(ds) == 1:
        d = ds[0]
        for c in cs:
            out.append([d, c, c.amt, method, disp])
    else:
        c = cs[0]
        for d in ds:
            out.append([d, c, d.amt, method, disp])


def seq_groups(lines: list[Ln]) -> list[list[Ln]]:
    """Сбалансированные подгруппы по нулю нарастающего итога; односторонние подгруппы (сторно одной стороны) склеиваются с соседом."""
    groups: list[list[Ln]] = []
    cur: list[Ln] = []
    cum = 0
    for l in lines:
        if l.amt == 0:
            continue
        cur.append(l)
        cum += l.amt if l.side == 0 else -l.amt
        if cum == 0:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    merged: list[list[Ln]] = []
    carry: list[Ln] = []
    for g in groups:
        g = carry + g
        nd = sum(1 for l in g if l.side == 0)
        if nd == 0 or nd == len(g):
            carry = g
            continue
        merged.append(g)
        carry = []
    if carry:
        if merged:
            merged[-1] = merged[-1] + carry
        else:
            merged.append(carry)
    return merged


def _seq_conflict(ds: list[Ln], cs: list[Ln]) -> bool:
    """SEQ-пара (1:1 / 1:N / N:1) противоречит заполненному CROSSCODE."""
    if len(ds) == 1 and len(cs) == 1:
        d, c = ds[0], cs[0]
        return (d.cross != "" and d.cross != c.acc) or (c.cross != "" and c.cross != d.acc)
    if len(ds) == 1:
        a = ds[0].acc
        return any(c.cross != "" and c.cross != a for c in cs)
    a = cs[0].acc
    return any(d.cross != "" and d.cross != a for d in ds)


def cross_groups(ds: list[Ln], cs: list[Ln], out: list, disp: int = 0) -> tuple[list[Ln], list[Ln]]:
    """Взаимные группы CROSSCODE. Возвращает непокрытые строки (rest_d, rest_c); найденные пары дописывает в out (method=CROSS)."""
    dk: dict[tuple, list[Ln]] = {}
    ck: dict[tuple, list[Ln]] = {}
    for d in ds:
        if d.cross:
            dk.setdefault((d.acc, d.cross), []).append(d)
    for c in cs:
        if c.cross:
            ck.setdefault((c.cross, c.acc), []).append(c)
    used: set[int] = set()
    for key, dl in dk.items():
        cl = ck.get(key)
        if not cl:
            continue
        if sum(x.amt for x in dl) == sum(x.amt for x in cl):
            if len(dl) == 1 or len(cl) == 1:
                _exact(dl, cl, "CROSS", disp, out)
            else:
                _nw(dl, cl, "CROSS", disp, out)   # одна и та же пара счетов: распределение между строками не меняет итог по счетам
            used.update(id(x) for x in dl)
            used.update(id(x) for x in cl)
        else:
            by_amt: dict[int, list[Ln]] = {}
            for c in cl:
                by_amt.setdefault(c.amt, []).append(c)
            for d in dl:
                lst = by_amt.get(d.amt)
                if lst:
                    c = lst.pop()
                    out.append([d, c, d.amt, "CROSS", disp])
                    used.add(id(d))
                    used.add(id(c))
    return [d for d in ds if id(d) not in used], [c for c in cs if id(c) not in used]


def _resolve_nm(ds: list[Ln], cs: list[Ln], disp: int, out: list) -> None:
    rd, rc = cross_groups(ds, cs, out, disp)
    if not rd and not rc:
        return
    if not rd or not rc:      # не должно случаться (подгруппа сбалансирована) — оставляем как есть, инвариант покажет
        return
    if len(rd) == 1 or len(rc) == 1:
        _exact(rd, rc, "CROSS", disp, out)
    else:
        _prop(rd, rc, "PROP", disp, out)


def compute_fiche(lines: list[Ln]) -> list[list]:
    """Пары ордера: список [dt_line, kt_line, amount, method, disputed]."""
    out: list[list] = []
    conflicts: list[tuple[int, int]] = []     # диапазоны [from, to) в out для спорных подгрупп
    for g in seq_groups(lines):
        ds = [l for l in g if l.side == 0]
        cs = [l for l in g if l.side == 1]
        if len(ds) == 1 or len(cs) == 1:
            a = len(out)
            _exact(ds, cs, "SEQ", 0, out)
            if _seq_conflict(ds, cs):
                conflicts.append((a, len(out)))
        else:
            _resolve_nm(ds, cs, 0, out)
    if conflicts:
        alt: list[list] = []
        ds = [l for l in lines if l.side == 0 and l.amt != 0]
        cs = [l for l in lines if l.side == 1 and l.amt != 0]
        rd, rc = cross_groups(ds, cs, alt, 1)
        if not rd and not rc and alt:
            return alt
        for a, b in conflicts:
            for p in out[a:b]:
                p[4] = 1
    return out


# ---------------------------------------------------------------------------------------------
# ручное закрепление
# ---------------------------------------------------------------------------------------------

def _take(q: deque, amt: int, last: bool = False) -> list[tuple[Ln, int]]:
    """Снимает со строк очереди сумму amt (со знаком). Строка противоположного знака берётся целиком (сторно), сумма при этом
    увеличивается, пока не наберётся нужная нетто-сумма; итог: Σ взятого = amt. last=True - последняя пара счёта: забирает все
    остатки строк (их нетто-сумма равна amt), чтобы каждая строка была распределена полностью."""
    res = []
    if last:
        while q:
            ln, rem = q.popleft()
            if rem:
                res.append((ln, rem))
        return res
    while amt != 0 and q:
        ln, rem = q[0]
        if rem == 0:
            q.popleft()
            continue
        if (rem > 0) == (amt > 0):
            x = rem if abs(rem) <= abs(amt) else amt
        else:
            x = rem
        res.append((ln, x))
        amt -= x
        if x == rem:
            q.popleft()
        else:
            q[0][1] = rem - x
    return res


def _match(dch: list, kch: list) -> list[tuple[Ln, Ln, int]]:
    """Сопоставляет куски Дт-строк и Кт-строк одной пары (равные нетто-суммы, возможны отрицательные куски сторно)."""
    out = []
    i = j = 0
    dl, da = dch[0] if dch else (None, 0)
    kl, ka = kch[0] if kch else (None, 0)
    while dl is not None and kl is not None:
        if (da > 0) == (ka > 0):
            x = da if abs(da) <= abs(ka) else ka
        else:
            x = da          # куски разных знаков: Дт-кусок идёт целиком, остаток Кт-куска сдвигается
        if x:
            out.append((dl, kl, x))
        da -= x
        ka -= x
        if da == 0:
            i += 1
            dl, da = dch[i] if i < len(dch) else (None, 0)
        if ka == 0:
            j += 1
            kl, ka = kch[j] if j < len(kch) else (None, 0)
    # остатки (куски сторно, суммирующиеся в ноль) - на последнюю строку противоположной стороны, чтобы каждая строка была распределена полностью
    if dl is not None and kch:
        out.append((dl, kch[-1][0], da))
        out.extend((l2, kch[-1][0], x2) for l2, x2 in dch[i + 1:])
    if kl is not None and dch:
        out.append((dch[-1][0], kl, ka))
        out.extend((dch[-1][0], l2, x2) for l2, x2 in kch[j + 1:])
    return [t for t in out if t[2]]


def _margins(lines: list[Ln]) -> dict:
    want = {0: {}, 1: {}}
    for l in lines:
        want[l.side][l.acc] = want[l.side].get(l.acc, 0) + l.amt
    return want


def override_amounts(lines: list[Ln], pairs: list[dict], divisor: int = 1) -> tuple[list[int] | None, str | None]:
    """Переводит суммы ручного закрепления (манаты как показывает интерфейс: после деноминации, 2 знака) во внутренние копейки Logo так,
    чтобы суммы по каждому счёту и стороне СОВПАДАЛИ с оборотами ордера точно. Для книг с делителем деноминации (divisor>1) суммы с точностью до цента
    пересчитываются: округлённые значения сводятся к точным остаткам по остовному лесу графа «счёт Дт - счёт Кт».
    Суммы пар могут быть отрицательными (сторно). Возвращает (список сумм по парам, None) либо (None, текст ошибки)."""
    if not pairs:
        return None, "Список пар пуст"
    want = _margins(lines)
    amts = []
    for p in pairs:
        try:
            a = float(p["amount"])
        except Exception:
            return None, "Некорректная сумма пары"
        if abs(a) < 1e-9:
            return None, "Суммы пар не могут быть нулевыми"
        amts.append(a)
    X = [int(round(a * 100 * divisor)) for a in amts]
    if any(x == 0 for x in X):
        return None, "Сумма пары слишком мала"
    got = {0: {}, 1: {}}
    cnt = {0: {}, 1: {}}
    for p, x in zip(pairs, X):
        got[0][p["dt_account"]] = got[0].get(p["dt_account"], 0) + x
        got[1][p["kt_account"]] = got[1].get(p["kt_account"], 0) + x
        cnt[0][p["dt_account"]] = cnt[0].get(p["dt_account"], 0) + 1
        cnt[1][p["kt_account"]] = cnt[1].get(p["kt_account"], 0) + 1
    for side, nm in ((0, "дебету"), (1, "кредиту")):
        for acc in set(want[side]) | set(got[side]):
            w, g = want[side].get(acc, 0), got[side].get(acc, 0)
            tol = 0 if divisor == 1 else divisor * (0.5 * cnt[side].get(acc, 0) + 0.5)    # округление показанных сумм до цента
            if abs(w - g) > tol:
                return None, (f"Сумма пар по {nm} счёта {acc} ({g / divisor / 100:,.2f}) не равна обороту счёта в ордере ({w / divisor / 100:,.2f})")
    if divisor == 1:
        return X, None
    # точные остатки: остовный лес графа счетов, невязка листа уходит на его ребро
    adj: dict[tuple, list[int]] = {}
    for i, p in enumerate(pairs):
        adj.setdefault((0, p["dt_account"]), []).append(i)
        adj.setdefault((1, p["kt_account"]), []).append(i)
    resid = {node: want[node[0]].get(node[1], 0) - got[node[0]].get(node[1], 0) for node in adj}
    seen: set = set()
    parent_edge: dict = {}
    comps = []
    for root in adj:
        if root in seen:
            continue
        seen.add(root)
        stack, comp = [root], []
        while stack:
            n = stack.pop()
            comp.append(n)
            for ei in adj[n]:
                p = pairs[ei]
                m = (1, p["kt_account"]) if n[0] == 0 else (0, p["dt_account"])
                if m not in seen:
                    seen.add(m)
                    parent_edge[m] = (ei, n)
                    stack.append(m)
        comps.append((root, comp))
    for root, comp in comps:
        for n in reversed(comp):
            if n == root:
                continue
            ei, par = parent_edge[n]
            d = resid[n]
            if d:
                X[ei] += d
                resid[par] -= d
                resid[n] = 0
        if resid[root]:
            return None, ("Пары разбивают ордер на группы, не сбалансированные точно в единицах Logo: проверьте, что каждая связанная группа "
                          f"счетов (например {root[1]}) имеет равные суммы по Дт и Кт")
    for x, a in zip(X, amts):
        if x == 0 or (x > 0) != (a > 0):
            return None, "Суммы пар слишком малы для точного пересчёта к старым единицам - укажите пары крупнее или объедините их"
    return X, None


def validate_override(lines: list[Ln], pairs: list[dict], divisor: int = 1) -> str | None:
    """None - корректно; иначе текст ошибки (по-русски). Суммы пар по каждому счёту и стороне должны равняться оборотам ордера."""
    return override_amounts(lines, pairs, divisor)[1]


def apply_override(lines: list[Ln], pairs: list[dict], divisor: int = 1) -> list[list]:
    X, err = override_amounts(lines, pairs, divisor)
    if err:
        raise ValueError(err)
    dq: dict[str, deque] = {}
    kq: dict[str, deque] = {}
    for l in lines:
        if l.amt:
            (dq if l.side == 0 else kq).setdefault(l.acc, deque()).append([l, l.amt])
    out: list[list] = []
    last_d = {p["dt_account"]: i for i, p in enumerate(pairs)}
    last_k = {p["kt_account"]: i for i, p in enumerate(pairs)}
    for i, (p, amt) in enumerate(zip(pairs, X)):
        dch = _take(dq[p["dt_account"]], amt, last_d[p["dt_account"]] == i)
        kch = _take(kq[p["kt_account"]], amt, last_k[p["kt_account"]] == i)
        for dl, kl, x in _match(dch, kch):
            out.append([dl, kl, x, "MANUAL", 0])
    return out


# ---------------------------------------------------------------------------------------------
# строки pairs
# ---------------------------------------------------------------------------------------------

def _usd_shares(pairs: list[list]) -> list[tuple[int, int]]:
    """USD-доли пар: по каждой строке — пропорционально сумме пары, остаток на последнюю пару строки (Σ = REPORTNET строки точно)."""
    res = [[0, 0] for _ in pairs]
    for side, idx in ((0, 0), (1, 1)):
        by: dict[int, list[int]] = {}
        for i, p in enumerate(pairs):
            by.setdefault(id(p[side]), []).append(i)
        for ids in by.values():
            ln: Ln = pairs[ids[0]][side]
            if ln.usd == 0 or ln.amt == 0:
                continue
            if len(ids) == 1:
                res[ids[0]][idx] = ln.usd
                continue
            cumA = cumS = 0
            for k, i in enumerate(ids):
                cumA += pairs[i][2]
                tgt = ln.usd if k == len(ids) - 1 else int(round(ln.usd * cumA / ln.amt))
                res[i][idx] = tgt - cumS
                cumS = tgt
    return [(a, b) for a, b in res]


def _rows(firm, year, fiche_id, date, closing, pairs: list[list]) -> list[tuple]:
    sh = _usd_shares(pairs)
    return [(firm, year, fiche_id, date, p[0].acc, p[1].acc, p[2], sh[i][0], p[3], p[4], closing, p[0].id, p[1].id, sh[i][1])
            for i, p in enumerate(pairs)]


LINE_SQL = ("SELECT id, fiche_id, date, account, dt, kt, usd_dt, usd_kt, cross_code, is_closing FROM lines "
            "WHERE firm=? AND is_opening=0 {extra} ORDER BY fiche_id, line_no, id")


def _to_lines(rows) -> list[Ln]:
    out = []
    for r in rows:
        if r[4] != 0 or r[5] == 0:
            out.append(Ln(r[0], r[3], r[4], 0, r[8] or "", r[6]))
        if r[5] != 0:
            out.append(Ln(r[0], r[3], r[5], 1, r[8] or "", r[7]))
    return out


def load_overrides(firm: int) -> dict[int, list[dict]]:
    from . import store
    c = store.connect()
    try:
        res = {}
        for fid, payload in c.execute("SELECT fiche_id, payload FROM pair_overrides WHERE firm=?", (firm,)):
            try:
                res[fid] = json.loads(payload)["pairs"]
            except Exception:
                pass
        return res
    finally:
        c.close()


def _divisor(conn, firm: int) -> int:
    from . import cache
    return cache.divisor_of_src(firm) or 1


def _build_fiche_rows(firm, year, fid, rows, ov, divisor: int = 1) -> list[tuple]:
    lines = _to_lines(rows)
    pairs = None
    if ov is not None and validate_override(lines, ov, divisor) is None:
        pairs = apply_override(lines, ov, divisor)
    if pairs is None:
        pairs = compute_fiche(lines)
    closing = max(r[9] for r in rows)
    return _rows(firm, year, fid, rows[0][2], closing, pairs)


def rebuild_firm(conn, firm: int, year: int) -> None:
    """Пересчитывает все пары фирмы. Не делает commit."""
    conn.execute("DELETE FROM pairs WHERE firm=?", (firm,))
    ovs = load_overrides(firm)
    div = _divisor(conn, firm)
    rows = conn.execute(LINE_SQL.format(extra=""), (firm,)).fetchall()
    batch: list[tuple] = []
    for fid, grp in groupby(rows, key=lambda r: r[1]):
        batch += _build_fiche_rows(firm, year, fid, list(grp), ovs.get(fid), div)
        if len(batch) > 50000:
            conn.executemany(PAIR_INSERT, batch)
            batch = []
    if batch:
        conn.executemany(PAIR_INSERT, batch)


def rebuild_fiche(conn, firm: int, fiche_id: int) -> None:
    """Пересчёт одного ордера (после изменения ручного закрепления). Не делает commit."""
    from . import cache
    y = (cache.label_of_src(firm),) if cache.label_of_src(firm) else None
    rows = conn.execute(LINE_SQL.format(extra="AND fiche_id=?"), (firm, fiche_id)).fetchall()
    conn.execute("DELETE FROM pairs WHERE firm=? AND fiche_id=?", (firm, fiche_id))
    if not rows or y is None:
        return
    ov = load_overrides(firm).get(fiche_id)
    conn.executemany(PAIR_INSERT, _build_fiche_rows(firm, y[0], fiche_id, rows, ov, _divisor(conn, firm)))


def rebuild_all(firms: list[int] | None = None, progress=None) -> dict[int, float]:
    """Пересчёт пар из уже загруженных проводок кэша (без обращения к Logo). Возвращает {firm: секунды}."""
    import time
    from . import cache
    conn = cache.connect()
    conn.isolation_level = None
    res = {}
    try:
        if firms is None:
            firms = [r[0] for r in conn.execute("SELECT src FROM sources WHERE synced_at IS NOT NULL ORDER BY src")]
            order = {s: i for i, s in enumerate(cache.main_srcs_ordered())}
            firms.sort(key=lambda s: (order.get(s, 10 ** 6), s))
        for f in firms:
            t = time.time()
            y = cache.label_of_src(f)
            conn.execute("BEGIN IMMEDIATE")
            try:
                rebuild_firm(conn, f, y)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            res[f] = round(time.time() - t, 1)
            if progress:
                progress(f, res[f])
    finally:
        conn.close()
    return res


# ---------------------------------------------------------------------------------------------
# API-функции: статистика, спорные, ручное закрепление
# ---------------------------------------------------------------------------------------------

def _firm_years(conn, year: int | None) -> list[dict]:
    """Основные книги метки (или всех меток): [{firm (= src), year (метка), divisor}] по возрастанию дат."""
    from . import books
    bs = sorted(books.main_books(), key=lambda b: (b.label, b.date_from, b.id))
    if year:
        bs = [b for b in bs if b.label == year]
    return [{"firm": b.src, "year": b.label, "divisor": b.divisor} for b in bs]


def stats(year: int | None = None) -> dict:
    from . import cache
    conn = cache.connect()
    try:
        years = []
        tot_m: dict[str, dict] = {}
        tot_all = tot_dis = tot_fiches = 0.0
        for fy in _firm_years(conn, year):
            div = fy["divisor"] or 1
            ms = {m: {"amount": 0.0, "share": 0.0, "count": 0} for m in METHODS}
            for m, a, n in conn.execute("SELECT method, SUM(amount), COUNT(*) FROM pairs WHERE firm=? GROUP BY method", (fy["firm"],)):
                ms[m] = {"amount": round(a / div / 100, 2), "share": 0.0, "count": n}
            total = sum(v["amount"] for v in ms.values())
            for v in ms.values():
                v["share"] = round(v["amount"] / total, 6) if total else 0.0
            r = conn.execute("SELECT COUNT(DISTINCT fiche_id), COALESCE(SUM(amount),0) FROM pairs WHERE firm=? AND disputed=1", (fy["firm"],)).fetchone()
            nf = conn.execute("SELECT COUNT(DISTINCT fiche_id) FROM pairs WHERE firm=?", (fy["firm"],)).fetchone()[0]
            years.append({"year": fy["year"], "firm": fy["firm"], "total": round(total, 2), "methods": ms, "disputed": r[0],
                          "disputed_amount": round(r[1] / div / 100, 2), "fiches": nf})
            for m, v in ms.items():
                t = tot_m.setdefault(m, {"amount": 0.0, "share": 0.0, "count": 0})
                t["amount"] += v["amount"]
                t["count"] += v["count"]
            tot_all += total
            tot_dis += r[0]
            tot_fiches += nf
        for v in tot_m.values():
            v["amount"] = round(v["amount"], 2)
            v["share"] = round(v["amount"] / tot_all, 6) if tot_all else 0.0
        return {"total": round(tot_all, 2), "methods": tot_m, "disputed": int(tot_dis), "fiches": int(tot_fiches), "years": years,
                "note": "Суммы — в " + dsparams.local_forms()[1] + " (после деноминации); способы: по порядку строк, по перекрёстной ссылке проводок, "
                        "пропорционально (расчётная), вручную (закреплено пользователем)"}
    finally:
        conn.close()


def disputed(year: int | None = None, limit: int = 1000) -> list[dict]:
    from . import cache
    conn = cache.connect()
    try:
        out = []
        for fy in _firm_years(conn, year):
            div = fy["divisor"] or 1
            q = ("SELECT p.fiche_id, MIN(p.date), SUM(CASE WHEN p.disputed=1 THEN p.amount ELSE 0 END), COUNT(*), "
                 "GROUP_CONCAT(DISTINCT p.method) FROM pairs p WHERE p.firm=? GROUP BY p.fiche_id HAVING MAX(p.disputed)=1")
            for fid, date, amt, n, meths in conn.execute(q, (fy["firm"],)).fetchall():
                f = conn.execute("SELECT fiche_no, fiche_text FROM lines WHERE firm=? AND fiche_id=? LIMIT 1", (fy["firm"], fid)).fetchone()
                has_ov = False
                out.append({"firm": fy["firm"], "year": fy["year"], "fiche_id": fid, "fiche_no": f["fiche_no"] if f else "", "date": date,
                            "text": f["fiche_text"] if f else "", "amount": round(amt / div / 100, 2), "pairs": n, "methods": meths,
                            "resolved_by": "CROSS" if meths and "CROSS" in meths.split(",") and "SEQ" not in meths.split(",") else "SEQ",
                            "manual": has_ov})
        out.sort(key=lambda r: -r["amount"])
        return out[:limit]
    finally:
        conn.close()


def get_override(firm: int, fiche_id: int) -> dict | None:
    from . import store
    c = store.connect()
    try:
        r = c.execute("SELECT payload FROM pair_overrides WHERE firm=? AND fiche_id=?", (firm, fiche_id)).fetchone()
        return json.loads(r[0]) if r else None
    finally:
        c.close()


def set_override(firm: int, fiche_id: int, pairs: list[dict], note: str = "") -> dict:
    """Сохраняет ручное закрепление ордера и пересчитывает его пары. ValueError с русским текстом — при ошибке данных."""
    from . import cache, store
    conn = cache.connect()
    try:
        if not cache.label_of_src(firm):
            raise ValueError(f"Фирма {firm} не найдена")
        div = cache.divisor_of_src(firm) or 1
        rows = conn.execute(LINE_SQL.format(extra="AND fiche_id=?"), (firm, fiche_id)).fetchall()
        if not rows:
            raise ValueError("Ордер не найден или это açılış-ордер (в пары не входит)")
        clean = [{"dt_account": str(p.get("dt_account", "")).strip(), "kt_account": str(p.get("kt_account", "")).strip(),
                  "amount": float(p.get("amount", 0))} for p in pairs or []]
        err = validate_override(_to_lines(rows), clean, div)
        if err:
            raise ValueError(err)
        s = store.connect()
        try:
            s.execute("INSERT OR REPLACE INTO pair_overrides(firm, fiche_id, payload) VALUES(?,?,?)",
                      (firm, fiche_id, json.dumps({"pairs": clean, "note": note, "saved_at": store.now()}, ensure_ascii=False)))
            s.commit()
        finally:
            s.close()
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            rebuild_fiche(conn, firm, fiche_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"ok": True, "firm": firm, "fiche_id": fiche_id, "pairs": len(clean)}
    finally:
        conn.close()


def delete_override(firm: int, fiche_id: int) -> bool:
    from . import cache, store
    s = store.connect()
    try:
        n = s.execute("DELETE FROM pair_overrides WHERE firm=? AND fiche_id=?", (firm, fiche_id)).rowcount
        s.commit()
    finally:
        s.close()
    conn = cache.connect()
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            rebuild_fiche(conn, firm, fiche_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return n > 0
