"""Конфигурация: переменные окружения (приоритет) -> файл config.ini -> значения по умолчанию (подключение к Logo по умолчанию пустое: приложение запускается и просит настроить подключение).

Файл настроек ищется так: переменная CONFIG_FILE, иначе `config.ini` в корне программы, иначе `data/config.ini`.
Разделы: [logo] server, user, password, database, driver; [app] port, auth, data_dir. Образец — config.ini.example."""
from __future__ import annotations

import configparser
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_ini() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cands = [os.environ.get("CONFIG_FILE") or "", str(ROOT / "config.ini"), str(ROOT / "data" / "config.ini")]
    for c in cands:
        if c and os.path.isfile(c):
            try:
                cp.read(c, encoding="utf-8-sig")
            except (configparser.Error, UnicodeDecodeError):
                raise RuntimeError("Файл настроек %s повреждён: проверьте формат (разделы [logo] и [app], строки вида ключ = значение)" % c)
            break
    return cp


_INI = _load_ini()


def _opt(env: str, section: str, key: str, default: str) -> str:
    v = os.environ.get(env)
    if v is not None and v != "":
        return v
    v = _INI.get(section, key, fallback=None)
    if v is not None and v.strip() != "":
        return v.strip()
    return default


LOGO_SERVER = _opt("LOGO_SERVER", "logo", "server", "")
LOGO_USER = _opt("LOGO_USER", "logo", "user", "")
LOGO_PASSWORD = _opt("LOGO_PASSWORD", "logo", "password", "")
LOGO_DB = _opt("LOGO_DB", "logo", "database", "LOGO_DB")
LOGO_DRIVER = _opt("LOGO_DRIVER", "logo", "driver", "")   # пусто = автоподбор из установленных (logo.pick_driver)

DATA_DIR = Path(_opt("DATA_DIR", "app", "data_dir", str(ROOT / "data")))
PORT = int(_opt("PORT", "app", "port", "8790"))
AUTH = _opt("AUTH", "app", "auth", "0").lower() in ("1", "true", "yes", "on")
FRONTEND_DIST = ROOT / "frontend" / "dist"

FINGERPRINT_TTL = 60              # секунд: значение по умолчанию и нижняя граница паузы между проверками отпечатка «текущих» книг (параметры набора sync)
FINGERPRINT_TTL_OLD = 86400       # то же для старых книг (раз в сутки или по кнопке синхронизации)
CACHE_PAGES_KB = 200000           # PRAGMA cache_size страниц кэша (КБ) на соединение (набор по умолчанию)
CACHE_PAGES_KB_OTHER = 65536      # то же для остальных наборов (несколько наборов не должны занимать память втрое)
SYNC_PARALLEL = max(1, int(_opt("SYNC_PARALLEL", "app", "sync_parallel", "1")))     # сколько полных синхронизаций одновременно (по всем наборам)
IDLE_DATASET_SEC = int(_opt("IDLE_DATASET_SEC", "app", "idle_dataset_sec", "1800"))  # через сколько секунд простоя набор (не по умолчанию) закрывается в памяти

DATA_DIR.mkdir(parents=True, exist_ok=True)
