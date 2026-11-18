# Конструктор бухгалтерских отчётов поверх Logo Tiger (MSSQL, ТОЛЬКО SELECT)

Прочитай сначала: `docs/ARCHITECTURE.md` (контракт API/схем и раздел «Изменения» — источник правды), потом нужные скиллы.

## Карта
- `backend/app/` — FastAPI: `main.py` (статика), `api.py` (роутер /api), `routes_forms.py` (формы, шаблоны, связи счетов), `auth.py` (AUTH=1, права по наборам),
  `registry.py` (подключения, наборы, пользователи), `dsctx.py`/`dsmgr.py` (рантайм набора), `logo.py` (чтение MSSQL, только SELECT), `cache.py` (SQLite-кэш),
  `store.py` (настройки, миграции), `engine/` (osv, card, ledger, analysis, chess, journal, form и др.).
- `backend/tests/` — pytest (в открытой копии только тесты, не требующие базы Logo). `frontend/` — React+Vite.
- `data/` — рабочие базы SQLite (не публикуются). `docs/` — контракт и руководство.

## Запуск
- Сервер: `.\run.ps1` (порт 8790; `-CheckOnly` — проверка окружения, `-Build` — пересборка фронта). Подключение к Logo — `config.ini` (образец `config.ini.example`) или переменные `LOGO_*`.
- Тесты: `cd backend; ..\.venv\Scripts\python -m pytest tests -q -p no:cacheprovider`.

## Главные правила
1. Logo — только чтение (ни записи, ни DDL, ни временных таблиц). Деньги внутри — целые копейки.
2. Контракт API/схемы меняется только записью в `docs/ARCHITECTURE.md` → «Изменения».
3. Интерфейс и тексты ошибок — по-русски. Без ORM/очередей/докеров: один процесс.
4. Не коммитить `config.ini`, `data/`, выгрузки с реальными данными (см. `SECURITY.md`).
