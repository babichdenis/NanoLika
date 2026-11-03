---
name: office-pptx
description: Build a short presentation (.pptx) from an outline: one idea per slide, readable type, the numbers that matter, speaker notes, then a PDF to send. Use when the user asks for slides, a deck, a pitch or a workshop presentation.
channel: mixed
metadata:
  author: nanoLika
  version: "1"
---

# Slides

`python-pptx` is in the runtime; `python_execute` runs it in the workspace. Never `pip install`.

## Shape of the job

1. In one `ask_user`: the audience, how long the talk is, and what the audience must do or decide afterwards. Ten slides for ten minutes; fewer is better.
2. Write the outline as a list of slide titles, each with one sentence of what it says. Send the outline only when the user asked to see it first; otherwise build it and let them edit.
3. Build, then `office_convert` to PDF for sending by mail.

## The deck

- `Presentation()`, `prs.slide_width = Inches(13.333)`, `prs.slide_height = Inches(7.5)` — 16:9, the shape every projector expects.
- Blank layout (`prs.slide_layouts[6]`) and place text yourself, or `layout 1` (Title and Content) and let the theme do it. One idea per slide: a title that states the point ("Spend is up 18% on postage"), not a label ("Postage").
- Type: title 32–40 pt, body 18–22 pt. If it does not fit, cut words rather than shrinking below 18 pt.
- Numbers on a slide, the words for them in the notes. One table or chart per slide at most: `chart_data = ChartData()`, `add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, …)`.
- Pictures from the workspace: `slide.shapes.add_picture(path, Inches(x), Inches(y), width=Inches(6))`; never stretch a picture out of proportion.
- Notes: `slide.notes_slide.notes_text_frame.text = "…"` — what to say, not what is written.
- A closing slide that asks for the decision, with the next step and who does it.

## Rules

- No lorem ipsum, no clip art, no rainbow colours: one accent colour plus greys, taken from the user's brand when known (`recall`).
- A deck that is really a document (long paragraphs to read) becomes a `.docx` instead — say so and offer it.
- Check the result: reopen with `Presentation(path)` and print the slide count and the title of each slide; a slide whose title is empty is a slide nobody can navigate.
- Cyrillic and Chinese render when the font has them: use the theme font for the deck (`Arial`, `Calibri`, `Noto Sans`) rather than a decorative one.
