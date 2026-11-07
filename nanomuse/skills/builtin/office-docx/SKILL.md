---
name: office-docx
description: Make or edit a Word document (.docx) with real styles, headings, tables, page numbers and a cover, then hand it over or turn it into a PDF. Use when the user asks for a document, a letter, a report, a contract draft or a memo as a Word file.
channel: mixed
metadata:
  author: nanoLika
  version: "1"
---

# Word documents

`python-docx` is in the runtime; `python_execute` runs it in the workspace. Never `pip install` - the runtime is frozen.

## Shape of the job

1. Ask for what is missing in **one** `ask_user`: who it is for, what it must contain, length, whether it is a draft to edit or a finished PDF. Do not ask about fonts.
2. Write the content first as an outline in your head (or as `outline.md`): title, sections, what goes in each. A document that says something beats a beautiful empty one.
3. Build the file with `python_execute`, then `office_convert` when a PDF is wanted.

## The script

- `Document()`, then styles, never manual font fiddling: `doc.styles["Heading 1"]`, `"Heading 2"`, `"Normal"`. Headings make the navigation pane and a table of contents work.
- Title page: the title in `Title`, the subtitle, the author, the date - then `doc.add_page_break()`.
- Body: `add_paragraph` for prose, `add_heading(level=…)` for sections, `add_bullet`-style paragraphs with `style="List Bullet"` / `"List Number"` for lists.
- Tables: `doc.add_table(rows=1, cols=n, style="Table Grid")`; header row bold, repeat it with `tblHeader` when the table runs over pages.
- Page numbers and a footer: a field in the footer -
  ```python
  from docx.oxml.ns import qn

  run = doc.sections[0].footer.paragraphs[0].add_run()
  fld = run._r.makeelement(qn("w:fldSimple"), {qn("w:instr"): "PAGE"})
  run._r.append(fld)
  ```
- Language and spacing: set `doc.styles["Normal"].font.name` and `paragraph_format.space_after` once, not per paragraph.
- Save to `documents/<name>.docx` (a folder per job when there are several files). The chat shows the file as a card by itself.

## Rules

- Text the user will sign (contracts, letters to a company, CVs) is theirs: keep their wording where they gave it, mark anything you added as a suggestion in a comment or a separate list, never quietly rewrite a name, a sum or a date.
- One document, one purpose. If the user asked for "a report and a summary", make two files rather than a 20-page hybrid.
- Cyrillic, Chinese and emoji are fine in `.docx`; if the PDF comes out with squares instead of letters, the converter lacked the font - say so and hand over the `.docx`, do not fight it.
- When the machine has neither Microsoft Office nor LibreOffice, `office_convert` fails: deliver the `.docx` and say the PDF needs the suite.
