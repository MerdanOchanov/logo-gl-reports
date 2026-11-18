"""Выгрузка Result в Excel: шапка, двухъярусные заголовки, числа числами, группировка строк (outline), итоги, закрепление."""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

MONEY_FMT = '#,##0.00;[Red]-#,##0.00;;@'   # ноль — пустая ячейка, отрицательные красным
INT_FMT = '#,##0;[Red]-#,##0;;@'
QTY_FMT = '#,##0.###;[Red]-#,##0.###;;@'    # количество (до 3 знаков), ноль — пустая ячейка


def build(result: dict, extra=None) -> bytes:
    """extra - ключи дополнительных колонок (columns.extra определения): колонки с optional=true выводятся, только если выбраны."""
    wb = Workbook()
    ws = wb.active
    ws.title = (result.get("title") or "Отчёт")[:31].replace("/", "-").replace("\\", "-").replace("*", "").replace("?", "").replace(":", "").replace("[", "").replace("]", "")
    extra = list(extra or [])
    cols = [c for c in result["columns"] if not c.get("optional") or c["key"] in extra]
    n_fixed = 2
    ncols = n_fixed + len(cols)
    bold = Font(bold=True)
    thin = Side(style="thin", color="999999")
    head_fill = PatternFill("solid", fgColor="E8EEF4")
    tot_fill = PatternFill("solid", fgColor="F2F2F2")

    ws.cell(1, 1, result.get("title") or "Отчёт").font = Font(bold=True, size=14)
    ws.cell(2, 1, result.get("subtitle") or "")
    ws.cell(3, 1, "Сформировано: " + datetime.now().strftime("%d.%m.%Y %H:%M"))

    r0 = 5   # верхний ярус шапки; нижний — r0+1
    two_tier = any(c.get("group") for c in cols)
    ws.cell(r0, 1, result.get("code_title") or "Код")
    ws.cell(r0, 2, result.get("name_title") or "Наименование")
    if two_tier:
        ws.merge_cells(start_row=r0, start_column=1, end_row=r0 + 1, end_column=1)
        ws.merge_cells(start_row=r0, start_column=2, end_row=r0 + 1, end_column=2)
        i = 0
        while i < len(cols):
            g = cols[i].get("group") or ""
            j = i
            while j + 1 < len(cols) and (cols[j + 1].get("group") or "") == g:
                j += 1
            c1, c2 = n_fixed + i + 1, n_fixed + j + 1
            ws.cell(r0, c1, g)
            if c2 > c1:
                ws.merge_cells(start_row=r0, start_column=c1, end_row=r0, end_column=c2)
            i = j + 1
        for k, c in enumerate(cols):
            ws.cell(r0 + 1, n_fixed + k + 1, c["title"])
        first_data = r0 + 2
    else:
        for k, c in enumerate(cols):
            ws.cell(r0, n_fixed + k + 1, c["title"])
        first_data = r0 + 1
    for r in range(r0, first_data):
        for c in range(1, ncols + 1):
            cell = ws.cell(r, c)
            cell.font = bold
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(top=thin, bottom=thin, left=thin, right=thin)

    ws.sheet_properties.outlinePr.summaryBelow = False
    row = first_data
    for r in result["rows"]:
        is_total = r.get("kind") == "total"
        ws.cell(row, 1, r.get("code") or "")
        nm = ws.cell(row, 2, r.get("name") or "")
        nm.alignment = Alignment(indent=min(int(r.get("level") or 0), 10))
        for k, c in enumerate(cols):
            v = (r.get("values") or {}).get(c["key"])
            cell = ws.cell(row, n_fixed + k + 1)
            if v is None:
                continue
            if c.get("type") == "text":
                cell.value = str(v)
            else:
                cell.value = v
                cell.number_format = INT_FMT if c.get("type") == "int" else QTY_FMT if c.get("type") == "qty" else MONEY_FMT
                cell.alignment = Alignment(horizontal="right")
        if r.get("bold") or is_total:
            for c in range(1, ncols + 1):
                ws.cell(row, c).font = bold
        if is_total:
            for c in range(1, ncols + 1):
                ws.cell(row, c).fill = tot_fill
                ws.cell(row, c).border = Border(top=thin, bottom=thin)
        elif (r.get("level") or 0) > 0:
            ws.row_dimensions[row].outlineLevel = min(int(r["level"]), 7)
        row += 1
    last = row - 1

    row += 1
    if result.get("checks"):
        ws.cell(row, 1, "Контроли").font = bold
        row += 1
        for ch in result["checks"]:
            lv = ch.get("level") or ("ok" if ch.get("ok") else "error")
            ws.cell(row, 1, {"ok": "OK", "warn": "ПРЕДУПРЕЖДЕНИЕ"}.get(lv, "ОШИБКА"))
            ws.cell(row, 2, f"{ch['name']}: {ch.get('detail', '')}")
            row += 1
    if result.get("warnings"):
        row += 1
        ws.cell(row, 1, "Предупреждения").font = bold
        row += 1
        for w in result["warnings"]:
            ws.cell(row, 2, w)
            row += 1

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 46
    wide = len(cols) > 14     # широкая шахматка / журнал: узкие графы, высокая шапка
    for k, c in enumerate(cols):
        if c.get("type", "money") in ("money", "qty"):
            w = 13 if wide else 18
        else:
            w = 14 if c.get("key") in ("account", "corr", "fiche_no", "method") else 40 if c.get("key") == "text2_4" else 18
        ws.column_dimensions[get_column_letter(n_fixed + k + 1)].width = w
    if wide:
        for r in range(r0, first_data):
            ws.row_dimensions[r].height = 32
    ws.freeze_panes = ws.cell(first_data, n_fixed + 1)
    ws.print_title_rows = f"{r0}:{first_data - 1}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    from openpyxl.worksheet.properties import PageSetupProperties
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    if last >= first_data:
        ws.auto_filter.ref = None
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
