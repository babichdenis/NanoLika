---
name: office-xlsx
description: Make or fix a spreadsheet (.xlsx): typed data, formulas that add up, a formatted header, filters, totals and a chart, checked for numbers stored as text. Use when the user asks for a table, a budget, a price list, a tracker or a calculation in Excel.
channel: mixed
metadata:
  author: nanoLika
  version: "1"
---

# Excel workbooks

`openpyxl` is in the runtime; `python_execute` runs it in the workspace. Never `pip install`.
A workbook with a macro and a button is `office_xlsm`, not this skill.

## Shape of the job

1. In one `ask_user`: what the columns are, what must be calculated, and whether the person will keep adding rows. A tracker needs room to grow; a report does not.
2. Decide the layout before writing code: header row on row 1, no merged cells above it, one row per record, totals at the end or in a summary sheet pinned first.
3. Write the file, then **read it back** with `openpyxl.load_workbook(data_only=True)` and print a few rows: a formula that Excel cannot parse, or numbers stored as text, is invisible until someone opens the file.

## The script

- `Workbook()`, `ws.title = "…"`; a second sheet `"Summary"` when there is something to sum up, and `wb.move_sheet` it to the front.
- Numbers are numbers: `cell.value = 12.5`, `cell.number_format = '#,##0.00 ₽'` (or `'0.0%'`, `'yyyy-mm-dd'`). Never write `"1 200 ₽"` as a string - it cannot be summed.
- Formulas start with `=`: `=SUM(B2:B100)`, `=IFERROR(VLOOKUP(...),"")`, `=SUMPRODUCT(...)`. Sum over a range with empty rows to the end of the planned table, not over exactly the filled rows.
- Header: bold, `PatternFill` with a light colour, `Font(color="FFFFFF")` on the dark fill, `ws.freeze_panes = "A2"`, `ws.auto_filter.ref = ws.dimensions`.
- Widths: `ws.column_dimensions["B"].width = 18` - a column of `#` is a bug the user sees.
- Chart: `BarChart` / `LineChart` / `PieChart` from `openpyxl.chart`, `chart.add_data(data, titles_from_data=True)`, `ws.add_chart(chart, "H2")`.
- Conditional formatting for anything that is a signal (spend over the limit, a date in the past): `CellIsRule`, not colours painted per cell.
- Save to `sheets/<name>.xlsx`. The chat shows the file as a card by itself.

## Checking before handing over

- No `#REF!`, `#DIV/0!`, `#NAME?` - open the workbook with `data_only=True`; a cached `None` where a number was expected means the formula never ran.
- Every total equals the same total computed in plain Python from the rows you wrote.
- Dates stay dates (a `datetime.date`, not `"01.02.2026"`).
- The person's own file is never overwritten: a fix goes to `…-fixed.xlsx` next to it unless they asked for the same file.
