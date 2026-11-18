# Архитектурный контракт

Обязателен для всех исполнителей. Менять контракт (API, схему, структуру папок) можно только дописав изменение в этот файл в раздел «Изменения» в конце.

## 0. Принципы

- `LOGO_DB` — ТОЛЬКО ЧТЕНИЕ. Ни одной записи, DDL, временных таблиц в ней.
- Перед любым SQL к Logo помнить: только SELECT, даты `YYYYMMDD`, отменённые проводки (`CANCELLED=1`) не читать, открытие = TRCODE 1, листья счетов — по префиксу кода.
- Определения регистров — `.claude/skills/buh-registers/`.
- Деньги внутри приложения — целые копейки (int, `round(x*100)`), наружу в API — число с 2 знаками.
- Интерфейс и все тексты для пользователя — на русском. Код, идентификаторы, комментарии — английский/русский по вкусу.
- Не усложнять: без ORM, без очередей, без докеров. Один процесс.

## 1. Стек и раскладка

```
backend/
  app/
    main.py          FastAPI, монтирует /api и статику frontend/dist
    config.py        env: LOGO_SERVER, LOGO_USER, LOGO_PASSWORD, LOGO_DB, DATA_DIR, PORT(8765), AUTH(0/1)
    logo.py          подключение к MSSQL (только чтение), выполнение SELECT
    cache.py         SQLite-кэш проводок (data/cache.db), синхронизация
    store.py         SQLite настроек (data/settings.db), миграции
    accounts.py      план счетов, дерево, листья
    classifiers.py   классификаторы, разрешение счёт→группа
    engine/          osv.py, card.py, ledger.py, analysis.py, chess.py, journal.py, form.py, common.py
    corr.py          расчёт пар корреспонденции
    export_xlsx.py   выгрузка результата в Excel (openpyxl)
    checks.py        контрольные соотношения
    auth.py          пользователи и роли (этап 8)
  tests/             pytest; сверка с прямым SQL к Logo
  requirements.txt
frontend/            Vite + React + TypeScript; сборка в frontend/dist
data/                cache.db, settings.db, templates/ (создаётся при запуске; не в исходниках)
run.ps1              сборка фронта при необходимости и запуск сервера
```

Python 3.14, Node 25 установлены. Драйвер MSSQL: `pyodbc` (ODBC Driver 17/18 есть); если нет колеса под 3.14 — `mssql-python` или `pymssql`. Dev: Vite проксирует `/api` на `http://localhost:8765`.

## 2. Кэш (data/cache.db) — одноразовый, пересоздаваемый

Все отчёты считаются из кэша, не из Logo напрямую. Это даёт единый движок по всем годам.

```sql
firms(firm INTEGER PK, year INT, name TEXT, period TEXT, enabled INT, divisor INT, -- 5000 для лет <2009, иначе 1
      fingerprint TEXT, synced_at TEXT, line_count INT)
accounts(firm INT, code TEXT, name TEXT, level INT, parent TEXT, root TEXT, is_leaf INT,
         specode TEXT, cyphcode TEXT, center_ctrl INT, -- волна 6: units TEXT, def_name TEXT, full_name TEXT, note TEXT, created TEXT, created_by TEXT,
         --   inactive INT, specode_name TEXT, name_restored INT (name = итоговое название: CLDEF, если продолжает DEFINITION_; def_name = DEFINITION_)
         PRIMARY KEY(firm, code))
specodes(firm INT, specode TEXT, name TEXT, PRIMARY KEY(firm, specode))   -- волна 6: расшифровки меток Logo (SPECODETYPE=35, CODETYPE=1)
lines(firm INT, year INT, id INT, fiche_id INT, fiche_no TEXT, date TEXT /*YYYY-MM-DD*/, trcode INT, line_no INT,
      account TEXT, dt INT, kt INT, usd_dt INT, usd_kt INT, cross_code TEXT, center TEXT,
      text TEXT, fiche_text TEXT, is_opening INT, is_closing INT, PRIMARY KEY(firm, id))
pairs(firm INT, year INT, fiche_id INT, date TEXT, dt_account TEXT, kt_account TEXT, amount INT, usd_amount INT,
      method TEXT /*SEQ|CROSS|PROP*/, disputed INT, is_closing INT)
centers(firm INT, code TEXT, name TEXT)
```

- Реестр фирм по умолчанию: годы отчётности = фирмы из карты «год → фирма» набора, период 01. Остальные фирмы — в реестре с `enabled=0`. Вторые периоды старых фирм (`_02`) — проверить, что там, и описать в отчёте; по умолчанию не грузить, если это данные следующего года.
- Синхронизация фирмы: отпечаток = `COUNT(*), SUM(DEBIT), MAX(LOGICALREF)` по EMFLINE + `MAX(CAPIBLOCK_MODIFIEDDATE), COUNT(*)` по EMFICHE и EMUHACC. Перед расчётом отчёта отпечатки задействованных фирм сверяются (не чаще раза в 60 с на фирму); изменилась — фирма перезагружается целиком в одной транзакции вместе с пересчётом `pairs`. Первая загрузка всех лет — по кнопке/при старте, с прогрессом.
- Исключать `CANCELLED<>0`. `is_opening = (TRCODE=1)`.
- `is_closing` — по правилам из настроек (таблица `closing_rules`, см. §3). Правило по умолчанию подобрать по данным (ордера закрытия 691/690→590→570 и списание 7xx→6xx в конце года) и описать в отчёте, какие ордера 2025 года оно пометило.
- Суммы в кэше — как в Logo (без деления). Деноминация применяется движком при `denominate=true`.
- `pairs`: алгоритм из скилла (A/B = SEQ, C = CROSS, D = PROP); `disputed=1`, если SEQ противоречит заполненному CROSSCODE. Инвариант: по каждой фирме Σ pairs.amount = Σ dt не-açılış строк, и по каждому счёту Σ пар = оборот счёта по каждой стороне.

## 3. Настройки (data/settings.db)

```sql
classifiers(id INTEGER PK, name TEXT, description TEXT, updated_at TEXT)
cls_groups(id INTEGER PK, classifier_id INT, parent_id INT NULL, code TEXT, name TEXT, sort INT)
cls_rules(id INTEGER PK, classifier_id INT, group_id INT, kind TEXT /*mask|range|list|specode*/, value TEXT,
          year_from INT NULL, year_to INT NULL, priority INT DEFAULT 0)
reports(id INTEGER PK, name TEXT, type TEXT, folder TEXT, definition TEXT /*JSON*/, updated_at TEXT)
report_versions(id INTEGER PK, report_id INT, definition TEXT, saved_at TEXT, comment TEXT)
account_names(code TEXT, year_from INT NULL, year_to INT NULL, name TEXT)   -- переопределение названий
closing_rules(id INTEGER PK, kind TEXT /*text|accounts*/, value TEXT, enabled INT)
templates(id INTEGER PK, name TEXT, filename TEXT, uploaded_at TEXT)        -- Excel-шаблоны бланков
pair_overrides(firm INT, fiche_id INT, payload TEXT)                        -- ручное закрепление корреспонденции
users(id INTEGER PK, login TEXT UNIQUE, pass_hash TEXT, role TEXT /*viewer|editor|admin*/)
```

Правила классификатора: `mask` — `329.01*` (звёздочка только в конце; без звёздочки — точный код и все его потомки), `range` — `150..157` по корню/префиксу включительно, `list` — коды через запятую, `specode` — значение метки Logo (точное) для данного года. Разрешение для (год, счёт-лист): берутся правила, действующие в году; побеждает больший `priority`, при равенстве — более длинное (специфичное) совпадение; если остаётся >1 группы — конфликт (возвращается список). Нет совпадений — «не сопоставлено».

## 4. Описание отчёта (definition JSON)

```jsonc
{
  "type": "osv",               // osv | card | ledger | analysis | chess | journal | form | carry_gaps
  "title": "ОСВ",
  "period": {"date_from": "2026-01-01", "date_to": "2026-09-30"},   // может пересекать границы лет
  "currency": "TMT",           // TMT | USD
  "scale": 1,                  // 1 | 1000 | 1000000
  "denominate": true,          // делить суммы лет <2009 на 5000
  "include_closing": true,     // false — исключить закрывающие ордера из оборотов
  "filters": {"accounts": ["1*", "320.01*"], "groups": [{"classifier": 1, "group": 12}],
              "centers": [], "trcodes": [], "text": ""},
  "rows": [                    // иерархия строк сверху вниз
    {"kind": "classifier", "id": 1, "depth": 2},
    {"kind": "account", "max_level": 2}
  ],
  "columns": {"balance": "expanded", "by_month": false, "show_usd": false},  // net | expanded
  "options": {"hide_zero": true, "sort": {"by": "code", "dir": "asc"}, "unmapped": true},
  // по типам:
  "account": "100.01*",        // card, ledger, analysis, journal — счёт/маска/группа
  "chess": {"rows": {"kind": "account", "level": 0}, "cols": {"kind": "account", "level": 0}},
  "journal": {"side": "credit"},
  "form": {"lines": [{"code": "010", "name": "Денежные средства", "formula": "SKD(100*)+SKD(102*)", "cell": "D12", "bold": false}],
           "template_id": null, "columns": ["begin", "end"]}
}
```

Семантика:
- Сальдо на дату D = açılış года(D) + обороты года(D) до D. Обороты периода = строки с `is_opening=0` в периоде (и `is_closing=0`, если `include_closing=false`). Если период пересекает год и «начальное + обороты ≠ конечное» из-за ручных правок переноса — в `warnings` и отдельный отчёт `carry_gaps`.
- `balance: "net"` — свёрнутое сальдо в каждом узле; `"expanded"` — узел = сумма дебетовых и сумма кредитовых сальдо листьев.
- Формулы форм: `SND(sel) SNK(sel) SKD(sel) SKK(sel)` — сальдо нач./кон. дебетовое/кредитовое (развёрнуто по листьям); `SN(sel) SK(sel)` — свёрнутое (Дт−Кт); `OBD(sel) OBK(sel)` — обороты; `OBDK(selДт, selКт)` — оборот в корреспонденции; `sel` — маска `100*`, диапазон `150..157`, группа `@1:76` (классификатор:код группы), список через `|`; ссылки на строки `[010]`; `+ - * / ( )`, числа. Разбор — собственный безопасный парсер, без `eval`.

## 5. API

Все ответы JSON. Ошибки: `{"detail": "текст по-русски"}`.

```
GET  /api/status                       → {logo_ok, firms_synced, firms_total, syncing, progress}
GET  /api/firms                        → [{firm, year, name, enabled, synced_at, line_count}]
PUT  /api/firms/{firm}                 {enabled}
POST /api/sync                         {firms?: [int], force?: bool} → запускает фоном; прогресс в /api/status
GET  /api/accounts?year=2026           → [{code, name, level, parent, root, is_leaf, specode, cyphcode, center_ctrl, units, is_currency, full_name, note, created, created_by, inactive, specode_name}] (поля — «Изменения», волна 6)
GET  /api/centers?year=2026

GET  /api/classifiers                  → [{id, name, description, groups_count, rules_count}]
POST /api/classifiers                  {name, description}
GET  /api/classifiers/{id}             → {id, name, description, groups: [...], rules: [...]}
PUT  /api/classifiers/{id}             полная замена документа (группы и правила)
DELETE /api/classifiers/{id}
POST /api/classifiers/import-specode   {year, name} → создаёт классификатор из меток Logo
POST /api/classifiers/{id}/copy
GET  /api/classifiers/{id}/resolve?year=2026 → {mapped: [{account, name, group_id}], unmapped: [{account, name, turnover}], conflicts: [{account, groups: []}]}

GET  /api/reports                      → [{id, name, type, folder, updated_at}]
POST /api/reports                      {name, type, folder, definition}
GET  /api/reports/{id}
PUT  /api/reports/{id}                 сохраняет и пишет версию
DELETE /api/reports/{id}
GET  /api/reports/{id}/versions ;  POST /api/reports/{id}/copy

POST /api/run                          {definition} → Result
POST /api/export/xlsx                  {definition} → файл .xlsx
POST /api/drill                        {period, accounts?: [маски], group?, side?: "dt"|"kt", corr?: [маски], include_closing, limit, offset}
                                       → {total, sum_dt, sum_kt, lines: [{firm, fiche_id, fiche_no, date, account, account_name, corr, dt, kt, text, method}]}
GET  /api/fiche?firm=1&id=138        → {fiche_no, date, trcode, text, lines: [...], pairs: [...]}
GET  /api/corr/stats?year=             → доля оборота по методам, число спорных
GET  /api/corr/disputed?year=          → список спорных ордеров
GET/PUT /api/closing-rules ; GET/PUT /api/account-names
POST /api/templates (multipart) ; GET /api/templates
POST /api/login {login, password} ; POST /api/logout ; GET /api/me     (при AUTH=0 — всегда admin)
```

Result:
```jsonc
{
  "title": "...", "subtitle": "период, валюта, единицы",
  "columns": [{"key": "open_dt", "title": "Дебет", "group": "Сальдо на начало", "type": "money"}],  // type: text|money|int
  "rows": [{"id": "a:100", "parent": null, "level": 0, "kind": "group|account|total|line|unmapped",
            "code": "100", "name": "KASA HESABI", "values": {"open_dt": 123.45}, "bold": false,
            "drill": {"accounts": ["100*"]}}],
  "totals": {"open_dt": 0.0},
  "checks": [{"name": "Оборот Дт = оборот Кт", "ok": true, "detail": "1 234 567,89"}],
  "warnings": ["..."]
}
```
Строки идут в порядке показа (обход дерева в глубину), `parent` — id родителя.

## 6. Интерфейс

Разделы: **Отчёты** (сохранённые, по папкам; «Новый отчёт» с выбором типа) · **Классификаторы** · **План счетов** · **Корреспонденция** (статистика, спорные ордера) · **Настройки** (фирмы и синхронизация, закрывающие ордера, названия счетов, шаблоны, пользователи).

Экран отчёта: слева сворачиваемая панель параметров (период с быстрыми кнопками месяц/квартал/год, годы, фильтры, строки, графы, опции), справа таблица-дерево: раскрытие/свёртывание узлов и «раскрыть до уровня N», двухъярусная шапка, закреплённые шапка и первые графы, числа с разделителями разрядов и выравниванием вправо, отрицательные красным, пустое вместо нуля. Под таблицей — полоса контролей (зелёная/красная). Клик по числу — расшифровка (проводки), клик по проводке — ордер целиком с парами корреспонденции. Кнопки: Сформировать, Сохранить, Сохранить как, Excel, Печать (печатная вёрстка `@media print`: альбомная, шапка повторяется).

Таблицу писать свою (HTML table + sticky), без тяжёлых грид-библиотек; до ~3000 строк рендерить без виртуализации с учётом свёрнутых узлов.

## Изменения

### Предложения от интерфейса (волна 1) — к согласованию с бэкенд-исполнителем

1. **Ключи граф Result для расшифровки.** Интерфейс считает графу «сальдо» (не расшифровывается проводками), если `key` начинается с `open|close|beg|end|bal|saldo|sn|sk`; остальные денежные графы строки с `drill` — обороты. Сторона определяется по суффиксу `_dt` / `_kt`. Для графы «по месяцам» ключ должен содержать `YYYY-MM` (например `turn_dt_2026-03`) — тогда расшифровка берёт период этого месяца. Рекомендуемые ключи ОСВ: `open_dt, open_kt, turn_dt, turn_kt, close_dt, close_kt`. Либо добавить в `columns[]` явные поля `drill: "dt"|"kt"|null` и `period` — интерфейс перейдёт на них.
2. **`PUT /api/classifiers/{id}`**: новые группы и правила интерфейс создаёт с **отрицательными временными `id`** (`-1, -2, …`), `parent_id`/`group_id` ссылаются на них же. Сервер должен при замене документа выдать настоящие id и пересвязать ссылки. После сохранения интерфейс перечитывает документ через `GET`.
3. **`/classifiers/{id}/resolve`**: `conflicts[].groups` допустим как список id или объектов `{id, code, name}`; `unmapped[].turnover` — оборот счёта за год (сумма Дт+Кт).
4. **Быстрое отнесение счёта в группу** создаёт правило `mask` с точным кодом счёта (без `*`).
5. **`/api/status.progress`**: интерфейс понимает число (0..1 или 0..100) либо `{done, total}`.
6. **`/api/drill`**: интерфейс передаёт `group` как есть из `row.drill.group`; в `row.drill` допустимы поля `accounts`, `group`, `side`, `corr`, `period`.
7. **Закрывающие правила** (`/api/closing-rules`): `kind=text` — подстрока в тексте ордера, `kind=accounts` — значение вида `691->590, 7*->6*` (формат записи правила согласовать; интерфейс показывает как есть).
8. **Порт 8765** на машине разработчика уже был занят другим приложением — при запуске сервера этого проекта это может мешать.

### Изменения бэкенда (волна 1)

- **Порт 8790** вместо 8765 (на 8765 отвечает чужое приложение). Прописан в `backend/app/config.py` (PORT), `run.ps1`, `frontend/vite.config.ts` (proxy).
- Добавлены файлы: `backend/app/api.py` (роутер /api), `engine/carry_gaps.py`, `engine/drill.py` (drill + fiche), `corr.py` (заглушка — точка вызова `rebuild_firm(conn, firm, year)` в `cache.sync_firm`, внутри транзакции фирмы).
- **Ключи граф ОСВ**: `open_dt, open_kt, turn_dt, turn_kt, close_dt, close_kt`; по месяцам — `turn_dt_YYYY-MM` / `turn_kt_YYYY-MM`; USD-графы (`show_usd`) — те же ключи с суффиксом `_usd`. В `columns[]` добавлены поля `drill` ("dt"|"kt"|null; у USD-граф null) и `period` (для месячных граф {date_from,date_to}). Колонок «код/наименование» в `columns` нет — они в `row.code/name`. Последняя строка `rows` — `kind:"total"` (дублирует `totals`).
- **`row.drill`**: `{accounts:[маска]}` и/или `{group:{classifier,group}}` (или `groups:[...]`, если в строках несколько классификаторов). `group.group = null` — «не сопоставлено», `-1` — конфликт правил. `/api/drill` принимает `group` (объект) или `groups` (список; объединение), `accounts`, `side`, `corr` (в волне 1 — маски по CROSSCODE строки; пары корреспонденции — волна 2), `include_closing`, `limit`, `offset`; доп. необязательные: `currency`, `denominate`, `centers`, `trcodes`, `text`, `mode` ("turn" по умолчанию | "open" — проводки, формирующие входящее сальдо | "close" — исходящее). Суммы строк — в манатах после деноминации (`denominate=true` по умолчанию).
- **Семантика сальдо**: вход на d1 = açılış года d1 + проводки года до d1; остаток на d2 = açılış года d2 + проводки года до d2 (не «вход + обороты»). При переходе через год расхождения (перенос с правками) — в `warnings` и check «Нач. + обороты = кон.» = false; подробности — `carry_gaps`. Счета разных лет сводятся по коду.
- `include_closing=false` исключает закрывающие ордера **везде**: из входящего, оборотов и остатка (чтобы нач.+обороты=кон. сохранялось). Фильтры по центрам/типам ордеров/тексту применяются к проводкам не-açılış; açılış не фильтруется (кроме `trcodes` без 1).
- `balance` по умолчанию `net`. `Итого` = сумма отображаемых значений корневых строк. Checks «Дт=Кт», «Σ нетто=0» выдаются только без фильтров; для USD (REPORTNET не балансируется) — только оборот/«нач+обороты».
- Правило закрывающих ордеров по умолчанию: `accounts` = `570*,590*,690*,691*,692*`; **ордер закрывающий, если ВСЕ его строки лежат на этих счетах** (поиск по слову в тексте ордера не годится: налоговые ордера могут содержать то же слово, но иметь счета 36x и закрывающими не являются). Формат `kind=accounts`: маски через запятую (не «691->590»); `kind=text`: подстрока без регистра в тексте ордера/строки. `PUT /api/closing-rules` (массив) пересчитывает `is_closing` во всём кэше.
- Классификатор: `PUT` принимает отрицательные/строковые временные id (заменяет настоящими, ссылки `parent_id`/`group_id` переписывает), id существующих групп/правил сохраняет. `resolve`: `conflicts[].groups` — список id; `unmapped[].turnover` = Дт+Кт не-açılış за год (в манатах); списки — только счета с проводками в году (включая açılış). Импорт SPECODE: группа на значение метки, родитель по «/» («76/1» → «76»), название «значение · название самого верхнего счёта с этой меткой», правило `specode` с year_from=year_to=год. Конфликтные счета в ОСВ показываются строкой «Конфликт правил», не задваиваются.
- Приоритет правил: больший `priority`, затем более длинное совпадение (длина маски/диапазона/значения метки).
- `/api/status.progress` = `{done,total,current,message,started_at,finished_at,errors}`. `POST /api/sync` при уже идущей синхронизации — 409.
- Отпечаток фирмы: `COUNT(*)`, Σ`ROUND(DEBIT*100)` (целое — детерминировано, float-SUM в MSSQL нестабилен), `MAX(LOGICALREF)`, число неотменённых + даты/счётчики EMFICHE/EMUHACC.
- Добавлены заглушки `GET /api/me` (admin), `/api/corr/stats` и `/api/corr/disputed` (пусто до волны 2). Не реализованы: `/api/templates`, `/api/login`, `/api/logout`.
- Вторые периоды старых фирм (`LG_<FFF>_02`) содержат только перенесённое открытие следующего года (черновик/дубль входящих), **не загружаются**.

**Ответ бэкенда на «Предложения от интерфейса»:** п.1 принят (ключи и поля `drill`/`period` в columns — см. выше); п.2 реализован; п.3 — конфликты списком id; п.4 принят (mask с точным кодом = код и потомки); п.5 принят (объект); п.6 принят; п.7 — формат `accounts` другой: маски через запятую, правило срабатывает на ордер целиком, если все его строки на этих счетах (запись вида `691->590` не поддерживается); п.8 — порт изменён на 8790.

### Предложения от интерфейса (волна 2) — допущения интерфейса, сервер сверяет и отвечает здесь

Интерфейс шлёт в `definition` (поверх общих полей §4) такие ключи; если сервер ждёт иное — ответить в этом разделе, интерфейс подстроится.
1. **card** — `account` (строка: маски через запятую, например `100.01*, 102*`), `card: {detail: "entries"|"day"|"month"}` (проводки / итоги по дням / итоги по месяцам; по умолчанию `entries`). Группа — через `filters.groups`.
2. **analysis** — `account`; `analysis: {corr: {kind:"account", level:0..4} | {kind:"classifier", id, depth}}` — детализация корреспондентов; `columns.by_month` — раскладка по месяцам.
3. **ledger** — `account` (одна или несколько масок через запятую); `columns.by_month` игнорируется (главная книга всегда по месяцам).
4. **chess** — `chess: {rows: A, cols: A}`, где `A = {kind:"account", level:0..4} | {kind:"classifier", id, depth}` (как в §4, плюс classifier).
5. **journal** — `account`; `journal: {side:"credit"|"debit", rows:"day"|"fiche"}` (строки по дням или по ордерам).
6. **form** — `form: {lines:[{code, name, formula, cell?, bold?, indent?:0..4}], template_id, columns:["begin"|"end"|"turn"|"prev_year", ...], checks:[{name, left, right}]}`; `left/right` — формулы со ссылками на строки `[010]`; результат проверки — в `Result.checks`.
7. **compare_years** — `compare: {years:[2024,2025,2026], metric:"turn_dt"|"turn_kt"|"open"|"close"|"net_turn"}`; строки — `rows` как у osv; графы — по одному на год + «изменение, %» (если сервер даёт).
8. `POST /api/forms/validate {formula, codes?: [строковые коды строк формы]}` → `{ok: bool, error?: "текст", pos?: int}` (интерфейс также принимает `{detail}` с 4xx как ошибку формулы).
9. `GET /api/templates` → `[{id,name,filename,uploaded_at}]`; `POST /api/templates` multipart, поля `file` и `name`; `DELETE /api/templates/{id}`.
10. `GET /api/account-links` → `[{id?, year_from, code_from, year_to, code_to, kind?}]`; `POST /api/account-links/auto {year_from, year_to}` → предложение автоподбора (список связей, не сохраняет); `PUT /api/account-links` — полная замена списка. Уточнить у 2B.
11. `GET /api/users` → `[{id,login,role}]`; `POST /api/users {login,password,role}`; `PUT /api/users/{id}`; `DELETE /api/users/{id}`; `POST /api/login {login,password}` → `{login,role}`; `GET /api/me` → 401 при AUTH=1 без сессии.
12. `POST /api/demo/seed` → `{created: n, reports: [...]}` — создаёт образцы отчётов и классификатор.
13. `GET /api/corr/disputed?year=` → `[{firm, fiche_id, fiche_no, date, text, amount, dt_accounts?, kt_accounts?}]`; `PUT /api/corr/override {firm, fiche_id, pairs:[{dt_account,kt_account,amount}]}` — ручное закрепление пар; `DELETE /api/corr/override?firm=&fiche_id=`.


### Изменения бэкенда (волна 2A: корреспонденция и регистры на парах)

**Схема `pairs` (кэш)** — пары теперь на уровне проводок. Добавлены колонки `dt_line`, `kt_line` (id строк `lines.id` Дт и Кт), `usd_kt_amount`; `usd_amount` — USD-доля со стороны Дт, `usd_kt_amount` — со стороны Кт (REPORTNET Дт и Кт не балансируются, поэтому у пары две USD-суммы). Индексы: `(firm,fiche_id)`, `(firm,date)`, `(firm,dt_account,date)`, `(firm,kt_account,date)`, `(firm,dt_line)`, `(firm,kt_line)`. `cache.init_db()` пересоздаёт пустую `pairs` старой схемы. Методы: `SEQ | CROSS | PROP | MANUAL` (MANUAL — ручное закрепление). Инварианты (тесты по всем загруженным фирмам): Σ пар = Σ Дт не-açılış; по каждому счёту Σ пар по стороне = оборот счёта по стороне; по каждой проводке Σ её пар = сумма проводки; Σ `usd_amount` = Σ `usd_dt`, Σ `usd_kt_amount` = Σ `usd_kt`.

**Алгоритм (`corr.py`)**: ордер режется на подгруппы по нулю нарастающего Σ(Дт−Кт) в порядке (`line_no`,`id`); односторонние подгруппы (сторно одной стороны) склеиваются с соседом. 1:1/1:N/N:1 — SEQ. N:M — взаимные группы CROSSCODE (Дт-строка (A, cross B) ↔ Кт-строка (B, cross A); группа целиком при равных суммах, иначе по равным суммам строк), остаток 1:N/N:1 — однозначно (CROSS), остаток N:M — PROP (d_i·c_j/T, целочисленное 2D-округление, суммы строк и столбцов точны; при отрицательной копейке — «северо-западный угол»). **Спорный** ордер: SEQ-пара противоречит заполненному CROSSCODE (у 1:1 — любая из двух сторон; у 1:N/N:1 — у строк множественной стороны). В спорном ордере приоритет у CROSSCODE: если взаимные группы целиком покрывают ордер и сбалансированы — ордер целиком берётся из них (CROSS, все пары `disputed=1`); иначе остаётся SEQ, а `disputed=1` получают только пары спорных подгрупп. `rebuild_firm` вызывается в транзакции `sync_firm`; `corr.rebuild_all()` пересчитывает пары всех фирм из уже загруженных проводок без обращения к Logo (десятки секунд на все годы); `POST /api/corr/rebuild {firms?}` — то же по HTTP.

**Ручное закрепление**: `PUT /api/corr/override {firm, fiche_id, pairs:[{dt_account, kt_account, amount}], note?}` — суммы в манатах; по каждому счёту и стороне Σ должна равняться обороту счёта в ордере (иначе 400 с пояснением); пары ордера пересчитываются (method=MANUAL, суммы проводок распределяются по строкам счёта по порядку), закрепление хранится в `settings.db.pair_overrides` (payload `{"pairs": [...], "note", "saved_at"}`) и переживает пересинхронизацию. `GET /api/corr/override?firm&fiche_id` — прочитать, `DELETE` — снять (ордер пересчитывается автоматически). Не поддерживается для лет до деноминации (2009) и для ордеров со сторно (отрицательными суммами). `GET /api/fiche` отдаёт `pairs[]` с `method`, `disputed`, `dt_line`, `kt_line`, `usd_amount` и флаг `manual`.

**`/api/corr/stats?year=`** → `{total, methods:{SEQ|CROSS|PROP|MANUAL: {amount, share, count}}, disputed (число ордеров), fiches, years:[{year, firm, total, methods, disputed, disputed_amount, fiches}], note}`; без `year` — все годы. **`/api/corr/disputed?year=&limit=`** → `[{firm, year, fiche_id, fiche_no, date, text, amount (спорная сумма), pairs, methods, resolved_by (CROSS|SEQ)}]`, по убыванию суммы.

**`/api/drill`**: основной режим (`mode=turn`) идёт по парам: строка на каждую пару проводки; `corr` — счёт-партнёр по паре (а не CROSSCODE строки), `method` — метод пары, `disputed`; суммы строки — суммы пары. Новые параметры: `corr` (маски партнёра; диапазоны `a..b` не поддерживаются), `corr_groups` / `corr_group` (`{classifier, group}` — партнёр в группе), `method` (строка или список), `disputed: true`. Режимы `open`/`close` — по-прежнему строки (corr = CROSSCODE). Если у фирмы нет пар — деградация на CROSSCODE.

**Типы отчётов `/api/run` и `/api/export/xlsx`**: `card`, `analysis`, `ledger`, `chess`, `journal` (движки `engine/card.py`, `analysis.py`, `ledger.py`, `chess.py`, `journal.py`; общее — `engine/pairs.py`; `engine/common.py` не менялся). Поля definition:
- `account` — счёт: маска (`100.01*`; `120.01` = код и потомки), несколько через запятую, `{classifier, group}` или список таких; для card/analysis/journal обязателен (или фильтры по счетам); для ledger пуст ⇒ раздел на каждый корневой счёт отфильтрованных. `filters.accounts/groups/centers/trcodes/text`, `currency`, `scale`, `denominate`, `include_closing` работают везде (для пар: `trcodes` — по ордеру, `centers`/`text` — «любая из двух проводок»; `include_closing=false` ⇒ `pairs.is_closing=0`, входящее/остаток — как в ОСВ).
- `card.detail`: `entries | day | month` (day/month добавляют строки итогов `kind=total`, id `sub:<дата|месяц>`); `card.lines=false` — только итоги. Строка: `code`=дата, `name`=текст; колонки `fiche_no, account, corr, method` (text), `dt, kt`, `bal_dt, bal_kt` (сальдо нарастающим итогом). Первая строка (id `open`) — «Входящее сальдо». Если у проводки несколько корреспондентов — строка на каждую пару (id `l<firm>:<id>:<n>`). При переходе через год и несовпадении açılış с остатком — строка «Корректировка переноса остатков». Строка карточки несёт `fiche:{firm,fiche_id}` для открытия ордера.
- `analysis.corr` — ось корреспондентов `{kind:account, level}` (по умолчанию 1) или `{kind:classifier, id, depth}`; `columns.by_month`; результат — дерево: счёт (сальдо нач./кон., обороты) → «по дебету — в корреспонденции с кредитом счетов» → корреспонденты; «по кредиту — с дебетом счетов» → корреспонденты. Ключи граф — как у ОСВ (`open_dt, open_kt, turn_dt, turn_kt, close_dt, close_kt`, месяцы `turn_dt_YYYY-MM`).
- `ledger.corr` — ось граф (по умолчанию уровень 0), `options.max_graphs` (40) — остальное в «Прочие». Колонки: `turn_dt` (итого Дт), `dtc:<ключ>` (с кредита счёта-ключа; `drill:"dt"`, `corr`), `turn_kt`, `bal_dt`, `bal_kt` (сальдо свёрнутое на конец месяца). Строки раздела: заголовок (`kind=account`), «Входящее сальдо», месяцы (`code`=YYYY-MM), «Итого за период» (`kind=total`).
- `chess.rows` / `chess.cols` — независимые оси `{kind:account, level:0..4}` | `{kind:classifier, id, depth}`; пустые строки/графы скрыты; колонки `c:<ключ>` + `total_dt`; последняя строка «Итого Кт»; `row.calc` — ключи граф, где в ячейке есть расчётные (PROP) пары. `filters.accounts/groups` в шахматке дают подматрицу (обе стороны пары в выбранных счетах), сверка с ОСВ при этом не выполняется. В USD ячейки — USD по стороне Дт.
- `journal.side` `credit | debit`, `journal.rows` `day | fiche`, `journal.corr` (ось граф, уровень 0), `options.max_graphs` (60). Колонки: `turn_kt` (credit) / `turn_dt` (debit) и `dtc:<ключ>` / `ktc:<ключ>`; строка fiche несёт `fiche:{firm,fiche_id}`.
- **Новые поля `columns[]` Result**: для граф корреспонденции — `corr: [маска]` или `corr_groups: [{classifier, group}]` (подставляются в `/api/drill` вместе с `row.drill`: `accounts/group`, `period`, а `side` = `columns[].drill`) и `name` (название ключа графы). Клиент, у которого расшифровка ячейки берёт `corr` только из `row.drill`, должен дополнять его `column.corr`/`column.corr_groups`. У строк `analysis` корреспондент уже лежит в `row.drill.corr`/`corr_groups`.
- Контроли (`checks`): сверка с ОСВ тем же путём, что и ОСВ (листья `aggregate_leaves`): Σ по корр. счетам = оборот по стороне, итоги строк/граф шахматки = обороты ОСВ по каждой строке/графе, кон. сальдо карточки/главной книги = исходящее ОСВ, итог журнала = оборот стороны; плюс информационный контроль «Доля методов корреспонденции». Excel: широкие матрицы (>14 граф) — узкие графы и высокая шапка; закрепление шапки и первых двух колонок — как раньше.

**Тесты**: `test_corr.py` (инварианты по всем фирмам, сверка матрицы пар 2026 с эталонным SQL скилла, ручное закрепление), `test_registers.py` (регистры против ОСВ и прямого SQL: 100*, 102*, 120.01*, 320*, 7* за 2026 и те же маски за другой год), `test_filters.py` (группы, центры, текст, типы ордеров, drill, пересинхронизация по отпечатку).

### Изменения бэкенда (волна 2B: формы по строкам, шаблоны Excel, связи счетов, сравнение лет, авторизация)

**Файлы:** `engine/form.py` (парсер и расчёт форм), `multiyear.py` (связи, автоподбор, `CodeMapper`, отчёт `compare_years`), `auth.py`, `routes_forms.py` (маршруты форм/шаблонов/связей/демо), миграция №2 в `store.py` (`account_links`), тесты `test_forms_parser.py`, `test_forms.py`, `test_templates.py`, `test_multiyear.py`, `test_auth.py`, `helpers_2b.py`. В `main.py` добавлены строки: `AuthMiddleware`, роутеры `auth` и `routes_forms` **перед** основным `api.router` — они перехватывают `POST /api/run` и `POST /api/export/xlsx` для типов `form` и `compare_years` (остальные типы делегируются в `api.run_definition`/`api.export_xlsx` без изменений), а также `/api/me`, `/api/login`, `/api/logout`.

**1. Формы (`type: "form"`).** `definition.form`:
```jsonc
{"lines": [{"code": "010", "name": "...", "formula": "SK(100*|102*)", "indent": 0, "bold": false,   // formula пусто → заголовок (kind:"group")
            "cell": "D12", "cells": {"begin": "C12", "end": "D12"}}],                             // для шаблона Excel
 "columns": ["begin", "end", "period", "prev", "prev_begin"],   // или объекты {"kind":"end","title":"На 31.12","key":"end"}; алиасы интерфейса: turn=period, prev_year=prev
 "checks": [{"name": "Актив = Пассив", "left": "[300]", "right": "[600]", "tolerance": 0.005}],   // tolerance в манатах (по умолчанию 0,0051)
 "coverage": ["1*", "2*"],        // контроль: счета этой области с данными, не вошедшие ни в одну строку формы → красный контроль + предупреждение
 "template_id": null, "template_sheet": null, "meta_cells": {"title": "A1", "subtitle": "A2"}}
```
- **Функции** (аргумент — селектор; регистр не важен; есть русские синонимы СНД СНК СКД СКК СН СК ОБД ОБК ОБДК): `SND/SNK` — сальдо на начало дебет/кредит, `SKD/SKK` — то же на конец (развёрнуто по листьям: сумма положительных/отрицательных остатков листьев), `SN/SK` — свёрнутое (Дт−Кт) на начало/конец, `OBD/OBK` — обороты, `OBDK(selДт, selКт)` — оборот в корреспонденции по таблице `pairs` (если для года пусто — понятная ошибка 400, не нули).
- **Селектор:** `100*` (префикс), `100.01` (код и потомки), `150..157` (диапазон по корням), `@1:76` (группа классификатора 1 с кодом «76» и её потомки), `|` — объединение, **`~` — исключение** (всё после `~` исключается): `1* ~ 150*|153*`.
- **Выражение:** `+ - * /`, скобки, унарный минус, числа (константы в манатах), ссылки на строки `[010]` (в т.ч. вперёд; циклы запрещены). Без `eval`. Деление на 0 → значение `null` в ячейке и предупреждение; ссылка на `null` тоже `null`.
- **Графы:** `end`/`period` — формула как записана за период отчёта (`period.date_from..date_to`); `begin` — состояние на начало периода (все `SK*`/`SKD`/`SKK` читаются как на начало, обороты = 0); `prev` — как `end`, но период сдвинут на год назад; `prev_begin` — как `begin` для прошлого года. Нет данных за прошлый год → `null` + предупреждение. Колонки Result: `{key, title (с датами), type:"money", drill:null, period:{date_from,date_to}}`. Результат — как в §5: строки `kind: "group"` (заголовки, без значений) и `kind: "line"`, `parent` по `indent`, `drill: {accounts:[...]}` — маски формулы (ссылки раскрываются; при исключении/группе — явный список счетов, вошедших в формулу, до 200).
- Ошибки формул → 400 с текстом по-русски и позицией «(позиция N)»; `options.hide_zero` скрывает строки с нулями; `include_closing`, `currency`, `scale`, `denominate`, `filters` — как в ОСВ.
- **`POST /api/forms/validate`** — без расчёта. `{formula, codes?}` → `{ok, error?, pos?, errors:[{line, field, pos, message}], refs, selectors}` (`pos` с 0; интерфейс может читать `error`/`pos` первой ошибки). `{form:{lines,checks?,coverage?}, year?}` → то же + по строкам `lines[{code, ok, refs, selectors:[{text, accounts}]}]`, при `year` — число листовых счетов плана года на селектор и `warnings` для нулевых. `GET /api/forms/help` — список функций/синтаксиса для подсказок.
- Демо-формы проверены на реальных данных: актив баланса-примера равен пассиву с точностью до небаланса начальных остатков в самом Logo (его же показывает ОСВ).

**2. Шаблоны Excel.** `POST /api/templates` (multipart `file`=.xlsx, `name`) → `{id,name,filename,uploaded_at,sheets}`; `GET /api/templates`; `GET /api/templates/{id}/download`; `DELETE /api/templates/{id}`. Файлы — `data/templates/{id}.xlsx`. Экспорт формы с `form.template_id` (`POST /api/export/xlsx`): значения записываются в ячейки копии шаблона (`line.cell` — графа `end`, иначе `period`, иначе первая; `line.cells` — `{графа: адрес}`; адрес `D12` или `Лист!D12` / `'Лист 1'!D12`), форматирование шаблона сохраняется (числовой формат меняется только у ячеек с форматом General), контроли и предупреждения в бланк не пишутся. Ошибки адреса/листа/графы/объединённой ячейки — 400, нет шаблона — 404.

**3. Связи счетов между годами** (`account_links(code_from, year_from, code_to, year_to, method, confidence, note)`, уникальность по (code_from, year_from, year_to); ручные `method="manual"`, автоподбор `auto:<способ>`).
- `GET /api/account-links?year_from&year_to&q&limit&offset` → плоский список `[{id, year_from, code_from, year_to, code_to, method, kind(=method), confidence, note}]`, общее число — заголовок `X-Total-Count`; `POST /api/account-links` (объект или список; ручные; проверяет коды по планам); `PUT /api/account-links/{id}`; `DELETE /api/account-links/{id}`; `DELETE /api/account-links?year_from&year_to[&method]`; `PUT /api/account-links` — полная замена связей затронутых пар лет (или пары `?year_from&year_to`).
- `POST /api/account-links/auto {year_from, year_to, min_confidence=0.7, list_limit=300}` — **ничего не пишет**: `{summary:{accounts_from/to, codes_in_both, codes_differ, all|leaves|leaves_with_turnover:{total, matched, disputed, unmatched, *_turnover_share}, by_method}, disputed:[{code_from, name_from, candidates:[{code,name,score}], reason, turnover}], unmatched:[...], matched_sample, links:[{year_from, code_from, year_to, code_to, kind, confidence}]}`. `POST /api/account-links/auto/apply` (те же параметры + `replace`) записывает уверенные связи, ручные не трогает, возвращает отчёт + `applied`. Способы: `code+name` (код и название совпали), `name` (название; приоритет тому же корню/уровню/родителю), `code+similar`, `name~` (похожее название в том же корне); спорные — несколько кандидатов или код совпал при другом названии.
- **Интерфейс для движков (исполнитель 2A):** `from app.multiyear import CodeMapper, map_code`; `m = CodeMapper(target_year)` (один раз на отчёт) → `m.map(code, year) -> str` (код в плане `target_year`; `year == target_year` → тот же код) и `m.map_ex(code, year) -> (код, how)`, `how ∈ same|link|chain|identity` (`identity` — связей нет, код считается неизменным: можно предупредить). Цепочка через соседние годы по связям (в обе стороны), прямая связь приоритетнее. Для периода через годы: `target = последний год периода`, каждый лист `(year, code)` приводить `m.map(code, year)` до группировки; без CodeMapper движки продолжают сводить «по коду» (волна 1).

**4. Сравнение лет (`type: "compare_years"`).** `definition`: `years: [2007, 2008]` (2..10), `measures: ["turn_dt","turn_kt","close"]` (допустимо `open, turn_dt, turn_kt, net_turn, close, close_dt, close_kt`; `close` — Дт−Кт со знаком), `mmdd_from/mmdd_to` (часть года, по умолчанию `01-01`..`12-31`), `target_year` (к плану какого года приводятся счета, по умолчанию последний), `use_links` (true), `delta` (true добавляет «Изменение, %» первого года к последнему); плюс `rows`, `filters`, `columns.balance`, `options`, `currency`, `scale`, `denominate` как в ОСВ. Форма `compare: {years, metric|measures, delta}` (как просил интерфейс) тоже принимается (`metric: net_turn` = оборот Дт − Кт; `delta` по умолчанию true). Колонки `y{год}_{показатель}` с `group` = год, `period` = даты года; `drill` у граф только для `target_year` (счета в строках — коды целевого года). Деноминация — по каждому году отдельно. Контроли по годам: Дт=Кт, Σ сальдо=0, «все счета размещены в строках» (ничего не потеряно при приведении), «родитель = сумма детей». Счета без связи и с совпавшим кодом сводятся по коду (предупреждение, если названия различаются), без пары в целевом плане — отдельными строками (предупреждение).

**5. Авторизация.** `AUTH=1` (по умолчанию 0 → все admin, `/api/me` = `{login:"admin", role:"admin", auth:false}`). Первый admin — из `ADMIN_LOGIN`/`ADMIN_PASSWORD` при первом обращении, если пользователей нет. `POST /api/login {login,password}` → `{login, role, auth}` + httpOnly-cookie `session` (12 ч, SameSite=Lax, подпись HMAC; ключ — `SECRET_KEY` или `data/.secret`); `POST /api/logout`; `GET /api/me` (401 без сессии); `GET/POST /api/users`, `PUT/DELETE /api/users/{id}` (только admin; нельзя удалить себя/последнего admin). PBKDF2-SHA256, 200 000 итераций. Права проверяет `AuthMiddleware` по таблице `auth.required_role(method, path)` (покрывает и будущие маршруты; ответы 401 «Требуется вход в систему» / 403 «Недостаточно прав…»): **viewer** — все GET (кроме `/api/users`) и POST `run`, `export/xlsx`, `drill`, `forms/validate`, `account-links/auto`; **editor** — плюс изменения `reports*`, `classifiers*`, `templates*`, `account-links*`, `corr/override`; **admin** — плюс `firms`, `sync`, `closing-rules`, `account-names`, `demo/seed`, `users`; любой неизвестный изменяющий маршрут — только admin (новые маршруты исполнителей 2A и интерфейса, которые должны быть доступны ниже, добавлять в `auth.py: VIEWER_POST/EDITOR_ANY`). Для явной защиты есть зависимость `auth.require_role("editor")`.

**6. Демо-данные.** `POST /api/demo/seed` (или `python -m app.routes_forms seed` из `backend`), идемпотентно по названиям, существующее не трогает → `{created: n, created_items, skipped, classifier_id, report_ids, reports:[{id,name,type}]}`. Создаёт классификатор «Российские коды (метки Logo 2026)» (через `import-specode`, 2026) и отчёты в папке «Примеры»: «ОСВ по счетам (пример)», «ОСВ по российским кодам (пример)» (период — последний год набора: метки Logo надёжны не во всех годах), «Баланс (пример)» (на 2025: графы begin/end/prev; Tekdüzen: активы 1xx/2xx, обязательства 3xx/4xx, капитал 5xx, результат 59x/6xx/7xx; контроли «Актив = Пассив» с допуском 0,50, разбор класса 1, классы 8–9 = 0, coverage 1*–7*) и «Отчёт о прибылях и убытках (пример)» (2025, графы period/prev, `include_closing=false` — закрывающие ордера исключены осознанно). Официальных бланков Туркменистана нет — слово «пример» обязательно (и `definition.note`).

**Ответ на «Предложения от интерфейса (волна 2)»:** п.6 принят (+ графы `turn`/`prev_year` как алиасы `period`/`prev`, `checks[].tolerance`, `coverage`, `meta_cells`); п.7 принят (`compare` понимается; рекомендуем плоскую форму `years/measures`); п.8 — формат ответа расширен, `ok/error/pos` сохранены; п.9 принят (+`sheets` в ответе загрузки, `/download`); п.10 — `GET` плоский список, `auto` не сохраняет и возвращает `links` + отчёт, `PUT` — замена списка; п.11 принят; п.12 — `created` — число, перечень в `reports`.

**Недоделки и ограничения 2B:** формы не считают по центрам затрат и по месяцам; клик по числу графы формы (`drill:null`) — расшифровка по `row.drill.accounts` + `column.period` на стороне интерфейса; `OBDK` проверен инвариантами на парах; связи 2007→2008 подобраны автоматически лишь частично — спорные и оставшиеся нужно проверять вручную; `compare_years` не сравнивает несколько фирм одного года.

### Изменения интерфейса (волна 2)

- Интерфейс подстроен под фактический контракт серверов 2A/2B: `compare_years` — поля верхнего уровня `years, target_year, measures, use_links, mmdd_from, mmdd_to` (а не `compare{}`); колонки формы `begin|end|period|prev|prev_begin`; пустая формула = строка-заголовок; `/api/forms/validate` → `{ok, errors:[{message,pos}]}`; связи счетов — `GET/POST /api/account-links`, `POST /auto`, `POST /auto/apply`, `PUT/DELETE /{id}`; расшифровка берёт `corr`/`corr_groups` из `columns[]` и `drill`/`period` из колонок; строки с `fiche` открывают ордер кнопкой «↗».
- Сборка фронтенда — **классический скрипт** (`format: iife`, плагин в `vite.config.ts` убирает `type="module"`): статика `/assets` отдаётся сервером с `Content-Type: text/plain` для `.js` (Python на Windows берёт тип из реестра), и браузер отказывался исполнять модуль. Правильное лечение на сервере — `mimetypes.add_type("application/javascript", ".js")` до монтирования `StaticFiles` в `backend/app/main.py`; после него обход не нужен, но и не мешает.
- Замечания к серверу (найдены при приёмке интерфейса): (1) `compare_years` на 2025/2026: контроль «все счета размещены в строках (оборот Дт)» красный — «в строках 0,01 из 1 000,00» (похоже на сверку с неверной суммой строк), и «сальдо на конец, Σ нетто = 0: 0,01» (это расхождение самого açılış, но помечено ✗); (2) первый расчёт ОСВ после перезапуска занимал ~36 с (прогрев), последующие — доли секунды; (3) в названиях из Logo (SPECODE-группы, проводки) потеряны символы («–» → «�», «ö» → «?») — проблема кодировки при чтении из MSSQL; (4) при `AUTH=0` `/api/users` отдаёт пустой список — экран «Пользователи» работает, но роли не действуют.

### Изменения интерфейса (волна 3: приёмка и удобство)

- **Контроли** (`Result.checks[]`): интерфейс читает необязательное поле `level: "ok"|"warn"|"error"` (нет поля — по `ok`: `ok` / `error`). Зелёный — норма, жёлтый — замечание, красный — расхождение; над полосой — сводка «N в норме · N замечаний · N расхождений»; контроль длиннее ~110 знаков показывается в одну строку с кнопкой «подробнее». Предупреждения `warnings[]` — отдельными жёлтыми строками.
- **Шапка отчёта**: под названием — «организация · период · валюта, единицы · с закрывающими / без закрывающих ордеров». Организация = название фирмы Logo года конца периода без хвостового года («Фирма 2026» → «Фирма»); та же строка идёт в печатную шапку.
- **Счёт в параметрах** (карточка, анализ, главная книга, журнал): поле с автоподсказкой по коду и названию из `/api/accounts?year=` года конца периода (несколько счетов через запятую, ↑↓ Enter).
- **«Открыть карточку счёта»**: кнопка в расшифровке (по счёту проводки) и значок ▤ у строки карточки; открывает в новой вкладке `#/report/new/card?account=&from=&to=&closing=&cur=&scale=&den=` и сразу формирует карточку за тот же период и с теми же настройками.
- **Расшифровка ячеек формы по строкам**: «на начало»/`prev_begin` → `/api/drill` с `mode:"open"`; «на конец»/«прошлый год» → `mode:"close"`; если в формуле строки (с учётом ссылок `[код]`) есть `OBD/OBK/OBDK` — `mode:"turn"`. Для сальдо над таблицей показывается «Сальдо (Дебет − Кредит)»; оно равно значению ячейки для свёрнутых `SN/SK` (сверено на строках баланса-примера).
- **Несохранённые изменения**: пометка «изменён» у названия, жёлтая подсказка «параметры изменены после последнего формирования», предупреждение при уходе (навигация по меню/«назад» и закрытие вкладки; то же в редакторе классификатора).
- **Сообщения об ошибках**: `api.ts` приводит ответы сервера к русскому тексту — 5xx и тексты с трассировками заменяются общей фразой, у 4xx убираются англоязычные хвосты («…: Error -3 while…») и технические пометки в скобках («(date_to)»), в 403 роли называются по-русски.
- **Печать**: во время печати вместо интерактивной таблицы рисуется статическая (`PrintTables`); таблицы шире 8 граф режутся на листы по ≤8 граф с повтором кода/названия и подписью «Графы 9–16 из 38»; в плоских матрицах (шахматка) строки без значений в данном листе не печатаются; шапка таблицы повторяется на каждой странице.
- **Роли**: viewer не видит кнопок сохранения/копирования/удаления и пункта «Настройки», над страницей — пометка «Режим просмотра»; editor видит только вкладки «Шаблоны Excel» и «Связи счетов»; admin — все. Сервер по-прежнему главный (403).
- Подписи двух левых граф зависят от вида отчёта (карточка: «Дата / Содержание операции»; форма: «Код строки / Показатель» и т. д.); ширина граф подгоняется под заголовок; смена пароля пользователя — диалог с полем-паролем вместо `window.prompt`; в «Сравнении лет» добавлена галка «Изменение, %» (`delta`).
- Удалены `src/mock.ts`, переменная `VITE_MOCK` и скрипт `dev:mock` (мок-данные устарели относительно контракта).
- Проверка карточки/расшифровки: суммы `/api/drill` по ~280 ячейкам ОСВ, ОСВ по группам, карточки, анализа (по месяцам), главной книги, шахматки и журнала-ордера совпали с числом в ячейке (с учётом `corr` колонки).

### Дефекты сервера, найденные при проходе интерфейса

1. **Первый расчёт после простоя 20–35 с.** Шаги: не делать запросов к отчётам >60 с; `POST /api/run` с любым отчётом (например, ОСВ 2025 или «Баланс (пример)») — ответ через ~21 с; сразу повторить — 0,3 с. То же у `POST /api/export/xlsx` (пример: выгрузка формы в шаблон ждала ~20 с). Похоже, проверка отпечатка MSSQL (`FINGERPRINT_TTL=60`) выполняется синхронно в запросе пользователя; лучше обновлять отпечаток в фоне. Интерфейс показывает счётчик секунд и пояснение.
2. **Потеря символов в текстах из Logo.** Шаги: `GET /api/fiche?firm=1&id=1` → `fiche_text` «…Onceki Mali Y?ldan»; в расшифровке «Баланс (пример)», строка 110 — те же «?» вместо турецких букв (ı, ş, ğ…). Кодировка при чтении MSSQL.
3. **Красные контроли на реальной неточности исходных данных.** `POST /api/run` для «ОСВ по счетам (пример)» (2025) — «Сальдо на начало/конец: Σ нетто = 0» и «Развёрнутое сальдо Дт = Кт» ✗ из-за небаланса açılış; в «Сравнении лет» (2024–2026) — «Σ нетто = 0» ✗ (небаланс открытия двух лет). Поле `checks[].level` ещё не приходит — такие контроли должны быть `warn`, пока расхождение меньше допуска.
4. **Карточка счёта: пустая колонка «Метод».** Шаги: `card`, счёт `100.01*`, период 2026 — `values.method == ""` у проводок с методом SEQ, колонка выглядит сломанной (непустая только для расчётных). Желательно «по порядку» или скрывать колонку, если везде пусто.
5. **Строка-заголовок счёта в анализе/главной книге: `name: "-"`** (анализ 320*, период 2026; в главной книге `name: ""`) — желательно подставлять название счёта из плана.
6. **Технические фразы в текстах ошибок.** `PUT/POST /api/templates` с повреждённым .xlsx → «Не удалось открыть файл как книгу Excel: Error -3 while decompressing data: invalid code…» (трассировка библиотеки); 403 — «нужна роль «editor», у вас «viewer»» (английские коды ролей); 400 — «(date_to)», «(years: [2024, 2025])». Интерфейс вырезает/переводит, но лучше исправить у источника.
7. **Отчёт по несуществующему счёту отдаёт 200 с пустой таблицей** (`card`, `99999*`) — допустимо, но стоит добавить в `warnings` «счёт не найден в плане счетов».
8. Организация в `Result` не передаётся — интерфейс берёт имя фирмы из `/api/firms`; для надёжной шапки и печати лучше добавить `Result.org` (название и номер фирмы).
9. Порт 8795 на момент приёмки был занят чужим временным сервером (AUTH=1, другой исполнитель); проверка входа и ролей сделана на 8796.

### Изменения бэкенда (волна 3: исправления по приёмке и ввод в работу)

- **Статика.** `main.py` регистрирует `mimetypes.add_type` для `.js .mjs .css .svg .woff2 .json` до монтирования `/assets` (проверено: `Content-Type: application/javascript`). Обход во фронтенде (`vite.config.ts`, сборка классическим скриптом) не менялся и не мешает; `vite.config.ts` и сборку в этой волне не трогал.
- **Отпечаток и политика проверки.** Замер: причина 13–28 с — не `SUM(ROUND(...))` и не соединение, а пять полных сканов кластерного индекса `EMFLINE` (~2400 страниц) в одном запросе при «холодном» кэше SQL Server (физическое чтение ~4 мс на страницу; тот же запрос то 0,03 с, то 15 с; `MAXDOP` не влияет). Новый отпечаток `v3|…` читает ~450 страниц: `COUNT`/`MAX(LOGICALREF)` EMFLINE по узкому индексу, агрегат EMFICHE (число, MAX ref, ΣTOTALACTIVE/PASSIVE как decimal, отменённые, MAX изменения) и EMUHACC (число, MAX изменения); на все фирмы около 2 с (≈0,1 с на фирму). Итоги ордеров = суммы строк (сверено на нескольких фирмах). `firms.checked_ts` (время последней сверки, переживает перезапуск), `config.FINGERPRINT_TTL_OLD`: текущий и прошлый год — не чаще 60 с, старше — раз в сутки или по кнопке (`POST /api/sync`). Запрос отпечатка ограничен 5 с, на весь расчёт 8 с (`cache.CHECK_TIMEOUT/CHECK_BUDGET`): при сбое/медленном Logo — расчёт по кэшу с предупреждением. Изменившаяся фирма пересинхронизируется (2–10 с) перед расчётом, в `warnings` — сообщение «данные … перезагружены»; `GET /api/status` получил `refresh {active, firm, year, message}`.
- **`Result.checks[].level`** `ok|warn|error`; `ok == (level == "ok")` (warn несёт `ok:false`). Расставляется на границе API (`common.finalize_checks`) и в движках. **Известные небалансы открытия:** `firms.open_imbalance` (Σ(Дт−Кт) ордеров açılış, копейки Logo) считается при каждой синхронизации по каждой фирме (миграция заполняет по кэшу); контроли «Σ нетто = 0», «развёрнутое Дт = Кт», «Актив = Пассив» (формы), «сальдо на конец» в compare_years при расхождении ровно на этот небаланс получают `warn` с текстом «небаланс в исходных данных Logo: открытие N года не сбалансировано на X»; иное расхождение — `error`. Величина небаланса зависит от данных (как правило, копейки). USD (REPORTNET): расхождение оборотов Дт/Кт — `warn`. Контроль compare_years «все счета размещены» теперь сверяет оборот Дт, Кт и сальдо; на 2025/2026 он не расходился (замечание об этом расхождении не воспроизведено ни в одном сочетании мер/валюты/баланса); реально расходится только при `options.unmapped=false` с классификатором (скрытая строка) — тогда `warn` с пояснением.
- **carry_gaps.** Красный контроль заменён сводкой (`level ok`): число балансовых счетов с расхождением и Σ|Δ|, отдельно классы 6–7 и 590. Непереносимые счета скрыты, показ — `options.show_noncarried=true` (отдельная группа в каждой границе); итог только по балансовым.
- **Кодировка.** Причина потери «–», İ, Ş, ş, ğ: драйвер ODBC сам переводил cp1254 в ANSI клиента (cp1251) с «best fit» (İ→I, Ş→S, не представимое → «�»). Лечение: `AutoTranslate=no` в строке подключения. Эталон — сырые байты (`CONVERT(varbinary)` → cp1254): совпадение 100% по названиям планов счетов и текстам. Замечание: `sqlcmd -f 65001` на этой машине сам теряет İ/Ş (выдаёт I/S или «�»), эталоном не годится — использовать `-f 1254` или varbinary. Данные всех фирм перезагружены; названия групп импорта SPECODE обновлены (`classifiers.refresh_specode_names`). «?» вместо букв, лежащие уже в самой базе (единичные ордера и счета), не восстановимы.
- **Ручное закрепление корреспонденции** работает для лет до 2009 (суммы — в новых манатах как в интерфейсе; сводятся к точным остаткам в старых копейках по остовному лесу графа счетов, допуск округления до цента) и для ордеров со сторно (суммы пар могут быть отрицательными). Тесты: книга до деноминации (÷5000), книга со сторно, синтетические случаи.
- **Закрывающие ордера.** Правило по умолчанию `570*,580*,590*,591*,690*,691*,692*` (580 — убытки прошлых лет, 591 — убыток года: в отдельные годы помесячное закрытие шло 690↔591, поэтому раньше не помечалось); миграция №3 обновляет старое значение. Закрытия всех лет помечаются по этому правилу.
- **По дефектам интерфейса:** карточка — метод SEQ показывается как «по порядку»; названия заголовков в анализе/главной книге берутся из плана по маске (`320*` → название корня) или классификатора; «по маске … счетов не найдено» — в `warnings` (card/analysis/ledger/journal/chess); `Result.organization` (строка) и `organization_firms [{firm, year, name}]`; тексты ошибок: роли «просмотр/редактор/администратор», без техпометок `(date_from)`, `(years: …)`; повреждённый .xlsx — «файл повреждён или это не книга Excel».
- **Ввод в работу:** `README.md`, `backup.ps1`, `docs/logo-readonly-login.sql`, `CLAUDE.md`, `run.ps1 -CheckOnly/-Build` (сборка фронтенда при отсутствии `dist`).

### Изменения бэкенда (волна 4: Журнал-ордер с фиксированными графами)
Расширение `definition.journal` (всё необязательное; без новых полей отчёт строится как раньше):
- `fixed_cols: [{sel, title?}]` — графы бланка в заданном порядке. `sel` — тот же селектор, что в формулах форм (`engine/form.parse_selector`): маска `102*`, код и потомки `320`, диапазон `150..157`, объединение через `|` (а также `,` и `;`), исключение `~`, группа `@классификатор:код`. `title` по умолчанию = `sel`. Графа показывается всегда, даже пустая. Корреспондент, подходящий нескольким графам, попадает в ПЕРВУЮ из них (двойного счёта нет).
- `rest: "other" | "auto" | "hide"` (по умолчанию `other` при непустом `fixed_cols`, иначе `auto`): `other` — одна графа «Прочие»; `auto` — автоматические графы после фиксированных (`journal.corr`, `options.max_graphs`; фиксированные счета в них не входят); `hide` — не показывать, «Итого» остаётся полным оборотом, в `warnings` — сумма, не вошедшая в графы, контроль «Σ по графам + не вошедшее в графы = итог».
- `with_balance: bool` — первая строка `bal_open` «Сальдо на начало (дебетовое|кредитовое)», после `total` строки `opp_turn` «Оборот по дебету/кредиту (противоположная сторона)» и `bal_close` «Сальдо на конец …». Значения — в колонке «Итого» (`turn_kt` для credit, `turn_dt` для debit), по модулю; знак — в названии строки. Сальдо по ОСВ свёрнутое (Дт−Кт) по выбранным счетам; сальдо на конец = начало + оборот Дт − оборот Кт. Строки: `bal_open` — `kind=line`, остальные `kind=total`; `row.drill` у сальдо несёт `mode: "open"|"close"`, у оборота противоположной стороны — `side` противоположный.
- Колонки: `dtc:f0`, `dtc:f1`… (credit) / `ktc:f0`… (debit), «Прочие» — `dtc:other`/`ktc:other` (при `auto` переполнение max_graphs, как раньше, — `…:~`). У колонок `name`=title, `corr: [термы sel]`, `drill` = сторона; новое необязательное `corr_exclude: [селектор, …]`.
- Контроли: `Итог = оборот Кт/Дт счёта (ОСВ)`, `Σ по графам = итог` (включая «Прочие»), при `with_balance` — `Сальдо на конец = исходящее сальдо ОСВ`; расчётные (PROP) пары помечаются предупреждением как раньше.
- Валидация (HTTP 400, по-русски): «Графа N: не указан отбор счетов (sel)», «Графа N: некорректный отбор счетов «…» — …», `journal.rest: other | auto | hide`, `journal.fixed_cols: ожидается список граф`.
- **`/api/drill`**: новые возможности отбора партнёра — `corr` теперь принимает селекторы целиком (диапазоны `a..b`, `|`, `,`, `~`, `@группа`), новый параметр `corr_exclude` (список селекторов; партнёр не должен подходить ни под один). Партнёр подходит, если он под любой `corr` (пусто = любой) и ни под один `corr_exclude`. Простые маски работают прежним путём. Расшифровка колонки = `row.drill` + `side=column.drill` + `corr`/`corr_exclude` колонки; сумма расшифровки = число в ячейке (в том числе для «Прочие», у которых `corr:["*"]`, `corr_exclude`=все `sel` фиксированных граф; у автоматических граф при `fixed_cols` тоже есть `corr_exclude`). Клиент обязан передавать `corr_exclude` колонки в drill вместе с `corr`.
- Примеры (папка «Примеры», `seed_demo`, идемпотентно по названию): «Журнал-ордер № 1 — касса (счёт 100, по кредиту)», «Ведомость № 1 — касса (счёт 100, по дебету)», «Журнал-ордер — поставщики (счёт 320, по кредиту)» — 2026 г., `rest=other`, `with_balance=true`, 6–8 фиксированных граф по крупнейшим корреспондентам. Тесты: `tests/test_journal_fixed.py`.

### Изменения бэкенда (волна 5: годы 2003–2005 и выбор книги года)
**Данные.** Годы до 2006 подключаются основными и альтернативными книгами (карта `config.DEFAULT_YEAR_FIRM` и `config.ALT_YEAR_FIRM`); старые манаты, делитель 5000 назначается сам (год < 2009). Периоды `_02_` старых фирм (только перенесённое открытие следующего года) не читаются. В старых книгах `CROSSCODE` пуст или заполнен частично — пары считаются по SEQ/PROP, в `corr/stats` видна доля методов по каждому году. Небаланс открытия в старой книге даёт `warn`; в тексте при деноминации добавлено «; до деноминации N старых манатов». `carry_gaps` между книгами разных источников: сводка + предупреждение (расхождение по 570/591, 590 скрыт как непереносимый; перекодировка кодов субсчетов, например 120.30.36 → 120.30.036). Чтение Logo терпимо к недекодируемым байтам: вместо «�» подставляется заменитель, синхронизация не падает.

**Выбор книги года (для переключателя в интерфейсе).** Всегда в отчётах участвует ровно одна фирма года (включённая, `enabled=1`). Выбор хранится в `settings.db` (таблица `year_books(year, firm, changed_at)`, миграция №4) и переживает пересоздание кэша.
- `GET /api/years` → `[{year, firm, name, enabled, synced_at, line_count, divisor, default_firm, alternatives: [{firm, name, synced_at, line_count}]}]`. `firm` — текущая книга года; `alternatives` — остальные книги этого года (пусто, если выбора нет). Для переключателя достаточно этого списка: показывать выпадающий список «Книга года» = текущая + альтернативные (подпись — `name`, `firm`), если `alternatives` не пуст.
- `GET /api/firms` — к каждой строке добавлены `alternatives: [{firm, name}]` (другие книги того же года, у фирм вне карты — `[]`) и `is_default` (основная книга по карте).
- `PUT /api/years/{year}/book` `{firm}` (роль admin при AUTH=1) → `{year, firm, previous, changed, sync_started}`. Ошибки (русский текст в `detail`): 400 «Фирма N не является книгой Y года (допустимы: …)» / «Для Y года нет альтернативных книг» / «Укажите фирму»; 409 «Синхронизация уже идёт - повторите смену книги после её окончания» (тогда ничего не меняется). Действие: включается выбранная фирма, остальные книги года выключаются; у новой книги сбрасываются отпечаток и отметка синхронизации (`synced_at=null`), запускается фоновая принудительная синхронизация (`sync_started=true`; полная перезагрузка проводок, плана счетов и пересчёт пар корреспонденции; ручные закрепления хранятся по фирме и сохраняются). Прогресс — `GET /api/status` (`syncing`, `progress`); пока книга не загружена, отчёты за этот год отвечают «Данные фирмы N (год Y) ещё не загружены…» (отчёт, запущенный с проверкой свежести, загрузит книгу сам). Повторный выбор той же книги — `changed=false`, синхронизация не запускается. Возврат на прежнюю книгу — то же действие. После смены интерфейсу нужно перечитать `GET /api/years`, `GET /api/accounts?year=` и перегенерировать открытые отчёты; `Result.organization_firms` показывает фактическую книгу.
- Предупреждение для пользователя: при выборе альтернативной книги разрыв переноса остатков между годами ожидаем; цепочка из согласованных книг сходится.

### Изменения бэкенда (волна 6: дополнительные поля счетов, кодировка 2003–2004, Python 3.8, автоподбор драйвера)
**`GET /api/accounts?year=` — новые поля каждого счёта** (дерево, подсказки и поиск берут их из этого же ответа; других ручек со счетами нет). Старые поля (`code, name, level, parent, root, is_leaf, specode, cyphcode, center_ctrl`) остались; `center_ctrl` — 0/1 («счёт требует центр затрат», в API именно с подчёркиванием).
| поле | тип | смысл |
|---|---|---|
| `units` | string (`""` если нет) | единица/знак счёта из Logo (`UNITS`): `$`, `kg`, `TMM`, `Tm`… |
| `is_currency` | bool | `units` содержит `$` — валютный счёт (надёжнее названия и `CCURRENCY`) |
| `full_name` | string \| null | полное название из `CLDEF`, если оно непустое и отличается от `name`; null — отдельного полного названия нет (в т.ч. когда оно уже использовано как `name`) |
| `note` | string \| null | примечание (`EXTNAME`, до 40 символов: ИНН, договор, туркменское название, владелец) |
| `created` | `YYYY-MM-DD` \| null | дата создания счёта; null, если в Logo пусто или год вне 1995..текущий (в старых книгах там шаблонные даты) |
| `created_by` | string \| null | имя пользователя Logo (`L_CAPIUSER.NAME`) по `CAPIBLOCK_CREATEDBY`; null, если не задано или не расшифровывается |
| `inactive` | bool | `ACTIVE=1` в Logo: пассивный (неиспользуемый) счёт; 0 — используется. Заполнено не во всех книгах |
| `specode_name` | string \| null | расшифровка метки `specode` из справочника Logo `LG_<FFF>_SPECODES` (`SPECODETYPE=35, CODETYPE=1`): `A` → «Название группы A», `B` → «Название группы B»; null, если у метки нет расшифровки (российские коды `62`, `10/1` в Logo не названы — их названия ведутся в классификаторе) |
Интерфейсу: колонки «Ед.», «Примечание», «Создан / кем», пометка `$` и серый шрифт для `inactive`, всплывающая подсказка `full_name`, у метки — `specode_name` (подсказкой), фильтр «только валютные» = `is_currency`.

**Название счёта (`name`) везде** (отчёты, дерево, подсказки, Excel, сравнение лет, классификаторы): если `CLDEF` непустой, длиннее `DEFINITION_` и начинается с него (сравнение после trim, без учёта регистра и повторных пробелов) — `name` = `CLDEF`, иначе прежнее `DEFINITION_`. Ручные переименования (`name_overrides`) главнее. В кэше `accounts.name` уже хранит итоговое название, `def_name` — исходное `DEFINITION_`. Восстановленные названия — только там, где `CLDEF` дополняет короткое или обрезанное название (например, добавляет перевод); остальные обрезанные названия имеют в `CLDEF` другой текст — они не меняются, `CLDEF` виден как `full_name`.

**Правило классификатора по единице.** Новый вид правила `kind="units"`, `value` — единица (`$`, `kg`…; сравнение без учёта регистра, точное совпадение, специфичность = длина значения; действует год-от/год-до, как у остальных). Допустимые виды теперь `mask | range | list | specode | units`; ошибка валидации: «Неизвестный вид правила «…» (mask, range, list, specode, units)». Пример: группа «Валютные счета» с правилом `units = $`. Движок отчётов передаёт единицу в `Resolver.resolve` третьим элементом кортежа `(код, specode, units)`.

**Кэш.** `accounts` дополнена колонками `units, def_name, full_name, note, created, created_by, inactive, specode_name, name_restored`; новая таблица `specodes(firm, specode, name)` (расшифровки меток по книге); `firms.plan_ver`. Миграция при запуске: колонки добавляются `ALTER TABLE`, а при старте сервера в фоне перечитывается ТОЛЬКО план счетов и метки всех загруженных книг (`cache.refresh_plans`, все книги ≈ несколько секунд), проводки не трогаются; книги с `plan_ver` ниже текущей версии (`PLAN_VER=2`) обновляются сами; обычная синхронизация фирмы заполняет план по-новому. До окончания фонового обновления (секунды) новые поля у счетов пусты.

**Тексты книг 2003–2005 (кодировка).** В старых книгах часть текстов ордеров (`EMFICHE.GENEXP1`) записана в OEM-кодировке cp857, остальное (тексты строк, названия счетов) — cp1254; смешения в одной строке нет. Для книг до 2006 года (`cache.OEM_BEFORE_YEAR`) при чтении включён построчный выбор (`logo.decode_auto`, кодек `cp1254auto`): строка целиком читается как cp857, если в ней есть байты ı İ ş Ş ğ Ğ ü ö ç Ü Ö Ç в виде cp857 (0x8D 0x98 0x9F 0x9E 0xA7 0xA6 0x81 0x94 0x87 0x9A 0x99 0x80) и нет типичных для cp1254 (0xFD 0xDD 0xFE 0xDE 0xF0 0xD0 0xFC 0xDC 0xF6 0xD6 0xE7 0xC7); иначе — cp1254. Для 2006+ декодер не включается (там 0x94 — обычная кавычка). Изменились только тексты ордеров 2003–2004 (в кэше после пересинхронизации); названия счетов и тексты строк не изменились ни в одном символе, названия счетов 2006+ побайтно те же.

**Python 3.8 и подключение.** Весь `backend/app` совместим с Python 3.8 (во всех модулях `from __future__ import annotations`; проверено `compileall` и импортом `app.main` на 3.8.10 и чтением плана счетов на 3.8 с драйверами ODBC Driver 17, SQL Server Native Client 11.0, «SQL Server» — результат идентичен 3.14). Драйвер ODBC: если `LOGO_DRIVER`/`[logo] driver` не заданы — первый найденный из «ODBC Driver 17 for SQL Server», «ODBC Driver 13 for SQL Server», «SQL Server Native Client 11.0», «SQL Server Native Client 10.0», «SQL Server», затем любой «ODBC Driver N» (18 — с `Encrypt=no`). `TrustServerCertificate`/`ApplicationIntent` добавляются только для «ODBC Driver N»; `AutoTranslate=no` — всегда; пароль в строке подключения заключается в `{}`. Нет драйвера — «Не найден драйвер ODBC для SQL Server. Установите «ODBC Driver 17 for SQL Server» … (найдено: …)». Настройки: переменные окружения главнее файла `config.ini` (рядом с программой, затем `data\config.ini`, либо путь в `CONFIG_FILE`; разделы `[logo] server, user, password, database, driver`, `[app] port, auth, data_dir`; образец `config.ini.example`). Запуск без PowerShell: `python -m app` из папки `backend` (порт из `PORT`/`config.ini`).

### Изменения бэкенда (волна 7: атрибуты ордеров и проводок)
Подтянуты из Logo и положены в кэш: у проводки — подразделение `EMFLINE.BRANCH`, отдел `EMFLINE.DEPARTMENT`, количество `EMFLINE.AMNT`; у ордера — источник `EMFICHE.MODULENR`, автор `CAPIBLOCK_CREATEDBY`, дата и время ввода (`CAPIBLOCK_CREADEDDATE` + `CREATEDHOUR`/`CREATEDMIN`), шифр документа `DOCODE`, доп. описание `GENEXP2..4`. По ним — доп. колонки в карточке и расшифровке, отбор во всех отчётах, разбивка и количество в ОСВ, справочники.

**Кэш.** `lines` + `branch INT, dept INT, qty_dt REAL, qty_kt REAL` (количество по стороне проводки, как USD: сторона по `SIGN`). Новые таблицы: `fiches(firm, fiche_id, module, doc_no, created_by, created_at 'YYYY-MM-DD HH:MM', text_ext, PK(firm,fiche_id))` — атрибуты ордера хранятся один раз на ордер, не в строках; `branches(firm,id,name)`, `departments(firm,id,name)` (из `L_CAPIDIV`/`L_CAPIDEPT` по номеру фирмы), `logo_users(id,name)` (из `L_CAPIUSER`), `modules(id,name,confirmed)` (из словаря `attrs.MODULES`, заполняется при старте). `firms.attr_ver` (версия атрибутов, `cache.ATTR_VER=1`). `text_ext` = `GENEXP2+GENEXP3+GENEXP4` склеенные БЕЗ разделителя (в Logo `GENEXP1..4` — один текст, нарезанный по 50 знаков: «…Cari Hesap Unvanlı 15.09.2026 ta» + «rihli Satınalma Faturası.»); `fiche_text` (= `GENEXP1`) остался прежним.
**Версия и пересинхронизация.** При старте сервера в фоне `cache.refresh_attrs_all()` дочитывает атрибуты всех загруженных книг с `attr_ver < ATTR_VER` (одна выборка `EMFLINE` + `EMFICHE` + справочники, UPDATE по `id`; суммы, пары, замки не трогаются; ≈1–3 с на книгу); если за это время данные Logo изменились (первые 10 полей отпечатка не совпали) — полная `sync_firm`. Перед отчётом `Context` вызывает `cache.ensure_attrs(книги периода)`: устаревшая книга дочитывается синхронно, в `warnings` — «Атрибуты ордеров и проводок книги N … загружены из Logo (X с)»; если Logo недоступна, а отчёту нужны атрибуты (отбор, `by_branch`, `qty`, `columns.extra`, карточка, расшифровка), — 400 «Атрибуты ордеров и проводок книги N ещё не загружены из Logo, а Logo недоступно: …» (полупустые атрибуты молча не отдаются). Обычная `sync_firm` заполняет атрибуты сразу.
**Отпечаток** — формат `v4|…` (13 полей): к прежним добавлены в том же скане `EMFICHE` (без доп. чтения `EMFLINE`) `Σ(MODULENR·3+CREATEDBY)`, `Σ(BRANCH·5+DEPARTMENT)` ордеров и `ΣLEN(DOCODE,GENEXP2..4)`. Ловит: смену источника, автора, подразделения/отдела ордера (строки наследуют `BRANCH` ордера: расхождений 0), правку шифра и доп. описания (по длине), а также любую правку ордера через `CAPIBLOCK_MODIFIEDDATE`. НЕ ловит: правку `AMNT` строки и подмену равной по длине строки `GENEXP2..4` без изменения даты изменения ордера (Logo обычно ставит её при любой правке; принудительно — `POST /api/sync {force:true}`).

**Словарь источников (`EMFICHE.MODULENR`).** Составлен по текстам ордеров (`GENEXP1..4`, «… tarihli <вид документа>.») и счетам проводок по книгам разных лет. Подтверждено данными: **0 «Ручной ввод»** (свободный текст, ордера открытия TRCODE 1, амортизация, «Virman Islemi», в старых книгах пакетные «kasa/banka/stok işlemleri»); **1 «Склад (материалы)»** («Sarf Çıkışları», «Malzeme İşlemleri», «Üretimden Girişler»; счета 150–152, 710, 730); **2 «Закупки»** («MAL ALIM FATURASI», «ALINAN HİZMET FATURASI», «Satınalma Faturası»; 150, 191, 320, 329); **3 «Продажи»** («TOPTAN SATIŞ FATURASI»; 600, 601, 391, 120); **4 «Взаиморасчёты с контрагентами»** («Virman İşlemi/Fişi», «Alacak Dekontu»; 120, 320, 335, 127, 131); **6 «Банк»** («Gelen/Gönderilen Havale», «Banka İşlemleri»; 102); **7 «Касса»** («Kasa Tahsilatları/Ödemeleri», «CH Tahsilat», «Bankaya Yatırılan», «Muhasebe Tediye İşlemi»; 100). Не подтверждено (нейтрально «Модуль N»): **20** (единичный ордер с текстом о распределении затрат — по одному ордеру вывод не делаем); любое другое значение, которого нет в словаре, тоже «Модуль N». В `GET /api/dicts` у источника есть `confirmed`. В самых старых книгах `MODULENR` не заполнен (все 0 = «Ручной ввод»).
**Подразделения и отделы.** Номера `BRANCH`/`DEPARTMENT` — реальные номера подразделений Logo, **0 — настоящее подразделение («Главное»)** (не «пусто»): значений -1 в данных обычно нет. Названия — `L_CAPIDIV` / `L_CAPIDEPT` по фирме; в старых книгах в справочнике названий может не хватать (используются номера 1–3, а в `L_CAPIDIV` есть только 0) — тогда «Подразделение N» / «Отдел N». Номер < 0 или NULL → «Не указано». Фактически `BRANCH` = `DEPARTMENT` почти всегда; расходятся у малой доли строк.

**Фильтры во всех отчётах по проводкам** (`osv, card, analysis, ledger, chess, journal, form, compare_years`): `filters.branches: [номера]`, `filters.departments: [номера]`, `filters.sources: [коды модуля]`, `filters.authors: [ид пользователя Logo]`; пусто/нет = без отбора. Несколько значений — «или» внутри одного поля, «и» между полями. Правила:
- ОСВ, карточка, форма (сальдо и обороты), `compare_years`, расчёт остатков в главной книге/журнале: отбираются **проводки**, включая строки открытия (açılış; у них свои `branch`/`dept` и автор); поэтому сальдо на начало/конец при отборе — сальдо «по этому подразделению/источнику/автору». Σ по всем подразделениям входящего сальдо и оборотов = полное значение (проверено тестом).
- Отчёты корреспонденции (`analysis, ledger, chess, journal`, `OBDK` в формах) работают на парах: **пара попадает в отчёт, только если обе её проводки (дебетовая и кредитовая) проходят отбор** (источник и автор — атрибуты ордера, общие для обеих). Пары между разными подразделениями при отборе по подразделению не показываются; поэтому Σ шахматки по одному подразделению ≤ оборота ОСВ с тем же отбором. Карточка — построчная: отбираются проводки счёта; корр. счёт показан по парам без отбора.
- Расшифровка (`POST /api/drill`) принимает те же `branches, departments, sources, authors` на верхнем уровне тела; в режиме по парам проводка-партнёр тоже должна проходить отбор — сумма расшифровки = число в ячейке отчёта с тем же отбором.
- **Контроли при отборе:** сверки «= ОСВ», «Σ нетто = 0», «развёрнутое Дт = Кт», «сальдо на конец = ОСВ» (главная книга), «Актив = Пассив» не считаются ошибкой: вместо них один информационный контроль `{name: "Отбор по подразделению/источнику/автору: сверка с полным оборотом не выполняется", level: "ok", detail: "подразделение — Цех 1, Цех 2; источник — Банк — суммы не обязаны сходиться …"}`; проверки формы `checks[]` при расхождении получают `level: "warn"` с пояснением. Остаются проверки, не зависящие от полноты (родитель = сумма детей, нач.+обороты=кон., Σ по подразделениям = общий оборот, карточка = ОСВ с тем же отбором).
- **Подзаголовок** (`Result.subtitle`) получает фрагмент `· Отбор: подразделение — Цех 1, Цех 2; отдел — …; источник — Банк; автор — Иванов` (названия из справочников). `warnings` объясняют правило пар.
- Ошибки (400): «Отбор по подразделениям|отделам|источникам|авторам: ожидается список номеров (целых чисел), получено «x»».

**ОСВ: разбивка и количество.**
- `columns.by_branch: true` — обороты Дт/Кт по каждому подразделению, встречающемуся в обороте периода (по возрастанию номера), отдельными графами **между «Сальдо на начало» и «Обороты за период (итого)»**: ключи `turn_dt_b{N}` / `turn_kt_b{N}` (для отрицательного номера `bm{N}`; USD-режим — те же ключи), `group` = «Обороты: <название подразделения>», `title` «Дебет»/«Кредит», `drill: "dt"|"kt"`, **новое поле `branch: N`** — расшифровка ячейки = `/api/drill` с `branches: [N]` + `row.drill` + `side` (суммы совпадают). Общие `turn_dt`/`turn_kt` остаются (группа «Обороты за период (итого)»). Контроли «Σ по подразделениям = общий оборот по дебету / по кредиту» (error при расхождении). Одновременно с `by_month` нельзя: 400 «Нельзя одновременно разбить обороты по месяцам и по подразделениям — выберите что-то одно». Работает вместе с `filters.*` и любым `balance`. Для остальных типов отчётов `by_branch` игнорируется.
- `columns.qty: true` — графы `qty_dt`, `qty_kt` (группа «Количество (оборот)», **новый `type: "qty"`**: число до 3 знаков, не зависит от валюты и `scale`, не делится на деноминацию; `drill: "dt"|"kt"`) — после граф сальдо на конец. Оборот `EMFLINE.AMNT` за период по стороне проводки (открытие не входит). Единицы измерения в проводке нет (она в `accounts.units` счёта): осмысленно по листьям и суммам внутри одного счёта; на уровнях выше и в «ИТОГО» количества просто складываются — в `warnings` об этом сказано («Количество: на уровнях выше листового счёта … складываются количества в разных единицах измерения»). Значения `qty_*` в `totals` тоже есть.

**Карточка счёта (`card`) и расшифровка (`/api/drill`): новые поля строки.** Карточка: в `Result.columns` добавлены 8 колонок с **`optional: true`** (в `values` каждой строки они уже заполнены; интерфейс показывает по выбору), порядок и типы:
| key | заголовок | тип | значение |
|---|---|---|---|
| `branch` | Подразделение | text | название (`L_CAPIDIV`) |
| `department` | Отдел | text | название (`L_CAPIDEPT`) |
| `qty` | Количество | qty | `AMNT` проводки; у проводки с несколькими парами — доля пары пропорционально сумме (Σ = количество проводки); пусто, если нет |
| `source` | Источник | text | название модуля (словарь выше; «Модуль N» для неизвестных) |
| `author` | Автор | text | имя из `L_CAPIUSER` |
| `entered_at` | Дата и время ввода | text | `YYYY-MM-DD HH:MM` (когда ордер введён; ≠ дате ордера) |
| `doc_no` | Шифр документа | text | `DOCODE` |
| `text2_4` | Доп. описание | text | `GENEXP2..4` склеенные непустые (продолжение текста ордера) |
`/api/drill` → `lines[]` получают те же ключи (`branch, department, qty, source, author, entered_at, doc_no, text2_4`) плюс идентификаторы `branch_id, department_id, source_id, author_id` (для фильтров). `GET /api/fiche` отдаёт на ордере `source, author, entered_at, doc_no, text2_4, source_id, author_id`, на каждой строке `lines[] branch, department, qty`. Пустые значения — `""` (для `qty` — `null`). Книги, где поле не заполнено (в старых книгах `source`=Ручной ввод, `doc_no` часто пуст), возвращают пустые строки/«Ручной ввод» — не ошибка.
**Excel.** `columns.extra: [ключи]` в definition (допустимые: `branch, department, qty, source, author, entered_at, doc_no, text2_4`; другое — 400 «Неизвестная дополнительная колонка «x» (допустимо: …)») — в `/api/export/xlsx` колонки с `optional: true` выводятся, только если выбраны (по умолчанию — как раньше, без них); `qty` в Excel — формат `#,##0.###`. **Новая ручка `POST /api/export/drill`** — тело как у `/api/drill` (+ `extra: [...]`; `limit` не более 50 000) → .xlsx расшифровки (доступна роли viewer). Ширина `text2_4` 40.

**Справочники: `GET /api/dicts?year=2026`** → `{year, firm, branches:[{id, name, lines}], departments:[{id, name, lines}], sources:[{id, name, fiches, confirmed}], authors:[{id, name, fiches}], warnings}` — только значения, реально встречающиеся в книге года (`lines` — число строк, `fiches` — число ордеров с проводками; авторы по убыванию числа ордеров). Нет книги года — 400 «Нет включённой книги за N год».

**Заполненность атрибутов** зависит от базы и года (в `BRANCH`/`DEPARTMENT` значение 0 — это «Главное», поэтому «заполнено» = учёт по подразделениям введён, `≠ 0`): в ранних книгах подразделение, источник и дополнительный текст ордера часто не заполнены, в поздних — в основном заполнены; количество `AMNT ≠ 0` — у большинства строк; автор и дата/время ввода — у всех ордеров. Значения источников в данных: 0, 1, 2, 3, 4, 6, 7, изредка 20.
Тесты: `backend/tests/test_attrs7.py` (оборот каждого подразделения против `GROUP BY BRANCH`, Σ по подразделениям, ОСВ с отбором по подразделению/источнику/автору против SQL, количество против `SUM(AMNT)`, расшифровка/карточка/ордер, справочники против SQL, книги 2003–2005, дочитывание атрибутов устаревшей книги).


### Изменения бэкенда (волна 8: Универсальность, этап A — один набор данных, книги, параметры, мастер, подключение)

Для существующей базы **все отчёты считаются как раньше до копейки** (регрессионное сравнение ответов — в отчёте исполнителя); старый API «год → фирма» сохранён поверх книг. Новые маршруты `/api/connections*`, `/api/datasets*`, `/api/books*`, `/api/dataset/params` — только admin; чтение `GET /api/datasets|dataset|books|books/proposal|books/scan` — viewer (`GET /api/connections` — admin). Все ответы — JSON, ошибки — `{"detail": "текст по-русски"}`.

**Понятия.** *Источник (src)* — пара «фирма + период Logo» (`LG_<FFF>_<PP>_…`); ключ `src = firm`, если период 01, иначе `period*1000 + firm`. *Книга* — источник + диапазон дат + метка года (`label`) + делитель + признак OEM-текстов + роль `main | alt | off`. *Слот* — книги-конкуренты за одно время (две книги за один год); в слоте ровно одна `main`. В API и отчётах «год» = метка. `firm` в `/api/fiche`, `/api/corr/override`, `POST /api/sync {firms}` — это **src** (для периода 01 = номер фирмы, ссылки не ломаются).

**Хранилище (только добавление).** `data/registry.db` (новый): `connections`, `datasets`, `meta`. `settings.db`, миграция №5: `books`, `dataset_params`, `scan_cache`. `cache.db`: таблица `sources` (`src, firm, period, name, fingerprint, synced_at, line_count, plan_ver, attr_ver, checked_ts, open_imbalance, oem`); прежняя `firms` не меняется и не читается. Данные набора №1 (основные и запасные книги + параметры) заносятся миграцией из прежней карты, `cache.firms` и `year_books`. Резервная копия `data/settings.db` перед миграцией — в `data/backup/`. Откат: остановить сервер, вернуть прежний код (он читает те же файлы), при желании удалить `data\registry.db` и таблицы `books/dataset_params/scan_cache`, `sources` (старый код их не читает); выбор основной книги в старой версии — через `year_books`, его новый код тоже обновляет.

**Подключение (пароль никогда не отдаётся).**
```
GET    /api/connections        -> [{id,name,server,database,login,has_password:true,driver,trust_cert,timeout_sec,origin:"config|ui",last_ok_at,last_error,env_fields:["password"],password_plain:false,warnings:[]}]
POST   /api/connections        {name,server,database,login,password,driver?:"",trust_cert?:true,timeout_sec?:10} -> объект
PUT    /api/connections/{id}   те же поля; password не передан/null - не менять, "" - очистить
DELETE /api/connections/{id}   409 «Подключение используется наборами: …»
POST   /api/connections/test   {id} или {server,database,login,password,driver?} (без сохранения; password не передан + id - берётся сохранённый)
POST   /api/connections/databases  {server,login,password,driver?} -> ["LOGO_DB", …]   (базы, где есть L_CAPIFIRM)
```
`test` -> `{"ok":true,"server_version":"Microsoft SQL Server 2019","database":"LOGO_DB","driver":"ODBC Driver 17 for SQL Server","logo":{"firms":3,"periods":5,"tables_firm_period":6},"collation":"Turkish_CI_AS","ansi_cp":"cp1254","warnings":["Логин имеет права администратора сервера (sysadmin)…"]}`; ошибки 400: «Не удалось подключиться к серверу «X»: проверьте адрес, имя базы, логин и пароль», «Это не база Logo: в ней нет справочника фирм L_CAPIFIRM», «Не найден драйвер ODBC…». Пароль хранится зашифрованным (DPAPI через ctypes, область «пользователь»; запасной вариант — открытым текстом, тогда `password_plain:true` и предупреждение); смена учётной записи Windows -> «Не удалось расшифровать пароль подключения… Введите пароль заново». `config.ini`/`LOGO_*` — первое подключение (`origin:"config"`); переменные окружения главнее сохранённых полей (`env_fields` — какие поля заданы в окружении, их в интерфейсе править нельзя).

**Набор данных.**
```
GET /api/datasets  -> [{id,name,org_name,is_default,archived,state:"new|ready",role,books_main,books_total,last_sync_at,logo_ok,connection:{id,name,server,database}}]
PUT /api/datasets/{id}  {name?,org_name?,connection_id?,is_default?,archived?,note?}
GET /api/dataset   -> то же + params{…} + books_count + cache_bytes + configured
PUT /api/dataset/params {ключ: значение, …} (частично) -> {resync_required, plan_rebuild, params}
GET /api/me     -> {login, role, auth, datasets:[{id,name,role}], dataset:{id,name}}
GET /api/status -> прежние поля + dataset:{id,name,state}
```
Ключи `params` (JSON): `currency_local {code,name,symbol,gen_pl?,prep_pl?}` (gen_pl/prep_pl — формы названия для текстов вида «в старых манатах»), `currency_report {code,name,enabled}`, `charset {ansi,oem}`, `denomination [{before:"YYYY-MM-DD",divisor}]`, `plan_profile {template:"turkish|none",mode:"sep|width",sep,root_len,widths[],balance_roots[],pnl_roots[],noncarried[]}`, `carry_notes [{from_label?,to_label?,from_firm?,to_firm?,text,denom_suffix?}]`, `sync {ttl_current,ttl_old,old_after_days}`. Смена `charset` -> `resync_required:true` (отпечатки источников сброшены, тексты перечитываются при следующей проверке); смена `plan_profile` -> `plan_rebuild:true` (план счетов перечитывается в фоне, проводки не трогаются). Валюта в `definition.currency`: код местной (`TMT`), код отчётной (`USD`), `LOCAL`, `REPORT`; иначе 400 «Валюта «XXX» не настроена в этом наборе данных (доступны: TMT, USD)»; отчётная выключена — 400 «В этой базе нет сумм в отчётной валюте».

**Книги и мастер.**
```
POST /api/books/scan {firms?:[int]} -> 202 {started:true} | 409 «Сканирование уже идёт»
GET  /api/books/scan -> {state:"idle|running|done|error",done,total,current,message,scanned_at,error}
GET  /api/books/proposal -> предложение (params, books[], off[], gaps_between_labels, unassigned_data); 409, если скана не было
GET  /api/books[?carry=1] -> {books:[{id,slot,label,part,src,src_firm,src_period,date_from,date_to,role,name,divisor,oem,reason,note,firm_name,synced_at,line_count,rows_outside,carry_next}], coverage:{date_from,date_to,gaps[],labels[]}, state}
PUT  /api/books {books:[{id?,slot?,label,part,src_firm,src_period,date_from,date_to,role,name?,divisor,oem,reason?,note?}]} полная замена
     -> {books, coverage, state, sync_needed:[src…], warnings[]}; 400 {detail, errors[], warnings[]}
POST /api/books/validate {books:[…]} -> {ok,errors[],warnings[]}   (ничего не пишет)
POST /api/books/apply-proposal {include_params?:true} -> как PUT (применяет последнее предложение целиком: книги и параметры)
POST /api/books/{id}/make-main -> {book, previous:[…], changed, sync_started}
```
Ошибки проверки (400): «Книги «2024 · фирма 1» и «2024 · фирма 2» пересекаются по датам 01.01.2024–31.12.2024: основной в слоте может быть только одна»; «Основные книги «…» и «…» пересекаются по датам …»; «Книга «…»: дата начала позже даты окончания»; «Книга «…»: в Logo нет таблиц проводок фирмы N, период 02»; «Книги одного периода фирмы N должны иметь одинаковый делитель…». Предупреждения (не блокируют): «В слоте N нет основной книги», «Между книгами X и Y нет книги за …», «Книга «…» пересекает дату деноминации …». Идентификатор новой книги >= 100000 (у перенесённых: основная по умолчанию = год, запасная = 10000+фирма). После `PUT` источники новых основных книг и книг с изменённым признаком OEM перечитываются (`sync_needed`; запуск — `POST /api/sync {firms:[…]}` или `{books:[ids]}`). `proposal.books[]` — элементы вида `{slot,label,part,src,firm,period,date_from,date_to,role,divisor,oem,name,confidence,reason,warnings}`, `proposal.off[]` — `{src,firm,period,reasons[]}`.

**Совместимый слой старого API (поля прежние, новые добавлены).** `GET /api/years` — по элементу на метку с основной книгой: прежние поля + `book_id,date_from,date_to,parts:[{book_id,firm,date_from,date_to}]` (метка из двух книг — один элемент, `firm` = последняя часть). `PUT /api/years/{y}/book {firm}` — `make-main` в слоте метки (прежние ответы и ошибки). `GET /api/firms` — строка на книгу любой роли (+`role,book_id,src,date_from,date_to`) и источники скана без книг (`enabled:false`); `PUT /api/firms/{firm} {enabled}`. `GET /api/accounts?year=Y[&book=id]` — план метки (объединение основных книг метки, поздний главнее) или одной книги. `POST /api/sync {firms?:[src], books?:[id], force?}`. `Result.organization_firms[] = {firm,year,name,book,period}`, `Result.dataset = {id,name}`; `org_name` набора, если задан, заменяет строку `organization`.

**Расчёты.** Сегмент отчёта = основная книга, а не календарный год: метка и фирма могут быть любыми, период не обязан быть календарным годом. Входящее сальдо книги = açılış + проводки до начала книги (всегда; закрывающие ордера прошлого года входят) + проводки внутри книги до начала периода (с фильтрами). Для сегмента того же источника, что и предыдущий, корректировка переноса остатков не строится (карточка, главная книга), а `carry_gaps` сравнивает только соседние книги разных источников. `compare_years`: `mmdd_from/mmdd_to`, отличные от значений по умолчанию, допустимы только для календарных меток, иначе 400 «Для года 2026 (01.07.2025–30.06.2026) частичный период не поддерживается: оставьте весь год». Тексты о деноминации и заметки о разрывах переноса берутся из книг и параметров набора (`denomination`, `carry_notes`), коды валют в заголовках граф — из `currency_*`. Разбор кодов счетов — по `plan_profile` (разделитель, длина корня, классы), а не по точке. Образцы (`POST /api/demo/seed`) доступны только при `plan_profile.template = "turkish"` (иначе 400 «Образцы подготовлены для турецкого плана счетов»), годы образцов — две последние основные метки набора.


### Изменения бэкенда (волна 9: Универсальность, этап B — наборы данных)

Всё, что описано в волне 8, сохранено; ниже — только добавления. Для набора №1 без заголовка `X-Dataset` ответы совпадают с этапом A (в `Result` и `/api/status` добавлены только поля, перечисленные в конце раздела).

**Выбор набора.** Заголовок `X-Dataset: <id>` на **всех** `/api/*`; для ссылок, которые не умеют заголовок (скачивание шаблона, `window.open`, экспорт), — параметр `?ds=<id>`. Порядок: заголовок → параметр → набор по умолчанию. Не требуют набора: `/api/login|logout|me`, `/api/connections*`, `/api/users*`, `GET|POST /api/datasets`, `PUT|DELETE /api/datasets/{id}`. Ошибки (JSON `{"detail"}`):

| Код | Текст |
|---|---|
| 400 | «Некорректный номер набора данных» (заголовок не число) |
| 404 | «Набор данных не найден» |
| 409 | «Набор данных «{name}» в архиве: верните его из архива в настройках» |
| 409 | «Набор данных «{name}» ещё не настроен: откройте «Книги и годы»» — для расчётных маршрутов (`/api/run`, `/api/drill`, `/api/export/*`, `/api/fiche`, `/api/accounts`, `/api/centers`, `/api/dicts`, `/api/corr/*`, `/api/account-links/auto*`, `/api/demo/seed`) у набора с `state="new"`; чтение книг, параметров, мастер, классификаторы, отчёты, синхронизация доступны |
| 403 | «Нет доступа к набору данных «{name}»» |
| 403 | «Недостаточно прав: для этого действия нужна роль «…», у вас — «…»» (роль считается **в выбранном наборе**) |
| 401 | «Требуется вход в систему» |

**Состояние в памяти — по набору.** Выбор набора ставит `ContextVar` вокруг всего приложения (ASGI-middleware `auth.AccessMiddleware`); в потоки пула контекст копируется. Фоновые потоки (синхронизация, `refresh_plans`, `refresh_attrs_all`, скан мастера, перечитывание плана) получают набор явно (`dsctx.run_in`). `job`, `refresh`, замки по источникам, отпечатки/TTL, пинг Logo, состояние скана, кэш профиля плана лежат в `DatasetRuntime` набора. Новая таблица реестра не нужна — рантаймы строятся из `registry.datasets` лениво (первое обращение: каталог, миграции `settings.db`, схема `cache.db`, фоновое дочитывание плана/атрибутов этого набора). Набор №1 — файлы в корне `data\` (рантайм `dsctx.default()`), остальные — `data/ds/<id>/{settings.db, cache.db, templates/}`.

**Реестр: пользователи и права (`registry.db`, user_version 2).**
```sql
users(id INTEGER PRIMARY KEY, login TEXT UNIQUE, pass_hash TEXT, role TEXT)            -- role='admin' - администратор всей установки
user_datasets(user_id, dataset_id, role TEXT CHECK(role IN ('viewer','editor','admin')), PRIMARY KEY(user_id, dataset_id))
```
Миграция (единожды, `meta.legacy_users_copied`): пользователи `settings.db` набора №1 копируются **с теми же `id` и хэшами паролей** — cookie «session» (она подписана id и хэшем пароля) продолжает работать, входить заново не нужно; не-администраторы получают доступ к набору №1 с прежней ролью; перед копированием — резервная копия `data/backup/settings-before-users-ГГГГММДД-ЧЧММСС.db`; `settings.db.users` не изменяется (откат). Эффективная роль: `AUTH=0` и администратор установки — `admin` во всех наборах; иначе роль из `user_datasets`, нет строки — 403.

```
GET  /api/me     -> {login, role (в выбранном наборе), auth, global_admin, datasets:[{id,name,role,state,is_default}] (доступные, без архивных), dataset:{id,name}}
GET  /api/users  (администратор установки) -> [{id, login, role, datasets:[{id, role}]}]
POST /api/users  {login, password, role, datasets?:[{dataset_id, role}]}   не-администратор без datasets - доступ к набору по умолчанию с этой ролью
PUT  /api/users/{id}  {role?, password?, datasets?}   смена роли без datasets переносит её на все наборы пользователя
PUT  /api/users/{id}/datasets  [{dataset_id, role: viewer|editor|admin}]   полная замена прав; [] - доступа нет
     400 «Набор данных №N не найден», «Роль в наборе данных: viewer, editor или admin»
```
Маршруты установки (`/api/connections*`, `/api/users*`, `POST|PUT|DELETE /api/datasets*`) — только администратор установки (403 «Недостаточно прав…»). `GET /api/datasets` — любому вошедшему, но возвращает только доступные ему наборы (администратору — все, включая архивные).

**Наборы данных.**
```
GET    /api/datasets      -> [{id,name,org_name,is_default,archived,state,role,books_main,books_total,last_sync_at,logo_ok,
                               syncing,queued,progress:{done,total,message}, connection:{id,name,server,database} (только администратору)}]
POST   /api/datasets      {name, connection_id, org_name?, plan_template:"turkish"|"none", copy_from?:id, copy_parts?:["classifiers","reports","names","closing","links","params","templates"]}
       -> карточка набора (state="new", books_total=0)
PUT    /api/datasets/{id} {name?, org_name?, connection_id?, is_default?, archived?, note?}
DELETE /api/datasets/{id}?confirm=<название>      (или тело {"confirm": "<название>"}) -> {ok:true, moved_to:"data/ds/_deleted/<id>-<ГГГГММДД-ЧЧММСС>"}
```
Создание: строка в реестре, каталог `data/ds/<id>/`, пустые `settings.db`/`cache.db`; шаблон «turkish» записывает профиль плана «турецкий» и правило закрывающих ордеров по умолчанию, «none» — ничего. Книг нет: дальше мастер (`POST /api/books/scan` → `GET /api/books/proposal` → `POST /api/books/apply-proposal`) в контексте нового набора (заголовок `X-Dataset`). `copy_from` копирует выбранные части настроек другого набора (формат экспорта ниже, режим «заменить»). Ошибки: 400 «Укажите название набора данных», «Набор данных с названием «X» уже есть», «Подключение не найдено», «Укажите подключение к Logo для набора данных», «Шаблон плана счетов: «turkish» (турецкий план) или «none» (пустой)»; `PUT`: 400 «Набор данных по умолчанию нельзя перевести в архив: сначала назначьте набором по умолчанию другой», «Набор по умолчанию должен быть всегда: назначьте набором по умолчанию другой набор».
Удаление: только архивного (409 «Удалить можно только набор данных из архива: сначала переведите «X» в архив»); набор №1 нельзя (409 «Исходный набор данных удалить нельзя: переведите его в архив»); неверное подтверждение — 400 «Для удаления введите название набора данных «X»»; идёт синхронизация/скан — 409 «В наборе данных «X» идёт синхронизация или сканирование: дождитесь окончания». Файлы **не стираются**, а переносятся в `data/ds/_deleted/<id>-<дата>/`. `GET /api/dataset` и `GET /api/status` — по выбранному набору; `/api/status` получил `queued` (см. ниже).

**Экспорт/импорт настроек набора.**
```
GET  /api/dataset/export?parts=reports,classifiers,names,closing,links,params,templates -> файл buhlogo-settings-v1.json (Content-Disposition: attachment); роль editor
POST /api/dataset/import   multipart: file (json), mode = "merge" (добавить) | "replace" (заменить одноимённые), parts? (через запятую); роль admin набора
     -> {"created":{"reports":3,"classifiers":1,...}, "updated":{...}, "skipped":{...}, "warnings":[...], "resync_required":false, "plan_rebuild":false}
```
Файл: `{"format":"buhlogo-settings","version":1,"exported_at","dataset":{"name"},"parts":[...],"reports":[{name,type,folder,definition}],"classifiers":[{id,name,description,groups:[{id,parent_id,code,name,sort}],rules:[{group_id,kind,value,year_from,year_to,priority}]}],"account_names":[{code,year_from,year_to,name}],"closing_rules":[{kind,value,enabled}],"account_links":[{code_from,year_from,code_to,year_to,method,confidence,note}],"params":{ключи dataset_params},"templates":[{id,name,filename,data_base64}]}`. Без паролей, подключений, пользователей, разметки книг, ручной корреспонденции (`pair_overrides`), кэша. Идентификаторы классификаторов, групп и шаблонов при импорте **перенумеровываются**, ссылки в определениях отчётов (`rows[].id`, `filters.groups[].classifier|group`, `form.template_id`) переписываются. `merge`: одноимённый отчёт (в той же папке)/классификатор/шаблон получает суффикс «(2)», совпадающие названия счетов, связи, правила закрытия и уже заданные параметры пропускаются; `replace`: одноимённое заменяется (идентификатор сохраняется, у отчёта новая версия), правила закрытия заменяются целиком, параметры файла перезаписываются. Параметры `charset`/`plan_profile` дают `resync_required`/`plan_rebuild` (как `PUT /api/dataset/params`) и пояснение в `warnings`. Ошибки 400: «Файл настроек не распознан», «Версия файла N не поддерживается (ожидается 1)», «Режим импорта: «merge» (добавить) или «replace» (заменить одноимённые)», «Неизвестная часть настроек «X» (допустимо: …)»; отчёт со ссылкой на отсутствующий в файле классификатор/группу/шаблон не импортируется (`skipped.reports`, в `warnings`: «Отчёт «X»: классификатор №N не найден в файле - пропущен»).

**Ресурсы.** Одновременно — не более `[app] sync_parallel` (по умолчанию 1) полных загрузок источника **на весь процесс** (по всем наборам). Ожидающая синхронизация: `GET /api/status` → `queued:true`, `progress.message` = «Источник N: в очереди на загрузку»; то же `queued`/`syncing`/`progress` в `GET /api/datasets` (краткий статус каждого набора). Кэш SQLite открывается на вызов и закрывается сразу; для наборов, кроме №1, `PRAGMA cache_size` = 64 МБ вместо 200 МБ (`CACHE_PAGES_KB_OTHER`); рантайм набора (не №1) без запросов дольше `[app] idle_dataset_sec` (1800 с) и без синхронизации/скана закрывается в памяти (файлы не затрагиваются; следующий запрос создаёт его заново).

**Дефект `POST /api/run` с неверной структурой (исправлен).** `rows` (не список / не объекты), `filters` (не объект; `accounts|centers|groups|trcodes` не списки; `groups` не список объектов; `trcodes` не числа), `columns`, `options`, `options.sort`, `period` (не объекты), описание не объект — теперь 400 по-русски, например: «Строки отчёта (rows) должны быть списком вида [{"kind": "account", "max_level": 1}]», «Поле «период (period)» должно быть объектом вида {"date_from": …}», «Описание отчёта должно быть объектом»; последняя страховка — 400 «Описание отчёта задано неверно: проверьте, что строки, отбор, графы и период заданы в нужном виде». То же для `POST /api/export/xlsx`.

**Новые/изменённые поля в ответах (для набора №1 без `X-Dataset`).** `GET /api/status`: `+queued`; `GET /api/me`: `role` — в выбранном наборе, `+global_admin`, элементы `datasets[]` `+state,is_default`; `GET /api/datasets`: `+syncing,queued,progress`, `connection` — только администратору; `GET /api/users`: `+datasets`. Прочее не менялось.

#### Дополнения к волне 8 (по замечаниям исполнителя интерфейса)

1. **Статистика по всем источникам в `GET /api/books/proposal`** (данные скана, новых запросов к Logo нет). У каждого элемента `books[]`, `off[]`, `unassigned_data[]` добавлены поля: `firm_name, title, begdate, enddate` (период в справочнике Logo), `lines, open_lines, turn_lines, fiches`, `date_min, date_max` (все проводки), `turn_date_min, turn_date_max` (только обороты, без открытия), `turn_dt, turn_kt, open_dt, open_kt` (число с 2 знаками, без деноминации), `has_open` (есть открытие, TRCODE=1), `oem_rows`.
2. **`POST /api/connections/databases` и `/test`**: при переданном `id` значения, не заданные в теле, берутся из сохранённого подключения; **пароль не передан (`null`) или пуст при заданном `id` → берётся сохранённый пароль**; так же `driver`, `trust_cert`, `timeout_sec`. Без `id` пустой пароль — это пустой пароль.
3. **`POST /api/dataset/plan-profile/detect`** `{firms?:[src]}` — профиль плана по уже загруженным планам (таблица `accounts` кэша основных книг), без скана и без обращения к Logo → `{profile:{…PlanProfile…}, separator, root_len, mode, template, checked:{accounts, books, sources:[src]}, stats:{codes, sep_share, root_len_share, turkish_share}, books:[{src,label,firm,codes,separator,root_len,template,stats}], discrepancies:["Книга 2024 (источник 2): разделитель «-», длина корня 3 - отличается от общего («.», 3)", "Счетов без разделителя «.»: 12 из 400 (корневые счета)", …], current:{…текущий профиль…}, differs:bool}`; 409 «Планы счетов ещё не загружены: выполните «Сканировать Logo» или загрузите данные». Роль admin.
4. **Параметры в предложении.** Заданные в наборе параметры (есть запись в `dataset_params`) предложение **не подменяет**: `proposal.params[k]` = текущее значение, а предложение Logo — в новом поле `proposal.params_suggested[k] = {value, source, configured, current, differs}` (`source` — по-русски, откуда взято; для валюты — с пометкой «слот может быть переименован в Logo»). Для незаданных параметров `params[k]` = предложение, как раньше. `POST /api/books/apply-proposal {include_params?:true, overwrite?:["currency_local",…]}` записывает **только незаданные** параметры; заданный перезаписывается лишь при явном указании ключа в `overwrite`.
5. **Признак `oem`.** В `GET /api/books/proposal` и `apply-proposal` признак OEM книги не предлагается отличным от текущей разметки, если в источнике не найдено ни одной OEM-строки (`oem_rows = 0`: на данные он не влияет); у такой книги `oem_note` с пояснением. Данные и разметка набора №1 не менялись.
6. **`GET /api/connections/drivers`** → `{drivers:["ODBC Driver 17 for SQL Server", …], auto:"ODBC Driver 17 for SQL Server"}` (установленные драйверы SQL Server и тот, что выбирается автоматически; `auto:""` — драйвера нет). Роль admin.
7. **Все найденные источники для «Добавить книгу вручную».** `GET /api/books/proposal` → `sources[]`: по элементу на каждый источник скана (в т.ч. неразмеченные и «осиротевшие» таблицы): `{src, firm, period, in_logo, has_tables, has_plan, assigned (входит в предложение), can_be_book, cannot_reason, reasons[] (почему «не использовать»), …статистика п.1}`; `can_be_book=false` — нет таблиц проводок/ордеров, плана счетов фирмы или проводок. `PUT /api/books` и `POST /api/books/validate` принимают книгу на любой такой источник (проверяется наличие таблиц проводок, ордеров и плана: «Книга «…»: в Logo нет таблиц проводок фирмы N, период MM», «Книга «…»: в Logo нет плана счетов фирмы N»).
8. **Синхронизацию `PUT /api/books` и `POST /api/books/apply-proposal` сами не запускают** — это зафиксированное поведение: ответ содержит `sync_needed:[src…]` (источники новых основных книг и книг с изменённым признаком OEM; у них сброшен отпечаток), интерфейс вызывает `POST /api/sync {firms: sync_needed}` и опрашивает `GET /api/status` (`syncing`, `queued`, `progress`). Только `POST /api/books/{id}/make-main` и `PUT /api/years/{y}/book` запускают синхронизацию сами (`sync_started`).

### Изменения бэкенда (волна 10 — исправления по тестированию интерфейса)

1. **`GET /api/account-links`** — форма ответа не менялась и соответствует контракту волны 2B: плоский список `[{id, year_from, code_from, year_to, code_to, method, kind(=method), confidence, note}]` с параметрами `year_from, year_to, q, limit, offset`; общее число — заголовок `X-Total-Count`. Интерфейс принимает и `{total, items}` (сервер такой формы не отдаёт).
2. **Нормализация описания отчёта при чтении.** `GET /api/reports/{id}` и `GET /api/reports/{id}/versions` отдают `definition` с обязательными разделами `period, filters, columns, options, rows` (плюс `title, currency, scale, denominate, include_closing`); недостающие или неверного вида разделы заполняются значениями по умолчанию для типа отчёта (период — последний год основных книг набора; `rows` — для ОСВ `[{kind:"account",max_level:1}]`, иначе `[]`; `columns {balance:"net",by_month:false,show_usd:false}`). Сохранённые записи не переписываются. Примеры (`/api/demo/seed`) создаются уже с полным набором разделов.
3. **Проверка подключения** (`POST /api/connections/test`, `/databases`): вход выполняется с жёстким пределом ожидания `timeout_sec` (+1 с); при превышении — 400 «Сервер «X» не ответил за отведённое время…».
4. Кнопка в сообщениях сервера названа «Прочитать фирмы из Logo» (вместо «Сканировать Logo»); «сканирование» в сообщениях — «чтение фирм из Logo».
5. **ОСВ через границу годов**: контроль «Нач. сальдо + обороты = кон. сальдо по каждому счёту» сверяет расхождение по каждому счёту с известными разрывами переноса на стыках книг (закрытие прошлого года − открытие следующего; классы 6–7 и 590 в открытие не переносятся). Полностью объяснённое расхождение — `level: "warn"` с пояснением; `error` — только если часть расхождения не объясняется переносом («из них не объясняется переносом между годами: N»). Проверено: период 01.07.2025–30.06.2026, оборот (сумма сверена), 103 счёта с расхождением — `warn`.
6. Служебные слова (`rest=hide`, `account_links`, `options.…`, `begin/end/prev`, `check:…`, `#N`, «Селекторы…») в предупреждениях и ошибках заменены русскими формулировками; графы форм называются «на начало», «на конец», «за период», «за прошлый период», «на начало прошлого периода» (`form.COLUMN_NAMES_RU`).
7. **Excel.** (а) Адрес ячейки бланка проверяется на границы листа (столбцы до XFD, строки до 1 048 576) — иначе 400 с понятным текстом (в том числе при `/api/forms/validate`). (б) `POST /api/export/drill`: необязательное поле `title` (заголовок листа; по умолчанию «Расшифровка»); период в шапке `дд.мм.гггг – дд.мм.гггг`; заголовки первых двух колонок — «Дата» и «Содержание» (в результате выгрузки появились необязательные поля `code_title`, `name_title`; по умолчанию «Код» и «Наименование»); даты в колонке «Дата» — `дд.мм.гггг`. (в) `POST /api/export/xlsx` и выгрузка форм: необязательное поле верхнего уровня `title` — название сохранённого отчёта, подставляется в заголовок листа (определение — в `definition`). (г) При выгрузке в бланк включён пересчёт формул при открытии (`fullCalcOnLoad`).
8. **Сравнение лет**: `mmdd_from > mmdd_to` — 400 «Часть года задана неверно: начало … позже конца …» (переход через новый год не поддерживается).
9. **Уровни контролей**: «Разрывы переноса (сводка)» при наличии расхождений — `warn` (при их отсутствии `ok`); контроль формы «Актив = Пассив» при ненулевой разнице в пределах известного небаланса открытия — `warn` (раньше `ok` по допуску).
10. **Журнал-ордер** `journal.rest="hide"` без `fixed_cols` — поведение как `auto` и предупреждение «Скрытие остальных корреспондентов действует только при заданных графах бланка…» (не ошибка).
11. **Ошибки проверки запроса (FastAPI)** вместо 422 с английским текстом возвращаются как **400** `{detail: "<русский текст>"}` без названий полей (`POST /api/dataset/import` без файла — «Не выбран файл»).
12. **Импорт настроек, режим «заменить»**: счётчик `updated.closing` равен числу правил закрывающих ордеров файла (раньше при совпадающем наборе было 0/0/0).
13. **`/api/books/proposal`**: `params_suggested[k].differs` для незаданного параметра (`configured:false`) сравнивается со значением-заглушкой, т.е. `true`, если предложение отличается. `GET /api/users`: у каждого доступа `datasets[]` — `{id, dataset_id, name, role}` (`name` — название набора); так же в ответе `PUT /api/users/{id}/datasets`. `GET /api/books`: у книг добавлено `loaded` (данные загружены), `line_count` у незагруженной книги — `null`; в предложении у книг добавлено `loaded`, числовые поля статистики (`lines, open_lines, turn_lines, turn_dt, …, fiches, oem_rows`) — числа или `null`, но не пустая строка.
14. **Известная особенность данных:** в текстах ордеров вместо турецких букв встречается «?» (например, «numaral?» вместо «numaralı») — в самой LOGO_DB лежит байт 0x3F (проверено `CAST(GENEXP1 AS VARBINARY)`), т.е. это данные источника, при чтении и в кэше потерь нет; не исправляется.

### Изменения бэкенда (волна 11 — исправления по тестированию входа)

1. **Нет пользователей (`auth=1`).** `GET /api/me` без входа → **401** `{"detail":"Требуется вход в систему","no_users":true}` — поле `no_users` присутствует (и равно `true`) только если в реестре нет ни одного пользователя; иначе его нет. Больше ничего не раскрывается. `POST /api/login` при пустом списке → **401** `{"detail":"Пользователи ещё не заведены. Администратору сервера нужно задать первого администратора при запуске программы — см. раздел «Вход по паролю» в инструкции по установке","no_users":true}`. Интерфейс по `no_users` показывает это сообщение вместо формы.
2. **Подключение к Logo — только администратору установки.** `GET /api/dataset` (как и `GET /api/datasets`) отдаёт блок `connection` только администратору установки (и при `auth=0`); остальным его нет. `GET /api/status` и выгрузка настроек сведений о подключении не содержат (проверено).
3. **Тексты без английских названий ролей:** «Нельзя снять роль администратора у последнего администратора». Коды ролей в полях API (`viewer|editor|admin`) не менялись.
4. **Защита от перебора пароля** (память процесса, сбрасывается перезапуском): отдельно по паре (логин без учёта регистра, адрес клиента) и по адресу; после 5 неверных попыток подряд пауза 60 с, далее 120, 240, … не более 15 мин. Во время паузы `POST /api/login` → **429** `{"detail":"Слишком много неудачных попыток входа. Повторите через N мин."}` + заголовок `Retry-After` (секунды); пароль в паузу не проверяется. Успешный вход сбрасывает счётчики. Для несуществующего логина хэш считается впустую (время ответа не выдаёт существование логина).
5. **Логин без учёта регистра:** вход (`Admin` = `admin`, в том числе кириллица) и проверка дубля при создании пользователя (409 «Пользователь «…» уже существует»).
6. **Способы корреспонденции в текстах для человека** — по-русски: «по порядку», «по перекрёстной ссылке», «пропорционально», «вручную» (контроль «Доля методов корреспонденции…», метки строк карточки, предупреждения журнала, главной книги, анализа счёта, `note` сводки способов). Машинные коды `SEQ|CROSS|PROP|MANUAL` в полях API не менялись.
7. **Чтение фирм из Logo можно остановить:** `POST /api/books/scan/cancel` (администратор набора) → `{ok, running}`; чтение останавливается между источниками, состояние возвращается в `idle` с `message` «Чтение фирм из Logo остановлено», результат не сохраняется. Перевод набора в архив и попытка его удаления во время чтения также подают сигнал остановки (удаление по-прежнему отклоняется, пока чтение идёт: «дождитесь окончания»).
8. Документация: `docs/РУКОВОДСТВО.md` — раздел 16 «Вход по паролю».
