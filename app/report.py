"""Builds the coordinator's Excel review file from an audit. Nothing is stored:
the file is generated on request and handed to the browser."""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

STATUSES = ["Pending", "Approved", "Needs changes"]
MAROON = "500000"
RED_BG, RED_FG = "FDE4E4", "C62828"
GREEN_BG, GREEN_FG = "E4F4E9", "1E6B3A"
AMBER_BG = "FFF3D4"


def _problem(o: dict) -> str:
    parts = [f'"{t}" isn\'t on the allowed list' for t in o.get("avoid_terms", [])]
    parts += [r for r in o.get("reasons", []) if r]
    if not parts and o.get("status") == "not_listed":
        parts.append("Starts with a verb that isn't on the allowed list")
    if not parts and o.get("status") not in (None, "ok"):
        parts.append("Vague or not measurable")
    return "; ".join(dict.fromkeys(parts))


def build_review_xlsx(audit: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Review"
    headers = ["Course", "File", "#", "Learning objective", "Problem found", "Suggested rewrite",
               "Bloom level", "Status", "Coordinator comment"]
    widths = [12, 26, 4, 60, 38, 60, 14, 16, 40]
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=MAROON)
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"

    order = {"avoid": 0, "vague": 1, "no_objectives": 2, "unreadable": 3, "clean": 4}
    results = sorted(audit.get("results", []), key=lambda r: (order.get(r["status"], 5), r["course"]))
    wrap = Alignment(wrap_text=True, vertical="top")
    for r in results:
        f = r.get("file", {})
        name = f.get("name", "")
        if not r.get("objectives"):
            ws.append([r["course"], name, "", "", r.get("summary") or "No learning objectives found",
                       "", "", "Pending", ""])
            row = ws.max_row
            for c in ws[row]:
                c.alignment = wrap
            ws.cell(row, 5).fill = PatternFill("solid", fgColor=AMBER_BG)
            if f.get("link"):
                ws.cell(row, 2).hyperlink = f["link"]
                ws.cell(row, 2).font = Font(color="1155CC", underline="single")
            continue
        for i, o in enumerate(r["objectives"], 1):
            bad = o.get("status") != "ok"
            ws.append([r["course"], name, i, o["text"], _problem(o) if bad else "Looks good",
                       (o.get("suggestion") or "") if bad else "", o.get("bloom_level") or "",
                       "Pending", ""])
            row = ws.max_row
            for c in ws[row]:
                c.alignment = wrap
            if bad:
                ws.cell(row, 5).font = Font(color=RED_FG)
                ws.cell(row, 5).fill = PatternFill("solid", fgColor=RED_BG)
                if o.get("suggestion"):
                    ws.cell(row, 6).fill = PatternFill("solid", fgColor=GREEN_BG)
            else:
                ws.cell(row, 5).font = Font(color=GREEN_FG)
            if f.get("link"):
                ws.cell(row, 2).hyperlink = f["link"]
                ws.cell(row, 2).font = Font(color="1155CC", underline="single")

    last = max(ws.max_row, 2)
    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True,
                        showDropDown=False, errorTitle="Status", error="Pick Pending, Approved or Needs changes")
    ws.add_data_validation(dv)
    dv.add(f"H2:H{last + 500}")
    ws.conditional_formatting.add(f"H2:H{last + 500}", FormulaRule(formula=['$H2="Approved"'],
                                  fill=PatternFill("solid", fgColor=GREEN_BG), font=Font(color=GREEN_FG, bold=True)))
    ws.conditional_formatting.add(f"H2:H{last + 500}", FormulaRule(formula=['$H2="Needs changes"'],
                                  fill=PatternFill("solid", fgColor=RED_BG), font=Font(color=RED_FG, bold=True)))
    ws.auto_filter.ref = f"A1:I{ws.max_row}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"

    # Summary sheet: one row per course
    sm = wb.create_sheet("Summary")
    sm.append(["Course", "File", "Objectives", "Need changes", "Result"])
    for c in sm[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=MAROON)
    for col, w in zip("ABCDE", [12, 30, 12, 14, 44]):
        sm.column_dimensions[col].width = w
    for r in results:
        n = len(r.get("objectives", []))
        bad = sum(1 for o in r.get("objectives", []) if o.get("status") != "ok")
        sm.append([r["course"], r.get("file", {}).get("name", ""), n, bad, r.get("summary", "")])
        if bad or not n:
            sm.cell(sm.max_row, 4 if bad else 5).font = Font(color=RED_FG if bad else "986300", bold=True)
    sm.append([])
    sm.append([f"Folder: {audit.get('folder_name', '')}", "", "", "",
               f"Generated {datetime.now().strftime('%b %d, %Y %I:%M %p')}"])
    sm.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
