"""Общее для движков на парах корреспонденции (card, analysis, ledger, chess, journal): выбор счетов, оси (уровень счёта /
уровень классификатора), выборка пар из кэша с фильтрами, графы по месяцам.

Фильтры по проводкам (centers/trcodes/text) для пар: trcodes — по ордеру; centers и text — «пара проходит, если проходит любая из двух
проводок» (центр затрат стоит только на 7xx-стороне, текст обычно общий у ордера). include_closing=false — pairs.is_closing=0.
Фильтры по счетам/группам для пар применяются к «своей» стороне отчёта (у шахматки — к обеим сторонам, это подматрица)."""
from __future__ import annotations

import calendar
from dataclasses import dataclass

from .. import accounts as acc_mod
from .. import cache, plan
from . import common as C
from .common import Context, ReportError, TOL

ROW_LIMIT = 20000


# ---------------------------------------------------------------------------------------------
# спецификация счетов
# ---------------------------------------------------------------------------------------------

def parse_account_spec(v) -> tuple[list[str], list[dict]]:
    """account: строка масок через запятую/«;», либо {classifier, group}, либо список таких элементов."""
    masks: list[str] = []
    groups: list[dict] = []

    def add(x):
        if x is None or x == "":
            return
        if isinstance(x, dict):
            if x.get("classifier") is not None:
                groups.append({"classifier": int(x["classifier"]), "group": x.get("group")})
        elif isinstance(x, (list, tuple)):
            for y in x:
                add(y)
        else:
            for p in str(x).replace(";", ",").split(","):
                p = p.strip()
                if p:
                    masks.append(p)

    add(v)
    return masks, groups


def spec_label(masks: list[str], groups: list[dict]) -> str:
    return ", ".join(masks + [f"группа {g['classifier']}:{g['group']}" for g in groups])


class Sel:
    """Множество листовых счетов по году: (spec account/маски/группы) ∩ пользовательские фильтры filters.accounts/groups."""

    def __init__(self, ctx: Context, masks: list[str] | None = None, groups: list[dict] | None = None):
        self.ctx = ctx
        self.masks = masks or []
        self.groups = groups or []
        self._cache: dict[int, set[str]] = {}
        self._active: dict[int, list[str]] = {}

    def active(self, year: int) -> list[str]:
        if year not in self._active:
            firm = next(s.firm for s in self.ctx.segments if s.key == year)
            conn = cache.connect()
            try:
                self._active[year] = [r[0] for r in conn.execute("SELECT DISTINCT account FROM lines WHERE firm=?", (firm,))]
            finally:
                conn.close()
        return self._active[year]

    def user_filtered(self, year: int) -> set[str]:
        leaves = {(year, c): C.LeafAgg(year, c) for c in self.active(year)}
        return {k[1] for k in C.filter_leaves(self.ctx, leaves)}

    def codes(self, year: int) -> set[str]:
        if year not in self._cache:
            base = self.user_filtered(year)
            if self.masks or self.groups:
                keep: set[str] = set()
                if self.masks:
                    keep |= {c for c in base if acc_mod.matches_any(c, self.masks)}
                for g in self.groups:
                    keep |= group_codes(self.ctx, year, list(base), g)
                base = keep
            self._cache[year] = base
        return self._cache[year]

    def drill(self) -> dict:
        out: dict = {}
        if self.masks:
            out["accounts"] = list(self.masks)
        if len(self.groups) == 1:
            out["group"] = dict(self.groups[0])
        elif self.groups:
            out["groups"] = [dict(g) for g in self.groups]
        return out


def group_codes(ctx: Context, year: int, codes: list[str], g: dict) -> set[str]:
    cid, gid = g.get("classifier"), g.get("group")
    rs = ctx.resolver(cid)
    if gid is not None and gid != -1 and gid not in rs.groups:
        raise ReportError(f"Группа {gid} не найдена в классификаторе {cid}")
    ids = rs.descendants(gid) if gid not in (None, -1) else set()
    res = ctx.resolve(cid, year, codes)
    out = set()
    for c in codes:
        r = res[c]
        if (gid is None and r.kind == "none") or (gid == -1 and r.kind == "conflict") or (r.kind == "ok" and r.group in ids):
            out.add(c)
    return out


# ---------------------------------------------------------------------------------------------
# оси
# ---------------------------------------------------------------------------------------------

@dataclass
class Key:
    sid: str
    code: str
    name: str
    sort: tuple


class Axis:
    """Ось матрицы/графы: account (level 0..4 — число точек в коде ключа) или classifier (id, depth — глубина группы)."""

    def __init__(self, ctx: Context, spec: dict | None, default_level: int = 0):
        spec = spec or {}
        self.ctx = ctx
        self.kind = spec.get("kind") or "account"
        if self.kind not in ("account", "classifier"):
            raise ReportError("Ось: kind = account | classifier")
        self.level = int(spec.get("level") if spec.get("level") is not None else default_level)
        if not 0 <= self.level <= 6:
            raise ReportError("Уровень счёта оси: 0..4")
        self.cid = spec.get("id")
        self.depth = int(spec.get("depth") or 1)
        if self.kind == "classifier":
            if not isinstance(self.cid, int):
                raise ReportError("Для оси по классификатору нужен id классификатора")
            self.rs = ctx.resolver(self.cid)
        self._k: dict[tuple[int, str], Key] = {}
        self._names: dict[str, tuple[int, str]] = {}

    def key(self, year: int, code: str) -> Key:
        k = self._k.get((year, code))
        if k is not None:
            return k
        if self.kind == "account":
            kc = plan.current().trunc(code, self.level)
            nm = self.ctx.plan(year).get(kc, {}).get("name", "")
            prev = self._names.get(kc)
            if nm and (prev is None or year >= prev[0]):
                self._names[kc] = (year, nm)
            k = Key(kc, kc, nm, (kc,))
        else:
            r = self.ctx.resolve(self.cid, year, [code])[code]
            if r.kind == "ok":
                path = self.rs.path(r.group)[: self.depth]
                g = path[-1]
                gg = self.rs.groups[g]
                k = Key(f"g{self.cid}:{g}", gg["code"], gg["name"], (0, gg["sort"], gg["code"]))
            elif r.kind == "conflict":
                k = Key("x", "", "Конфликт правил", (1, 0, "~"))
            else:
                k = Key("u", "", "Не сопоставлено", (2, 0, "~"))
        self._k[(year, code)] = k
        return k

    def name_of(self, k: Key) -> str:
        if self.kind == "account":
            return self._names.get(k.sid, (0, k.name))[1] or k.name
        return k.name

    def drill(self, k: Key) -> dict:
        """Фрагмент drill для «корреспондент = ключ оси»."""
        if self.kind == "account":
            return {"corr": [k.sid]}
        if k.sid.startswith("g"):
            return {"corr_groups": [{"classifier": self.cid, "group": int(k.sid.split(":")[1])}]}
        return {"corr_groups": [{"classifier": self.cid, "group": -1 if k.sid == "x" else None}]}


# ---------------------------------------------------------------------------------------------
# выборка пар
# ---------------------------------------------------------------------------------------------

def line_filter_pairs_sql(d: dict) -> tuple[str, str, list]:
    """(join, условия, параметры) для pairs p. Условия по проводкам подключают lines ld (Дт-строка) и lk (Кт-строка)."""
    f = d["filters"]
    cond, params, join = "", [], ""
    if not d["include_closing"]:
        cond += " AND p.is_closing=0"
    if f["centers"] or f["trcodes"] or f["text"] or C.attr_filters_active(d):
        join = " JOIN lines ld ON ld.firm=p.firm AND ld.id=p.dt_line JOIN lines lk ON lk.firm=p.firm AND lk.id=p.kt_line"
        # подразделение/отдел/источник/автор: пара попадает в отчёт, только если обе её проводки (Дт и Кт) проходят отбор
        for al in ("ld", "lk"):
            asql, aparams = C.attr_cond_sql(d, al)
            cond += asql
            params += aparams
        if f["trcodes"]:
            cond += " AND ld.trcode IN (" + ",".join("?" * len(f["trcodes"])) + ")"
            params += f["trcodes"]
        if f["centers"]:
            ph = ",".join("?" * len(f["centers"]))
            cond += f" AND (ld.center IN ({ph}) OR lk.center IN ({ph}))"
            params += f["centers"] * 2
        if f["text"]:
            cond += " AND (instr(lower(ld.text),?)>0 OR instr(lower(ld.fiche_text),?)>0 OR instr(lower(lk.text),?)>0)"
            t = f["text"].lower()
            params += [t, t, t]
    return join, cond, params


def attr_pair_sql(d: dict) -> tuple[str, str, list]:
    """Только отбор по атрибутам (подразделение/отдел/источник/автор) для pairs p: (join, условия, параметры); пара проходит, если обе проводки проходят."""
    if not C.attr_filters_active(d):
        return "", "", []
    join = " JOIN lines ld ON ld.firm=p.firm AND ld.id=p.dt_line JOIN lines lk ON lk.firm=p.firm AND lk.id=p.kt_line"
    cond, params = "", []
    for al in ("ld", "lk"):
        asql, aparams = C.attr_cond_sql(d, al)
        cond += asql
        params += aparams
    return join, cond, params


def fetch_pairs(ctx: Context, gran: str = "", with_fiche: bool = False):
    """Генератор (seg, rows): rows = (dt_acc, kt_acc, g, amount, usd_dt, usd_kt, prop) — суммы в копейках после деноминации (float);
    g — '' | 'YYYY-MM' (gran=month) | 'YYYY-MM-DD' (day) | 'fiche_id' (fiche); prop=1, если в сумме есть расчётные (PROP) пары."""
    d = ctx.d
    join, cond, params = line_filter_pairs_sql(d)
    gexpr = {"": "''", "month": "substr(p.date,1,7)", "day": "p.date", "fiche": "p.fiche_id"}[gran]
    conn = cache.connect()
    try:
        for seg in ctx.segments:
            sql = (f"SELECT p.dt_account, p.kt_account, {gexpr}, SUM(p.amount), SUM(p.usd_amount), SUM(p.usd_kt_amount), "
                   f"MAX(p.method='PROP'), MAX(p.date) FROM pairs p{join} WHERE p.firm=? AND p.date>=? AND p.date<=?{cond} GROUP BY 1,2,3")
            div = ctx.divisor(seg)
            rows = [(a, b, g, am / div, ud, uk, pr, dt) for a, b, g, am, ud, uk, pr, dt in
                    conn.execute(sql, [seg.firm, seg.d_from.isoformat(), seg.d_to.isoformat(), *params])]
            yield seg, rows
    finally:
        conn.close()


def amt_of(ctx: Context, row, side: str) -> float:
    """Сумма пары в нужной валюте: side='dt' — со стороны Дт (usd_amount), 'kt' — со стороны Кт (usd_kt_amount)."""
    if ctx.usd_main:
        return row[4] if side == "dt" else row[5]
    return row[3]


# ---------------------------------------------------------------------------------------------
# остатки (через тот же путь, что и ОСВ)
# ---------------------------------------------------------------------------------------------

def leaves_for(ctx: Context, sel: Sel, by_month: bool = False) -> dict[tuple[int, str], C.LeafAgg]:
    """Листья ОСВ (вход, обороты, остаток) по выбранным счетам — независимый от пар путь для сверки."""
    leaves = C.aggregate_leaves(ctx, by_month)
    keep = {}
    by_year: dict[int, set[str]] = {}
    for (y, c), a in leaves.items():
        s = by_year.get(y)
        if s is None:
            s = by_year[y] = sel.codes(y)
        if c in s:
            keep[(y, c)] = a
    return keep


def blk(ctx: Context) -> int:
    return 1 if ctx.usd_main else 0


def sum_leaves(ctx: Context, leaves, expanded: bool = False) -> dict:
    """Σ по листьям: open (net, pos, neg), t_dt, t_kt, close (net, pos, neg) в копейках после деноминации."""
    o = {"open": 0.0, "open_pos": 0.0, "open_neg": 0.0, "dt": 0.0, "kt": 0.0, "close": 0.0, "close_pos": 0.0, "close_neg": 0.0}
    for a in leaves.values():
        v = a.usd if ctx.usd_main else a.tmt
        o["open"] += v[0]
        o["open_pos"] += max(v[0], 0.0)
        o["open_neg"] += max(-v[0], 0.0)
        o["dt"] += v[1]
        o["kt"] += v[2]
        o["close"] += v[3]
        o["close_pos"] += max(v[3], 0.0)
        o["close_neg"] += max(-v[3], 0.0)
    return o


def bal_cols(ctx: Context, s: dict, which: str, expanded: bool) -> tuple[float, float]:
    """(дебет, кредит) сальдо: свёрнутое по нетто либо развёрнутое по листьям."""
    if expanded:
        return s[which + "_pos"], s[which + "_neg"]
    n = s[which]
    return max(n, 0.0), max(-n, 0.0)


def out_val(ctx: Context, x: float):
    """None для нуля (пустая ячейка), иначе число отчёта."""
    v = ctx.out(x)
    return v if v != 0 else None


# ---------------------------------------------------------------------------------------------
# графы / месяцы
# ---------------------------------------------------------------------------------------------

def month_period(ym: str) -> dict:
    y, m = int(ym[:4]), int(ym[5:])
    return {"date_from": f"{ym}-01", "date_to": f"{ym}-{calendar.monthrange(y, m)[1]:02d}"}


def clip_period(ctx: Context, ym: str) -> dict:
    p = month_period(ym)
    return {"date_from": max(p["date_from"], ctx.d1.isoformat()), "date_to": min(p["date_to"], ctx.d2.isoformat())}


def need_account(ctx: Context, masks, groups, what: str) -> None:
    f = ctx.d["filters"]
    if not (masks or groups or f["accounts"] or f["groups"]):
        raise ReportError(f"Укажите счёт, маску или группу для отчёта «{what}» (поле «Счёт» или фильтры по счетам)")


def finish(d: dict, ctx: Context, title: str, extra_sub: str, columns, rows, totals, checks, warnings) -> dict:
    if len(rows) > ROW_LIMIT:
        raise ReportError("Слишком много строк (более 20 000) — сократите период, глубину или добавьте фильтры")
    info = C.attr_filter_check(ctx)
    if info:
        checks = list(checks) + [info]
    return {"title": d.get("title") or title, "subtitle": C.subtitle(ctx, extra_sub), "columns": columns, "rows": rows,
            "totals": totals, "checks": checks, "warnings": warnings}


def mask_name(ctx: Context, mask: str) -> str:
    """Название счёта/маски из плана: точный код, либо код без «*», либо (для нескольких счетов) «Счета <маска>: первое название и др.»."""
    pl = ctx.plan(ctx.segments[-1].key)
    base = plan.current().mask_name(mask)
    if mask in pl:
        return pl[mask].get("name") or ""
    if base in pl:
        return pl[base].get("name") or ""
    hit = sorted(c for c in pl if acc_mod.code_matches(c, mask))
    if not hit:
        return ""
    return (pl[hit[0]].get("name") or "") + (f" и ещё {len(hit) - 1} сч." if len(hit) > 1 else "")


def group_name(g: dict) -> str:
    from .. import store
    c = store.connect()
    try:
        r = c.execute("SELECT name FROM cls_groups WHERE id=? AND classifier_id=?", (g.get("group"), g.get("classifier"))).fetchone()
        k = c.execute("SELECT name FROM classifiers WHERE id=?", (g.get("classifier"),)).fetchone()
    finally:
        c.close()
    return (r["name"] if r else f"группа {g.get('group')}") + (f" ({k['name']})" if k else "")


def missing_mask_warnings(ctx: Context, d: dict) -> list[str]:
    """«по маске … счетов не найдено» - для масок account и filters.accounts, ни одна из которых не совпала со счетами плана периода."""
    masks, _ = parse_account_spec(d.get("account"))
    masks = masks + list(d["filters"]["accounts"])
    out = []
    plans = [ctx.plan(s.key) for s in ctx.segments]
    for m in masks:
        if not any(acc_mod.code_matches(c, m) for p in plans for c in p):
            out.append(f"По маске «{m}» счетов в плане счетов за период не найдено - отчёт пуст")
    return out


def common_warnings(ctx: Context, d: dict) -> list[str]:
    w = list(ctx.warnings)
    w += missing_mask_warnings(ctx, d)
    if not d["include_closing"]:
        w.append("Закрывающие ордера исключены из сальдо на начало, оборотов и сальдо на конец")
    f = d["filters"]
    if f["centers"] or f["trcodes"] or f["text"]:
        w.append("Фильтры по центрам/типам ордеров/тексту: для пар «проходит любая из двух проводок»; сверка с ОСВ не выполняется")
    if C.attr_filters_active(d):
        w.append("Отбор по подразделению/отделу/источнику/автору: пара корреспонденции попадает в отчёт, только если обе её проводки (дебетовая и "
                 "кредитовая) проходят отбор; пары между разными подразделениями не показываются; сверка с ОСВ не выполняется")
    dw = C.denom_warning(ctx)
    if dw:
        w.append(dw)
    return w


def method_note(ctx: Context, d: dict, sel_dt: Sel | None = None) -> dict:
    """Доля метода в оборотах периода (по всем парам периода, без учёта выбора счетов) — для информационного контроля."""
    out = {"SEQ": 0.0, "CROSS": 0.0, "PROP": 0.0, "MANUAL": 0.0}
    disputed = 0.0
    join, cond, params = line_filter_pairs_sql(d)
    conn = cache.connect()
    try:
        for seg in ctx.segments:
            div = ctx.divisor(seg)
            for m, a, dis in conn.execute(
                    f"SELECT p.method, SUM(p.amount), SUM(CASE WHEN p.disputed=1 THEN p.amount ELSE 0 END) FROM pairs p{join} "
                    f"WHERE p.firm=? AND p.date>=? AND p.date<=?{cond} GROUP BY 1",
                    [seg.firm, seg.d_from.isoformat(), seg.d_to.isoformat(), *params]):
                out[m] = out.get(m, 0.0) + a / div
                disputed += dis / div
    finally:
        conn.close()
    out["_disputed"] = disputed
    return out


def method_check(ctx: Context, d: dict) -> dict:
    m = method_note(ctx, d)
    total = sum(v for k, v in m.items() if not k.startswith("_"))
    if not total:
        return {"name": "Метод корреспонденции", "ok": True, "detail": "нет проводок"}
    sh = lambda k: f"{m.get(k, 0.0) / total * 100:.2f}%"  # noqa: E731
    det = (f"по порядку {sh('SEQ')}, по перекрёстной ссылке {sh('CROSS')}, пропорционально {sh('PROP')}, вручную {sh('MANUAL')}; спорные: "
           f"{C.fmt_money(ctx.out(m['_disputed']))}")
    return {"name": "Доля методов корреспонденции (весь оборот периода)", "ok": True, "detail": det}
