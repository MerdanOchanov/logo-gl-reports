"""Анализ счёта (type=analysis): сальдо нач.; обороты Дт в корреспонденции с кредитом счетов; обороты Кт в корреспонденции с дебетом
счетов; сальдо кон. Корреспонденты — на уровне счёта (analysis.corr = {kind:account, level}) или по группам классификатора
({kind:classifier, id, depth}). По умолчанию уровень 1. columns.by_month — обороты по месяцам.
Ключи граф как у ОСВ: open_dt/open_kt, turn_dt/turn_kt (+ turn_dt_YYYY-MM), close_dt/close_kt."""
from __future__ import annotations

from . import common as C
from . import pairs as P
from .common import Context, ReportError, TOL


def run(defn: dict) -> dict:
    d = C.normalize(defn)
    ctx = Context(d)
    masks, groups = P.parse_account_spec(d.get("account"))
    P.need_account(ctx, masks, groups, "Анализ счёта")
    sel = P.Sel(ctx, masks, groups)
    axis = P.Axis(ctx, (d.get("analysis") or {}).get("corr"), default_level=1)
    by_month = d["columns"]["by_month"]
    months = C.month_list(ctx.d1, ctx.d2) if by_month else []
    if len(months) > 36:
        raise ReportError("Разбивка по месяцам возможна не более чем на 36 месяцев")
    expanded = d["columns"]["balance"] == "expanded"
    warnings = P.common_warnings(ctx, d)

    # ---- пары ---------------------------------------------------------------------------------
    dtm: dict[str, dict[str, float]] = {}      # corr sid -> {'': total, ym: v}
    ktm: dict[str, dict[str, float]] = {}
    keys: dict[str, P.Key] = {}
    calc: set[tuple[str, str]] = set()
    for seg, rows in P.fetch_pairs(ctx, "month" if by_month else ""):
        s = sel.codes(seg.key)
        for dt_acc, kt_acc, g, am, ud, uk, pr, _ in rows:
            if dt_acc in s:
                k = axis.key(seg.key, kt_acc)
                keys[k.sid] = k
                v = ud if ctx.usd_main else am
                m = dtm.setdefault(k.sid, {})
                m[""] = m.get("", 0.0) + v
                if by_month:
                    m[g] = m.get(g, 0.0) + v
                if pr:
                    calc.add(("dt", k.sid))
            if kt_acc in s:
                k = axis.key(seg.key, dt_acc)
                keys[k.sid] = k
                v = uk if ctx.usd_main else am
                m = ktm.setdefault(k.sid, {})
                m[""] = m.get("", 0.0) + v
                if by_month:
                    m[g] = m.get(g, 0.0) + v
                if pr:
                    calc.add(("kt", k.sid))

    leaves = P.leaves_for(ctx, sel)
    tot = P.sum_leaves(ctx, leaves)

    # ---- графы --------------------------------------------------------------------------------
    cols: list[dict] = [
        {"key": "open_dt", "title": "Дебет", "group": "Сальдо на начало", "type": "money", "drill": None},
        {"key": "open_kt", "title": "Кредит", "group": "Сальдо на начало", "type": "money", "drill": None}]
    for ym in months:
        g = C.month_title(ym)
        cols += [{"key": f"turn_dt_{ym}", "title": "Дебет", "group": g, "type": "money", "drill": "dt", "period": P.month_period(ym)},
                 {"key": f"turn_kt_{ym}", "title": "Кредит", "group": g, "type": "money", "drill": "kt", "period": P.month_period(ym)}]
    tg = "Обороты за период" if not months else "Обороты за период (итого)"
    cols += [{"key": "turn_dt", "title": "Дебет", "group": tg, "type": "money", "drill": "dt"},
             {"key": "turn_kt", "title": "Кредит", "group": tg, "type": "money", "drill": "kt"},
             {"key": "close_dt", "title": "Дебет", "group": "Сальдо на конец", "type": "money", "drill": None},
             {"key": "close_kt", "title": "Кредит", "group": "Сальдо на конец", "type": "money", "drill": None}]

    def turn_vals(m_dt: dict | None, m_kt: dict | None) -> dict:
        v = {}
        for side, m in (("dt", m_dt), ("kt", m_kt)):
            if m is None:
                continue
            v[f"turn_{side}"] = P.out_val(ctx, m.get("", 0.0))
            for ym in months:
                v[f"turn_{side}_{ym}"] = P.out_val(ctx, m.get(ym, 0.0))
        return v

    sec_drill = sel.drill()
    label = P.spec_label(masks, groups) or "по фильтрам"
    name = ""
    if len(masks) == 1 and not groups:
        name = P.mask_name(ctx, masks[0])
    elif len(groups) == 1 and not masks:
        name = P.group_name(groups[0])
    elif masks or groups:
        name = "Счета: " + label
    rows_out: list[dict] = []
    od, ok_ = P.bal_cols(ctx, tot, "open", expanded)
    cd, ck = P.bal_cols(ctx, tot, "close", expanded)
    top = {"open_dt": ctx.out(od), "open_kt": ctx.out(ok_), "close_dt": ctx.out(cd), "close_kt": ctx.out(ck)}
    sum_dt = sum(m.get("", 0.0) for m in dtm.values())
    sum_kt = sum(m.get("", 0.0) for m in ktm.values())
    top["turn_dt"], top["turn_kt"] = ctx.out(sum_dt), ctx.out(sum_kt)
    for ym in months:
        top[f"turn_dt_{ym}"] = ctx.out(sum(m.get(ym, 0.0) for m in dtm.values()))
        top[f"turn_kt_{ym}"] = ctx.out(sum(m.get(ym, 0.0) for m in ktm.values()))
    rows_out.append({"id": "s", "parent": None, "level": 0, "kind": "account", "code": label, "name": name, "values": top, "bold": True,
                     "drill": dict(sec_drill)})

    sort = d["options"]["sort"]

    def ordered(m: dict) -> list[str]:
        sids = list(m)
        if sort["by"] == "name":
            sids.sort(key=lambda s: (axis.name_of(keys[s]) or "").casefold(), reverse=sort["dir"] == "desc")
        elif sort["by"] in ("turn_dt", "turn_kt", "turn"):
            sids.sort(key=lambda s: (-abs(m[s].get("", 0.0)), keys[s].sort))
        else:
            sids.sort(key=lambda s: keys[s].sort, reverse=sort["dir"] == "desc")
        return sids

    for side, m, title in (("dt", dtm, "Обороты по дебету — в корреспонденции с кредитом счетов"),
                           ("kt", ktm, "Обороты по кредиту — в корреспонденции с дебетом счетов")):
        gid = f"s|{side}"
        agg: dict[str, float] = {}
        for mm in m.values():
            for kk, vv in mm.items():
                agg[kk] = agg.get(kk, 0.0) + vv
        tv = turn_vals(agg, None) if side == "dt" else turn_vals(None, agg)
        rows_out.append({"id": gid, "parent": "s", "level": 1, "kind": "group", "code": "", "name": title,
                         "values": {k: v for k, v in tv.items()}, "bold": True,
                         "drill": {**sec_drill, "side": side}})
        for sid in ordered(m):
            k = keys[sid]
            vals = turn_vals(m[sid], None) if side == "dt" else turn_vals(None, m[sid])
            if all(v is None for v in vals.values()):
                continue
            if d["options"]["hide_zero"] and all(abs(x) < TOL for x in (m[sid].get("", 0.0),)):
                continue
            rows_out.append({"id": f"{gid}|{sid}", "parent": gid, "level": 2, "kind": "account", "code": k.code,
                             "name": axis.name_of(k) + ("  (расчётная)" if (side, sid) in calc else ""), "values": vals, "bold": False,
                             "drill": {**sec_drill, "side": side, **axis.drill(k)}})
    totals = dict(top)
    rows_out.append({"id": "total", "parent": None, "level": 0, "kind": "total", "code": "", "name": "ИТОГО", "values": dict(top), "bold": True,
                     "drill": {}})

    # ---- контроли -----------------------------------------------------------------------------
    checks: list[dict] = []
    if not C.line_filters_active(d):
        for side, ssum, osv in (("Дт", sum_dt, tot["dt"]), ("Кт", sum_kt, tot["kt"])):
            checks.append({"name": f"Σ по корр. счетам = оборот счёта по {side} (ОСВ)", "ok": abs(ssum - osv) < TOL,
                           "detail": f"пары {C.fmt_money(ctx.out(ssum))}, ОСВ {C.fmt_money(ctx.out(osv))}"})
        if by_month:
            tm_dt = sum(sum(v for k, v in m.items() if k) for m in dtm.values())
            checks.append({"name": "Σ месяцев = итог по Дт", "ok": abs(tm_dt - sum_dt) < TOL, "detail": C.fmt_money(ctx.out(tm_dt))})
    checks.append({"name": "Нач. сальдо + Дт − Кт = кон. сальдо", "ok": abs(tot["open"] + tot["dt"] - tot["kt"] - tot["close"]) < TOL,
                   "detail": f"нач. {C.fmt_money(ctx.out(tot['open']))}, кон. {C.fmt_money(ctx.out(tot['close']))}"
                             + ("" if abs(tot["open"] + tot["dt"] - tot["kt"] - tot["close"]) < TOL
                                else " (перенос остатков между годами, см. «Разрывы переноса»)")})
    if calc:
        warnings.append("Корреспонденты, помеченные «расчётная», получены пропорциональным распределением (N:M без взаимного CROSSCODE)")
    checks.append(P.method_check(ctx, d))
    return P.finish(d, ctx, "Анализ счёта", f"корреспонденты: {_axis_label(axis)}", cols, rows_out, totals, checks, warnings)


def _axis_label(axis) -> str:
    return f"счета уровня {axis.level}" if axis.kind == "account" else f"группы классификатора {axis.cid}"
