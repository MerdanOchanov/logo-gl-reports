"""Экспорт/импорт настроек набора данных: перенос сделанной работы между наборами - сохранённые отчёты и папки, классификаторы, шаблоны
Excel, названия счетов, правила закрывающих ордеров, связи счетов, параметры набора. БЕЗ паролей, подключений, пользователей, разметки книг, ручной
корреспонденции и кэша. Работает в контексте текущего набора (dsctx.current())."""
from __future__ import annotations

import base64
import binascii
import json
import threading
from typing import Dict, List, Optional

from . import dsctx, dsparams, store

FORMAT = "buhlogo-settings"
VERSION = 1
PARTS = ("reports", "classifiers", "names", "closing", "links", "params", "templates")
COPY_PARTS = PARTS
PART_RU = {"reports": "отчёты", "classifiers": "классификаторы", "names": "названия счетов", "closing": "закрывающие ордера",
           "links": "связи счетов", "params": "параметры набора", "templates": "шаблоны Excel"}


class SettingsFileError(ValueError):
    pass


def _templates_dir():
    from . import routes_forms
    return routes_forms.templates_dir()


# ---------------------------------------------------------------------------------------------
# экспорт
# ---------------------------------------------------------------------------------------------

def export_settings(parts: Optional[List[str]] = None) -> dict:
    parts = [p for p in (parts or PARTS) if p in PARTS]
    rt = dsctx.current()
    out: dict = {"format": FORMAT, "version": VERSION, "exported_at": store.now(), "dataset": {"name": rt.name}, "parts": parts}
    conn = store.connect()
    try:
        if "reports" in parts:
            out["reports"] = [{"name": r["name"], "type": r["type"], "folder": r["folder"] or "", "definition": json.loads(r["definition"])}
                              for r in conn.execute("SELECT name, type, folder, definition FROM reports ORDER BY folder, name, id")]
        if "classifiers" in parts:
            cl = []
            for c in conn.execute("SELECT id, name, description FROM classifiers ORDER BY id").fetchall():
                cl.append({"id": c["id"], "name": c["name"], "description": c["description"] or "",
                           "groups": [dict(g) for g in conn.execute("SELECT id, parent_id, code, name, sort FROM cls_groups WHERE classifier_id=? ORDER BY sort, id", (c["id"],))],
                           "rules": [dict(r) for r in conn.execute("SELECT group_id, kind, value, year_from, year_to, priority FROM cls_rules WHERE classifier_id=? ORDER BY id", (c["id"],))]})
            out["classifiers"] = cl
        if "names" in parts:
            out["account_names"] = [dict(r) for r in conn.execute("SELECT code, year_from, year_to, name FROM account_names ORDER BY code, year_from")]
        if "closing" in parts:
            out["closing_rules"] = [dict(r) for r in conn.execute("SELECT kind, value, enabled FROM closing_rules ORDER BY id")]
        if "links" in parts:
            out["account_links"] = [dict(r) for r in conn.execute("SELECT code_from, year_from, code_to, year_to, method, confidence, note FROM account_links ORDER BY id")]
        if "params" in parts:
            prm = dsparams.get_all()
            out["params"] = {k: prm[k] for k in dsparams.KEYS if k in prm}
        if "templates" in parts:
            tpl = []
            d = _templates_dir()
            for t in conn.execute("SELECT id, name, filename FROM templates ORDER BY id").fetchall():
                f = d / ("%d.xlsx" % t["id"])
                if f.exists():
                    tpl.append({"id": t["id"], "name": t["name"], "filename": t["filename"], "data_base64": base64.b64encode(f.read_bytes()).decode("ascii")})
            out["templates"] = tpl
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------------------------
# импорт
# ---------------------------------------------------------------------------------------------

def _uniq(name: str, taken: set) -> str:
    if name not in taken:
        return name
    n = 2
    while "%s (%d)" % (name, n) in taken:
        n += 1
    return "%s (%d)" % (name, n)


def _walk_remap(node, cls_map: Dict[int, int], grp_map: Dict[int, int], tpl_map: Dict[int, int], missing: list) -> None:
    """Переписывает ссылки на классификаторы/группы/шаблоны в определении отчёта (на месте). Не найденные - в missing."""
    if isinstance(node, dict):
        if node.get("kind") == "classifier" and isinstance(node.get("id"), int):
            if node["id"] in cls_map:
                node["id"] = cls_map[node["id"]]
            else:
                missing.append("классификатор №%s" % node["id"])
        if "classifier" in node and isinstance(node.get("classifier"), int):
            old_c = node["classifier"]
            if old_c in cls_map:
                node["classifier"] = cls_map[old_c]
            else:
                missing.append("классификатор №%s" % old_c)
            g = node.get("group")
            if isinstance(g, int) and g != -1:
                if g in grp_map:
                    node["group"] = grp_map[g]
                else:
                    missing.append("группа №%s" % g)
        if isinstance(node.get("template_id"), int):
            if node["template_id"] in tpl_map:
                node["template_id"] = tpl_map[node["template_id"]]
            else:
                missing.append("шаблон Excel №%s" % node["template_id"])
        for v in node.values():
            _walk_remap(v, cls_map, grp_map, tpl_map, missing)
    elif isinstance(node, list):
        for v in node:
            _walk_remap(v, cls_map, grp_map, tpl_map, missing)


def validate_file(data) -> dict:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise SettingsFileError("Файл настроек не распознан")
    if data.get("version") != VERSION:
        raise SettingsFileError("Версия файла %s не поддерживается (ожидается %d)" % (data.get("version"), VERSION))
    return data


def import_settings(data: dict, mode: str = "merge", parts: Optional[List[str]] = None, resync: bool = True) -> dict:
    """mode: «merge» (добавить; одноимённое получает «(2)», существующее не трогается) или «replace» (заменить одноимённые). Ответ:
    {created:{часть:n}, updated:{часть:n}, skipped:{часть:n}, warnings:[...], resync_required, plan_rebuild}."""
    validate_file(data)
    if mode not in ("merge", "replace"):
        raise SettingsFileError("Режим импорта: «добавить» или «заменить одноимённые»")
    parts = [p for p in (parts or data.get("parts") or PARTS) if p in PARTS]
    created = {p: 0 for p in parts}
    updated = {p: 0 for p in parts}
    skipped = {p: 0 for p in parts}
    warnings: List[str] = []
    replace = mode == "replace"
    files_to_write: Dict[int, bytes] = {}
    conn = store.connect()
    params_res = {"resync_required": False, "plan_rebuild": False}
    try:
        conn.execute("BEGIN IMMEDIATE")
        # --- шаблоны Excel
        tpl_map: Dict[int, int] = {}
        if "templates" in parts:
            have = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM templates")}
            for t in data.get("templates") or []:
                try:
                    raw = base64.b64decode(t["data_base64"])
                    name = str(t.get("name") or t.get("filename") or "Шаблон")
                except (KeyError, TypeError, binascii.Error):
                    skipped["templates"] += 1
                    warnings.append("Шаблон Excel в файле повреждён - пропущен")
                    continue
                if replace and name in have:
                    tid = have[name]
                    conn.execute("UPDATE templates SET filename=?, uploaded_at=? WHERE id=?", (t.get("filename") or name + ".xlsx", store.now(), tid))
                    updated["templates"] += 1
                else:
                    name = _uniq(name, set(have))
                    tid = conn.execute("INSERT INTO templates(name, filename, uploaded_at) VALUES(?,?,?)",
                                       (name, t.get("filename") or name + ".xlsx", store.now())).lastrowid
                    have[name] = tid
                    created["templates"] += 1
                files_to_write[tid] = raw
                if isinstance(t.get("id"), int):
                    tpl_map[t["id"]] = tid
        elif data.get("templates"):
            have = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM templates")}
            for t in data["templates"]:
                if isinstance(t.get("id"), int) and t.get("name") in have:
                    tpl_map[t["id"]] = have[t["name"]]
        # --- классификаторы
        cls_map: Dict[int, int] = {}
        grp_map: Dict[int, int] = {}
        if "classifiers" in parts:
            have = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM classifiers")}
            for c in data.get("classifiers") or []:
                name = str(c.get("name") or "").strip()
                if not name:
                    skipped["classifiers"] += 1
                    continue
                if replace and name in have:
                    cid = have[name]
                    conn.execute("UPDATE classifiers SET description=?, updated_at=? WHERE id=?", (c.get("description") or "", store.now(), cid))
                    conn.execute("DELETE FROM cls_groups WHERE classifier_id=?", (cid,))
                    conn.execute("DELETE FROM cls_rules WHERE classifier_id=?", (cid,))
                    updated["classifiers"] += 1
                else:
                    name = _uniq(name, set(have))
                    cid = conn.execute("INSERT INTO classifiers(name, description, updated_at) VALUES(?,?,?)", (name, c.get("description") or "", store.now())).lastrowid
                    have[name] = cid
                    created["classifiers"] += 1
                if isinstance(c.get("id"), int):
                    cls_map[c["id"]] = cid
                local: Dict[int, int] = {}
                for g in c.get("groups") or []:
                    nid = conn.execute("INSERT INTO cls_groups(classifier_id, parent_id, code, name, sort) VALUES(?,?,?,?,?)",
                                       (cid, None, g.get("code") or "", g.get("name") or "", int(g.get("sort") or 0))).lastrowid
                    local[g["id"]] = nid
                    grp_map[g["id"]] = nid
                for g in c.get("groups") or []:
                    if g.get("parent_id") in local:
                        conn.execute("UPDATE cls_groups SET parent_id=? WHERE id=?", (local[g["parent_id"]], local[g["id"]]))
                for r in c.get("rules") or []:
                    if r.get("group_id") not in local:
                        continue
                    conn.execute("INSERT INTO cls_rules(classifier_id, group_id, kind, value, year_from, year_to, priority) VALUES(?,?,?,?,?,?,?)",
                                 (cid, local[r["group_id"]], r.get("kind"), str(r.get("value", "")), r.get("year_from"), r.get("year_to"), int(r.get("priority") or 0)))
        elif data.get("classifiers"):
            # классификаторы не импортируются: ссылки отчётов переносятся на одноимённые классификаторы набора (группы - по коду)
            have = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM classifiers")}
            for c in data["classifiers"]:
                cid = have.get(c.get("name"))
                if cid is None or not isinstance(c.get("id"), int):
                    continue
                cls_map[c["id"]] = cid
                codes = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM cls_groups WHERE classifier_id=?", (cid,))}
                for g in c.get("groups") or []:
                    if g.get("code") in codes:
                        grp_map[g["id"]] = codes[g["code"]]
        # --- отчёты
        if "reports" in parts:
            have = {(r["folder"] or "", r["name"]): r["id"] for r in conn.execute("SELECT id, name, folder FROM reports")}
            for r in data.get("reports") or []:
                name = str(r.get("name") or "").strip()
                if not name or not isinstance(r.get("definition"), dict):
                    skipped["reports"] += 1
                    continue
                import copy as _copy
                defn = _copy.deepcopy(r["definition"])
                missing: list = []
                _walk_remap(defn, cls_map, grp_map, tpl_map, missing)
                if missing:
                    skipped["reports"] += 1
                    warnings.append("Отчёт «%s»: %s не найден в файле - пропущен" % (name, ", ".join(sorted(set(missing)))))
                    continue
                folder = r.get("folder") or ""
                dj = json.dumps(defn, ensure_ascii=False)
                ts = store.now()
                if replace and (folder, name) in have:
                    rid = have[(folder, name)]
                    conn.execute("UPDATE reports SET type=?, definition=?, updated_at=? WHERE id=?", (r.get("type") or defn.get("type") or "osv", dj, ts, rid))
                    conn.execute("INSERT INTO report_versions(report_id, definition, saved_at, comment) VALUES(?,?,?,?)", (rid, dj, ts, "Импорт настроек"))
                    updated["reports"] += 1
                else:
                    name = _uniq(name, {n for (f, n) in have if f == folder})
                    rid = conn.execute("INSERT INTO reports(name, type, folder, definition, updated_at) VALUES(?,?,?,?,?)",
                                       (name, r.get("type") or defn.get("type") or "osv", folder, dj, ts)).lastrowid
                    conn.execute("INSERT INTO report_versions(report_id, definition, saved_at, comment) VALUES(?,?,?,?)", (rid, dj, ts, "Импорт настроек"))
                    have[(folder, name)] = rid
                    created["reports"] += 1
        # --- названия счетов
        if "names" in parts:
            have = {(r["code"], r["year_from"], r["year_to"]): r["rowid"] for r in conn.execute("SELECT rowid, code, year_from, year_to FROM account_names")}
            for it in data.get("account_names") or []:
                code, nm = str(it.get("code") or "").strip(), str(it.get("name") or "").strip()
                if not code or not nm:
                    skipped["names"] += 1
                    continue
                key = (code, it.get("year_from"), it.get("year_to"))
                if key in have:
                    if replace:
                        conn.execute("UPDATE account_names SET name=? WHERE rowid=?", (nm, have[key]))
                        updated["names"] += 1
                    else:
                        skipped["names"] += 1
                else:
                    have[key] = conn.execute("INSERT INTO account_names(code, year_from, year_to, name) VALUES(?,?,?,?)", (code, key[1], key[2], nm)).lastrowid
                    created["names"] += 1
        # --- правила закрывающих ордеров
        closing_changed = False
        if "closing" in parts:
            rules = [r for r in (data.get("closing_rules") or []) if r.get("kind") in ("text", "accounts") and str(r.get("value") or "").strip()]
            have = {(r["kind"], r["value"]) for r in conn.execute("SELECT kind, value FROM closing_rules")}
            if replace:
                if {(r["kind"], str(r["value"]).strip()) for r in rules} != have:
                    closing_changed = True
                    conn.execute("DELETE FROM closing_rules")
                    have = set()
                updated["closing"] += len(rules)     # режим «заменить»: все правила файла считаются заменёнными, даже если набор уже совпадал
            for r in rules:
                k = (r["kind"], str(r["value"]).strip())
                if k in have:
                    skipped["closing"] += 1 if not replace else 0
                    continue
                conn.execute("INSERT INTO closing_rules(kind, value, enabled) VALUES(?,?,?)", (k[0], k[1], 1 if r.get("enabled", True) else 0))
                have.add(k)
                if not replace:
                    created["closing"] += 1
                    closing_changed = True
        # --- связи счетов
        if "links" in parts:
            for l in data.get("account_links") or []:
                try:
                    vals = (str(l["code_from"]), int(l["year_from"]), str(l["code_to"]), int(l["year_to"]), l.get("method") or "manual", l.get("confidence"), l.get("note") or "")
                except (KeyError, TypeError, ValueError):
                    skipped["links"] += 1
                    continue
                exists = conn.execute("SELECT id FROM account_links WHERE code_from=? AND year_from=? AND year_to=?", (vals[0], vals[1], vals[3])).fetchone()
                if exists and not replace:
                    skipped["links"] += 1
                elif exists:
                    conn.execute("UPDATE account_links SET code_to=?, method=?, confidence=?, note=? WHERE id=?", (vals[2], vals[4], vals[5], vals[6], exists["id"]))
                    updated["links"] += 1
                else:
                    conn.execute("INSERT INTO account_links(code_from, year_from, code_to, year_to, method, confidence, note) VALUES(?,?,?,?,?,?,?)", vals)
                    created["links"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    # --- файлы шаблонов (после успешной записи в базу)
    if files_to_write:
        d = _templates_dir()
        for tid, raw in files_to_write.items():
            (d / ("%d.xlsx" % tid)).write_bytes(raw)
    # --- параметры набора (проверка формата; кодировка/профиль плана требуют перечитать данные - как PUT /api/dataset/params)
    if "params" in parts and isinstance(data.get("params"), dict):
        cur_rows = _param_keys_present()
        items = {}
        for k, v in data["params"].items():
            if k not in dsparams.KEYS:
                continue
            if not replace and k in cur_rows:
                skipped["params"] += 1
                continue
            try:
                dsparams.validate(k, v)
            except (ValueError, TypeError, KeyError) as e:
                skipped["params"] += 1
                warnings.append(str(e))
                continue
            items[k] = v
        if items:
            params_res = apply_params(items, resync=resync)
            (updated if replace else created)["params"] += len(items)
            if params_res.get("resync_required") and resync:
                warnings.append("Кодировка набора изменена: тексты ордеров будут перечитаны из Logo при следующей проверке данных")
            if params_res.get("plan_rebuild") and resync:
                warnings.append("Профиль плана счетов изменён: план счетов перечитывается в фоне")
    if closing_changed and resync:
        _recompute_closing_if_loaded()
    return {"created": created, "updated": updated, "skipped": skipped, "warnings": warnings,
            "resync_required": bool(params_res.get("resync_required")), "plan_rebuild": bool(params_res.get("plan_rebuild"))}


def _param_keys_present() -> set:
    conn = store.connect()
    try:
        return {r["key"] for r in conn.execute("SELECT key FROM dataset_params")}
    finally:
        conn.close()


def _recompute_closing_if_loaded() -> None:
    from . import cache
    conn = cache.connect()
    try:
        has = conn.execute("SELECT 1 FROM lines LIMIT 1").fetchone() is not None
    finally:
        conn.close()
    if has:
        cache.recompute_closing()


def apply_params(items: dict, resync: bool = True) -> dict:
    """Запись параметров набора + последствия (то же, что PUT /api/dataset/params): {resync_required, plan_rebuild, params}."""
    from . import cache
    old_plan = dsparams.get("plan_profile")
    res = dsparams.set_many(items)
    res["plan_rebuild"] = "plan_profile" in items and dsparams.get("plan_profile") != old_plan
    if resync and res["plan_rebuild"]:      # уровни/родители счетов в кэше построены по профилю плана - перечитываем план (проводки не трогаются)
        threading.Thread(target=dsctx.run_in, args=(dsctx.current(), cache.refresh_plans, True), daemon=True).start()
    if resync and res["resync_required"]:
        conn = cache.connect()
        try:
            conn.execute("UPDATE sources SET fingerprint=NULL, checked_ts=NULL")
            conn.commit()
        finally:
            conn.close()
    res["params"] = dsparams.get_all()
    return res
