"""HTTP-маршруты волны 2B: формы по строкам (проверка формул), шаблоны Excel, связи счетов между годами, демо-данные.

Этот роутер подключается в main.py ПЕРЕД основным (api.router): он перехватывает POST /api/run и POST /api/export/xlsx
для типов form и compare_years, а для остальных типов делегирует в api.py без изменений.

Запуск демо-данных из консоли:  python -m app.routes_forms seed   (из каталога backend)"""
from __future__ import annotations

import io
import re
import sys
from datetime import datetime
from typing import Optional, Union
from urllib.parse import quote

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from . import books, classifiers, config, dsctx, dsparams, multiyear, plan, store
from . import export_xlsx as xlsx_mod
from .engine import form as form_engine
from .engine.common import ReportError, finalize_checks, attach_organization

router = APIRouter(prefix="/api")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
OWN_TYPES = ("form", "compare_years")


def err(status: int, text: str):
    raise HTTPException(status, text)


# ---------------------------------------------------------------------------------------------
# расчёт: form / compare_years, остальное — как в api.py
# ---------------------------------------------------------------------------------------------

def run_own(defn: dict) -> dict:
    try:
        if defn.get("type") == "form":
            return attach_organization(finalize_checks(form_engine.run(defn)), defn)
        return attach_organization(finalize_checks(multiyear.run_compare(defn)), defn)
    except ReportError as e:
        err(400, str(e))
    except form_engine.FormulaError as e:
        err(400, str(e))
    except (AttributeError, TypeError, KeyError) as e:
        import logging
        logging.getLogger("buhlogo").exception("Неверная структура описания отчёта: %s", e)
        err(400, "Описание отчёта задано неверно: проверьте, что строки, отбор, графы и период заданы в нужном виде")


@router.post("/run")
def run(body: dict = Body(...)):
    defn = body.get("definition") or body
    if not isinstance(defn, dict):
        err(400, "Описание отчёта должно быть объектом")
    if (defn or {}).get("type") in OWN_TYPES:
        return run_own(defn)
    from . import api
    return api.run_definition(defn)


@router.post("/export/xlsx")
def export_xlsx(body: dict = Body(...)):
    defn = body.get("definition") or body
    if not isinstance(defn, dict):
        err(400, "Описание отчёта должно быть объектом")
    if (defn or {}).get("type") not in OWN_TYPES:
        from . import api
        return api.export_xlsx(body)
    result = run_own(defn)
    if str(body.get("title") or "").strip():
        result["title"] = str(body["title"]).strip()      # название сохранённого отчёта (необязательное поле тела запроса)
    tid = (defn.get("form") or {}).get("template_id") if defn.get("type") == "form" else None
    if tid:
        path = template_path(int(tid))
        if path is None:
            err(404, f"Шаблон Excel №{tid} не найден (возможно, удалён)")
        try:
            data = fill_template(result, defn, path)
        except ReportError as e:
            err(400, str(e))
    else:
        data = xlsx_mod.build(result, (defn.get("columns") or {}).get("extra"))
    name = quote((result.get("title") or "report") + ".xlsx")
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f"attachment; filename=report.xlsx; filename*=UTF-8''{name}"})


# ---------------------------------------------------------------------------------------------
# формы: проверка формул
# ---------------------------------------------------------------------------------------------

@router.post("/forms/validate")
def forms_validate(body: dict = Body(...)):
    """Проверка без расчёта. Тело: {formula, codes?} — одна формула; либо {form: {lines, checks?, coverage?}, year?} — вся форма
    (year — подсчёт счетов, находимых каждым селектором в плане этого года). Ответ: {ok, errors:[{line, field, pos, message}], ...}.
    pos — позиция ошибки в тексте формулы (с 0)."""
    if "formula" in body:
        return form_engine.validate_formula(body.get("formula"), body.get("codes"))
    form = body.get("form") or body.get("definition", {}).get("form")
    if form is None and "lines" in body:
        form = body
    if form is None:
        err(400, "Передайте formula (одна формула) или form (описание формы)")
    year = body.get("year")
    return form_engine.validate_text(form, int(year) if year else None)


@router.get("/forms/help")
def forms_help():
    return {"functions": [{"name": k, "args": form_engine.FUNCS[k], "help": v} for k, v in form_engine.FUNC_HELP.items()],
            "aliases": form_engine.FUNC_ALIASES,
            "selectors": ["100*", "150..157", "@1:76", "100*|102*", "1* ~ 150*|153*"],
            "columns": list(form_engine.COLUMN_KINDS)}


# ---------------------------------------------------------------------------------------------
# шаблоны Excel
# ---------------------------------------------------------------------------------------------

def templates_dir():
    d = dsctx.current().templates_dir or (config.DATA_DIR / "templates")
    d.mkdir(parents=True, exist_ok=True)
    return d


def template_path(tid: int):
    conn = store.connect()
    try:
        r = conn.execute("SELECT id FROM templates WHERE id=?", (tid,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    p = templates_dir() / f"{tid}.xlsx"
    return p if p.exists() else None


@router.post("/templates")
async def templates_upload(file: UploadFile = File(...), name: str = Form("")):
    from openpyxl import load_workbook
    data = await file.read()
    fn = file.filename or "template.xlsx"
    if not fn.lower().endswith(".xlsx"):
        err(400, "Шаблон должен быть файлом .xlsx")
    if len(data) > 15 * 1024 * 1024:
        err(400, "Файл шаблона больше 15 МБ")
    try:
        wb = load_workbook(io.BytesIO(data))
        sheets = wb.sheetnames
    except Exception as e:
        err(400, "Не удалось открыть файл: он повреждён или это не книга Excel (.xlsx). Сохраните шаблон в Excel как «Книга Excel (*.xlsx)» и загрузите снова.")
    conn = store.connect()
    try:
        cur = conn.execute("INSERT INTO templates(name, filename, uploaded_at) VALUES(?,?,?)", (name.strip() or fn.rsplit(".", 1)[0], fn, store.now()))
        tid = cur.lastrowid
        (templates_dir() / f"{tid}.xlsx").write_bytes(data)
        conn.commit()
        r = conn.execute("SELECT id, name, filename, uploaded_at FROM templates WHERE id=?", (tid,)).fetchone()
    finally:
        conn.close()
    return {**dict(r), "sheets": sheets}


@router.get("/templates")
def templates_list():
    conn = store.connect()
    try:
        return [dict(r) for r in conn.execute("SELECT id, name, filename, uploaded_at FROM templates ORDER BY id DESC")]
    finally:
        conn.close()


@router.get("/templates/{tid}/download")
def templates_download(tid: int):
    p = template_path(tid)
    if p is None:
        err(404, "Шаблон не найден")
    conn = store.connect()
    try:
        fn = conn.execute("SELECT filename FROM templates WHERE id=?", (tid,)).fetchone()[0]
    finally:
        conn.close()
    return FileResponse(p, media_type=XLSX, filename=fn)


@router.delete("/templates/{tid}")
def templates_delete(tid: int):
    conn = store.connect()
    try:
        n = conn.execute("DELETE FROM templates WHERE id=?", (tid,)).rowcount
        conn.commit()
    finally:
        conn.close()
    if not n:
        err(404, "Шаблон не найден")
    p = templates_dir() / f"{tid}.xlsx"
    if p.exists():
        p.unlink()
    return {"ok": True}


def fill_template(result: dict, defn: dict, path) -> bytes:
    """Копия шаблона, значения формы записаны в ячейки line.cell / line.cells; оформление шаблона сохраняется.
    line.cell — значение графы по умолчанию (end, иначе period, иначе первая графа формы); line.cells — {графа: адрес}.
    form.meta_cells: {title, subtitle} — адреса для заголовка/подзаголовка; form.template_sheet — лист по умолчанию."""
    from openpyxl import load_workbook
    from openpyxl.cell.cell import MergedCell
    wb = load_workbook(path)
    from openpyxl.workbook.properties import CalcProperties
    wb.calculation = CalcProperties(fullCalcOnLoad=True)      # формулы шаблона пересчитываются при открытии файла
    form = defn.get("form") or {}
    keys = [c["key"] for c in result["columns"]]
    default_key = "end" if "end" in keys else ("period" if "period" in keys else keys[0])
    rows = {r["code"]: r for r in result["rows"] if r.get("kind") in ("line",)}
    default_sheet = form.get("template_sheet")

    def sheet_of(name):
        nm = name or default_sheet
        if nm:
            if nm not in wb.sheetnames:
                raise ReportError(f"В шаблоне нет листа «{nm}» (есть: {', '.join(wb.sheetnames)})")
            return wb[nm]
        return wb.active

    def put(addr, value, what, is_money=True):
        try:
            sh, a = form_engine.split_cell(str(addr))
        except ValueError as e:
            raise ReportError(f"{what}: {e}")
        ws = sheet_of(sh)
        cell = ws[a]
        if isinstance(cell, MergedCell):
            raise ReportError(f"{what}: ячейка {addr} входит в объединённый диапазон, укажите его верхнюю левую ячейку")
        cell.value = value
        if is_money and cell.number_format == "General":
            cell.number_format = xlsx_mod.MONEY_FMT

    for ln in form.get("lines") or []:
        code = str(ln.get("code") or "")
        targets = []
        if ln.get("cell"):
            targets.append((ln["cell"], default_key))
        for k, addr in (ln.get("cells") or {}).items():
            targets.append((addr, k))
        for addr, key in targets:
            if key not in keys:
                names = form_engine.COLUMN_NAMES_RU
                raise ReportError(f"Строка {code}: графа «{names.get(key, key)}» не входит в форму (в форме есть графы: "
                                  + ", ".join("«%s»" % names.get(k, k) for k in keys) + ")")
            row = rows.get(code)
            v = None if row is None else (row.get("values") or {}).get(key)
            if v is None:
                continue
            put(addr, v, f"Строка {code}")
    meta = form.get("meta_cells") or {}
    if meta.get("title"):
        put(meta["title"], result.get("title") or "", "Ячейка заголовка", False)
    if meta.get("subtitle"):
        put(meta["subtitle"], result.get("subtitle") or "", "Ячейка подзаголовка", False)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------
# связи счетов между годами
# ---------------------------------------------------------------------------------------------

def _links(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except multiyear.LinkError as e:
        err(400, str(e))
    except ReportError as e:
        err(400, str(e))


@router.get("/account-links")
def links_list(response: Response, year_from: Optional[int] = None, year_to: Optional[int] = None, q: str = "",
               limit: int = Query(5000, le=20000), offset: int = 0):
    """Плоский список связей [{id, year_from, code_from, year_to, code_to, method, kind, confidence, note}]; общее число — в заголовке X-Total-Count."""
    r = multiyear.list_links(year_from, year_to, q, limit, offset)
    response.headers["X-Total-Count"] = str(r["total"])
    return [{**x, "kind": x["method"]} for x in r["items"]]


@router.put("/account-links")
def links_replace(body: Union[dict, list] = Body(...), year_from: Optional[int] = None, year_to: Optional[int] = None):
    """Полная замена связей: тело — список связей (или {items: [...]}); заменяются все связи тех пар лет, что есть в списке;
    с параметрами year_from & year_to заменяется именно эта пара (пустой список — очистка пары)."""
    items = body.get("items", []) if isinstance(body, dict) else body
    return _links(multiyear.replace_links, items, year_from, year_to)


@router.post("/account-links")
def links_create(body: Union[dict, list] = Body(...)):
    items = body if isinstance(body, list) else [body]
    return _links(multiyear.upsert_links, items)


@router.post("/account-links/auto")
def links_auto(body: dict = Body(...)):
    """Автоподбор (без записи): отчёт «сколько сопоставлено / спорных / без пары» + списки спорных и без пары (по обороту)."""
    def go():
        rep = multiyear.auto_match(int(body.get("year_from") or 0), int(body.get("year_to") or 0),
                                   float(body.get("min_confidence") or multiyear.AUTO_MIN_CONFIDENCE), int(body.get("list_limit") or 300))
        out = multiyear.public_report(rep)
        out["links"] = [{"year_from": rep["summary"]["year_from"], "code_from": r["code_from"], "year_to": rep["summary"]["year_to"],
                         "code_to": r["code_to"], "kind": r["method"], "confidence": r["confidence"]}
                        for r in rep["_results"] if r["status"] == "matched"]
        return out
    return _links(go)


@router.post("/account-links/auto/apply")
def links_auto_apply(body: dict = Body(...)):
    """Записывает уверенные автосвязи (ручные не трогает); возвращает тот же отчёт + applied."""
    return _links(multiyear.apply_auto, int(body.get("year_from") or 0), int(body.get("year_to") or 0),
                  float(body.get("min_confidence") or multiyear.AUTO_MIN_CONFIDENCE), bool(body.get("replace", True)))


@router.put("/account-links/{lid}")
def links_update(lid: int, body: dict = Body(...)):
    r = _links(multiyear.update_link, lid, body)
    if r is None:
        err(404, "Связь не найдена")
    return r


@router.delete("/account-links/{lid}")
def links_delete(lid: int):
    if not multiyear.delete_link(lid):
        err(404, "Связь не найдена")
    return {"ok": True}


@router.delete("/account-links")
def links_delete_bulk(year_from: int, year_to: int, method: Optional[str] = None):
    return {"deleted": multiyear.delete_links(year_from, year_to, method)}


# ---------------------------------------------------------------------------------------------
# демонстрационные данные
# ---------------------------------------------------------------------------------------------

DEMO_FOLDER = "Примеры"
DEMO_CLASSIFIER_FMT = "Российские коды (метки Logo %d)"
DEMO_NOTE = ("Пример на основе турецкого плана счетов Tekdüzen (классы 1–7). Официальных бланков Туркменистана с кодами строк нет — "
             "состав и формулы строк подлежат согласованию с бухгалтером.")


def _base(title: str, d1: str, d2: str) -> dict:
    return {"title": title, "period": {"date_from": d1, "date_to": d2}, "currency": dsparams.get("currency_local")["code"], "scale": 1, "denominate": True,
            "include_closing": True, "filters": {"accounts": [], "groups": [], "centers": [], "trcodes": [], "text": ""},
            "options": {"hide_zero": True, "sort": {"by": "code", "dir": "asc"}, "unmapped": True},
            "columns": {"balance": "net", "by_month": False, "show_usd": False}, "rows": [], "note": DEMO_NOTE}


def demo_years() -> tuple:
    """(предыдущая метка, последняя метка) основных книг набора; образцы подготовлены только для турецкого плана счетов."""
    if plan.current().template != "turkish":
        raise classifiers.ClassifierError("Образцы подготовлены для турецкого плана счетов")
    ls = books.labels()
    if not ls:
        raise classifiers.ClassifierError("В наборе нет основных книг: разметьте книги в разделе «Книги и годы»")
    return (ls[-2] if len(ls) > 1 else ls[-1]), ls[-1]


def balance_example(d1=None, d2=None) -> dict:
    yp = demo_years()[0]
    d1, d2 = d1 or f"{yp}-01-01", d2 or f"{yp}-12-31"
    L = lambda code, name, formula="", indent=1, bold=False: {"code": code, "name": name, "formula": formula, "indent": indent, "bold": bold}
    lines = [
        L("A", "АКТИВ", indent=0),
        L("110", "Денежные средства и эквиваленты (10x)", "SK(10*)"),
        L("120", "Дебиторская задолженность, дебетовые остатки (12x)", "SKD(12* ~ 129*)"),
        L("125", "Резерв по сомнительным долгам (129)", "SK(129*)"),
        L("130", "Прочие краткосрочные финансовые активы и дебиторы (11x, 13x, 14x, 16x, 17x)", "SK(11*|13*|14*|16*|17*)"),
        L("135", "Расходы будущих периодов (18x)", "SK(18*)"),
        L("140", "Запасы (15x)", "SK(15*)"),
        L("150", "Прочие оборотные активы (19x)", "SK(19*)"),
        L("190", "Итого оборотные активы", "[110]+[120]+[125]+[130]+[135]+[140]+[150]", 0, True),
        L("210", "Основные средства, нетто (25x)", "SK(25*)"),
        L("220", "Нематериальные активы, нетто (26x)", "SK(26*)"),
        L("230", "Прочие внеоборотные активы (2xx кроме 25x, 26x)", "SK(2* ~ 25*|26*)"),
        L("290", "Итого внеоборотные активы", "[210]+[220]+[230]", 0, True),
        L("295", "Дебетовые остатки по счетам обязательств (3xx, 4xx)", "SKD(3*|4*)"),
        L("300", "ИТОГО АКТИВ", "[190]+[290]+[295]", 0, True),
        L("P", "ПАССИВ", indent=0),
        L("320", "Торговая кредиторская задолженность (32x)", "SKK(32*)"),
        L("325", "Прочие краткосрочные обязательства (3xx кроме 32x)", "SKK(3* ~ 32*)"),
        L("330", "Кредитовые остатки по дебиторам, авансы полученные (12x)", "SKK(12* ~ 129*)"),
        L("340", "Итого краткосрочные обязательства", "[320]+[325]+[330]", 0, True),
        L("410", "Долгосрочные обязательства (4xx)", "SKK(4*)"),
        L("420", "Итого обязательства", "[340]+[410]", 0, True),
        L("510", "Уставный капитал (50x, 51x)", "-SK(50*|51*)"),
        L("520", "Резервы и прочий капитал (52x–56x)", "-SK(52*|53*|54*|55*|56*)"),
        L("530", "Прибыль и убытки прошлых лет (57x, 58x)", "-SK(57*|58*)"),
        L("540", "Результат отчётного года (59x и незакрытые 6xx, 7xx)", "-SK(59*|6*|7*)"),
        L("550", "Итого капитал", "[510]+[520]+[530]+[540]", 0, True),
        L("600", "ИТОГО ПАССИВ", "[420]+[550]", 0, True),
    ]
    d = _base("Баланс (пример — не официальный бланк)", d1, d2)
    d["type"] = "form"
    d["include_closing"] = True
    d["form"] = {"lines": lines, "template_id": None, "columns": ["begin", "end", "prev"],
                 "checks": [{"name": "Актив = Пассив (допуск 0,50: начальный остаток в самом Logo может быть не сбалансирован на копейки)", "left": "[300]", "right": "[600]", "tolerance": 0.5},
                            {"name": "Оборотные активы = сальдо класса 1 + кредитовые остатки 12x", "left": "[190]",
                             "right": "SK(1*)+SKK(12* ~ 129*)"},
                            {"name": "Классы 8–9 в балансе не участвуют (их сальдо должно быть 0)", "left": "SK(8*|9*)", "right": "0"}],
                 "coverage": ["1*", "2*", "3*", "4*", "5*", "6*", "7*"]}
    return d


def pnl_example(d1=None, d2=None) -> dict:
    yp = demo_years()[0]
    d1, d2 = d1 or f"{yp}-01-01", d2 or f"{yp}-12-31"
    L = lambda code, name, formula="", indent=1, bold=False: {"code": code, "name": name, "formula": formula, "indent": indent, "bold": bold}
    lines = [
        L("A", "Результаты основной деятельности", indent=0),
        L("010", "Выручка от продаж (60x)", "OBK(60*)-OBD(60*)"),
        L("020", "Скидки и возвраты (61x)", "OBK(61*)-OBD(61*)"),
        L("030", "Чистая выручка", "[010]+[020]", 0, True),
        L("040", "Себестоимость продаж (62x)", "OBD(62*)-OBK(62*)"),
        L("050", "Валовая прибыль", "[030]-[040]", 0, True),
        L("060", "Операционные расходы (63x)", "OBD(63*)-OBK(63*)"),
        L("070", "Затраты класса 7, не списанные на себестоимость и расходы", "OBD(7*)-OBK(7*)"),
        L("080", "Операционная прибыль", "[050]-[060]-[070]", 0, True),
        L("B", "Прочая деятельность", indent=0),
        L("090", "Прочие доходы (64x)", "OBK(64*)-OBD(64*)"),
        L("100", "Прочие расходы (65x)", "OBD(65*)-OBK(65*)"),
        L("110", "Финансовые расходы (66x)", "OBD(66*)-OBK(66*)"),
        L("120", "Чрезвычайные доходы и расходы, нетто (67x, 68x)", "OBK(67*|68*)-OBD(67*|68*)"),
        L("130", "Прибыль до налогообложения", "[080]+[090]-[100]-[110]+[120]", 0, True),
        L("140", "Налог на прибыль и обязательные платежи (69x)", "OBD(69*)-OBK(69*)"),
        L("150", "Чистая прибыль (убыток)", "[130]-[140]", 0, True),
    ]
    d = _base("Отчёт о прибылях и убытках (пример — не официальный бланк)", d1, d2)
    d["type"] = "form"
    d["include_closing"] = False    # закрывающие ордера 570/590/690/691/692 исключены осознанно: иначе обороты 6xx обнуляются
    d["form"] = {"lines": lines, "template_id": None, "columns": ["period", "prev"],
                 "checks": [{"name": "Чистая прибыль = оборот Кт − оборот Дт по классам 6 и 7", "left": "[150]", "right": "OBK(6*|7*)-OBD(6*|7*)"}],
                 "coverage": ["6*", "7*"]}
    return d


def journal_examples(d1=None, d2=None) -> list[tuple[str, dict]]:
    yl = demo_years()[1]
    d1, d2 = d1 or f"{yl}-01-01", d2 or f"{yl}-12-31"
    """Журналы-ордера/ведомости с фиксированными графами под бланк (графы подобраны по крупнейшим корреспондентам 2026 г.)."""
    def mk(title, account, side, cols):
        d = _base(title, d1, d2)
        d.update({"type": "journal", "account": account,
                  "journal": {"side": side, "rows": "day", "fixed_cols": [{"sel": s, "title": t} for s, t in cols], "rest": "other", "with_balance": True}})
        return (title, d)
    return [
        mk("Журнал-ордер № 1 — касса (счёт 100, по кредиту)", "100*", "credit",
           [("102*", "102 Банки"), ("100*", "100 Касса (внутр. перемещения)"), ("196*", "196 Подотчёт — персонал"), ("195*", "195 Подотчёт — на работы"),
            ("320*", "320 Поставщики"), ("335*", "335 Расчёты с персоналом")]),
        mk("Ведомость № 1 — касса (счёт 100, по дебету)", "100*", "debit",
           [("120*", "120 Покупатели"), ("320*", "320 Поставщики"), ("100*", "100 Касса (внутр. перемещения)"), ("102*", "102 Банки"),
            ("197*", "197 Недостачи и излишки"), ("335*", "335 Расчёты с персоналом"), ("679*", "679 Прочие доходы")]),
        mk("Журнал-ордер — поставщики (счёт 320, по кредиту)", "320*", "credit",
           [("150*", "150 Сырьё и материалы"), ("153*", "153 Товары"), ("120*", "120 Покупатели"), ("253*", "253 Машины и оборудование"),
            ("102*", "102 Банки"), ("131*", "131 Расчёты с учредителями"), ("100*", "100 Касса"), ("191*", "191 НДС к вычету")]),
    ]


def seed_demo() -> dict:
    """Идемпотентно: создаёт отсутствующие демо-объекты (по названию); существующие не трогает."""
    created, skipped = [], []
    yp, yl = demo_years()
    DEMO_CLASSIFIER = DEMO_CLASSIFIER_FMT % yl
    cid = None
    for c in classifiers.list_classifiers():
        if c["name"] == DEMO_CLASSIFIER:
            cid = c["id"]
    if cid is None:
        cid = classifiers.import_specode(yl, DEMO_CLASSIFIER)
        created.append(f"классификатор «{DEMO_CLASSIFIER}»")
    else:
        skipped.append(f"классификатор «{DEMO_CLASSIFIER}»")
    osv_acc = _base("ОСВ по счетам (пример)", f"{yp}-01-01", f"{yp}-12-31")
    osv_acc.update({"type": "osv", "rows": [{"kind": "account", "max_level": 1}], "columns": {"balance": "expanded", "by_month": False, "show_usd": False}})
    osv_ru = _base("ОСВ по российским кодам (пример)", f"{yl}-01-01", f"{yl}-09-30")
    osv_ru.update({"type": "osv", "rows": [{"kind": "classifier", "id": cid, "depth": 2}, {"kind": "account", "max_level": 0}],
                   "columns": {"balance": "net", "by_month": False, "show_usd": False}})
    existing = {r["name"] for r in store.list_reports() if r["folder"] == DEMO_FOLDER}
    ids = {}
    for name, typ, d in (("ОСВ по счетам (пример)", "osv", osv_acc), ("ОСВ по российским кодам (пример)", "osv", osv_ru),
                         ("Баланс (пример)", "form", balance_example()), ("Отчёт о прибылях и убытках (пример)", "form", pnl_example()),
                         *[(n, "journal", d) for n, d in journal_examples()]):
        if name in existing:
            skipped.append(f"отчёт «{name}»")
            continue
        ids[name] = store.create_report(name, typ, DEMO_FOLDER, store.normalize_definition(d, typ), "Демонстрационные данные")
        created.append(f"отчёт «{name}»")
    reports = [{"id": r["id"], "name": r["name"], "type": r["type"]} for r in store.list_reports() if r["folder"] == DEMO_FOLDER]
    return {"created": len(created), "created_items": created, "skipped": skipped, "classifier_id": cid, "report_ids": ids, "reports": reports}


@router.post("/demo/seed")
def demo_seed():
    try:
        return seed_demo()
    except classifiers.ClassifierError as e:
        err(400, str(e))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "seed":
        store.migrate()
        print(seed_demo())
    else:
        print("usage: python -m app.routes_forms seed")
