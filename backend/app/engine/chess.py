"""Шахматная ведомость (type=chess): матрица Дт (строки) × Кт (графы). Оси независимы: chess.rows / chess.cols =
{kind: account, level: 0..4} либо {kind: classifier, id, depth}. Пустые строки и графы скрыты. Итоги по строкам (= оборот Дт ОСВ) и по графам
(= оборот Кт ОСВ); строка «Итого». Ячейки, где есть расчётные (PROP) пары, перечислены в row.calc (ключи граф).
Фильтры filters.accounts/groups — подматрица (обе стороны пары в выбранных счетах). Ключи граф: c:<ключ>, итог — total_dt.
Колонка несёт drill='dt' и corr / corr_groups (для расшифровки ячейки: строки.drill + колонка.corr)."""
from __future__ import annotations

from . import common as C
from . import pairs as P
from .common import Context, ReportError, TOL


def run(defn: dict) -> dict:
    d = C.normalize(defn)
    ctx = Context(d)
    usd = ctx.usd_main
    ch = d.get("chess") or {}
    rax = P.Axis(ctx, ch.get("rows") or {"kind": "account", "level": 0})
    cax = P.Axis(ctx, ch.get("cols") or {"kind": "account", "level": 0})
    warnings = P.common_warnings(ctx, d)
    sel = P.Sel(ctx)
    restricted = bool(d["filters"]["accounts"] or d["filters"]["groups"])

    cell: dict[tuple[str, str], float] = {}
    calc: set[tuple[str, str]] = set()
    rkeys: dict[str, P.Key] = {}
    ckeys: dict[str, P.Key] = {}
    col_kt: dict[str, float] = {}   # итог графы со стороны Кт (для USD — usd_kt_amount)
    for seg, rows in P.fetch_pairs(ctx):
        s = sel.codes(seg.key)
        for dt_acc, kt_acc, _, am, ud, uk, pr, _ in rows:
            if restricted and (dt_acc not in s or kt_acc not in s):
                continue
            rk = rax.key(seg.key, dt_acc)
            ck = cax.key(seg.key, kt_acc)
            rkeys[rk.sid] = rk
            ckeys[ck.sid] = ck
            v = ud if usd else am
            cell[(rk.sid, ck.sid)] = cell.get((rk.sid, ck.sid), 0.0) + v
            col_kt[ck.sid] = col_kt.get(ck.sid, 0.0) + (uk if usd else am)
            if pr:
                calc.add((rk.sid, ck.sid))

    row_tot: dict[str, float] = {}
    col_tot: dict[str, float] = {}
    for (r, c), v in cell.items():
        row_tot[r] = row_tot.get(r, 0.0) + v
        col_tot[c] = col_tot.get(c, 0.0) + v
    # пустые оси скрываем: остаются строки/графы с хотя бы одной ненулевой ячейкой
    nz_r = {r for (r, c), v in cell.items() if abs(v) >= 0.5}
    nz_c = {c for (r, c), v in cell.items() if abs(v) >= 0.5}
    rsids = sorted(nz_r, key=lambda s: rkeys[s].sort)
    csids = sorted(nz_c, key=lambda s: ckeys[s].sort)
    if len(rsids) * len(csids) > 4_000_000:
        raise ReportError("Матрица слишком велика — выберите более крупный уровень осей или добавьте фильтры")

    cols: list[dict] = []
    for sid in csids:
        k = ckeys[sid]
        c = {"key": f"c:{sid}", "title": k.code or k.name, "group": "Кредит счетов",
             "type": "money", "drill": "dt", "name": cax.name_of(k)}
        c.update(cax.drill(k))
        cols.append(c)
    cols.append({"key": "total_dt", "title": "Итого Дт", "group": "Итого", "type": "money", "drill": "dt"})

    by_row: dict[str, dict[str, float]] = {}
    for (r, c), v in cell.items():
        by_row.setdefault(r, {})[c] = v
    rows: list[dict] = []
    for sid in rsids:
        k = rkeys[sid]
        vals = {}
        for c in csids:
            v = by_row.get(sid, {}).get(c, 0.0)
            vals[f"c:{c}"] = P.out_val(ctx, v)
        vals["total_dt"] = P.out_val(ctx, row_tot.get(sid, 0.0))
        row = {"id": f"r:{sid}", "parent": None, "level": 0, "kind": "account", "code": k.code, "name": rax.name_of(k), "values": vals,
               "bold": False, "drill": {**({"accounts": [k.sid]} if rax.kind == "account" else {}),
                                        **({"group": {"classifier": rax.cid, "group": int(k.sid.split(":")[1])}}
                                           if rax.kind == "classifier" and k.sid.startswith("g") else {})}}
        cl = [f"c:{c}" for c in csids if (sid, c) in calc]
        if cl:
            row["calc"] = cl
        rows.append(row)
    tvals = {f"c:{c}": P.out_val(ctx, col_tot.get(c, 0.0)) for c in csids}
    grand = sum(row_tot.values())
    tvals["total_dt"] = P.out_val(ctx, grand)
    rows.append({"id": "total", "parent": None, "level": 0, "kind": "total", "code": "", "name": "Итого Кт", "values": tvals, "bold": True,
                 "drill": {}})
    totals = {k: v for k, v in tvals.items()}

    # ---- контроли: сверка с ОСВ (путь через lines) -----------------------------------------------------
    checks: list[dict] = []
    sel_all = P.Sel(ctx)
    leaves = P.leaves_for(ctx, sel_all)
    osv_dt: dict[str, float] = {}
    osv_kt: dict[str, float] = {}
    for (y, c), a in leaves.items():
        v = a.usd if usd else a.tmt
        osv_dt[rax.key(y, c).sid] = osv_dt.get(rax.key(y, c).sid, 0.0) + v[1]
        osv_kt[cax.key(y, c).sid] = osv_kt.get(cax.key(y, c).sid, 0.0) + v[2]
    if not restricted and not C.line_filters_active(d):
        bad_r = [s for s in set(osv_dt) | set(row_tot) if abs(osv_dt.get(s, 0.0) - row_tot.get(s, 0.0)) >= TOL]
        checks.append({"name": "Итог каждой строки = оборот Дт ОСВ", "ok": not bad_r,
                       "detail": f"строк: {len(rsids)}" if not bad_r else "расхождения: " + ", ".join(
                           f"{s} ({C.fmt_money(ctx.out(row_tot.get(s, 0.0)))} ≠ {C.fmt_money(ctx.out(osv_dt.get(s, 0.0)))})" for s in sorted(bad_r)[:3])})
        kt_src = col_kt if usd else col_tot
        bad_c = [s for s in set(osv_kt) | set(kt_src) if abs(osv_kt.get(s, 0.0) - kt_src.get(s, 0.0)) >= TOL]
        checks.append({"name": "Итог каждой графы = оборот Кт ОСВ", "ok": not bad_c,
                       "detail": f"граф: {len(csids)}" if not bad_c else "расхождения: " + ", ".join(
                           f"{s} ({C.fmt_money(ctx.out(kt_src.get(s, 0.0)))} ≠ {C.fmt_money(ctx.out(osv_kt.get(s, 0.0)))})" for s in sorted(bad_c)[:3])})
        tdt, tkt = sum(osv_dt.values()), sum(osv_kt.values())
        checks.append({"name": "Общий итог = Σ оборотов Дт ОСВ = Σ оборотов Кт ОСВ", "ok": abs(grand - tdt) < TOL and (usd or abs(tdt - tkt) < TOL),   # USD (REPORTNET) Дт и Кт не балансируются
                       "detail": f"шахматка {C.fmt_money(ctx.out(grand))}, ОСВ Дт {C.fmt_money(ctx.out(tdt))}, Кт {C.fmt_money(ctx.out(tkt))}"})
        if usd:
            warnings.append(C.report_label(ctx.d) + ": ячейки — " + C.report_label(ctx.d) + " по стороне Дт (суммы по строкам Дт); итоги граф сверены по стороне Кт и могут отличаться от суммы ячеек")
    else:
        checks.append({"name": "Сверка с ОСВ", "ok": True,
                       "detail": "не выполняется при фильтрах по счетам/центрам/типам/тексту (матрица — подмножество оборотов)"})
    if calc:
        warnings.append(f"Расчётных ячеек (пропорциональное распределение N:M): {len(calc)} — они перечислены в строках отчёта")
    checks.append(P.method_check(ctx, d))
    if len(csids) > 100:
        warnings.append(f"Граф: {len(csids)}")
    return P.finish(d, ctx, "Шахматная ведомость", f"строки Дт: {_lbl(rax)}; графы Кт: {_lbl(cax)}", cols, rows, totals, checks, warnings)


def _lbl(ax) -> str:
    return f"счета уровня {ax.level}" if ax.kind == "account" else f"группы классификатора {ax.cid} (глубина {ax.depth})"
