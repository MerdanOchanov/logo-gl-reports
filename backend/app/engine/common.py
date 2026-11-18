"""Общие части движков отчётов: разбор definition, выборка из кэша (по годам-сегментам), фильтры, деноминация/единицы,
построение дерева строк (классификатор и/или уровни счёта в любом порядке), сортировка, обход в глубину.

Деньги в кэше — целые копейки «как в Logo». Здесь они переводятся в «копейки после деноминации» (float) и только на выходе
округляются: round(x / 100 / scale, 2). Суммы USD не делятся на divisor (REPORTNET непрерывен)."""
from __future__ import annotations

import re
import sqlite3
import tempfile  # noqa: F401
from dataclasses import dataclass, field
from typing import Optional
from datetime import date, datetime, timedelta

from .. import accounts as acc_mod
from .. import books, cache, classifiers, config, dsparams, store
from .. import plan as plan_mod


class ReportError(ValueError):
    """Ошибка описания отчёта / данных — текст по-русски, уходит клиенту как 400."""


MONTHS_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
SCALE_NAMES = {1: "в единицах", 1000: "в тысячах", 1000000: "в миллионах"}


# ---------------------------------------------------------------------------------------------
# definition
# ---------------------------------------------------------------------------------------------

def parse_date(s, what="date") -> date:
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        raise ReportError(f"Некорректная дата {'начала' if what == 'date_from' else 'окончания' if what == 'date_to' else ''} периода: «{s}», ожидается ГГГГ-ММ-ДД")


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def normalize(defn: dict) -> dict:
    if not isinstance(defn, dict):
        raise ReportError("Описание отчёта должно быть объектом")
    d = dict(defn)
    p = _obj(d.get("period"), "период", '{"date_from": "2026-01-01", "date_to": "2026-12-31"}')
    if not p.get("date_from") or not p.get("date_to"):
        raise ReportError("Не задан период отчёта: укажите даты начала и окончания")
    d1, d2 = parse_date(p["date_from"], "date_from"), parse_date(p["date_to"], "date_to")
    if d1 > d2:
        raise ReportError("Дата начала периода позже даты окончания")
    d["_d1"], d["_d2"] = d1, d2
    loc, rep_ = dsparams.get("currency_local"), dsparams.get("currency_report")
    cur = str(d.get("currency") or loc["code"]).upper()
    if cur in (loc["code"], "LOCAL"):
        d["_report"] = False
    elif cur in (rep_["code"], "REPORT"):
        if not rep_.get("enabled"):
            raise ReportError("В этой базе нет сумм в отчётной валюте")
        d["_report"] = True
    else:
        raise ReportError(f"Валюта «{cur}» не настроена в этом наборе данных (доступны: {loc['code']}, {rep_['code']})")
    d["currency"] = cur
    d["scale"] = d.get("scale") or 1
    if d["scale"] not in (1, 1000, 1000000):
        raise ReportError("Единицы измерения: единицы валюты, тысячи или миллионы")
    d["denominate"] = bool(d.get("denominate", True))
    d["include_closing"] = bool(d.get("include_closing", True))
    f = _obj(d.get("filters"), "отбор", '{"accounts": ["100*"]}')
    groups = _lst(f.get("groups"), "отбор по группам классификатора", '[{"classifier": 1, "group": 5}]')
    if any(not isinstance(g, dict) for g in groups):
        raise ReportError("Отбор по группам классификатора задан неверно: нужен список групп, у каждой — классификатор и группа")
    try:
        trcodes = [int(x) for x in _lst(f.get("trcodes"), "отбор по видам ордеров", "[1, 2]")]
    except (TypeError, ValueError):
        raise ReportError("Отбор по видам ордеров должен быть списком целых чисел")
    d["filters"] = {
        "accounts": [str(x).strip() for x in _lst(f.get("accounts"), "отбор по счетам", '["100*"]') if str(x).strip()],
        "groups": groups,
        "centers": [str(x).strip() for x in _lst(f.get("centers"), "отбор по центрам", '["01"]') if str(x).strip()],
        "trcodes": trcodes,
        "text": str(f.get("text") or "").strip(),
        "branches": _ints(f.get("branches"), "подразделениям"),
        "departments": _ints(f.get("departments"), "отделам"),
        "sources": _ints(f.get("sources"), "источникам"),
        "authors": _ints(f.get("authors"), "авторам"),
    }
    c = _obj(d.get("columns"), "графы", '{"balance": "net"}')
    bal = c.get("balance") or "net"
    if bal not in ("net", "expanded"):
        raise ReportError("Сальдо: свёрнутое или развёрнутое")
    d["_show_report_ignored"] = bool(c.get("show_usd", False)) and not rep_.get("enabled")
    d["columns"] = {"balance": bal, "by_month": bool(c.get("by_month", False)), "show_usd": bool(c.get("show_usd", False)) and bool(rep_.get("enabled")),
                    "by_branch": bool(c.get("by_branch", False)), "qty": bool(c.get("qty", False)), "extra": _extra(c.get("extra"))}
    if d["columns"]["by_month"] and d["columns"]["by_branch"]:
        raise ReportError("Нельзя одновременно разбить обороты по месяцам и по подразделениям — выберите что-то одно")
    o = _obj(d.get("options"), "параметры", '{"hide_zero": false}')
    sort = _obj(o.get("sort"), "сортировка", '{"by": "code", "dir": "asc"}')
    d["options"] = {"hide_zero": bool(o.get("hide_zero", False)),
                    "sort": {"by": sort.get("by") or "code", "dir": "desc" if sort.get("dir") == "desc" else "asc"},
                    "unmapped": bool(o.get("unmapped", True))}
    rows = _lst(d.get("rows"), "строки отчёта", '[{"kind": "account", "max_level": 1}]') or [{"kind": "account"}]
    for r in rows:
        if not isinstance(r, dict):
            raise ReportError("Строки отчёта заданы неверно: каждая строка — это счёт или классификатор")
        if r.get("kind") not in ("classifier", "account"):
            raise ReportError(f"Неизвестный вид строки «{r.get('kind')}» (допустимы «счёт» и «классификатор»)")
        if r["kind"] == "classifier" and not isinstance(r.get("id"), int):
            raise ReportError("Для строк по классификатору нужен id классификатора")
    d["rows"] = rows
    return d


def _human(name: str) -> str:
    """Название поля для пользователя: без английского имени в скобках («период (period)» -> «период»)."""
    return re.sub(r"\s*\([^)]*\)", "", name).strip()


def _obj(v, name: str, example: str) -> dict:
    """Поле описания отчёта, которое должно быть объектом (пусто - {}); иначе ReportError по-русски."""
    if v is None or v == "" or v == []:
        return {}
    if not isinstance(v, dict):
        raise ReportError("Раздел отчёта «%s» задан неверно" % _human(name))
    return v


def _lst(v, name: str, example: str) -> list:
    """Поле описания отчёта, которое должно быть списком (пусто - []); иначе ReportError по-русски."""
    if v is None or v == "" or v == {}:
        return []
    if not isinstance(v, (list, tuple)):
        raise ReportError("Раздел отчёта «%s» задан неверно: ожидается список" % _human(name))
    return list(v)


def _ints(v, what: str) -> list[int]:
    out: list[int] = []
    if v is None or v == "":
        return out
    if not isinstance(v, (list, tuple)):
        v = [v]
    for x in v:
        try:
            n = int(x)
        except (TypeError, ValueError):
            raise ReportError(f"Отбор по {what}: ожидается список номеров (целых чисел), получено «{x}»")
        if n not in out:
            out.append(n)
    return out


def _extra(v) -> list[str]:
    from .. import attrs
    if v is None or v == "":
        return []
    if not isinstance(v, (list, tuple)):
        raise ReportError("Дополнительные колонки заданы неверно: ожидается список колонок")
    out: list[str] = []
    for x in v:
        if x not in attrs.EXTRA_KEYS:
            raise ReportError(f"Неизвестная дополнительная колонка «{x}»")
        if x not in out:
            out.append(x)
    return out


def attr_filters_active(d: dict) -> bool:
    """Активен отбор по подразделению / отделу / источнику / автору (атрибуты проводок и ордеров, волна 7)."""
    f = d["filters"]
    return bool(f["branches"] or f["departments"] or f["sources"] or f["authors"])


def line_filters_active(d: dict) -> bool:
    f = d["filters"]
    return bool(f["centers"] or f["trcodes"] or f["text"] or attr_filters_active(d))


def any_filters_active(d: dict) -> bool:
    f = d["filters"]
    return bool(f["accounts"] or f["groups"] or f["centers"] or f["trcodes"] or f["text"] or attr_filters_active(d))


def needs_attrs(d: dict) -> bool:
    """Отчёту нужны атрибуты проводок/ордеров (отбор, разбивка по подразделениям, количество, доп. колонки, карточка счёта)."""
    c = d["columns"]
    return bool(attr_filters_active(d) or c["by_branch"] or c["qty"] or c["extra"] or d.get("type") == "card" or d.get("_force_attrs"))


def attr_cond_sql(d: dict, alias: str = "lines") -> tuple[str, list]:
    """Условия отбора по атрибутам для проводки `alias` (таблица lines): (« AND …», параметры). Применяются и к строкам открытия (у них свои branch).
    Источник и автор - атрибуты ордера (таблица fiches)."""
    f = d["filters"]
    sql, params = "", []
    if f["branches"]:
        sql += f" AND {alias}.branch IN (" + ",".join("?" * len(f["branches"])) + ")"
        params += f["branches"]
    if f["departments"]:
        sql += f" AND {alias}.dept IN (" + ",".join("?" * len(f["departments"])) + ")"
        params += f["departments"]
    fi = []
    if f["sources"]:
        fi.append("f.module IN (" + ",".join("?" * len(f["sources"])) + ")")
        params += f["sources"]
    if f["authors"]:
        fi.append("f.created_by IN (" + ",".join("?" * len(f["authors"])) + ")")
        params += f["authors"]
    if fi:
        sql += f" AND EXISTS (SELECT 1 FROM fiches f WHERE f.firm={alias}.firm AND f.fiche_id={alias}.fiche_id AND " + " AND ".join(fi) + ")"
    return sql, params


def attr_filter_text(ctx) -> str:
    """Описание отбора по атрибутам для подзаголовка: «Отбор: подразделение — Цех 1, Цех 2; источник — Банк; автор — …»."""
    f = ctx.d["filters"]
    parts = []
    if f["branches"]:
        parts.append("подразделение — " + ", ".join(ctx.names().branch(i) for i in f["branches"]))
    if f["departments"]:
        parts.append("отдел — " + ", ".join(ctx.names().dept(i) for i in f["departments"]))
    if f["sources"]:
        parts.append("источник — " + ", ".join(ctx.names().source(i) for i in f["sources"]))
    if f["authors"]:
        parts.append("автор — " + ", ".join(ctx.names().author(i) or str(i) for i in f["authors"]))
    return ("Отбор: " + "; ".join(parts)) if parts else ""


def attr_filter_check(ctx) -> dict | None:
    """Информационный контроль вместо сверок с полным оборотом при отборе по подразделению/источнику/автору (level ok, не ошибка)."""
    if not attr_filters_active(ctx.d):
        return None
    return {"name": "Отбор по подразделению/источнику/автору: сверка с полным оборотом не выполняется", "ok": True, "level": "ok",
            "detail": attr_filter_text(ctx).replace("Отбор: ", "", 1) + " — суммы не обязаны сходиться с полным оборотом, сальдо и небалансом открытия"}


# ---------------------------------------------------------------------------------------------
# сегменты (годы периода)
# ---------------------------------------------------------------------------------------------

@dataclass
class Segment:
    """Участок периода отчёта в пределах одной основной книги. firm - ключ источника (src) в кэше; key - идентификатор книги (ключ словарей
    планов/классификаторов в движках); year - метка книги (ТОЛЬКО для показа)."""
    year: int
    firm: int
    divisor: int
    d_from: date
    d_to: date
    first: bool = False
    last: bool = False
    key: int = 0
    book_from: Optional[date] = None
    book_to: Optional[date] = None
    continues: bool = False       # тот же источник, что у предыдущего сегмента: остаток продолжается, переноса остатков нет
    name: str = ""

    def __post_init__(self):
        if not self.key:
            self.key = self.year
        if self.book_from is None:
            self.book_from = self.d_from
        if self.book_to is None:
            self.book_to = self.d_to


def report_label(d_or_ctx) -> str:
    """Код валюты отчёта для подзаголовков и заголовков граф."""
    d = d_or_ctx.d if hasattr(d_or_ctx, "d") else d_or_ctx
    return dsparams.get("currency_report" if d.get("_report") else "currency_local")["code"]


def denom_warning(ctx) -> Optional[str]:
    """Предупреждение «деноминация выключена» - по сегментам с divisor > 1 (дата и делитель - из книг и параметров набора)."""
    return denom_warning_for(ctx.segments, ctx.d["denominate"], ctx.usd_main)


def denom_warning_for(segments, denominate: bool, usd_main: bool) -> Optional[str]:
    segs = [s for s in segments if s.divisor > 1]
    if not segs or denominate or usd_main:
        return None
    last = max(s.book_to for s in segs)
    div = max(s.divisor for s in segs)
    after = sorted(x["before"] for x in dsparams.get("denomination") if x["before"] > last.isoformat())
    end = datetime.strptime(after[0], "%Y-%m-%d").date() if after else last + timedelta(days=1)
    when = f"годы до {end.year} г." if (end.month, end.day) == (1, 1) else f"даты до {fmt_date(end)}"
    return f"Деноминация выключена: суммы за {when} показаны в старых {dsparams.local_forms()[1]} (÷{div} для сопоставимости)"


class Context:
    """Контекст расчёта: сегменты по годам, планы счетов, предупреждения."""

    def __init__(self, d: dict, sync: bool = True):
        self.d = d
        self.d1: date = d["_d1"]
        self.d2: date = d["_d2"]
        self.warnings: list[str] = []
        self.denom = d["denominate"]
        self.usd_main = bool(d.get("_report"))
        self.scale = d["scale"]
        self.include_closing = d["include_closing"]
        self._plans: dict[int, dict[str, dict]] = {}
        self._resolvers: dict[int, classifiers.Resolver] = {}
        self._res_cache: dict[tuple[int, int], dict[str, classifiers.Res]] = {}
        self._names = None
        if d.get("_show_report_ignored"):
            self.warnings.append("В этой базе нет сумм в отчётной валюте: графы в отчётной валюте не показаны")
        self.segments: list[Segment] = self._make_segments(sync)
        if sync:     # атрибуты проводок/ордеров книг, загруженных до волны 7, дочитываются из Logo (секунды); без них отчёт с атрибутами не строится
            try:
                self.warnings += cache.ensure_attrs([s.firm for s in self.segments])
            except ValueError as e:
                if needs_attrs(d):
                    raise ReportError(str(e))

    def names(self):
        if self._names is None:
            from .. import attrs
            self._names = attrs.Names([s.firm for s in self.segments])
        return self._names

    # -- сегменты ---------------------------------------------------------------------------
    def _make_segments(self, sync: bool) -> list[Segment]:
        bsegs = books.segments(self.d1, self.d2)
        if not bsegs:
            raise ReportError("Нет данных за выбранный период: нет основных книг за эти даты (см. Настройки → Книги и годы)")
        for g1, g2 in books.gaps(self.d1, self.d2):
            self.warnings.append(f"Нет книги за {fmt_date(g1)}–{fmt_date(g2)} — этот участок не вошёл в отчёт")
        if sync:
            try:
                self.warnings += cache.ensure_fresh(list(dict.fromkeys(b.src for b, _, _ in bsegs)))
            except ValueError as e:
                raise ReportError(str(e))
        segs: list[Segment] = []
        for b, f, t in bsegs:
            segs.append(Segment(b.label, b.src, b.divisor, f, t, key=b.id, book_from=b.date_from, book_to=b.date_to,
                                continues=bool(segs and segs[-1].firm == b.src), name=b.name))
        segs[0].first = True
        segs[-1].last = True
        synced = cache.source_rows()
        for s in segs:
            r = synced.get(s.firm)
            if r is None or r["synced_at"] is None:
                raise ReportError(f"Данные фирмы {books.src_parts(s.firm)[0]} (год {s.year}) ещё не загружены. Запустите синхронизацию (Настройки → Книги и годы).")
        return segs

    def key_of(self, label: int) -> int:
        """Ключ книги (seg.key) для метки года: последний сегмент отчёта с этой меткой, иначе последняя основная книга метки."""
        for s in reversed(self.segments):
            if s.year == label:
                return s.key
        b = books.book_for_label(label)
        return b.id if b else label

    @property
    def cross_year(self) -> bool:
        """Период охватывает несколько источников (книги одного источника - непрерывный период)."""
        return len({s.firm for s in self.segments}) > 1

    def divisor(self, seg: Segment, usd: bool = False) -> int:
        return 1 if (usd or not self.denom) else seg.divisor

    # -- план счетов ---------------------------------------------------------------------------
    def plan(self, key: int) -> dict[str, dict]:
        """План счетов книги по ключу сегмента (seg.key)."""
        if key not in self._plans:
            self._plans[key] = {r["code"]: r for r in acc_mod.get_plan_book(key)}
        return self._plans[key]

    # -- классификаторы ------------------------------------------------------------------------
    def resolver(self, cid: int) -> classifiers.Resolver:
        if cid not in self._resolvers:
            try:
                self._resolvers[cid] = classifiers.Resolver(cid)
            except classifiers.ClassifierError as e:
                raise ReportError(str(e))
        return self._resolvers[cid]

    def resolve(self, cid: int, key: int, codes: list[str]) -> dict[str, classifiers.Res]:
        """Правила классификатора действуют по метке книги (year_from/year_to), планы - по ключу книги."""
        ck = (cid, key)
        have = self._res_cache.setdefault(ck, {})
        need = [c for c in codes if c not in have]
        if need:
            plan = self.plan(key)
            have.update(self.resolver(cid).resolve(books.label_of_key(key), [(c, plan.get(c, {}).get("specode", ""), plan.get(c, {}).get("units") or "") for c in need]))
        return have

    # -- единицы -------------------------------------------------------------------------------
    def out(self, x: float) -> float:
        """внутренние копейки → число для отчёта (с учётом scale), без «-0.0»."""
        v = round(x / 100.0 / self.scale, 2)
        return v + 0.0 if v != 0 else 0.0


# ---------------------------------------------------------------------------------------------
# выборка листьев из кэша
# ---------------------------------------------------------------------------------------------

VEC_NAMES = ["open_net", "open_pos", "open_neg", "t_dt", "t_kt", "close_net", "close_pos", "close_neg"]
NV = 8  # длина блока местной валюты; блок отчётной валюты следом; затем месяцы по 4 числа


class LeafAgg:
    """Агрегат листа (год, счёт): векторы в копейках после деноминации. tmt = [open_net, t_dt, t_kt, close_net], usd — так же."""
    __slots__ = ("year", "code", "tmt", "usd", "months", "branches", "qty")

    def __init__(self, year: int, code: str):
        self.year, self.code = year, code
        self.tmt = [0.0, 0.0, 0.0, 0.0]
        self.usd = [0.0, 0.0, 0.0, 0.0]
        self.months: dict[str, list[float]] = {}
        self.branches: dict[int, list[float]] = {}   # подразделение -> [dt, kt, usd_dt, usd_kt] (копейки после деноминации; usd как есть)
        self.qty = [0.0, 0.0]                         # оборот количества (EMFLINE.AMNT) по Дт и Кт


def line_filter_sql(d: dict) -> tuple[str, list, bool]:
    """Фильтры по проводкам (кроме açılış): (sql-хвост, параметры, открытие_разрешено)."""
    f = d["filters"]
    sql, params = "", []
    if not d["include_closing"]:
        sql += " AND is_closing=0"
    if f["centers"]:
        sql += " AND center IN (" + ",".join("?" * len(f["centers"])) + ")"
        params += f["centers"]
    if f["trcodes"]:
        sql += " AND trcode IN (" + ",".join("?" * len(f["trcodes"])) + ")"
        params += f["trcodes"]
    if f["text"]:
        sql += " AND (instr(lower(text),?)>0 OR instr(lower(fiche_text),?)>0)"
        t = f["text"].lower()
        params += [t, t]
    asql, aparams = attr_cond_sql(d, "lines")     # подразделение/отдел/источник/автор - и к строкам открытия (см. attr_cond_sql), здесь - к проводкам периода
    sql += asql
    params += aparams
    open_ok = (not f["trcodes"]) or (1 in f["trcodes"])
    return sql, params, open_ok


def aggregate_leaves(ctx: Context, by_month: bool = False, by_branch: bool = False) -> dict[tuple[int, str], LeafAgg]:
    """Агрегирует кэш по (год, счёт): вход на d1 (первый сегмент), обороты периода, остаток на d2 (последний сегмент).
    Вход = açılış + проводки года до d_from; остаток = açılış + проводки года до d_to (контракт §4).
    Отбор по подразделению/отделу/источнику/автору действует и на строки открытия. by_month / by_branch (не вместе) - оборот в разрезе
    месяцев / подразделений (LeafAgg.months / LeafAgg.branches). Всегда считается оборот количества (LeafAgg.qty)."""
    d = ctx.d
    nf, nf_params, open_ok = line_filter_sql(d)
    op_sql, op_params = attr_cond_sql(d, "lines")
    opening_cond = "(is_opening=1 AND %d=1%s)" % (1 if open_ok else 0, op_sql)
    conn = cache.connect()
    leaves: dict[tuple[int, str], LeafAgg] = {}

    def leaf(y, code) -> LeafAgg:
        k = (y, code)
        if k not in leaves:
            leaves[k] = LeafAgg(y, code)
        return leaves[k]

    try:
        for seg in ctx.segments:
            div_t = ctx.divisor(seg)
            df, dt_ = seg.d_from.isoformat(), seg.d_to.isoformat()
            bf = seg.book_from.isoformat()
            # три слагаемых: açılış + проводки ДО начала книги (всегда, без фильтров: закрытие предыдущего года входит во входящее) +
            # проводки внутри книги до начала периода (с фильтрами). Для книги, начинающейся с начала периода источника, добавка пуста.
            if seg.first:
                sql = ("SELECT account, SUM(dt-kt), SUM(usd_dt-usd_kt) FROM lines WHERE firm=? AND (" + opening_cond +
                       " OR (is_opening=0 AND date<?) OR (is_opening=0 AND date>=? AND date<?" + nf + ")) GROUP BY account")
                for acc, net, unet in conn.execute(sql, [seg.firm, *op_params, bf, bf, df, *nf_params]):
                    a = leaf(seg.key, acc)
                    a.tmt[0] += net / div_t
                    a.usd[0] += unet
            if by_month or by_branch:
                gx = "substr(date,1,7)" if by_month else "COALESCE(branch,-1)"
                sql = (f"SELECT account, {gx}, SUM(dt), SUM(kt), SUM(usd_dt), SUM(usd_kt), SUM(qty_dt), SUM(qty_kt) FROM lines WHERE firm=? AND is_opening=0 "
                       f"AND date>=? AND date<=?{nf} GROUP BY account, {gx}")
                for acc, g, sdt, skt, sudt, sukt, qd, qk in conn.execute(sql, [seg.firm, df, dt_, *nf_params]):
                    a = leaf(seg.key, acc)
                    m = (a.months if by_month else a.branches).setdefault(g, [0.0, 0.0, 0.0, 0.0])
                    m[0] += sdt / div_t
                    m[1] += skt / div_t
                    m[2] += sudt
                    m[3] += sukt
                    a.tmt[1] += sdt / div_t
                    a.tmt[2] += skt / div_t
                    a.usd[1] += sudt
                    a.usd[2] += sukt
                    a.qty[0] += qd or 0.0
                    a.qty[1] += qk or 0.0
            else:
                sql = ("SELECT account, SUM(dt), SUM(kt), SUM(usd_dt), SUM(usd_kt), SUM(qty_dt), SUM(qty_kt) FROM lines WHERE firm=? AND is_opening=0 "
                       "AND date>=? AND date<=?" + nf + " GROUP BY account")
                for acc, sdt, skt, sudt, sukt, qd, qk in conn.execute(sql, [seg.firm, df, dt_, *nf_params]):
                    a = leaf(seg.key, acc)
                    a.tmt[1] += sdt / div_t
                    a.tmt[2] += skt / div_t
                    a.usd[1] += sudt
                    a.usd[2] += sukt
                    a.qty[0] += qd or 0.0
                    a.qty[1] += qk or 0.0
            if seg.last:
                sql = ("SELECT account, SUM(dt-kt), SUM(usd_dt-usd_kt) FROM lines WHERE firm=? AND (" + opening_cond +
                       " OR (is_opening=0 AND date<?) OR (is_opening=0 AND date>=? AND date<=?" + nf + ")) GROUP BY account")
                for acc, net, unet in conn.execute(sql, [seg.firm, *op_params, bf, bf, dt_, *nf_params]):
                    a = leaf(seg.key, acc)
                    a.tmt[3] += net / div_t
                    a.usd[3] += unet
    finally:
        conn.close()
    return leaves


# ---------------------------------------------------------------------------------------------
# фильтры листьев
# ---------------------------------------------------------------------------------------------

def filter_leaves(ctx: Context, leaves: dict[tuple[int, str], LeafAgg]) -> dict[tuple[int, str], LeafAgg]:
    f = ctx.d["filters"]
    out = leaves
    if f["accounts"]:
        out = {k: v for k, v in out.items() if acc_mod.matches_any(k[1], f["accounts"])}
    if f["groups"]:
        allowed: dict[tuple[int, str], bool] = {}
        by_year: dict[int, list[str]] = {}
        for (y, c) in out:
            by_year.setdefault(y, []).append(c)
        for g in f["groups"]:
            cid, gid = g.get("classifier"), g.get("group")
            rs = ctx.resolver(cid)
            if gid is not None and gid != -1 and gid not in rs.groups:
                raise ReportError(f"Группа {gid} не найдена в классификаторе {cid}")
            ids = rs.descendants(gid) if gid not in (None, -1) else set()
            for y, codes in by_year.items():
                res = ctx.resolve(cid, y, codes)
                for c in codes:
                    r = res[c]
                    ok = (gid is None and r.kind == "none") or (gid == -1 and r.kind == "conflict") or (r.kind == "ok" and r.group in ids)
                    if ok:
                        allowed[(y, c)] = True
        out = {k: v for k, v in out.items() if k in allowed}
    return out


# ---------------------------------------------------------------------------------------------
# дерево строк
# ---------------------------------------------------------------------------------------------

@dataclass
class Seg:
    """Один сегмент пути листа в дереве: узел «группа» / «счёт» / «не сопоставлено»."""
    sid: str
    kind: str                       # group | account | unmapped
    code: str
    name: str
    sort: tuple = ()
    drill_accounts: str | None = None
    drill_group: tuple | None = None   # (classifier, group|None|-1)


@dataclass
class Node:
    id: str
    parent: "Node | None"
    level: int
    kind: str
    code: str
    name: str
    sort: tuple
    vec: list = field(default_factory=list)
    children: dict = field(default_factory=dict)
    drill_accounts: str | None = None
    drill_groups: dict = field(default_factory=dict)   # classifier -> group
    leaf_count: int = 0
    own: list = field(default_factory=list)   # вклад листьев, закончивших путь именно в этом узле


def account_chain(ctx: Context, year: int, code: str, max_level: int | None) -> list[Seg]:
    plan = ctx.plan(year)
    chain: list[str] = []
    x: str | None = code
    guard = 0
    while x is not None and guard < 20:
        chain.append(x)
        x = plan.get(x, {}).get("parent")
        guard += 1
    chain.reverse()
    segs = []
    for c in chain:
        lvl = plan_mod.current().level_of(c)
        if max_level is not None and lvl > max_level and segs:
            break
        segs.append(Seg(f"a:{c}", "account", c, plan.get(c, {}).get("name", ""), (c,), c, None))
    return segs


def classifier_chain(ctx: Context, spec: dict, year: int, code: str, unmapped_on: bool) -> list[Seg] | None:
    cid = spec["id"]
    depth = spec.get("depth")
    rs = ctx.resolver(cid)
    r = ctx.resolve(cid, year, [code])[code]
    if r.kind == "ok":
        path = rs.path(r.group)
        if depth:
            path = path[: int(depth)]
        return [Seg(f"g{cid}:{g}", "group", rs.groups[g]["code"], rs.groups[g]["name"], (rs.groups[g]["sort"], rs.groups[g]["code"]),
                    None, (cid, g)) for g in path]
    if not unmapped_on:
        return None
    if r.kind == "conflict":
        return [Seg(f"x{cid}", "unmapped", "", "Конфликт правил (счёт в нескольких группах)", (1, "~"), None, (cid, -1))]
    return [Seg(f"u{cid}", "unmapped", "", "Не сопоставлено", (2, "~"), None, (cid, None))]


def build_tree(ctx: Context, items: list[tuple[int, str, list]], vec_len: int) -> tuple[list[Node], dict]:
    """items: [(год, счёт, вектор)]. Строит иерархию по ctx.d['rows'].
    Возвращает (корневые узлы, статистика {skipped:int, unmapped_codes:set, conflict_codes:set})."""
    rows = ctx.d["rows"]
    unmapped_on = ctx.d["options"]["unmapped"]
    roots: dict[str, Node] = {}
    stats = {"skipped": [], "unmapped": set(), "conflict": set()}
    names: dict[str, str] = {}
    for year, code, vec in items:
        path: list[Seg] = []
        skipped = False
        for spec in rows:
            if spec["kind"] == "account":
                path += account_chain(ctx, year, code, spec.get("max_level"))
            else:
                ch = classifier_chain(ctx, spec, year, code, unmapped_on)
                if ch is None:
                    skipped = True
                    break
                path += ch
                if ch[-1].kind == "unmapped":
                    (stats["conflict"] if ch[-1].sid.startswith("x") else stats["unmapped"]).add((year, code))
        if skipped:
            stats["skipped"].append((year, code, vec))
            continue
        if not path:
            path = [Seg(f"a:{code}", "account", code, "", (code,), code, None)]
        parent = None
        pid = ""
        children = roots
        for lvl, sg in enumerate(path):
            pid = f"{pid}|{sg.sid}" if pid else sg.sid
            node = children.get(sg.sid)
            if node is None:
                node = Node(pid, parent, lvl, sg.kind, sg.code, sg.name, sg.sort, [0.0] * vec_len)
                children[sg.sid] = node
            if sg.kind == "account":
                node.name = sg.name or node.name  # последний (более поздний) год даёт название
                node.drill_accounts = sg.drill_accounts
            elif sg.drill_group is not None:
                node.drill_groups[sg.drill_group[0]] = sg.drill_group[1]
            if parent is not None:
                node.drill_accounts = node.drill_accounts or parent.drill_accounts
                for k, v in parent.drill_groups.items():
                    node.drill_groups.setdefault(k, v)
            v = node.vec
            for i in range(vec_len):
                v[i] += vec[i]
            node.leaf_count += 1
            if lvl == len(path) - 1:
                if not node.own:
                    node.own = [0.0] * vec_len
                for i in range(vec_len):
                    node.own[i] += vec[i]
            parent = node
            children = node.children
    return list(roots.values()), stats


def drill_of(node: Node) -> dict:
    out: dict = {}
    if node.drill_accounts:
        out["accounts"] = [node.drill_accounts]
    gs = [{"classifier": k, "group": v} for k, v in node.drill_groups.items()]
    if len(gs) == 1:
        out["group"] = gs[0]
    elif gs:
        out["groups"] = gs
    return out


def sort_children(nodes: list[Node], sort: dict, colval) -> list[Node]:
    """Сортировка соседей: by=code|name|<ключ графы>; «не сопоставлено» всегда в конце."""
    by, rev = sort["by"], sort["dir"] == "desc"
    plain = [n for n in nodes if n.kind != "unmapped"]
    tail = sorted([n for n in nodes if n.kind == "unmapped"], key=lambda n: n.sort)
    if by == "code":
        plain.sort(key=lambda n: (0, *n.sort) if n.kind == "group" else (1, 0, n.code), reverse=rev)
    elif by == "name":
        plain.sort(key=lambda n: (n.name or "").casefold(), reverse=rev)
    else:
        plain.sort(key=lambda n: (colval(n, by), n.code), reverse=rev)
    return plain + tail


def flatten(nodes: list[Node], sort: dict, colval, visit) -> None:
    """Обход в глубину в порядке показа; visit(node) вызывается для каждого узла."""
    for n in sort_children(nodes, sort, colval):
        visit(n)
        if n.children:
            flatten(list(n.children.values()), sort, colval, visit)


def month_list(d1: date, d2: date) -> list[str]:
    out, cur = [], date(d1.year, d1.month, 1)
    while cur <= d2:
        out.append(cur.strftime("%Y-%m"))
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def month_title(ym: str) -> str:
    return f"{MONTHS_RU[int(ym[5:]) - 1]} {ym[:4]}"


def subtitle(ctx: Context, extra: str = "") -> str:
    d = ctx.d
    cur = report_label(ctx)
    unit = {1: "", 1000: ", в тысячах", 1000000: ", в миллионах"}[ctx.scale]
    s = f"Период: {fmt_date(ctx.d1)} – {fmt_date(ctx.d2)} · Валюта: {cur}{unit}"
    if not d["include_closing"]:
        s += " · без закрывающих ордеров"
    ft = attr_filter_text(ctx)
    if ft:
        s += " · " + ft
    if extra:
        s += " · " + extra
    return s


# ---------------------------------------------------------------------------------------------
# допуски
# ---------------------------------------------------------------------------------------------

TOL = 0.51      # копеек (после деноминации) — допуск сравнения сумм (0.005 манат)


def fmt_money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


# ---------------------------------------------------------------------------------------------
# контроли: уровни ok | warn | error и «известные небалансы открытия»
# ---------------------------------------------------------------------------------------------
# Open-ордера (açılış) в Logo по каждой фирме могут быть не сбалансированы (как правило, на копейки). Величина считается при
# синхронизации фирмы (cache.firms.open_imbalance, копейки Logo) и объясняет расхождение «Σ нетто = 0» / «Актив = Пассив» на эту сумму.

def open_imbalance(seg: "Segment", denominate: bool = True) -> float:
    """Небаланс открытия года сегмента в копейках после деноминации (Дт−Кт), 0 если неизвестно."""
    conn = cache.connect()
    try:
        r = conn.execute("SELECT open_imbalance FROM sources WHERE src=?", (seg.firm,)).fetchone()
    finally:
        conn.close()
    v = (r[0] if r and r[0] is not None else 0)
    return v / (seg.divisor if (denominate and seg.divisor) else 1)


def imbalance_text(seg: "Segment", imb: float, out) -> str:
    extra = ""
    if seg.divisor and seg.divisor > 1 and abs(open_imbalance(seg, False) - imb) > 1e-9:
        # книги до деноминации хранятся в старых единицах: после деления небаланс - доли единицы, поэтому даём и исходную сумму
        extra = f"; до деноминации {fmt_money(abs(open_imbalance(seg, False)) / 100.0)} старых {dsparams.local_forms()[0]}"
    return (f"небаланс в исходных данных Logo: открытие {seg.year} года не сбалансировано на {fmt_money(abs(out(imb)))} "
            f"(Σ Дт−Кт по ордерам открытия фирмы {books.src_parts(seg.firm)[0]}{extra})")


def net_zero_check(ctx: "Context", name: str, value: float, seg: "Segment", detail: str | None = None) -> dict:
    """Контроль «Σ нетто = 0» (value в копейках после деноминации). level: ok | warn (равно известному небалансу открытия) | error."""
    det = detail if detail is not None else fmt_money(ctx.out(value))
    if abs(value) < TOL:
        return {"name": name, "ok": True, "level": "ok", "detail": det}
    imb = open_imbalance(seg, ctx.denom)
    if abs(imb) >= TOL and abs(value - imb) < TOL:
        return {"name": name, "ok": False, "level": "warn", "detail": f"{det}; {imbalance_text(seg, imb, ctx.out)}"}
    return {"name": name, "ok": False, "level": "error", "detail": det}


def explain_by_imbalance(ctx: "Context", check: dict, diff: float, seg: "Segment") -> dict:
    """Если непрошедший контроль расходится ровно на известный небаланс открытия (в любую сторону; diff в копейках) — понижает до warn."""
    if check.get("ok"):
        return check
    imb = open_imbalance(seg, ctx.denom)
    if abs(imb) >= TOL and abs(abs(diff) - abs(imb)) < TOL:
        check = dict(check)
        check["level"] = "warn"
        check["detail"] = f"{check.get('detail', '')}; {imbalance_text(seg, imb, ctx.out)}".lstrip("; ")
    return check


def attach_organization(result: dict, defn: dict) -> dict:
    """Result.organization - название фирмы (фирм) периода, organization_firms - [{firm, year, name, book, period}], dataset - {id, name} набора."""
    try:
        if defn.get("years"):
            bks = [b for y in sorted({int(y) for y in defn["years"]}) for b in books.mains_of_label(y)]
        elif (defn.get("compare") or {}).get("years"):
            bks = [b for y in sorted({int(y) for y in defn["compare"]["years"]}) for b in books.mains_of_label(y)]
        else:
            p = defn.get("period") or {}
            bks = [b for b, _, _ in books.segments(parse_date(p["date_from"]), parse_date(p["date_to"]))]
        rows = cache.source_rows()
        items = [{"firm": b.src_firm, "year": b.label, "name": ((rows.get(b.src) or {}).get("name") or b.name or "").strip(), "book": b.id,
                  "period": b.src_period} for b in bks]
        result["organization_firms"] = items
        result["organization"] = "; ".join(f"{i['name']} (фирма {i['firm']}, {i['year']} г.)" for i in items)
    except Exception:
        result.setdefault("organization", "")
    try:
        from .. import dsctx, registry
        ds = registry.get_dataset(dsctx.current().id) or registry.root_dataset() or registry.default_dataset()
        result["dataset"] = {"id": ds["id"], "name": ds["name"]}
        if ds.get("org_name"):
            result["organization"] = ds["org_name"]
    except Exception:
        pass
    return result


def finalize_checks(result: dict) -> dict:
    """Приводит result.checks к контракту: у каждого контроля есть level (ok|warn|error); ok == (level == 'ok')."""
    for ch in result.get("checks") or []:
        lv = ch.get("level")
        if lv not in ("ok", "warn", "error"):
            lv = "ok" if ch.get("ok") else "error"
        ch["level"] = lv
        ch["ok"] = lv == "ok"
    return result
