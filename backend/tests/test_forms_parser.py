"""Парсер формул форм: корректные выражения, ошибки с позициями, проверка формы целиком (без обращения к данным)."""
import pytest

from app.engine import form as F


def test_parse_ok_variants():
    ast = F.parse_formula("SKD(100*)+SKD(102*)-[010]*2/(1+3)")
    assert [s.text for s in F.selectors_of(ast)] == ["100*", "102*"]
    assert F.refs_of(ast) == [("010", 17)] or [r for r, _ in F.refs_of(ast)] == ["010"]
    assert F.parse_formula("-SN(1*)")[0] == "neg"
    assert F.parse_formula("--5")[0] == "neg"
    assert F.parse_formula("  skd ( 100.01* ) ")[0] == "fn"          # регистр и пробелы
    assert F.parse_formula("СКД(100*)")[1] == "SKD"                   # русские синонимы
    assert F.parse_formula("ОБДК(100*, 320*)")[1] == "OBDK"


def test_selector_syntax():
    s = F.parse_selector("1* ~ 150*|153*")
    assert [t[1] for t in s.include] == ["1*"] and [t[1] for t in s.exclude] == ["150*", "153*"]
    s = F.parse_selector("150..157 | 100* | @3:76/1")
    assert s.include == (("range", "150..157"), ("mask", "100*"), ("group", 3, "76/1"))
    assert F.parse_selector("*").include == (("mask", "*"),)
    assert F.parse_selector("320.01.1.04/AS").include == (("mask", "320.01.1.04/AS"),)


@pytest.mark.parametrize("text, pos, fragment", [
    ("SKD(100*", 3, "Не закрыта скобка функции"),
    ("FOO(1)", 0, "Неизвестная функция"),
    ("SKD(1) +", 8, "Формула оборвана"),
    ("SKD(1))", 6, "Лишний символ"),
    ("[010", 0, "Не закрыта ссылка"),
    ("[ ]", 0, "Пустая ссылка"),
    ("OBDK(1*)", 0, "принимает аргументов: 2"),
    ("SKD()", 4, "Пустой селектор"),
    ("SKD((1))", 4, "скобки не допускаются"),
    ("1 2", 2, "Лишний символ"),
    ("SKD(1* ~ )", 9, "Пустой элемент"),
    ("SKD(1*2)", 4, "Звёздочка допускается только в конце"),
    ("SKD(@x:1)", 4, "Группа задаётся как"),
    ("SKD(150..)", 4, "Диапазон должен быть"),
    ("SKD 100", 4, "ожидается «(»"),
    ("()", 1, "Неожиданный символ"),
    ("10a", 0, "Недопустимое число"),
    ("SKD(1*) $", 8, "Лишний символ"),
    ("", None, "Пустая формула"),
])
def test_parse_errors_have_russian_text_and_position(text, pos, fragment):
    with pytest.raises(F.FormulaError) as ei:
        F.parse_formula(text)
    e = ei.value
    assert fragment in e.message, e.message
    if pos is not None:
        assert e.pos == pos, (text, e.pos, e.message)
        assert f"позиция {pos + 1}" in str(e)


def test_no_eval_injection():
    for bad in ["__import__('os').system('x')", "SKD(100*) ** 2", "SKD(100*) // 2", "SKD(1*); 1", "a=1"]:
        with pytest.raises(F.FormulaError):
            F.parse_formula(bad)


def test_deep_nesting_is_rejected_not_crashed():
    with pytest.raises(F.FormulaError):
        F.parse_formula("(" * 500 + "1" + ")" * 500)
    with pytest.raises(F.FormulaError):
        F.parse_formula("-" * 500 + "1")
    with pytest.raises(F.FormulaError):
        F.parse_formula("1+" * 2000 + "1")


def test_check_form_errors():
    form = {"lines": [
        {"code": "010", "name": "a", "formula": "SKD(100*)"},
        {"code": "010", "name": "dup", "formula": "SKD(101*)"},
        {"code": "020", "formula": "[010]+[999]"},
        {"code": "030", "formula": "[040]+1"},
        {"code": "040", "formula": "[030]+1"},
        {"code": "H", "name": "Заголовок"},
        {"code": "050", "formula": "[H]"},
        {"code": "060", "formula": "SKD(1*", "cell": "ZZZ"},
        {"code": "", "formula": "1"},
    ], "checks": [{"name": "c", "left": "[010]", "right": "[777]"}]}
    lines, checks, errors = F.check_form(form)
    msgs = " | ".join(e["message"] for e in errors)
    assert "повторяется" in msgs
    assert "несуществующую строку [999]" in msgs
    assert "Циклическая ссылка" in msgs
    assert "заголовок без формулы" in msgs
    assert "Не закрыта скобка функции" in msgs
    assert "Некорректный адрес ячейки" in msgs
    assert "не задан код" in msgs
    assert any(e["line"] == "check:c" and "[777]" in e["message"] for e in errors)
    text = F.format_errors(errors)
    assert text.startswith("Ошибки в форме:")


def test_split_cell():
    assert F.split_cell("D12") == (None, "D12")
    assert F.split_cell("Лист1!d12") == ("Лист1", "D12")
    assert F.split_cell("'Баланс 1'!$C$5") == ("Баланс 1", "C5")
    with pytest.raises(ValueError):
        F.split_cell("12D")
    with pytest.raises(ValueError):
        F.split_cell("!A1")


def test_validate_formula_and_form():
    assert F.validate_formula("SKD(100*)+[010]", codes=["010"])["ok"]
    r = F.validate_formula("SKD(100*)+[011]", codes=["010"])
    assert not r["ok"] and r["errors"][0]["pos"] == 10
    r = F.validate_formula("SKD(")
    assert not r["ok"] and r["errors"][0]["message"]
    ok = F.validate_text({"lines": [{"code": "1", "formula": "SKD(100*)"}, {"code": "2", "formula": "[1]*2"}]})
    assert ok["ok"] and ok["lines"][1]["refs"] == ["1"]
