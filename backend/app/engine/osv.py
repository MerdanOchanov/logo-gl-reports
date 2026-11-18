"""Оборотно-сальдовая ведомость (type=osv)."""
from __future__ import annotations

from .. import dsparams
from . import common as C
from .common import Context, ReportError, TOL


# индексы вектора узла: блок местной валюты 0..7, блок отчётной валюты 8..15, месяцы с 16 по 4 числа (dt, kt, usd_dt, usd_kt)
def leaf_vec(a: C.LeafAgg, months: list[str], branches: list[int] | None = None) -> list[float]:
    """Вектор листа: 0..15 — блоки местной/отчётной валюты; затем по 4 числа на месяц или на подразделение (взаимно исключают друг друга); в конце 2 числа — количество Дт/Кт."""
    branches = branches or []
    nslots = len(months) + len(branches)
    v = [0.0] * (16 + 4 * nslots + 2)
    for base, src in ((0, a.tmt), (8, a.usd)):
        on, tdt, tkt, cn = src
        v[base + 0], v[base + 1], v[base + 2] = on, max(on, 0.0), max(-on, 0.0)
        v[base + 3], v[base + 4] = tdt, tkt
        v[base + 5], v[base + 6], v[base + 7] = cn, max(cn, 0.0), max(-cn, 0.0)
    for i, ym in enumerate(months):
        m = a.months.get(ym)
        if m:
            v[16 + 4 * i: 20 + 4 * i] = m
    for i, br in enumerate(branches):
        m = a.branches.get(br)
        if m:
            v[16 + 4 * i: 20 + 4 * i] = m
    v[16 + 4 * nslots], v[16 + 4 * nslots + 1] = a.qty
    return v


class Columns:
    """Определение граф и извлечение значений из вектора узла."""

    def __init__(self, ctx: Context, months: list[str], branches: list[int] | None = None):
        d = ctx.d
        self.expanded = d["columns"]["balance"] == "expanded"
        self.base = 8 if ctx.usd_main else 0
        self.months = months if d["columns"]["by_month"] else []
        self.branches = branches or []
        self.qty = d["columns"]["qty"]
        self.qty_keys = ("qty_dt", "qty_kt")
        self.branch_cols: dict[str, int] = {}   # ключ графы -> номер подразделения
        self.show_usd = d["columns"]["show_usd"] and not ctx.usd_main
        self.spec: list[tuple] = []     # (key, group, title, extractor)
        b = self.base
        self.spec += [
            ("open_dt", "Сальдо на начало", "Дебет", self._bal(b, 0, +1)),
            ("open_kt", "Сальдо на начало", "Кредит", self._bal(b, 0, -1)),
        ]
        for i, ym in enumerate(self.months):
            off = 16 + 4 * i + (2 if ctx.usd_main else 0)
            g = C.month_title(ym)
            self.spec += [(f"turn_dt_{ym}", g, "Дебет", self._at(off)), (f"turn_kt_{ym}", g, "Кредит", self._at(off + 1))]
        names = ctx.names() if self.branches else None
        for i, br in enumerate(self.branches):
            off = 16 + 4 * i + (2 if ctx.usd_main else 0)
            g = "Обороты: " + names.branch(br)
            bk = branch_key(br)
            self.spec += [(f"turn_dt_{bk}", g, "Дебет", self._at(off)), (f"turn_kt_{bk}", g, "Кредит", self._at(off + 1))]
            self.branch_cols[f"turn_dt_{bk}"] = self.branch_cols[f"turn_kt_{bk}"] = br
        tg = "Обороты за период" if not (self.months or self.branches) else "Обороты за период (итого)"
        self.spec += [
            ("turn_dt", tg, "Дебет", self._at(b + 3)),
            ("turn_kt", tg, "Кредит", self._at(b + 4)),
            ("close_dt", "Сальдо на конец", "Дебет", self._bal(b, 5, +1)),
            ("close_kt", "Сальдо на конец", "Кредит", self._bal(b, 5, -1)),
        ]
        if self.show_usd:
            rc = dsparams.get("currency_report")["code"]
            b = 8
            self.spec += [
                ("open_dt_usd", rc + ": сальдо на начало", "Дебет", self._bal(b, 0, +1)),
                ("open_kt_usd", rc + ": сальдо на начало", "Кредит", self._bal(b, 0, -1)),
                ("turn_dt_usd", rc + ": обороты", "Дебет", self._at(b + 3)),
                ("turn_kt_usd", rc + ": обороты", "Кредит", self._at(b + 4)),
                ("close_dt_usd", rc + ": сальдо на конец", "Дебет", self._bal(b, 5, +1)),
                ("close_kt_usd", rc + ": сальдо на конец", "Кредит", self._bal(b, 5, -1)),
            ]
        if self.qty:
            q0 = 16 + 4 * (len(self.months) + len(self.branches))
            self.spec += [("qty_dt", "Количество (оборот)", "Дебет", self._at(q0)), ("qty_kt", "Количество (оборот)", "Кредит", self._at(q0 + 1))]
        self.keys = [s[0] for s in self.spec]
        self.fn = {s[0]: s[3] for s in self.spec}
        self.core = ["open_dt", "open_kt", "turn_dt", "turn_kt", "close_dt", "close_kt"]

    def _at(self, i):
        return lambda v: v[i]

    def _bal(self, base, off, sign):
        # off=0 вход, 5 — остаток; в блоке: net, pos, neg
        net, pos, neg = base + off, base + off + 1, base + off + 2
        if self.expanded:
            return (lambda v: v[pos]) if sign > 0 else (lambda v: v[neg])
        return (lambda v: max(v[net], 0.0)) if sign > 0 else (lambda v: max(-v[net], 0.0))

    def values(self, vec) -> dict[str, float]:
        return {k: f(vec) for k, f in self.fn.items()}

    def out_values(self, ctx: Context, vec) -> dict[str, float]:
        """Значения для вывода: деньги — ctx.out (копейки -> единицы/тысячи), количество — как есть (до 3 знаков, без масштаба)."""
        return {k: (round(v, 3) + 0.0 if k in self.qty_keys else ctx.out(v)) for k, v in self.values(vec).items()}

    def defs(self) -> list[dict]:
        out = []
        for k, g, t, _ in self.spec:
            c = {"key": k, "title": t, "group": g, "type": "qty" if k in self.qty_keys else "money",
                 "drill": None if k.endswith("_usd") else ("dt" if "_dt" in k else "kt")}
            if k in self.branch_cols:
                c["branch"] = self.branch_cols[k]    # номер подразделения: расшифровка ячейки = /api/drill с branches:[branch]
            if k[-7:-4] != "" and k[-7:].count("-") == 1 and k[-7:-3].isdigit():
                ym = k[-7:]
                y, m = int(ym[:4]), int(ym[5:])
                import calendar
                c["period"] = {"date_from": f"{ym}-01", "date_to": f"{ym}-{calendar.monthrange(y, m)[1]:02d}"}
            out.append(c)
        return out


def branch_key(br: int) -> str:
    """Суффикс ключа графы подразделения: b0, b1, … (для номера < 0 — bm1)."""
    return f"b{br}" if br >= 0 else f"bm{-br}"


def run(defn: dict) -> dict:
    d = C.normalize(defn)
    ctx = Context(d)
    by_month = d["columns"]["by_month"]
    by_branch = d["columns"]["by_branch"]
    months = C.month_list(ctx.d1, ctx.d2) if by_month else []
    if len(months) > 36:
        raise ReportError("Разбивка по месяцам возможна не более чем на 36 месяцев")
    warnings = list(ctx.warnings)

    leaves = C.aggregate_leaves(ctx, by_month, by_branch)
    leaves = C.filter_leaves(ctx, leaves)
    branches = sorted({br for a in leaves.values() for br in a.branches}) if by_branch else []
    cols = Columns(ctx, months, branches)
    items = [(y, c, leaf_vec(a, months, branches)) for (y, c), a in sorted(leaves.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
    vec_len = 16 + 4 * (len(months) + len(branches)) + 2
    total_vec = [0.0] * vec_len
    for _, _, v in items:
        for i in range(vec_len):
            total_vec[i] += v[i]

    roots, stats = C.build_tree(ctx, items, vec_len)

    # ---- предупреждения ---------------------------------------------------------------------
    if any(r["kind"] == "classifier" for r in d["rows"]):
        if stats["conflict"]:
            codes = sorted({c for _, c in stats["conflict"]})
            warnings.append(f"Конфликт правил классификатора: {len(codes)} сч. попали в несколько групп (показаны отдельной строкой): "
                            + ", ".join(codes[:8]) + ("…" if len(codes) > 8 else ""))
        if stats["skipped"]:
            s = {k: sum(abs(x[2][i]) for x in stats["skipped"]) for k, i in (("turn", 3), ("open", 0), ("close", 5))}
            warnings.append(f"Строка «не сопоставлено» скрыта в настройках отчёта: {len({c for _, c, _ in stats['skipped']})} сч., "
                            f"обороты Дт {C.fmt_money(ctx.out(sum(x[2][3] for x in stats['skipped'])))} не показаны")
    if ctx.cross_year:
        deep = any(r["kind"] == "account" and (r.get("max_level") is None or r.get("max_level") >= 2) for r in d["rows"])
        if deep:
            warnings.append("Период охватывает несколько лет: счета сводятся по коду. Коды аналитики (уровни 2 и глубже) в разные годы "
                            "могут означать разные организации; сопоставимы уровни 0–1.")
    dw = C.denom_warning(ctx)
    if dw:
        warnings.append(dw)
    if not d["include_closing"]:
        warnings.append("Закрывающие ордера исключены из сальдо на начало, оборотов и сальдо на конец (картина «как будто закрытия не было»)")
    f_ = d["filters"]
    if f_["centers"] or f_["trcodes"] or f_["text"]:
        warnings.append("Фильтры по центрам/типам ордеров/тексту применены к движению; входящее сальдо (açılış) не фильтруется")
    if C.attr_filters_active(d):
        warnings.append("Отбор по подразделению/отделу/источнику/автору применён и к движению, и к строкам открытия (açılış): у них свои подразделение и автор")
    if cols.qty and any(r.children for r in roots):
        warnings.append("Количество: на уровнях выше листового счёта и в итоговой строке складываются количества в разных единицах измерения "
                        "(кг, шт., м и т.д.) — осмыслены только значения по счетам с одной единицей")

    # ---- проверка «нач + обороты = кон» по счетам (слияние лет по коду) ---------------------------
    by_code: dict[str, list[float]] = {}
    for y, c, v in items:
        g = by_code.setdefault(c, [0.0, 0.0, 0.0, 0.0])
        g[0] += v[0 if not ctx.usd_main else 8]
        g[1] += v[3 if not ctx.usd_main else 11]
        g[2] += v[4 if not ctx.usd_main else 12]
        g[3] += v[5 if not ctx.usd_main else 13]
    gaps = {c: g[3] - (g[0] + g[1] - g[2]) for c, g in by_code.items() if abs(g[3] - (g[0] + g[1] - g[2])) >= TOL}
    gaps_unexplained = dict(gaps)
    if gaps:
        try:
            from . import carry_gaps as _cg
            known = _cg.boundary_gaps(ctx)
        except Exception:       # noqa: BLE001 - пояснение не обязательно: без него расхождение остаётся ошибкой
            known = None
        if known is not None:
            gaps_unexplained = {c: g for c, g in gaps.items() if abs(g - known.get(c, 0.0)) >= TOL}
        top = sorted(gaps, key=lambda c: -abs(gaps[c]))[:5]
        if not gaps_unexplained:
            what = ("все они объясняются известными разрывами переноса остатков между книгами (закрытие прошлого года не равно открытию "
                    "следующего, а классы 6–7 и счёт 590 в открытие не переносятся)")
        else:
            what = f"не объясняются известными разрывами переноса: {len(gaps_unexplained)} сч."
        warnings.append(f"Начальное сальдо + обороты ≠ конечное сальдо по {len(gaps)} сч.: {what}; подробно — отчёт «Разрывы переноса». "
                        "Крупнейшие: " + "; ".join(f"{c}: {C.fmt_money(ctx.out(gaps[c]))}" for c in top))

    # ---- обход дерева ---------------------------------------------------------------------------
    sort = d["options"]["sort"]
    hide = d["options"]["hide_zero"]

    def colval(n, key):
        f = cols.fn.get(key)
        return f(n.vec) if f else 0.0

    def nonzero(n) -> bool:
        return any(abs(cols.fn[k](n.vec)) >= TOL for k in cols.core)

    vis_cache: dict[str, bool] = {}

    def visible(n) -> bool:
        if not hide:
            return True
        if n.id not in vis_cache:
            vis_cache[n.id] = nonzero(n) or any(visible(c) for c in n.children.values())
        return vis_cache[n.id]

    rows_out: list[dict] = []
    max_nodes = 20000

    def emit(nodes, level_parent_id):
        for n in C.sort_children(nodes, sort, colval):
            if not visible(n):
                continue
            vals = cols.out_values(ctx, n.vec)
            rows_out.append({"id": n.id, "parent": level_parent_id, "level": n.level, "kind": n.kind, "code": n.code, "name": n.name,
                             "values": vals, "bold": bool(n.children), "drill": C.drill_of(n)})
            if len(rows_out) > max_nodes:
                raise ReportError("Слишком много строк (более 20 000) — уменьшите глубину или добавьте фильтры")
            if n.children:
                emit(list(n.children.values()), n.id)

    emit(roots, None)

    # итого: сумма отображаемых значений корневых узлов
    totals_raw = {k: 0.0 for k in cols.keys}
    for r in roots:
        for k, v in cols.values(r.vec).items():
            totals_raw[k] += v
    totals = {k: (round(v, 3) + 0.0 if k in cols.qty_keys else ctx.out(v)) for k, v in totals_raw.items()}
    rows_out.append({"id": "total", "parent": None, "level": 0, "kind": "total", "code": "", "name": "ИТОГО", "values": dict(totals),
                     "bold": True, "drill": {}})

    # ---- контроли ------------------------------------------------------------------------------
    checks: list[dict] = []
    filtered = C.any_filters_active(d)
    b = 8 if ctx.usd_main else 0
    if not filtered:
        dd = total_vec[b + 3] - total_vec[b + 4]
        ch = {"name": "Оборот Дт = оборот Кт", "ok": abs(dd) < TOL,
              "detail": f"Дт {C.fmt_money(ctx.out(total_vec[b + 3]))}, Кт {C.fmt_money(ctx.out(total_vec[b + 4]))}"}
        if not ch["ok"] and ctx.usd_main:   # REPORTNET (USD) по сторонам не балансируется - округление курсовых сумм
            ch["level"] = "warn"
            ch["detail"] += "; в " + C.report_label(ctx) + " (REPORTNET) обороты Дт и Кт не обязаны совпадать до цента - округление курсовых сумм"
        checks.append(ch)
        if not ctx.usd_main:  # USD не балансируется (разные курсы по сторонам)
            pass
        seg_first, seg_last = ctx.segments[0], ctx.segments[-1]
        for name, i, seg in (("Сальдо на начало: Σ нетто = 0", b + 0, seg_first), ("Сальдо на конец: Σ нетто = 0", b + 5, seg_last)):
            if ctx.usd_main:
                continue  # REPORTNET (USD) не обязан балансироваться
            checks.append(C.net_zero_check(ctx, name, total_vec[i], seg))
        if cols.expanded and not ctx.usd_main:
            for nm, ip, ineg, seg in (("Развёрнутое сальдо на начало: Дт = Кт", 1, 2, seg_first), ("Развёрнутое сальдо на конец: Дт = Кт", 6, 7, seg_last)):
                diff = total_vec[ip] - total_vec[ineg]
                ch = {"name": nm, "ok": abs(diff) < TOL, "level": "ok" if abs(diff) < TOL else "error",
                      "detail": f"Дт {C.fmt_money(ctx.out(total_vec[ip]))}, Кт {C.fmt_money(ctx.out(total_vec[ineg]))}"}
                checks.append(C.explain_by_imbalance(ctx, ch, diff, seg))
    if by_branch:
        for nm, ia, ib in (("Σ по подразделениям = общий оборот по дебету", 3, 0), ("Σ по подразделениям = общий оборот по кредиту", 4, 1)):
            s_br = sum(total_vec[16 + 4 * i + (2 if ctx.usd_main else 0) + ib] for i in range(len(branches)))
            checks.append({"name": nm, "ok": abs(s_br - total_vec[b + ia]) < TOL,
                           "detail": f"подразделений: {len(branches)}; Σ {C.fmt_money(ctx.out(s_br))}, всего {C.fmt_money(ctx.out(total_vec[b + ia]))}"})
    info = C.attr_filter_check(ctx)
    if info:
        checks.append(info)
    if not gaps:
        checks.append({"name": "Нач. сальдо + обороты = кон. сальдо по каждому счёту", "ok": True, "level": "ok", "detail": "расхождений нет"})
    elif not gaps_unexplained:
        checks.append({"name": "Нач. сальдо + обороты = кон. сальдо по каждому счёту", "ok": False, "level": "warn",
                       "detail": f"расхождений: {len(gaps)} сч.; все они объясняются известными разрывами переноса остатков между годами "
                                 "(закрытие прошлого года не равно открытию следующего), итоги по периоду сходятся"})
    else:
        checks.append({"name": "Нач. сальдо + обороты = кон. сальдо по каждому счёту", "ok": False, "level": "error",
                       "detail": f"расхождений: {len(gaps)} сч., из них не объясняется переносом между годами: {len(gaps_unexplained)}"})
    # родитель = сумма детей
    bad = 0

    def walk(n):
        nonlocal bad
        if n.children:
            for i in range(vec_len):
                if abs(sum(c.vec[i] for c in n.children.values()) + (n.own[i] if n.own else 0.0) - n.vec[i]) >= TOL:
                    bad += 1
                    break
            for c in n.children.values():
                walk(c)

    for r in roots:
        walk(r)
    checks.append({"name": "Родитель = сумма детей", "ok": bad == 0, "detail": "по всем узлам иерархии" if not bad else f"нарушений: {bad}"})
    if any(r["kind"] == "classifier" for r in d["rows"]):
        placed = [0.0] * vec_len
        for r in roots:
            for i in range(vec_len):
                placed[i] += r.vec[i]
        # при классификаторе в любой позиции корни содержат всё размещённое
        ok = all(abs(placed[i] - total_vec[i]) < TOL for i in (b + 0, b + 3, b + 4, b + 5))
        unm = sum(n.vec[b + 3] for n in _all_nodes(roots) if n.kind == "unmapped")
        checks.append({"name": "Группы + не сопоставлено = всего", "ok": ok,
                       "detail": f"размещено: оборот Дт {C.fmt_money(ctx.out(placed[b + 3]))} из {C.fmt_money(ctx.out(total_vec[b + 3]))}; "
                                 f"в «не сопоставлено / конфликт»: {C.fmt_money(ctx.out(unm))}"})

    return C.finalize_checks({"title": d.get("title") or "Оборотно-сальдовая ведомость",
            "subtitle": C.subtitle(ctx, "сальдо " + ("развёрнутое" if cols.expanded else "свёрнутое")),
            "columns": cols.defs(), "rows": rows_out, "totals": totals, "checks": checks, "warnings": warnings})


def _all_nodes(roots):
    for r in roots:
        yield r
        yield from _all_nodes(list(r.children.values()))
