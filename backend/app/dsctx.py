"""Рантайм набора данных: состояние фоновых задач, замки, пути; выбор набора на запрос (ContextVar, ставит middleware в auth.py).
Набор №1 (path='' в реестре) - это рантайм `default()`: его пути не заданы и означают модульные store.DB_PATH / cache.DB_PATH (так работают тесты).
Остальные наборы (data/ds/<id>/) - рантаймы в `_runtimes`, их создаёт dsmgr.runtime_for(). ВСЁ изменяемое состояние (job, refresh, замки, отпечатки, ping,
scan, кэш профиля плана) лежит в рантайме; фоновые потоки не наследуют ContextVar - набор передаётся явно через run_in()."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional


def _new_job() -> dict:
    return {"syncing": False, "queued": False, "done": 0, "total": 0, "current": None, "message": "", "started_at": None,
            "finished_at": None, "errors": [], "results": []}


@dataclass
class DatasetRuntime:
    id: int = 1
    name: str = ""
    settings_path: Optional[Path] = None      # None = store.DB_PATH
    cache_path: Optional[Path] = None         # None = cache.DB_PATH
    templates_dir: Optional[Path] = None      # None = <DATA_DIR>/templates
    conn_id: Optional[int] = None             # None = подключение набора по умолчанию из реестра
    job: dict = field(default_factory=_new_job)
    job_lock: threading.Lock = field(default_factory=threading.Lock)
    refresh: dict = field(default_factory=lambda: {"active": False, "firm": None, "year": None, "message": "", "started_at": None})
    locks: Dict[int, threading.Lock] = field(default_factory=dict)
    locks_guard: threading.Lock = field(default_factory=threading.Lock)
    last_check: Dict[int, float] = field(default_factory=dict)
    ping: dict = field(default_factory=lambda: {"t": 0.0, "ok": False})
    scan: dict = field(default_factory=lambda: {"state": "idle", "done": 0, "total": 0, "current": "", "message": "", "scanned_at": None, "error": None})
    scan_lock: threading.Lock = field(default_factory=threading.Lock)
    books_ver: int = 0                        # растёт при записи книг/параметров
    plan_cache: Optional[tuple] = None        # (путь настроек, books_ver, PlanProfile) - кэш plan.current()
    cache_kb: Optional[int] = None            # PRAGMA cache_size (КБ) соединений кэша; None = config.CACHE_PAGES_KB
    ready: bool = False                       # миграции settings.db / cache.db выполнены (dsmgr.ensure_ready)
    init_lock: threading.Lock = field(default_factory=threading.Lock)
    last_used: float = 0.0                    # time.time() последнего запроса (для закрытия простаивающих наборов)

    def lock(self, src: int) -> threading.Lock:
        with self.locks_guard:
            return self.locks.setdefault(src, threading.Lock())


_default = DatasetRuntime(ready=True)
_current: ContextVar = ContextVar("dataset", default=None)
_runtimes: Dict[int, DatasetRuntime] = {}      # наборы не по умолчанию (path != '')
_rt_guard = threading.Lock()


def default() -> DatasetRuntime:
    return _default


def current() -> DatasetRuntime:
    return _current.get() or _default


@contextmanager
def use(rt: DatasetRuntime):
    tok = _current.set(rt)
    try:
        yield rt
    finally:
        _current.reset(tok)


def run_in(rt: DatasetRuntime, fn: Callable, *a, **kw):
    """Для фоновых потоков (они не наследуют contextvars): with use(rt): fn(*a)."""
    with use(rt):
        return fn(*a, **kw)


# ---- рантаймы наборов не из корня data\ -----------------------------------------------------------------

def get_runtime(ds_id: int) -> Optional[DatasetRuntime]:
    with _rt_guard:
        return _runtimes.get(ds_id)


def put_runtime(rt: DatasetRuntime) -> DatasetRuntime:
    with _rt_guard:
        return _runtimes.setdefault(rt.id, rt)


def forget(ds_id: int) -> None:
    with _rt_guard:
        _runtimes.pop(ds_id, None)


def all_runtimes() -> list:
    with _rt_guard:
        return [_default] + list(_runtimes.values())


def busy(rt: DatasetRuntime) -> bool:
    return bool(rt.job.get("syncing") or rt.scan.get("state") == "running" or rt.refresh.get("active"))


def evict_idle(max_idle: float, now: Optional[float] = None) -> list:
    """Закрывает (забывает) простаивающие наборы не по умолчанию: без синхронизации/сканирования и без запросов дольше max_idle секунд.
    Соединения SQLite открываются на вызов и закрываются сразу, поэтому освобождаются кэш профиля плана и таблицы состояния;
    следующий запрос создаст рантайм заново (файлы наборов не затрагиваются). Возвращает идентификаторы закрытых наборов."""
    import time as _t
    now = now or _t.time()
    out = []
    with _rt_guard:
        for did, rt in list(_runtimes.items()):
            if not busy(rt) and rt.last_used and now - rt.last_used > max_idle:
                _runtimes.pop(did, None)
                out.append(did)
    return out


# ---- общее ограничение числа одновременных полных синхронизаций (набор × источник) ----------------------------------

_sync_sem: Optional[threading.Semaphore] = None
_sync_limit = 0
_sync_guard = threading.Lock()


def set_sync_limit(n: int) -> None:
    global _sync_sem, _sync_limit
    with _sync_guard:
        _sync_limit = max(1, int(n))
        _sync_sem = threading.Semaphore(_sync_limit)


def sync_slot() -> threading.Semaphore:
    if _sync_sem is None:
        from . import config
        set_sync_limit(getattr(config, "SYNC_PARALLEL", 1))
    return _sync_sem            # type: ignore


@contextmanager
def sync_turn(rt: DatasetRuntime, on_wait: Optional[Callable] = None):
    """Место в очереди полных синхронизаций. Пока ждём - rt.job['queued']=True (интерфейс показывает «в очереди»)."""
    sem = sync_slot()
    if not sem.acquire(False):
        rt.job["queued"] = True
        if on_wait:
            on_wait()
        sem.acquire()
        rt.job["queued"] = False
    try:
        yield
    finally:
        sem.release()
