"""Заглушка прежней карты «год -> фирма»: в открытой версии особой миграции существующей установки нет.
Разметку книг и параметры набора создаёт мастер «Книги и годы». Публичные имена совпадают с рабочим модулем."""
from __future__ import annotations

LEGACY_YEAR_FIRM: dict = {}
LEGACY_ALT_YEAR_FIRM: dict = {}
LEGACY_DENOMINATION: list = []
LEGACY_OEM_BEFORE_YEAR = 0      # ни один год не считается «старым» (OEM-тексты определяет мастер)
LEGACY_CARRY_NOTES: list = []
LEGACY_PARAMS = {
    "currency_local": {"code": "TMT", "name": "манат", "symbol": "m", "gen_pl": "манатов", "prep_pl": "манатах"},
    "currency_report": {"code": "USD", "name": "доллар США", "enabled": True},
    "charset": {"ansi": "cp1254", "oem": "cp857"},
    "denomination": LEGACY_DENOMINATION,
    "carry_notes": LEGACY_CARRY_NOTES,
    "sync": {"ttl_current": 60, "ttl_old": 86400, "old_after_days": 400},
}
# правило закрывающих ордеров по умолчанию для шаблона «турецкий план» (общедоступный план счетов)
TURKISH_CLOSING_ACCOUNTS = "570*,580*,590*,591*,690*,691*,692*"
LEGACY_DATASET_NAME = "__нет_прежней_установки__"
