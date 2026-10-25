# Office documents without a 157 MB LibreOffice

## Что было

`@deepseek-ai/dsh` тянет `@deepseek-ai/libreoffice-kit-<platform>` — собственный LibreOffice,
**157 МБ на платформу**. Он нужен только двум вещам: `dsh-office-to-pdf` (конвертация
документа в PDF) и офисным навыкам для legacy-форматов и превью. Установщик от этого
раздувается: 275 МБ (Windows) / 474 МБ (dmg на macOS).

## Что решено

| платформа | что делаем | почему |
|---|---|---|
| **Windows** | LibreOffice **не везём** | на машине стоит Microsoft Office (Word/Excel/PowerPoint, Click-to-Run 16): PDF делает он сам, и только он умеет записывать VBA |
| **macOS** | пока везём (можно выключить) | LibreOffice есть не у всех; при `NANOLIKA_BUNDLE_LIBREOFFICE=0` он тоже не попадает в сборку, а конвертация идёт через Microsoft Word/Excel (AppleScript) или `/Applications/LibreOffice.app` |
| **Linux** | по умолчанию не везём | `NANOLIKA_BUNDLE_LIBREOFFICE=1` вернёт |

Выбор делает `harness/desktop/scripts/prepare-dsh.mjs` при сборке: он удаляет каталоги
`node_modules/@deepseek-ai/libreoffice-kit-*` до записи `dsh.json`.

## Что остаётся работать без LibreOffice

Создание и правка документов — это OOXML (zip + XML), для них есть:

- `office-docx` — `python-docx`
- `office-xlsx` — `openpyxl`
- `office-pptx` — `python-pptx`

Эти три библиотеки добавлены в рантайм (`pip install -e ".[hands,office]"`, extra `office`
в `pyproject.toml`) и собираются в PyInstaller-спеке вместе с шаблонами
(`docx/templates/default.docx`, `pptx/templates/default.pptx`). Вес — ~5 МБ вместо 157 МБ.

## Чего без конвертера не сделать

- **PDF** из документа: нужен Word/Excel (Windows COM, macOS AppleScript) или системный
  LibreOffice (`soffice --headless --convert-to pdf`).
- **legacy `.doc`/`.xls`**: только через Office/LibreOffice.
- **Точные превью и пересчёт формул при конвертации**: то же.

## VBA

Записывать макросы можно только при установленном Microsoft Office:

1. **Windows, COM**: `Excel.Application` → `ThisWorkbook.VBProject.VBComponents.Add` +
   `CodeModule.AddFromString`. Один раз нужно включить «Trust access to the VBA project
   object model» (Файл → Параметры → Центр управления безопасностью → Параметры макросов).
   Драйвер — `cscript` + VBScript (работает всегда) или `pywin32` (если стоит Python).
2. **Без Office**: собрать `.xlsm` из шаблона с `xl/vbaProject.bin` (кросс-платформенно,
   но макрос не подписан) либо отдать `.bas` и инструкцию импорта.

## Как проверить сборку

```bash
node harness/desktop/scripts/prepare-dsh.mjs --out dsh   # в выводе: dropped @deepseek-ai/libreoffice-kit-…
cat dsh/dsh.json                                          # megabytes должны упасть примерно на 157
```
