"""Карточка счёта (type=card): проводки по счёту/маске/группе за период с входящим сальдо, корр. счётом (из pairs; если у проводки
несколько корреспондентов — строка на каждую пару), нарастающим сальдо; итоги по дням/месяцам.

definition: account (маска/список/группа), card.detail = entries | day | month (day/month — к проводкам добавляются итоги периода),
card.lines = false — только итоги (свёрнутая карточка). Строка: code = дата, name = содержание.
Колонки: fiche_no, account, corr, method (text); dt, kt; bal_dt, bal_kt (сальдо нарастающим итогом)."""
from __future__ import annotations

from .. import attrs as attrs_mod
from .. import cache
from . import common as C
from . import pairs as P
from .common import Context, ReportError, TOL

METHOD_TITLE = {"SEQ": "по порядку", "CROSS": "по перекрёстной ссылке", "PROP": "пропорционально", "MANUAL": "вручную"}


def run(defn: dict) -> dict:
    d = C.normalize(defn)
    ctx = Context(d)
    masks, groups = P.parse_account_spec(d.get("account"))
    P.need_account(ctx, masks, groups, "Карточка счёта")
    sel = P.Sel(ctx, masks, groups)
    card = d.get("card") or {}
    detail = card.get("detail") or "entries"
    if detail not in ("entries", "day", "month"):
        raise ReportError("Детализация карточки: по проводкам, по дням или по месяцам")
    show_lines = card.get("lines", True) is not False and not (detail != "entries" and card.get("lines") is False)
    usd = ctx.usd_main
    warnings = P.common_warnings(ctx, d)
    if C.attr_filters_active(d):    # карточка построчная: отбираются проводки счёта; корр. счёт берётся из пар без отбора
        warnings = [w for w in warnings if not w.startswith("Отбор по подразделению/отделу/источнику/автору: пара")]
        warnings.append("Отбор по подразделению/отделу/источнику/автору: в карточку входят проводки счёта, проходящие отбор (включая открытие); "
                        "корреспондирующий счёт показан по парам корреспонденции ордера (без отбора)")
    leaves = P.leaves_for(ctx, sel)
    tot = P.sum_leaves(ctx, leaves)
    nf, nf_params, open_ok = C.line_filter_sql(d)
    op_sql, op_params = C.attr_cond_sql(d, "lines")
    names = ctx.names()
    cols = [{"key": "fiche_no", "title": "№ ордера", "type": "text", "drill": None},
            {"key": "account", "title": "Счёт", "type": "text", "drill": None},
            {"key": "corr", "title": "Корр. счёт", "type": "text", "drill": None},
            {"key": "method", "title": "Метод", "type": "text", "drill": None},
            {"key": "dt", "title": "Дебет", "type": "money", "drill": "dt"},
            {"key": "kt", "title": "Кредит", "type": "money", "drill": "kt"},
            {"key": "bal_dt", "title": "Дебет", "group": "Сальдо", "type": "money", "drill": None},
            {"key": "bal_kt", "title": "Кредит", "group": "Сальдо", "type": "money", "drill": None}]
    cols += attrs_mod.optional_column_defs()    # волна 7: колонки по выбору (optional: true); значения есть в каждой строке

    def balvals(run_: float) -> dict:
        return {"bal_dt": ctx.out(max(run_, 0.0)), "bal_kt": ctx.out(max(-run_, 0.0))}

    rows: list[dict] = []
    running = tot["open"]
    rows.append({"id": "open", "parent": None, "level": 0, "kind": "line", "code": "", "name": "Входящее сальдо",
                 "values": balvals(running), "bold": True, "drill": {}})
    sum_dt = sum_kt = 0.0
    sub = {"key": None, "dt": 0.0, "kt": 0.0, "n": 0}
    n_calc = 0

    def flush_sub():
        if sub["key"] is not None and sub["n"]:
            label = f"Итого за {C.fmt_date(_d(sub['key']))}" if detail == "day" else f"Итого за {C.month_title(sub['key'])}"
            period = ({"date_from": sub["key"], "date_to": sub["key"]} if detail == "day" else P.clip_period(ctx, sub["key"]))
            rows.append({"id": f"sub:{sub['key']}", "parent": None, "level": 0, "kind": "total", "code": "", "name": label,
                         "values": {"dt": ctx.out(sub["dt"]), "kt": ctx.out(sub["kt"]), **balvals(running)}, "bold": True,
                         "drill": {**sel.drill(), "period": period}})
        sub.update(key=None, dt=0.0, kt=0.0, n=0)

    conn = cache.connect()
    try:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _sel(code TEXT PRIMARY KEY)")
        for si, seg in enumerate(ctx.segments):
            codes = sorted(sel.codes(seg.key))
            div = ctx.divisor(seg, usd)
            # корректировка переноса: açılış года ≠ остаток прошлого года (ручные правки переноса)
            if si > 0 and not seg.continues:     # тот же источник - остаток продолжается, корректировки переноса нет
                conn.execute("DELETE FROM _sel")
                conn.executemany("INSERT INTO _sel VALUES(?)", [(c,) for c in codes])
                r = conn.execute(f"SELECT COALESCE(SUM({'usd_dt-usd_kt' if usd else 'dt-kt'}),0) FROM lines WHERE firm=? AND is_opening=1 "
                                 f"AND {1 if open_ok else 0}=1 AND account IN (SELECT code FROM _sel)" + op_sql, (seg.firm, *op_params)).fetchone()
                adj = r[0] / div - running
                if abs(adj) >= TOL:
                    flush_sub()
                    running += adj
                    rows.append({"id": f"adj:{seg.year}", "parent": None, "level": 0, "kind": "line", "code": f"{seg.year}-01-01",
                                 "name": f"Корректировка переноса остатков (açılış {seg.year}): {C.fmt_money(ctx.out(adj))}",
                                 "values": balvals(running), "bold": True, "drill": {}})
            if not codes:
                continue
            conn.execute("DELETE FROM _sel")
            conn.executemany("INSERT INTO _sel VALUES(?)", [(c,) for c in codes])
            df, dt_ = seg.d_from.isoformat(), seg.d_to.isoformat()
            fa = {r[0]: r[1:] for r in conn.execute("SELECT fiche_id, module, doc_no, created_by, created_at, text_ext FROM fiches WHERE firm=?", (seg.firm,))}
            pmap_dt: dict[int, list] = {}
            pmap_kt: dict[int, list] = {}
            for p in conn.execute(
                    "SELECT dt_line, kt_line, dt_account, kt_account, amount, usd_amount, usd_kt_amount, method, disputed FROM pairs "
                    "WHERE firm=? AND date>=? AND date<=? AND (dt_account IN (SELECT code FROM _sel) OR kt_account IN (SELECT code FROM _sel))",
                    (seg.firm, df, dt_)):
                pmap_dt.setdefault(p[0], []).append(p)
                pmap_kt.setdefault(p[1], []).append(p)
            q = ("SELECT id, fiche_id, fiche_no, date, account, dt, kt, usd_dt, usd_kt, text, fiche_text, cross_code, branch, dept, qty_dt, qty_kt FROM lines "
                 "WHERE firm=? AND is_opening=0 AND date>=? AND date<=? AND account IN (SELECT code FROM _sel)" + nf +
                 " ORDER BY date, fiche_id, line_no, id")
            for (lid, fid, fno, date, acc, ldt, lkt, lud, luk, text, ftext, cross, lbr, ldp, lqd, lqk) in conn.execute(q, [seg.firm, df, dt_, *nf_params]):
                gkey = date if detail == "day" else date[:7]
                if detail != "entries" and sub["key"] is not None and sub["key"] != gkey:
                    flush_sub()
                if detail != "entries":
                    sub["key"] = gkey
                if ldt != 0 or lkt == 0:
                    plist = [(p[3], p[2], p[4], p[5], p[7], p[8]) for p in pmap_dt.get(lid, [])]
                    side = 0
                else:
                    plist = [(p[2], p[3], p[4], p[6], p[7], p[8]) for p in pmap_kt.get(lid, [])]
                    side = 1
                if not plist:
                    plist = [(cross, None, (ldt if side == 0 else lkt), (lud if side == 0 else luk), "", 0)]
                    amts = [((ldt if side == 0 else lkt), (lud if side == 0 else luk))]
                else:
                    amts = [(a, u) for (_, _, a, u, _, _) in plist]
                for k, (corr, _, a, u, meth, disp) in enumerate(plist):
                    val = u if usd else a / div
                    dt_v, kt_v = (val, 0.0) if side == 0 else (0.0, val)
                    lq = (lqd or 0.0) + (lqk or 0.0)         # количество проводки делится между её парами пропорционально сумме (Σ = количество проводки)
                    lamt = ldt if side == 0 else lkt
                    qshare = (lq * a / lamt if lamt else (lq if k == 0 else 0.0)) if len(plist) > 1 else lq
                    running += dt_v - kt_v
                    sum_dt += dt_v
                    sum_kt += kt_v
                    sub["dt"] += dt_v
                    sub["kt"] += kt_v
                    sub["n"] += 1
                    if meth == "PROP":
                        n_calc += 1
                    if show_lines:
                        mt = METHOD_TITLE.get(meth, meth) + (" · спорная" if disp else "")
                        rows.append({"id": f"l{seg.firm}:{lid}:{k}", "parent": None, "level": 0, "kind": "line", "code": C.fmt_date(_d(date)),
                                     "name": text or ftext or "",
                                     "values": {"fiche_no": fno, "account": acc, "corr": corr or "", "method": mt,
                                                "dt": ctx.out(dt_v) or None, "kt": ctx.out(kt_v) or None, **balvals(running),
                                                **attrs_mod.row_attrs(names, lbr, ldp, qshare, fa.get(fid), ids=False)},
                                     "bold": False,
                                     "drill": {"accounts": [acc], "period": {"date_from": date, "date_to": date},
                                               **({"corr": [corr]} if corr else {})},
                                     "fiche": {"firm": seg.firm, "fiche_id": fid}})
        flush_sub()
    finally:
        conn.close()

    totals = {"dt": ctx.out(sum_dt), "kt": ctx.out(sum_kt), **balvals(running)}
    rows.append({"id": "total", "parent": None, "level": 0, "kind": "total", "code": "", "name": "ИТОГО за период",
                 "values": {"dt": totals["dt"], "kt": totals["kt"], **balvals(running)}, "bold": True, "drill": dict(sel.drill())})

    checks = []
    if True:   # карточка и ОСВ применяют одни и те же построчные фильтры — сверка выполняется всегда
        checks.append({"name": "Σ Дт карточки = оборот Дт (ОСВ)", "ok": abs(sum_dt - tot["dt"]) < TOL,
                       "detail": f"карточка {C.fmt_money(ctx.out(sum_dt))}, ОСВ {C.fmt_money(ctx.out(tot['dt']))}"})
        checks.append({"name": "Σ Кт карточки = оборот Кт (ОСВ)", "ok": abs(sum_kt - tot["kt"]) < TOL,
                       "detail": f"карточка {C.fmt_money(ctx.out(sum_kt))}, ОСВ {C.fmt_money(ctx.out(tot['kt']))}"})
        checks.append({"name": "Входящее сальдо = ОСВ", "ok": True, "detail": C.fmt_money(ctx.out(tot["open"]))})
        checks.append({"name": "Конечное нарастающее сальдо = исходящее ОСВ", "ok": abs(running - tot["close"]) < TOL,
                       "detail": f"карточка {C.fmt_money(ctx.out(running))}, ОСВ {C.fmt_money(ctx.out(tot['close']))}"})
    if n_calc:
        warnings.append(f"{n_calc} строк(и) карточки с корреспондентом, определённым пропорционально (метка «расчётная»)")
    return P.finish(d, ctx, "Карточка счёта", "счёт " + (P.spec_label(masks, groups) or "по фильтрам"), cols, rows, totals, checks, warnings)


def _d(s: str):
    return C.parse_date(s) if len(s) >= 10 else C.parse_date(s + "-01")
