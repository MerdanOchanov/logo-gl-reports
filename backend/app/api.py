"""HTTP API (/api/*) по контракту docs/ARCHITECTURE.md §5."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response

from . import accounts as acc_mod
from . import attrs as attrs_mod
from . import books, cache, classifiers, dsctx, registry, store
from . import export_xlsx as xlsx_mod
from . import corr as corr_mod
from .engine import analysis, card, carry_gaps, chess, drill as drill_mod, journal, ledger, osv
from .engine.common import ReportError, finalize_checks, attach_organization

router = APIRouter(prefix="/api")

TYPE_NAMES = {"card": "карточка счёта", "ledger": "главная книга", "analysis": "анализ счёта", "chess": "шахматка",
              "journal": "журнал-ордер", "form": "форма по строкам"}


def err(status: int, text: str):
    raise HTTPException(status, text)


# ---- статус, фирмы, синхронизация -----------------------------------------------------------------

def _ds_info() -> dict:
    try:
        d = registry.get_dataset(dsctx.current().id) or registry.root_dataset() or registry.default_dataset()
        return {"id": d["id"], "name": d["name"], "state": d["state"]}
    except Exception:
        return {"id": 1, "name": "", "state": "ready"}


@router.get("/status")
def status():
    s = cache.status()
    s["dataset"] = _ds_info()
    return s


@router.get("/firms")
def firms():
    return cache.list_firms()


@router.put("/firms/{firm}")
def put_firm(firm: int, body: dict = Body(...)):
    if not cache.set_firm_enabled(firm, bool(body.get("enabled"))):
        err(404, "Фирма не найдена")
    return {"ok": True}


@router.get("/years")
def years():
    return cache.list_years()


@router.put("/years/{year}/book")
def put_year_book(year: int, body: dict = Body(...)):
    """Смена основной книги (фирмы) года. Одновременно в отчётах участвует ровно одна фирма года."""
    try:
        firm = int(body.get("firm"))
    except (TypeError, ValueError):
        err(400, "Укажите фирму")
    if dsctx.current().job["syncing"]:
        err(409, "Синхронизация уже идёт - повторите смену книги после её окончания")
    try:
        res = cache.set_year_book(year, firm)
    except ValueError as e:
        err(400, str(e))
    res["sync_started"] = bool(res["changed"]) and cache.start_sync([firm], force=True)
    return res


@router.post("/sync")
def sync(body: dict = Body(default={})):
    """firms - список src (для периода 01 - номер фирмы); books - список идентификаторов книг; по умолчанию - источники основных книг."""
    firms = body.get("firms")
    if body.get("books"):
        ids = {int(x) for x in body["books"]}
        firms = sorted({b.src for b in books.all_books() if b.id in ids})
    if not cache.start_sync(firms, bool(body.get("force"))):
        err(409, "Синхронизация уже идёт")
    return {"started": True}


@router.get("/accounts")
def accounts(year: int, book: Optional[int] = None):
    """План счетов метки года (объединение основных книг метки); book - план одной книги."""
    if book is not None:
        return acc_mod.get_plan_book(book)
    return acc_mod.get_plan(year)


@router.get("/centers")
def centers(year: int):
    return acc_mod.get_centers(year)


# ---- классификаторы ------------------------------------------------------------------------------------

def _cls(fn, *a):
    try:
        return fn(*a)
    except classifiers.ClassifierError as e:
        err(400, str(e))


@router.get("/classifiers")
def cls_list():
    return classifiers.list_classifiers()


@router.post("/classifiers")
def cls_create(body: dict = Body(...)):
    cid = _cls(classifiers.create_classifier, body.get("name"), body.get("description", ""))
    return classifiers.get_classifier(cid)


@router.post("/classifiers/import-specode")
def cls_import(body: dict = Body(...)):
    if not body.get("year"):
        err(400, "Укажите год")
    cid = _cls(classifiers.import_specode, int(body["year"]), body.get("name") or "")
    return classifiers.get_classifier(cid)


@router.get("/classifiers/{cid}")
def cls_get(cid: int):
    d = classifiers.get_classifier(cid)
    if not d:
        err(404, "Классификатор не найден")
    return d


@router.put("/classifiers/{cid}")
def cls_put(cid: int, body: dict = Body(...)):
    return _cls(classifiers.replace_classifier, cid, body)


@router.delete("/classifiers/{cid}")
def cls_delete(cid: int):
    if not classifiers.delete_classifier(cid):
        err(404, "Классификатор не найден")
    return {"ok": True}


@router.post("/classifiers/{cid}/copy")
def cls_copy(cid: int):
    new = classifiers.copy_classifier(cid)
    if new is None:
        err(404, "Классификатор не найден")
    return classifiers.get_classifier(new)


@router.get("/classifiers/{cid}/resolve")
def cls_resolve(cid: int, year: int):
    return _cls(classifiers.resolve_year, cid, year)


# ---- сохранённые отчёты ----------------------------------------------------------------------------------

@router.get("/reports")
def rep_list():
    return store.list_reports()


@router.post("/reports")
def rep_create(body: dict = Body(...)):
    if not body.get("name"):
        err(400, "Укажите название отчёта")
    definition = body.get("definition") or {}
    rid = store.create_report(body["name"], body.get("type") or definition.get("type") or "osv", body.get("folder", ""), definition,
                              body.get("comment", ""))
    return store.get_report(rid)


@router.get("/reports/{rid}")
def rep_get(rid: int):
    r = store.get_report(rid)
    if not r:
        err(404, "Отчёт не найден")
    return r


@router.put("/reports/{rid}")
def rep_put(rid: int, body: dict = Body(...)):
    if not store.update_report(rid, body):
        err(404, "Отчёт не найден")
    return store.get_report(rid)


@router.delete("/reports/{rid}")
def rep_delete(rid: int):
    if not store.delete_report(rid):
        err(404, "Отчёт не найден")
    return {"ok": True}


@router.get("/reports/{rid}/versions")
def rep_versions(rid: int):
    if not store.get_report(rid):
        err(404, "Отчёт не найден")
    return store.report_versions(rid)


@router.post("/reports/{rid}/copy")
def rep_copy(rid: int):
    new = store.copy_report(rid)
    if new is None:
        err(404, "Отчёт не найден")
    return store.get_report(new)


# ---- расчёт ---------------------------------------------------------------------------------------------

ENGINES = {"card": card, "analysis": analysis, "ledger": ledger, "chess": chess, "journal": journal}


def run_definition(defn: dict) -> dict:
    if defn is not None and not isinstance(defn, dict):
        err(400, "Описание отчёта должно быть объектом")
    t = (defn or {}).get("type") or "osv"
    try:
        return attach_organization(_run_definition(defn, t), defn)
    except ReportError as e:
        err(400, str(e))
    except (AttributeError, TypeError, KeyError) as e:      # последняя страховка: поле описания отчёта неверного вида, не пойманное проверкой normalize
        import logging
        logging.getLogger("buhlogo").exception("Неверная структура описания отчёта: %s", e)
        err(400, "Описание отчёта задано неверно: проверьте, что строки, отбор, графы и период заданы в нужном виде")


def _run_definition(defn: dict, t: str) -> dict:
    try:
        if t == "osv":
            return finalize_checks(osv.run(defn))
        if t == "carry_gaps":
            return finalize_checks(carry_gaps.run(defn))
        if t in ENGINES:
            return finalize_checks(ENGINES[t].run(defn))
    except ReportError as e:
        err(400, str(e))
    if t in TYPE_NAMES:
        err(501, f"Отчёт «{TYPE_NAMES[t]}» ({t}) ещё не реализован на сервере — будет в следующих волнах")
    err(400, f"Неизвестный тип отчёта «{t}»")


@router.post("/run")
def run(body: dict = Body(...)):
    return run_definition(body.get("definition") or body)


@router.post("/export/xlsx")
def export_xlsx(body: dict = Body(...)):
    defn = body.get("definition") or body
    result = run_definition(defn)
    if str(body.get("title") or "").strip() and body.get("definition"):
        result["title"] = str(body["title"]).strip()      # название сохранённого отчёта (необязательное поле тела запроса)
    data = xlsx_mod.build(result, ((defn or {}).get("columns") or {}).get("extra"))
    name = quote((result.get("title") or "report") + ".xlsx")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=report.xlsx; filename*=UTF-8''{name}"})


@router.post("/drill")
def drill(body: dict = Body(...)):
    try:
        return drill_mod.drill(body)
    except ReportError as e:
        err(400, str(e))


@router.post("/export/drill")
def export_drill(body: dict = Body(...)):
    """Расшифровка в Excel: тело как у /api/drill (limit по умолчанию и максимум 50 000 строк) + extra: [ключи доп. колонок] (или columns.extra)."""
    req = dict(body)
    req["limit"] = min(int(req.get("limit") or 50000), 50000)
    req["offset"] = 0
    extra = req.get("extra") or (req.get("columns") or {}).get("extra") or []
    try:
        res = drill_mod.drill(req)
    except ReportError as e:
        err(400, str(e))
    from .engine.common import fmt_date, parse_date
    cols = [{"key": "fiche_no", "title": "№ ордера", "type": "text"}, {"key": "account", "title": "Счёт", "type": "text"},
            {"key": "account_name", "title": "Название счёта", "type": "text"}, {"key": "corr", "title": "Корр. счёт", "type": "text"},
            {"key": "method", "title": "Метод", "type": "text"},
            {"key": "dt", "title": "Дебет", "type": "money"}, {"key": "kt", "title": "Кредит", "type": "money"}] + attrs_mod.optional_column_defs()
    rows = [{"id": str(i), "kind": "line", "code": ln["date"], "name": ln["text"] or "", "level": 0,
             "values": {k: ln.get(k) for k in [c["key"] for c in cols]}} for i, ln in enumerate(res["lines"])]
    for r_ in rows:
        try:
            r_["code"] = fmt_date(parse_date(r_["code"]))
        except Exception:       # noqa: BLE001
            pass
    rows.append({"id": "total", "kind": "total", "code": "", "name": "ИТОГО", "level": 0, "values": {"dt": res["sum_dt"], "kt": res["sum_kt"]}})
    warnings = list(res.get("warnings") or [])
    if res["total"] > len(res["lines"]):
        warnings.append(f"Выгружено {len(res['lines'])} из {res['total']} строк (предел 50 000) — уточните отбор")
    p = req.get("period") or {}

    def _d(x):
        try:
            return fmt_date(parse_date(x))
        except Exception:       # noqa: BLE001
            return str(x or "")
    title = str(req.get("title") or "").strip() or "Расшифровка"
    result = {"title": title, "subtitle": f"Период: {_d(p.get('date_from'))} – {_d(p.get('date_to'))}; строк: {res['total']}",
              "code_title": "Дата", "name_title": "Содержание", "columns": cols, "rows": rows, "checks": [], "warnings": warnings}
    data = xlsx_mod.build(result, extra)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=drill.xlsx"})


@router.get("/dicts")
def dicts(year: int):
    """Справочники книги года для отбора: подразделения, отделы, источники ордеров, авторы (только встречающиеся значения)."""
    try:
        return attrs_mod.dicts(year)
    except ValueError as e:
        err(400, str(e))


@router.get("/fiche")
def fiche(firm: int, id: int, denominate: bool = True):
    try:
        r = drill_mod.fiche(firm, id, denominate)
    except ReportError as e:
        err(400, str(e))
    if r is None:
        err(404, "Ордер не найден")
    return r


# ---- настройки --------------------------------------------------------------------------------------------

@router.get("/closing-rules")
def closing_rules_get():
    return store.get_closing_rules()


@router.put("/closing-rules")
def closing_rules_put(body: list = Body(...)):
    try:
        store.set_closing_rules(body)
    except ValueError as e:
        err(400, str(e))
    res = cache.recompute_closing()
    return {"rules": store.get_closing_rules(), "closing_lines": res}


@router.get("/account-names")
def account_names_get():
    return store.get_account_names()


@router.put("/account-names")
def account_names_put(body: list = Body(...)):
    store.set_account_names(body)
    return store.get_account_names()


# ---- корреспонденция --------------------------------------------------------------------------------------------

@router.get("/corr/stats")
def corr_stats(year: Optional[int] = None):
    return corr_mod.stats(year)


@router.get("/corr/disputed")
def corr_disputed(year: Optional[int] = None, limit: int = 1000):
    return corr_mod.disputed(year, limit)


@router.get("/corr/override")
def corr_override_get(firm: int, fiche_id: int):
    r = corr_mod.get_override(firm, fiche_id)
    if r is None:
        err(404, "Ручное закрепление для ордера не задано")
    return r


@router.put("/corr/override")
def corr_override_put(body: dict = Body(...)):
    try:
        return corr_mod.set_override(int(body.get("firm")), int(body.get("fiche_id")), body.get("pairs") or [], body.get("note") or "")
    except (TypeError, ValueError) as e:
        err(400, str(e))


@router.delete("/corr/override")
def corr_override_delete(firm: int, fiche_id: int):
    return {"ok": True, "existed": corr_mod.delete_override(firm, fiche_id)}


@router.post("/corr/rebuild")
def corr_rebuild(body: dict = Body(default={})):
    """Пересчёт пар из загруженных проводок (без Logo); firms — необязательный список."""
    return {"seconds": corr_mod.rebuild_all(body.get("firms"))}


@router.get("/me")
def me():
    return {"login": "admin", "role": "admin"}
