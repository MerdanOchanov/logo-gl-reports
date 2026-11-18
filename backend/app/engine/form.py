"""Формы по строкам (type=form): безопасный разбор формул (без eval) и расчёт строк по главной книге из кэша.

Формула — выражение из функций, ссылок на строки `[010]`, чисел, `+ - * /` и скобок. Функции (аргумент — селектор счетов):
  SND/SNK — сальдо на начало, дебетовое/кредитовое (развёрнуто по листьям);  SKD/SKK — то же на конец;
  SN/SK   — свёрнутое сальдо (Дт − Кт) на начало/конец;  OBD/OBK — обороты Дт/Кт;  OBDK(selДт, selКт) — оборот в корреспонденции.
Селектор: маска `100*` (без звёздочки — код и потомки), диапазон `150..157`, группа классификатора `@1:76`,
объединение `|`, исключение `~` (всё после `~` исключается): `1* ~ 150*|153*`.
Допустимы русские синонимы функций: СНД СНК СКД СКК СН СК ОБД ОБК ОБДК.

Все суммы внутри вычисляются в манатах (после деноминации); константы формулы тоже в манатах."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .. import accounts as acc_mod
from .. import plan as plan_mod
from .. import classifiers
from . import common as C
from .common import Context, ReportError

FUNCS = {"SND": 1, "SNK": 1, "SKD": 1, "SKK": 1, "SN": 1, "SK": 1, "OBD": 1, "OBK": 1, "OBDK": 2}
FUNC_ALIASES = {"СНД": "SND", "СНК": "SNK", "СКД": "SKD", "СКК": "SKK", "СН": "SN", "СК": "SK",
                "ОБД": "OBD", "ОБК": "OBK", "ОБДК": "OBDK"}
FUNC_HELP = {
    "SND": "сальдо на начало, дебетовое", "SNK": "сальдо на начало, кредитовое",
    "SKD": "сальдо на конец, дебетовое", "SKK": "сальдо на конец, кредитовое",
    "SN": "сальдо на начало, свёрнутое (Дт−Кт)", "SK": "сальдо на конец, свёрнутое (Дт−Кт)",
    "OBD": "оборот по дебету за период", "OBK": "оборот по кредиту за период",
    "OBDK": "оборот в корреспонденции OBDK(Дт-селектор, Кт-селектор)",
}
COLUMN_KINDS = ("begin", "end", "period", "prev", "prev_begin")
COLUMN_NAMES_RU = {"begin": "на начало", "end": "на конец", "period": "за период", "prev": "за прошлый период", "prev_begin": "на начало прошлого периода"}
COLUMN_ALIASES = {"turn": "period", "prev_year": "prev"}      # названия граф, которые присылает интерфейс
MAX_FORMULA_LEN = 4000
TOL_MANAT = 0.0051


class FormulaError(ValueError):
    """Ошибка разбора формулы; pos — позиция (с 0) в тексте формулы."""

    def __init__(self, message: str, pos: int | None = None):
        super().__init__(message)
        self.message = message
        self.pos = pos

    def __str__(self) -> str:
        return self.message if self.pos is None else f"{self.message} (позиция {self.pos + 1})"


# ---------------------------------------------------------------------------------------------
# селекторы
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Selector:
    text: str
    include: tuple      # термы: ("mask", "100*") | ("range", "150..157") | ("group", cid, "76")
    exclude: tuple


_CODE_RE = re.compile(r"^[\w./\-]+$")


def _parse_term(term: str, pos: int) -> tuple:
    if term.startswith("@"):
        m = re.fullmatch(r"@(\d+)\s*:\s*(.+)", term)
        if not m:
            raise FormulaError(f"Группа задаётся как @классификатор:код_группы, например @1:76 (получено «{term}»)", pos)
        return ("group", int(m.group(1)), m.group(2).strip())
    if ".." in term:
        lo, _, hi = term.partition("..")
        lo, hi = lo.strip(), hi.strip()
        if not lo or not hi or "*" in term or ".." in hi:
            raise FormulaError(f"Диапазон должен быть вида 150..157 (получено «{term}»)", pos)
        if not _CODE_RE.match(lo) or not _CODE_RE.match(hi):
            raise FormulaError(f"Недопустимые символы в диапазоне «{term}»", pos)
        return ("range", f"{lo}..{hi}")
    if term == "*":
        return ("mask", "*")
    if "*" in term[:-1]:
        raise FormulaError(f"Звёздочка допускается только в конце маски (получено «{term}»)", pos)
    if not _CODE_RE.match(term.rstrip("*")):
        raise FormulaError(f"Недопустимые символы в коде счёта «{term}»", pos)
    return ("mask", term)


def parse_selector(text: str, offset: int = 0) -> Selector:
    if not text.strip():
        raise FormulaError("Пустой селектор счетов", offset)
    segs: list[tuple[str | None, str, int]] = []
    last, sep = 0, None
    for m in re.finditer(r"[|~]", text):
        segs.append((sep, text[last:m.start()], last))
        sep, last = m.group(), m.end()
    segs.append((sep, text[last:], last))
    inc: list = []
    exc: list = []
    target = inc
    for sp, t, pos in segs:
        if sp == "~":
            target = exc
        term = t.strip()
        tpos = offset + pos + (len(t) - len(t.lstrip()))
        if not term:
            raise FormulaError("Пустой элемент селектора (лишний «|» или «~», либо перед «~» нет включаемых счетов)", tpos)
        target.append(_parse_term(term, tpos))
    return Selector(re.sub(r"\s+", " ", text.strip()), tuple(inc), tuple(exc))


# ---------------------------------------------------------------------------------------------
# разбор формулы -> AST
# ---------------------------------------------------------------------------------------------

_NUM_RE = re.compile(r"\d+(?:\.\d+)?|\.\d+")
_IDENT_RE = re.compile(r"[A-Za-zА-Яа-яЁё_]+")


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.depth = 0
        self.ops = 0

    def count_op(self, pos):
        self.ops += 1
        if self.ops > 300:
            raise FormulaError("Слишком много операций в одной формуле (более 300) — разбейте на несколько строк со ссылками [код]", pos)

    def ws(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def parse(self):
        if len(self.s) > MAX_FORMULA_LEN:
            raise FormulaError(f"Формула слишком длинная (более {MAX_FORMULA_LEN} символов)")
        node = self.expr()
        self.ws()
        if self.i < len(self.s):
            raise FormulaError(f"Лишний символ «{self.s[self.i]}»: ожидалась операция (+ - * /) или конец формулы", self.i)
        return node

    def expr(self):
        node = self.term()
        while True:
            self.ws()
            if self.i < len(self.s) and self.s[self.i] in "+-":
                op = self.s[self.i]
                self.count_op(self.i)
                self.i += 1
                node = ("bin", op, node, self.term())
            else:
                return node

    def term(self):
        node = self.unary()
        while True:
            self.ws()
            if self.i < len(self.s) and self.s[self.i] in "*/":
                op = self.s[self.i]
                self.count_op(self.i)
                self.i += 1
                node = ("bin", op, node, self.unary())
            else:
                return node

    def unary(self):
        self.ws()
        if self.i < len(self.s) and self.s[self.i] in "+-":
            op = self.s[self.i]
            self.i += 1
            self.depth += 1
            if self.depth > 80:
                raise FormulaError("Слишком глубокая вложенность", self.i)
            x = self.unary()
            self.depth -= 1
            return ("neg", x) if op == "-" else x
        return self.primary()

    def primary(self):
        self.ws()
        s, i = self.s, self.i
        if i >= len(s):
            raise FormulaError("Формула оборвана: ожидалось число, функция, ссылка [строка] или «(»", i)
        c = s[i]
        if c == "(":
            self.i += 1
            self.depth += 1
            if self.depth > 80:
                raise FormulaError("Слишком глубокая вложенность скобок", i)
            node = self.expr()
            self.ws()
            if self.i >= len(s) or s[self.i] != ")":
                raise FormulaError("Не закрыта скобка «(»", i)
            self.i += 1
            self.depth -= 1
            return node
        if c == "[":
            j = s.find("]", i)
            if j < 0:
                raise FormulaError("Не закрыта ссылка на строку: ожидалась «]»", i)
            code = s[i + 1:j].strip()
            if not code:
                raise FormulaError("Пустая ссылка на строку «[]»: укажите код строки, например [010]", i)
            self.i = j + 1
            return ("ref", code, i)
        if c.isdigit() or (c == "." and i + 1 < len(s) and s[i + 1].isdigit()):
            m = _NUM_RE.match(s, i)
            self.i = m.end()
            if self.i < len(s) and (s[self.i].isalpha() or s[self.i] == "_"):
                raise FormulaError(f"Недопустимое число «{s[i:self.i + 1]}…»", i)
            return ("num", float(m.group()))
        if c.isalpha() or c == "_":
            m = _IDENT_RE.match(s, i)
            raw = m.group()
            name = raw.upper()
            name = FUNC_ALIASES.get(name, name)
            if name not in FUNCS:
                raise FormulaError(f"Неизвестная функция «{raw}». Допустимые: " + ", ".join(FUNCS), i)
            j = m.end()
            while j < len(s) and s[j].isspace():
                j += 1
            if j >= len(s) or s[j] != "(":
                raise FormulaError(f"После имени функции {name} ожидается «(»", j if j < len(s) else len(s))
            start = j + 1
            k = start
            while k < len(s) and s[k] != ")":
                if s[k] == "(":
                    raise FormulaError("В селекторе счетов скобки не допускаются; арифметику пишите снаружи функции, "
                                       "например SKD(100*)+SKD(102*)", k)
                k += 1
            if k >= len(s):
                raise FormulaError(f"Не закрыта скобка функции {name}", j)
            inner = s[start:k]
            parts, last = [], 0
            for cm in re.finditer(",", inner):
                parts.append((inner[last:cm.start()], last))
                last = cm.end()
            parts.append((inner[last:], last))
            if len(parts) != FUNCS[name]:
                raise FormulaError(f"Функция {name} принимает аргументов: {FUNCS[name]}, указано: {len(parts)}", i)
            sels = [parse_selector(p, start + off) for p, off in parts]
            self.i = k + 1
            return ("fn", name, tuple(sels), i)
        raise FormulaError(f"Неожиданный символ «{c}»: ожидалось число, функция, ссылка [строка] или «(»", i)


def parse_formula(text: str):
    if text is None or not str(text).strip():
        raise FormulaError("Пустая формула", 0)
    return _Parser(str(text)).parse()


def walk(node):
    yield node
    if node[0] == "bin":
        yield from walk(node[2])
        yield from walk(node[3])
    elif node[0] == "neg":
        yield from walk(node[1])


def refs_of(node) -> list[tuple[str, int]]:
    return [(n[1], n[2]) for n in walk(node) if n[0] == "ref"]


def selectors_of(node) -> list[Selector]:
    out = []
    for n in walk(node):
        if n[0] == "fn":
            out.extend(n[2])
    return out


# ---------------------------------------------------------------------------------------------
# определение формы: нормализация и проверка
# ---------------------------------------------------------------------------------------------

_CELL_RE = re.compile(r"^\$?[A-Za-z]{1,3}\$?\d{1,7}$")


def split_cell(addr: str) -> tuple[str | None, str]:
    """'D12' | 'Лист!D12' | "'Лист 1'!D12" → (лист|None, 'D12'); ValueError при неверном адресе."""
    a = (addr or "").strip()
    sheet = None
    if "!" in a:
        sheet, _, a = a.rpartition("!")
        sheet = sheet.strip()
        if len(sheet) >= 2 and sheet[0] == "'" and sheet[-1] == "'":
            sheet = sheet[1:-1].replace("''", "'")
        if not sheet:
            raise ValueError(f"Пустое имя листа в адресе «{addr}»")
    a = a.replace("$", "")
    if not _CELL_RE.match(a):
        raise ValueError(f"Некорректный адрес ячейки «{addr}» (ожидается D12 или Лист!D12)")
    m = re.match(r"^([A-Za-z]+)(\d+)$", a)
    if m:
        col = 0
        for ch in m.group(1).upper():
            col = col * 26 + ord(ch) - 64
        if col > 16384 or not 1 <= int(m.group(2)) <= 1048576:
            raise ValueError(f"Адрес ячейки «{addr}» вне границ листа Excel: столбцы до XFD, строки от 1 до 1 048 576")
    return sheet, a.upper()


def normalize_columns(form: dict) -> list[dict]:
    raw = form.get("columns") or ["begin", "end"]
    out, seen = [], set()
    for c in raw:
        spec = {"kind": c} if isinstance(c, str) else dict(c or {})
        name = spec.get("kind") or spec.get("key")
        kind = COLUMN_ALIASES.get(name, name)
        if kind not in COLUMN_KINDS:
            raise ReportError(f"Неизвестная графа формы «{name}»: допустимы графы " + ", ".join(f"«{COLUMN_NAMES_RU[k]}»" for k in COLUMN_KINDS))
        key = spec.get("key") or name
        if key in seen:
            raise ReportError(f"Графа «{COLUMN_NAMES_RU.get(key, key)}» указана в форме дважды")
        seen.add(key)
        out.append({"key": key, "kind": kind, "title": spec.get("title")})
    return out


def check_form(form: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Разбирает строки и контрольные равенства. Возвращает (lines, checks, errors).
    lines: [{code, name, formula, ast, bold, indent, header, cell, cells, refs, selectors}]; errors: [{line, field, pos, message}]."""
    errors: list[dict] = []

    def err(line, field, message, pos=None):
        errors.append({"line": line, "field": field, "pos": pos, "message": message})

    lines: list[dict] = []
    if not isinstance(form, dict) or not isinstance(form.get("lines"), list) or not form["lines"]:
        err(None, "lines", "В форме нет ни одной строки")
        return [], [], errors
    codes: set[str] = set()
    for idx, ln in enumerate(form["lines"], 1):
        code = str(ln.get("code") or "").strip()
        if not code:
            err(f"#{idx}", "code", f"У строки №{idx} не задан код")
            continue
        if code in codes:
            err(code, "code", f"Код строки «{code}» повторяется")
        codes.add(code)
        formula = str(ln.get("formula") or "").strip()
        item = {"code": code, "name": str(ln.get("name") or ""), "formula": formula, "ast": None, "bold": bool(ln.get("bold", False)),
                "indent": max(0, int(ln.get("indent") or 0)), "header": not formula, "cell": ln.get("cell"),
                "cells": ln.get("cells") or {}, "refs": [], "selectors": []}
        if formula:
            try:
                item["ast"] = parse_formula(formula)
                item["refs"] = refs_of(item["ast"])
                item["selectors"] = selectors_of(item["ast"])
            except FormulaError as e:
                err(code, "formula", e.message, e.pos)
        for fld, val in (("cell", ln.get("cell")),) + tuple((f"cells.{k}", v) for k, v in (ln.get("cells") or {}).items()):
            if val:
                try:
                    split_cell(str(val))
                except ValueError as e:
                    err(code, fld, str(e))
        lines.append(item)
    by_code = {ln["code"]: ln for ln in lines}
    for ln in lines:
        for ref, pos in ln["refs"]:
            if ref not in by_code:
                err(ln["code"], "formula", f"Ссылка на несуществующую строку [{ref}]", pos)
            elif by_code[ref]["header"]:
                err(ln["code"], "formula", f"Строка [{ref}] — заголовок без формулы, на неё нельзя ссылаться", pos)
    # циклы
    state: dict[str, int] = {}

    def dfs(code, path):
        state[code] = 1
        for ref, _ in by_code[code]["refs"]:
            if ref not in by_code:
                continue
            if state.get(ref) == 1:
                cyc = path[path.index(ref):] + [ref] if ref in path else [code, ref]
                err(code, "formula", "Циклическая ссылка: " + " → ".join(cyc))
            elif ref not in state:
                dfs(ref, path + [ref])
        state[code] = 2

    for ln in lines:
        if ln["code"] not in state:
            dfs(ln["code"], [ln["code"]])
    checks: list[dict] = []
    for idx, ch in enumerate(form.get("checks") or [], 1):
        nm = str(ch.get("name") or f"Контроль {idx}")
        try:
            tol = float(ch.get("tolerance")) if ch.get("tolerance") not in (None, "") else TOL_MANAT
        except (TypeError, ValueError):
            tol = TOL_MANAT
            err(f"check:{nm}", "tolerance", "Допуск контроля должен быть числом в единицах валюты")
        item = {"name": nm, "left": None, "right": None, "tol": tol}
        for side in ("left", "right"):
            try:
                ast = parse_formula(ch.get(side))
                for ref, pos in refs_of(ast):
                    if ref not in by_code:
                        err(f"check:{nm}", side, f"Ссылка на несуществующую строку [{ref}]", pos)
                    elif by_code[ref]["header"]:
                        err(f"check:{nm}", side, f"Строка [{ref}] — заголовок без формулы", pos)
                item[side] = ast
            except FormulaError as e:
                err(f"check:{nm}", side, e.message, e.pos)
        checks.append(item)
    for idx, sel in enumerate(form.get("coverage") or [], 1):
        try:
            parse_selector(str(sel))
        except FormulaError as e:
            err("coverage", f"coverage[{idx}]", e.message, e.pos)
    return lines, checks, errors


def _where(line) -> str:
    """Место ошибки для пользователя: строка формы, контроль или проверка покрытия (внутренние метки «#1», «check:…» не показываются)."""
    if not line:
        return "Форма"
    line = str(line)
    if line.startswith("#"):
        return "Строка №" + line[1:]
    if line.startswith("check:"):
        return "Контроль «%s»" % line[6:]
    if line == "coverage":
        return "Проверка охвата счетов"
    return "Строка " + line


def format_errors(errors: list[dict]) -> str:
    parts = []
    for e in errors[:8]:
        where = _where(e.get("line"))
        pos = f" (позиция {e['pos'] + 1})" if e.get("pos") is not None else ""
        parts.append(f"{where}: {e['message']}{pos}")
    if len(errors) > 8:
        parts.append(f"…и ещё ошибок: {len(errors) - 8}")
    return "Ошибки в форме: " + "; ".join(parts)


# ---------------------------------------------------------------------------------------------
# сопоставление счетов селектору
# ---------------------------------------------------------------------------------------------

class PlanContext:
    """Минимальный контекст для проверки селекторов по плану года без периода (resolver/resolve как у Context)."""

    def __init__(self):
        self._plans: dict[int, dict] = {}
        self._rs: dict[int, classifiers.Resolver] = {}
        self._res: dict[tuple[int, int], dict] = {}

    def plan(self, year):
        if year not in self._plans:
            self._plans[year] = {r["code"]: r for r in acc_mod.get_plan(year)}
        return self._plans[year]

    def resolver(self, cid):
        if cid not in self._rs:
            try:
                self._rs[cid] = classifiers.Resolver(cid)
            except classifiers.ClassifierError as e:
                raise ReportError(str(e))
        return self._rs[cid]

    def resolve(self, cid, year, codes):
        have = self._res.setdefault((cid, year), {})
        need = [c for c in codes if c not in have]
        if need:
            plan = self.plan(year)
            have.update(self.resolver(cid).resolve(year, [(c, plan.get(c, {}).get("specode", ""), plan.get(c, {}).get("units") or "") for c in need]))
        return have


def _term_fn(ctx, term: tuple):
    kind = term[0]
    if kind in ("mask", "range"):
        v = term[1]
        return lambda y, c: acc_mod.code_matches(c, v)
    cid, gcode = term[1], term[2]
    rs = ctx.resolver(cid)
    found = [g["id"] for g in rs.groups.values() if str(g["code"]) == gcode]
    if not found:
        raise ReportError(f"В классификаторе {cid} («{rs.name}») нет группы с кодом «{gcode}»")
    ids = rs.descendants(found[0])

    def fn(y, c):
        r = ctx.resolve(cid, y, [c])[c]
        return r.kind == "ok" and r.group in ids
    return fn


def make_matcher(ctx, sel: Selector):
    inc = [_term_fn(ctx, t) for t in sel.include]
    exc = [_term_fn(ctx, t) for t in sel.exclude]

    def match(y: int, c: str) -> bool:
        return any(f(y, c) for f in inc) and not any(f(y, c) for f in exc)
    return match


def selector_masks(sel: Selector, ctx, year: int | None) -> list[str] | None:
    """Маски для drill, если селектор выражается ими (без исключений/групп; диапазон раскрывается корнями плана)."""
    if sel.exclude or any(t[0] == "group" for t in sel.include):
        return None
    out: list[str] = []
    for t in sel.include:
        if t[0] == "mask":
            out.append(t[1])
        else:
            if year is None:
                return None
            roots = [c for c in ctx.plan(year) if plan_mod.current().is_root(c) and acc_mod.code_matches(c, t[1])]
            out.extend(roots)
    return out


# ---------------------------------------------------------------------------------------------
# вычисление
# ---------------------------------------------------------------------------------------------

class _Null(Exception):
    """Значение не определено (деление на ноль / ссылка на неопределённую строку)."""


class Evaluator:
    """Значения функций на одном контексте периода. mode: 'end' (как записано) | 'begin' (состояние на начало периода)."""

    def __init__(self, ctx: Context, leaves: dict, mode: str):
        self.ctx = ctx
        self.mode = mode
        self.usd = ctx.usd_main
        self.items = [(k[0], k[1], (a.usd if self.usd else a.tmt)) for k, a in leaves.items()]
        self._matchers: dict[str, callable] = {}
        self._matched: dict[str, list] = {}
        self._pairs = None

    def matcher(self, sel: Selector):
        if sel.text not in self._matchers:
            self._matchers[sel.text] = make_matcher(self.ctx, sel)
        return self._matchers[sel.text]

    def matched(self, sel: Selector) -> list:
        if sel.text not in self._matched:
            m = self.matcher(sel)
            self._matched[sel.text] = [(y, c, v) for y, c, v in self.items if m(y, c)]
        return self._matched[sel.text]

    def _load_pairs(self) -> list:
        from .. import cache
        ctx = self.ctx
        out = []
        conn = cache.connect()
        try:
            nf = "" if ctx.include_closing else " AND p.is_closing=0"
            from . import pairs as P_
            a_join, a_cond, a_params = P_.attr_pair_sql(ctx.d)    # подразделение/отдел/источник/автор: обе проводки пары проходят отбор
            nf += a_cond
            for seg in ctx.segments:
                if conn.execute("SELECT 1 FROM pairs WHERE firm=? LIMIT 1", (seg.firm,)).fetchone() is None:
                    raise ReportError(f"Корреспонденция за {seg.year} год ещё не рассчитана (пары проводок пусты) — функция OBDK недоступна. "
                                      "Дождитесь пересинхронизации кэша.")
                div = ctx.divisor(seg)
                for dta, kta, am, uam in conn.execute(
                        "SELECT p.dt_account, p.kt_account, SUM(p.amount), SUM(p.usd_amount) FROM pairs p" + a_join +
                        " WHERE p.firm=? AND p.date>=? AND p.date<=?" + nf + " GROUP BY p.dt_account, p.kt_account",
                        [seg.firm, seg.d_from.isoformat(), seg.d_to.isoformat(), *a_params]):
                    out.append((seg.key, dta, kta, (uam or 0) if self.usd else (am or 0) / div))
        finally:
            conn.close()
        return out

    def call(self, name: str, sels: tuple) -> float:
        """Значение функции в манатах."""
        begin = self.mode == "begin"
        if begin:
            name = {"SKD": "SND", "SKK": "SNK", "SK": "SN"}.get(name, name)
            if name in ("OBD", "OBK", "OBDK"):
                return 0.0
        if name == "OBDK":
            if self._pairs is None:
                self._pairs = self._load_pairs()
            ma, mb = self.matcher(sels[0]), self.matcher(sels[1])
            return sum(v for y, d, k, v in self._pairs if ma(y, d) and mb(y, k)) / 100.0
        rows = self.matched(sels[0])
        if name == "SND":
            s = sum(v[0] for _, _, v in rows if v[0] > 0)
        elif name == "SNK":
            s = sum(-v[0] for _, _, v in rows if v[0] < 0)
        elif name == "SKD":
            s = sum(v[3] for _, _, v in rows if v[3] > 0)
        elif name == "SKK":
            s = sum(-v[3] for _, _, v in rows if v[3] < 0)
        elif name == "SN":
            s = sum(v[0] for _, _, v in rows)
        elif name == "SK":
            s = sum(v[3] for _, _, v in rows)
        elif name == "OBD":
            s = sum(v[1] for _, _, v in rows)
        elif name == "OBK":
            s = sum(v[2] for _, _, v in rows)
        else:  # pragma: no cover
            raise ReportError(f"Функция {name} не реализована")
        return s / 100.0


def eval_ast(node, ev: Evaluator, ref_value) -> float:
    k = node[0]
    if k == "num":
        return node[1]
    if k == "ref":
        v = ref_value(node[1])
        if v is None:
            raise _Null()
        return v
    if k == "fn":
        return ev.call(node[1], node[2])
    if k == "neg":
        return -eval_ast(node[1], ev, ref_value)
    a = eval_ast(node[2], ev, ref_value)
    b = eval_ast(node[3], ev, ref_value)
    op = node[1]
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if abs(b) < 1e-12:
        raise _Null()
    return a / b


def shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:      # 29 февраля
        return d.replace(year=d.year + years, day=28)


def _col_title(kind: str, d1: date, d2: date, p1: date, p2: date) -> str:
    f = C.fmt_date
    return {"begin": f"На {f(d1)}", "end": f"На {f(d2)}", "period": f"За {f(d1)} – {f(d2)}",
            "prev": f"Прошлый год ({f(p1)} – {f(p2)})", "prev_begin": f"На {f(p1)}"}[kind]


def validate_text(form: dict, year: int | None = None) -> dict:
    """POST /api/forms/validate: проверка без расчёта. Возвращает {ok, errors, lines:[{code, ok, refs, selectors:[{text, accounts}]}]}.
    С year — для каждого селектора число листовых счетов плана этого года (0 — предупреждение)."""
    lines, checks, errors = check_form(form)
    pctx = PlanContext() if year else None
    out_lines = []
    warnings = []
    leaves_by_year = None
    if year:
        leaves_by_year = [c for c, r in pctx.plan(year).items() if r.get("is_leaf")]
    for ln in lines:
        sels = []
        for s in ln["selectors"]:
            item = {"text": s.text}
            if pctx is not None and leaves_by_year is not None:
                try:
                    m = make_matcher(pctx, s)
                    item["accounts"] = sum(1 for c in leaves_by_year if m(year, c))
                    if item["accounts"] == 0:
                        warnings.append(f"Строка {ln['code']}: селектор «{s.text}» не находит счетов в плане {year} года")
                except ReportError as e:
                    errors.append({"line": ln["code"], "field": "formula", "pos": None, "message": str(e)})
            sels.append(item)
        bad = any(e["line"] == ln["code"] for e in errors)
        out_lines.append({"code": ln["code"], "ok": not bad, "header": ln["header"], "refs": sorted({r for r, _ in ln["refs"]}), "selectors": sels})
    out = {"ok": not errors, "errors": errors, "warnings": warnings, "lines": out_lines}
    if errors:
        out["error"], out["pos"] = errors[0]["message"], errors[0]["pos"]
    return out


def validate_formula(text: str, codes: list[str] | None = None) -> dict:
    """Проверка одной формулы. codes — допустимые коды строк для ссылок (None — ссылки не проверяются)."""
    try:
        ast = parse_formula(text)
    except FormulaError as e:
        return {"ok": False, "error": e.message, "pos": e.pos, "errors": [{"line": None, "field": "formula", "pos": e.pos, "message": e.message}],
                "refs": [], "selectors": []}
    errs = []
    refs = refs_of(ast)
    if codes is not None:
        for ref, pos in refs:
            if ref not in codes:
                errs.append({"line": None, "field": "formula", "pos": pos, "message": f"Ссылка на несуществующую строку [{ref}]"})
    out = {"ok": not errs, "errors": errs, "refs": sorted({r for r, _ in refs}), "selectors": [s.text for s in selectors_of(ast)]}
    if errs:
        out["error"], out["pos"] = errs[0]["message"], errs[0]["pos"]
    return out


def run(defn: dict) -> dict:
    d = C.normalize(defn)
    form = d.get("form")
    if not isinstance(form, dict):
        raise ReportError("Для отчёта типа «форма» нужен блок form (lines, columns)")
    lines, checks, errors = check_form(form)
    if errors:
        raise ReportError(format_errors(errors))
    cols = normalize_columns(form)
    by_code = {ln["code"]: ln for ln in lines}

    ctx = Context(d)
    warnings = list(ctx.warnings)
    leaves = C.filter_leaves(ctx, C.aggregate_leaves(ctx))
    ctx_prev, leaves_prev = None, None
    p1, p2 = shift_year(ctx.d1, -1), shift_year(ctx.d2, -1)
    if any(c["kind"] in ("prev", "prev_begin") for c in cols):
        dp = dict(d)
        dp["_d1"], dp["_d2"] = p1, p2
        dp["period"] = {"date_from": p1.isoformat(), "date_to": p2.isoformat()}
        try:
            ctx_prev = Context(dp)
            leaves_prev = C.filter_leaves(ctx_prev, C.aggregate_leaves(ctx_prev))
            warnings += ["Сравнительный период: " + w for w in ctx_prev.warnings]
        except ReportError as e:
            warnings.append(f"Сравнительные данные за {C.fmt_date(p1)} – {C.fmt_date(p2)} недоступны: {e}")

    evs: dict[str, Evaluator | None] = {}
    for c in cols:
        kind = c["kind"]
        if kind in ("begin", "end", "period"):
            evs[c["key"]] = Evaluator(ctx, leaves, "begin" if kind == "begin" else "end")
        else:
            evs[c["key"]] = Evaluator(ctx_prev, leaves_prev, "begin" if kind == "prev_begin" else "end") if ctx_prev else None

    # значения строк по графам (в манатах), None — не определено
    values: dict[str, dict[str, float | None]] = {c["key"]: {} for c in cols}
    null_warned: set[tuple[str, str]] = set()
    for c in cols:
        key = c["key"]
        ev = evs[key]
        memo = values[key]
        if ev is None:
            for ln in lines:
                if not ln["header"]:
                    memo[ln["code"]] = None
            continue

        def line_value(code, memo, ev, key):
            if code in memo:
                return memo[code]
            ln = by_code[code]
            try:
                v = eval_ast(ln["ast"], ev, lambda r: line_value(r, memo, ev, key))
            except _Null:
                v = None
                if (code, key) not in null_warned:
                    null_warned.add((code, key))
                    warnings.append(f"Строка {code}: значение не определено (деление на ноль или ссылка на неопределённую строку), графа «{c.get('title') or COLUMN_NAMES_RU.get(c['kind'], key)}»")
            memo[code] = v
            return v

        for ln in lines:
            if not ln["header"]:
                line_value(ln["code"], memo, ev, key)

    # предупреждение: селекторы без счетов (по текущему периоду) — одним сообщением
    cur_ev = next((e for e in evs.values() if e is not None), None)
    if cur_ev is not None:
        seen_sel: set[str] = set()
        empty_lines: list[str] = []
        for ln in lines:
            for s in ln["selectors"]:
                if s.text not in seen_sel:
                    seen_sel.add(s.text)
                    if not cur_ev.matched(s) and ln["code"] not in empty_lines:
                        empty_lines.append(ln["code"])
        if empty_lines:
            warnings.append("В строках " + ", ".join(empty_lines) + " нет счетов с данными за период — значение по ним равно нулю")

    # строки результата
    scale_units = ctx.out
    titles = {c["key"]: c["title"] or _col_title(c["kind"], ctx.d1, ctx.d2, p1, p2) for c in cols}
    columns = []
    for c in cols:
        kind = c["kind"]
        if kind in ("begin", "end", "period"):
            pr = {"date_from": ctx.d1.isoformat(), "date_to": (ctx.d1 if kind == "begin" else ctx.d2).isoformat()}
        else:
            pr = {"date_from": p1.isoformat(), "date_to": (p1 if kind == "prev_begin" else p2).isoformat()}
        columns.append({"key": c["key"], "title": titles[c["key"]], "group": None, "type": "money", "drill": None, "period": pr})

    year_for_masks = ctx.segments[-1].key

    def drill_for(ln) -> dict:
        sels: list[Selector] = []
        seen_l: set[str] = set()

        def collect(l):
            if l["code"] in seen_l:
                return
            seen_l.add(l["code"])
            sels.extend(l["selectors"])
            for r, _ in l["refs"]:
                if r in by_code:
                    collect(by_code[r])
        collect(ln)
        if not sels:
            return {}
        masks: list[str] = []
        simple = True
        for s in sels:
            m = selector_masks(s, ctx, year_for_masks)
            if m is None:
                simple = False
                break
            masks += m
        if simple:
            return {"accounts": list(dict.fromkeys(masks))}
        # исключения/группы: явный список счетов, вошедших в формулу
        ev0 = cur_ev
        codes: dict[str, bool] = {}
        for s in sels:
            for _, c, _v in (ev0.matched(s) if ev0 else []):
                codes[c] = True
        if 0 < len(codes) <= 200:
            return {"accounts": list(codes)}
        inc: list[str] = []
        for s in sels:
            for t in s.include:
                if t[0] == "mask":
                    inc.append(t[1])
        return {"accounts": list(dict.fromkeys(inc))} if inc else {}

    hide = d["options"]["hide_zero"]
    rows_out: list[dict] = []
    stack: list[tuple[int, str]] = []     # (indent, id)
    for ln in lines:
        rid = f"l:{ln['code']}"
        while stack and stack[-1][0] >= ln["indent"]:
            stack.pop()
        parent = stack[-1][1] if stack else None
        vals: dict[str, float | None] = {}
        if not ln["header"]:
            for c in cols:
                v = values[c["key"]].get(ln["code"])
                vals[c["key"]] = None if v is None else scale_units(v * 100.0)
            if hide and all((v is None or abs(v) < 0.005) for v in vals.values()):
                continue
        rows_out.append({"id": rid, "parent": parent, "level": ln["indent"], "kind": "group" if ln["header"] else "line", "code": ln["code"],
                         "name": ln["name"], "values": vals, "bold": ln["bold"] or ln["header"],
                         "drill": {} if ln["header"] else drill_for(ln)})
        stack.append((ln["indent"], rid))

    # контрольные равенства
    out_checks: list[dict] = []
    for ch in checks:
        for c in cols:
            ev = evs[c["key"]]
            nm = ch["name"] + (f" — {titles[c['key']]}" if len(cols) > 1 else "")
            if ev is None:
                out_checks.append({"name": nm, "ok": False, "detail": "нет данных за период графы"})
                continue
            memo = values[c["key"]]

            def lv(code, memo=memo, ev=ev, key=c["key"]):
                if code in memo:
                    return memo[code]
                try:
                    v = eval_ast(by_code[code]["ast"], ev, lv)
                except _Null:
                    v = None
                memo[code] = v
                return v
            try:
                left = eval_ast(ch["left"], ev, lv)
                right = eval_ast(ch["right"], ev, lv)
            except _Null:
                out_checks.append({"name": nm, "ok": False, "detail": "значение не определено (деление на ноль или пустая ссылка)"})
                continue
            diff = left - right
            item = {"name": nm, "ok": abs(diff) < ch["tol"], "level": "ok" if abs(diff) < ch["tol"] else "error",
                    "detail": f"{C.fmt_money(scale_units(left * 100))} и {C.fmt_money(scale_units(right * 100))}; "
                              f"разница {C.fmt_money(scale_units(diff * 100))}"}
            if not item["ok"] and C.attr_filters_active(d):    # при отборе по подразделению/источнику/автору баланс форм не обязан сходиться
                item["level"] = "warn"
                item["detail"] += "; отбор по подразделению/источнику/автору — равенство не обязано выполняться"
            elif not item["ok"] and not ev.usd:     # расхождение ровно на известный небаланс открытия года - предупреждение, не ошибка
                seg = ev.ctx.segments[0] if ev.mode == "begin" else ev.ctx.segments[-1]
                item = C.explain_by_imbalance(ev.ctx, item, diff * 100.0, seg)
            elif item["ok"] and not ev.usd and abs(diff * 100.0) >= C.TOL:
                # ненулевая разница, принятая допуском, в пределах известного небаланса открытия года - предупреждение, а не «ок»
                seg = ev.ctx.segments[0] if ev.mode == "begin" else ev.ctx.segments[-1]
                imb = C.open_imbalance(seg, ev.ctx.denom)
                if abs(imb) >= C.TOL and abs(diff * 100.0) <= abs(imb) + C.TOL:
                    item["ok"], item["level"] = False, "warn"
                    item["detail"] += "; " + C.imbalance_text(seg, imb, ev.ctx.out)
            out_checks.append(item)

    cov = form.get("coverage") or []
    if cov and cur_ev is not None:
        scope = [make_matcher(ctx, parse_selector(str(x))) for x in cov]
        used = [cur_ev.matcher(s) for ln in lines for s in ln["selectors"]]
        lost = []
        for k, a in leaves.items():
            arr = a.usd if ctx.usd_main else a.tmt
            if not any(m(k[0], k[1]) for m in scope):
                continue
            if any(m(k[0], k[1]) for m in used):
                continue
            if any(abs(x) >= C.TOL for x in arr):
                lost.append((k[1], arr))
        names = ", ".join(sorted({c for c, _ in lost})[:8]) + ("…" if len({c for c, _ in lost}) > 8 else "")
        out_checks.append({"name": "Все счета области с данными учтены в строках формы", "ok": not lost,
                           "detail": "расхождений нет" if not lost else f"не учтено счетов: {len({c for c, _ in lost})} ({names})"})
        if lost:
            warnings.append("Часть счетов области (" + ", ".join(str(x) for x in cov) + ") не вошла ни в одну строку формы: " + names)

    if not d["include_closing"]:
        warnings.append("Закрывающие ордера исключены из сальдо и оборотов формы")
    info = C.attr_filter_check(ctx)
    if info:
        out_checks.append(info)
        warnings.append("Отбор по подразделению/отделу/источнику/автору применён к сальдо открытия, оборотам и парам корреспонденции (OBDK): "
                        "пара учитывается, если обе её проводки проходят отбор")
    # уникальные предупреждения в порядке появления
    warnings = list(dict.fromkeys(warnings))
    return C.finalize_checks({"title": d.get("title") or "Форма по строкам", "subtitle": C.subtitle(ctx), "columns": columns, "rows": rows_out,
                              "totals": {}, "checks": out_checks, "warnings": warnings})
