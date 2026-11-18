"""Экспорт/импорт настроек набора данных (этап B): GET /api/dataset/export, POST /api/dataset/import."""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from . import dsctx, settings_io

router = APIRouter(prefix="/api")


def _parts(raw: Optional[str]) -> list:
    if not raw:
        return list(settings_io.PARTS)
    out = [x.strip() for x in str(raw).replace(";", ",").split(",") if x.strip()]
    bad = [x for x in out if x not in settings_io.PARTS]
    if bad:
        raise HTTPException(400, "Неизвестная часть настроек «%s»: допустимы %s" % (bad[0], ", ".join(settings_io.PART_RU[p] for p in settings_io.PARTS)))
    return out


@router.get("/dataset/export")
def dataset_export(parts: Optional[str] = None):
    """Файл buhlogo-settings-v1.json: сохранённые отчёты и папки, классификаторы, шаблоны Excel, названия счетов, правила закрывающих ордеров,
    связи счетов, параметры набора. Без паролей, подключений, пользователей, разметки книг и кэша. parts - список через запятую
    (reports, classifiers, names, closing, links, params, templates); по умолчанию - всё."""
    data = settings_io.export_settings(_parts(parts))
    name = quote("buhlogo-settings-v1.json")
    return Response(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"), media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=buhlogo-settings-v1.json; filename*=UTF-8''%s" % name})


@router.post("/dataset/import")
async def dataset_import(request: Request, file: UploadFile = File(...), mode: str = Form("merge"), parts: str = Form("")):
    """multipart: file (json), mode = merge (добавить; одноимённое получает «(2)») | replace (заменить одноимённые), parts - необязательный список
    через запятую. Ответ: {created, updated, skipped, warnings, resync_required, plan_rebuild}."""
    raw = await file.read()
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(400, "Файл настроек больше 200 МБ")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Файл настроек не распознан")
    try:
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(settings_io.import_settings, data, mode, _parts(parts) if parts else None)
    except settings_io.SettingsFileError as e:
        raise HTTPException(400, str(e))
