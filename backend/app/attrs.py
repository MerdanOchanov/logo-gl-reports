"""Атрибуты ордеров и проводок (волна 7): подразделение, отдел, количество, источник (модуль Logo), автор, дата/время ввода, шифр документа,
доп. описание (GENEXP2-4); справочники для интерфейса (/api/dicts) и подстановка названий."""
from __future__ import annotations

from . import cache

# Источник ордера = EMFICHE.MODULENR. Словарь составлен по данным 24 книг (годы 2003-2026): для каждого значения разобраны типовые тексты
# ордеров (GENEXP1..4 вида «… tarihli <вид документа>.») и счета проводок. confirmed=True - смысл подтверждён текстами и счетами;
# остальные значения называются нейтрально «Модуль N» (см. module_name).
MODULES = {
    0: ("Ручной ввод", True),                       # свободный текст, ордера открытия (TRCODE 1), амортизация, «Virman Islemi», пакетные «kasa/banka/stok işlemleri» 2003-2005
    1: ("Склад (материалы)", True),                 # «Sarf Çıkışları», «Malzeme İşlemleri», «Üretimden Girişler»; счета 150/151/152/730/710
    2: ("Закупки", True),                           # «MAL ALIM FATURASI», «ALINAN HİZMET FATURASI», «Satınalma Faturası»; счета 150/191/320/329
    3: ("Продажи", True),                           # «TOPTAN SATIŞ FATURASI», «Toptan Satış Faturası»; счета 600/391/120
    4: ("Взаиморасчёты с контрагентами", True),     # «Virman İşlemi/Fişi», «Alacak Dekontu» (Cari Hesap); счета 120/320/335/127/131
    6: ("Банк", True),                              # «Gelen Havale», «Gönderilen Havale», «Banka İşlemleri»; счёт 102
    7: ("Касса", True),                             # «Kasa Tahsilatları/Ödemeleri», «CH Tahsilat», «Bankaya Yatırılan», «Muhasebe Tediye»; счёт 100
}
EXTRA_KEYS = ("branch", "department", "qty", "source", "author", "entered_at", "doc_no", "text2_4")


def module_name(mid) -> str:
    if mid is None:
        return "Не указан"
    n = MODULES.get(int(mid))
    return n[0] if n else f"Модуль {int(mid)}"


class Names:
    """Названия подразделений/отделов (по книгам периода; при совпадении номера - название более поздней книги), пользователей, модулей."""

    def __init__(self, firms: list[int]):
        self.branches: dict[int, str] = {}
        self.depts: dict[int, str] = {}
        self.users: dict[int, str] = {}
        conn = cache.connect()
        try:
            for fm in firms:     # порядок книг - по возрастанию года, поздние перекрывают ранние
                for i, n in conn.execute("SELECT id, name FROM branches WHERE firm=?", (fm,)):
                    if n:
                        self.branches[i] = n
                for i, n in conn.execute("SELECT id, name FROM departments WHERE firm=?", (fm,)):
                    if n:
                        self.depts[i] = n
            for i, n in conn.execute("SELECT id, name FROM logo_users"):
                self.users[i] = n
        finally:
            conn.close()

    def branch(self, i) -> str:
        if i is None or i < 0:
            return "Не указано"
        return self.branches.get(i) or f"Подразделение {i}"

    def dept(self, i) -> str:
        if i is None or i < 0:
            return "Не указано"
        return self.depts.get(i) or f"Отдел {i}"

    def author(self, i) -> str:
        if not i:
            return ""
        return self.users.get(i) or f"Пользователь {i}"

    def source(self, i) -> str:
        return module_name(i)


def fiche_attrs(conn, firm: int, fiche_ids) -> dict:
    """{fiche_id: (module, doc_no, created_by, created_at, text_ext)} для списка ордеров одной книги."""
    ids = list(dict.fromkeys(fiche_ids))
    out: dict = {}
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        for r in conn.execute(f"SELECT fiche_id, module, doc_no, created_by, created_at, text_ext FROM fiches WHERE firm=? AND fiche_id IN ({','.join('?' * len(part))})",
                              [firm, *part]):
            out[r[0]] = (r[1], r[2], r[3], r[4], r[5])
    return out


# Описания дополнительных колонок (optional: true) карточки счёта и расшифровки: (ключ, заголовок, тип)
OPTIONAL_COLUMNS = (("branch", "Подразделение", "text"), ("department", "Отдел", "text"), ("qty", "Количество", "qty"),
                    ("source", "Источник", "text"), ("author", "Автор", "text"), ("entered_at", "Дата и время ввода", "text"),
                    ("doc_no", "Шифр документа", "text"), ("text2_4", "Доп. описание", "text"))


def optional_column_defs() -> list[dict]:
    return [{"key": k, "title": t, "type": ty, "drill": None, "optional": True} for k, t, ty in OPTIONAL_COLUMNS]


def row_attrs(names: Names, branch, dept, qty, fa, ids: bool = True) -> dict:
    """Поля строки расшифровки/карточки: branch, department, qty, source, author, entered_at, doc_no, text2_4 (+ *_id при ids=True)."""
    mod, doc, by, at, ext = fa if fa else (None, "", None, None, "")
    out = {"branch": names.branch(branch) if branch is not None else "", "department": names.dept(dept) if dept is not None else "",
           "qty": (round(qty, 3) if qty else None),
           "source": names.source(mod) if mod is not None else "", "author": names.author(by), "entered_at": at or "",
           "doc_no": doc or "", "text2_4": ext or ""}
    if ids:
        out.update({"branch_id": branch, "department_id": dept, "source_id": mod, "author_id": by})
    return out


def dicts(year: int) -> dict:
    """Справочники книги года для фильтров интерфейса: только встречающиеся значения, с числом строк / ордеров."""
    ym = cache.year_firm_map()
    if year not in ym:
        raise ValueError(f"Нет включённой книги за {year} год")
    firm = ym[year]["firm"]
    warn = cache.ensure_attrs([firm])
    names = Names([firm])
    conn = cache.connect()
    try:
        br = [{"id": i, "name": names.branch(i), "lines": n} for i, n in conn.execute(
            "SELECT COALESCE(branch,-1), COUNT(*) FROM lines WHERE firm=? GROUP BY 1 ORDER BY 1", (firm,))]
        dp = [{"id": i, "name": names.dept(i), "lines": n} for i, n in conn.execute(
            "SELECT COALESCE(dept,-1), COUNT(*) FROM lines WHERE firm=? GROUP BY 1 ORDER BY 1", (firm,))]
        src = [{"id": i, "name": names.source(i), "fiches": n, "confirmed": i in MODULES} for i, n in conn.execute(
            "SELECT f.module, COUNT(DISTINCT l.fiche_id) FROM lines l JOIN fiches f ON f.firm=l.firm AND f.fiche_id=l.fiche_id "
            "WHERE l.firm=? AND f.module IS NOT NULL GROUP BY 1 ORDER BY 1", (firm,))]
        au = [{"id": i, "name": names.author(i), "fiches": n} for i, n in conn.execute(
            "SELECT f.created_by, COUNT(DISTINCT l.fiche_id) FROM lines l JOIN fiches f ON f.firm=l.firm AND f.fiche_id=l.fiche_id "
            "WHERE l.firm=? AND f.created_by IS NOT NULL GROUP BY 1 ORDER BY 2 DESC", (firm,))]
    finally:
        conn.close()
    return {"year": year, "firm": firm, "branches": br, "departments": dp, "sources": src, "authors": au, "warnings": warn}
