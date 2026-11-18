"""Главная книга (type=ledger): по счёту (маска/группа; несколько масок через запятую — раздел на каждую; без «account» — раздел на
каждый корневой счёт выбранных фильтрами) помесячно: обороты по дебету с расшифровкой «с кредита счетов» по графам (ledger.corr —
ось графов, по умолчанию уровень 0), оборот по кредиту итогом, сальдо (свёрнутое) на конец месяца. Строки раздела: заголовок,
«Входящее сальдо», месяцы, «Итого за период».
Ключи граф: turn_dt, dtc:<ключ графы> (drill=dt, corr=[…]), turn_kt, bal_dt, bal_kt. Графов больше options.max_graphs (40) — остальные в «Прочие»."""
from __future__ import annotations

from .. import cache, plan
from . import common as C
from . import pairs as P
from .common import Context, ReportError, TOL


def run(defn: dict) -> dict:
    d = C.normalize(defn)
    ctx = Context(d)
    usd = ctx.usd_main
    masks, groups = P.parse_account_spec(d.get("account"))
    axis = P.Axis(ctx, (d.get("ledger") or {}).get("corr"), default_level=0)
    max_graphs = int((d.get("options") or {}).get("max_graphs") or 40)
    warnings = P.common_warnings(ctx, d)
    base = P.Sel(ctx)

    # ---- разделы ----------------------------------------------------------------------------------
    sections: list[dict] = []   # {label, name, drill, sel}
    if masks or groups:
        for m in masks:
            sections.append({"label": m, "name": P.mask_name(ctx, m), "drill": {"accounts": [m]},
                             "sel": P.Sel(ctx, [m], [])})
        for g in groups:
            sections.append({"label": f"группа {g['classifier']}:{g['group']}", "name": P.group_name(g), "drill": {"group": dict(g)},
                             "sel": P.Sel(ctx, [], [g])})
    else:
        roots: dict[str, str] = {}
        for seg in ctx.segments:
            for c in base.codes(seg.key):
                roots.setdefault(plan.current().root_of(c), "")
        if not roots:
            raise ReportError("Нет счетов для главной книги (проверьте фильтры и период)")
        for r in sorted(roots):
            sections.append({"label": r, "name": P.mask_name(ctx, r), "drill": {"accounts": [r]},
                             "sel": P.Sel(ctx, [r], [])})
    if len(sections) > 400:
        raise ReportError("Слишком много разделов главной книги (более 400) — задайте счета или фильтры")

    # ---- данные --------------------------------------------------------------------------------------
    leaves_all = C.aggregate_leaves(ctx)
    pair_rows = list(P.fetch_pairs(ctx, "month"))
    conn = cache.connect()
    try:
        opening_by_seg: dict[int, dict[str, float]] = {}
        _, _, open_ok = C.line_filter_sql(d)
        op_sql, op_params = C.attr_cond_sql(d, "lines")
        for seg in ctx.segments[1:]:
            opening_by_seg[seg.key] = {a: (un if usd else n) / ctx.divisor(seg, usd) for a, n, un in conn.execute(
                "SELECT account, SUM(dt-kt), SUM(usd_dt-usd_kt) FROM lines WHERE firm=? AND is_opening=1 AND ? =1" + op_sql + " GROUP BY account",
                (seg.firm, 1 if open_ok else 0, *op_params))}
    finally:
        conn.close()

    keys: dict[str, P.Key] = {}
    data: list[dict] = []
    total_by_key: dict[str, float] = {}
    for si, sec in enumerate(sections):
        sel: P.Sel = sec["sel"]
        dt_m: dict[str, dict[str, float]] = {}        # ym -> {sid: v}
        kt_m: dict[str, float] = {}
        prop: set[tuple[str, str]] = set()
        for seg, rows in pair_rows:
            s = sel.codes(seg.key)
            for dt_acc, kt_acc, ym, am, ud, uk, pr, _ in rows:
                if dt_acc in s:
                    k = axis.key(seg.key, kt_acc)
                    keys[k.sid] = k
                    v = ud if usd else am
                    m = dt_m.setdefault(ym, {})
                    m[k.sid] = m.get(k.sid, 0.0) + v
                    total_by_key[k.sid] = total_by_key.get(k.sid, 0.0) + abs(v)
                    if pr:
                        prop.add((ym, k.sid))
                if kt_acc in s:
                    kt_m[ym] = kt_m.get(ym, 0.0) + (uk if usd else am)
        leaves = {k: a for k, a in leaves_all.items() if k[1] in sel.codes(k[0])}
        data.append({"sec": sec, "dt": dt_m, "kt": kt_m, "prop": prop, "osv": P.sum_leaves(ctx, leaves)})

    graph_sids = sorted(total_by_key, key=lambda s: keys[s].sort)
    other = set()
    if len(graph_sids) > max_graphs:
        top = set(sorted(total_by_key, key=lambda s: -total_by_key[s])[:max_graphs])
        other = {s for s in graph_sids if s not in top}
        graph_sids = [s for s in graph_sids if s in top]
        warnings.append(f"Графов корреспонденции {len(top) + len(other)} — показаны {max_graphs} крупнейших, остальные сведены в «Прочие»")

    cols: list[dict] = [{"key": "turn_dt", "title": "Итого", "group": "Обороты по дебету", "type": "money", "drill": "dt"}]
    for sid in graph_sids:
        k = keys[sid]
        c = {"key": f"dtc:{sid}", "title": k.code or k.name, "group": "в т.ч. с кредита счетов", "type": "money", "drill": "dt",
             "name": axis.name_of(k)}
        c.update(axis.drill(k))
        cols.append(c)
    if other:
        cols.append({"key": "dtc:~", "title": "Прочие", "group": "в т.ч. с кредита счетов", "type": "money", "drill": None})
    cols += [{"key": "turn_kt", "title": "Оборот по кредиту", "group": "Обороты по кредиту", "type": "money", "drill": "kt"},
             {"key": "bal_dt", "title": "Дебет", "group": "Сальдо на конец месяца", "type": "money", "drill": None},
             {"key": "bal_kt", "title": "Кредит", "group": "Сальдо на конец месяца", "type": "money", "drill": None}]

    def vals_for(dtm: dict[str, float], dt_total: float, kt_total: float) -> dict:
        v = {"turn_dt": P.out_val(ctx, dt_total), "turn_kt": P.out_val(ctx, kt_total)}
        for sid in graph_sids:
            v[f"dtc:{sid}"] = P.out_val(ctx, dtm.get(sid, 0.0))
        if other:
            v["dtc:~"] = P.out_val(ctx, sum(dtm.get(s, 0.0) for s in other))
        return v

    # ---- строки -------------------------------------------------------------------------------------
    rows: list[dict] = []
    hide = d["options"]["hide_zero"]
    chk_dt = chk_kt = chk_close = 0
    bad_notes: list[str] = []
    g_dt = {sid: 0.0 for sid in graph_sids}
    g_tot = {"dt": 0.0, "kt": 0.0, "other": 0.0}
    for si, dd in enumerate(data):
        sec = dd["sec"]
        sid_ = f"s{si}"
        osv = dd["osv"]
        rows.append({"id": sid_, "parent": None, "level": 0, "kind": "account", "code": sec["label"], "name": sec["name"], "values": {},
                     "bold": True, "drill": dict(sec["drill"])})
        bal = osv["open"]
        rows.append({"id": f"{sid_}|open", "parent": sid_, "level": 1, "kind": "line", "code": "", "name": "Входящее сальдо",
                     "values": {"bal_dt": ctx.out(max(bal, 0.0)), "bal_kt": ctx.out(max(-bal, 0.0))}, "bold": True, "drill": {}})
        sum_dt = sum_kt = 0.0
        sec_g: dict[str, float] = {}
        for si2, seg in enumerate(ctx.segments):
            if si2 > 0 and not seg.continues:
                s = sec["sel"].codes(seg.key)
                bal = sum(v for a, v in opening_by_seg[seg.key].items() if a in s)
            for ym in C.month_list(seg.d_from, seg.d_to):
                dtm = dd["dt"].get(ym, {})
                dt_t = sum(dtm.values())
                kt_t = dd["kt"].get(ym, 0.0)
                bal += dt_t - kt_t
                sum_dt += dt_t
                sum_kt += kt_t
                for k_, v_ in dtm.items():
                    sec_g[k_] = sec_g.get(k_, 0.0) + v_
                if hide and abs(dt_t) < TOL and abs(kt_t) < TOL:
                    continue
                v = vals_for(dtm, dt_t, kt_t)
                v["bal_dt"], v["bal_kt"] = ctx.out(max(bal, 0.0)), ctx.out(max(-bal, 0.0))
                rows.append({"id": f"{sid_}|{ym}", "parent": sid_, "level": 1, "kind": "line", "code": ym, "name": C.month_title(ym),
                             "values": v, "bold": False,
                             "drill": {**sec["drill"], "period": P.clip_period(ctx, ym)}})
        v = vals_for(sec_g, sum_dt, sum_kt)
        v["bal_dt"], v["bal_kt"] = ctx.out(max(bal, 0.0)), ctx.out(max(-bal, 0.0))
        rows.append({"id": f"{sid_}|tot", "parent": sid_, "level": 1, "kind": "total", "code": "", "name": "Итого за период", "values": v,
                     "bold": True, "drill": dict(sec["drill"])})
        g_tot["dt"] += sum_dt
        g_tot["kt"] += sum_kt
        for k_, v_ in sec_g.items():
            if k_ in g_dt:
                g_dt[k_] += v_
            else:
                g_tot["other"] += v_
        if abs(sum_dt - osv["dt"]) >= TOL or abs(sum_kt - osv["kt"]) >= TOL:
            bad_notes.append(f"{sec['label']}: Дт {C.fmt_money(ctx.out(sum_dt))}/{C.fmt_money(ctx.out(osv['dt']))}")
        if abs(bal - osv["close"]) >= TOL:
            chk_close += 1
        chk_dt += 1
    if len(sections) > 1:
        v = vals_for(g_dt, g_tot["dt"], g_tot["kt"])
        if other:
            v["dtc:~"] = P.out_val(ctx, g_tot["other"])
        rows.append({"id": "total", "parent": None, "level": 0, "kind": "total", "code": "", "name": "ИТОГО по всем разделам", "values": v,
                     "bold": True, "drill": {}})
        totals = v
    else:
        totals = rows[-1]["values"]

    checks: list[dict] = []
    if not C.line_filters_active(d):
        checks.append({"name": "Обороты Дт/Кт по месяцам = ОСВ (по каждому разделу)", "ok": not bad_notes,
                       "detail": f"разделов: {len(sections)}" if not bad_notes else "расхождения: " + "; ".join(bad_notes[:3])})
    if not C.attr_filters_active(d):    # при отборе по подразделению/источнику/автору обороты по парам (обе проводки в отборе) не равны оборотам проводок
        checks.append({"name": "Сальдо на конец периода = ОСВ", "ok": chk_close == 0,
                       "detail": "по всем разделам" if not chk_close else f"расхождений: {chk_close} (перенос остатков между годами)"})
    props = sum(1 for dd in data if dd["prop"])
    if props:
        warnings.append("В графах есть корреспонденция, определённая пропорционально — см. «Доля методов корреспонденции»")
    checks.append(P.method_check(ctx, d))
    return P.finish(d, ctx, "Главная книга", f"графы: {'счета уровня %d' % axis.level if axis.kind == 'account' else 'группы %s' % axis.cid}",
                    cols, rows, totals, checks, warnings)
