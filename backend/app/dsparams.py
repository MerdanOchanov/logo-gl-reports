"""Параметры набора данных (settings.db → dataset_params, значения — JSON): валюты, кодировка, деноминация, профиль плана, заметки о разрывах, синхронизация."""
from __future__ import annotations

import json
from typing import Any, Dict

from . import charset, store

KEYS = ("currency_local", "currency_report", "charset", "denomination", "plan_profile", "carry_notes", "sync")

DEFAULTS = {   # пустой набор (до подтверждения разметки): нейтральные значения; «турецкий план» подставляется шаблоном при создании/подтверждении
    "currency_local": {"code": "LOCAL", "name": "местная валюта", "symbol": ""},
    "currency_report": {"code": "REPORT", "name": "отчётная валюта", "enabled": False},
    "charset": {"ansi": "cp1254", "oem": "cp857"},
    "denomination": [],
    "plan_profile": {"template": "none", "mode": "sep", "sep": ".", "root_len": 3, "widths": [], "balance_roots": [], "pnl_roots": [], "noncarried": []},
    "carry_notes": [],
    "sync": {"ttl_current": 60, "ttl_old": 86400, "old_after_days": 400},
}


def get_all() -> Dict[str, Any]:
    conn = store.connect()
    try:
        rows = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM dataset_params")}
    except Exception:
        rows = {}
    finally:
        conn.close()
    out = {k: json.loads(json.dumps(v)) for k, v in DEFAULTS.items()}
    out.update(rows)
    return out


def get(key: str) -> Any:
    return get_all()[key]


def is_configured() -> bool:
    conn = store.connect()
    try:
        return conn.execute("SELECT 1 FROM dataset_params WHERE key='plan_profile'").fetchone() is not None
    finally:
        conn.close()


def validate(key: str, v: Any) -> Any:
    """Проверяет значение параметра; ValueError — по-русски."""
    def bad(msg):
        raise ValueError("Параметр «%s»: %s" % (key, msg))
    if key == "currency_local":
        if not isinstance(v, dict) or not str(v.get("code", "")).strip():
            bad("укажите код валюты")
        out = {"code": str(v["code"]).strip().upper(), "name": str(v.get("name", "")).strip(), "symbol": str(v.get("symbol", "")).strip()}
        for k in ("gen_pl", "prep_pl"):     # формы названия для текстов («суммы в старых манатах»): родительный и предложный падеж мн. числа
            if str(v.get(k) or "").strip():
                out[k] = str(v[k]).strip()
        return out
    if key == "currency_report":
        if not isinstance(v, dict) or not str(v.get("code", "")).strip():
            bad("укажите код валюты")
        return {"code": str(v["code"]).strip().upper(), "name": str(v.get("name", "")).strip(), "enabled": bool(v.get("enabled", True))}
    if key == "charset":
        if not isinstance(v, dict):
            bad("ожидается {ansi, oem}")
        ansi, oem = str(v.get("ansi", "")).strip().lower(), str(v.get("oem", "") or "").strip().lower()
        if not ansi or not charset.valid_codec(ansi):
            bad("неизвестная кодовая страница «%s»" % ansi)
        if oem and not charset.valid_codec(oem):
            bad("неизвестная OEM-кодовая страница «%s»" % oem)
        return {"ansi": ansi, "oem": oem}
    if key == "denomination":
        if not isinstance(v, list):
            bad("ожидается список {before, divisor}")
        out = []
        for it in v:
            try:
                b = str(it["before"])[:10]
                int(b[:4]); int(b[5:7]); int(b[8:10])
                dv = int(it["divisor"])
            except Exception:
                bad("каждая строка — {before: «ГГГГ-ММ-ДД», divisor: целое}")
            if dv < 1:
                bad("делитель должен быть не меньше 1")
            out.append({"before": b, "divisor": dv})
        return sorted(out, key=lambda x: x["before"])
    if key == "plan_profile":
        from . import plan
        if not isinstance(v, dict):
            bad("ожидается объект профиля плана счетов")
        prof = plan.PlanProfile.from_params(v).to_params()
        if prof.get("mode") not in ("sep", "width"):
            bad("режим: sep или width")
        return prof
    if key == "carry_notes":
        if not isinstance(v, list):
            bad("ожидается список заметок")
        out = []
        for it in v:
            if not isinstance(it, dict) or not (str(it.get("text", "")).strip() or str(it.get("denom_suffix", "")).strip()):
                bad("у каждой заметки должен быть текст")
            n = {"text": str(it.get("text", "")).strip()}
            if str(it.get("denom_suffix", "")).strip():
                n["denom_suffix"] = str(it["denom_suffix"]).strip()
            for k in ("from_label", "to_label", "from_firm", "to_firm"):
                if it.get(k) not in (None, ""):
                    n[k] = int(it[k])
            out.append(n)
        return out
    if key == "sync":
        if not isinstance(v, dict):
            bad("ожидается {ttl_current, ttl_old, old_after_days}")
        d = dict(DEFAULTS["sync"])
        for k in d:
            if v.get(k) not in (None, ""):
                d[k] = int(v[k])
                if d[k] < 0:
                    bad("значения не могут быть отрицательными")
        return d
    raise ValueError("Неизвестный параметр набора «%s»" % key)


def set_many(items: Dict[str, Any]) -> Dict[str, Any]:
    """Записывает параметры (после проверки); возвращает {"resync_required": bool} (смена кодировки требует перечитать тексты)."""
    clean = {k: validate(k, v) for k, v in items.items()}
    old = get_all()
    conn = store.connect()
    try:
        for k, v in clean.items():
            conn.execute("INSERT OR REPLACE INTO dataset_params(key, value) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    from . import dsctx
    dsctx.current().books_ver += 1
    return {"resync_required": "charset" in clean and clean["charset"] != old["charset"]}


def local_forms() -> tuple:
    """(род. мн., предл. мн.) названия местной валюты для текстов; по умолчанию - нейтральные формы."""
    c = get("currency_local")
    return c.get("gen_pl") or "единиц валюты", c.get("prep_pl") or "единицах валюты"


def local_code() -> str:
    return get("currency_local")["code"]


def report_code() -> str:
    return get("currency_report")["code"]
