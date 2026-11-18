"""Профиль плана счетов: разделитель/ширины сегментов, корни, классы. Заменяет жёсткую привязку к точке «.».

Два режима разбора кода:
- ``sep``  — сегменты разделены строкой ``sep`` (``329.01.002`` при sep="."); уровень = число разделителей.
- ``width``— разделителя нет; код режется по накопленным ширинам ``widths`` (``[4,6,8]``: ``10001001`` -> ``1000``,``10``,``01``).
  Ширина, не меньшая длины кода, границей не считается (последняя ширина — просто длина самого длинного кода).

Для sep="." все методы дают ровно то, что давал прежний код (проверяется тестом test_plan.py).

Условие «потомки» в SQL: ``descendant_sql(column, code)``.
- sep: ``(col=? OR substr(col,1,len(code)+len(sep))=?)`` c параметрами ``[code, code+sep]``.
- width: если ``len(code)`` — граница сегмента, ``(col=? OR (substr(col,1,n)=? AND length(col)>n))``;
  иначе (код не на границе) потомков нет — только точное совпадение ``col=?``.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List, Optional, Set, Tuple

DEFAULT_PARAMS = {
    "template": "turkish",
    "mode": "sep",
    "sep": ".",
    "root_len": 3,
    "widths": [],
    "balance_roots": ["1", "2", "3", "4", "5"],
    "pnl_roots": ["6", "7"],
    "noncarried": ["6*", "7*", "590"],
}

SEP_CANDIDATES = (".", "-", "/", ":", " ")
SEP_THRESHOLD = 0.15


def _copy_defaults() -> dict:
    d = dict(DEFAULT_PARAMS)
    for k in ("widths", "balance_roots", "pnl_roots", "noncarried"):
        d[k] = list(DEFAULT_PARAMS[k])
    return d


class PlanProfile:
    def __init__(self, template: str = "turkish", mode: str = "sep", sep: str = ".", root_len: int = 3,
                 widths: Optional[List[int]] = None, balance_roots: Optional[List[str]] = None,
                 pnl_roots: Optional[List[str]] = None, noncarried: Optional[List[str]] = None) -> None:
        self.template = template if template in ("turkish", "none") else "none"
        self.sep = sep or ""
        self.mode = "width" if (mode == "width" or not self.sep) else "sep"
        self.root_len = int(root_len or 3)
        ws = sorted({int(w) for w in (widths or []) if int(w) > 0})
        if self.mode == "width" and not ws:
            ws = [self.root_len]
        self.widths = ws
        self.balance_roots = [str(x) for x in (balance_roots or [])]
        self.pnl_roots = [str(x) for x in (pnl_roots or [])]
        self.noncarried = [str(x) for x in (noncarried or [])]

    # ---- конструкторы / сериализация ----
    @classmethod
    def from_params(cls, d: Optional[dict]) -> "PlanProfile":
        p = _copy_defaults()
        for k, v in (d or {}).items():
            if k in p and v is not None:
                p[k] = v
        return cls(**p)

    def to_params(self) -> dict:
        return {"template": self.template, "mode": self.mode, "sep": self.sep, "root_len": self.root_len,
                "widths": list(self.widths), "balance_roots": list(self.balance_roots),
                "pnl_roots": list(self.pnl_roots), "noncarried": list(self.noncarried)}

    def __repr__(self) -> str:
        return "PlanProfile(%r)" % (self.to_params(),)

    # ---- разбор кода ----
    def parts(self, code: str) -> List[str]:
        if self.mode == "sep":
            return code.split(self.sep)
        out, prev = [], 0
        for w in self.widths:
            if w >= len(code):
                break
            out.append(code[prev:w])
            prev = w
        out.append(code[prev:])
        return out

    def level_of(self, code: str) -> int:
        return len(self.parts(code)) - 1

    def root_of(self, code: str) -> str:
        return self.parts(code)[0]

    def is_root(self, code: str) -> bool:
        return self.level_of(code) == 0

    def _join(self, parts: List[str]) -> str:
        return (self.sep if self.mode == "sep" else "").join(parts)

    def prefixes(self, code: str) -> List[str]:
        """Собственные префиксы (по частям) и затем сам код."""
        ps = self.parts(code)
        return [self._join(ps[:i]) for i in range(1, len(ps) + 1)]

    def trunc(self, code: str, level: int) -> str:
        return self._join(self.parts(code)[: level + 1])

    def parent_of(self, code: str, codes: Set[str]) -> Optional[str]:
        for cand in reversed(self.prefixes(code)[:-1]):
            if cand in codes:
                return cand
        return None

    def leaf_set(self, codes: Iterable[str]) -> Set[str]:
        codes = set(codes)
        has_child: Set[str] = set()
        for c in codes:
            has_child.update(self.prefixes(c)[:-1])
        return codes - has_child

    def mask_name(self, mask: str) -> str:
        """Подпись маски без звёздочки и хвостового разделителя (`329.01*` -> `329.01`)."""
        m = mask.rstrip("*")
        return m.rstrip(self.sep) if self.sep else m

    # ---- потомки / маски ----
    def child_prefix(self, code: str) -> str:
        """Префикс прямых и дальних потомков: code+sep (режим sep). В режиме width — пусто: использовать descendant_sql."""
        return code + self.sep if self.mode == "sep" else ""

    def is_descendant_or_self(self, code: str, base: str) -> bool:
        if self.mode == "sep":
            return code == base or code.startswith(base + self.sep)
        return base in self.prefixes(code)

    def descendant_sql(self, column: str, code: str) -> Tuple[str, list]:
        if self.mode == "sep":
            n = len(code) + len(self.sep)
            return "(%s=? OR substr(%s,1,%d)=?)" % (column, column, n), [code, code + self.sep]
        n = len(code)
        if n in self.widths:
            return ("(%s=? OR (substr(%s,1,%d)=? AND length(%s)>%d))" % (column, column, n, column, n)), [code, code]
        return "%s=?" % column, [code]

    def code_matches(self, code: str, mask: str) -> bool:
        mask = mask.strip()
        if not mask:
            return False
        if mask == "*":
            return True
        if ".." in mask:
            lo, hi = (x.strip() for x in mask.split("..", 1))
            c_lo = self.trunc(code, len(self.parts(lo)) - 1)
            c_hi = self.trunc(code, len(self.parts(hi)) - 1)
            return c_lo >= lo and c_hi <= hi
        if mask.endswith("*"):
            return code.startswith(mask[:-1])
        return self.is_descendant_or_self(code, mask)

    def account_sql(self, masks: List[str], column: str = "account") -> Tuple[str, list]:
        parts, params = [], []  # type: List[str], list
        for m in masks:
            m = m.strip()
            if not m:
                continue
            if m.endswith("*"):
                p = m[:-1]
                parts.append("substr(%s,1,%d)=?" % (column, len(p)))
                params.append(p)
            else:
                s, pr = self.descendant_sql(column, m)
                parts.append(s)
                params.extend(pr)
        return (" OR ".join(parts) or "0"), params

    # ---- классы счетов ----
    def is_noncarried(self, code: str) -> bool:
        return any(self.code_matches(code, m) for m in self.noncarried)

    def is_balance_root(self, code: str) -> bool:
        r = self.root_of(code)
        return any(r.startswith(x) for x in self.balance_roots if x)

    def is_pnl_root(self, code: str) -> bool:
        r = self.root_of(code)
        return any(r.startswith(x) for x in self.pnl_roots if x)


# ---- автоопределение ----
def _is_turkish_root(r: str) -> bool:
    return len(r) == 3 and r.isdigit() and r[0] in "1234567"


def detect_profile(codes: Iterable[str]) -> Dict[str, dict]:
    """{"profile": {...параметры PlanProfile...}, "stats": {codes, sep_share, root_len_share, turkish_share}}."""
    cs = sorted({(c or "").strip() for c in codes if (c or "").strip()})
    n = len(cs)
    if n == 0:
        return {"profile": _copy_defaults(), "stats": {"codes": 0, "sep_share": 0.0, "root_len_share": 0.0, "turkish_share": 0.0}}
    shares = {s: sum(1 for c in cs if s in c) / n for s in SEP_CANDIDATES}
    best = max(SEP_CANDIDATES, key=lambda s: (shares[s], -SEP_CANDIDATES.index(s)))
    sep_share = shares[best]
    if sep_share >= SEP_THRESHOLD:
        mode, sep, widths = "sep", best, []
        firsts = [c.split(sep)[0] for c in cs]
        root_len = Counter(len(f) for f in firsts).most_common(1)[0][0]
    else:
        mode, sep = "width", ""
        cset = set(cs)
        cnt = Counter()  # type: Counter
        for c in cs:
            for L in range(1, len(c)):
                if c[:L] in cset:
                    cnt[L] += 1
        thr = max(1, int(0.01 * n))
        ws = sorted(L for L, k in cnt.items() if k >= thr)
        ws.append(max(len(c) for c in cs))
        widths = sorted(set(ws))
        root_len = widths[0]
        firsts = [c[:root_len] for c in cs]
    root_len_share = sum(1 for f in firsts if len(f) == root_len) / n
    turkish_share = sum(1 for f in firsts if _is_turkish_root(f)) / n
    template = "turkish" if turkish_share >= 0.95 else "none"
    prof = _copy_defaults()
    prof.update({"template": template, "mode": mode, "sep": sep or ".", "root_len": root_len, "widths": widths})
    if mode == "width":
        prof["sep"] = ""
    if template == "none":
        prof.update({"balance_roots": [], "pnl_roots": [], "noncarried": []})
    return {"profile": prof,
            "stats": {"codes": n, "sep_share": round(sep_share, 4), "root_len_share": round(root_len_share, 4),
                      "turkish_share": round(turkish_share, 4)}}


def current() -> PlanProfile:
    """Профиль плана счетов текущего набора (из dataset_params.plan_profile; кэш лежит в рантайме набора, ключ - путь настроек и счётчик изменений)."""
    from . import dsctx, dsparams, store
    rt = dsctx.current()
    key = (str(store.settings_path()), rt.books_ver)
    c = rt.plan_cache
    if c is None or c[0] != key:
        c = rt.plan_cache = (key, PlanProfile.from_params(dsparams.get("plan_profile")))
    return c[1]
