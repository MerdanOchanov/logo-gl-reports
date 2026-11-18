"""FastAPI: /api + статика frontend/dist."""
from __future__ import annotations

import mimetypes
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import books, cache, config, dsctx, dsmgr, legacy_gap, registry, store
from .api import router

# На Windows mimetypes берёт типы из реестра (.js -> text/plain): задаём явно до монтирования статики
for _ext, _type in ((".js", "application/javascript"), (".mjs", "application/javascript"), (".css", "text/css"),
                    (".svg", "image/svg+xml"), (".woff2", "font/woff2"), (".json", "application/json")):
    mimetypes.add_type(_type, _ext)

app = FastAPI(title="Конструктор бухгалтерских отчётов")


@app.on_event("startup")
def _startup():
    registry.init()
    rt = dsmgr.runtime_for(registry.root_dataset() or registry.default_dataset())     # набор №1 (файлы в корне data)
    dsctx.set_sync_limit(config.SYNC_PARALLEL)
    with dsctx.use(rt):
        store.migrate()
        cache.init_db()
        _compat_check()
    rt.ready = True
    # фоновые потоки не наследуют ContextVar - набор передаётся явно; остальные наборы инициализируются лениво при первом обращении (dsmgr.ensure_ready)
    threading.Thread(target=dsctx.run_in, args=(rt, cache.refresh_plans), name="refresh-plans", daemon=True).start()       # план счетов (волна 6) - только план, не проводки
    threading.Thread(target=dsctx.run_in, args=(rt, cache.refresh_attrs_all), name="refresh-attrs", daemon=True).start()   # атрибуты проводок/ордеров (волна 7)


def _compat_check() -> None:
    """Набор №1 после миграции: метка/фирма/делитель основных книг должны совпасть с прежней картой «год → фирма»."""
    import logging
    try:
        if (registry.root_dataset() or {}).get("name") != legacy_gap.LEGACY_DATASET_NAME:
            return
        got = {b.label: (b.src_firm, b.divisor) for b in books.main_books() if b.src_period == 1}
        bad = []
        for y, f in legacy_gap.LEGACY_YEAR_FIRM.items():
            alts = legacy_gap.LEGACY_ALT_YEAR_FIRM.get(y, [])
            if y not in got or got[y][0] not in [f] + alts:
                bad.append(str(y))
        if bad:
            logging.getLogger("buhlogo").error("Разметка набора не совпала с прежней картой: годы %s", ", ".join(bad))
    except Exception:
        pass


_FIELD_RU = {"file": "файл", "year": "год", "year_from": "начальный год", "year_to": "конечный год", "firm": "фирма", "id": "номер", "limit": "предел",
             "offset": "смещение", "date_from": "дата начала", "date_to": "дата окончания", "name": "название", "login": "логин", "password": "пароль",
             "role": "роль", "mode": "режим", "book": "книга", "rid": "номер отчёта", "cid": "номер", "uid": "номер пользователя", "lid": "номер связи"}


def validation_text(errors) -> str:
    """Ошибки проверки запроса FastAPI (422) -> один русский текст без английских названий полей."""
    out = []
    for e in errors or []:
        loc = [str(x) for x in (e.get("loc") or ()) if x not in ("body", "query", "path", "header", "cookie")]
        typ = str(e.get("type") or "")
        field = _FIELD_RU.get(loc[-1]) if loc else None
        if typ == "missing":
            if field == "файл" or (loc and loc[-1] == "file"):
                t = "Не выбран файл"
            elif field:
                t = "Не указано обязательное значение: " + field
            elif not loc:
                t = "Не передано тело запроса"
            else:
                t = "Не указано обязательное значение"
        elif typ.startswith("json_invalid") or typ == "json_type" or typ == "model_attributes_type":
            t = "Тело запроса задано неверно: ожидаются данные в нужном виде"
        elif "int" in typ:
            t = ("Значение «%s» должно быть целым числом" % field) if field else "Значение должно быть целым числом"
        elif "float" in typ or "number" in typ:
            t = ("Значение «%s» должно быть числом" % field) if field else "Значение должно быть числом"
        elif "bool" in typ:
            t = "Значение должно быть «да» или «нет»"
        elif typ in ("greater_than", "greater_than_equal", "less_than", "less_than_equal"):
            t = ("Значение «%s» вне допустимых пределов" % field) if field else "Значение вне допустимых пределов"
        else:
            t = ("Значение «%s» задано неверно" % field) if field else "Запрос задан неверно"
        if t not in out:
            out.append(t)
    return "; ".join(out) or "Запрос задан неверно"


@app.exception_handler(RequestValidationError)
async def _validation(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": validation_text(exc.errors())}, status_code=400)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    return JSONResponse({"detail": f"Внутренняя ошибка сервера: {exc}"}, status_code=500)


# волна 2B: авторизация (роли проверяет middleware) и формы/шаблоны/связи счетов — раньше основного роутера (перехватывают /api/run, /api/export/xlsx, /api/me)
from . import auth, routes_forms  # noqa: E402
app.add_middleware(auth.AccessMiddleware)
app.include_router(auth.router)
app.include_router(routes_forms.router)
from . import routes_dataset, routes_multi  # noqa: E402
app.include_router(routes_dataset.router)
app.include_router(routes_multi.router)
app.include_router(router)

if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets") if (config.FRONTEND_DIST / "assets").exists() else None

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Не найдено"}, status_code=404)
        f = config.FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html")
