"""Запуск сервера: `python -m app` из папки backend. Порт и прочие настройки - переменные окружения или config.ini (см. config.py)."""
from __future__ import annotations

import uvicorn

from . import config

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=config.PORT)
