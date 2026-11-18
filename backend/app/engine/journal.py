"""Журнал-ордер / ведомость (type=journal) по счёту/маске/группе.
journal.side = credit — журнал-ордер: обороты по кредиту счёта в дебет корреспондирующих счетов (графы); debit — ведомость:
обороты по дебету счёта с кредита корреспондирующих счетов. journal.rows = day | fiche (строки — по дням или по ордерам);
journal.corr — ось графов (по умолчанию счета уровня 0); options.max_graphs — максимум граф (60), остальные в «Прочие».
Итоговая строка — итоги по графам (дебетуемым счетам), её сумма = оборот счёта по стороне.
Бланк: journal.fixed_cols [{sel, title}] — графы в заданном порядке (корреспондент относится к ПЕРВОЙ подходящей), journal.rest =
other | auto | hide — куда девать остальных, journal.with_balance — сальдо на начало/конец и оборот по противоположной стороне."""
from __future__ import annotations

from .. import cache
from . import common as C
from . import pairs as P
from .common import Context, ReportError, TOL
from .form import FormulaError, make_matcher, parse_selector


def _parse_fixed(raw) -> list[dict]:
    """fixed_cols -> [{sel (нормализованный текст), title, selector}]; ошибки — по-русски с номером графы."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ReportError("Графы журнала заданы неверно: ожидается список граф")
    out = []
    for n, c in enumerate(raw, 1):
        if isinstance(c, str):
            c = {"sel": c}
        if not isinstance(c, dict):
            raise ReportError(f"Графа {n}: ожидается описание графы с отбором счетов и заголовком")
        text = str(c.get("sel") or "").strip()
        if not text:
            raise ReportError(f"Графа {n}: не указан отбор счетов")
        try:
            sel = parse_selector(text.replace(",", "|").replace(";", "|"))
        except FormulaError as e:
            raise ReportError(f"Графа {n}: некорректный отбор счетов «{text}» — {e.message}")
        out.append({"sel": sel.text, "title": str(c.get("title") or "").strip() or text, "selector": sel})
    return out


def _terms(sel) -> tuple[list[str], str]:
    """(включающие термы как строки, текст исключений для corr_exclude или '')."""
    def t(x):
        return x[1] if x[0] in ("mask", "range") else f"@{x[1]}:{x[2]}"
    return [t(x) for x in sel.include], "|".join(t(x) for x in sel.exclude)


def run(defn: dict) -> dict:
    d = C.normalize(defn)
    ctx = Context(d)
    usd = ctx.usd_main
    j = d.get("journal") or {}
    side = j.get("side") or "credit"
    if side not in ("credit", "debit"):
        raise ReportError("Сторона журнала: по кредиту (журнал-ордер) или по дебету (ведомость)")
    mode = j.get("rows") or "day"
    if mode not in ("day", "fiche"):
        raise ReportError("Строки журнала: по дням или по ордерам")
    masks, groups = P.parse_account_spec(d.get("account"))
    P.need_account(ctx, masks, groups, "Журнал-ордер")
    sel = P.Sel(ctx, masks, groups)
    axis = P.Axis(ctx, j.get("corr"), default_level=0)
    max_graphs = int((d.get("options") or {}).get("max_graphs") or 60)
    fixed = _parse_fixed(j.get("fixed_cols"))
    rest = j.get("rest") or ("other" if fixed else "auto")
    if rest not in ("other", "auto", "hide"):
        raise ReportError("Остальные корреспонденты: в графу «Прочие», по обороту автоматически или скрыть")
    rest_note = None
    if not fixed:
        if rest == "hide":
            rest_note = ("Скрытие остальных корреспондентов действует только при заданных графах бланка; графы не заданы, "
                         "поэтому журнал построен обычным образом — показаны все корреспонденты")
        rest = "auto"
    with_balance = bool(j.get("with_balance"))
    fmatch = [make_matcher(ctx, f["selector"]) for f in fixed]
    fcache: dict[tuple[int, str], str | None] = {}

    def fixed_sid(year: int, code: str) -> str | None:
        k = (year, code)
        if k not in fcache:
            fcache[k] = next((f"f{i}" for i, m in enumerate(fmatch) if m(year, code)), None)
        return fcache[k]
    warnings = P.common_warnings(ctx, d)
    if rest_note:
        warnings.append(rest_note)
    cr = side == "credit"
    sd = "kt" if cr else "dt"

    keys: dict[str, P.Key] = {}
    recs: dict[tuple[int, str], dict] = {}
    total_by_key: dict[str, float] = {}
    calc = 0
    for seg, rows in P.fetch_pairs(ctx, mode):
        s = sel.codes(seg.key)
        for dt_acc, kt_acc, g, am, ud, uk, pr, mdate in rows:
            own, other = (kt_acc, dt_acc) if cr else (dt_acc, kt_acc)
            if own not in s:
                continue
            v = (uk if cr else ud) if usd else am
            fs = fixed_sid(seg.key, other) if fixed else None
            if fs is not None:
                sid = fs
            elif rest == "other":
                sid = "other"
            elif rest == "hide":
                sid = None
            else:
                k = axis.key(seg.key, other)
                keys[k.sid] = k
                sid = k.sid
                total_by_key[sid] = total_by_key.get(sid, 0.0) + abs(v)
            r = recs.get((seg.firm, str(g)))
            if r is None:
                r = recs[(seg.firm, str(g))] = {"date": mdate if mode == "fiche" else g, "firm": seg.firm, "fid": g if mode == "fiche" else None,
                                                "cells": {}, "total": 0.0}
            if sid is not None:
                r["cells"][sid] = r["cells"].get(sid, 0.0) + v
            r["total"] += v
            if pr:
                calc += 1
    if mode == "fiche" and recs:
        conn = cache.connect()
        try:
            for seg in ctx.segments:
                info = {fid: (no, tx) for fid, no, tx in conn.execute(
                    "SELECT fiche_id, MIN(fiche_no), MIN(fiche_text) FROM lines WHERE firm=? AND is_opening=0 GROUP BY fiche_id", (seg.firm,))}
                for (firm, g), r in recs.items():
                    if firm == seg.firm:
                        r["no"], r["text"] = info.get(int(g), ("", ""))
        finally:
            conn.close()

    all_sids = [f"f{i}" for i in range(len(fixed))] + (["other"] if fixed and rest == "other" else [])
    graph_sids = sorted(total_by_key, key=lambda s: keys[s].sort)
    other = set()
    if len(graph_sids) > max_graphs:
        top = set(sorted(total_by_key, key=lambda s: -total_by_key[s])[:max_graphs])
        other = {s for s in graph_sids if s not in top}
        graph_sids = [s for s in graph_sids if s in top]
        warnings.append(f"Граф {len(top) + len(other)} — показаны {max_graphs} крупнейших, остальные в «Прочие»")

    tkey = "turn_kt" if cr else "turn_dt"
    gprefix = "dtc" if cr else "ktc"
    cols: list[dict] = [{"key": tkey, "title": "Итого", "group": "Оборот по кредиту" if cr else "Оборот по дебету", "type": "money", "drill": sd}]
    gtitle = "В дебет счетов" if cr else "С кредита счетов"
    fixed_sels = [f["sel"] for f in fixed]
    excl_all = list(fixed_sels)
    for i, f in enumerate(fixed):
        inc, own_exc = _terms(f["selector"])
        ex = ([own_exc] if own_exc else []) + [fixed_sels[m] for m in range(i)]
        c = {"key": f"{gprefix}:f{i}", "title": f["title"], "group": gtitle, "type": "money", "drill": sd, "name": f["title"], "corr": inc}
        if ex:
            c["corr_exclude"] = ex
        cols.append(c)
    if fixed and rest == "other":
        cols.append({"key": f"{gprefix}:other", "title": "Прочие", "group": gtitle, "type": "money", "drill": sd, "name": "Прочие корреспондирующие счета",
                     "corr": ["*"], "corr_exclude": list(excl_all)})
    for sid in graph_sids:
        k = keys[sid]
        c = {"key": f"{gprefix}:{sid}", "title": k.code or k.name, "group": gtitle, "type": "money", "drill": sd, "name": axis.name_of(k)}
        c.update(axis.drill(k))
        if fixed:
            c["corr_exclude"] = list(excl_all)
        cols.append(c)
    if other:
        cols.append({"key": f"{gprefix}:~", "title": "Прочие", "group": gtitle, "type": "money", "drill": None})

    def vals(cells: dict[str, float], total: float) -> dict:
        v = {tkey: P.out_val(ctx, total)}
        for sid in all_sids:
            v[f"{gprefix}:{sid}"] = P.out_val(ctx, cells.get(sid, 0.0))
        for sid in graph_sids:
            v[f"{gprefix}:{sid}"] = P.out_val(ctx, cells.get(sid, 0.0))
        if other:
            v[f"{gprefix}:~"] = P.out_val(ctx, sum(cells.get(s, 0.0) for s in other))
        return v

    order = sorted(recs.items(), key=lambda kv: (kv[1]["date"], kv[0][0], int(kv[0][1]) if mode == "fiche" else 0))
    rows: list[dict] = []
    tot_cells: dict[str, float] = {}
    tot = 0.0
    for (firm, g), r in order:
        for k_, v_ in r["cells"].items():
            tot_cells[k_] = tot_cells.get(k_, 0.0) + v_
        tot += r["total"]
        if abs(r["total"]) < TOL and d["options"]["hide_zero"]:
            continue
        date = r["date"]
        row = {"id": f"j:{firm}:{g}", "parent": None, "level": 0, "kind": "line", "code": C.fmt_date(C.parse_date(date)),
               "values": vals(r["cells"], r["total"]), "bold": False,
               "drill": {**sel.drill(), "side": sd, "period": {"date_from": date, "date_to": date}}}
        if mode == "fiche":
            row["name"] = f"№ {r.get('no', '')} · {r.get('text', '')}"
            row["fiche"] = {"firm": firm, "fiche_id": int(g)}
        else:
            row["name"] = "Обороты за день"
        rows.append(row)
    tv = vals(tot_cells, tot)
    total_row = {"id": "total", "parent": None, "level": 0, "kind": "total", "code": "", "name": "ИТОГО (по " + ("дебетуемым" if cr else "кредитуемым") + " счетам)",
                 "values": tv, "bold": True, "drill": {**sel.drill(), "side": sd}}

    leaves = P.leaves_for(ctx, sel)
    osv = P.sum_leaves(ctx, leaves)
    osv_turn = osv["kt"] if cr else osv["dt"]
    opp_turn = osv["dt"] if cr else osv["kt"]
    per = {"date_from": ctx.d1.isoformat(), "date_to": ctx.d2.isoformat()}

    def bal_name(label: str, net: float) -> str:
        return f"{label}" + (" (дебетовое)" if net > TOL else " (кредитовое)" if net < -TOL else "")

    if with_balance:
        o_net = osv["open"]
        rows.insert(0, {"id": "bal_open", "parent": None, "level": 0, "kind": "line", "code": "", "name": bal_name("Сальдо на начало", o_net),
                        "values": {tkey: P.out_val(ctx, abs(o_net))}, "bold": True, "drill": {**sel.drill(), "mode": "open", "period": per}})
    rows.append(total_row)
    if with_balance:
        # сальдо на конец = сальдо на начало + оборот Дт − оборот Кт (по тем же парам: своя сторона — из журнала, противоположная — из ОСВ)
        own_turn = tot
        e_net = o_net + (opp_turn - own_turn if cr else own_turn - opp_turn)
        rows.append({"id": "opp_turn", "parent": None, "level": 0, "kind": "total", "code": "",
                     "name": "Оборот по " + ("дебету" if cr else "кредиту") + " (противоположная сторона)",
                     "values": {tkey: P.out_val(ctx, opp_turn)}, "bold": False,
                     "drill": {**sel.drill(), "side": "dt" if cr else "kt", "period": per}})
        rows.append({"id": "bal_close", "parent": None, "level": 0, "kind": "total", "code": "", "name": bal_name("Сальдо на конец", e_net),
                     "values": {tkey: P.out_val(ctx, abs(e_net))}, "bold": True, "drill": {**sel.drill(), "mode": "close", "period": per}})

    checks: list[dict] = []
    if not C.line_filters_active(d):
        checks.append({"name": f"Итог = оборот {'Кт' if cr else 'Дт'} счёта (ОСВ)", "ok": abs(tot - osv_turn) < TOL,
                       "detail": f"журнал {C.fmt_money(ctx.out(tot))}, ОСВ {C.fmt_money(ctx.out(osv_turn))}"})
        if with_balance:
            checks.append({"name": "Сальдо на конец = исходящее сальдо ОСВ", "ok": abs(e_net - osv["close"]) < TOL,
                           "detail": f"журнал {C.fmt_money(ctx.out(e_net))}, ОСВ {C.fmt_money(ctx.out(osv['close']))} (Дт − Кт)"})
    elif with_balance:
        warnings.append("Сальдо при фильтрах по центрам/типам ордеров/тексту считается по всем проводкам счёта и может не сходиться с оборотами журнала")
    shown = sum(tot_cells.values())
    if rest == "hide":
        hidden = tot - shown
        checks.append({"name": "Σ по графам + не вошедшее в графы = итог", "ok": abs(shown + hidden - tot) < TOL, "detail": C.fmt_money(ctx.out(tot))})
        if abs(hidden) >= TOL:
            warnings.append(f"Вне заданных граф осталось {C.fmt_money(ctx.out(hidden))} — в графы не вошло (остальные корреспонденты скрыты), в «Итого» учтено")
    else:
        checks.append({"name": "Σ по графам = итог", "ok": abs(shown - tot) < TOL, "detail": C.fmt_money(ctx.out(tot))})
    if calc:
        warnings.append("Часть корреспонденции определена пропорционально — графы по таким ордерам расчётные")
    checks.append(P.method_check(ctx, d))
    return P.finish(d, ctx, "Журнал-ордер" if cr else "Ведомость", f"{'по кредиту' if cr else 'по дебету'} счёта {P.spec_label(masks, groups) or 'по фильтрам'}",
                    cols, rows, tv, checks, warnings)
