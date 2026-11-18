"""Наборы данных этапа B: рантаймы по строкам реестра, выбор набора на запрос, создание/удаление, ленивая инициализация файлов набора.
Набор №1 (path='') - рантайм dsctx.default() на файлах в корне data (store.DB_PATH / cache.DB_PATH); остальные - data/ds/<id>/{settings.db, cache.db, templates/}."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

from . import config, dsctx, registry


class DatasetError(Exception):
    """Ошибка выбора/управления набором: status - HTTP-код, detail - текст по-русски."""

    def __init__(self, status: int, detail: str):
        Exception.__init__(self, detail)
        self.status = status
        self.detail = detail


def data_dir_of(row: dict) -> Optional[Path]:
    """Каталог файлов набора; None для набора №1 (файлы в корне data)."""
    return (config.DATA_DIR / row["path"]) if row.get("path") else None


def runtime_for(row: dict) -> dsctx.DatasetRuntime:
    """Рантайм набора по строке реестра (создаётся один раз; название и подключение обновляются при каждом обращении)."""
    if not row.get("path"):
        rt = dsctx.default()
    else:
        rt = dsctx.get_runtime(row["id"])
        if rt is None:
            d = config.DATA_DIR / row["path"]
            rt = dsctx.put_runtime(dsctx.DatasetRuntime(id=row["id"], settings_path=d / "settings.db", cache_path=d / "cache.db",
                                                        templates_dir=d / "templates", cache_kb=config.CACHE_PAGES_KB_OTHER))
    rt.id, rt.name, rt.conn_id = row["id"], row["name"], row["connection_id"]
    rt.last_used = time.time()
    return rt


def ensure_ready(rt: dsctx.DatasetRuntime) -> None:
    """Первое обращение к набору в этом процессе: каталог, миграции settings.db, схема cache.db, фоновое дочитывание плана/атрибутов (только этот набор)."""
    if rt.ready:
        return
    from . import cache, store
    with rt.init_lock:
        if rt.ready:
            return
        for p in (rt.settings_path, rt.cache_path):
            if p is not None:
                p.parent.mkdir(parents=True, exist_ok=True)
        if rt.templates_dir is not None:
            rt.templates_dir.mkdir(parents=True, exist_ok=True)
        with dsctx.use(rt):
            store.migrate()
            cache.init_db()
        rt.ready = True
    for fn in (cache.refresh_plans, cache.refresh_attrs_all):
        threading.Thread(target=dsctx.run_in, args=(rt, fn), name="ds%d-%s" % (rt.id, fn.__name__), daemon=True).start()


def resolve(ds_id: Optional[int] = None, allow_archived: bool = False) -> Tuple[dsctx.DatasetRuntime, dict]:
    """Набор запроса: ds_id или набор по умолчанию. DatasetError: 404 «Набор данных не найден», 409 «в архиве»."""
    if ds_id is None:
        row = registry.default_dataset()
    else:
        row = registry.get_dataset(ds_id)
        if row is None:
            raise DatasetError(404, "Набор данных не найден")
    if row["archived"] and not allow_archived:
        raise DatasetError(409, "Набор данных «%s» в архиве: верните его из архива в настройках" % row["name"])
    rt = runtime_for(row)
    ensure_ready(rt)
    return rt, row


_last_sweep = [0.0]


def sweep(now: Optional[float] = None) -> list:
    """Закрывает простаивающие наборы (не чаще раза в минуту)."""
    now = now or time.time()
    if now - _last_sweep[0] < 60:
        return []
    _last_sweep[0] = now
    return dsctx.evict_idle(config.IDLE_DATASET_SEC, now)


# ---------------------------------------------------------------------------------------------
# создание и удаление
# ---------------------------------------------------------------------------------------------

def create(body: dict, created_by: str = "") -> dict:
    """POST /api/datasets: {name, connection_id, org_name?, plan_template: turkish|none, copy_from?: id, copy_parts?: [...]}. Новый набор пуст (без книг),
    state='new': дальше мастер «Книги и годы» (скан Logo -> предложение -> применение) в контексте этого набора."""
    from . import dsparams, legacy_gap, settings_io, store
    template = body.get("plan_template") or "none"
    if template not in ("turkish", "none"):
        raise DatasetError(400, "Шаблон плана счетов: «turkish» (турецкий план) или «none» (пустой)")
    try:
        cid = int(body.get("connection_id"))
    except (TypeError, ValueError):
        raise DatasetError(400, "Укажите подключение к Logo для набора данных")
    src_rt = None
    copy_from = body.get("copy_from")
    if copy_from not in (None, ""):
        try:
            src_rt, _ = resolve(int(copy_from))
        except (TypeError, ValueError):
            raise DatasetError(400, "Набор для копирования настроек указан неверно")
    try:
        row = registry.create_dataset(body.get("name"), cid, body.get("org_name") or "", created_by)
    except ValueError as e:
        raise DatasetError(400, str(e))
    rt = runtime_for(row)
    try:
        ensure_ready(rt)
        with dsctx.use(rt):
            if template == "turkish":
                dsparams.set_many({"plan_profile": {"template": "turkish", "mode": "sep", "sep": ".", "root_len": 3, "widths": [],
                                                    "balance_roots": ["1", "2", "3", "4", "5"], "pnl_roots": ["6", "7"], "noncarried": ["6*", "7*", "590"]}})
                store.set_closing_rules([{"kind": "accounts", "value": legacy_gap.TURKISH_CLOSING_ACCOUNTS, "enabled": True}])
        if src_rt is not None:
            parts = body.get("copy_parts") or list(settings_io.COPY_PARTS)
            with dsctx.use(src_rt):
                data = settings_io.export_settings([p for p in parts if p in settings_io.PARTS])
            with dsctx.use(rt):
                settings_io.import_settings(data, "replace", [p for p in parts if p in settings_io.PARTS], resync=False)
    except Exception:
        # создание не удалось - строку и каталог убираем, чтобы не оставлять «полунабор»
        dsctx.forget(row["id"])
        registry.delete_dataset_row(row["id"])
        d = data_dir_of(row)
        if d is not None and d.exists():
            shutil.rmtree(d, ignore_errors=True)
        raise
    return registry.get_dataset(row["id"])


def remove(did: int, confirm: str) -> dict:
    """DELETE /api/datasets/{id}?confirm=<название>: только архивный набор; каталог переносится в data/ds/_deleted/<id>-<дата>/ (файлы не стираются)."""
    row = registry.get_dataset(did)
    if row is None:
        raise DatasetError(404, "Набор данных не найден")
    if not row["path"]:
        raise DatasetError(409, "Исходный набор данных удалить нельзя: переведите его в архив")
    if not row["archived"]:
        raise DatasetError(409, "Удалить можно только набор данных из архива: сначала переведите «%s» в архив" % row["name"])
    if (confirm or "").strip() != row["name"]:
        raise DatasetError(400, "Для удаления введите название набора данных «%s»" % row["name"])
    rt = dsctx.get_runtime(did)
    if rt is not None and dsctx.busy(rt):
        rt.scan_cancel.set()
        raise DatasetError(409, "В наборе данных «%s» идёт синхронизация или чтение фирм из Logo: дождитесь окончания" % row["name"])
    moved = ""
    d = config.DATA_DIR / row["path"]
    dsctx.forget(did)
    if d.exists():
        dst = config.DATA_DIR / "ds" / "_deleted" / ("%d-%s" % (did, time.strftime("%Y%m%d-%H%M%S")))
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(d), str(dst))
        except OSError as e:
            raise DatasetError(409, "Не удалось перенести файлы набора данных (они заняты другой программой): %s" % e)
        moved = str(dst)
    registry.delete_dataset_row(did)
    return {"ok": True, "moved_to": moved}
