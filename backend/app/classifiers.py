"""Классификаторы: группы (дерево) + правила «счёт → группа»; импорт из меток Logo (SPECODE); разрешение счёт → группа."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import accounts as acc_mod
from . import cache, store

RULE_KINDS = ("mask", "range", "list", "specode", "units")


class ClassifierError(ValueError):
    pass


# ---- CRUD -------------------------------------------------------------------------------------

def list_classifiers() -> list[dict]:
    conn = store.connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT c.id, c.name, c.description, "
            "(SELECT COUNT(*) FROM cls_groups g WHERE g.classifier_id=c.id) AS groups_count, "
            "(SELECT COUNT(*) FROM cls_rules r WHERE r.classifier_id=c.id) AS rules_count "
            "FROM classifiers c ORDER BY c.name")]
    finally:
        conn.close()


def get_classifier(cid: int) -> dict | None:
    conn = store.connect()
    try:
        c = conn.execute("SELECT id, name, description, updated_at FROM classifiers WHERE id=?", (cid,)).fetchone()
        if not c:
            return None
        d = dict(c)
        d["groups"] = [dict(r) for r in conn.execute(
            "SELECT id, parent_id, code, name, sort FROM cls_groups WHERE classifier_id=? ORDER BY sort, id", (cid,))]
        d["rules"] = [dict(r) for r in conn.execute(
            "SELECT id, group_id, kind, value, year_from, year_to, priority FROM cls_rules WHERE classifier_id=? ORDER BY id", (cid,))]
        return d
    finally:
        conn.close()


def create_classifier(name: str, description: str = "") -> int:
    name = (name or "").strip()
    if not name:
        raise ClassifierError("Укажите название классификатора")
    conn = store.connect()
    try:
        cur = conn.execute("INSERT INTO classifiers(name, description, updated_at) VALUES(?,?,?)", (name, description or "", store.now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _validate_rule(r: dict) -> None:
    kind = r.get("kind")
    val = str(r.get("value", "")).strip()
    if kind not in RULE_KINDS:
        raise ClassifierError(f"Неизвестный вид правила «{kind}» (mask, range, list, specode, units)")
    if not val:
        raise ClassifierError("У правила пустое значение")
    if kind == "range":
        parts = [x.strip() for x in val.split("..")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ClassifierError(f"Диапазон «{val}» должен быть вида 150..157")


def replace_classifier(cid: int, doc: dict) -> dict:
    """Полная замена документа. Группы/правила с известными id (этого классификатора) обновляются, остальные создаются
    (клиентские временные id заменяются, ссылки parent_id/group_id переписываются), отсутствующие удаляются."""
    conn = store.connect()
    try:
        c = conn.execute("SELECT id FROM classifiers WHERE id=?", (cid,)).fetchone()
        if not c:
            raise ClassifierError("Классификатор не найден")
        groups = doc.get("groups") or []
        rules = doc.get("rules") or []
        for r in rules:
            _validate_rule(r)
        existing_g = {r[0] for r in conn.execute("SELECT id FROM cls_groups WHERE classifier_id=?", (cid,))}
        existing_r = {r[0] for r in conn.execute("SELECT id FROM cls_rules WHERE classifier_id=?", (cid,))}
        idmap: dict = {}
        for i, g in enumerate(groups):
            gid = g.get("id")
            if isinstance(gid, int) and gid in existing_g and gid not in idmap:
                idmap[gid] = gid
                conn.execute("UPDATE cls_groups SET code=?, name=?, sort=? WHERE id=?",
                             (str(g.get("code", "")), str(g.get("name", "")), g.get("sort", i), gid))
            else:
                cur = conn.execute("INSERT INTO cls_groups(classifier_id, parent_id, code, name, sort) VALUES(?,?,?,?,?)",
                                   (cid, None, str(g.get("code", "")), str(g.get("name", "")), g.get("sort", i)))
                idmap[gid if gid is not None else f"_new{i}"] = cur.lastrowid
        for i, g in enumerate(groups):
            gid = g.get("id")
            real = idmap.get(gid if gid is not None else f"_new{i}")
            parent = g.get("parent_id")
            if parent is not None and parent not in idmap:
                raise ClassifierError(f"Группа {g.get('code') or gid}: родитель {parent} не найден в документе")
            if parent is not None and idmap[parent] == real:
                raise ClassifierError("Группа не может быть родителем самой себе")
            conn.execute("UPDATE cls_groups SET parent_id=? WHERE id=?", (idmap[parent] if parent is not None else None, real))
        # проверка на циклы
        parents = {r[0]: r[1] for r in conn.execute("SELECT id, parent_id FROM cls_groups WHERE classifier_id=?", (cid,))}
        for gid in parents:
            seen, x = set(), gid
            while x is not None:
                if x in seen:
                    raise ClassifierError("В дереве групп обнаружен цикл")
                seen.add(x)
                x = parents.get(x)
        for gid in existing_g - set(idmap.values()):
            conn.execute("DELETE FROM cls_groups WHERE id=?", (gid,))
        keep_r = set()
        for r in rules:
            gid = r.get("group_id")
            if gid not in idmap:
                raise ClassifierError(f"Правило «{r.get('value')}»: группа {gid} не найдена в документе")
            vals = (idmap[gid], r["kind"], str(r["value"]).strip(), r.get("year_from"), r.get("year_to"), int(r.get("priority") or 0))
            rid = r.get("id")
            if isinstance(rid, int) and rid in existing_r and rid not in keep_r:
                conn.execute("UPDATE cls_rules SET group_id=?, kind=?, value=?, year_from=?, year_to=?, priority=? WHERE id=?", (*vals, rid))
                keep_r.add(rid)
            else:
                cur = conn.execute("INSERT INTO cls_rules(classifier_id, group_id, kind, value, year_from, year_to, priority) VALUES(?,?,?,?,?,?,?)",
                                   (cid, *vals))
                keep_r.add(cur.lastrowid)
        for rid in existing_r - keep_r:
            conn.execute("DELETE FROM cls_rules WHERE id=?", (rid,))
        name = (doc.get("name") or "").strip()
        if name:
            conn.execute("UPDATE classifiers SET name=?, description=?, updated_at=? WHERE id=?",
                         (name, doc.get("description", "") or "", store.now(), cid))
        else:
            conn.execute("UPDATE classifiers SET updated_at=? WHERE id=?", (store.now(), cid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_classifier(cid)


def delete_classifier(cid: int) -> bool:
    conn = store.connect()
    try:
        n = conn.execute("DELETE FROM classifiers WHERE id=?", (cid,)).rowcount
        conn.execute("DELETE FROM cls_groups WHERE classifier_id=?", (cid,))
        conn.execute("DELETE FROM cls_rules WHERE classifier_id=?", (cid,))
        conn.commit()
        return n > 0
    finally:
        conn.close()


def copy_classifier(cid: int) -> int | None:
    src = get_classifier(cid)
    if not src:
        return None
    new_id = create_classifier(f"{src['name']} (копия)", src["description"])
    doc = {"name": f"{src['name']} (копия)", "description": src["description"],
           "groups": [{**g, "id": f"t{g['id']}", "parent_id": (f"t{g['parent_id']}" if g["parent_id"] is not None else None)}
                      for g in src["groups"]],
           "rules": [{**r, "id": None, "group_id": f"t{r['group_id']}"} for r in src["rules"]]}
    replace_classifier(new_id, doc)
    return new_id


# ---- импорт из меток Logo ------------------------------------------------------------------------

def _natural_key(code: str):
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"([0-9]+)", code) if p != ""]


def import_specode(year: int, name: str) -> int:
    """Создаёт классификатор из SPECODE листьев плана года: группа на каждое значение метки,
    иерархия по «/» («76» → «76/1»); у каждой группы правило specode (год = year)."""
    fm = acc_mod.firm_for_year(year)
    if not fm:
        raise ClassifierError(f"Нет загруженной фирмы за {year} год")
    conn = cache.connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT a.code, a.name, a.specode, a.level FROM accounts a WHERE a.firm=? AND a.specode<>'' ORDER BY a.level, a.code", (fm["firm"],))]
    finally:
        conn.close()
    if not rows:
        raise ClassifierError(f"В плане счетов {year} года нет меток (SPECODE)")
    over = store.name_overrides(year)
    sample: dict[str, str] = {}
    for r in rows:  # первая (самая верхняя) запись с этим значением даёт пример названия
        sample.setdefault(r["specode"], over.get(r["code"], r["name"]))
    values = sorted(sample, key=_natural_key)
    all_codes = set(values)
    for v in values:  # родители вида «76» для «76/1»
        parts = v.split("/")
        for i in range(1, len(parts)):
            all_codes.add("/".join(parts[:i]))
    codes_sorted = sorted(all_codes, key=_natural_key)
    cid = create_classifier(name or f"Метки Logo {year}", f"Импорт меток SPECODE за {year} год")
    gid_by_code: dict[str, int] = {}
    conn = store.connect()
    try:
        for i, code in enumerate(codes_sorted):
            parent = None
            if "/" in code:
                parent = gid_by_code.get(code.rsplit("/", 1)[0])
            nm = f"{code} · {sample[code]}" if code in sample else code
            cur = conn.execute("INSERT INTO cls_groups(classifier_id, parent_id, code, name, sort) VALUES(?,?,?,?,?)", (cid, parent, code, nm, i))
            gid_by_code[code] = cur.lastrowid
        for code in values:
            conn.execute("INSERT INTO cls_rules(classifier_id, group_id, kind, value, year_from, year_to, priority) VALUES(?,?,?,?,?,?,0)",
                         (cid, gid_by_code[code], "specode", code, year, year))
        conn.commit()
    finally:
        conn.close()
    return cid


def refresh_specode_names() -> int:
    """Обновляет названия групп классификаторов, созданных импортом SPECODE («значение · название счёта»), по текущим названиям планов
    (нужно после исправления кодировки: названия в Logo читались с потерей İ Ş ş ğ –). Переименованные вручную группы не трогает.
    Возвращает число обновлённых групп."""
    import re
    n = 0
    cs = store.connect()
    try:
        for c in cs.execute("SELECT id, description FROM classifiers").fetchall():
            m = re.match(r"Импорт меток SPECODE за (\d{4}) год", c["description"] or "")
            if not m:
                continue
            year = int(m.group(1))
            fm = acc_mod.firm_for_year(year)
            if not fm:
                continue
            cc = cache.connect()
            try:
                rows = [dict(r) for r in cc.execute(
                    "SELECT code, name, specode FROM accounts WHERE firm=? AND specode<>'' ORDER BY level, code", (fm["firm"],))]
            finally:
                cc.close()
            over = store.name_overrides(year)
            sample: dict[str, str] = {}
            for r in rows:
                sample.setdefault(r["specode"], over.get(r["code"], r["name"]))
            for g in cs.execute("SELECT id, code, name FROM cls_groups WHERE classifier_id=?", (c["id"],)).fetchall():
                if g["code"] in sample and (g["name"] or "").startswith(f"{g['code']} · "):
                    new_name = f"{g['code']} · {sample[g['code']]}"
                    if new_name != g["name"]:
                        cs.execute("UPDATE cls_groups SET name=? WHERE id=?", (new_name, g["id"]))
                        n += 1
        cs.commit()
    finally:
        cs.close()
    return n


# ---- разрешение счёт → группа --------------------------------------------------------------------------

@dataclass
class Res:
    kind: str                      # ok | conflict | none
    group: int | None = None
    groups: list[int] = field(default_factory=list)


class Resolver:
    def __init__(self, classifier_id: int):
        doc = get_classifier(classifier_id)
        if not doc:
            raise ClassifierError(f"Классификатор {classifier_id} не найден")
        self.id = classifier_id
        self.name = doc["name"]
        self.groups = {g["id"]: g for g in doc["groups"]}
        self.rules = doc["rules"]
        self.children: dict[int | None, list[int]] = {}
        for g in sorted(doc["groups"], key=lambda g: (g["sort"], g["id"])):
            self.children.setdefault(g["parent_id"], []).append(g["id"])

    def path(self, gid: int) -> list[int]:
        out = []
        x: int | None = gid
        while x is not None:
            out.append(x)
            x = self.groups[x]["parent_id"]
        return out[::-1]

    def descendants(self, gid: int) -> set[int]:
        out, stack = set(), [gid]
        while stack:
            x = stack.pop()
            out.add(x)
            stack.extend(self.children.get(x, []))
        return out

    @staticmethod
    def _spec_of(rule: dict, code: str, specode: str, units: str = "") -> int | None:
        """Специфичность совпадения правила (None — не совпало)."""
        kind, val = rule["kind"], str(rule["value"]).strip()
        if kind == "mask":
            return acc_mod.mask_specificity(code, val) if acc_mod.code_matches(code, val) else None
        if kind == "range":
            return acc_mod.mask_specificity(code, val) if acc_mod.code_matches(code, val.replace("...", "..")) else None
        if kind == "list":
            best = None
            for item in re.split(r"[,;\s]+", val):
                if item and acc_mod.code_matches(code, item):
                    s = acc_mod.mask_specificity(code, item)
                    best = s if best is None or s > best else best
            return best
        if kind == "specode":
            return len(val) if specode == val else None
        if kind == "units":   # единица счёта из плана Logo (UNITS): `$`, kg, TMM...; регистр не важен; `$` - валютные счета
            return len(val) if (units or "").strip().lower() == val.lower() else None
        return None

    def resolve(self, year: int | None, accounts: list[tuple]) -> dict[str, Res]:
        """accounts: [(код, specode)] или [(код, specode, units)] (units нужен правилам вида `units`)."""
        rules = [r for r in self.rules if r["group_id"] in self.groups and
                 (year is None or ((r["year_from"] is None or year >= r["year_from"]) and (r["year_to"] is None or year <= r["year_to"])))]
        out: dict[str, Res] = {}
        for item in accounts:
            code, specode = item[0], item[1]
            units = item[2] if len(item) > 2 else ""
            best: list[tuple[int, int, int]] = []  # (priority, spec, group)
            for r in rules:
                s = self._spec_of(r, code, specode or "", units or "")
                if s is not None:
                    best.append((r["priority"], s, r["group_id"]))
            if not best:
                out[code] = Res("none")
                continue
            top = max((p, s) for p, s, _ in best)
            gids = sorted({g for p, s, g in best if (p, s) == top})
            out[code] = Res("ok", gids[0]) if len(gids) == 1 else Res("conflict", None, gids)
        return out


def resolve_year(cid: int, year: int) -> dict:
    """GET /classifiers/{id}/resolve: по счетам года, у которых есть проводки (включая açılış)."""
    rs = Resolver(cid)
    fm = acc_mod.firm_for_year(year)
    if not fm:
        raise ClassifierError(f"Нет загруженной фирмы за {year} год")
    conn = cache.connect()
    try:
        act = {r["account"]: r["t"] for r in conn.execute(
            "SELECT account, SUM(CASE WHEN is_opening=0 THEN dt+kt ELSE 0 END) AS t FROM lines WHERE firm=? GROUP BY account", (fm["firm"],))}
        plan = {r["code"]: dict(r) for r in conn.execute("SELECT code, name, specode, units FROM accounts WHERE firm=?", (fm["firm"],))}
    finally:
        conn.close()
    over = store.name_overrides(year)
    div = fm["divisor"]
    accs = [(c, plan.get(c, {}).get("specode", ""), plan.get(c, {}).get("units") or "") for c in sorted(act)]
    res = rs.resolve(year, accs)
    mapped, unmapped, conflicts = [], [], []
    for c, *_ in accs:
        name = over.get(c, plan.get(c, {}).get("name", ""))
        r = res[c]
        if r.kind == "ok":
            mapped.append({"account": c, "name": name, "group_id": r.group})
        elif r.kind == "conflict":
            conflicts.append({"account": c, "name": name, "groups": r.groups})
        else:
            unmapped.append({"account": c, "name": name, "turnover": round(act[c] / 100 / div, 2)})
    unmapped.sort(key=lambda x: -x["turnover"])
    return {"mapped": mapped, "unmapped": unmapped, "conflicts": conflicts}
