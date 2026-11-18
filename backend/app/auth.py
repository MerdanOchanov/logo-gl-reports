"""Авторизация (включается AUTH=1; по умолчанию выключена — все запросы выполняются от имени admin) и выбор набора данных на запрос.

Роли: viewer — смотреть и формировать отчёты; editor — ещё сохранять отчёты, править классификаторы, связи счетов, шаблоны;
admin — ещё настройки (фирмы, синхронизация, закрывающие ордера, названия счетов, демо-данные). Этап B: пользователи и их пароли - общие на
установку (registry.db); роль admin в таблице пользователей - администратор всей установки (все наборы, подключения, наборы, пользователи);
остальные получают права на наборы по отдельности (registry.user_datasets: нет строки - нет доступа, иначе роль viewer|editor|admin в наборе).
Пароли — PBKDF2-HMAC-SHA256; сессия — подписанная httpOnly-cookie «session» (без хранения на сервере; смена пароля/роли
учитывается при каждом запросе). Первый admin создаётся из ADMIN_LOGIN / ADMIN_PASSWORD при первом запуске с AUTH=1.

Проверка прав и выбор набора — ASGI-middleware AccessMiddleware (покрывает все маршруты /api/*, в т.ч. будущие) по таблице required_role():
набор берётся из заголовка X-Dataset, затем из параметра ?ds=, затем набор по умолчанию, и ставится в ContextVar (dsctx.use) на время запроса —
в потоки пула контекст копируется, фоновым потокам набор передаётся явно. Для явного использования в маршрутах есть зависимость require_role("editor")."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import config, dsctx, dsmgr, registry

ROLES = ("viewer", "editor", "admin")
ROLE_RU = {"viewer": "просмотр", "editor": "редактор", "admin": "администратор"}
RANK = {r: i for i, r in enumerate(ROLES)}
COOKIE = "session"
SESSION_TTL = 12 * 3600
PBKDF2_ITERS = 200_000

router = APIRouter(prefix="/api")


def enabled() -> bool:
    return bool(config.AUTH)


# ---------------------------------------------------------------------------------------------
# пароли и подпись сессии
# ---------------------------------------------------------------------------------------------

def hash_password(password: str, iters: int = PBKDF2_ITERS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return f"pbkdf2_sha256${iters}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algo, iters, salt, dk = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


_secret_cache: dict[str, bytes] = {}


def _secret() -> bytes:
    env = os.environ.get("SECRET_KEY")
    if env:
        return env.encode("utf-8")
    path = config.DATA_DIR / ".secret"
    key = str(path)
    if key not in _secret_cache:
        if path.exists():
            _secret_cache[key] = path.read_bytes()
        else:
            data = secrets.token_bytes(32)
            path.write_bytes(data)
            _secret_cache[key] = data
    return _secret_cache[key]


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_token(user: dict) -> str:
    body = json.dumps({"id": user["id"], "exp": int(time.time()) + SESSION_TTL,
                       "pv": hashlib.sha256((user["pass_hash"] or "").encode()).hexdigest()[:12]}, separators=(",", ":"))
    b = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{b}.{_sign(b)}"


def read_token(token: str | None) -> dict | None:
    """Токен → пользователь {id, login, role} или None (подпись/срок/смена пароля)."""
    if not token or "." not in token:
        return None
    b, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(b)):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
    except Exception:
        return None
    if body.get("exp", 0) < time.time():
        return None
    u = get_user(int(body.get("id", 0)))
    if not u or hashlib.sha256((u["pass_hash"] or "").encode()).hexdigest()[:12] != body.get("pv"):
        return None
    return {"id": u["id"], "login": u["login"], "role": u["role"]}




# ---------------------------------------------------------------------------------------------
# пользователи (общие на установку: registry.db)
# ---------------------------------------------------------------------------------------------

def get_user(uid: int) -> dict | None:
    return registry.get_user(uid)


def get_user_by_login(login: str) -> dict | None:
    return registry.get_user_by_login(login)


def list_users() -> list[dict]:
    return registry.list_users()


def _check_login(login: str) -> str:
    login = (login or "").strip()
    if not re.fullmatch(r"[\w.@\-]{2,64}", login):
        raise HTTPException(400, "Логин: 2–64 символа (буквы, цифры, . _ - @)")
    return login


def _check_password(pw: str) -> str:
    if not isinstance(pw, str) or len(pw) < 6:
        raise HTTPException(400, "Пароль должен быть не короче 6 символов")
    return pw


def _check_role(role: str) -> str:
    if role not in ROLES:
        raise HTTPException(400, "Роль должна быть: просмотр, редактор или администратор")
    return role


def create_user(login: str, password: str, role: str, datasets: list | None = None) -> dict:
    """datasets: [{dataset_id, role}] - права на наборы (не-администратору); не передано - доступ к набору по умолчанию с ролью пользователя."""
    login, password, role = _check_login(login), _check_password(password), _check_role(role)
    import sqlite3
    try:
        uid = registry.insert_user(login, hash_password(password), role)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Пользователь «{login}» уже существует")
    if role != "admin":
        try:
            if datasets is None:
                registry.grant_default(uid, role)
            else:
                registry.set_user_grants(uid, datasets)
        except ValueError as e:
            registry.delete_user(uid)
            raise HTTPException(400, str(e))
    return {"id": uid, "login": login, "role": role}


_bootstrapped: set[str] = set()


def ensure_bootstrap() -> None:
    """Первый admin из ADMIN_LOGIN/ADMIN_PASSWORD (только если пользователей нет)."""
    key = str(registry.path())
    if key in _bootstrapped:
        return
    if registry.count_users() == 0:
        login, pw = os.environ.get("ADMIN_LOGIN"), os.environ.get("ADMIN_PASSWORD")
        if login and pw:
            create_user(login, pw, "admin")
    _bootstrapped.add(key)


# ---------------------------------------------------------------------------------------------
# политика доступа
# ---------------------------------------------------------------------------------------------

PUBLIC = {("POST", "/api/login"), ("POST", "/api/logout"), ("GET", "/api/me")}
# POST-запросы «только посмотреть / сформировать» — доступны viewer
VIEWER_POST = [r"^/api/run$", r"^/api/export/xlsx$", r"^/api/export/drill$", r"^/api/drill$", r"^/api/forms/validate$", r"^/api/account-links/auto$"]
ADMIN_ANY = [r"^/api/users(/.*)?$", r"^/api/sync$", r"^/api/books(/.*)?$", r"^/api/datasets(/.*)?$", r"^/api/dataset/params$", r"^/api/dataset/import$",
             r"^/api/firms(/.*)?$", r"^/api/years/\d+/book$", r"^/api/closing-rules$", r"^/api/account-names$", r"^/api/demo/seed$"]
EDITOR_ANY = [r"^/api/reports(/.*)?$", r"^/api/classifiers(/.*)?$", r"^/api/templates(/.*)?$", r"^/api/account-links(/.*)?$",
              r"^/api/corr/override$"]
EDITOR_GET = [r"^/api/dataset/export$"]
# маршруты уровня установки: нужна роль admin в таблице пользователей (не роль в наборе); набор для них не выбирается
GLOBAL_ADMIN = [r"^/api/users(/.*)?$", r"^/api/connections(/.*)?$"]
GLOBAL_ADMIN_WRITE = [r"^/api/datasets(/.*)?$"]       # создание, переименование, архив, удаление, набор по умолчанию (список GET - любому)
NO_DATASET = GLOBAL_ADMIN + [r"^/api/(login|logout|me)$", r"^/api/datasets(/\d+)?$"]
# расчётные маршруты: у ненастроенного набора (state='new', ещё нет книг) недоступны
COMPUTE = [r"^/api/(run|drill|fiche|accounts|centers|dicts|demo/seed)$", r"^/api/export/(xlsx|drill)$", r"^/api/corr(/.*)?$", r"^/api/account-links/auto(/apply)?$"]


def required_role(method: str, path: str) -> str | None:
    """Минимальная роль для (метод, путь); None — без входа (публичные)."""
    path = path.rstrip("/") or "/"
    if (method, path) in PUBLIC:
        return None
    if method in ("GET", "HEAD", "OPTIONS"):
        # чтение настроек, пользователей и т.п. — только то, что не раскрывает чужих учётных данных
        if re.match(r"^/api/users(/.*)?$", path) or re.match(r"^/api/connections(/.*)?$", path):
            return "admin"
        if any(re.match(p, path) for p in EDITOR_GET):
            return "editor"
        return "viewer"
    if any(re.match(p, path) for p in VIEWER_POST):
        return "viewer"
    if any(re.match(p, path) for p in ADMIN_ANY):
        return "admin"
    if any(re.match(p, path) for p in EDITOR_ANY):
        return "editor"
    return "admin"       # неизвестные изменяющие маршруты — по умолчанию только admin


def is_global_admin_route(method: str, path: str) -> bool:
    path = path.rstrip("/") or "/"
    if any(re.match(p, path) for p in GLOBAL_ADMIN):
        return True
    return method not in ("GET", "HEAD", "OPTIONS") and any(re.match(p, path) for p in GLOBAL_ADMIN_WRITE)


def current_user(request: Request) -> dict | None:
    if not enabled():
        return {"id": 0, "login": "admin", "role": "admin"}
    ensure_bootstrap()
    return read_token(request.cookies.get(COOKIE))


def dataset_role(user: dict, ds: dict) -> str | None:
    """Роль пользователя в наборе: AUTH=0 и глобальный admin - admin; иначе registry.user_datasets (нет строки - None)."""
    if not enabled() or user["role"] == "admin":
        return "admin"
    return registry.user_grants(user["id"]).get(ds["id"])


def accessible_datasets(user: dict) -> list[dict]:
    """[(строка набора, роль)] доступных пользователю наборов (архивные - только глобальному администратору)."""
    out = []
    grants = None if (not enabled() or user["role"] == "admin") else registry.user_grants(user["id"])
    for d in registry.list_datasets():
        role = "admin" if grants is None else grants.get(d["id"])
        if role is None or (d["archived"] and grants is not None):
            continue
        out.append({"ds": d, "role": role})
    return out


def requested_dataset_id(request: Request) -> int | None:
    """X-Dataset -> ?ds= -> None (набор по умолчанию)."""
    raw = request.headers.get("x-dataset")
    if raw in (None, ""):
        raw = request.query_params.get("ds")
    if raw in (None, ""):
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        raise dsmgr.DatasetError(400, "Некорректный номер набора данных")


def _deny_role(need: str, have: str) -> JSONResponse:
    return JSONResponse({"detail": f"Недостаточно прав: для этого действия нужна роль «{ROLE_RU[need]}», у вас — «{ROLE_RU[have]}»"}, status_code=403)


def decide(request: Request):
    """Решение по запросу: (rt | None, ds_row | None, user | None, role | None, ошибка-ответ | None). Синхронная (работает в пуле потоков)."""
    method, path = request.method, request.url.path
    need = required_role(method, path)
    user = None
    if need is None:                         # login / logout / me: без проверки; me сам разбирает вход и набор
        if (method, path.rstrip("/")) == ("GET", "/api/me"):
            user = current_user(request)
        return None, None, user, None, None
    user = current_user(request)
    if user is None:
        return None, None, None, None, JSONResponse({"detail": "Требуется вход в систему"}, status_code=401)
    if is_global_admin_route(method, path):
        if user["role"] != "admin":
            return None, None, user, None, _deny_role("admin", user["role"])
        return None, None, user, "admin", None
    if path.rstrip("/") == "/api/datasets" and method == "GET":
        return None, None, user, None, None          # список наборов фильтрует сам обработчик
    try:
        ds_id = requested_dataset_id(request)
        rt, row = dsmgr.resolve(ds_id)
    except dsmgr.DatasetError as e:
        return None, None, user, None, JSONResponse({"detail": e.detail}, status_code=e.status)
    dsmgr.sweep()
    role = dataset_role(user, row)
    if role is None:
        return None, None, user, None, JSONResponse({"detail": f"Нет доступа к набору данных «{row['name']}»"}, status_code=403)
    if RANK[role] < RANK[need]:
        return None, None, user, role, _deny_role(need, role)
    if row["state"] == "new" and any(re.match(p, path) for p in COMPUTE):
        return None, None, user, role, JSONResponse({"detail": f"Набор данных «{row['name']}» ещё не настроен: откройте «Книги и годы»"}, status_code=409)
    return rt, row, user, role, None


class AccessMiddleware:
    """Чистый ASGI (без BaseHTTPMiddleware): ContextVar набора ставится вокруг всего приложения, поэтому его видят и обработчики, и потоки пула."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api"):
            return await self.app(scope, receive, send)
        request = Request(scope)
        rt, row, user, role, error = await run_in_threadpool(decide, request)
        if error is not None:
            return await error(scope, receive, send)
        st = scope.setdefault("state", {})
        st["user"], st["dataset"], st["dataset_role"] = user, row, role
        if rt is None:
            return await self.app(scope, receive, send)
        with dsctx.use(rt):
            return await self.app(scope, receive, send)


AuthMiddleware = AccessMiddleware      # прежнее имя


def require_role(role: str):
    """Зависимость для явной защиты маршрута: Depends(require_role('editor'))."""
    def dep(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(401, "Требуется вход в систему")
        have = getattr(request.state, "dataset_role", None) or user["role"]
        if RANK[have] < RANK[role]:
            raise HTTPException(403, f"Недостаточно прав: для этого действия нужна роль «{ROLE_RU[role]}», у вас — «{ROLE_RU[have]}»")
        return user
    return dep


# ---------------------------------------------------------------------------------------------
# маршруты
# ---------------------------------------------------------------------------------------------

NO_USERS_TEXT = ("Пользователи ещё не заведены. Администратору сервера нужно задать первого администратора при запуске программы — "
                 "см. раздел «Вход по паролю» в инструкции по установке")

# защита от перебора пароля: в памяти процесса; ключи — (логин без регистра, адрес) и адрес
_FAIL_FREE = 5            # неудачных попыток подряд без паузы
_LOCK_FIRST = 60          # пауза после 5-й неудачи, сек; дальше удваивается
_LOCK_MAX = 15 * 60
_fails: dict = {}         # ключ -> [число неудач подряд, заблокировано до (monotonic)]
_fails_lock = threading.Lock()
_DUMMY_HASH = None


def reset_throttle() -> None:
    with _fails_lock:
        _fails.clear()


def _client_addr(request: Request) -> str:
    return request.client.host if request.client else "?"


def _throttle_keys(login: str, addr: str) -> list:
    return [("pair", login.casefold(), addr), ("addr", addr)]


def _locked_for(keys: list) -> int:
    """Сколько секунд ещё ждать (0 — можно пробовать)."""
    now = time.monotonic()
    with _fails_lock:
        return max([int(v[1] - now) + 1 for k in keys for v in [_fails.get(k)] if v and v[1] > now] or [0])


def _register_fail(keys: list) -> None:
    now = time.monotonic()
    with _fails_lock:
        for k in keys:
            rec = _fails.setdefault(k, [0, 0.0])
            rec[0] += 1
            if rec[0] >= _FAIL_FREE:
                rec[1] = now + min(_LOCK_MAX, _LOCK_FIRST * 2 ** (rec[0] - _FAIL_FREE))
        if len(_fails) > 5000:       # не растём без предела
            for k in [k for k, v in _fails.items() if v[1] < now][:2500]:
                _fails.pop(k, None)


def _register_ok(keys: list) -> None:
    with _fails_lock:
        for k in keys:
            _fails.pop(k, None)


@router.post("/login")
def login(request: Request, response: Response, body: dict = Body(...)):
    global _DUMMY_HASH
    if not enabled():
        return {"login": "admin", "role": "admin", "auth": False}
    ensure_bootstrap()
    if registry.count_users() == 0:
        return JSONResponse({"detail": NO_USERS_TEXT, "no_users": True}, status_code=401)
    name = str(body.get("login") or "").strip()
    keys = _throttle_keys(name, _client_addr(request))
    wait = _locked_for(keys)
    if wait:
        mins = max(1, -(-wait // 60))
        return JSONResponse({"detail": f"Слишком много неудачных попыток входа. Повторите через {mins} мин."}, status_code=429,
                            headers={"Retry-After": str(wait)})
    u = get_user_by_login(name)
    if u is None:                    # несуществующий логин: считаем хэш впустую, чтобы время ответа не выдавало логин
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password("dummy-password")
        verify_password(str(body.get("password") or ""), _DUMMY_HASH)
        ok = False
    else:
        ok = verify_password(str(body.get("password") or ""), u["pass_hash"])
    if not ok:
        _register_fail(keys)
        raise HTTPException(401, "Неверный логин или пароль")
    _register_ok(keys)
    response.set_cookie(COOKIE, make_token(u), httponly=True, samesite="lax", max_age=SESSION_TTL, path="/")
    return {"login": u["login"], "role": u["role"], "auth": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    """{login, role (в выбранном наборе), auth, global_admin, datasets:[{id,name,role}] (доступные, без архивных), dataset:{id,name}} - выбранный набор:
    X-Dataset/?ds=, если он доступен, иначе набор по умолчанию, иначе первый доступный."""
    u = getattr(request.state, "user", None) or current_user(request)
    if u is None:
        out = {"detail": "Требуется вход в систему"}
        try:
            if enabled() and registry.count_users() == 0:
                out["no_users"] = True
        except Exception:
            pass
        return JSONResponse(out, status_code=401)
    try:
        acc = [a for a in accessible_datasets(u) if not a["ds"]["archived"]]
        want = requested_dataset_id(request)
    except dsmgr.DatasetError as e:
        raise HTTPException(e.status, e.detail)
    except Exception:
        acc, want = [], None
    chosen = next((a for a in acc if a["ds"]["id"] == want), None) or next((a for a in acc if a["ds"]["is_default"]), None) or (acc[0] if acc else None)
    out = {"login": u["login"], "role": chosen["role"] if chosen else ("admin" if u["role"] == "admin" else "viewer"), "auth": enabled(),
           "global_admin": u["role"] == "admin",
           "datasets": [{"id": a["ds"]["id"], "name": a["ds"]["name"], "role": a["role"], "state": a["ds"]["state"], "is_default": bool(a["ds"]["is_default"])} for a in acc]}
    if chosen:
        out["dataset"] = {"id": chosen["ds"]["id"], "name": chosen["ds"]["name"]}
    return out


@router.get("/users")
def users_list():
    return list_users()


@router.post("/users")
def users_create(body: dict = Body(...)):
    ds = body.get("datasets")
    if ds is not None and not isinstance(ds, list):
        raise HTTPException(400, "Права на наборы данных заданы неверно: ожидается список, где у каждой записи указан набор и роль")
    return create_user(body.get("login"), body.get("password"), body.get("role") or "viewer", ds)


@router.put("/users/{uid}")
def users_update(uid: int, request: Request, body: dict = Body(...)):
    u = get_user(uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    role = _check_role(body["role"]) if "role" in body else u["role"]
    ph = hash_password(_check_password(body["password"])) if body.get("password") else u["pass_hash"]
    if u["role"] == "admin" and role != "admin" and _admin_count() <= 1:
        raise HTTPException(400, "Нельзя снять роль администратора у последнего администратора")
    registry.update_user(uid, role, ph)
    try:
        if isinstance(body.get("datasets"), list):
            registry.set_user_grants(uid, body["datasets"])
        elif role != "admin" and ("role" in body):
            grants = registry.user_grants(uid)       # роль без явных прав: переносится на все наборы пользователя (или набор по умолчанию)
            if grants:
                registry.set_user_grants(uid, [{"dataset_id": d, "role": role} for d in grants])
            else:
                registry.grant_default(uid, role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": uid, "login": u["login"], "role": role}


@router.put("/users/{uid}/datasets")
def users_datasets(uid: int, body: list = Body(...)):
    """Права пользователя на наборы: [{dataset_id, role: viewer|editor|admin}]; пустой список - доступа нет. Администратору установки не нужны."""
    u = get_user(uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    try:
        grants = registry.set_user_grants(uid, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": uid, "login": u["login"], "role": u["role"], "datasets": grants}


@router.delete("/users/{uid}")
def users_delete(uid: int, request: Request):
    u = get_user(uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    cur = getattr(request.state, "user", None) or current_user(request)
    if cur and cur.get("id") == uid and enabled():
        raise HTTPException(400, "Нельзя удалить самого себя")
    if u["role"] == "admin" and _admin_count() <= 1:
        raise HTTPException(400, "Нельзя удалить последнего администратора")
    registry.delete_user(uid)
    return {"ok": True}


def _admin_count() -> int:
    return registry.count_admins()
