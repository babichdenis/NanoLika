<!-- Сгенерировано scripts/capabilities_md.py — не править руками. -->
<!-- Читать вместе с AGENTS.md (правила) и docs/store.md (склад). -->

# Что умеет NanoLika: инвентарь сборки

Сгенерировано из живого реестра инструментов - править руками бессмысленно, файл
перезаписывается. Задача: чтобы ни один агент не строил заново то, что уже есть.

**Есть:** mail, calendar, contacts, hands, store.
**Выключено:** browser, phone.

| чего нет | где включить |
|---|---|
| browser | Settings → Browser |
| phone | в настройках приложения |

## Mail - почта

- `read_emails`: Read the user's mailbox over IMAP.
- `send_email` (needs the user's yes): Send an email from the user's account over SMTP.

## Calendar and contacts - календарь и контакты

- `calendar`: The user's calendar (read from their calendar feeds).
- `contacts`: The user's address book.

## Files and documents - файлы и документы

- `files`: Work with files in the workspace.
- `office_convert`: Turn a Word, Excel or PowerPoint file into another format with the office suite that is on this machine: Microsoft Office over COM on Windows, LibreOffice elsewhere.
- `office_vba`: Put a VBA macro into a Word, Excel or PowerPoint file.

## Web - интернет

- `web_search`: Search the web. Returns titles, URLs and snippets. Use `web_fetch` to read a result in full.
- `web_fetch`: Fetch a public web page or API (HTTP GET only) and return its readable content as markdown/text (HTML is converted, JSON is pretty-printed).

## This computer - этот компьютер

- `shell` (needs the user's yes): Run a shell command in the workspace directory (bash).
- `python_execute`: Execute a Python script in a subprocess (cwd = workspace) and return stdout/stderr.
- `computer_screen`: Look at this computer's screen: a screenshot, which window is in front and the picture's size as W×H.
- `computer_act`: One action on this computer's screen, then a fresh look.
- `computer_task`: Hand a job on this computer's screen to the operator: it opens the application, looks at the screen, clicks, types and scrolls step by step until the job is done, then reports what it found or did.

## Memory, goals, time - память, цели, время

- `remember`: Save a durable fact about the user or their preferences for future sessions (e.g.
- `recall`: Search long-term memory for facts about the user relevant to `query`.
- `forget`: Delete memories. Provide `memory_id` (e.g. m_1a2b3c4d) to delete one, or `query` to delete every memory containing that text. Use when the user asks you to forget something.
- `goals`: Manage long-term goals and their step plans.
- `reminders`: Schedule something for later.
- `triggers`: Do something whenever an event happens.

## Skills, store, bots - навыки, склад, боты

- `skills`: Skills are written-down ways of doing a job (a weekly review, a trip plan, an inbox triage …); the system prompt lists the ones available.
- `store`: The NanoLika store: the capabilities that are NOT in this build.
- `bot_projects` (needs the user's yes): Create local Telegram/MAX bot projects, connect them through the paid nanoLika gateway, inspect status, pause/resume.

## Everything else - остальное

- `ask_user`: Ask the user a clarifying question and wait for the answer.
- `media`: Sound through the account's NanoLika Cloud key.
- `office_xlsm`: Make an Excel workbook with a working macro and a button, without Microsoft Office: data goes to a sheet, the macro reads commands from the hidden _cfg sheet (format, filter, sort, pdf, refresh, stamp, clear) and draws a button on open.
- `picture`: Draw one picture through the account's NanoLika Cloud key and save it in the workspace, so the chat shows it.
- `terminate`: Finish the current task.

## Правила

- Экран и ввод уже разрешены: на macOS их держит помощник приложения (*NanoLika Computer Use*), а инструменты `computer_*` ходят через него. Не проси человека выдать Screen Recording или Accessibility и не отправляй его в System Settings; чёрный кадр или сбой шага — это неисправность, о ней и говори.
- Сначала посмотри в этот список, потом в `store` (`action=list`), и только потом что-то строй.
- `pip install` в замороженном рантайме не работает: код добавляется только со склада.
- MCP-сервер - это для того, чего нет ни в сборке, ни на складе, а не вместо встроенного коннектора.
