---
name: scan-to-text
description: Read text from a photo, a screenshot or a scanned PDF when there is no text layer. Use when the user sends a picture of a document, a receipt, a contract page or a scan and asks what is in it, or when a PDF comes back with "no text layer".
metadata:
  author: NanoLika
  version: "1"
  store: ocr-vision
---

# Text out of a picture

The files tool reads PDFs by their text layer; a scan has none. The screen's own OCR is not
in the installer — it comes from the store, once per machine.

## 1. Is it there?

```python
import sys; sys.path.insert(0, "<data_dir>/addons/ocr-vision")
import ocr_vision
ok, why = ocr_vision.available()
```

If that fails, install the capsule first: `store` action=addon name=ocr-vision (3 MB, macOS
Vision, no models, no pip). Then run the code again in the same session — no restart.

## 2. A picture

```python
import sys; sys.path.insert(0, "<data_dir>/addons/ocr-vision")
import ocr_vision
print(ocr_vision.text("/path/to/photo.jpg"))          # lines, top to bottom
for word in ocr_vision.words("/path/to/photo.jpg"):    # with boxes, in pixels
    print(word.text, word.confidence, word.box)
```

Python is easier here than the shell: `ocr_vision.text` gives you the whole page as lines,
and `words` gives boxes when the position matters (a table, a form, a stamp).

## 3. A scanned PDF

Vision reads pictures, not PDF pages, and this capsule does not rasterise. So:

- one page: ask the user to send it as a picture, or take a screenshot of it on the screen
  (`computer_screen`) and OCR that file;
- a whole document: the `pdf-tools` capsule (PyMuPDF) will rasterise pages — check the store
  with `store` action=list before promising it.

## 4. Say what you did

OCR is a guess: quote the text you got, and when a word looks wrong next to the numbers
(sums, ИНН, dates) say so instead of silently "fixing" it.
