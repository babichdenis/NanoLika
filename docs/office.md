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

## Мост в рантайме: `office_convert` и `office_vba`

Два инструмента в `nanomuse/tools/office.py` закрывают то, что раньше делал LibreOffice из
dsh. Оба работают через то, что есть на машине:

| | Windows | macOS / Linux |
|---|---|---|
| `office_convert` | Microsoft Office по COM (`cscript` + VBScript, Word/Excel/PowerPoint) | `soffice --headless` (системный LibreOffice, `SOFFICE_PATH`) |
| `office_vba` | Microsoft Office по COM: `VBProject.VBComponents` | только `.bas` для импорта руками |

Что проверено на DESKTOP-56NPC9C (Windows 10, Office 16.0.14334) в живой сессии
пользователя, а не в служебной:

- Word → PDF: **работает**, PDF 1.7, кириллица целая (62 КБ из docx с заголовком и абзацем).
- xlsx → .xlsm с макросом (модуль с кириллическим именем и кириллицей в коде):
  **работает**, в контейнере `xl/vbaProject.bin`, `macroEnabled` в `[Content_Types].xml`.
- xlsx → PDF: **нужен любой драйвер принтера** («Ни один принтер не установлен»); Excel
  экспортирует PDF через принтер, Word — нет. На этой машине принтеров нет, поэтому
  `office_convert` для таблиц здесь честно падает с этим текстом и подсказкой.

Две вещи, на которых всё спотыкается:

1. **Скрипт для `cscript` — только UTF-16.** UTF-8 с BOM даёт ошибку компиляции на первой
   строке, ANSI калечит кириллические пути (`C:\Users\nadts\Отчёт.xlsx`).
2. **`AccessVBOM`.** Без него `doc.VBProject` отвечает «Программный доступ к проекту Visual
   Basic запрещён». Включается один раз на пользователя:

   ```bat
   reg add HKCU\Software\Microsoft\Office\16.0\Excel\Security /v AccessVBOM /t REG_DWORD /d 1 /f
   reg add HKCU\Software\Microsoft\Office\16.0\Word\Security  /v AccessVBOM /t REG_DWORD /d 1 /f
   ```

   Это и есть та галочка «Trust access to the VBA project object model», только без похода
   в Центр управления безопасностью.

Ещё одно наблюдение: Office — одноэкземплярное приложение. Убитый `taskkill`-ом WINWORD
остаётся «висеть» для следующего `CreateObject`, и тогда `Documents.Open` молча ждёт
диалог восстановления. Если автоматизация зависла — сначала `taskkill /f /im WINWORD.EXE` и
`reg delete HKCU\Software\Microsoft\Office\16.0\Word\Resiliency /f`.

Смоук-тесты: `tests/test_office.py` (пути, `.bas`, определение `vbaProject.bin`, настоящая
конвертация через LibreOffice, если он есть).
