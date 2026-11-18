"""API подключения, набора данных и мастера разметки книг (docs/ARCHITECTURE.md «Изменения», волна 8)."""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import auth, books, cache, charset, dsctx, dsmgr, dsparams, logo, registry, secretbox, settings_io, store, wizard

router = APIRouter(prefix="/api")


def err(status: int, text: str):
    raise HTTPException(status, text)


# ---------------------------------------------------------------------------------------------
# подключения
# ---------------------------------------------------------------------------------------------

@router.get("/connections")
def connections_list():
    return registry.list_connections()


@router.post("/connections")
def connections_create(body: dict = Body(...)):
    try:
        return registry.create_connection(body)
    except ValueError as e:
        err(400, str(e))


@router.put("/connections/{cid}")
def connections_update(cid: int, body: dict = Body(...)):
    try:
        return registry.update_connection(cid, body)
    except KeyError as e:
        err(404, str(e.args[0]))
    except ValueError as e:
        err(400, str(e))


@router.delete("/connections/{cid}")
def connections_delete(cid: int):
    try:
        registry.delete_connection(cid)
    except KeyError as e:
        err(404, str(e.args[0]))
    except ValueError as e:
        err(409, str(e))
    return {"ok": True}


def _lc_from_body(body: dict) -> logo.LogoConn:
    """Параметры из тела запроса; password не передан - из сохранённого подключения id (если задан)."""
    saved = None
    if body.get("id"):
        saved = registry.logo_conn(int(body["id"]))
    server = str(body.get("server") or (saved.server if saved else "")).strip()
    database = str(body.get("database") or (saved.database if saved else "")).strip()
    login = str(body.get("login") or (saved.login if saved else "")).strip()
    if not server or not login:
        err(400, "Укажите сервер и логин подключения")
    pwd = body.get("password")
    if pwd is None or (pwd == "" and saved is not None):      # пароль не передан (или пуст) и указан id: берётся сохранённый пароль подключения
        pwd = saved.password if saved else ""
    trust = body.get("trust_cert")
    trust = (saved.trust_cert if saved else True) if trust is None else bool(trust)
    drv = body.get("driver")
    drv = (saved.driver if saved else "") if drv in (None, "") else drv
    return logo.LogoConn(server, database or "master", login, str(pwd), str(drv), trust, int(body.get("timeout_sec") or (saved.timeout if saved else 10)))


def _connect_limited(lc: logo.LogoConn):
    """Вход в SQL Server с жёстким пределом ожидания: ответ не позже заданного таймаута + 1 с, даже если драйвер зависает на недоступном адресе.
    Запоздавшее соединение закрывается в фоне."""
    limit = max(1, int(lc.timeout or 10))
    box: dict = {}
    abandoned = threading.Event()

    def work():
        try:
            c = logo.connect(lc=lc)
        except Exception as e:      # noqa: BLE001
            box["err"] = e
            return
        if abandoned.is_set():
            try:
                c.close()
            except Exception:
                pass
        else:
            box["conn"] = c
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(limit + 1)
    if t.is_alive():
        abandoned.set()
        if "conn" in box:
            try:
                box["conn"].close()
            except Exception:
                pass
        raise TimeoutError("Сервер не ответил за %d с" % limit)
    if "err" in box:
        raise box["err"]
    return box["conn"]


def _short_error(e: Exception, server: str) -> str:
    msg = str(e)
    if isinstance(e, TimeoutError):
        return f"Сервер «{server}» не ответил за отведённое время: проверьте адрес, порт и доступность сервера"
    if "драйвер ODBC" in msg:
        return msg
    return f"Не удалось подключиться к серверу «{server}»: проверьте адрес, имя базы, логин и пароль"


@router.get("/connections/drivers")
def connections_drivers():
    """Установленные драйверы ODBC для SQL Server (для выпадающего списка) и тот, что подберётся автоматически."""
    import pyodbc
    have = [d for d in pyodbc.drivers() if "sql server" in d.lower()]
    try:
        auto = logo.pick_driver("")
    except Exception:
        auto = ""
    return {"drivers": sorted(have), "auto": auto}


@router.post("/connections/test")
def connections_test(body: dict = Body(...)):
    try:
        lc = _lc_from_body(body)
    except secretbox.SecretError as e:
        err(400, str(e))
    warnings = []
    try:
        conn = _connect_limited(lc)
    except Exception as e:
        if body.get("id"):
            registry.set_status(int(body["id"]), False, "не удалось подключиться")
        err(400, _short_error(e, lc.server))
    try:
        def q(sql):
            return logo.query(sql, conn=conn)
        ver = str(q("SELECT @@VERSION")[0][0]).splitlines()[0]
        m = re.search(r"SQL Server (\d{4})", ver)
        info = {"ok": True, "server_version": ("Microsoft SQL Server " + m.group(1)) if m else ver[:80], "database": q("SELECT DB_NAME()")[0][0],
                "driver": logo.pick_driver(lc.driver)}
        try:
            if q("SELECT CAST(IS_SRVROLEMEMBER('sysadmin') AS int)")[0][0] == 1:
                warnings.append("Логин имеет права администратора сервера (sysadmin). Для чтения создайте отдельный логин только на чтение: docs/logo-readonly-login.sql")
        except Exception:
            pass
        try:
            firms = q("SELECT COUNT(*) FROM L_CAPIFIRM")[0][0]
        except Exception:
            err(400, "Это не база Logo: в ней нет справочника фирм L_CAPIFIRM")
        periods = q("SELECT COUNT(*) FROM L_CAPIPERIOD")[0][0]
        tabs = q("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME LIKE 'LG[_]%[_]%[_]EMFLINE'")[0][0]
        info["logo"] = {"firms": firms, "periods": periods, "tables_firm_period": tabs}
        try:
            cs = wizard._detect_charset(conn, lc)
            info["collation"], info["ansi_cp"] = cs.get("collation", ""), cs.get("ansi", "")
        except Exception:
            info["collation"], info["ansi_cp"] = "", ""
    finally:
        conn.close()
    if body.get("id"):
        registry.set_status(int(body["id"]), True)
        c = registry.get_connection(int(body["id"]))
        if c and c.get("password_plain"):
            warnings.append("Пароль подключения хранится без шифрования (шифрование Windows недоступно)")
    info["warnings"] = warnings
    return info


@router.post("/connections/databases")
def connections_databases(body: dict = Body(...)):
    try:
        lc = _lc_from_body(body)
    except secretbox.SecretError as e:
        err(400, str(e))
    try:
        conn = _connect_limited(lc)
    except Exception as e:
        err(400, _short_error(e, lc.server))
    out = []
    try:
        names = [r[0] for r in logo.query("SELECT name FROM sys.databases WHERE state = 0 AND database_id > 4 ORDER BY name", conn=conn)]
        for n in names:
            if "]" in n:
                continue
            try:
                if logo.query(f"SELECT COUNT(*) FROM [{n}].INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='L_CAPIFIRM'", conn=conn)[0][0]:
                    out.append(n)
            except Exception:
                continue
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------------------------
# набор данных
# ---------------------------------------------------------------------------------------------

def _current_row() -> dict:
    """Строка реестра для набора текущего запроса (вне запроса и для тестовых рантаймов - набор №1)."""
    return registry.get_dataset(dsctx.current().id) or registry.root_dataset() or registry.default_dataset()


def _dataset_public(ds: dict, admin: bool = True, role: str = "admin") -> dict:
    """Краткая карточка набора; считается В КОНТЕКСТЕ этого набора (dsctx.use(runtime_for(ds)))."""
    bks = books.all_books()
    rows = cache.source_rows()
    last = max([r["synced_at"] for r in rows.values() if r.get("synced_at")] or [None], key=lambda x: x or "") if rows else None
    job = dsctx.current().job
    out = {"id": ds["id"], "name": ds["name"], "org_name": ds["org_name"], "is_default": bool(ds["is_default"]), "archived": bool(ds["archived"]),
           "state": ds["state"], "role": role, "books_main": sum(1 for b in bks if b.role == "main"), "books_total": len(bks),
           "last_sync_at": last, "logo_ok": cache._logo_ok(),
           "syncing": bool(job["syncing"]), "queued": bool(job.get("queued")),
           "progress": {"done": job["done"], "total": job["total"], "message": job["message"]}}
    if admin:
        out["connection"] = {"id": ds["connection_id"], "name": ds["conn_name"], "server": ds["conn_server"], "database": ds["conn_database"]}
    return out


def _brief(ds: dict, admin: bool, role: str) -> dict:
    rt = dsmgr.runtime_for(ds)
    dsmgr.ensure_ready(rt)
    return dsctx.run_in(rt, _dataset_public, ds, admin, role)


@router.get("/datasets")
def datasets_list(request: Request):
    """Только доступные пользователю наборы (администратору установки - все, включая архивные). Каждый набор считается в своём контексте
    (параллельно: проверка связи с Logo у недоступного сервера не задерживает остальные)."""
    user = getattr(request.state, "user", None) or {"id": 0, "login": "admin", "role": "admin"}
    acc = auth.accessible_datasets(user)
    if not acc:
        return []
    is_admin = user["role"] == "admin" or not auth.enabled()
    with ThreadPoolExecutor(max_workers=min(8, len(acc))) as ex:
        futs = [ex.submit(_brief, a["ds"], is_admin, a["role"]) for a in acc]
        return [f.result() for f in futs]


@router.post("/datasets")
def datasets_create(request: Request, body: dict = Body(...)):
    """Новый пустой набор: {name, connection_id, org_name?, plan_template: turkish|none, copy_from?: id, copy_parts?: [...]} (только администратор установки)."""
    user = getattr(request.state, "user", None) or {"login": ""}
    try:
        row = dsmgr.create(body, user.get("login") or "")
    except dsmgr.DatasetError as e:
        err(e.status, e.detail)
    return _brief(row, True, "admin")


@router.put("/datasets/{did}")
def datasets_update(did: int, body: dict = Body(...)):
    try:
        ds = registry.update_dataset(did, body)
    except KeyError as e:
        err(404, str(e.args[0]))
    except ValueError as e:
        err(400, str(e))
    return _brief(ds, True, "admin")


@router.delete("/datasets/{did}")
def datasets_delete(did: int, confirm: Optional[str] = Query(None), body: Optional[dict] = Body(None)):
    """Удаление ТОЛЬКО архивного набора с подтверждением названием (?confirm=Название или {"confirm": "Название"}); файлы переносятся в data/ds/_deleted/."""
    c = confirm if confirm is not None else ((body or {}).get("confirm") if isinstance(body, dict) else None)
    try:
        return dsmgr.remove(did, c or "")
    except dsmgr.DatasetError as e:
        err(e.status, e.detail)


@router.get("/dataset")
def dataset_get(request: Request):
    ds = _current_row()
    out = _dataset_public(ds, True, getattr(request.state, "dataset_role", None) or "admin")
    out["params"] = dsparams.get_all()
    out["books_count"] = out["books_total"]
    out["cache_bytes"] = cache.cache_size_bytes()
    out["configured"] = dsparams.is_configured()
    return out


@router.put("/dataset/params")
def dataset_params_put(body: dict = Body(...)):
    if not isinstance(body, dict) or not body:
        err(400, "Передайте параметры набора: ключи " + ", ".join(dsparams.KEYS))
    try:
        return settings_io.apply_params(body)
    except ValueError as e:
        err(400, str(e))
    except (TypeError, KeyError):
        err(400, "Значение параметра набора задано неверно")


# ---------------------------------------------------------------------------------------------
# книги и мастер
# ---------------------------------------------------------------------------------------------

def _scan_load() -> Optional[dict]:
    conn = store.connect()
    try:
        r = conn.execute("SELECT scanned_at, payload FROM scan_cache WHERE id=1").fetchone()
    finally:
        conn.close()
    if r is None:
        return None
    try:
        return json.loads(r["payload"])
    except ValueError:
        return None


def _scan_save(payload: dict) -> None:
    conn = store.connect()
    try:
        conn.execute("INSERT OR REPLACE INTO scan_cache(id, scanned_at, payload) VALUES(1,?,?)", (payload["proposal"].get("scanned_at") or store.now(),
                                                                                            json.dumps(payload, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def _scan_job(rt, firms) -> None:
    st = rt.scan

    def progress(done, total, current, message):
        st.update(done=done, total=total, current=str(current or ""), message=message or "")
    try:
        sc = wizard.scan(firms=firms, progress=progress)
        prof = (sc.get("params_detected") or {}).get("plan_profile", {}).get("profile") or {}
        st.update(message="Проверка переноса остатков между книгами")
        sc["transitions"] = wizard.transition_stats(wizard.candidate_pairs(sc), root_len=int(prof.get("root_len") or 3),
                                                    balance_roots=list(prof.get("balance_roots") or []))
        prop = wizard.propose(sc)
        _scan_save({"proposal": prop, "sources": len(sc.get("sources") or [])})
        st.update(state="done", message="Готово", scanned_at=prop.get("scanned_at"), error=None)
    except Exception as e:
        st.update(state="error", error=str(e), message="Чтение фирм из Logo не удалось")


@router.post("/books/scan")
def books_scan(body: dict = Body(default={})):
    rt = dsctx.current()
    with rt.scan_lock:
        if rt.scan["state"] == "running":
            err(409, "Чтение фирм из Logo уже идёт")
        rt.scan.update(state="running", done=0, total=0, current="", message="Запуск", error=None)
    firms = body.get("firms") if isinstance(body, dict) else None
    threading.Thread(target=dsctx.run_in, args=(rt, _scan_job, rt, firms), daemon=True).start()
    return JSONResponse({"started": True}, status_code=202)


@router.get("/books/scan")
def books_scan_state():
    st = dict(dsctx.current().scan)
    if st["state"] == "idle":
        p = _scan_load()
        if p:
            st.update(state="done", scanned_at=p["proposal"].get("scanned_at"))
    return st


PARAM_SOURCE = {"currency_local": "справочник валют Logo (L_CURRENCYLIST), валюта учёта фирмы с самыми поздними оборотами",
                "currency_report": "справочник валют Logo (L_CURRENCYLIST), отчётная валюта фирмы; включена, если в проводках есть суммы REPORTNET",
                "charset": "COLLATION колонок таблиц проводок и плана счетов Logo", "denomination": "переносы остатков между соседними книгами (масштаб)",
                "plan_profile": "коды счетов планов последних источников"}


def _configured_keys() -> set:
    conn = store.connect()
    try:
        return {r["key"] for r in conn.execute("SELECT key FROM dataset_params")}
    finally:
        conn.close()


_BOOK_NUM_STATS = ("lines", "open_lines", "turn_lines", "turn_dt", "turn_kt", "open_dt", "open_kt", "fiches", "oem_rows")


def _aligned(prop: dict) -> dict:
    """Предложение, согласованное с набором: (1) признак OEM книги не предлагается другим, если у текущей книги он задан и в источнике не найдено ни одной
    OEM-строки (на данные не влияет); (2) параметры набора, уже заданные, показываются текущими значениями, а предложение Logo - отдельно в params_suggested."""
    import copy
    prop = copy.deepcopy(prop)
    cur = {(b.src, b.date_from.isoformat()): b for b in books.all_books()}
    cur_by_src = {}
    for b in books.all_books():
        cur_by_src.setdefault(b.src, b)
    for b in prop.get("books", []):
        c = cur.get((b["src"], b["date_from"])) or cur_by_src.get(b["src"])
        if c is not None and bool(c.oem) != bool(b.get("oem")) and not b.get("oem_rows"):
            b["oem"] = bool(c.oem)
            b["oem_note"] = "Признак OEM-текстов оставлен как в текущей разметке: в источнике нет строк в OEM-кодировке, на данные он не влияет"
    for b in prop.get("books", []):
        # статистика книги, которой нет в чтении Logo (добавлена вручную, ещё не загружена): числа — null, а не пустая строка; loaded — есть ли данные
        for k in _BOOK_NUM_STATS:
            if b.get(k) == "":
                b[k] = None
        b["loaded"] = bool(b.get("lines"))
    conf = _configured_keys()
    now = dsparams.get_all()
    sug = {}
    for k, v in list((prop.get("params") or {}).items()):
        if v is None:
            continue
        item = {"value": v, "source": PARAM_SOURCE.get(k, "данные Logo"), "configured": k in conf, "current": now.get(k) if k in conf else None}
        if k == "currency_local" and isinstance(v, dict) and v.get("note"):
            item["source"] += ". " + v["note"]
        item["differs"] = _cmp_param(k, now.get(k), v)      # у незаданного параметра сравнение идёт со значением-заглушкой
        sug[k] = item
        if k in conf:
            prop["params"][k] = now.get(k)         # заданное значение не подменяется предложением
    prop["params_suggested"] = sug
    return prop


def _cmp_param(k, cur, sugg) -> bool:
    try:
        a = dsparams.validate(k, cur)
        c = dict(sugg) if isinstance(sugg, dict) else sugg
        if isinstance(c, dict):
            c.pop("note", None)
        return a != dsparams.validate(k, c)
    except Exception:
        return True


@router.get("/books/proposal")
def books_proposal():
    p = _scan_load()
    if not p:
        err(409, "Фирмы из Logo ещё не прочитаны: нажмите «Прочитать фирмы из Logo»")
    return _aligned(p["proposal"])


@router.post("/dataset/plan-profile/detect")
def plan_profile_detect(body: dict = Body(default={})):
    """Профиль плана счетов по УЖЕ загруженным планам (кэш), без скана и без обращения к Logo: основные книги набора (или book/src из тела)."""
    from . import plan as plan_mod
    srcs = body.get("firms") if isinstance(body, dict) else None
    mains = [b for b in books.all_books() if b.role == "main"]
    if srcs:
        want = {int(x) for x in srcs}
    else:
        want = {b.src for b in mains}
    conn = cache.connect()
    try:
        per, allc = [], []
        for src in sorted(want):
            codes = [r[0] for r in conn.execute("SELECT code FROM accounts WHERE firm=?", (src,))]
            if not codes:
                continue
            allc += codes
            d = plan_mod.detect_profile(codes)
            bk = next((b for b in mains if b.src == src), None) or next((b for b in books.of_src(src)), None)
            per.append({"src": src, "label": bk.label if bk else None, "firm": books.src_parts(src)[0], "codes": len(codes),
                        "separator": d["profile"]["sep"] if d["profile"]["mode"] == "sep" else "", "root_len": d["profile"]["root_len"], "template": d["profile"]["template"],
                        "stats": d["stats"]})
    finally:
        conn.close()
    if not allc:
        err(409, "Планы счетов ещё не загружены: выполните «Прочитать фирмы из Logo» или загрузите данные")
    d = plan_mod.detect_profile(allc)
    prof = d["profile"]
    sep = prof["sep"] if prof["mode"] == "sep" else ""
    n = len(set(allc))
    disc = []
    for x in per:
        if (x["separator"], x["root_len"]) != (sep, prof["root_len"]):
            disc.append("Книга %s (источник %d): разделитель «%s», длина корня %d - отличается от общего («%s», %d)"
                        % (x["label"] or "?", x["src"], x["separator"] or "нет", x["root_len"], sep or "нет", prof["root_len"]))
    if sep:
        no_sep = sum(1 for c in set(allc) if sep not in c)
        if no_sep:
            disc.append("Счетов без разделителя «%s»: %d из %d (корневые счета)" % (sep, no_sep, n))
    odd_root = round((1 - d["stats"]["root_len_share"]) * n)
    if odd_root:
        disc.append("Счетов с другой длиной корня, чем %d: %d из %d" % (prof["root_len"], odd_root, n))
    cur = dsparams.get("plan_profile")
    return {"profile": prof, "separator": sep, "root_len": prof["root_len"], "mode": prof["mode"], "template": prof["template"],
            "checked": {"accounts": n, "books": len(per), "sources": [x["src"] for x in per]}, "stats": d["stats"], "books": per,
            "discrepancies": disc, "current": cur, "differs": (cur.get("sep"), cur.get("root_len"), cur.get("mode"), cur.get("template")) !=
            (prof["sep"], prof["root_len"], prof["mode"], prof["template"])}


def _books_state(carry: bool = False) -> dict:
    bks = books.all_books()
    rows = cache.source_rows()
    by_src = {}
    for b in bks:
        by_src.setdefault(b.src, []).append(b)
    conn = cache.connect()
    out = []
    try:
        outside = {}
        for src, lst in by_src.items():
            r = rows.get(src)
            if not r or r.get("synced_at") is None:
                continue
            cond = " OR ".join("(date>=? AND date<=?)" for _ in lst)
            prm = [src]
            for b in lst:
                prm += [b.date_from.isoformat(), b.date_to.isoformat()]
            outside[src] = conn.execute(f"SELECT COUNT(*) FROM lines WHERE firm=? AND is_opening=0 AND NOT ({cond})", prm).fetchone()[0]
        mains = sorted([b for b in bks if b.role == "main"], key=lambda b: (b.date_from, b.id))
        nxt = {a.id: b for a, b in zip(mains, mains[1:])}
        for b in bks:
            d = b.to_dict()
            r = rows.get(b.src) or {}
            loaded = r.get("synced_at") is not None
            d.update({"firm_name": r.get("name") or b.name, "synced_at": r.get("synced_at"), "loaded": loaded,
                      "line_count": (r.get("line_count") or 0) if loaded else None,
                      "rows_outside": outside.get(b.src), "carry_next": None})
            if carry and b.id in nxt and nxt[b.id].src != b.src:
                a, c = b, nxt[b.id]
                cl = dict(conn.execute("SELECT account, SUM(dt-kt) FROM lines WHERE firm=? AND (is_opening=1 OR date<=?) GROUP BY account", (a.src, a.date_to.isoformat())).fetchall())
                op = dict(conn.execute("SELECT account, SUM(dt-kt) FROM lines WHERE firm=? AND is_opening=1 GROUP BY account", (c.src,)).fetchall())
                prof = dsparams.get("plan_profile")
                from . import plan as plan_mod
                pp = plan_mod.PlanProfile.from_params(prof)
                gap = sum(abs(op.get(k, 0) / c.divisor - cl.get(k, 0) / a.divisor) for k in set(cl) | set(op) if not pp.is_noncarried(k) and pp.is_balance_root(k))
                d["carry_next"] = {"to_book": c.id, "gap": round(gap / 100.0, 2)}
            out.append(d)
    finally:
        conn.close()
    mains = [b for b in bks if b.role == "main"]
    gaps = []
    if mains:
        lo, hi = min(b.date_from for b in mains), max(b.date_to for b in mains)
        gaps = [{"date_from": g1.isoformat(), "date_to": g2.isoformat()} for g1, g2 in books.gaps(lo, hi)]
    return {"books": out, "coverage": {"date_from": min((b.date_from for b in mains), default=None) and min(b.date_from for b in mains).isoformat(),
                                       "date_to": max((b.date_to for b in mains), default=None) and max(b.date_to for b in mains).isoformat(),
                                       "gaps": gaps, "labels": sorted({b.label for b in mains})},
            "state": _current_row()["state"]}


@router.get("/books")
def books_get(carry: bool = False):
    return _books_state(carry)


def _validate(items: list) -> dict:
    try:
        tables = wizard.list_tables()
    except Exception:
        tables = None          # Logo недоступно - наличие таблиц не проверяем
    return books.validate(items, tables, dsparams.get("denomination"))


@router.post("/books/validate")
def books_validate(body: dict = Body(...)):
    items = body.get("books")
    if not isinstance(items, list):
        err(400, "Передайте список книг: {books: [...]}")
    return _validate(items)


@router.put("/books")
def books_put(body: dict = Body(...)):
    items = body.get("books")
    if not isinstance(items, list):
        err(400, "Передайте список книг: {books: [...]}")
    v = _validate(items)
    if not v["ok"]:
        return JSONResponse({"detail": "; ".join(v["errors"]), "errors": v["errors"], "warnings": v["warnings"]}, status_code=400)
    before = {b.id: b for b in books.all_books()}
    new = books.replace_all(items)
    conn = cache.connect()
    need = []
    try:
        rows = cache.source_rows(conn)
        for b in new:
            if b.role != "main":
                continue
            old = before.get(b.id)
            r = rows.get(b.src)
            if r is None or r.get("synced_at") is None:
                need.append(b.src)
            elif old is None or old.role != "main" or (r.get("oem") or 0) != (1 if cache.oem_for_src(b.src) else 0):
                cache._reset_source(conn, b.src)    # новая основная книга или другая кодировка текстов - источник перечитывается
                need.append(b.src)
        conn.commit()
    finally:
        conn.close()
    if any(b.role == "main" for b in new):
        registry.set_state(_current_row()["id"], "ready")
    res = _books_state()
    res["sync_needed"] = sorted(set(need))
    res["warnings"] = v["warnings"]
    return res


@router.post("/books/apply-proposal")
def books_apply_proposal(body: dict = Body(default={})):
    """Применить предложение мастера целиком: разметка книг (основные и запасные) и, если include_params, параметры набора."""
    p = _scan_load()
    if not p:
        err(409, "Фирмы из Logo ещё не прочитаны: нажмите «Прочитать фирмы из Logo»")
    prop = _aligned(p["proposal"])
    items = [{"slot": b["slot"], "label": b["label"], "part": b.get("part", 1), "src_firm": b["firm"], "src_period": b["period"], "date_from": b["date_from"],
              "date_to": b["date_to"], "role": b["role"], "name": b.get("name", ""), "divisor": b.get("divisor", 1), "oem": bool(b.get("oem")),
              "reason": b.get("reason", "")} for b in prop["books"]]
    if body.get("include_params", True):
        # заданные в наборе параметры НЕ перезаписываются, кроме перечисленных в overwrite (по каждому параметру - явно)
        force = set(body.get("overwrite") or []) if isinstance(body, dict) else set()
        conf = _configured_keys()
        prm = {}
        for k in ("currency_local", "currency_report", "charset", "denomination", "plan_profile"):
            v = (prop.get("params_suggested") or {}).get(k, {}).get("value")
            if v is not None and (k not in conf or k in force):
                prm[k] = v
        if "currency_local" in prm:
            prm["currency_local"] = {k: v for k, v in prm["currency_local"].items() if k != "note"}
        if prm:
            dsparams.set_many(prm)
            if dsparams.get("plan_profile").get("template") == "turkish" and not store.get_closing_rules():
                from . import legacy_gap
                store.set_closing_rules([{"kind": "accounts", "value": legacy_gap.TURKISH_CLOSING_ACCOUNTS, "enabled": True}])
    return books_put({"books": items})


@router.post("/books/{bid}/make-main")
def books_make_main(bid: int):
    if dsctx.current().job["syncing"]:
        err(409, "Синхронизация уже идёт - повторите смену книги после её окончания")
    try:
        res = books.make_main(bid)
    except ValueError as e:
        err(404, str(e))
    b = res["book"]
    started = False
    if res["changed"]:
        conn = cache.connect()
        try:
            cache._reset_source(conn, b.src)
            conn.commit()
        finally:
            conn.close()
        store.set_year_book(b.label, b.src_firm)
        started = cache.start_sync([b.src], force=True)
    return {"book": b.to_dict(), "previous": [x.to_dict() for x in res["previous"]], "changed": res["changed"], "sync_started": started}
