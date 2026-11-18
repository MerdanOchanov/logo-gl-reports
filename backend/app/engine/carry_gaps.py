"""Разрывы переноса остатков между годами (type=carry_gaps): закрытие года Y-1 (açılış + все проводки) против açılış года Y по счетам."""
from __future__ import annotations

from .. import accounts as acc_mod
from .. import books, cache, dsparams, plan
from . import common as C
from .common import ReportError, TOL


def is_noncarried(code: str) -> bool:
    """Счета, которые в açılış не переносятся по построению: классы 6-7 и 590 (результат года закрывается ордерами)."""
    return plan.current().is_noncarried(code)


def boundary_gaps(ctx) -> dict:
    """Известные разрывы переноса на стыках книг отчёта: {счёт: Σ (открытие следующего года − закрытие предыдущего)} в тех же единицах,
    что и листья отчёта (копейки после деноминации). Классы 6-7 и 590 в açılış не переносятся по построению, их разрыв — тоже известный."""
    out: dict = {}
    segs = ctx.segments
    if len(segs) < 2:
        return out
    usd = bool(ctx.usd_main)
    col = "usd_dt-usd_kt" if usd else "dt-kt"
    conn = cache.connect()
    try:
        for a, b in zip(segs, segs[1:]):
            if b.continues or a.firm == b.firm:
                continue
            da, db = ctx.divisor(a, usd), ctx.divisor(b, usd)
            close = {c: v / da for c, v in conn.execute(f"SELECT account, SUM({col}) FROM lines WHERE firm=? AND (is_opening=1 OR date<=?) GROUP BY account",
                                                        (a.firm, a.d_to.isoformat()))}
            opened = {c: v / db for c, v in conn.execute(f"SELECT account, SUM({col}) FROM lines WHERE firm=? AND is_opening=1 GROUP BY account", (b.firm,))}
            for c in set(close) | set(opened):
                out[c] = out.get(c, 0.0) + opened.get(c, 0.0) - close.get(c, 0.0)
    finally:
        conn.close()
    return out


def run(defn: dict) -> dict:
    d = C.normalize(defn)
    y1, y2 = d["_d1"].year, d["_d2"].year
    mains = sorted(books.main_books(), key=lambda b: (b.date_from, b.id))
    # граница переноса: соседние основные книги РАЗНЫХ источников без разрыва по датам (на границе одного источника разрыва нет по построению)
    bounds = [(a, b) for a, b in zip(mains, mains[1:]) if a.src != b.src and (b.date_from - a.date_to).days <= 7 and y1 <= b.date_from.year <= y2]
    if not bounds:
        raise ReportError("Для выбранных лет нет пары соседних загруженных лет — разрывы переноса не с чем сравнивать")
    firms = sorted({x.src for bb in bounds for x in bb})
    warnings: list[str] = []
    try:
        warnings += cache.ensure_fresh(firms)
    except ValueError as e:
        raise ReportError(str(e))
    synced = cache.source_rows()
    notes = dsparams.get("carry_notes")
    f = d["filters"]
    usd = bool(d.get("_report"))
    col = "usd_dt-usd_kt" if usd else "dt-kt"
    scale = d["scale"]

    def out(x):
        v = round(x / 100.0 / scale, 2)
        return v + 0.0 if v != 0 else 0.0

    show_nc = bool((d["options"].get("show_noncarried") if isinstance(d.get("options"), dict) else False)
                   or (defn.get("options") or {}).get("show_noncarried") or (defn.get("carry") or {}).get("show_noncarried"))
    conn = cache.connect()
    rows: list[dict] = []
    tot = {"close_prev": 0.0, "open_next": 0.0, "gap": 0.0}
    n_gaps_total, abs_total = 0, 0.0          # балансовые счета (переносятся)
    n_nc_total, abs_nc_total = 0, 0.0         # классы 6-7 и 590: не переносятся по построению
    try:
        for bp, bn in bounds:
            yp, yn = bp.label, bn.label
            dp = 1 if (usd or not d["denominate"]) else bp.divisor
            dn = 1 if (usd or not d["denominate"]) else bn.divisor
            if (synced.get(bp.src) or {}).get("synced_at") is None or (synced.get(bn.src) or {}).get("synced_at") is None:
                raise ReportError(f"Данные года {yp if (synced.get(bp.src) or {}).get('synced_at') is None else yn} ещё не загружены - запустите синхронизацию")
            close = {a: v / dp for a, v in conn.execute(f"SELECT account, SUM({col}) FROM lines WHERE firm=? AND (is_opening=1 OR date<=?) GROUP BY account",
                                                        (bp.src, bp.date_to.isoformat()))}
            opened = {a: v / dn for a, v in conn.execute(f"SELECT account, SUM({col}) FROM lines WHERE firm=? AND is_opening=1 GROUP BY account", (bn.src,))}
            codes = sorted(set(close) | set(opened))
            if f["accounts"]:
                codes = [c for c in codes if acc_mod.matches_any(c, f["accounts"])]
            plan_n = {r["code"]: r["name"] for r in acc_mod.get_plan_book(bn.id)}
            plan_p = {r["code"]: r["name"] for r in acc_mod.get_plan_book(bp.id)}
            tag_p, tag_n = (f"{yp}", f"{yn}") if yp != yn else (f"{yp}.{bp.part}", f"{yn}.{bn.part}")
            gid = f"b:{tag_p}-{tag_n}"
            children, nc_children = [], []
            n_gap, abs_gap, n_nc, abs_nc = 0, 0.0, 0, 0.0
            for c in codes:
                a, b = close.get(c, 0.0), opened.get(c, 0.0)
                g = b - a
                has_gap = abs(g) >= TOL
                nc = is_noncarried(c)
                if has_gap:
                    if nc:
                        n_nc += 1
                        abs_nc += abs(g)
                    else:
                        n_gap += 1
                        abs_gap += abs(g)
                if (d["options"]["hide_zero"] and not has_gap) or (not has_gap and abs(a) < TOL and abs(b) < TOL):
                    continue
                if nc and not show_nc:
                    continue
                parent = f"{gid}|nc" if nc else gid
                row = {"id": f"{gid}|a:{c}", "parent": parent, "level": 2 if nc else 1, "kind": "account", "code": c,
                       "name": plan_n.get(c) or plan_p.get(c) or "", "bold": False,
                       "values": {"close_prev": out(a), "open_next": out(b), "gap": out(g)}, "drill": {"accounts": [c]}}
                (nc_children if nc else children).append(row)
            sort = d["options"]["sort"]
            for lst in (children, nc_children):
                if sort["by"] == "gap":
                    lst.sort(key=lambda r: abs(r["values"]["gap"]), reverse=sort["dir"] != "asc")
                else:
                    lst.sort(key=lambda r: r["code"], reverse=sort["dir"] == "desc")
            sums = {k: sum(r["values"][k] for r in children) for k in ("close_prev", "open_next", "gap")}
            rows.append({"id": gid, "parent": None, "level": 0, "kind": "group", "code": f"{tag_p}→{tag_n}",
                         "name": f"Закрытие {tag_p} → открытие {tag_n}: балансовых счетов с расхождением {n_gap}, Σ|Δ| {C.fmt_money(round(abs_gap / 100.0 / scale, 2))}",
                         "bold": True, "values": {k: round(v, 2) for k, v in sums.items()}, "drill": {}})
            rows += children
            if show_nc:
                nsums = {k: sum(r["values"][k] for r in nc_children) for k in ("close_prev", "open_next", "gap")}
                rows.append({"id": f"{gid}|nc", "parent": gid, "level": 1, "kind": "group", "code": "6–7, 590",
                             "name": f"Не переносятся по построению (классы 6–7 и 590): расхождений {n_nc}, Σ|Δ| {C.fmt_money(round(abs_nc / 100.0 / scale, 2))}",
                             "bold": False, "values": {k: round(v, 2) for k, v in nsums.items()}, "drill": {}})
                rows += nc_children
            for k in tot:
                tot[k] += sums[k]
            n_gaps_total += n_gap
            abs_total += abs_gap
            n_nc_total += n_nc
            abs_nc_total += abs_nc
            suffix = ""
            for nt in notes:    # заметки набора об известных разрывах переноса (dataset_params.carry_notes)
                if nt.get("from_label", yp) != yp or nt.get("to_label", yn) != yn:
                    continue
                if nt.get("from_firm", bp.src_firm) != bp.src_firm or nt.get("to_firm", bn.src_firm) != bn.src_firm:
                    continue
                if nt.get("denom_suffix"):
                    suffix = nt["denom_suffix"]
                else:
                    warnings.append(nt["text"])
            if bp.divisor != bn.divisor and d["denominate"]:
                warnings.append(f"Граница {yp}→{yn}: деноминация ÷{bp.divisor / bn.divisor:g} применена к году {yp}{suffix}")
    finally:
        conn.close()
    warnings.append("Классы 6–7 и счёт 590 в açılış не переносятся (результат года закрывается ордерами), их расхождения не показаны в основной части"
                    + (" (показаны отдельной группой)" if show_nc else " (чтобы увидеть их, включите показ непереносимых счетов)") + ".")
    rows.append({"id": "total", "parent": None, "level": 0, "kind": "total", "code": "", "name": "ИТОГО (балансовые счета)", "bold": True,
                 "values": {k: round(v, 2) for k, v in tot.items()}, "drill": {}})
    cols = [{"key": "close_prev", "title": "Закрытие прошлого года (Дт−Кт)", "group": "Остаток", "type": "money", "drill": None},
            {"key": "open_next", "title": "Открытие года (Дт−Кт)", "group": "Остаток", "type": "money", "drill": None},
            {"key": "gap", "title": "Расхождение", "group": "Остаток", "type": "money", "drill": None}]
    return {"title": d.get("title") or "Разрывы переноса остатков между годами",
            "subtitle": f"Годы {y1}–{y2} · сравнение закрытия года Y-1 с açılış года Y · Валюта: {C.report_label(d)}",
            "columns": cols, "rows": rows, "totals": {k: round(v, 2) for k, v in tot.items()},
            "checks": [{"name": "Разрывы переноса (сводка)", "ok": not (n_gaps_total or n_nc_total), "level": "warn" if (n_gaps_total or n_nc_total) else "ok",
                        "detail": f"балансовых счетов с расхождением: {n_gaps_total}, Σ|Δ| {C.fmt_money(round(abs_total / 100.0 / scale, 2))}; "
                                  f"не переносятся по построению (классы 6–7, 590): {n_nc_total}, Σ|Δ| {C.fmt_money(round(abs_nc_total / 100.0 / scale, 2))}"
                                  + ("" if show_nc else " - скрыты")}],
            "warnings": warnings}
