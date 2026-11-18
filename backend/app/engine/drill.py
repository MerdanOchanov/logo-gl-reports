"""Расшифровка (drill) до проводок/пар корреспонденции и просмотр ордера целиком."""
from __future__ import annotations

from .. import accounts as acc_mod
from .. import attrs as attrs_mod
from .. import books, cache, plan
from . import common as C
from .common import ReportError
from .form import FormulaError, make_matcher, parse_selector


def _acc_sql(masks: list[str]) -> tuple[str, list]:
    expr, params = cache._account_expr(masks)
    return f"({expr})", params


def _mask_expr(col: str, masks: list[str]) -> tuple[str, list]:
    parts, params = [], []
    for m in masks:
        m = m.strip()
        if not m:
            continue
        if m.endswith("*"):
            parts.append(f"substr({col},1,{len(m) - 1})=?")
            params.append(m[:-1])
        elif ".." in m:
            raise ReportError("Диапазоны счетов (a..b) в расшифровке корреспондента не поддерживаются — используйте маски")
        else:
            dsql, dpar = plan.current().descendant_sql(col, m)
            parts.append(dsql)
            params += dpar
    return "(" + " OR ".join(parts) + ")", params


def _group_set(ctx, conn, seg, groups) -> set[str]:
    """Счета года сегмента, входящие в любую из групп (объединение)."""
    act = [r[0] for r in conn.execute("SELECT DISTINCT account FROM lines WHERE firm=?", (seg.firm,))]
    out: set[str] = set()
    for g in groups:
        cid, gid = g.get("classifier"), g.get("group")
        rs = ctx.resolver(cid)
        if gid not in (None, -1) and gid not in rs.groups:
            raise ReportError(f"Группа {gid} не найдена в классификаторе {cid}")
        res = ctx.resolve(cid, seg.key, act)
        ids = rs.descendants(gid) if gid not in (None, -1) else set()
        out |= {c for c in act if (gid is None and res[c].kind == "none") or (gid == -1 and res[c].kind == "conflict")
                or (res[c].kind == "ok" and res[c].group in ids)}
    return out


def _fill_tmp(conn, table: str, codes) -> None:
    conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS {table}(code TEXT PRIMARY KEY)")
    conn.execute(f"DELETE FROM {table}")
    conn.executemany(f"INSERT INTO {table} VALUES(?)", [(c,) for c in codes])


def _parse_sels(items, what: str) -> list:
    out = []
    for x in items:
        try:
            out.append(parse_selector(str(x).replace(",", "|").replace(";", "|")))
        except FormulaError as e:
            raise ReportError(f"{what}: «{x}» — {e.message}")
    return out


def _corr_set(ctx, conn, seg, inc_sels, exc_sels) -> set[str]:
    """Счета-партнёры года сегмента: подходят под любой включающий селектор (если они есть) и не подходят ни под один исключающий."""
    act = [r[0] for r in conn.execute("SELECT DISTINCT account FROM lines WHERE firm=?", (seg.firm,))]
    inc = [make_matcher(ctx, s_) for s_ in inc_sels]
    exc = [make_matcher(ctx, s_) for s_ in exc_sels]
    return {c for c in act if (not inc or any(m(seg.key, c) for m in inc)) and not any(m(seg.key, c) for m in exc)}


def _norm_groups(gs) -> list[dict]:
    out = []
    for g in gs:
        if not isinstance(g, dict) or g.get("classifier") is None:
            raise ReportError("Группа задаётся как {classifier, group}")
        out.append({"classifier": int(g["classifier"]), "group": g.get("group")})
    return out


def drill(req: dict) -> dict:
    """Проводки по условиям. Режим turn (по умолчанию) идёт по парам корреспонденции: строка на каждую пару проводки, corr — счёт
    партнёра по паре, method — метод пары (SEQ|CROSS|PROP|MANUAL), суммы строки — суммы пары. Параметры: accounts, group(s), side,
    corr (маски счетов-партнёров), corr_group(s) (группы классификатора для партнёров), method (строка или список), disputed (bool).
    Режимы open/close (проводки, формирующие входящее/исходящее сальдо) — без пар, corr = CROSSCODE строки."""
    p = req.get("period") or {}
    d = C.normalize({"period": p, "currency": req.get("currency"), "denominate": req.get("denominate", True),
                     "include_closing": req.get("include_closing", True),
                     "filters": {"centers": req.get("centers"), "trcodes": req.get("trcodes"), "text": req.get("text"),
                                 "branches": req.get("branches"), "departments": req.get("departments"),
                                 "sources": req.get("sources"), "authors": req.get("authors")},
                     "_force_attrs": True})
    ctx = C.Context(d)
    names = ctx.names()
    attr_on = C.attr_filters_active(d)
    op_sql, op_params = C.attr_cond_sql(d, "lines")
    usd = ctx.usd_main
    masks = [m for m in (req.get("accounts") or []) if m]
    groups = _norm_groups(req.get("groups") or ([req["group"]] if req.get("group") else []))
    side = req.get("side")
    corr = [m for m in (req.get("corr") or []) if m]
    cgroups = _norm_groups(req.get("corr_groups") or ([req["corr_group"]] if req.get("corr_group") else []))
    corr_ex = [x for x in (req.get("corr_exclude") or []) if str(x).strip()]
    # расширенный отбор партнёров: диапазоны a..b, объединения |, исключения ~, группы @, а также corr_exclude (исключающие селекторы)
    adv = bool(corr_ex) or any(any(ch in m for ch in ("..", "|", "~", "@", ",", ";")) for m in corr)
    corr_sels = _parse_sels(corr, "Корреспондент") if adv else []
    ex_sels = _parse_sels(corr_ex, "Исключение корреспондента") if adv else []
    if adv:
        corr = []
    methods = req.get("method")
    methods = [methods] if isinstance(methods, str) else list(methods or [])
    mode = req.get("mode") or "turn"
    limit = int(req.get("limit") or 200)
    offset = int(req.get("offset") or 0)
    nf, nf_params, open_ok = C.line_filter_sql(d)
    pair_mode = mode == "turn"
    conn = cache.connect()
    try:
        parts = []
        for seg in ctx.segments:
            if (mode == "open" and not seg.first) or (mode == "close" and not seg.last):
                continue
            where, params = ["firm=?"], [seg.firm]
            if masks:
                s_, pr = _acc_sql(masks)
                where.append(s_)
                params += pr
            codes = _group_set(ctx, conn, seg, groups) if groups else None
            if side == "dt":
                where.append("dt<>0")
            elif side == "kt":
                where.append("kt<>0")
            df, dt_ = seg.d_from.isoformat(), seg.d_to.isoformat()
            bf = seg.book_from.isoformat()
            if mode == "open":
                where.append("((is_opening=1 AND %d=1%s) OR (is_opening=0 AND date<?) OR (is_opening=0 AND date>=? AND date<?%s))" % (1 if open_ok else 0, op_sql, nf))
                params += [*op_params, bf, bf, df, *nf_params]
            elif mode == "close":
                where.append("((is_opening=1 AND %d=1%s) OR (is_opening=0 AND date<?) OR (is_opening=0 AND date>=? AND date<=?%s))" % (1 if open_ok else 0, op_sql, nf))
                params += [*op_params, bf, bf, dt_, *nf_params]
            else:
                where.append("is_opening=0 AND date>=? AND date<=?" + nf)
                params += [df, dt_, *nf_params]
            has_pairs = pair_mode and conn.execute("SELECT 1 FROM pairs WHERE firm=? LIMIT 1", (seg.firm,)).fetchone() is not None
            cg = _group_set(ctx, conn, seg, cgroups) if (cgroups and has_pairs) else None
            if adv:
                cs = _corr_set(ctx, conn, seg, corr_sels, ex_sels)
                cg = cs if cg is None else (cg & cs)
            parts.append((seg, where, params, codes, has_pairs, cg))

        def build(where, params, codes, has_pairs, cg):
            w = " AND ".join(where)
            if codes is not None:
                _fill_tmp(conn, "_dr", codes)
                w += " AND account IN (SELECT code FROM _dr)"
            if not has_pairs:      # нет пар (режимы open/close или фирма не рассчитана): CROSSCODE строки
                lw, lp = w, list(params)
                if adv and cg is not None:
                    _fill_tmp(conn, "_cg", cg)
                    lw += " AND cross_code IN (SELECT code FROM _cg)"
                if corr:
                    e, pr = _mask_expr("cross_code", corr)
                    lw += " AND " + e
                    lp += pr
                return None, lw, lp
            cond1 = cond2 = ""
            p1: list = []
            p2: list = []
            if corr:
                e, pr = _mask_expr("p.kt_account", corr)
                cond1 += " AND " + e
                p1 += pr
                e, pr = _mask_expr("p.dt_account", corr)
                cond2 += " AND " + e
                p2 += pr
            if cg is not None:
                _fill_tmp(conn, "_cg", cg)
                cond1 += " AND p.kt_account IN (SELECT code FROM _cg)"
                cond2 += " AND p.dt_account IN (SELECT code FROM _cg)"
            if methods:
                ph = ",".join("?" * len(methods))
                cond1 += f" AND p.method IN ({ph})"
                cond2 += f" AND p.method IN ({ph})"
                p1 += methods
                p2 += methods
            if req.get("disputed"):
                cond1 += " AND p.disputed=1"
                cond2 += " AND p.disputed=1"
            if attr_on:     # отбор по атрибутам: проводка-партнёр пары тоже должна проходить отбор (как в отчётах по парам)
                q_sql, q_params = C.attr_cond_sql(d, "Q")
                cond1 += " AND EXISTS (SELECT 1 FROM lines Q WHERE Q.firm=p.firm AND Q.id=p.kt_line" + q_sql + ")"
                p1 += q_params
                cond2 += " AND EXISTS (SELECT 1 FROM lines Q WHERE Q.firm=p.firm AND Q.id=p.dt_line" + q_sql + ")"
                p2 += q_params
            dtv = "p.usd_amount" if usd else "p.amount"
            ktv = "p.usd_kt_amount" if usd else "p.amount"
            cols = ("L.firm AS firm, L.fiche_id AS fiche_id, L.fiche_no AS fiche_no, L.date AS date, L.account AS account, "
                    "L.id AS lid, L.line_no AS line_no, L.text AS text, L.fiche_text AS fiche_text, L.branch AS br, L.dept AS dp, "
                    "L.qty_dt AS qd, L.qty_kt AS qk, L.dt AS ldt, L.kt AS lkt, p.amount AS pam")
            sql_parts, prm = [], []
            if side != "kt":
                sql_parts.append(f"SELECT {cols}, p.kt_account AS corr, {dtv} AS dtv, 0 AS ktv, p.method AS method, p.disputed AS disputed "
                                 f"FROM L JOIN pairs p ON p.firm=L.firm AND p.dt_line=L.id WHERE L.dt<>0{cond1}")
                prm += p1
            if side != "dt":
                sql_parts.append(f"SELECT {cols}, p.dt_account AS corr, 0 AS dtv, {ktv} AS ktv, p.method AS method, p.disputed AS disputed "
                                 f"FROM L JOIN pairs p ON p.firm=L.firm AND p.kt_line=L.id WHERE L.dt=0 AND L.kt<>0{cond2}")
                prm += p2
            sql = f"WITH L AS (SELECT * FROM lines WHERE {w}), u AS (" + " UNION ALL ".join(sql_parts) + ") "
            return sql, None, list(params) + prm

        total, s_dt, s_kt = 0, 0.0, 0.0
        counts = []
        dtc, ktc = ("usd_dt", "usd_kt") if usd else ("dt", "kt")
        for seg, where, params, codes, has_pairs, cg in parts:
            cte, lw, prm = build(where, params, codes, has_pairs, cg)
            div = ctx.divisor(seg, usd)
            if cte:
                r = conn.execute(cte + "SELECT COUNT(*), COALESCE(SUM(dtv),0), COALESCE(SUM(ktv),0) FROM u", prm).fetchone()
            else:
                r = conn.execute(f"SELECT COUNT(*), COALESCE(SUM({dtc}),0), COALESCE(SUM({ktc}),0) FROM lines WHERE {lw}", prm).fetchone()
            s_dt += r[1] / div / 100
            s_kt += r[2] / div / 100
            counts.append((seg, cte, lw, prm, codes, cg, r[0]))
            total += r[0]
        out = []
        skip, need = offset, limit
        for seg, cte, lw, prm, codes, cg, n in counts:
            if need <= 0:
                break
            if skip >= n:
                skip -= n
                continue
            if codes is not None:      # временные таблицы могли быть перезаполнены другим сегментом
                _fill_tmp(conn, "_dr", codes)
            if cg is not None:
                _fill_tmp(conn, "_cg", cg)
            div = ctx.divisor(seg, usd)
            acc_names = {r["code"]: r["name"] for r in acc_mod.get_plan_book(seg.key)}
            if cte:
                rows = conn.execute(cte + "SELECT firm, fiche_id, fiche_no, date, account, corr, dtv, ktv, text, fiche_text, method, disputed, "
                                    "br, dp, qd, qk, ldt, lkt, pam FROM u "
                                    "ORDER BY date, fiche_id, line_no, lid, corr LIMIT ? OFFSET ?", [*prm, need, skip]).fetchall()
                fa = attrs_mod.fiche_attrs(conn, seg.firm, [r[1] for r in rows])
                for r in rows:
                    lq = (r[14] or 0.0) + (r[15] or 0.0)     # количество проводки делится между её парами пропорционально сумме
                    lamt = r[16] or r[17]
                    qty = (lq * (r[18] or 0) / lamt) if lamt else lq
                    out.append({"firm": r[0], "year": seg.year, "fiche_id": r[1], "fiche_no": r[2], "date": r[3], "account": r[4],
                                "account_name": acc_names.get(r[4], ""), "corr": r[5], "dt": round(r[6] / div / 100, 2),
                                "kt": round(r[7] / div / 100, 2), "text": r[8] or r[9], "fiche_text": r[9], "method": r[10],
                                "disputed": r[11], **attrs_mod.row_attrs(names, r[12], r[13], qty, fa.get(r[1]))})
            else:
                rows = conn.execute(
                    f"SELECT firm, fiche_id, fiche_no, date, account, cross_code, {dtc}, {ktc}, text, fiche_text, id, branch, dept, qty_dt, qty_kt "
                    f"FROM lines WHERE {lw} ORDER BY date, fiche_id, line_no, id LIMIT ? OFFSET ?", [*prm, need, skip]).fetchall()
                fa = attrs_mod.fiche_attrs(conn, seg.firm, [r[1] for r in rows])
                for r in rows:
                    out.append({"firm": r[0], "year": seg.year, "fiche_id": r[1], "fiche_no": r[2], "date": r[3], "account": r[4],
                                "account_name": acc_names.get(r[4], ""), "corr": r[5], "dt": round(r[6] / div / 100, 2),
                                "kt": round(r[7] / div / 100, 2), "text": r[8] or r[9], "fiche_text": r[9], "method": None,
                                **attrs_mod.row_attrs(names, r[11], r[12], (r[13] or 0.0) + (r[14] or 0.0), fa.get(r[1]))})
            skip = 0
            need -= len(rows)
        return {"total": total, "sum_dt": round(s_dt, 2), "sum_kt": round(s_kt, 2), "lines": out, "warnings": ctx.warnings}
    finally:
        conn.close()


def fiche(firm: int, fiche_id: int, denominate: bool = True) -> dict | None:
    conn = cache.connect()
    try:
        label = cache.label_of_src(firm)
        if not label:
            raise ReportError(f"Фирма {firm} не найдена")
        f = {"year": label, "divisor": cache.divisor_of_src(firm)}
        rows = conn.execute("SELECT * FROM lines WHERE firm=? AND fiche_id=? ORDER BY line_no, id", (firm, fiche_id)).fetchall()
        if not rows:
            return None
        div = f["divisor"] if denominate else 1
        plan = {r["code"]: r["name"] for r in acc_mod.get_plan_srcs([firm], f["year"])}
        first = rows[0]
        names = attrs_mod.Names([firm])
        fa = attrs_mod.fiche_attrs(conn, firm, [fiche_id]).get(fiche_id)
        lines = [{"id": r["id"], "line_no": r["line_no"], "account": r["account"], "account_name": plan.get(r["account"], ""),
                  "dt": round(r["dt"] / div / 100, 2), "kt": round(r["kt"] / div / 100, 2),
                  "usd_dt": round(r["usd_dt"] / 100, 2), "usd_kt": round(r["usd_kt"] / 100, 2),
                  "corr": r["cross_code"], "center": r["center"], "text": r["text"],
                  "branch": names.branch(r["branch"]) if r["branch"] is not None else "",
                  "department": names.dept(r["dept"]) if r["dept"] is not None else "",
                  "qty": round((r["qty_dt"] or 0.0) + (r["qty_kt"] or 0.0), 3) or None} for r in rows]
        fx = attrs_mod.row_attrs(names, None, None, None, fa)
        pairs = [{"dt_account": r["dt_account"], "kt_account": r["kt_account"], "amount": round(r["amount"] / div / 100, 2),
                  "usd_amount": round(r["usd_amount"] / 100, 2), "method": r["method"], "disputed": r["disputed"],
                  "dt_line": r["dt_line"], "kt_line": r["kt_line"]}
                 for r in conn.execute("SELECT * FROM pairs WHERE firm=? AND fiche_id=? ORDER BY rowid", (firm, fiche_id))]
        from .. import store
        ov = store.connect()
        try:
            has_override = ov.execute("SELECT 1 FROM pair_overrides WHERE firm=? AND fiche_id=?", (firm, fiche_id)).fetchone() is not None
        finally:
            ov.close()
        return {"firm": firm, "year": f["year"], "fiche_id": fiche_id, "fiche_no": first["fiche_no"], "date": first["date"],
                "trcode": first["trcode"], "text": first["fiche_text"], "is_opening": first["is_opening"],
                "is_closing": max(r["is_closing"] for r in rows), "lines": lines, "pairs": pairs, "manual": has_override,
                "source": fx["source"], "author": fx["author"], "entered_at": fx["entered_at"], "doc_no": fx["doc_no"], "text2_4": fx["text2_4"],
                "source_id": fx["source_id"], "author_id": fx["author_id"]}
    finally:
        conn.close()
