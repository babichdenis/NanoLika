## 2026-10-29 · Redact vault secrets before model context and delivery

Closed audit item I9 in runtime code. Recalled memories and older session messages/tool
arguments are redacted before a provider sees them. Provider deltas are buffered by the
longest vault value so a secret split across chunks is never streamed; assistant text and
reasoning are redacted before session history, timeline and audit storage. Channel egress
also applies the vault redactor as a final guard, including edited live replies and messages
delivered from sync. Existing memory records remain intact; assistant copies saved to thread
history are masked so they cannot leak on a later turn.

Validation: 92 tests passed across Sentinel, agent, vault and channel manager, including
split-delta masking and outbound channel delivery. Ruff check, formatting and clean-cache
mypy for the three changed runtime modules passed. A first mypy run with the existing cache
hit an internal error. No deployment was made.

## 2026-10-29 · Isolation boundary mapped before I1/I2 changes

Revised the isolation audit's initial per-thread proposal to preserve the owner's requirement:
client preferences, effective work patterns and pointers to known files are long-lived client
knowledge shared by that client's sessions; each thread's transcript, running task and uploads
remain session-owned. This was a source review only; no behavior changed.

Current evidence: `Thread` already owns its timeline and agent session. `ConversationSync` maps
synced threads to a Cloud account through `owner_of`, but local ownerless threads have no such
identity. The messenger's `access_scope` is per private chat and currently only scopes access
settings. Public bot sign-in creates/finds an account by `(channel, sender_id)` and returns its
account ID, but the channel manager currently keeps only the API key and model. `MuseService`
still builds one workspace/tool set and one `MemoryStore`; messenger uploads and `/api/files/upload`
are saved without a thread ID. Therefore the next change must propagate an explicit customer
identity from ingress through the `Thread` and tool context, keep topic IDs as session IDs, and
keep group chats from borrowing a private sender's memory. Do not migrate unknown old files or
memories to a customer account without a trusted owner mapping.

Audit updates are in `~/Downloads/NanoLika-isolation-razbor.md`. No code or production state was
changed by this mapping.

## 2026-10-29 · Hub replay protection survives relay restarts

Closed audit item I14 at the relay boundary. Cloud now persists used call IDs and nonces per
account in SQLite, rejects reused values for 24 hours, and rejects new-format calls whose
Unix timestamp is over five minutes out of sync. Python, desktop, Android and iOS callers
send a timestamp and fresh nonce; older clients remain supported with call-ID deduplication.
Receipts cascade when an account is deleted. Schema creation and migration run in one DB
transaction. This prevents accidental or captured-frame replay from dispatching the same
device action again after a restart.

Validation: cloud Hub suite 17/17, runtime Hub suite 27/27, desktop Hub suite 12/12, desktop
TypeScript check, package build, Python Ruff check/format, and iOS Swift syntax parse passed.
Commit `df9f1c03` records the change. The full desktop suite could not be evaluated under installed Node 20 (the package requires
Node 22+ and unrelated bundled tests import `zstdDecompressSync`); Android unit tests could not
configure because no Android SDK is installed. No production relay was deployed. The isolation
audit in Downloads records the status. Other conversation-sync changes remain outside this commit.

## 2026-10-29 · Sync stage 2: lease, presence per device, delivery claim, 304 catalog, incremental hydrate

Closed the second sync stage from `NanoLika-sync-razbor.md`: S1, S2, S12, S8, S10 and S13.
The relay now keeps one turn lease per conversation (`sync_leases`, take/renew/release) and
one delivery claim per synced message and channel chat (`sync_deliveries`); presence is keyed
by `(account, cid, device)` so two devices no longer overwrite each other. The lease is
non-blocking for old clients: only `take: true` gets `409 conversation_busy`, everyone else
gets the holder and goes on. The runtime takes the lease in `_worker`, renews every 40 s,
waits up to 90 s for another device and then asks for the chat outright; channels pass the
same worker, so they do not answer in parallel either. A claim older than 60 s is taken over.

The web catalogue now carries an ETag and answers `304`; the web tab keeps a permanent
`/v1/hub` socket and refreshes once per burst of `sync` frames instead of polling every three
seconds, with a 30 s fallback only while the socket is down. `hydrate_thread` reads a chat
whole once per process and then only rows after `message_cursors[cid]`, through a canonical
in-memory transcript. Renames are stamped with the relay's own time, so a lagging clock no
longer loses a title, and re-sending the same title does not move `seq`.

Committed `ec8acbf2` (relay, runtime, dsh, web, tests), `f5055ee5` (web socket/version, docs)
and `5ea6bd27` (rename tests). Checks: cloud `test_sync.py` + `test_chat_catalog.py` 39 passed;
full cloud 645 passed, 3 owner on-hold (hub 2, pricing 1); runtime full 1076 passed, 6 skipped,
73 subtests; web chat-cloud 11/11; dsh sync 31/31; Ruff/format and diff-check clean.

Live relay: narrow patches over the existing overrides, backup
`/opt/nanomuse/cloud/backups/sync-stage2-20261029T121500Z`; `cloud-relay-1` restarted, health
200, both new tables present. Web serves `app.js?v=20261029-syncsp`, the hub-backed
`chat-sync.js` and SW cache `...20261029e`. Rebuilt the runtime from the committed worktree
(PyInstaller, native hands kept) and installed it after an idle gate; backup
`~/.nanolika/backups/sync-stage2-20261029T123500Z`, ad-hoc signature verified, embedded
`sync.engine`/`service`/`channels.manager` contain the new lease/claim/hydrate code. Rebuilt
and installed the dsh plugin in both desktop copies (backup
`~/.nanolika/backups/sync-stage2-plugin-20261029T123800Z`); app restarted and dsh ready.

S9 followed the owner's choice of option A (`c85e9b6c`): the first chat born in a browser
goes up as `kind: main`, later ones stay side; when the account already has a main the web
reads `main_exists.cid_main`, adopts it and re-sends the batch without overwriting the
existing title. Old side chats are not promoted retroactively. Live web serves
`app.js?v=20261029-main` and SW cache `...20261029f`; 13/13 web chat tests pass.

Not touched: `sync_enabled`, media discounts, hub behaviour on an expired tariff. Open: S14
(same text from two devices) needs every client to rewrite its rejected `mid` to `mid_of`
before a `duplicate` rejection can be enabled. Trap: another session was editing
hub/isolation files in the same worktree; only the sync files were staged, and the runtime
was built from a detached worktree at HEAD so those unfinished changes were not packaged.

## 2026-10-29 · Local files in an answer become S3 links (reg.ru)

A Telegram task from the desktop agent returned local paths (`/Volumes/Dev/nanoLika/...`);
the bot and the cloud web cannot read them. Added `share.ts` to the dsh plugin: before a task
result goes back over the hub, every Markdown link to an existing absolute file is uploaded to
the private `nanolika` bucket and replaced with a presigned GET URL valid for 24 hours. Uploads
stream to S3; the bucket lifecycle deletes `files/` objects after 30 days and aborts unfinished
multipart uploads after one day.

Config is `~/.nanolika/desktop/nanolika/s3.json` (endpoint, region, bucket, no secrets); the
access key and secret are read from the macOS Keychain (`nanolika-s3-access`,
`nanolika-s3-secret`). Endpoint `https://s3.regru.cloud`, path-style, `us-east-1`; the bucket is
private (anonymous path-style is 403). Verified PUT/GET/DELETE against the real bucket, a real
presigned download, and the lifecycle rule. Tests: plugin `share`/`task`/`sync` 44/44.

Committed `370885f5`. Rebuilt the plugin and installed it in both desktop copies (backup
`~/.nanolika/backups/s3-share-20261029T144000Z`), app restarted and ready. Trap: keys were sent
in chat; rotate the pair before public use. Free egress means large clips go as links, not
through the bot or hub.

## 2026-10-29 · Admin: provider wallets and the price table on one page

The admin console's Providers tab redirected to `/app/prices/`, which the public domain answers
404 by design, so the operator could not see either the provider wallets or the comparison
table. Added `GET /v1/admin/provider-balances` (the routing status without the heavy offers)
and a balances panel above the price table on the Providers tab; the tab now renders the
comparison table itself instead of redirecting. The panel shows each provider's available
amount and currency, the ruble estimate, when it was checked, and whether it is below the 90 ₽
route floor; `/app/prices/` stays private.

Live: narrow patches to the existing admin.js/index.html and api.py overrides, backup
`/opt/nanomuse/cloud/backups/admin-balances-20261029T143000Z`; relay restarted, health 200,
admin 200, `admin.js?v=20261029-balances`. The token-protected endpoint returned the live
wallets: Polza 74.72 ₽ (below the floor after one paid run), AITunnel 306.78 ₽, RouterAI
440.41 ₽, ProxyAPI 109.13 ₽, DeepSeek $2.64, OpenRouter $8.62. Committed `c56d674d`.

## 2026-10-29 · Bot prompt mismatch: KeyError 'capabilities' after reboot

A Telegram message sent while the Mac was rebooting failed before the model call:
`prompts.SYSTEM_PROMPT.format()` raised `KeyError: 'capabilities'`. The bot image carried an
older `nanomuse/agent/core.py` that did not pass the placeholder, while the newer
`overrides/prompts.py` had `{capabilities}` in the system prompt. The desktop later pulled the
same user message from sync, but messages from another device are shown, not answered, so the
chat had no reply on either side.

Fixed with a narrow bot-only override: `overrides/agent/core.py` now has
`capabilities_section()` and passes `capabilities=self.capabilities_section()`, and
`overrides/agent/capabilities.py` is mounted next to it. Both containers (app and
site-packages paths) get the mounts; compose was validated before `docker compose up -d`.
Backup `/opt/nanolika-bot/backups/sync-fix-20261029T132500Z`. Bot recreated and healthy, no
traceback on start. The message that failed is not retried automatically: send it again.

Trap: the bot mixes an image from one build with file overrides from later builds. A new
prompts.py without the matching core.py compiles and starts fine, then fails on the first
turn; deployment checks must exercise one real turn or at least call
`build_system_prompt`, not only `/healthz`.

The next Telegram message surfaced a second skew: the bot's chat preference still held
`mode: full-access` from before `channels.shared_thread` was turned on. For a shared chat the
manager tried to push that mode to the desktop before every turn; the DSH preset is named
`danger-full-access`, the call failed, and the turn ended with the misleading "installed
version cannot change access" reply. Fixed in `920562c1`: a shared chat ignores a stored
per-messenger mode (the desktop app's setting governs it), and the DSH plugin maps
`full-access` to `danger-full-access` when the host does not know the former. Bot manager
override deployed and the container restarted; plugin rebuilt and installed in both desktop
copies (backup `~/.nanolika/backups/sync-preset-20261029T140500Z`), app restarted. The stale
preference is harmless now; the bot no longer sends `channel_permissions` for a shared chat.

## 2026-10-29 · Isolation audit: preserve client experience, separate session state

The owner asked to close the findings in `~/Downloads/NanoLika-isolation-razbor.md`. The
design constraint is to keep accumulating each customer's preferences, useful file locations
and effective work patterns for that customer's sessions, while keeping every session's
conversation and active task separate. A blanket per-thread memory split would break that
behavior; derive customer scope before changing memory or workspace boundaries.

Committed `99af666c`: full access now skips routine confirmations but preserves Sentinel
warnings and tainted-context asks; the system prompt inserts its capabilities block once.
Touched-file Ruff checks and formatting passed. Functional tests were not run. The audit in
Downloads records these two items as done and the remaining items as open. Existing unrelated
conversation-sync changes in the working tree were left untouched.

Next: map the isolation boundary to customer identity and session task state, then implement
the highest-risk data boundary without discarding shared customer knowledge. Recheck Hub and
messenger findings against the current protocol before changing their documented behavior.
Trap: the audit uses an older merge-base; some claims are already explicitly limited to the
desktop runtime or are intentional Hub behavior.

## 2026-10-29 · Cloud hygiene и стабильное завершение уведомлений

## 2026-10-29: Verify and install the completed audit fixes

The final runtime source at 071a8cad passed the complete non-live suite: 1071 tests and 73 subtests, with five environment skips and one live test deselected (413.24 seconds). Mypy passed for 162 source files and runtime Ruff check/format passed. The web passed lint, types, 129 tests and its production build; the isolated committed-source desktop plugin passed build, types and 425 tests. Canonical avatar resources passed four tests and 40 resource subtests.

Built the final runtime on the installed Mac with native hands, browser and Office dependencies. The packaged health/SPA/workspace and actual MCP initialize/tools-list checks passed with empty test data and no provider calls. Required sync modules are in PYZ; the embedded code of all eight follow-up modules matches the verified source. Installed the plugin and final runtime only after an idle gate, backed up the whole application, preserved signing metadata and verified the ad-hoc signature. Final application backup: `~/.nanolika/backups/audit-runtime-final-20261029T064518Z`. After launch the MCP process is running, work is idle, sync is enabled/unpaused with no error and the previous cursor intact. Personal history, credentials and studio avatars were not replaced.

On the bot, deploy only four narrow patches to existing overrides: MAX readiness/configured-type checks and explicit app-control presence scope. Preserve unrelated live routing, media and synchronization code. Backup: `/opt/nanolika-bot/backups/audit-bot-guards-20261029T064144Z`. Before restart the bot was idle with no working turn or pending approval. Afterwards health returned 200 and installed-module probes confirmed a 503 before MAX acknowledgment/dedup when its bus is absent, plus the presence scope and prompt. Cloud/chat health and the browser client returned 200. Web static files retain old hashed chunks for already open pages.

The audit distinguishes completed source, installed files and remaining work. Owner-paused pricing, sync defaults and expired-plan decisions remain untouched; three cloud tests reflect those decisions. The broader CSP rollout still requires compatibility review for generated srcdoc panels. Refactoring and retention recommendations are not claimed as completed. Traps: a session cookie can expire during a full application backup, so each local check regenerates it; an incomplete code-only snapshot needs the tracked phone/hook fixtures before running the desktop suite.


## 2026-10-29: Validate optional channel payloads and MAX readiness

The full type check reported 30 errors across eight runtime files. Narrow repeated JSON mapping lookups once, preserve optional text during history hydration, use the schema role and an empty shared-id sentinel, and keep optional store inventory duck-typed. MAX now rejects webhooks with the existing 503 response until its bus is ready, before acknowledgment or deduplication; direct updates without a bus return safely. A wrong channel registry entry is also treated as unconfigured.

Validation: mypy passed for 162 source files, global runtime Ruff check passed, touched-file formatting passed; 110 channel/agent/project/profile tests, three sync hydration/history tests and 18 MAX tests passed. New regressions cover absent bus and retry, malformed media/approval payloads and an invalid channel registry entry. No type suppressions, pricing or sync-policy changes. This follow-up is separate from the already verified and installed eb350942 runtime; a refreshed package and the narrow server MAX guard require their own deployment record.


## 2026-10-29: Wait for the side-chat transition's own sync pull

The full runtime run completed with 1066 passing tests, 5 platform skips and one failure: the side-chat toggle test assumed the next recorded request must be its bootstrap. A main-only background poll could already be in flight before the settings change. Wait for the exact all-scope pull from zero with a 300-message tail, then assert that bootstrap occurs once; retain history, cursor, push and switch-off assertions. No runtime sync logic, defaults or owner-paused policies changed.

Validation: all 22 sync tests passed after the test fix; touched-file Ruff/format passed. The earlier full run remains recorded as one failure, rather than being relabelled as a fresh full green run. Desktop runtime and plugin installation uses an idle gate and a full application backup; active work is never interrupted to make the installation pass.


## 2026-10-29: Finish runtime formatting checks

The audit still had eleven runtime/test files failing Ruff's formatting gate after the functional fixes. Format those files only and verify identical Python ASTs before and after. Full runtime Ruff check and format check now pass across 280 files. No behavior, pricing or sync defaults changed. The ongoing full runtime test and separately built desktop snapshot contain the same behavior, with formatting as the sole source difference.


## 2026-10-29: Keep bundled avatar copies aligned

The full runtime audit found five canonical WebP resources stale in the web and desktop packages. Add an avatar-only mode to the existing brand generator, skip the absent retired demo directory, copy the canonical artwork and regenerate content revisions. Rebuild the committed web app so its packaged copies match too. The internal dragon identifier and personal studio avatars stay intact.

Validation: all four brand-asset tests and 40 resource subtests passed; web lint, types, 129 tests and production build passed. A separate committed-source desktop snapshot is being verified before installation. No account data or custom avatars were copied into verification snapshots. Trap: the full brand generator also renders unrelated icons and references a retired demo, so a narrow resource-copy mode is needed for this task.


## 2026-10-29: Refresh device capabilities and distinguish app presence

The audit found stale GUI capabilities after tool changes and device presence being read as messenger availability. Recompute the small capability inventory on access; describe device presence as app control only, with server history and Telegram/MAX delivery independent of an offline computer. Add a live tool/settings regression and update the hub contract in both languages. Packaging tests now emulate DMG conversion and stub Finder/sync instead of invoking the host UI. Regenerated CAPABILITIES.md for the earlier office copy punctuation fix.

Validation: 45 packaging, phone, capabilities and presence tests passed; touched-file Ruff and formatting passed. Production runtime installation is pending the idle gate; no live account, device or conversation was changed by these tests. The packaging failure was a stale fake, while the capability cache was a product defect.


Подагент устранил 22 Ruff-ошибки и отформатировал разрешённые исходники cloud: 42 файла. Форматирование отдельно проверено через AST без docstrings; разрешённые lint-дельты разобраны явно: порядок импортов, UTC alias того же singleton, unused imports и простой неиспользуемый alias aa_by_name. Неожиданных AST-дельт нет. SQL, тарифы, defaults и подписанные store-артефакты не менялись. Каждый файл сопоставлен по SHA256 с проверенным снимком.

В первом полном прогоне на исходном Mac обнаружилась гонка самого теста controls: он закрывал SQLite после sent.append, пока background worker ещё писал аудит. Исправлено ожидание Event в finally завершения flush_notices. db.py не менялся. Отдельно 14/14 controls-тестов прошли.

Полный исправленный тестовый снимок: 637 passed, 3 failed за 208.71s. Оставшиеся три, hub при истёкшем тарифе и скидка для картинок, явно отложены владельцем и не переписывались. Полный Ruff check cloud прошёл. Формат разрешённых файлов чистый, общий format-check по-прежнему указывает 8 замороженных baseline/store файлов: config.py, db.py, hub.py, service.py, sync.py, test_plans.py и два подписанных store-файла. Конфиг Ruff не ослаблялся.

Проверяемый снимок содержал только tracked code/tests и необходимые tracked fixtures, без .env, БД, пользовательской истории и секретов; authoritative repo остаётся на установленном Mac. Исходный параллельный cloud-прогон завершился segfault на гонке тестового close, поэтому результат снимка и отдельный исправленный controls-прогон указаны явно. Рабочий сервер не перезапускался ради нейтрального форматирования, его уникальные override-правки не перетирались.

## 2026-10-29 · Runtime copy и iOS: ключи совпадают с переводами

Устранены подтверждённые аудитом сбои voice и iOS-локалей. В runtime и встроенной инструкции bot-projects менялась только пунктуация предложений, без изменения условий доступа, согласований, цен и действий. Сканер строк теперь проходит.

Шесть требовавшихся переводов уже имелись во всех девяти локалях, но часть AppLocalized в upstream Swift использовала старый nanoMuse ключ после переименования каталога. Исправлен customer_branding: за пределами нашего NanoMuse каталога переименовываются только строковые аргументы AppLocalized. Произвольные storage/wire строки, классы, пути и dragon не меняются. Генерация обновила 14 upstream Swift-файлов; весь текст вне строковых литералов byte-identical. Каталог переводов не дублировался и не переписывался.

Проверки: 10/10 voice/iOS/rebrand тестов, включая идемпотентность и сохранение wire/storage идентификаторов; Swift parse всех 14 файлов; полный ruff check nanomuse tests scripts. Полный runtime pytest запущен отдельно и ещё не объявляется зелёным. Native бинарники не выпускались, версии не менялись. Это исправления исходников и генератора для следующей штатной сборки, рабочие история/аккаунты и оплаченные запросы не затрагивались.

## 2026-10-29 · Синхронизация: этап 1, ничего не теряется

Закрыт первый этап разбора `NanoLika-sync-razbor.md`, коммит `772f8aab`.

- **S3:** `changes()` больше не отдаёт счётчик всего аккаунта как курсор `scope=main`; курсор — `seq` последней реально выданной строки. В рантайме и dsh отдельные курсоры `main`/`all`; Android/iOS переключают scope полным tail-pull.
- **S4-A:** `GET /v1/sync/catalog` больше не пишет в БД. Склейка «Ленты» перенесена в `push`: перенос сообщений получает новый `seq`, исходный чат — томбстоун, канонический обновляется последним. Добавлен фоновый `fold_all_feeds` раз в 5 минут; точный матч имени сохранён.
- **S5:** retention больше не делает физический `DELETE`; старые сообщения получают `deleted=1`, пустой текст и новый `seq`, порядок по `seq`. `push` возвращает `pruned`.
- **S6:** свои строки после потери `sync.json` применяются по `mid`, а не отбрасываются по `device_id`: runtime, dsh, Kotlin, Swift. Защита `_apply_conversation` от второго пустого чата сохранена.
- **S7:** повторный `mid` приходит в `rejected` как `known_mid`; тихого `accepted=0` больше нет. Полные правки/регенерация сообщений оставлены на отдельный этап.

Проверки: cloud `test_sync.py` + `test_chat_catalog.py` — 36 passed; полный cloud — 641 passed, 3 failed (только отложенные hub 2 и pricing 1); runtime `test_sync.py` — 23 passed, полный runtime — 1072 passed, 5 skipped, 1 deselected, 73 subtests; dsh `sync.test.mjs` — 31 passed, полный harness — 426/426; Ruff/format изменённых файлов; `swiftc -parse` чистый. Android-модуль без JDK/SDK не прогонялся.

Не тронуто: `sync_enabled`, скидки, hub при истёкшем тарифе, формат API. dsh-плагин собран и установлен в обе копии, приложение перезапущено. Runtime-бинарник, Android/iOS и relay пока не пересобирались: live `sync.py` старше репозитория, live `catalog` уже read-only; нужен узкий патч S3/S5/S7, а не замена файлов целиком.

## 2026-10-29 · Модели: пилюля Лика+, избранное у поля и ошибка расчёта

В кабинете текстовая ссылка «поднять тариф» заменена правой колонкой «Тариф» с пилюлей «Лика+» у закрытых моделей; верхняя строка «На старших тарифах доступно ещё N» заканчивается такой же пилюлей. Обе открывают черновик окна тарифа и пополнения. Строка стала на семь колонок, но осталась компактной: пилюля только у закрытых, у остальных пусто. В веб-пикере закрытая модель тоже получает пилюлю «Лика+» в правом столбце рядом с «Подробнее»; в приложении замок и нижняя подсказка тоже подписаны «Лика+».

Выпадающий список модели у поля переписки в вебе теперь показывает избранные чат-модели, а не весь каталог. Если текущая модель не в избранном, она остаётся отдельным текущим пунктом, чтобы выбор не сбрасывался. После переключения звёздочек в полной панели список у поля обновляется через `onFavourites`.

Ошибка «Не удалось рассчитать» больше не сваливает всё в «проверьте параметры»: устаревшая модель говорит «обновите список», истёкший вход просит войти заново, `bad_quote` называет количество токенов. Расчёт DeepSeek V4.1 Flash с текущими 1000/512 и ценами 36,51/146,05 отвечает 200; в логах был один 404 на устаревший/недоступный id, после перезагрузки данные берутся из `/v1/me/models`.

Проверки: composer-демо показал только избранные модели и сохранение текущей; веб-тесты 17/17, `node --check` изменённых файлов. Live: кабинет `models.js?v=20261029-pill`, веб `app.js?v=20261029-fav`, `model-sheet.js?v=20261029-fav`, `composer-controls.js?v=20261029-fav`, SW cache `...20261029c`, health 200. Скидки, `sync_enabled` и hub не менялись.

## 2026-10-29 · Модели: широкая таблица, производители и фильтры со счётчиками

Кабинет `/app/models/` переделан под компактную широкую таблицу: шесть колонок, без «Подробнее» и расчёта под строкой, производитель стоит в строке модели. Убрана кнопка «Обновить цены»: цены читаются при открытии страницы, в процессе просмотра их никто не меняет. «На другом тарифе» заменено ссылкой «поднять тариф»; она открывает черновик окна тарифа и пополнения. Черновик берёт тарифы из `/v1/payments/config`, показывает сумму и ведёт в кабинет, где оплата уже работает; кабинет по `?pay=1&plan=...` сам открывает окно оплаты и выбирает тариф или сумму.

Производители приведены к одному имени: `~Anthropic` и `Anthropic`, `Alibaba` и `Qwen`, `Gigachat` и `Sber` и подобные пары сведены; в фильтре 16 крупных производителей и «Другие» вместо 67 строк. В веб-пикере те же названия: «Все производители», а не «провайдеры». Термин в кабинете заменён на «производитель».

В веб-пикере (`model-sheet.js`) теперь те же фильтры, что в кабинете, с живыми счётчиками: Все модели, ★ Избранное, Выбранные, Кодинг, Переписка, Картинки, Видео, Озвучка, Распознавание и Музыка. Источник — `/v1/me/models`, поэтому счётчики совпадают с кабинетом, а закрытые тарифом модели видны с ценой, disabled-выбором и пилюлей «Лика+». По умолчанию открывается «Все модели»; «Выбранные» берутся из полки аккаунта, «Кодинг» ищется по тем же словам. Кнопка «Обновить цены» убрана и здесь. Service worker cache обновлён, чтобы старый `model-sheet.js` не залипал.

Проверки: 2/2 новых теста фильтров веб-пикера, `node --check` изменённых JS; harness typecheck, локали и build; live `/app/models/` отдаёт `models.js?v=20261029-maker`, веб отдаёт `app.js?v=20261029-filters` и `model-sheet.js?v=20261029-filters`, health 200. Плагин приложения пересобран и установлен, приложение перезапущено. Скидки, `sync_enabled` и hub не менялись.

## 2026-10-29 · Выбор моделей: все модели, замки тарифа и вкладки как в BotHub

Кабинет `/v1/me/models` больше не прячет модели, закрытые тарифом: каждая строка приходит с `plan_locked`, ценой и скидкой. Прямой вызов такой модели по-прежнему получает `404 plan_model`, а приложение через `/v1/models` видит только разрешённое. В таблице кабинета появились фильтр и группы по провайдеру, колонка «Умеет» для картинок, видео и звука, тег «на другом тарифе», сортировка по цене, контексту и имени. Раздел, провайдер и сортировка запоминаются в localStorage. Цена и контекст сортируются по всему разделу, а не внутри группы провайдера.

В приложении пикер стал ближе к BotHub: вкладки видов, «Избранное» отдельной вкладкой вместо скрытого по умолчанию фильтра, запоминание последней вкладки, провайдер и скидка в строке, закрытые тарифом модели с ценой и замком, ссылка в тарифы. Добавлен общий список «Все модели» над перепиской, картинками и клипами: выбранная модель сама встаёт в свой раздел, текущие модели отмечены. Карточек нет, только компактные строки. В кабинете также поправлена вёрстка: звёздочка встала в правый верхний угол, метаданные занимают всю ширину строки.

Проверки: harness 425/425, typecheck и build зелёные; профильные cloud 19/19, `test_plans` 16/17, единственный красный — отложенное правило hub. Веб выложен точечно в `/opt/nanomuse/cloud/overrides/cabinet-compact-20261027`, live отдаёт `models.js?v=20261029-models`; сборка плагина установлена в payload и внешнюю копию, приложение перезапущено. Скидки, `sync_enabled` и поведение hub при истёкшем тарифе не менялись.

## 2026-10-29 · Аудит A5: старый forget не отключает активное устройство

Уточнено замечание аудита: существующий Hub.forget явно отказывает 409 device_online живой записи, а не сообщает об успешном отключении. Старый DELETE /v1/devices удаляет только офлайн-запись и не отзывает ключ; кабинет использует отдельный DELETE /v1/link/devices, который отзывает собственный вход компьютера и закрывает сокет. Унификация этих операций сломала бы старых клиентов и скрыла разницу, поэтому продуктовый код не менялся.

Добавлены три контрактных проверки: живое устройство остаётся подключённым после явного 409, офлайн-forget не отзывает вход, чужой аккаунт не удаляет запись владельца. Вместе с disconnect-набором 10/10, Ruff чистый. Разница документирована на EN/ZH. В работающем relay независимо проверен код обеих операций: online refusal и UPDATE api_keys.revoked_at + drop_signed_out присутствуют. Реальные устройства не отключались, повторная выкладка не требуется. Отложенные правила тарифа hub не менялись.

## 2026-10-29 · Runtime Ruff: набор символов и импорты

Устранены две оставшиеся Ruff-ошибки runtime: из набора lstrip в split_quiet убран повторный дефис, в test_chat_scopes отсортированы импорты. Набор удаляемых символов идентичен, поведение quiet-маркера проверено на пробелах, запятой, двоеточии, нескольких дефисах и переводах строк. 3/3 chat-scopes тестов и полный ruff check nanomuse tests scripts прошли. Это эквивалентная запись выражения, повторная выкладка не требуется. Полный runtime pytest выполняется отдельно для актуального списка оставшихся сбоев.

## 2026-10-29 · A3 выложен, ожидание кеша админки обновлено

Коммит 9cb5bbea выложен точечными изменениями, уникальный live API/OAuth сохранён. Дождались нулевого in_flight, сделан online backup базы и backup кода: /opt/nanomuse/cloud/backups/audit-a3-20261028T230113Z и /opt/nanomuse/cloud/data/backups/audit-a3-20261028T230113Z.sqlite3. В контейнере реально импортируется новый store, healthz и site-session отвечают 200. На отдельной in-memory БД проверены opaque/hash-only хранение, невозможность Bearer-доступа cookie, миграция старой cookie, logout и отзыв исходного ключа. Рабочие аккаунты, баланс, SMTP и модели не затрагивались. Независимый review блокеров не нашёл, ещё десять проверок смены аккаунта, Origin, logout и revoke_session прошли.

Полный cloud-прогон с A3: 633 passed, 4 failed. Три относятся к явно отложенным владельцем скидкам/hub. Четвёртый, console cache, устарел после отдельного CSP-коммита: админка теперь имеет более строгий no-store. Тест уточнён только для /app/admin, обычные ресурсы по-прежнему требуют no-cache и ETag. 26/26 console/header/browser/opaque тестов и Ruff прошли. Повторная выкладка для изменения ожидания теста не нужна.

## 2026-10-28 · Аудит A3: отдельная браузерная сессия

Вместо API-ключа HttpOnly cookie содержит случайный opaque token. В SQLite отдельная таблица хранит только SHA256 токена, родительский key_hash и фиксированный срок; db.py и sync_enabled не менялись. Проверка родительского ключа сохраняет отзыв, срок действия, блокировку и удаление аккаунта; ON DELETE CASCADE удаляет связанные сессии. Cookie не подходит как Bearer для API и не продлевается запросом статуса. Срок не более 30 дней и не дольше родительского ключа.

На одной базе один раз фиксируется начало 30-дневной миграции: старый ключ, созданный раньше, обменивается на opaque cookie при проверке статуса. Новая cookie не живёт дольше остатка окна, рестарт окно не продлевает. После окна существующий localStorage Bearer может явно установить cookie через прежний authenticated POST. OAuth logout отзывает cookie без отзыва ключа; site-session logout сохраняет отзыв ключа и закрытие hub-сокетов. Сохранена серверная доработка удаления старого host-only nm_signed. EN и ZH контракт обновлены без правок отложенного sync.

Подагент: 80/80 профильных auth/account/browser/OAuth/Telegram/user-key/hub-signout проверок, 12/12 новых opaque tests, Ruff затронутых файлов и формат своих модулей чистые. Проверки не отправляли сообщения или платные генерации. Выкладка ещё не объявлена завершённой, её результат будет отдельной записью после проверки live.

Ловушка общего дерева: параллельный коммит CSP 3c0c7cc5 случайно включил A3-вызовы из api.py до коммита зависимого модуля. Сохраняем CSP, добавляем все зависимости и завершаем A3 одной последовательностью. Подготовленный откат только этих вызовов после завершения A3 неприменим; чужие участки api.py не отменяются.

## 2026-10-28 · Аудит A1: оставшиеся ожидания sync-тестов

В полном cloud-прогоне два sync-теста проверяли прежние строки отказа из _json. Новый общий ограничитель возвращает тот же код too_large и точный max_bytes до вызова endpoint. Проверены уже имевшиеся в общем дереве три незакоммиченные правки ожиданий, добавлена проверка структурного max_bytes=1024, тест переименован по тому, что он теперь проверяет. Проверка ранней остановки потока сохранена, не ослаблена.

37/37 sync + request-limits тестов, Ruff и git diff --check прошли. Код приложения не менялся, повторная выкладка не нужна: работающий relay уже проверен на 413. Общий cloud-прогон запущен отдельно; известные отложенные решения по скидкам и hub не менялись.

## 2026-10-28 · Аудит A10-web: цена и баланс при отказе генерации

Runtime сохраняет разрешённые поля insufficient_funds в структурном funds, прежняя классификация/allowance_detail сохранены. Отдельная веб-карточка показывает точную цену и нулевой/ненулевой баланс, безопасную ссылку на кабинет и явный выбор другой модели. Старый отказ бесплатной квоты остаётся отдельным. Публичный cloud web сохраняет поля как HTTP, так и SSE-отказа; общий форматтер используется обоими веб-клиентами на RU/EN/ZH. При отсутствующих/некорректных суммах цены не выдумываются, опасные ссылки заменяются безопасной. Автоматического повторного платного запроса нет.

Проверено на исходном Mac: 129/129 веб-тестов, lint/typecheck/build; 12/12 runtime failure-тестов; 2/2 HTTP/SSE browser-теста и Ruff. Собранные static включены в коммит. На VPS установлены runtime failures + веб-сборка, в публичном cloud web добавлены только точечные изменения без замены остальных live-доработок. Health обоих доменов 200, funds/app/stream отдаются 200, проверены хеши файла сборки с карточкой и синтетический runtime notice без запуска модели. Backup бота /opt/nanolika-bot/backups/audit-a10-web-20261028T201937Z. Старые хешированные assets на сервере оставлены для уже открытых страниц.

Финальный проверенный harness A10 и тестовые исправления подагента установлены в payload и внешнюю копию desktop после проверки отсутствия работающих задач; backup ~/.nanolika/backups/telegram-sync-20261028T201836Z. Приложение перезапущено, API work отвечает. Полный harness ранее 425/425, build/typecheck успешны. Скидки, sync_enabled и hub не затрагивались.

## 2026-10-28 · Аудит A1: лимит до разбора загрузки

Добавлен ASGI-ограничитель фактического размера тела до FastAPI/Starlette multipart-парсера. Проверяет Content-Length и реальный поток, останавливается сразу после превышения, не запускает endpoint на оборванном запросе. Допустимое тело свыше 1 МиБ переносится во временный файл, воспроизводится порциями до 64 КиБ; файл закрывается и disconnect передаётся дальше. Отказ 413 too_large содержит max_bytes и доступен телеметрии. CORS остаётся внешним слоем.

Проверено в исходном репозитории: 12/12 тестов, включая настоящий create_app и отсутствие вызовов парсера/endpoint, дробный поток и ложный размер, точную границу, spill-to-disk, disconnect и websocket passthrough; Ruff чистый. Не менялись скидки, sync_enabled и hub при истёкшем тарифе.

Выложено точечным изменением live API, который содержит дополнительные серверные доработки видео/тарифов: полная замена исходником запрещена. Backup /opt/nanomuse/cloud/backups/audit-a1-web-20261028T201937Z. Проверка работающего relay: multipart с объявленным размером 16 МиБ + 1 байт получает 413 до передачи тела, healthz 200; модельные вызовы не выполнялись. Первая попытка выкладки автоматически откачена: новый funds.js нельзя монтировать внутри readonly parent web_client без существующей точки. Исправлено добавлением нового файла в host-parent и отдельным bind только для /srv, readonly не ослаблен.

## 2026-10-28 · Аудит harness: финальная сборка прошла

После исправления A10 владельцем desktop-участка в b28d4302 независимо повторены pnpm build и pnpm typecheck, оба завершились с кодом 0. Временный блокер RefusalCard.baseURL из предыдущей записи снят. Результат участка 04ca2e67: 25/25 профильных тестов, полный harness 425/425, сборка и типы зелёные на Node 24. Установленное приложение не изменялось этим агентом. Общий аудит обновлён под lock.

## 2026-10-28 · Аудит harness: локали и модальности модели

По запросу владельца взяты два красных desktop-теста и сверены триггеры CI. Исходный полный набор на Node 24: 422/424. Проверка с названием про defaults на самом деле падала на устаревшем ожидании takesImages: после aa9e1eb6 явные модальности релея авторитетны, имя является fallback только для старого релея. Добавлена отдельная таблица десяти случаев, выбор моделей не менялся. Удалён 41 неиспользуемый ключ во всех трёх локалях после удаления прежних экранов: chCloudDelete, mcChats/mcOthers/mcNoThirdParty, navPhone, moStage, mdLook, прежние mdVideo и mdClips/mdAnimate. Ключ mdVideoOff и новые A10 строки сохранены. Проверка использования ключей не ослаблена.

Проверки после изменения: 25/25 профильных тестов, весь pnpm test 425/425, git diff --check. pnpm build собрал JS, но декларации остановились на параллельной незакоммиченной A10 доработке RefusalCard.tsx: cloud.baseURL отсутствует в текущем типе live state. Файл не включён в этот коммит, проблема передана основному агенту для согласованного исправления и повторной сборки. Установленный desktop и сервер этим изменением не обновлялись, внешние сообщения и модельные вызовы не выполнялись.

Триггеры workflow проверены по исходникам. Корневой ci.yml запускается по push main/v* и pull_request, ручного запуска нет; для полной проверки рабочей ветки нужен PR. Android и iOS имеют workflow_dispatch. Это соответствует текущему PR-first процессу, workflows не менялись. Аудит в Downloads обновлён под координационным lock. Скидки, sync_enabled и hub при истёкшем тарифе не затронуты.

Ловушка: успешные тесты по ранее собранному lib не подтверждают, что текущий параллельно изменяемый TypeScript собирается. Проверять build отдельно и не включать чужой незакоммиченный файл, чтобы скрыть ошибку проверки.

## 2026-10-28 · MAX и веб: проверено на работающем сервере

По запросу владельца сверены списки с двумя активными desktop/Telegram-беседами и Лентой. Найдены 20 старых облачных дублей, ошибочно оставленных активными: они переведены в архив по подтверждённым CID без удаления или объединения истории. После обновления и пересоздания бота API и snapshot одинаково показывают 3 активных облачных беседы, 30 архивных и 2 локальные. Подтверждены Main chat, Исправление ошибок, Лента; облачная принадлежность и архив являются независимыми признаками.

Изменения исходников уже включены в `42ce6938` (runtime, собранный веб) и `23725611` (веб-интерфейс). Восстанавливается `catalog_mode` после запуска, сохраняются отметки архива, список обновляется по WebSocket; архивная MAX-беседа не запускает модель, а предлагает выбор. В вебе доступны Активные/Архив/Локальные и архив/восстановление; старые локальные истории сохранены с возможностью удалить их явно. Переключение Активные/Архив в облачном web_client перерисовывает список сразу.

Проверки: 82 Python-теста синхронизации/каналов/истории; все 125 веб-тестов; ESLint для изменённых веб-файлов; Ruff для изменённых Python-файлов; сборка веба с TypeScript. Живые проверки после перезапуска: pull без ошибки, точный набор трёх активных CID, совпадение `/api/threads` и `/api/state`, запрет архивирования main (400), успешный идемпотентный archive-запрос для уже архивной беседы. Тестовые сообщения в мессенджеры и платные вызовы модели не отправлялись.

Выложено: бот с новыми module/static mounts пересоздан; сохранены остальные production-only дополнения в manager/api/service и ресурсы static. Backup кода/compose `/opt/nanolika-bot/backups/max-web-scopes-20261028T164725Z`; резервная копия БД перед архивированием `/opt/nanomuse/cloud/backups/max-web-active-20261028T164202Z`. Проверка compose до перезапуска поймала неверный отступ нового списка mounts; исходный compose восстановлен, отступ исправлен, проверка пройдена перед успешным пересозданием.

Ловушка: список файлов thread на сервере не равен активному списку облачных бесед. Не удалять данные только по совпадению заголовков и не превращать архивные облачные CID в локальные, чтобы спрятать дубли. Локальные проекты и инструментальная история остаются на установившем их компьютере.

## 2026-10-28 · Telegram ↔ desktop: продолжение беседы, офлайн и темы

Запрос владельца: ответы Telegram не показывались в desktop, вопрос о текущей генерации запускал отдельного серверного агента без локального контекста; сохранить продолжение с выключенным компьютером и догрузку после включения, не потерять облачный переключатель.

Сделано:
- Desktop объявляет `shared_chat`; бот проверяет точный CID на известном компьютере беседы, поддерживает старое объявление `chat.open_desktop` через безопасную проверку. Занятый локальный агент не заменяется параллельным серверным запуском.
- При недоступном компьютере беседа продолжается на сервере с общей историей. Модель получает актуальную доступность устройств и указание честно сообщать о невозможности проверить локальные процессы/файлы, не перезапускать платную генерацию ради проверки статуса.
- Desktop после подключения подтягивает удалённые сообщения; перед задачей воспроизводит сохранённые удалённые реплики как контекст модели. Ошибка обновления, архив или выключенное облачко не разрешают исполнение по старому сопоставлению.
- Удалённые сообщения располагаются вне свёрнутых групп действий; переиспользованный DOM очищается при смене чата, модельные контекстные строки не принимаются за пользовательские сообщения.
- Telegram доставляет ответы в активные native topics без копии в общую тему. Последняя явно выбранная/использованная тема хранится по аккаунту, владельцу и чату; архив и удаление проверяются по облачному каталогу. Фоновое обнаружение не меняет выбор. Bot API не управляет экраном, на котором Telegram открывается.
- В репозиторий возвращён уже работающий на сервере `sync/catalog.py` и его включение в engine, включая очистку старой ошибки после успешного pull; это исключает потерю lazy-истории при следующем развёртывании. Сохранены и включены связанные ранее незакоммиченные исправления гидратации истории, архивов и переключателя облака.
- Исправлены прежний неверный вызов BlockingPortal с keyword arguments в тесте и устаревшее ожидание удалённой по просьбе владельца кнопки «Архив».

Проверено: 79 Python-тестов истории/каналов/синхронизации; Ruff для изменённых Python-файлов; полная сборка плагина с проверкой типов; 39 desktop-тестов синхронизации/пузырей/облачного переключателя. Полный набор плагина: 421/423, остаются два сбоя вне этих правок — старое ожидание defaults в `desk.test.mjs` и неиспользуемые ключи в `locales.test.mjs`.

Установлено: серверный бот перезапущен, pull успешен, ошибка сброшена; backup `/opt/nanolika-bot/backups/telegram-shared-continuation-20261028T160528Z`. Плагин собран на исходном Mac, установлен в payload и внешнюю копию, приложение перезапущено при пустом списке выполняемых задач; backup `~/.nanolika/backups/telegram-sync-20261028T162256Z`. Живая проверка без запуска модели подтверждает тот же CID/sessionId и объявленный `shared_chat` после перезапуска. Реальный платный модельный запрос и отправка тестовых сообщений в мессенджеры не запускались.

Ловушки: share отключилась во время работы; доступ восстановлен через SSH владельцем. Не считать `sync-remote.json` доказательством отображения: сообщения уже были на диске, но прятались в DOM. Установленный профиль ссылается на payload плагина, поэтому обновлены обе копии. Остальные несвязанные изменения и подготовленные файлы в общем репозитории не включаются в этот коммит.

## 2026-10-28 · Тарифы: модели по плану, видео только с оплаченным балансом

Владелец: клиентская Лика должна видеть только модели своего тарифа; на низком тарифе убрать дорогие; видео открывать только если оплаченный баланс покрывает резерв (порог 590 ₽).

Сделано:
- `PLANS` понимает `max_chat_out_rub`, `max_video_tokens`, `video_min_paid_rub`, `models`, `exclude`;
- дорогие модели скрыты из `/v1/me/models`, `/v1/models`, полки и каталога, а `model_for` отказывает и при прямом вызове по id — 404 `plan_model` или 402 `video_deposit`;
- бесплатный/истёкший аккаунт получает условия самого низкого тарифа;
- видео-модели: 590 ₽ — вход в тариф; внутри активного месяца цена считается по конкретному клипу (4 с 480p ≈ 10,26 ₽), общего резерва 500 ₽ нет; на «Лике» остаются `clip-mini`/`clip` (per_clip ≤ 10 000), дорогие клипы закрыты;
- боевой `PLANS` в `docker-compose.override.yml` обновлён, relay пересоздан; `/v1/config` отдаёт новые поля;
- тесты: `test_plans.py` — низкий/высокий тариф, прямой вызов скрытой модели, видео-депозит; тесты моделей/полки — 5 зелёных.

Изоляция клиентов: личные ключи `via=user` уже model-only (`test_user_api_keys.py`: `/v1/me`, `/v1/me/keys`, `/v1/devices`, `/v1/auth/sign-out-all` → 403 `api_key_scope`); админка требует `X-Admin-Token`, которого в клиенте нет; токены Telegram/MAX живут на сервере бота. Остаётся организационное: клиентская Лика не должна иметь SSH-ключей/`.env` на своей машине, а её модельный ключ должен быть личным, не сессионным.

## 2026-10-28 · Медиа в Telegram и MAX: клипы играют, MAX больше не текстовый

После разбора upstream-телефонов стало ясно, что главный разрыв у нас — медиа в мессенджерах. У Telegram видео уходило документом, входящее видео игнорировалось; MAX вообще отказывался отправлять файлы (`MAX file delivery is not enabled`) и не читал вложения.

Что сделано:
- Telegram: `sendVideo`/`sendAnimation`/`sendAudio` вместо `sendDocument` для mp4/mov/webm, gif и звука; входящие `video`, `video_note`, `animation` скачиваются и попадают в сообщение.
- MAX: `POST /uploads` + `POST /messages` с вложением `image`/`video`/`audio`/`file`; повтор при `attachment.not.ready`; входящие вложения скачиваются (видео — через `GET /videos/{token}`), вебхук отвечает до загрузки клипа.
- Медиа-запросы идут отдельным HTTP-клиентом без bot token, чтобы токен не уходил на CDN/upload-хосты.
- Тесты: `tests/test_channels_telegram.py`, `tests/test_channels_max.py` — 24 зелёных; ruff чистый.
- Выложено на бот `151.248.123.86`: `overrides/channels/{max,telegram}.py` с бэкапами, контейнер перезапущен, Telegram и MAX connected.

Коммит `e09c47cb`.

## 2026-10-28 · Быстрое переключение чатов и проверка изоляции контекста

Владелец заметил медленную загрузку при переключении чатов и спросил, не смешались ли их истории. В веб-консоли сообщения уже хранились в памяти отдельно по ID темы, но при каждом возврате тема повторно запрашивала последние 150 событий. Теперь повторное открытие использует этот кэш; события продолжают обновляться по WebSocket, а после восстановления соединения текущая тема перечитывается с сервера.

Проверил разделение контекста: `MuseService` создаёт отдельный `MuseAgent` и файл сессии на каждый thread ID; Telegram-темы получают отдельные ID, облачная синхронизация отдельно сопоставляет их с CID. Добавлен тест двух синхронизированных чатов с разными вопросами и ответами. Прямого переноса истории одного чата в другой в этих путях не обнаружено. При этом память и цели общие для аккаунта, поэтому Лика может учитывать сохранённые сведения из другой темы; синхронизация переписки в облако сохраняет пользовательские сообщения и финальные ответы, но не трассы инструментов и промежуточные результаты. Это может объяснять забытые действия после перехода между устройствами.

Проверки в этой среде не стартовали: `npm run check` остановился на отсутствующем `eslint`, Python-тесты — на отсутствующем `loguru`, Ruff не установлен. Изменения на сервер не выкладывались, поэтому живой аккаунт и конкретные сохранённые сессии не проверены. Локально продолжить можно после восстановления зависимостей; живую проверку сделать после обновления веб-клиента.

## 2026-10-28 · Сайдбар: локальные файлы и видео, а не только URL

Владелец попросил, чтобы Лика показывала скачанный клип в правом сайдбаре, как opencode. `preview_show` умел только URL/HTML: локальный файл в `<video>`/`<img>` не подставить, `file://` в кадре не грузится. Плюс `present` давал карточку в чате, а клик по ней вёл в стоковый просмотрщик dsh, который отвечает «Предпросмотр для этого типа файлов пока недоступен».

Что сделано:
- новый маршрут `/nanomuse/file?path=…` (`preview-file.ts`) через `mountGuarded`, с `Range` и типами video/audio/image/pdf/text;
- `preview_show` принимает `path`, проверяет файл и вид; `showPreview` сам строит URL;
- панель рисует `<video>`/`<audio>`, `present` с клипом/картинкой/PDF сам открывает её;
- клик по карточке файла для видео/аудио перехватывается и открывается в нашей панели;
- найден и исправлен белый экран: `shell.overlay` искал панель только в `rooms` и не находил `PREVIEW_PANEL`; ширина уменьшена до `clamp(320px, 34vw, 640px)`;
- тесты: `tests/preview-file.test.mjs` (kindOfFile, Range, реальная отдача файла).

Сборка и typecheck зелёные; установлено в `payload/.../dsh-nanomuse/lib`, приложение перезапущено, клип открывается. Коммит `d3b99246`.

## 2026-10-28 · Общая облачная история перед ответом в веб-чате и MAX

Владелец попросил проверить перенос общей истории между компьютером, веб-чатом и MAX. Нашёл два разных пробела: HTTP/WebSocket-отправка в локальном веб-консоли могла сразу передать локально сохранённый контекст модели; при передаче сообщения из мессенджера на компьютер `sync_cid` уже передавался, но обработчик задачи компьютера его не использовал. MAX и Telegram используют один менеджер входящих каналов.

**Изменено:** общий менеджер каналов теперь обновляет контекст из полной облачной истории перед запуском модели; локальные HTTP- и WebSocket-входы делают то же для синхронизированных тем. При обработке задачи на целевом устройстве оно загружает ту же историю по `sync_cid`, исключая из неё текущую фразу, если она уже успела попасть в облако, чтобы не передать её модели дважды. Существующая привязка Telegram-темы остаётся по стабильному ID беседы. История обновляется только когда синхронизация включена и беседа разрешена настройкой синхронизации.

**Проверки:** Ruff и компиляция затронутых Python-файлов проходят. Pytest пока не стартует: доступный Python не содержит `loguru`, а в образе бота нет `pytest`. Добавлены тесты для веб-отправки из устаревшей локальной сессии и задачи на удалённом устройстве. Обновление развёрнуто на боте; проверены загрузка HTTP/WebSocket API, веб-консоли и обработчиков каналов, импорты модулей и health endpoint. Резервные файлы и копия прежнего compose сохранены в `/opt/nanolika-bot/backups/shared-history-all-entrypoints-20261028/`. Для сквозной проверки остаётся отправить по одному тестовому сообщению через веб и MAX.

Протокол задачи и поведение синхронизации описаны в `docs/hub.md` и `docs/cloud.md`.

## 2026-10-28 · Видео отключено в настройках приложения, остались только картинки

Денис решил: видео-генерацию пока не чинить, а просто убрать её из настроек приложения (код пусть живёт в исходниках, но в UI недоступен). Картинки оставить; у раздела медиа — только четыре элемента: модель для картинок, студия («Нарисовать новое лицо»), переключатель «ничего не рисовать» и кнопка «Вернуть образ Лики» (сбрасывает нарисованную внешность на встроенную Лику, с подтверждением).

**Что изменено** (`dsh-nanomuse`, коммит `69ab29c1`, ветка `feat/voice-ru`):
- `MediaSection.tsx` переписан: убраны выбор видео-модели, переключатель анимации, «сделать/переделать ролики», прогресс, список клипов, выбор «обычный/неподвижный/без картинки». Осталась одна карточка-модель картинок + кнопка студии + off + restore.
- `ModelsSection.tsx`: из `SLOTS` убран `video` — ряд «Создание роликов» больше не рисуется (тип `Slot` и код `SlotPicker` остались на месте).
- `OwnKey.tsx`: в карточке «Use it for» (свои ключи) видео-слот скрыт.
- `MuseSettings.tsx`: иконка раздела медиа — картинка вместо камеры.
- `locales.ts`: строки `mdDrawFaceSub`, `mdRestore`, `mdRestoreSub`, `mdRestoreConfirm` добавлены в en/zh/ru; `vrImageVideo*` переписаны без «и видео».

**Сборка и установка:** `npm run build` чистый; бандл обновлён в `payload/dsh/node_modules/dsh-nanomuse/lib/` (бэкап старого — `lib.bak-videooff-20261028-135931`). Живая проверка в интерфейсе — после перезапуска приложения. Кнопка restore вызывает `call('profile', { avatar: 'dragon' })` (существующий маршрут, канонический образ Лики).

## 2026-10-28 · Видео: опрос Polza шёл не туда — путь с лишним /api

Владелец попросил разобраться, почему видео не доходит до файла. Лика подала клип через релей, получила `task_id`, но опрос отвечал «генерация не найдена».

**Причина оказалась в одной строке.** Подача идёт на `POST /v1/media` (так задано в `VIDEO_PATHS`), а опрос был захардкожен на `GET /api/v1/media/{task_id}` — лишний `/api`, потому что база провайдера уже указывает на `polza.ai/api`. Прямая проверка: `GET /v1/media/{id}` → 200 `completed` + ссылка на S3, `GET /api/v1/media/{id}` → 404 «маршрут не найден».

Второй слой путаницы: в отчёте Лики id были обрезаны (`gen_2226678725` вместо `gen_2226678725162962945`), поэтому её прямые проверки тоже говорили «не найдено».

Исправлено в `overrides/deepseek-routing-20261027/nanomuse_cloud/api.py`: путь опроса берётся из `VIDEO_PATHS` (как у подачи), запасной путь — `/v1/media`. Бэкап рядом. Релей перезапущен.

**Проверено:** оба клипа Seedance 2 Mini (4 с) на Polza в статусе `completed`, ссылки на `s3.polza.ai` есть. Деньги не списаны — списание идёт при первом успешном опросе; фоновое закрытие подхватит их через 50 минут, приложение — при следующем опросе.

**Форматы, на которых стоят адаптеры:**
- polza: `POST /v1/media` → `GET /v1/media/{id}`, результат в `data.url` (dict или список);
- aitunnel: `POST /v1/videos` (202) → `GET /v1/videos/{id}`, статус `pending/completed`, ссылка в `unsigned_urls[0]`.

OpenAI `/v1/videos` как «стандарт» не используем: у OpenAI этот API закрыт 24.09.2026, у Google совместимый эндпоинт — с 22.10.2026. Внутренний контракт Лики (DashScope-подобный) остаётся, адаптеры — под наших поставщиков.

## 2026-10-28 · Telegram should answer from the cloud transcript

The owner clarified that a Telegram topic and the desktop chat must be two clients of one server-owned conversation. Turning off the computer must not fork or shorten the conversation.

The Telegram topic already maps to a stable cloud conversation ID, but the agent answered from the runtime's persisted local session. Incremental sync only fills rows after its cursor, so a stale session could omit older messages that were still present on the relay. Added a pre-turn hydration step: flush any completed local turn, read the mapped cloud transcript in pages, and rebuild the agent's shared user/assistant context from it. If sync is unavailable or the conversation is busy, the bot now declines the turn instead of answering from stale history. Added a focused regression test for a stale local session.

Checks: Python syntax compilation and Ruff lint passed. The focused pytest could not start because the available Python environment lacks `loguru`; Ruff's formatting check also reports pre-existing formatting in the touched modules, outside the new code. Deployed to `/opt/nanolika-bot/overrides/chat-center-20261023-233911/sync/engine.py` and `/opt/nanolika-bot/overrides/channels/telegram_topics.py`; backups are in `/opt/nanolika-bot/backups/telegram-cloud-hydrate-20261028/`. Both modules compiled in the container, the imported live classes expose the hydration call, and the bot is healthy after restart. A real Telegram turn is still needed to verify answer continuity and computer delivery.

Trap: the cloud cursor tells the runtime which events are new, but it does not prove its local agent session contains the complete conversation. A channel turn must hydrate from the conversation transcript itself before inference.

## 2026-10-28 · Telegram topic messages stopped before reaching the agent

The owner reported that a message in a Telegram topic got no AI reply and did not appear on the computer. The live bot log shows Telegram received the update at 10:57:35, then `_handle_inbound` crashed because it always passed `once_key` and `once_model`, while the installed `MuseService.send()` predates those optional parameters. The service call happens before the user event is persisted, so the message was lost from the shared conversation as well as unanswered.

Changed the ordinary channel path to omit per-chat Cloud billing parameters when no per-chat key is configured. Preserved those parameters when a chat has its own Cloud key. Applied only this small change to the live channel manager, preserving its unrelated WebApp keyboard behavior. Server backup: `/opt/nanolika-bot/backups/telegram-send-compat-20261028/manager.py`.

Live check: the patched module compiles in the bot container; after restart `nanolika-bot` is `running healthy`, and the updated file hash differs from the recorded backup. The local Ruff check passed for the source and test; the focused pytest could not run because the mounted Python 3.11 virtualenv points to a missing interpreter and system Python lacks `loguru`. A fresh Telegram message is still needed to confirm the live agent reply and computer sync. The earlier update was acknowledged by Telegram, but was not saved in the conversation and cannot be replayed by the bot.

Trap: the production channel manager contains a legacy WebApp keyboard block absent from the current source tree. Replacing the whole file would have removed it, so the live change was applied narrowly to the deployed file.

## 2026-10-28 · Починка загрузки кадров для видео аватара через Polza

Владелец попросил исправить генерацию четырёх роликов настроек аватара: картинки создавались, видео завершалось `Upload policy failed (HTTP 502)`.

Причина: видео-модели направлены в Polza, но клиент всегда запрашивал upload policy у DashScope с ключом `UPSTREAM_KEY`. У Polza вместо этого есть своё временное хранилище; его API возвращает URL кадра в корне JSON, на `s3.polza.ai`. Polza Media принимает кадр в `input.images` как URL. Также активный override релея всё ещё отправлял медиа по `/api/v1/media`, что прокси дополнял повторным `/api`.

Исправлено: релей теперь сообщает клиенту режим загрузки через Storage API, принимает и ограничивает размер/тип кадра, загружает его с ключом Polza и проверяет ссылку хранилища; видео-запрос переводит `img_url` в `input.images`. Avatar Studio использует этот маршрут для Polza, сохраняя прежнюю загрузку DashScope. Обновлён путь медиа на `/v1/media`.

Выложено в живые overrides; добавлен override Avatar Studio и bind mount в Compose, bot пересоздан. Бэкапы до изменений: `/opt/nanomuse/cloud/backups/avatar-video-20261028/api.py.before` и `/opt/nanolika-bot/backups/avatar-video-20261028/`.

Проверки: Ruff и Python syntax compilation проходят; relay `/healthz` отвечает, relay импортирует API, bot здоров и загружает новый `AvatarStudio._clip_once`. Автотест добавлен для upload → video payload, но не запускался: доступное окружение без совместимого Python 3.11/httpx, а установка зависимостей не выполнялась. Платная генерация клипов для проверки не запускалась.

## 2026-10-28 · Почему DeepSeek шёл через Polza, сколько это стоило и почему контекст не сжимался

Владелец спросил, зачем чат идёт через Polza и откуда 96 ₽ за час, при том что картинки шли другим механизмом.

**Маршрут.** Релей выбирает по живой цене. На `deepseek-v4-flash`: polza 9,43/18,86 ₽ за 1M и кэш 1,89; routerai 11,63/17,27 (но баланс 1,25 ₽ ниже порога 90 ₽, поэтому выключен); deepseek напрямую и openrouter 12,08/48,32. Polza дешевле — потому она. Внутри polza запрос исполняет GMICloud (`inference_provider: GMICloud` в учёте).

**Откуда счёт.** По журналу: 66 запросов за час, **46,1 млн токенов входа**, фактическая закупка **147 ₽**, в среднем 2,23 ₽ и **699 тыс. токенов на запрос** (максимум 708 674). То есть деньги уходят не на наценку провайдера, а на объём: каждый шаг агента заново отправляет всю переписку.

**Почему не сжималось.** В dsh есть пакеты сжатия (`dsh-compaction-basic`, `command-compact`, `tool-result-pruner`), они включены в пресет `nanomuse.patch.yml`. Но порог по умолчанию — `thresholdRatio 0.8`, то есть 80% окна: у модели окно 1 048 575 → сжатие включается на ~838k. Сессия Лики держалась на ~700k и до порога не доходила, поэтому каждый запрос тащил весь контекст.

**Сделано:** в пресете у `compaction-basic` выставлено `thresholdRatio: 0.15` и `retainTokens: 15000` — сжатие около 157k, свежие 15k остаются дословно. Бэкап пресета рядом (`.bak-<время>`). Нужен перезапуск NanoLika, пресет читается при старте сессии.

**Оценка:** при потолке 157k те же 66 запросов — около 33–50 ₽ вместо 147 ₽ (в 3–4 раза меньше). При 84k (ratio 0.08) — около 20 ₽, но это уже заметно режет детали.

**GMICloud напрямую.** Дал владелец токен. Каталог открывается: `https://api.gmi-serving.com/v1/models`, 85 моделей с ценами. Чат отвечает `402 insufficient_quota` — на аккаунте нет баланса. Цены: `deepseek-v4.1-flash-1017` — вход 24,16, выход 96,64, кэш-чтение **0,48 ₽/1M** (вчетверо дешевле кэша polza). Для контекста в 700k с кэшем это было бы выгоднее, но для короткого контекста после сжатия polza дешевле по входу (9,43 против 24,16).

**Как это делает opencode** (для ориентира): сжимает при приближении к пределу модели (буфер 15% окна, минимум 16k), оставляет ~15k свежих токенов дословно, остальное заменяет структурной сводкой (Objective, Requirements, Decisions, Work state, Next Move, Files); запрос сводки переиспользует prompt-кэш; длинные результаты инструментов режутся; при отказе провайдера «слишком длинно» — сжатие и один повтор.


## 2026-10-28 — Telegram feed formatting and sync recovery

- Added readable Markdown rendering for JSON cards sent to the mapped Telegram «Лента» topic.
- Restored the signed-in Cloud account binding to conversation sync at runtime startup.
- Kept Telegram topics attached by stable relay conversation ID when local thread IDs differ.
- Replayed a bounded recent sync window on the bot. The relay cursor is advancing again; delivery receipts for the two specific old rows did not confirm, so those exact messages still need a live Telegram-side check.
- Deployed with backups under `/opt/nanolika-bot/backups/`; Python syntax compilation passed.

## 2026-10-28 · «Слишком много запросов сразу»: лимит одновременных запросов был 4

Владелец спросил про ошибку «Слишком много запросов сразу. Подождите немного и повторите». В журнале релея — событие `budget.refused | too_many_in_flight` в 07:26:52 и один ответ 429 на `/v1/chat/completions`.

Это не провайдер и не баланс: у релея есть **лимит одновременных запросов одного аккаунта** — `MAX_IN_FLIGHT`, по умолчанию **4** (`config.py:690`, проверка в `service.py:2631`). Лика (deepseek-harness) запускает больше четырёх запросов сразу — инструменты, параллельные шаги, — и пятый отбивается с `too_many_in_flight`. Приложение показывает это как «много запросов».

Поставил `MAX_IN_FLIGHT=12` в `.env` и пересоздал релей; проверено: `settings.max_in_flight == 12`.

Если 429 пойдут от самих провайдеров (polza/routerai любят бурсты), их релей перебирает по цепочке — 429 входит в `retryable()`.

## 2026-10-28 · Telegram topic card buttons

Removed the per-topic history card buttons for Archive and Chats. Earlier remains first in the inline keyboard, while Later remains on older pages. Telegram positions inline keyboards below message text, so “top” means the first and only action on the latest page. Bot backup: `/opt/nanolika-bot/backups/chat-state-sync-20261028T072118Z`; deployment completed and the container is running. Details: nanoLika/artifacts/chat-state-sync-2026-10-28/RESULTS.md.

## 2026-10-28 · Картинка в чате: polza понимает только `input_image`

Следующая ошибка Лики после 413: `chat 400 deepseek-flash: Модель "deepseek/deepseek-v4-flash" не имеет доступных провайдеров с поддержкой: input_image`. Сообщение сбивает с толку — «нет провайдеров» звучит как проблема баланса или роутинга, а на самом деле это **диалект polza**.

Проверил прямыми запросами к каждому:
- polza: `input_image` со строкой в `image_url` → **200**; стандартный `image_url` с объектом `{"url": ...}` → **400** и ровно это сообщение;
- aitunnel, routerai: принимают оба формата.

То есть приложение шлёт обычный OpenAI-формат `image_url`, polza его не понимает и отвечает «нет провайдеров с поддержкой: input_image» (у них внутри имя вида `input_image`).

Исправлено в `provider_routing.py`: `_polza_message()` переводит `image_url` → `input_image` со строкой, и `_supplier_body("polza", …)` прогоняет через неё все сообщения. Деплой: файл живёт в `overrides/deepseek-routing-20261027/`, бэкап рядом. Проверено после фикса: polza с картинкой (тот же `deepseek/deepseek-v4-flash`) — **200**.

Урок на будущее: у polza своя схема контента, и её сообщение об ошибке называет *свой* внутренний тип, а не стандартный. Смотреть на фактический запрос, а не на текст ошибки.

## 2026-10-28 · Лика падала на тексте: 413 от прокси polza (лимит 1 МБ)

Владелец: «она от текста падает, до картинок ещё даже дело не дошло». В журнале событий релея — четыре подряд `upstream.error: chat 413 deepseek-flash` от `deepseek-harness/0.2.0-rc.2`. То есть запрос доходил до облака, а 413 отдавал не провайдер, а **nginx прокси перед polza**: в `polza-proxy.conf` не было `client_max_body_size`, у nginx по умолчанию 1 МБ. Длинная переписка (инструменты, история) переваливала за мегабайт — прокси отвечал 413, релей показывал его как ошибку провайдера, приложение — «The model provider is having trouble» / `PI_AI_ERROR`.

Исправлено: в `polza-proxy.conf` добавлен `client_max_body_size 20m` (столько же, сколько у Caddy `request_body max_size 20MB`), контейнер `polza-proxy` пересоздан. Проверено телом 7,5 МБ: было 413, стало 200.

Заодно проверено, что сами провайдеры живы после пополнения: polza — 200 на `deepseek/deepseek-v4.1-flash-1017`, openrouter — 200. Балансы: polza 1000,61 ₽, openrouter ~986 ₽ в рублях, aitunnel 382 ₽; routerai 1,25 ₽ и proxyapi 40,82 ₽ ниже порога маршрутизации 90 ₽, поэтому в чат-цепочку не попадают.

Урок: 413 от внутреннего прокси выглядит как ошибка провайдера и уводит в сторону. Смотреть `events.detail` в `cloud.db` — там видно и статус, и модель.

## 2026-10-28 · Chat sync verification closeout

The final redundant incremental TypeScript run was stopped after seven minutes of mounted-share reads with no diagnostics; it is not counted as passing. The preceding full main-selection typecheck passed, and the final offline guards passed all 28 desktop sync checks. Five cloud UI checks and relevant Python/runtime checks passed. Relay and bot are live; installed desktop host code still requires an app restart. See nanoLika/artifacts/chat-state-sync-2026-10-28/RESULTS.md for state and rollback.

## 2026-10-28 · Final Main chat selection and offline sync verification

The owner clarified that the 74-message Main chat, not the four-message namesake, is the active one. Its original cid and transcript now remain the account main. The short namesake is archived separately. The runtime's existing move operation and the desktop catalogue handler preserve both histories without merging. Live checks confirm three active chats, one topic per active chat, matching bot/account main ids, and unpaused sync; authenticated live history pagination returned two non-overlapping pages of eight messages.

Added two offline guards: archive before a new chat's first upload is retained until it gets a cid, and a queued archive of a remotely deleted chat no longer blocks all catalogue pulls. A tombstone's local cloud mark prevents accidental republishing while still archiving its local copy. All 28 desktop sync checks, five cloud UI checks, fourteen topic assertions and runtime/history checks pass. Final runtime updates are installed on both desktop profiles with original backups retained; a desktop restart remains required. The preceding full main-selection typecheck passed; the last incremental check after the offline guards is running, no diagnostics yet (nanoLika artifact session 99431). No paid model calls were needed. Exact state and rollback paths remain in nanoLika/artifacts/chat-state-sync-2026-10-28/RESULTS.md.

## 2026-10-28 · Shared chat state published, selected Main chat preserved

The owner selected the 74-message Main chat, Исправление ошибок and one Лента. The four-message Main chat was archived separately. The selected existing conversation became the account main, with ids and transcripts preserved. Live relay and bot checks confirm exactly three active conversations, one native topic mapping per active conversation, matching account/bot main ids and unpaused sync with no error.

Published relay archive/restore and stable catalogue/feed support. Published bot catalogue discovery, id-based topic binding, active/archive menus, paginated eight-message previews and reconnect protection for old unmapped local threads. Replaced the revoked bot catalogue service sign-in through the authenticated bot endpoint and encrypted vault; revoked the unused diagnostic session. Private Telegram topics cannot be closed/reopened by Bot API, so archive uses the shared menus and native name prefixes without deleting messages.

Desktop runtime updates are installed on both found profiles with code backups. Cloud-off is now a local pause retaining the shared id/history, and a first message no longer silently enables a local chat's cloud. Archive changes retry after offline periods, restore follows the relay without feedback, and main selection follows the chosen existing session without combining transcripts. The apps were not force-quit: a desktop restart is still needed to activate the host code.

Checks: 26 desktop sync checks, 5 cloud UI checks, 14 mocked Telegram topic assertions, history/runtime main-move and isolated relay integration checks passed; relevant Python Ruff checks passed. Initial full desktop typecheck passed after the narrowing repair; final incremental typecheck for the main-selection follow-up is still running, with no diagnostics at last check. Result, remaining activation/typecheck and exact backups: nanoLika/artifacts/chat-state-sync-2026-10-28/RESULTS.md. Critical traps: stale service auth paused catalogue sync independently of per-chat inference; private Telegram close/reopen methods are supergroup-only; source/share and deployed lazy sync differ; SMB listing prevented one copy before installed code was changed, so the final build was staged locally.

## 2026-10-28 · Видео: перебор polza → aitunnel, и почему сборка не доезжала

Владелец попросил доделать видео. Сделано, но по дороге выяснились две вещи, которые стоили времени.

**Блокер №1 — баланс polza: 0,61 ₽.** Поэтому и картинки, и видео падали с 402. Проверил балансы всех: polza 0,61 ₽, routerai 1,25 ₽, **aitunnel 382 ₽**, openrouter ~$10, proxyapi отвечает 404 на баланс. То есть реально платить сейчас может aitunnel, а не polza.

**Блокер №2 — сборка образа не доезжает до контейнера.** Живой релей грузит код не из пакета в образе, а из bind-монтирования `overrides/`:
- `overrides/deepseek-routing-20261027/nanomuse_cloud/api.py` → `/srv/nanomuse-cloud/nanomuse_cloud/api.py`
- `overrides/connection-state-20261027/nanomuse_cloud/db.py` → туда же.
Поэтому `docker compose build` + `up -d` ничего не менял (файлы в контейнере оставались старыми, `docker cp` падал с «device or resource busy»). Править надо именно эти файлы в overrides. Бэкапы рядом: `.bak-<время>`.

**Что умеют по видео (проверено запросами):** polza — `/api/v1/media` (таск), aitunnel — `POST /v1/videos` (202, `id`) и `GET /v1/videos/{id}` (status `pending`/`completed`, ссылка в `unsigned_urls[0]`, стоимость в `usage.cost_rub`). RouterAI видео требует держать 500 ₽ под клип — при балансе 1,25 ₽ это 402. Bothub видео на тарифе не открыто («not available on your plan»).

**Код:**
- `db.py`: колонка `video_tasks.provider` (+миграция), чтобы опрос знал, кто держит задачу;
- `api.py`: `_provider_video_submit` принимает провайдера, у aitunnel свой путь и тело `{model, prompt, seconds}`; `_provider_video_poll` умеет aitunnel; опрос в маршруте и в фоновом закрытии берёт провайдера из задачи;
- `_image_with_fallback` и видео-цепочка идут по каталогу, на 402/429/5xx — к следующему;
- `catalog_routes.py`: `video_candidates` (polza, aitunnel);
- `export.py`: в кандидаты теперь попадает и aitunnel (provider = aitunnel, id — его имя модели), в том числе без цены; у aitunnel заполняется `provider_model_id` (имя модели из payload).

**Итоговые цепочки:** `clip`/`clip-mini`/`clip-pro` — polza → aitunnel; `image` — polza → bothub → routerai → aitunnel (текущий поставщик всегда первым).

**Проверено:** aitunnel-видео seedance-2.0-mini 4 с — задача создалась, дошла до `completed`, ссылка получена, списано 64,80 ₽. Релей healthy, в логах ошибок нет.

**Что осталось:** биллинг всё ещё считает по `spec.per_image`/`per_clip` из конфига, а не по `retail` из `user_catalog`; галочка «скрыть модель» пока только в браузере. И баланс polza надо пополнить — иначе первый же запрос каждый раз уходит в резерв.

## 2026-10-28 · Релей: перебор поставщиков картинок включён, ключи Bothub и OpenRouter

Владелец дал ключи Bothub и RouterAI, потом сообщил, что оплатил OpenRouter. Сделано.

**Ключи.** Bothub (`BOTHUB_KEY`) добавлен в `/opt/nanomuse/cloud/.env`, права 600, в git не попал. RouterAI уже был (`ROUTERAI_KEY`, `PRICE_ROUTERAI_KEY`) — проверил, работает. OpenRouter — ключ `OPENROUTER_KEY` уже был, ходит через локальный прокси `172.19.0.1:9443`; баланс: пополнено $25, израсходовано $15, осталось ~$10.

**Что умеют поставщики (проверено запросами 28.10):**
- Bothub: `POST /openai/v1/images/generations`, ответ `b64_json`. Сгенерировал две картинки: gpt-image-2 (~1 ₽) и gemini-3.1-flash-image (~10,5 ₽). Ключ рабочий.
- RouterAI: `POST /api/v1/images/generations` есть (400 на несуществующую модель).
- AITUNNEL: `POST /v1/images/generations` есть.
- OpenRouter и ProxyAPI: такого маршрута нет (404) — картинки они отдают только через чат, в перебор не годятся.

**Код.** `cloud/nanomuse_cloud/catalog_routes.py` — читает `/srv/models/api/catalog.json` (mtime-кеш, рестарт не нужен), отдаёт кандидатов по возрастанию закупки; в перебор картинок пускает только polza/bothub/routerai/aitunnel. В `api.py` добавлены `_openai_image` (OpenAI-совместимый адаптер: b64_json или url) и `_image_with_fallback`: идёт по цепочке, на 402/429/5xx переходит к следующему, на 4xx содержания останавливается. Текущий поставщик модели остаётся ПЕРВЫМ — в каталоге у polza цена за токены, а не за картинку, и по ней он выглядел бы дороже, чем есть.

Цепочки сейчас:
- `image`: polza → bothub → routerai
- `image-lite`: polza 2,90 → bothub 4,56 → routerai
- `image-banana`: polza 3,00 → routerai → bothub
- `image-seedream`: polza 4,00 → routerai 3,66 → bothub 4,13

**Деплой.** В `docker-compose.yml` у relay добавлен том `./models:/srv/models:ro`, в `.env` — `MODELS_CATALOG=/srv/models/api/catalog.json` и `bothub` в `CLOUD_PROVIDERS`. Релей пересобран (`docker compose build relay && up -d`), каталог виден из контейнера: 709 моделей. Бэкапы на сервере: `nanomuse_cloud/api.py.bak-*`, `docker-compose.yml.bak-*`.

Риск, который остался: `charge_image` по-прежнему берёт цену из конфига модели, а не из `user_catalog` — биллинг на новую базу не переведён. Это следующий шаг, вместе с видео (там пока только polza).

## 2026-10-28 · Shared archive, cloud switch and Telegram history, implementation in progress

The owner requested continuous agreement of desktop/Telegram chat state, preserving per-chat cloud-off and loading history in pages. Inspection of the actual shared desktop profile confirmed archive flags missing from the relay. Published relay sync/catalogue support and consolidated daily Feed metadata without deleting message text. Two already archived shared chats were reconciled; the final two active chat names are still requested, so other legacy chats were not guessed or mass-published as Telegram topics.

Desktop source now queues offline archive changes, applies remote restore without echoing archive operations, and treats side-chat cloud-off as a local pause retaining the shared id/history. The main account chat stays shared; account deletion is separate. Telegram source has catalogue-driven topic discovery, archive close/reopen, separate active/archive lists, and bounded eight-message history cards with older cursors. Source/client methods and three-language UI text were updated. These desktop/bot changes are not installed yet.

Checks: isolated relay integration passed; fourteen topic checks and history-page checks passed with mocked Telegram against bot dependencies; touched Python Ruff checks passed. Desktop sync has 24 passing assertions and one pre-existing C9 failure, independently reproduced against the before-change source. Full desktop typecheck is still running over slow SMB dependencies. Initial declaration build and redundant Python collection were cancelled after prolonged share reads. Rollback and next steps are recorded in nanoLika/artifacts/chat-state-sync-2026-10-28/RESULTS.md. Do not call this rollout complete: final build, installation, active-chat choice and live Telegram verification remain.

## 2026-10-28 · Таблица переделана и релей: нашёлся блокер с ключами

Владелец попросил сделать таблицу читабельной: во всю ширину, липкая шапка, отдельно «закупка вход/выход» и «клиенту вход/выход», галочка скрытия первым столбцом, серые строки для моделей, которые можно улучшить прямыми контрактами. И начинать релей.

Сделано в `~/Dev/modelscope`:
- шапка в две строки с группами «Закупка ₽» и «Клиенту ₽», липнет при прокрутке (с 1200px, когда таблица влезает без горизонтальной прокрутки);
- колонки: ✓ · Модель · Вендор · Тип · Закупка у · закупка вход/выход · клиенту вход/выход · Маржа · Контекст · Предл. · Подключено;
- первая колонка — галочка «показывать клиентам»: снял — строка гаснет, список скрытых живёт в localStorage, есть «скачать скрытые.json» и «показать все». У клиентов пока не скрывает: для этого нужен API на сервере, следующий шаг;
- серые строки — 34 модели, где наша закупка идёт по прайсу вендора или выше. `official.py` берёт официальные цены из models.dev, `pricing.py` считает `direct_gain`, в ячейке метка «прямой» с подсказкой. Это и есть список для прямых контрактов;
- `app.js` получил версию в адресе — иначе браузер держал старый скрипт после пересборки (из-за этого таблица один раз отрисовалась пустой);
- класс `wide` уже был занят адаптивным текстом и на узком экране скрывал всю страницу — для широкой вёрстки теперь `full`.

Релей: проверил, чем он может платить. В контейнере есть ключи polza, aitunnel, openrouter, proxyapi. **Нет bothub и routerai.** А в каталоге для главной картинки `image` (gemini-3.1-flash-image) второй по цене — как раз bothub, 10,56 ₽/карт. У остальных (openrouter, proxyapi, aitunnel) эта модель продаётся за токены через чат — другой адаптер и другая проверка. Для видео `clip-pro` единственный оплаченный посекундный канал — polza (routerai без ключа).

То есть код перебора сам по себе не спасёт: нужен второй платящий канал. Варианты — ключ Bothub (дешевле всего, 10,56 ₽/карт.) или пополнение polza; для видео — ключ RouterAI. Пока это не решено, деплой перебора в живой релей не делал: поменяется ноль поведения, зато появится риск.

Опубликовано, коммит `594f6f6`.

## 2026-10-28 · Telegram and desktop chat synchronization, investigation

Asked to share desktop conversations with Telegram, then clarified that history should load in pages instead of copying everything. Read-only inspection confirmed that the installed desktop and bot use the same account. The current account catalogue has nine live conversations; only three have a matching Telegram topic mapping. Automatic topic discovery excludes historical conversations and requires loaded user events, while the bot catalogue lazily loads bodies only for selected threads. This can prevent unmapped conversations from becoming topics. Existing topic mappings also contain repeated conversation ids; no topics were merged or removed.

Proposed scope: shared catalogue, stable conversation-id mapping, recent history on demand and cursor-based older pages. Native Telegram Bot API has callback updates but no scroll update, so older history needs a button or a WebApp. No runtime, account settings or customer messages changed. Implementation remains pending the PLAN stage: inspect and change the relay history API, Telegram topic/history controls and their tests; preserve existing conversations and avoid matching by title alone.

## 2026-10-28 · Витрина на сервере и каталог для релея

Владелец решил: работаем только с новой таблицей («в той таблице больше утонем»), старую операторскую не трогаем, пока релей и биллинг не переведены. Попросил задеплоить таблицу с конечными ценами.

Опубликовано на `https://cloud.nanolika.ru/phzhk6tnr6d0e2ac7j3ze8eose/`: таблица с колонками «Закупка», «Клиенту ₽», «Маржа»; страница «Наш каталог» — 690 моделей с закупкой, клиентской ценой и маржой.

Чтобы релей мог переключаться между поставщиками, добавлено:
- `offers.provider_model_id` — id, которым поставщик называет модель (`google/gemini-3.1-flash-image` у polza, `gemini-3.1-flash-image` у bothub). Без него релей не знает, что просить у конкретного поставщика. Заполняется при сборе.
- `export.py` → `site/api/catalog.json`: `menu` (внутренние имена `image`, `image-banana`, `clip-pro` → слаг базы, 21 запись), `retail` (цена клиенту), `candidates` (поставщики по возрастанию закупки с id и ценой). 709 моделей, у 530 два и более кандидата.
- Кандидаты в одной единице с ценой клиента идут первыми (`same_unit`): «10 ₽ за картинку» и «40 ₽ за 1M токенов» числом не сравнить, поэтому цены в других единицах остаются резервом, но не определяют порядок.
- Устаревшие алиасы routerai с `~` пропускаются, если у поставщика есть канонический id.

Проверено: `https://cloud.nanolika.ru/phzhk6tnr6d0e2ac7j3ze8eose/api/catalog.json` — 547 КБ, 200. Для `image` кандидаты: bothub 10,56 ₽/карт. (в той же единице), дальше polza, routerai, openrouter, proxyapi (у них цена за токены — резерв). Для `clip-pro` (seedance-2.5): bothub 18,17 / polza 18,90 / routerai 24,20 ₽/с.

Дальше по плану: научить релей читать этот файл и перебирать кандидатов при 402/429/5xx, потом биллинг по `retail`. Старую страницу цен (`retail.json`) гасим только после сверки списаний.

Коммиты `169bb7f`, `7688a2f`.

## 2026-10-28 · Витрина: цена клиенту из закупки, 690 моделей

Владелец переключил работу на таблицу моделей: «она более адекватная, будем из неё делать конечные цены для потребителей» — и попросил колонки выхода для клиентов, чтобы не уйти в минус.

Сделан слой витрины (`pricing.py` + `user_catalog`), считается из закупки:
- **закупка** — минимум среди прямых площадок (polza, routerai, bothub, proxyapi, openrouter, прямой DeepSeek). aitunnel в базовую цену не идёт: на закрытых моделях он дороже в 2,5 раза, а где дешевле (deepseek fp4) — другой продукт по качеству;
- **наценка** — токены ×1,6, картинки ×1,5, видео ×1,45, речь ×1,5. Запас на эквайринг, поддержку и на то, что дешёвая площадка может отказать и запрос уйдёт к следующей;
- **округление** — вверх до двух значащих цифр, чтобы округление не съедало маржу.

Заполнено 690 моделей: chat 406, image 94, video 60, embedding 37, speech 37, transcription 26, решения 21, rerank 5, музыка 3, классификация 1. Маржа 45–75%, медиана ~60% — выше aitunnel (×2,48) мы не задираем.

Примеры (закупка → клиенту): gpt-6.1-terra 43,12 → 69 ₽/1M (+60%), claude-sonnet-5.5 144,45 → 240 (+66%), gemini-3.1-flash-image 10,56 → 16 ₽/карт. (+52%), gemini-3.1-flash-lite-image 2,90 → 4,40 ₽/карт. (+52%), nano-banana-2.1 3 → 4,5 ₽/карт., gpt-image-2 1,92 → 2,9, seedance-2.0 12,83 → 19 ₽/с (+48%).

Заодно у polza «запрос» для картинок теперь считается картинкой (₽/карт.), иначе в одной строке стояли «₽/запрос» и «₽/карт.».

В таблице на главной — колонки «Клиенту ₽» и «Маржа»; на странице «Наш каталог» — закупка, у кого, клиентская цена, выход и маржа. Цифры предварительные, правятся в `user_catalog`.

Отдельно: жалоба Лики «не дают картинок» — это про релей nanoMuse (псевдонимы меню `image`, `image-banana` → реальные id и перебор поставщиков при 402). К таблице отношения не имеет, работа отдельная.

Коммит `169bb7f`, опубликовано.

## 2026-10-28 · Где мы уже на дне, а где отдаём плечо: колонка «Закупка»

Владелец спросил, как забрать те 2–2,5 плеча, которые берёт aitunnel, и попросил пометить модели, где дешевле уже не найдём, и проблемные, где нужны другие выходы. Посчитал по базе (729 моделей, 3496 предложений).

**Плечо — это не закупка, а розница.** Сравнение «aitunnel против нашего минимума» по 219 моделям, где есть обе цены: aitunnel дороже в 217 случаях, медиана ×2,48, максимум ×9,95 (qwen3-5-9b: 1,61 против 16,00 ₽). То есть мы уже покупаем в 2,5 раза дешевле aitunnel. Забрать плечо можно не сменой поставщика, а ценой продажи: ставить розницу между своим закупом и aitunnel (например ×1,8) — маржа ~45% и всё ещё дешевле aitunnel. Полное плечо ×2,48 забирается только прямым договором с вендором: сейчас разницу между прайсом OpenAI/Anthropic и нашей ценой берут агрегаторы (Bothub ×0,88 прайса, Polza ×1,16, RouterAI ×1,30, OpenRouter ×1,00).

**Открытые модели, где дешевле не найдём — 83.** Это модели, которые внутри aitunnel держат 3+ хоста (значит открытые веса), и наш минимум уже ниже любого из них: gpt-oss-20b (1,45 против 3,60), qwen3-5-9b (1,61 против 16,00), mistral-nemo (1,73 против 3,31), gpt-5-nano, glm-5.2, glm-5.3-flash, gpt-oss-120b, deepseek-v4-flash, gpt-6-luna, gemini-2.5-flash-lite. Для них рычага снизу нет — цена уже на дне рынка.

**Где aitunnel дешевле — 2 модели.** deepseek-v4.1-flash (наш 5,71 против 1,86) и deepseek-v4.1-flash-1017 (11,54 против 3,64) — у aitunnel это OpenInference на fp4, то есть другой продукт по качеству. Выход: либо брать там, либо договариваться с OpenInference напрямую; на fp8 разница не такая.

**Проблемные — 178 моделей с единственным источником цены** (метка «1» в новой колонке): bothub 69, routerai 42, polza 40, openrouter 24, proxyapi 3. Из них 41 картинка и 37 видео — там сравнивать не с чем вообще (aleph-2, hailuo-3, flux-3-video, avatar-iv, heygen-video-1 — только RouterAI; flux-2-pro, text-to-image, gemini-omni-1.1-flash — только Polza). Ещё 20 моделей без цены совсем (эмбеддинги GigaChat, flux-2-max/klein-4b, seed-audio, gpt-4o-transcribe-diarize).

Для интерфейса добавлена колонка «Закупка»: зелёная метка — дешевле у прямых площадок, жёлтая — у aitunnel, «1» — других источников цены нет. Коммит `433c83c`, опубликовано.

## 2026-10-28 · Проверка aitunnel: цифры верные, но это его розница, а не прайс провайдеров

Владелец попросил проверить, не врёт ли aitunnel про цены его провайдеров. Проверял тремя способами.

1. **Наши цифры против каталога aitunnel.** Сырой ответ `data/raw/aitunnel/gpt-6-1-terra.json` содержит ровно то, что в базе: `openai/flex` 100/500, `openai` 200/1000, `azure/eu` 240/1200, `openai/fast` (priority) 400/2000 ₽ за 1M. Расхождений нет — сборщик ничего не пересчитывает.

2. **Прайс провайдера против aitunnel.** У Anthropic на anthropic.com/pricing: Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50 за 1M. По курсу ЦБ 80,53 ₽/$ это 322/1611, 161/805 и 8/40 ₽. Aitunnel на всех четырёх маршрутах (Azure, Google, Amazon Bedrock, Claude Platform on AWS) показывает одинаковые 800/4000 ₽ — это ×2,48 к прайсу Anthropic. У OpenAI `gpt-6.1-terra` официально $1/$5 (models.dev, раздел openai) = 80,53/402,65 ₽, у aitunnel — flex 100/500 (×1,24), обычный 200/1000 (×2,48), Azure 240/1200 (×2,98), fast 400/2000 (×4,97). Вывод: у закрытых моделей aitunnel ставит свою розничную цену с наценкой ×2,48 (flex ×1,24, priority ×4,97), а имя в строке — это маршрут, через который он отдаёт модель, а не тот, кто поставил цену.

3. **Открытые модели — наоборот, честный пассаж.** У `glm-5.3` официально $1,4/$4,4 (Z.AI) = 112,74 ₽, а внутри aitunnel 49 разных хостов от 13,98 ₽ (Wafer fp4, ×0,12) до 560 ₽ (Alibaba prime, ×4,97) — там цена реально зависит от хоста и квантизации. У `deepseek-v4.1-flash-1017` от 3,64 ₽ (OpenInference fp4) до 90 ₽ (Fireworks us) при официальных 12,08 ₽.

Для сравнения те же закрытые модели у остальных: OpenRouter ровно по прайсу (×1,00), RouterAI ×1,30, Polza ×1,16–1,45, Bothub ×0,88–0,90 (перепродажа flex-тарифа), ProxyAPI ×3,23.

Что сделано: у метки `aitunnel` в панели теперь подсказка «цена aitunnel за маршрут OpenAI, а не прайс OpenAI» — чтобы строку нельзя было прочитать как цену самого провайдера. Коммит `05be4e9`, опубликовано.
## 2026-10-28 · Прямой DeepSeek, история применённых тарифов и честная ошибка отсутствия маршрутов

Повторный сбой Лики вызван новым карантином двух AI Tunnel Flash-маршрутов после расхождения общей каталожной цены и суммы поставщика. Прямой DeepSeek был подключён, но не участвовал в priced-routing. Добавлен его адаптер: авторизованный список моделей + официальный прайс USD/M, раздельные вход/кеш/выход, UTC peak/off-peak и проверенный календарь праздников КНР 2026. Совместимость enable_thinking/reasoning_effort переведена в параметры DeepSeek. Срок котировки ограничен проверенным календарём; неизвестные схемы/годы не угадываются. Для расширения календаря предусмотрен CLOUD_DEEPSEEK_HOLIDAYS_FILE.

В ledger.extra.model_accounting.price_quote теперь сохраняется применённый снимок ставок, источника, курса, времени проверки и параметров маршрута. Общий provider-routing.json остаётся последним каталогом, не архивом. Стоимость из ответа поставщика и catalog_estimate различаются; прежние отсутствующие снимки не восстанавливаются задним числом. Пустая цепочка маршрутов возвращает 503/provider_routes_unavailable до начала потока, снимает резерв, не списывает за чат. Розничные цены, пороги тарифов и ключи не менялись.

Проверено 76 тестов, обычный и потоковый реальный ответ DeepSeek через изолированный ASGI-релей с базой в памяти, отдельный видимый ответ с синтетической картинкой и инструментом, сохранение снимков, отсутствие списания при пустой цепочке. Новых lint-замечаний нет. В рабочем API после публикации две котировки DeepSeek, остаток проверен, ошибки каталога нет, relay healthy. Резервная копия: /opt/nanomuse/cloud/backups/deepseek-routing-20261027T220850Z. Отчёт: nanoLika/artifacts/deepseek-routing-2026-10-27/RESULTS.md.

## 2026-10-28 · aitunnel — наша площадка, но цена у него не одна

Владелец уточнил вопрос: не «нет цены RouterAI», а «нет цены aitunnel» у gpt-6.1-terra — и «то есть мы его рассматриваем как всех остальных поставщиков?». Разобрались: aitunnel собирается и это наша шестая площадка, но у него внутри несколько площадок-исполнителей, и цена у каждой своя. Для gpt-6.1-terra в каталоге aitunnel четыре предложения: OpenAI flex 100/500, OpenAI 200/1000, Azure 240/1200, OpenAI fast+priority 400/2000 ₽ за 1M (вход/выход). Одна цифра «цена aitunnel» тут была бы враньём — разброс в четыре раза.

Раньше это выглядело как четыре чужие серые строки «OpenAI», «Azure» без единого слова aitunnel — отсюда и ощущение, что его нет. Сделано в `~/Dev/modelscope`:
- aitunnel считается нашим: строки зелёные, с меткой «подключён»;
- подпись aitunnel стоит первой в ячейке: `aitunnel OpenAI`, `aitunnel Azure`;
- новая галочка «свернуть aitunnel» (включена по умолчанию): одна строка с лучшей ценой и пометкой «+3 площадки»; снял галочку — видны все площадки внутри aitunnel с ценами и uptime;
- счётчик «у нас N поставщиков» считает площадки, а не строки: у gpt-6.1-terra шесть (Bothub, Polza.ai, OpenRouter, aitunnel, RouterAI, ProxyAPI);
- галочка появляется только там, где у aitunnel больше одной площадки.

Проверено: gpt-6.1-terra (6 площадок, aitunnel свёрнут в OpenAI +3), seedance-2.0 (4 площадки: Bothub 14,14 ₽/с, Polza от 12,82, RouterAI 15,83, aitunnel Seed без цены), aleph-2 (RouterAI 29,31 ₽/с, aitunnel Runway без цены).

Опубликовано (729 моделей, md5 `app.js` совпадает), коммит `75f8308`.

## 2026-10-27 · Проверка BotHub и двух подключений ProxyAPI

Диагностика без изменений сервера и без генераций. Переданный ключ BotHub проверен только в памяти: /v1/models отвечает 200 и возвращает 486 моделей, контроль без ключа и с неверным ключом — 401. /auth/me и персональный /pricing/models для API_COMPLETIONS тоже отвечают 200. Получен структурированный тариф PRO/BASIC для deepseek-v4-flash-0731: вход 20,408559 ₽, выход 40,817118 ₽, чтение кеша 4,081712 ₽ за 1M. В settings нет подключения BotHub, отсутствуют адаптеры маршрутизации/баланса; старый HTML-парсер BotHub сознательно отключён как неточный. Публичный OpenAPI предоставляет нормальный источник цен с единицами CAPS/RUB/USD и режимом провайдера. Секрет не сохранялся в исходники/аргументы/отчёт/настройки.

Два ProxyAPI — общий API и OpenAI API одного сервиса; константное сравнение подтвердило один и тот же ключ. Оба читают один /proxyapi/balance. Это один счёт с двумя маршрутами, остатки не складываются. Подробности без секретов: nanoLika/artifacts/bothub-check-2026-10-27/RESULTS.md.

## 2026-10-27 · AI Tunnel: выбор внутреннего исполнителя по цене

Устранён пропуск второго уровня маршрутизации: AI Tunnel теперь получает `provider.sort=price` и `require_parameters=true`, включая явно настроенный резерв и выключенный auto-routing. Сохраняется запрет известных 4-битных вариантов; неизвестная точность остаётся допустимой для закрытых моделей. Клиент не может подменить серверную политику; остальные поставщики не меняются. Это порядок «дешёвые сначала», а не гарантия цены общего каталога 10/32: фиксированный тариф требует конкретного endpoint, `only` и `allow_fallbacks=false`.

Проверки: 51 тест общего набора прошёл, включая потоковые ответы и однократное списание; одно устаревшее ожидание списка точностей перепроверено свежим запуском пяти итоговых policy-тестов — все прошли. Отдельно проверена сериализация итогового запроса в контейнере. Два крошечных запроса непосредственно к поставщику приняты: текстовый 0731 — 0,01 ₽; с синтетической картинкой и инструментом v4.1 — 0,02 ₽, в последнем лимит 16 токенов ушёл на рассуждение, завершённый видимый ответ не проверен. Клиентские аккаунты релей за диагностику не списывал. Новых lint-замечаний нет. Выложен один модуль с резервной копией compose/исходника/SQLite: /opt/nanomuse/cloud/backups/aitunnel-provider-policy-20261027T212216Z. Подробности: nanoLika/artifacts/aitunnel-provider-policy-2026-10-27/RESULTS.md.

## 2026-10-27 · В базе теперь шесть площадок: bothub, proxyapi и openrouter

Владелец спросил, где остальные провайдеры: «где openrouter, где bothub, AITUNNEL, ProxyAPI, где от них цены». Проверка показала: в базе собирались только polza, routerai и прямой DeepSeek. aitunnel собирался (1275 предложений), но в панели выглядел как «Azure», «Seed», «OpenInference» — под именами площадок внутри него, своего имени нигде не было. openrouter, bothub и proxyapi не собирались вовсе.

Что сделано в `~/Dev/modelscope`:
- **bothub.chat** — 387 моделей. API под ключом (401), но каталог `/models` отдаёт серверный HTML с ценами в рублях: вход/выход за 1M токенов, видео «За секунду», картинки «За изображение»/«За генерацию», речь «За 1K символов», расшифровка «Аудио» (за минуту). Тип берём из модальности карточки (Текст/Видео/Изображения/Аудио/Эмбеддинги/Музыка) — она точнее цены; вендора bothub в слаг не кладёт, поэтому для новых моделей угадываем по семейству (flux → black-forest-labs, kling → kling, suno → suno и т.д.), иначе все 78 новинок свалились бы в «unknown».
- **proxyapi.ru** — 443 модели со страницы `/models/browse`, цены за 1M токенов. API тоже под ключом (401), HTML открыт.
- **openrouter.ai** — 386 моделей. Сам сайт блокирует российские адреса: 403 «Access denied by security policy» на любой User-Agent, и с этого Mac, и с VPS, и из настоящего Chrome (проверено), публичные CORS-прокси не помогли. Взяли зеркало `models.dev/api.json`, раздел `openrouter`: те же id и те же доллары за 1M, пересчёт по курсу ЦБ — как уже делаем для прямого DeepSeek. Это единственное место в базе, где цена не рублёвая с площадки, а пересчёт; в README записано.

Интерфейс: у строк из aitunnel появилась серая метка `aitunnel` — теперь видно, что это его цены, а не пропавшая площадка. «Наши» теперь шесть: Polza, RouterAI, Bothub, ProxyAPI, OpenRouter, прямой DeepSeek. Счётчик в панели и колонка «Подключено» считают их все.

Итого 729 моделей и 3496 предложений (было 624 и 2288): текст 424, картинки 97, видео 61, речь 41, эмбеддинги 40, расшифровка 27, решения 21, переранжирование 9, музыка 8, классификация 1. Появилось 78 моделей, которых у нас не было ни в одном каталоге: линейка Flux (dev/schnell/pro/kontext/fill/depth/redux), Kling 2.1–3.0 и motion control, LTX 2.x, Vidu Q3, PixVerse V6, Suno V5/V6, ElevenLabs voice cloning.

Проверка на deepseek-v4.1-flash-1017 — шесть цен в одной панели: polza 11,54/17,13 · routerai 11,63/17,27 · deepseek 12,08/48,32 · openrouter 12,08/48,32 · bothub 16,17/64,68 · proxyapi 26,07/100,25 (вход/выход за 1M). Плюс 36 чужих площадок из aitunnel, у каждой метка.

Заодно: вход на базу моделей и на страницу цен сделан по домашнему IP владельца без пароля (Caddy `remote_ip`), остальным по-прежнему basic_auth. Сам адрес теперь живёт только в `.env` на сервере, не в репозитории; IP получен временным эндпоинтом `/my-ip`, который потом убран.

Опубликовано на `https://cloud.nanolika.ru/phzhk6tnr6d0e2ac7j3ze8eose/` (729 моделей, 97 картинок, 61 видео, md5 `app.js` совпадает), коммит `4b699fe`.

## 2026-10-27 · Диагностика PI_AI_ERROR: нет доступного маршрута модели

Проверка без изменений runtime и без платных запросов. В relay два сбоя `No configured model provider answered (no routes)`: AI Tunnel v4.1 Flash и 1017 попали в ценовой карантин после фактических 31,79/23,72 ₽ вместо 15,89/15,81 ₽; остальные каталожные поставщики ниже рабочего порога остатка 90 ₽ / $1. Публичный API AI Tunnel продолжает отдавать 30/120 ₽ за 1M, хотя фактическая стоимость соответствует 60/240 и 45/180. На синтетическом запросе в живом маршрутизаторе подтверждено: текстовый запрос имеет запасные text-only маршруты, добавление изображения при наличии tools оставляет ноль. Тело сбойного запроса не перехватывалось. Карантин и финансовые правила не менялись. Отчёт: nanoLika/artifacts/provider-no-routes-2026-10-27/RESULTS.md.

## 2026-10-27 · Telegram WebApp: вход в уже привязанный аккаунт

Исправлен переход из marketing /webapp/: раньше он только проверял привязку и открывал чат без браузерной сессии. Добавлены same-origin вход `/v1/auth/telegram-webapp` и `/web/telegram.html`: строгая проверка URL-декодированной подписи Telegram, свежесть запуска 5 минут, поиск существующего аккаунта без регистрации/слияния, отзываемая сессия на 30 дней. Данные запуска передаются фрагментом и сразу убираются из адреса. Исправлена запись в read-only SDK initDataUnsafe; чат разрешён внутри Telegram Web, внешний SDK разрешён только странице входа.

Проверено: 24 backend-теста, 5 браузерных сценариев; опубликованная страница внутри тестового родителя Telegram Web с настоящими заголовками и подставным ответом авторизации; совпадение публичных файлов, отказ 401 на неверную подпись и healthy relay. Реальный запуск из бота требует повторного открытия у владельца. Изменения выложены с резервными копиями compose/исходников/SQLite и двух файлов сайта. Подробности: nanoLika/artifacts/telegram-webapp-session-2026-10-27/RESULTS.md.

## 2026-10-27 · Seedance есть у polza и routerai: видео и картинки не собирались, двойники слить

Владелец спросил: «неужели seedance нет ни у одного из наших поставщиков?» — и приложил скрин с seedance без цен и с дублем DeepSeek. Оба наблюдения оказались правдой, и оба — про сборщик, а не про поставщиков.

**Seedance есть.** У polza пять моделей (`bytedance/seedance-2.5`, `-2`, `-2-fast`, `-2-mini`, `-1.5-pro`), у routerai шесть с ценой в `pricing.seconds`. В базу они не попадали, потому что сборщики отбрасывали всё, что priced не за токены: у polza стоял фильтр `if not pricing.get("prompt_per_million"): continue`, у routerai — `if not pricing.get("prompt")`. Это вырезало 70 моделей polza и 78 routerai: все видео и почти все картинки наших поставщиков. В базе они были видны только через aitunnel — без цен, отсюда «прочерки» на скриншоте.

**Дубль DeepSeek — алиас routerai.** routerai отдаёт и `deepseek/deepseek-v4.1-flash-1017` (38 предложений), и `~deepseek/deepseek-v4-flash-latest` с тем же именем «DeepSeek: DeepSeek V4.1 Flash 1017» и теми же ценами, но всего с 3 предложениями. Владелец кликнул вторую строку и решил, что нового ничего нет. `~` в id — пометка «устаревший алиас».

Сделано в `~/Dev/modelscope`:
- у цены появилась единица: `offers.price_unit` (`per_1m`, `per_second`, `per_video`, `per_image`, `per_minute`, `per_1m_chars`, `per_request`) и `offers.price_from` — цена «от» у ступенчатых тарифов. Цена не за токены лежит в `request_cost` и печатается с суффиксом: `2,57 ₽/с`, `от 5,25 ₽/ролик`, `4,19 ₽/карт.`;
- сборщики принимают `video_per_second`, `per_request`, `stt_per_minute`, `tts_per_million_characters`, `tiers`, `seconds`, `image_output`, `request`. У polza из ступеней берётся самая дешёвая с пометкой «от» — иначе базовая ступень seedance 1.5 pro (135 ₽ за ролик) выдавалась бы за обычную цену, а 480p/4s без звука стоит 5,25;
- `routerai.image_output` двоякое: у моделей с текстовым входом это цена за токен картинки (0,0018 ₽ → 1 788 ₽ за 1M), у recraft и riverflow — за изображение (8,37 ₽). Различаем по величине, порог 0,05 ₽;
- ноль в `prompt`/`completion` у routerai теперь «не применимо», а не «бесплатно». Иначе у seedance цена за секунду перебивалась нулями и модель выглядела бесплатной;
- `dedupe.py` сливает двойников по нормализованному имени, 23 пары в таблице `model_aliases`. Слияние вызывается из `refresh.py`, дальше сбор сразу кладёт предложения к основной модели;
- слаги новых моделей — от полного пути: `qwen/image` → `qwen-image` (было `image`), `kling/v3` → `kling-v3`. Мусорные слаги (`image`, `v3`, `v2-6`, `veo3`, `2-5`, `2-6`) удалены и пересозданы;
- в интерфейсе: колонка «Вход ₽» показывает `от 12,82 ₽/с`, `от 5,25 ₽/ролик`, `4,19 ₽/карт.`; панель для видео сравнивает по цене за секунду и пишет «✓ цена» вместо «✓ вход»; в карточке для видео и картинок заголовок «самая низкая цена».

Сейчас в базе 624 модели и 2288 предложений (текст 388, картинки 65, видео 40, эмбеддинги 39, речь 30, расшифровка 27, решения 21, переранжирование 8, музыка 5, классификация 1). `deepseek-v4-flash-1017` — 40 предложений от 33 поставщиков, 4 наших. Seedance: `seedance-2.0` — polza от 12,82 ₽/с, routerai 15,82 ₽/с; `-fast` 10,46 / 9,49; `-mini` от 2,56 / 7,91; `2.5` 18,9 / 24,18; `1.5-pro` от 5,25 ₽/ролик / 5,42 ₽/с.

Проверено в браузере: чип «видео» → 40 моделей с ценами `₽/с` и `₽/ролик`, чип «картинки» → 65, абсурдных `0,0015 ₽/карт.` не осталось (это была цена за токен картинки, теперь `₽/1M`), панель `seedance-2.0` — 3 предложения: Polza.ai от 12,82 ₽/с, RouterAI 15,82 ₽/с, Seed без цены. Консоль без ошибок.

Опубликовано на `https://cloud.nanolika.ru/phzhk6tnr6d0e2ac7j3ze8eose/` (200, `Cache-Control: no-cache`, md5 `app.js` совпадает), коммит `731812f`. Владельцу: обновить страницу с Cmd+Shift+R — старую копию держал кеш браузера.

## 2026-10-27 · «Все модели текстовые»: починен фильтр типа, типы кнопками, сортировка по числу предложений

Владелец открыл базу и увидел только наших поставщиков и только текстовые модели. Проверка: на закрытом адресе лежит правильная сборка — 38 предложений по deepseek-v4.1-flash-1017 (2 наших), 41 модель «картинки», 30 «видео». Причина впечатления — в порядке выдачи: таблица сортировалась по алфавиту, а наверху как раз мелкие модели (Aiesa, Aion, aleph-2), у которых есть только polza/routerai и тип «текст». Плюс фильтр по типу был сломан: сравнивал слаг («image») с подписью («картинки») и не находил ничего.

Сделано: по умолчанию список сортируется по числу предложений — первыми идут glm-5.3 (50), deepseek-v4.1-flash (42) и т.д., где чужие площадки видны сразу. Типы моделей — кнопками с числом (текст 406, картинки 41, эмбеддинги 39, видео 30, речь 30, расшифровка 24, решения 21, переранжирование 8, классификация 1), метки типов в таблице покрашены. Фильтр по типу теперь сравнивает слаг, а не подпись. В панели строка «у нас N поставщиков» считает поставщиков, а не варианты (у gemini-3.1-flash-image было «3 поставщика» при двух: polza с двумя вариантами), и если других площадок у модели нет — так и пишет.

Заодно: в Caddy для /srv/models добавлен `Cache-Control: no-cache`. Раньше заголовков кеша не было, браузер мог держать старую копию страницы и показывать вчерашние данные.

Отдельно про видео: у моделей вроде flux-3-video и aleph-2 в каталоге aitunnel цен нет вообще (только задержки p50 и политики) — в таблице у них прочерки, это не баг разметки. Цены на картинки есть там, где модель продаёт polza/routerai (gemini-3.1-flash-image: RouterAI 52,32 / 313,91).

Проверено в браузере: чип «картинки» → 41 модель, чип «видео» → 30, панель gemini-3.1-flash-image (6 предложений, 3 подключённых, 3 Google без цен), мобильная ширина 390 px с прокруткой чипов.

## 2026-10-27 · Все поставщики в панели, пометка «подключён» и перетаскиваемая высота

Просили: у deepseek-v4.1-flash-1017 в таблице видно только двух наших поставщиков, а данных по ней 32 — показать всех, к кому можно подключиться, и визуально отделить подключённых от неподключённых; плюс сделать ресайз границы панели.

Сделано в `~/Dev/modelscope`: панель и раньше показывала все 38 предложений, но без разделения. Теперь подключённые строки — зелёная полоса и метка «подключён», остальные — серая полоса и приглушённое имя. Если у чужой площадки вход ниже нашего лучшего, на строке жёлтая метка «ниже вход» с оговоркой в подсказке, а под названием модели — «вход ниже нашего: OpenInference 4,4 ₽ (выход 242 ₽) — не подключён». Метка намеренно про вход: у дешёвых по входу выход бывает в разы дороже, и «дешевле нашего» без уточнения вводило в заблуждение (первая версия так и делала — поймал на этом же примере).

Счётчик в шапке: «38 предложений · 32 поставщика», на переключателе «только подключённые» — сколько их (2). Переключатель прячет чужие площадки. Колонка в таблице стала «Подключено» — «✓ 2 / 32»: подключённые из всех, по кому есть данные. Фильтры переименованы в «только подключённые» и «✓ 2+ подключённых», на странице поставщиков — колонка «Подключение» (3 из 89 «✓ подключён», остальные «не подключён»). Карточка модели получила те же метки.

Ресайз: за верхнюю границу панели (мышь, палец, стрелки при фокусе), минимум 120 px, максимум — окно минус 120 px, высота запоминается в localStorage, двойной щелчок возвращает исходную (26% экрана, на телефоне 42%). Внутренние отступы страницы пересчитываются через переменную `--panel-h`.

Находка, которая отвечает на исходный вопрос «у кого ещё дешевле»: по смешанной цене `(k·вход+выход)/(k+1)` при 1:1 и 3:1 ни одна чужая площадка не дешевле нашего лучшего ни в одной из 522 моделей; при 10:1 таких случаев один — kimi-k3 у relace (219,52 против 235,44 ₽/1M, на 7%). То есть список «к кому подключиться» интересен не ценой, а скорее доступностью модели, uptime и политиками.

Ловушки: подпись варианта в select («1:3 (длинные ответы)») распирала контрол до 183 px и обрезала счётчик — оставил только числа; `span { display: none }` в мобильной шапке убивал подпись переключателя и пометку «вход ниже нашего» — прятать надо адресно; на телефоне название модели уехало в ноль, поэтому шапка панели там в две строки.

Проверено в браузере: 38 строк, 2 зелёных, метка на OpenInference, «только подключённые» → 2 строки, модель без наших поставщиков показывает пояснение, перетаскивание 218 → 388 px с запоминанием, сброс двойным щелчком, ширина 390 px. Опубликовано на закрытом адресе.

## 2026-10-27 · Однозначные состояния Telegram/MAX/компьютера и разрыв связи

Просили: убрать противоречие «Подключить»/«подключено», показать доступные сведения о привязанном Telegram и компьютере, оставить только действие разрыва связи в подключённом состоянии.

Сделано: окно всегда заново проверяет сервер; подключённое и неподключённое состояния имеют отдельные заголовки/содержимое/действия. Показаны сохранённые сведения привязки мессенджера и устройства, отличены офлайн и отсутствие привязки. На кнопках кабинета виден статус. Исправлен адаптер API, который превращал DELETE в POST. Для компьютера добавлен авторизованный DELETE /v1/link/devices/{id}; закрытая связь devices.key_hash записывается при подключении и позволяет отозвать именно вход компьютера, закрыть его сокет и убрать запись, не разлогинивая браузер. Чужие устройства, общие сессии и старые офлайн-привязки без известной сессии не отключаются с ложным успехом. Поле key_hash не экспортируется списками устройств.

Проверено: 20 тестов привязок/отключения и ещё 2 интеграционные проверки HTTP DELETE и обычной связи двух устройств, всего 22. Браузерные сценарии на опубликованном JS с тестовыми ответами: Telegram, компьютер онлайн/офлайн, метаданные, DELETE, переход QR в подключённое состояние и отказ основному чату. JS/Python синтаксис проверен; Ruff без новых замечаний относительно исходных файлов. После выкладки релей healthy, миграция поля и новый метод присутствуют. Реальные привязки клиента, сессии и денежные остатки вручную не изменялись.

Копия: /opt/nanomuse/cloud/backups/connection-state-20261027T185119Z/ содержит код, compose и согласованную закрытую SQLite-копию. Overlay: /opt/nanomuse/cloud/overrides/connection-state-20261027/nanomuse_cloud/. Протокол дописан в docs/hub.md, тесты в cloud/tests/test_device_disconnect.py; подробности в nanoLika/artifacts/connection-state-2026-10-27/RESULTS.md.

Ловушки: старый DELETE /v1/devices/{id} лишь забывает офлайн-запись и не подходит для отзыва доступа; нельзя сопоставлять вход компьютера по названию. Добавление хеша в devices_for раскрыло бы его через админский экспорт, поэтому связь читается отдельно только внутри операции отключения. Старому офлайн-устройству без записанного входа потребуется один раз открыть приложение.

## 2026-10-27 · Галочки выбора: где у нас два поставщика и кто из них дешевле

Просили: отметить галочками модели, где у нас есть два поставщика (не в мире, а у нас), и среди этих поставщиков отметить тех, кто дешевле всех по входу и отдельно по выходу; если это один и тот же — тем лучше. Открытый вопрос владельца: как выбирать, когда вход дешевле у одного, а выход у другого.

Сделано: в таблице моделей колонка «У нас» — число наших поставщиков (polza, routerai, deepseek-direct), `✓ 2` и больше значит «есть из чего выбрать». Таких моделей 310 из 600; фильтр `✓ 2+ наших` оставляет только их. В панели предложений галочки стоят на строках наших поставщиков: `✓ вход`, `✓ выход`, `✓ вход и выход`. Строка под названием модели говорит то же словами.

Для расхождения добавлен переключатель соотношения `при 1:3 / 1:1 / 3:1 / 10:1` и цена миллиона смешанных токенов `(k·вход + выход)/(k+1)`: видно, с какого соотношения выбор переворачивается. На странице поставщиков колонка «У нас» помечает `✓ наш`.

Главная находка: из 286 моделей, где у нас два поставщика с ценами, вход и выход дешевле у одного и того же в 285 случаях — polza и routerai перепродают одни и те же площадки с разной наценкой, поэтому вопрос «вход у одного, выход у другого» на практике почти не возникает. Единственное расхождение — `glm-4-6`: RouterAI 44,99/183,12 против Polza 45,69/182,76; Polza выигрывает только при очень длинных ответах (соотношение ниже 1:2).

Проверено в браузере: метки на gpt-5-nano (Polza дешевле и по входу, и по выходу), расхождение на glm-4-6 с переключением соотношения, фильтр 310 из 600, мобильная ширина 390 px. Опубликовано на том же закрытом адресе.

Ловушка: `.panel-ours` с nowrap уезжал за край на телефоне — проверять мобильную версию только через iframe 390 px (headless-Chrome игнорирует --window-size).

## 2026-10-27 · Панель предложений: база моделей связана с базой поставщиков

Просили: связать модели и поставщиков — при нажатии на модель показывать внизу экрана (четверть) все предложения по ней, чтобы посмотреть, как это выглядит, пока непонятно, как отбирать.

Сделано в `~/Dev/modelscope`: главная страница стала двухуровневой. Сверху таблица моделей с поиском и фильтрами (вендор, поставщик, тип, «только у нас»), снизу фиксированная панель 26vh со всеми предложениями выбранной модели: поставщик, вариант, вход/выход/кэш, контекст, максимум вывода, uptime, p50, запросы за 30 минут, политики. Самая дешёвая по входу строка подсвечена, колонки панели сортируются, ↗ открывает карточку модели, имя поставщика ведёт на его строку в базе поставщиков. В обратную сторону: на странице поставщиков число моделей стало ссылкой на главную с включённым фильтром. Клавиши ↑ ↓ ходят по списку, Esc закрывает панель, состояние живёт в адресе (`index.html?provider=polza&model=gpt-5-nano`) — ссылкой можно делиться.

Данные панели лежат в самой странице: 2150 предложений, 497 КБ, по сети ~60 КБ после gzip. Поэтому поиск и панель работают мгновенно и без сервера.

Проверено в браузере: щелчок по строке, 6 предложений у gpt-5-nano (дешевле всего Polza 2,92 / 23,33 против 5 / 40 у aitunnel), переход по ссылке из базы поставщиков (343 модели у polza), клавиатура, Esc, мобильная ширина 390 px без горизонтального разползания страницы. Опубликовано на том же закрытом адресе.

Ловушки: число в колонке «Предл.» считало поставщиков, а не предложения — у glm-5.3 их 39 против 50; теперь в колонке предложения, в подсказке — поставщики. Под панелью строка пряталась — scrollIntoView не знает про фиксированную панель и липкую шапку, прокрутка считается вручную. В headless-Chrome ширина окна не задаётся (`--window-size` игнорируется, layout 500 px), мобильную вёрстку проверял через iframe 390 px.

Открыто: как отбирать модели в витрину — владелец ещё не решил, `user_catalog` пуст.

## 2026-10-27 · Компактные таблицы моделей и расхода в кабинете

Просили: привести кабинет к плотности и читаемости приложенных примеров, заменить крупные карточки моделей и истории таблицами.

Сделано: добавлен account/compact.css с обычной системной типографикой и небольшими контролами. Модели в /app/models/ и окне выбора чата представлены строками таблицы; подробности раскрываются. История /app/usage/ теперь содержит таблицы по моделям и обращениям. Навигация кабинета получила ссылку на модели. Сохранены настройки, фильтры, избранное, сохранение, курсорная подгрузка, формы и проверки аккаунта. Учитываются только существующие данные; длительность и статус обращения не выдумывались. Финансовые расчёты и API не менялись.

Проверено: браузерные сценарии поиска/выбора/сохранения/подробностей/избранного, обновление и подгрузка истории, кабинет на 390/768/1440 с тестовыми ответами API; JS без ошибок и без горизонтального переполнения страницы. 10 опубликованных файлов сверены по SHA-256. Скриншоты и подробный отчёт в проекте nanoLika: artifacts/cabinet-compact-2026-10-27/RESULTS.md. Локальные отличавшиеся исходники обновлены по затронутым функциям, посторонние изменения сохранены.

Копия: /opt/nanomuse/cloud/backups/cabinet-compact-20261027T181944Z/. Overlay: /opt/nanomuse/cloud/overrides/cabinet-compact-20261027/nanomuse_cloud/. Релей пересоздан с постоянными mounts, /healthz 200.

Ловушка: новый compact.css нельзя сразу смонтировать дочерним файлом в read-only web_client, если файл ещё отсутствует в исходном каталоге родительского mount. Первый запуск был отклонён Docker; добавление файла в родительский источник устранило ошибку. Публикация после этого продолжена. Также фактические файлы overlay расходились с локальными, поэтому базой выкладки послужил снимок контейнера.

## 2026-10-27 · База моделей поставщиков и закрытый адрес на VPS

Просили: базу всех моделей наших поставщиков и каталога aitunnel с ежедневной проверкой новинок и очень подробной страницей каждой модели, а опубликовать — закрытым путём как страницу цен `cfp64…`.

Сделано: проект `~/Dev/modelscope`. Три слоя в SQLite (вендоры+модели, поставщики+предложения, витрина `user_catalog`) плюс история прогонов и изменений. Источники: aitunnel.ru (357 моделей, 1214 предложений — разбор RSC-payload страницы, не вёрстки), polza.ai (343), routerai.ru (500), прямой DeepSeek (4). Итого 600 моделей, 87 вендоров, 89 поставщиков, 2150 предложений, 886 отзывов, 1237 записей истории. Страницы статические: индекс с поиском и фильтрами, карточка модели, поставщики, изменения, витрина — 604 файла, 5,6 МБ.

Публикация: `https://cloud.nanolika.ru/phzhk6tnr6d0e2ac7j3ze8eose/`, логин `operator`, пароль в `nanoLika/docs/access.txt`. В `cloud/Caddyfile` добавлен блок «База моделей» (`root /srv/models`), в compose — volume `./models:/srv/models:ro`; файлы в `/opt/nanomuse/cloud/models`. Без пароля 401, с паролем 200, страница цен `cfp64…` не тронута.

Ежедневно в 06:30 через launchd `local.modelscope.refresh` → `daily.sh` (сбор, страницы, заливка). Проверено принудительным запуском: python 3.11.7, 91 с, 0 ошибок, страницы на сервере обновились.

Ловушки: в zsh `status` — read-only переменная, код возврата не пробрасывался. Под launchd `python3` — 3.9 из CommandLineTools, а у python.org-сборки 3.11 без `Install Certificates.command` нет корневых сертификатов: в терминале это скрывает `SSL_CERT_FILE` из профиля, под launchd падало на `CERTIFICATE_VERIFY_FAILED`. Ещё: у aitunnel слаг вида `open-inference/fp4` — это поставщик + вариант, а не 226 поставщиков; прямой DeepSeek разведён с перепродажей aitunnel как `deepseek-direct`.

Не вышло: openrouter.ai отдаёт 403 на любой User-Agent (блокировка по IP), его каталог виден через polza и routerai; bothub.chat не публикует цены в разметке; vsegpt считает «₽ за 1000 символов» — другая единица; neuroapi не отвечает. LiteLLM (4324 модели с ценами вендоров) отложен как справочник для обогащения.

Открыто: витрина `user_catalog` пока пустая; пуш `modelscope` на GitHub не сделан (локально три коммита).

## 2026-10-27 · Пустой кабинет и заметный вход в браузер

Просили: заметную кнопку веб-чата рядом с Telegram/MAX на нескольких страницах и исправление настроек зарегистрированного аккаунта, где оставались только заголовок и «Обновить».

Причина: account.js был новее HTML в действующем overlay; обработчик create-api-key падал на null до запуска load(). renderMe также ожидал отсутствующий paid-balance-note. Исправлены проверки необязательных элементов, пропуск списка ключей без соответствующей панели и поддержка существующей nm.key при отсутствии сессии веб-чата. Владелец проверяется через /v1/me до показа данных. Версия подключения скрипта обновлена.

Проверено после выкладки: четыре браузерных сценария с опубликованным JS и тестовыми ответами API: web-сессия, legacy-сессия, отсутствие сессии, другой владелец. В первых двух работают данные, настройки и обновление; в остальных личные данные скрыты. Чистый браузер больше не получает JS-исключение и видит ссылку входа. Личный аккаунт владельца и операции записи не использовались.

Также опубликованы кнопки «В браузере» в 13 группах каналов на 10 страницах сайта. Проверены 390/768/1440 и SHA-256 публичных файлов. Подробности: nanoLika/artifacts/browser-entry-account-2026-10-27/RESULTS.md.

Копия кабинета: /opt/nanomuse/cloud/backups/cabinet-startup-20261027T174040Z/. Исправлены основной источник и действующий overlay, запись сохраняет inode подключённых файлов. Перезапуск не требовался. Ловушка: локальный index.html уже содержал новые элементы, а серверный overlay оставался старым; проверять только локальную разметку недостаточно.

## 2026-10-27 · Выкладка порогового тарифа и названия тарифа в шапке сайта

Просили: показать активный тариф рядом с почтой на сайте и перенести порог оплаченного остатка в денежную логику релея без объяснения порогов на витрине.

Сделано: опубликованы существующие локальные browser_sessions.py и live_billing.py. db.py/service.py уже совпадали с сервером. Для live_billing добавлены постоянные mounts в обоих путях импорта. Связь устройств включена и для базовой Лики в PLANS. Другие изменения api.py не переносились. Сайт получил тариф в шапке, счётчик и узкие исправления условий offer/agent. Подробный отчёт: nanoLika/artifacts/site-sync-2026-10-27/RESULTS.md.

Проверено: 14 тестов live_billing/browser_sessions прошли. Relay healthy после пересоздания, /healthz 200, работающий код содержит paid_balance_uy/account_plan, все три тарифа connect=true. Сайт проверен на 390/768/1440 с тестовым ответом аккаунта. Реальные платежи и ручные изменения клиентских остатков не выполнялись.

Копия: /opt/nanomuse/cloud/backups/paid-balance-20261027T172348Z/ (модули, compose, согласованная SQLite-копия). Боевая оплата не включалась; платёж провайдера остаётся отдельной проверкой перед запуском.

Ловушка: новая база и service сочетались со старым подтверждением live-платежа и site-session. Необходимо проверять работающие модули, оба пути импорта и пересоздавать контейнер после изменения mounts.

## 2026-10-27 — Проверка исправления маршрутизации инструментов на сервере

**Выкладка:** коммит `ff158590` выложен в overlay `/opt/nanomuse/cloud/overrides/auto-routing-20261025/nanomuse_cloud/provider_routing.py`, relay перезапущен и стал healthy. После перезапуска выполнено штатное обновление каталогов и балансов. Живой API теперь помечает `tools` и `tool_choice` у AITUNNEL DeepSeek V4 Flash 0731, V4 Flash, V4.1 Flash и 1017; 0731 остаётся выбранным маршрутом. Ошибок обновления каталогов нет. Тестовый платный запрос к модели не отправлялся.

## 2026-10-27 — Ошибка «The model provider stopped answering» на агентских запросах

**Причина:** журнал релея показал `No configured model provider answered (no routes)`, то есть upstream-запрос вообще не был отправлен. Запросы с инструментами исключали AITUNNEL: его каталог моделей не возвращает `supported_parameters`, а маршрутизатор воспринимал это как отсутствие tool calling. Альтернативы с объявленной поддержкой инструментов были ниже порога баланса.

**Исправлено:** для четырёх DeepSeek Flash моделей AITUNNEL, у которых страница модели прямо подтверждает function calling, добавлены `tools` и `tool_choice` как известные параметры. Это не включается глобально для всех моделей AITUNNEL. Добавлен сухой тест, который воспроизводит ситуацию с балансом OpenRouter ниже $1 и подтверждает выбор AITUNNEL V4 Flash 0731 для запроса с инструментом.

**Проверено:** 59 тестов маршрутизации и страницы цен, Ruff и `git diff --check` прошли. Платный запрос не выполнялся. После выкладки нужно проверить health и убедиться, что каталог маршрутизации обновился; старый кэш поставщика может хранить пустой список параметров до следующего обновления котировок.

## 2026-10-27 — Проверка выкладки Flash-котировок и балансов

**Проверено после выкладки коммита `3ea77b3e`:** контейнер `cloud-relay-1` healthy. Авторизованный API показывает для DeepSeek Flash все OpenRouter предложения: `deepseek-v4-flash`, `deepseek-v4-flash-0731`, `deepseek-v4.1-flash`, `deepseek-v4.1-flash-1017`, `deepseek-v4-flash-vision-exp` и динамические aliases. Для текущего базиса запроса дешевле всего OpenRouter `deepseek-v4-flash` за 4,99 / 9,98 ₽ за миллион входных / выходных токенов; он помечен неактивным из-за остатка ниже порога. Сейчас раздаёт AITUNNEL `deepseek-v4-flash-0731` за 10 / 32 ₽.

**Остатки на момент проверки:** OpenRouter $0,0068 при минимуме $1; DeepSeek $0; Polza 29,61 ₽, ProxyAPI 40,82 ₽ на общий счёт, RouterAI 1,25 ₽ при пороге 90 ₽; AITUNNEL 499,34 ₽ и выше порога. Точные остатки и причины теперь показываются в раскрытии каждой котировки и в сводной таблице. Внешний старый адрес `/app/prices/` отвечает 404 по настройке Caddy; страница доступна по защищённому служебному адресу `https://cloud.nanolika.ru/cfp64ns5jgh4er8uxhis3t5t/`.

## 2026-10-27 — Котировки Flash и остатки поставщиков в админке

**Просили:** показывать все варианты DeepSeek Flash от OpenRouter, не скрывать их из-за низкого баланса, и вывести цифры, по которым понятно, кого пополнять.

**Причина:** админская таблица брала котировки через список активных маршрутов. Провайдер ниже рабочего порога отбрасывался до построения таблицы, поэтому поколения OpenRouter пропадали. Сама таблица остатков была отдельно, а небольшой положительный остаток ниже порога всё равно назывался «Доступен».

**Сделано:** режим инвентаризации маршрутов возвращает котировки даже при низком балансе и помечает причину недоступности; обычный runtime-маршрут остаётся отфильтрованным. OpenRouter V4 Flash, 0731, 1017, Vision и динамические Flash aliases объединены в одну внутреннюю семью. В строках котировок показываются точный остаток поставщика, рублёвая оценка для USD и порог маршрута. В сводке остатков низкий баланс и ошибки проверки выделяются отдельно; неизвестный баланс не показывается как ноль.

**Проверено:** 58 тестов маршрутизации и прайс-админки, Ruff, `node --check` для `prices.js` и `git diff --check` прошли. Живой ответ и состояние контейнера после выкладки ещё не проверялись.

**Осталось:** выложить файлы в overlay релея, проверить авторизованный `/v1/admin/prices` и страницу `/app/prices/` на сервере. Не выполнять платные запросы к моделям.

## 2026-10-27 — Маршрутизация DeepSeek Flash по поколениям

**Просили:** использовать более дешёвые поколения Flash при равной совместимости и не платить 30/120 ₽ за 1017, когда AITUNNEL предлагает 0731 за 10/32 ₽.

**Причина:** в интерфейсе поколения уже были собраны в одну семью, но `ProviderRouter.identity()` связывал только плавающий Flash и V4.1/1017. Поэтому котировки V4 Flash 0731 и базового V4 Flash показывались справочно, а маршрутизатор их исключал.

**Сделано:** V4 Flash, V4 Flash 0731, V4.1 Flash и 1017 теперь относятся к одной внутренней семье маршрутизации. Отбор по поставщику сохраняет обычные проверки модальностей, инструментов, контекста, баланса и свежей цены. Для текста выбирается самый дешёвый совместимый маршрут; при отказе релей пробует следующие цены. Запрос с изображением останется на предложении, которое заявляет поддержку image.

**Проверено:** 56 тестов маршрутизации и страницы цен прошли; Ruff и `git diff --check` прошли. После выкладки проверен авторизованный ответ `/v1/admin/prices`: активный маршрут — AITUNNEL `deepseek-v4-flash-0731`, 10/32 ₽ за миллион; `deepseek-v4-flash` — 13,58/33,6 ₽, 1017 и плавающий V4.1 Flash — 30/120 ₽. 0731 отмечен доступным и выбранным. Контейнер релея healthy, страница прайса, JS и CSS отвечают 200.

**Выкладка:** коммит `b43bc295` (`fix(relay): route DeepSeek Flash to cheaper generations`), модуль выложен в `/opt/nanomuse/cloud/overrides/auto-routing-20261025/nanomuse_cloud/provider_routing.py`; контейнер перезапущен.

**Осталось:** отдельный платный запрос не отправлялся. Фактический выбор маршрута подтверждён по текущей котировке и серверному API, а не платным вызовом.

## 2026-10-27 — Витрина закупок по семействам моделей

**Просили:** одна клиентская строка на модель-семейство с розничной ценой, под ней поколения и предложения поставщиков; отмечать только фактический выбранный маршрут и скрывать всё семейство одной галочкой.

**Сделано:**
* Админская таблица сворачивает варианты в одну строку, для DeepSeek Flash и Pro использует короткие названия. В раскрытии видны поколения, provider ID, закупочные цены, текущий маршрут, резервы и предложения, которые не участвуют в маршрутизации.
* Розничные цены остаются на строке семейства. Отключение видимости выключает клиентские варианты семейства; включение оставляет видимым только родительский продукт.
* Сумма закупки показывается как текущая только при наличии выбранного маршрута с котировкой. Статический upstream из конфига больше не выдаётся за реально обслуживающего поставщика. Добавлены ссылки на кабинеты ProxyAPI, Polza и RouterAI.
* Отключены дубли вариантов DeepSeek Flash/Pro в клиентском меню, родительские позиции оставлены.

**Проверено:** целевые тесты прайса 12/12, Ruff, `node --check` для `prices.js` и `git diff --check`. Полный набор cloud: 538 passed, 6 failed в сценариях приглашений/лимитов, строк консоли, websocket-тарифа, скидки на изображения и возвратов; эти файлы не входили в изменение. В проде после выкладки `/app/prices/`, JS и CSS отвечают 200, неавторизованный прайс API отвечает 401, модуль импортируется и контейнер healthy.

**Выкладка:** закоммичено `ac4b697b` (`feat(cloud): group model offers by customer-facing family`), файлы выложены в overlay-монты релея и контейнер перезапущен. Резервная копия: `/opt/nanomuse/cloud/backups/prices-family-20261027-ac4b697b/`.

**Осталось:** проверка данных API внутри авторизованной сессии пользователя; платные запросы и переключение провайдера не тестировались.

## 2026-10-27 — Проверка релея и цены моделей для клиента

**Просили:** после перезапуска проверить ошибки «The model provider stopped answering», завершить разбор каталога моделей и сделать понятными фактического поставщика и цену, которую платит клиент.

**Сделано в исходниках:**
* В админском списке цен показаны текущий поставщик и точный upstream ID, альтернативные предложения, закупочная цена и цены для каждого плана. Проверка «ниже закупки» учитывает реальные скидки тарифов.
* В выборе модели настольного приложения добавлены понятное название модели, пояснение об оплате из баланса nanoLika Cloud и сообщение, когда каталог недоступен.
* Релей при ошибке каждого провайдера теперь пишет безопасную диагностику с именем провайдера, upstream-моделью и типом ошибки; секреты и тело запроса в журнал не попадают.
* Убрана ошибка TypeScript в подписи завершения хода. Проверка типов теперь проходит.

**Проверено:** 54 теста релея и прайса прошли, Ruff и проверка diff прошли. Настольная сборка и проверка типов прошли. Из 409 настольных тестов 408 прошли; один тест синхронизации боковых чатов падает с ожиданием 1, фактически 2 (`tests/sync.test.mjs`, C9). Изменения не затрагивали синхронизацию. На работающем сервере `/healthz` отвечает `ok`, за последние 30 минут в журнале не найдено ошибок upstream. Сбой ответа модели сейчас не воспроизведён, реальный запрос к платной модели для диагностики не отправлялся.

**Не сделано:** эти изменения ещё не выложены на сервер и не вошли в установщик; текущая диагностика ошибок появится в серверных логах после выкладки.

## 2026-10-27 — Автовыбор самого дешёвого поставщика DeepSeek Flash

**Просили:** сравнивать поставщиков по семейству Flash, не выделять ревизии в отдельную логику, при недостатке баланса незаметно переключать запрос на следующего поставщика; после пополнения снова выбирать самого дешёвого.

**Сделано:**
* Удалено специальное исключение endpoint-ревизии из маршрутизации, прайс-листа и каталога моделей. Варианты Flash теперь сравниваются как одна модель, а исходный ID используется только при обращении к выбранному провайдеру.
* Сортировка маршрутов идёт по полной оценке запроса (вход и запрошенный максимум выхода). Убрано ограничение «резерв не дороже 3×»: все совместимые предложения остаются цепочкой автоматического переключения.
* Добавлен резерв баланса: менее или ровно $1 для долларовых и 90 ₽ для рублёвых поставщиков. При HTTP 402 запрос повторяется у следующего поставщика до выдачи ответа; провайдер временно исключается. Успешная проверка после пополнения выше порога снимает блок и возвращает его в ценовое сравнение. Проверка балансов работает при старте и затем каждые 120 секунд.
* В админском сводном списке устаревшие отметки исключения пересчитываются по текущим правилам; клиентская видимость отдельной записи остаётся обычной настройкой меню.

**Проверено:** 49 тестов маршрутизации, балансов, каталога и меню прошли; Ruff для изменённых модулей и тестов прошёл. В полном наборе cloud прошло 533 теста, 6 упали в несвязанных сценариях учётных записей, тарифов, текстов и возвратов. Реальные платные запросы не запускались.

**Выкладка:** исходники переданы в `/opt/nanomuse/cloud`, образ релея пересобран и перезапущен. Проверено: контейнер healthy, `/healthz` отвечает 200; в работающем модуле `1017` нормализуется в семейство DeepSeek Flash, старого исключения версии нет. Порог в кэше — $1 / 90 ₽. На момент проверки OpenRouter имел $0.0068, ProxyAPI 40.82 ₽, Polza 29.61 ₽, RouterAI 1.25 ₽, AITunnel 499.36 ₽; поэтому ниже порога были все, кроме AITunnel. Проверка балансов идёт автоматически; после пополнения выше порога провайдер возвращается в выбор, и следующий запрос снова выбирает самое дешёвое доступное предложение. Платный запрос к модели для проверки не выполнялся. Резервные копии заменённых overlay-файлов сохранены на VPS.
## 2026-10-27 — Рассуждения и подписи шагов на языке человека

**Просили:** чтобы рассуждения модели шли на языке приложения (сейчас на русском), а не по-английски. Денис показал в чате английские строки «Analyzing the request» и «Deep diving…» и спросил, как это делает AILINA.

**Что выяснено:**
* Подписи шагов рисует не наш плагин, а сам dsh — пакет `@deepseek-ai/dsh-client-ui-chat`. Там есть словари `zh` и `en` с ключами `message.stepProcess.*` и `chat.deepDiving`; при русском языке dsh берёт английский словарь как запасной, поэтому строки оставались английскими.
* `/Applications/AILINA.app` — отдельное нативное приложение, не на dsh. Как ориентир для этой задачи не подходит.
* Русский язык в приложении даёт вендорный пакет `harness/vendor/dsh-locale-ru` (`@absolutemikhail/dsh-locale-ru`). Он регистрирует словари через `locale.register(ns, 'ru', dict)`; `scripts/patch-dsh.mjs` при сборке подкладывает пакет в дерево dsh, а в `src/main.ts` он прописан в профиле, и язык по умолчанию — `ru`.

**Сделано:**
* `harness/vendor/dsh-locale-ru/lib/client.js`: в словарь пространства `chat` (переменная `dict4`, ns `chat`) добавлены 44 перевода — заголовки шагов (`message.stepProcess.thinking` → «Анализирую запрос»), варианты «готовлюсь» и «сделано», склейки `joinTwo`/`comma`/`more`. Синтаксис проверен `node --check`.
* `harness/dsh-nanomuse/src/rooms-tools.ts`: в контекст системного промпта добавлена строка — думать и писать заголовок и краткое описание каждого шага на языке человека (`languageName(rooms.lang)`), код, команды, пути и цитаты оставлять как есть. Ушло в коммит `8549a0e4`.

**Проверено:** `tsc` чистый, `pnpm run build` собрался; в установленной копии пакета ключ `message.stepProcess.thinking` = «Анализирую запрос», и он действительно в словаре с ns `chat`.

**Коммиты:** `d7b42600` (переводы шагов) в `feat/voice-ru`, запушен в `origin`.

**Не проверено:** живой вид после перезапуска приложения — нужен рестарт NanoLika и новый ход в чате. Заодно не проверены языки кроме русского: для `en`/`zh` поведение прежнее, других пакетов нет.

**Осталось:** если понадобится третий язык — класть рядом ещё один пакет по образцу `dsh-locale-ru` (словари `chat` и остальных ns плюс `addLanguage`).
## 2026-10-27 — Каталог моделей и быстрый список избранного

**Просили:** привести выбор моделей к каталогу с поиском, счётчиками категорий, избранным,
контекстом и стоимостью; сделать выбор удобным в вебе и настольном приложении; выяснить, почему
список чатов не изменился и где единообразно показывать тариф.

**Сделано в исходниках** (`cloud/nanomuse_cloud/console/models/` и
`harness/dsh-nanomuse/src/client/`):

* разделы каталога в кабинете сворачиваются, в каждой карточке сразу видны контекст и предел
  ответа; счётчики категорий уже формируются в исходнике страницы;
* в настройках настольного приложения список выбора начинает с избранных моделей, снизу добавлено
  переключение на полный каталог; категории можно сворачивать, цена из клиентского прайса и
  контекст показываются рядом с названием;
* избранное читается и записывается через `/v1/me/models`, поэтому синхронизируется аккаунтом.
  Веб-кабинет использует ту же серверную полку.
* быстрый список показывает число избранных моделей по типам; полный каталог открывается через
  нижнюю кнопку управления. Для пустого избранного добавлена явная подсказка вместо пустого места.

**Проверка:** `node --check` для страницы каталога и `python3 -m py_compile` для API завершились
без ошибок; клиентский TSX компонент собран esbuild и проверен Node 22. Полная проверка TypeScript
и тесты подвисли при чтении исходников с подключённого диска и были остановлены. Живой сервер не
обновлялся.

**Осталось:** веб-чат в `cloud/nanomuse_cloud/console/app.js` отправляет `chat.send` напрямую
устройству по Hub и не передаёт выбранную модель; отдельного селектора, который реально меняет
модель выполнения, в этом потоке нет. Изменённый настольный picker находится в настройках, а не в
выпадающем списке самого чата, показанном на снимке экрана. Для выбора модели в чате нужна поддержка
этого выбора на принимающем устройстве и в протоколе Hub. Текущий список бесед без преобразований
показывает результат `chat.list`;
группировка лент и архивирование в этом изменении не делались. Стили тарифных кнопок публичного
сайта сюда не менялись. Задача не выложена полностью.

## 2026-10-27 — Скины Лики: папка на сайте, студия тянет каталог по запросу

**Просили:** свою базу скинов на сайте как хранилище, которая пополняется без переустановки приложения; при открытии списка он подтягивается с сайта. Уточнение Дениса: главное — скины самого приложения (UI), но образы Лики тоже оставить.

**Сделано:**
* На nanolika.ru появилась папка `skins/`: `index.json` (каталог) и по файлу на скин в `skins/styles/` — 7 встроенных стилей Лики (muse, flat, clay, watercolor, pixel, line, sticker) с промптами. Сборка каталога — `scripts/build_skins.py`; выкладка — прежним `deploy.py` (с копией сайта перед заливкой). Живьём: https://nanolika.ru/skins/index.json отдаёт 7 скинов.
* Пополнение: новый JSON в `publish/skins/styles/` → `python3 scripts/build_skins.py` → `python3 deploy.py`.
* Host-половина плагина (`src/cloud.ts`): маршрут `GET /skins/list` тянет каталог с сайта со стороны хоста (CORS не мешает), кэш на час, при недоступном сайте отдаёт прошлую копию. `refresh=1` обходит кэш — студия открывается и забирает список заново, поэтому правки на сайте видны без переустановки.
* Клиентская студия (`AvatarStudio.tsx`): при открытии загружает каталог, подмешивает скачанные стили поверх встроенных (скачанный промпт перекрывает встроенный), новые стили появляются чипсами рядом с Muse; выбранный стиль уходит в профиль аккаунта.

**Проверено:** `pnpm run typecheck` чистый, `pnpm run build` собрался, `lib` установлен в живое приложение (резерв `lib.bak-20261027-130927`), в собранном чанке маршрут и адрес каталога на месте, сайт-каталог отвечает 200.

**Не проверено:** живое поведение студии после перезапуска приложения (host-половина читается при старте) — нужен рестарт и открытие студии. Автотесты плагина не гонялись.

**Осталось:** скины самого приложения (UI-оформление) — Денис считает их главными; формат папки уже несёт поле `kind`, темы можно класть рядом и добавлять выбор в настройках оформления. Импорт чужих наборов — по желанию, позже.
## 2026-10-27 — «Итог незавершённой работы» вместо ответа: ход больше не обрывается молча

**Просили:** убрать сообщение «Достигнут лимит времени или шагов… Завершение всей задачи не
подтверждено», которое появлялось вместо нормального ответа после длинной серии команд.

**Почему так вышло.** Это не сбой и не ошибка провайдера: это наш собственный бюджет хода
(`src/turn-budget.ts`, коммит `a42030d6`). Счётчик шагов доходил до сорока, и ход **обрезался**
через `sessionController.cancel`, а карточка итога (`TurnOutcome.tsx`) честно писала, что работа не
закончена. Модель при этом не успевала ничего сказать: её просто останавливали.

**Что сделано** (`turn-budget.ts`, `cloud.ts`, `tests/turn-budget.test.mjs`):
* у бюджета появились `wrapUp` и `grace` (8 шагов): на пределе ход больше не отменяется, а
  получает steer-сообщение «инструментов больше не вызывай, напиши итог словами: что сделано, что
  проверено, что осталось»;
* ход завершается принудительно только если после этой просьбы инструменты продолжаются; по часам
  те же два дополнительных минуты;
* поведение без `wrapUp` осталось прежним (на это есть тест), поэтому старые вызовы не ломаются;
* тесты: 9 из 9 по бюджету, 12 из 12 по итогам хода, `tsc` чистый.

**Собрано и положено в живой каталог:** `pnpm run build` → `lib` скопирован в
`~/Library/Application Support/nanolika-desktop/payload/dsh/node_modules/dsh-nanomuse`, прежняя
копия сохранена в `plugin-backups/dsh-nanomuse-20261027-…`. **Нужен перезапуск приложения**:
host-половина (`cloud.ts`) читается при старте.

**Что не проверено:** живой ход длиной больше сорока шагов. Такая проверка требует перезапуска и
специально длинной задачи; до неё правка подтверждена только тестами и проверкой типов.

## 2026-10-27 — model catalogue safety and picker details

- Removed the generic alias that routed DeepSeek V4.1 Flash to the dated OpenRouter `1017` endpoint. That endpoint is now explicitly excluded from ordinary Flash routing; compatible offers are compared by estimated request cost and each input/output rate is rejected if it exceeds the cheapest comparable rate by more than the configured multiple (default ×3).
- Fixed the admin cost row to use the actual selected route rather than the first item in the provider list. Excluded offers now carry a reason into the provider table.
- Kept excluded `1017` and `:free` supplier quotes visible for review but removed them from the eligible “best offer” calculation. Provider minimum/median input prices ignore excluded offers and show their excluded count separately.
- Normalized Flash market rows to `DeepSeek V4.1 Flash`, retained the original provider ids for routing/audit, added context, response limits, modalities and parameters when a supplier exposes them, retained zero-priced OpenRouter offers as `free`, and added new/changed model detection. Newly detected entries default to hidden until reviewed.
- Added free/context/capability details to the web and desktop model selectors where catalogue data supplies them. Unknown metadata remains unknown instead of being guessed.
- Checks: Python files compile; targeted Ruff checks on routing, menu, price-admin and tests pass; JavaScript syntax checks pass. `provider_prices.py` still has pre-existing lint findings (semicolon statements and unused fetcher URL variables). The targeted Python tests and desktop TypeScript check stalled while reading the mounted `/Volumes/babichdenis` checkout. No live relay change or deployment was made, so the `1017` block takes effect only after the updated source is deployed.

## 2026-10-26 — DeepSeek Flash routing and task step cap

- Confirmed the OpenRouter `DeepSeek V4.1 Flash 1017` entry is the dated snapshot of V4.1 Flash; its public listing shows 1M context and $0.15 input / $0.60 output per 1M tokens. The exact $11 breakdown still requires the OpenRouter Activity page: this checkout has no OpenRouter credential or account activity log.
- Fixed route identity so the floating DeepSeek V4.1 Flash id and the OpenRouter `...-1017` id match. The admin table now labels the least-cost estimate as a calculated route and keeps the actual supplier of the last request distinct.
- Updated the local supplier price key so the floating name and dated snapshot land under the same family key; provider-specific input/output quotes remain separately visible.
- Changed agent tool steps to unlimited by default, while retaining an explicitly configured cap. Existing configs with the legacy defaults 30 and 40 are migrated to unlimited. The phone operator's separate `max_steps` remains independent.
- Validation: agent tests 24 passed; provider-routing and menu-admin tests 30 passed; Ruff checks for changed Python modules passed; both admin JavaScript files pass syntax checks. No production relay was changed or restarted.

## 2026-10-26 — API key check and Riga VPN

- Verified that the account page already exposes personal model API keys: create, one-time copy, per-key spend and revoke. Personal keys can list models and call inference, are blocked from account/device APIs and the device hub, and their inference charges carry that key hash into the ledger.
- Added `cloud/tests/test_user_api_keys.py` for key creation, inference accounting, scope denial, session-list separation and revocation. The focused test passes; Ruff check and format pass.
- Related account, hub and web-client tests: 28 passed, 14 failed. The failures are the existing `connect_limited` tariff gate assumptions in hub tests plus one invite-rate expectation; they are outside personal-key behavior and need separate cleanup.
- Checked the Riga VPN host read-only: the VLESS inbound and client are enabled; ports 8390 and 443 accept connections from this Mac. Client traffic last appeared on 2026-10-21. No server-side outage or config change was found. The phone's VPN app/profile and current error are still unknown.
- Region separation remains a design assessment in `docs/relay-regions.md`, not a deployed second relay. A foreign provider still receives prompt data when called by the Russian relay; region allowlists must be enforced in routing before claiming regional separation. No production deployment performed.

## 2026-10-26 · отключение навязчивой карточки остановки

- Убрана карточка «Итог незавершённой работы» из интерфейса: раньше она автоматически вставлялась в переписку после незаконченного хода. Учёт ошибок инструментов для защиты от повторов сохранён.
- Снято автоматическое завершение хода только из-за 20 минут ожидания. Медленный вызов больше не обрывается таймером; остаётся предел в 40 шагов, защищающий от бесконечного цикла.
- Добавлена проверка, что долгий ход не отменяется одним лишь временем.
- Validation: plugin build, TypeScript check, and 19 budget/outcome tests passed. The installed app was not restarted; it will take effect after the plugin is updated.

# Журнал работы: что сделано и что осталось

## 2026-10-26 · подготовлен тестовый адаптер YooKassa

**Просили:** найти реализацию микрофона и подключить YooKassa с магазином 1498091.

**Сделано в исходниках:** добавлен отдельный адаптер YooKassa для redirect-оплаты. Платёж
создаётся с ключом идемпотентности, переход в оплату разрешён только на домены YooKassa,
а входящее уведомление служит сигналом для повторной сверки через авторизованный API. Реализованы
сверка состояния и возвратов, восстановление незавершённого заказа, тестовый режим с отдельным
кошельком; подключён отдельный адрес уведомлений. Общая страница оплаты и кабинет принимают
платёжные ссылки YooKassa. Боевые платежи остаются закрыты до подключения и проверки чека,
его доставки и возврата. Документация настройки находится в `cloud/docs/DEPLOY.md`.

**Микрофон:** кнопка и захват звука находятся в
`harness/dsh-nanomuse/src/client/voice.ts`; локальный распознаватель Whisper находится в
`harness/dsh-nanomuse/src/speech-whisper.ts`. Кнопка записывает звук через микрофон браузерного
движка, отправляет WAV в маршрут `/voice` на компьютере; модель Whisper выполняет распознавание
локально.

**Проверка:** `pytest` по YooKassa, платежам, сверке и платёжному аудиту: 32 теста прошли.
Внешний магазин не вызывался, реальный платёж не создавался, сервер не обновлялся. На живом
сервере не проверено: для проверки нужен тестовый ключ, установленный напрямую в секретах
окружения. Перед этим новый live-ключ нужно перевыпустить, так как прежний был отправлен в чат.
**Осталось:** проверить реальный тестовый платёж, чек и возврат на стенде YooKassa; подключить
обработку чека к заказу и только после этого обсуждать включение live. Ловушка: YooKassa
возвращает строковые идентификаторы платежей и неподписанные уведомления, в отличие от T-Банка
с числовыми ID и подписью; прямое доверие уведомлению позволило бы подделать зачисление.

## 2026-10-26 · Тариф включается по оплаченной части оставшегося баланса

**Просили:** исправить смысл тарифа: например, после пополнений 300 ₽ и 400 ₽ и расхода
100 ₽ на оплаченной части остаётся 600 ₽, поэтому на 30 дней включается «Лика» со скидкой
10%. Также рядом с адресом аккаунта нужно постоянно показывать название действующего тарифа.

**Сделано в исходниках:** подтверждённое пополнение накапливает `paid_balance_uy`; оплаченный
расход уменьшает эту сумму; при достижении порога включается самый высокий подходящий тариф на
30 дней. Сумма порога остаётся доступна для работы, бонусы не засчитываются, новое пополнение
не продлевает активный срок, но может открыть более высокий тариф. Полный возврат корректирует
остаток и восстанавливает прежние условия, если они не были заменены позднее. Старые аккаунты
однократно получают расчёт оплаченного остатка из накопленных пополнений за вычетом расходов.
`/v1/me` возвращает оплаченный остаток для порогов. Кабинет показывает его отдельно от общего
остатка; email в кабинете и меню сайта сопровождается именем тарифа и скидкой.

**Сайт:** карточки называют цену порогом баланса, кнопки — суммой пополнения; пример 300 − 100
+ 400 = 600 ₽ разъясняет включение «Лики». Обновлены публичная оферта, страница тарифа и
описание ИИ-агента. Текущая оплата остаётся явно помеченной как тестовая/неактивная.

**Проверка:** 49 тестов платёжного контура прошли; `node --check` для изменённых браузерных
скриптов и `compileall` для relay прошли. **На живом сервере не проверено:** текущий `/v1/payments/config`
остаётся DEMO, ключа для публикации на сервер нет; реальные списания не включались. **Осталось:**
перенести исходники сайта и relay в целевое окружение после готовности кассовых настроек, затем
проверить тестовый и первый разрешённый платёж. Ловушка: в старой логике цена тарифа одновременно
была отдельной покупкой и суммой пополнения, а обычное пополнение не запускало тариф.

## 2026-10-26 · Модели настраивались в трёх местах; «Свои модели» и «NanoLika Cloud» были одним и тем же

**Просили:** проверить пункты настроек на актуальность и убрать дубль между «Своими моделями» и
«NanoLika Cloud».

**Почему так вышло.** Навигацию настроек собирает наш `MuseSettings` из всех разделов, что
регистрируют и наши страницы, и стоковые плагины харнесса, — а плагинов, которые трогают модели,
оказалось пять: наши «Модели» (четыре слота), наши «Свои модели» (`CloudSection` часть `keys`),
стоковая `dsh-client-ui-settings-models` («Модели» в «Дополнительно», те же строки провайдеров с
ключами), панель `OwnKeyPanel`, встроенная ещё и в страницу аккаунта, и пикеры картинок/роликов,
продублированные на «Картинках и роликах». Ключи наши и стоковой страницы — **один магазин**: свои
строки провайдеров мы пишем в адаптер моделей харнесса (`src/providers.ts`), поэтому стоковая
страница показывала ровно то, чем управляют «Свои модели».

**Что сделано** (`CloudSection.tsx`, `MuseSettings.tsx`, `locales.ts`):
* страница аккаунта больше не рисует `OwnKeyPanel` и прокси-строки — вместо них строка-ссылка
  «Свои модели» (и во вошедшем, и в не вошедшем состоянии);
* «Свои модели» переехали в повседневную группу, сразу после «Моделей»;
* стоковая «Модели» убрана из навигации (`REPLACED_BY_OURS`): плагин остаётся загруженным, страница
  просто не предлагается третьим путём к тому же;
* новый ключ локали `ownKeysSummary` во всех трёх языках.

**Собрано и положено:** `node build.mjs` в `harness/dsh-nanomuse` → `lib` скопирован в живой
payload `~/Library/Application Support/nanolika-desktop/payload/dsh/node_modules/dsh-nanomuse`
(старый `lib` сохранён как `lib.backup-20261026-1646`). **Нужен перезапуск приложения** — клиент
читает бандл при старте.

**Осталось (не сделано, требует решения):** «Картинки и ролики» всё ещё держат свои копии пикеров
картинок и роликов — это третий выбор тех же двух слотов; по аудиту разделов есть ещё пары дублей
(список «всегда разрешено» на «Работе с компьютером» и на «Разрешениях», «показать папку» на
«Хранилище» и на «Файлах», опрос `link/channels` в «Мессенджерах» и «С телефона»), устаревшие
тексты в «Кошельке» и «Хранилище», и осиротевшие ключи локалей (`gnStar`, `legalMeta`,
`legalThanks`, `legalThanksText`, `abDocs`, `abIssues`) от чужой незакоммиченной правки — из-за них
`tests/locales.test.mjs` сейчас красный.

## 2026-10-26 — Исчезает оформление desktop

По жалобе «остаётся скелет» проверено подключение CSS. В Chromium воспроизведён дефект ensureStyles: очистка первого владельца удаляла общий CSS и атрибуты html у второго живого владельца. Добавлены общий для поколений бандла реестр владельцев, отложенная очистка при переподключении и восстановление случайно удалённых стилей/атрибутов. Без опроса DOM и наблюдения переписки; явное отключение плагина остаётся рабочим. Браузерный сценарий воспроизвёл старый сбой и проверил семь вариантов жизненного цикла после исправления. Детали и ограничения — docs/desktop-skin-lifecycle.md. Проверка типов и 11 тестов стилей/DOM hooks прошли. Дополнительно обнаружен и исправлен в исходниках startup: ensureProfile отменял выбор обновляемой копии плагина и снова направлял профиль на старый payload. Для внешнего плагина добавлена ссылка на закреплённые зависимости runtime. Обновлены и рабочая копия, и реально загружаемый payload-клиент с резервными копиями. На реальном HTTP-интерфейсе приложения проверено принудительное удаление CSS/атрибута: оформление восстановилось. Затем 60 секунд наблюдения: один владелец, один CSS, бумажный фон сохраняется, ошибок страницы нет. TypeScript клиентского плагина прошёл; desktop прошёл с typeRoots закреплённого runtime (его собственная папка @types отсутствовала). Семь браузерных сценариев жизненного цикла и 11 тестов стилей/hooks прошли. Окно пользователя не перезагружалось после обновления фактического клиента: нужен Cmd+R, работа сервера при этом сохраняется. Изменение startup находится в исходниках для следующей сборки оболочки; инсталлятор не собирался.


## 2026-10-26 · Микрофон: Blob из записи убран совсем

**Уточнение к записи ниже, по факту.** Правка через `FileReader` не помогла: после перезапуска
приложения запись голосом снова дала `Speech recognition failed: External buffers are not
allowed`. Значит дело не в способе чтения, а в самом `Blob`, который отдаёт `MediaRecorder` в
Electron: любой путь через него упирается во внешнюю память.

**Что сделано теперь** (коммит `f4d8ac32`): запись вообще не пользуется `Blob`. Звук берётся
сэмплами `AudioContext` через `ScriptProcessor` и складывается в один `AudioBuffer` — ровно так,
как в нашем собственном `client/voice.ts`, который был написан из-за этой же граблей.
`decodeAudioData` из пути исчез, `AudioContext` будится явно: в `suspended` событий записи не
бывает, и человек видит «ничего не записалось» вместо ошибки.

**Где правлено:** живой бандл `payload/dsh/.../dsh-experimental-client-ui-voice-input/lib/client.js`,
патч `harness/desktop/scripts/patch-dsh.mjs` (узнаёт несколько форм кода пакета, идемпотентен,
печатает, какие места не нашлись) и дерево `harness/desktop/dsh`. Патч проверен на чистом пакете
из `NanoLika.app`: вставки применяются, `node --check` проходит.

**Что проверено и что нет.** Проверено: распознаватель и модели (фраза распознана дословно),
синтаксис обоих файлов, применение патча к чистому пакету. Не проверено: живая запись голосом
после этой правки — нужен перезапуск приложения и голос.

## 2026-10-26 — Ошибка upstream Reka

Подтверждён turn/end error PI_AI_ERROR в session-311bab2f, turn 2: внешний поставщик вернул Upstream error from Reka. Это не отмена бюджета. До него turn 1 закончился отдельной ошибкой недостатка средств у поставщика. Добавлено распознавание upstream-error как server в карточке, с локализованным объяснением; технический оригинал остаётся в раскрываемых подробностях. Слепой повтор всей задачи не включён.

## 2026-10-26 · Микрофон: рвалось чтение записи, а не распознавание

**Просили:** починить микрофон — «заявлен в настройках, а работает через ошибку», и в бандле, и
в исходном коде. Присланная ошибка: `Speech recognition failed: External buffers are not allowed`.

**Что оказалось.** Ни модели, ни распознаватель не виноваты: русская фраза, прогнанная через те
же `sherpa-onnx` и те же файлы в `$DSH_HOME/speech-to-text/whisper`, вернулась дословно за 14 с.
Рвалось раньше — на чтении записи: в Electron `Blob.arrayBuffer()` отдаёт ArrayBuffer на внешней
памяти Blink, и Chromium отказывается его принимать.

**Как это видно по тексту ошибки** — главный урок записи. Клиент подписывает шаги внутри
`stage("decode"/"resample"/"encode")`, и отказ декодирования выглядел бы как
`stage:decode:External buffers…`. В сообщении префикса не было — значит отказ пришёл ДО
`stage`, то есть из `const raw = await blob.arrayBuffer()`. Прежний патч (коммит `20657c60`)
делал `raw.slice(0)` — копию уже после отказа, поэтому микрофон не работал ни разу, а сборка
при этом была зелёной.

**Что сделано.**
- Живой бандл: `payload/dsh/node_modules/@deepseek-ai/dsh-experimental-client-ui-voice-input/lib/client.js`
  — чтение записи заменено на `recordingBytes(blob)` (FileReader + копия в обычный ArrayBuffer),
  функция добавлена в конец файла. Обёртка `stage` не тронута.
- `harness/desktop/scripts/patch-dsh.mjs` (коммит `6c46b821`): патч больше не ищет точную строку
  `decodeAudioData(await blob.arrayBuffer())` — пакет оборачивает вызов в свой `stage("decode", …)`,
  и поиск промахивался молча; теперь заменяется само чтение, а если замены не нашлось, сборка
  печатает предупреждение, а не проходит тихо.
- Подготовленное дерево `harness/desktop/dsh/...` приведено к той же правке.

**Чем проверено.** Синтаксис обоих файлов (`node --check`), затем перезапуск приложения в 14:13
и запись голосом в приложении — Денис подтвердил, что работает. Тексты страницы «Диктовка» не
менялись: они и так обещают микрофон рядом с кнопкой отправки.

**Что осталось.** Правка живого бандла сделана на месте, в `.app` её нет: установщик 1.0.8
соберётся с ней только через `patch-dsh.mjs`. Копия приложения на `.7` не проверялась — там
тот же пакет, и микрофон, скорее всего, вёл себя так же, пока её не пересоберут.

## 2026-10-26 — Итоги после неудачных попыток

Добавлен TurnOutcomes: сохраняет итог прерванного/ошибочного/незавершённого хода, отличает остановку по бюджету от общей отмены, останавливает два одинаковых ошибочных вызова подряд. Последний итог показан в соответствующей беседе над вводом и сохраняется после перезапуска. Модель получает правило объяснять неудачу, менять подход и писать видимые пояснения простым русским языком для русскоязычного пользователя. Платного суммирования и автоматического повтора нет. Контракт и ограничения — docs/turn-outcomes.md. Найденный при проверке старый сбой сборки voice.ts исправлен: отсутствующий ключ micWorking заменён существующим working. Проверено: 36 тестов turn-outcomes/turn-budget/refusals прошли; полный build.mjs с TypeScript завершился успешно. Собранный плагин и preset скопированы в установленное приложение с резервной копией, хеши client.js/cloud.js совпали. В момент установки активны пользовательская и дочерние сессии, поэтому процесс не перезапускался: новый host-код включится после следующего запуска приложения. Исходники находятся в основной ветке feat/voice-ru; чужие незакоммиченные About/Sections/locales/panels сохранены. Инсталлятор не собирался.


Один файл, который читают все сессии. Сюда пишется то, чего не видно в коде и в `git log`:
почему решили именно так, что проверено на живом сервере, где остались грабли и куда идти
дальше. Порядок — свежее сверху. Каждая запись: дата, что просили, что сделали, чем
проверили, что осталось.

Правило для агента, который работает в этой сессии: **в конце каждой законченной задачи
допиши сюда запись**, а не только коммит. Если задача не закончена — запиши, где остановился
и чего не хватает. Никогда не переписывай чужие записи: только добавляй новую сверху.

**Начинай сессию с раздела «Как всё сложилось» и «Где мы сейчас»**: там вся история проекта и
живое состояние, проверенное 26.10.2026. Датированные записи ниже нужны для причин и граблей,
хронику они не заменяют.

---

## Как всё сложилось: хроника проекта

Постоянный раздел, не запись сессии. Короткая история от пустого репозитория до сегодняшнего
дня, чтобы новая сессия не начинала с нуля. Источники: `git log`, `CHANGELOG.md`, теги.

### Апрель — сентябрь 2026: чужое приложение, потом своё

- **25.04.2026** репозиторий создан как зеркало приложения Minis (позже OpenMinis): Android и
  iOS, README, шаблоны issue. Своей разработки ещё нет.
- **25.07.2026** Minis открыт наружу: `feat: open-source the Minis app`, в README сказано, что
  зеркало не принимает pull request.
- **19.08.2026** и **02.09.2026** два больших заноса работы по приложению до линии 1.13:
  чат, провайдеры, песочница, резервные копии, правки экранов.
- **22.09.2026** перелом: появляется **свой рантайм на Python** (`OpenMuse v0.1.0 core`):
  агентский цикл, Sentinel как привратник, vault, память, цели, инструменты, CLI, сервер
  приложения, веб-приложение. С этого дня продукт свой, а не переделка чужого.
- **24.09.2026** лицензия меняется на **GPL-3.0-or-later**; `0.1.1`: OpenMinis 1.13 выходит под
  именем nanoMuse; рантайм учится жить на телефоне (PRoot, rootfs, мост в CLI).
- **25.09.2026** за один день выходит **четырнадцать выпусков** (0.1.2—0.1.15): лицо агента,
  дом, защита от опасных действий, память, приветствие, «Руки» (экран телефона как рука),
  «Дотянуться» (телефон управляет компьютером через спутник), «Сцена» (видно, что делают руки).

### Конец сентября: облако, консоль, веб

- **26.09.2026** `0.1.16`; в репозитории появляется `cloud/` — свой релей. Материалы запуска
  решено в публичный репозиторий не класть.
- **29.09.2026** `0.1.18`: открытая регистрация, дневная норма в деньгах.
- **30.09.2026** пять выпусков за день (0.1.19—0.1.23): каждый девайс это Муза, звонки, кодинг-
  агенты, аудит всего проекта, релей 0.4 с нормой ¥15 и приглашениями, студия аватара.
- **01.10—02.10.2026** шесть выпусков (0.1.24—0.1.28): вход по телефону, одно имя и лицо на
  аккаунт, витрина вокруг телефона, управление данными, **настольное приложение выходит
  установщиками на DeepSeek Harness**. Здесь начинается `harness/dsh-nanomuse`.
- **03.10—04.10.2026** пять выпусков (0.1.29—0.1.33): комнаты (Feed, Идеи, Цели, Библиотека),
  никакого токена в адресах, паритет четырёх клиентов, аргумент `step` у каждого инструмента.
- **05.10.2026** пять выпусков (0.1.34—0.1.38): руки с передачей хода, чат-приложения,
  выравнивание iPhone и десктопа, **одна беседа на всех устройствах** (синхронизация), боковые
  чаты остаются на устройстве, у рук Мака появляется отдельный helper.
- **06.10—07.10.2026** три выпуска (0.1.39—0.1.41): каталог провайдеров с возможностями,
  понятные отказы, выбор модели под каждую из четырёх работ.

### 09.10.2026: 1.0.0 Keel

Первая стабильная версия: приложение для Android и iPhone, десктоп для Мака, Windows и Linux,
веб-консоль и открытый релей. Один аккаунт на все устройства, GPL-3.0-or-later, некоммерческий
проект. Дальше пять дней подряд ежедневные выпуски-проверки: 1.0.1 Ballast, 1.0.2 Trim,
1.0.3 Helm, 1.0.4 Compass, 1.0.5 Lookout.

### 15.10—17.10.2026: поворот на русский, имя NanoLika

- **15.10** поддержка серии GPT-6 и живого списка моделей Codex.
- **16.10** Serply как поиск по Google со своим ключом.
- **17.10** **ребрендинг в NanoLika**: своё имя, адреса `nanolika.ru` и `cloud.nanolika.ru`,
  русский словарь десктопа (1402 надписи), русская веб-консоль, рубли вместо юаней, звёздочки и
  подталкивания к чужим ключам убраны.

### 20.10—26.10.2026: деньги, поставщики, установщики

- **20.10** иконки и аватары NanoLika; helper сам владеет своими разрешениями; поиск идёт через
  бесплатные тылы релея (`web-search.ts`).
- **21.10—22.10** одна память на все чаты; тарифы релея, продажа и закупка учитываются
  раздельно; чат в Telegram может стать отдельным аккаунтом; слияние с апстримом
  (`pre-upstream-merge-20261022`).
- **23.10** платежи: оплаченный заказ превращается в тариф одной кнопкой; раздел «Платежи» для
  оператора; оформленные установщики Windows (NSIS) и окно dmg; русская речь через локальный
  Whisper; **1.0.6**.
- **24.10** вход в веб-Лику через Яндекс, Google, GitHub и Telegram; страница цен поставщиков в
  админке; новые поставщики proxyapi, bothub, neuroapi, openrouter; облачность каждой беседы
  отдельно; **весь код релея переезжает в репозиторий**, файловые overrides больше не нужны.
- **25.10** страница цен для оператора за случайным адресом и паролем; ответ соседней моделью,
  когда все поставщики отказали; сторож балансов; внешний аудит (nuclei, ffuf: 0 находок);
  склад того, что не лезет в установщик; инвентарь возможностей `CAPABILITIES.md`; ядро dsh в
  payload; helper вынесен из бандла; привязка телефона по коду; темы Telegram и HTML-разметка;
  мост в Office без LibreOffice; **1.0.8**.
- **26.10** приветствие на русском; кнопки входа через Яндекс и Google на первом экране плюс
  мелким шрифтом «пропустить регистрацию»; правда о числе моделей; вкладка «Браузер» в правой
  панели включена в сборке; снимки рук убраны из переписки; учёт и цены в релее.

### Чем это всё проверялось

Живой сервер, а не только тесты: ответ через релей, снимок экрана и движение мыши настоящим
helper'ом на двух машинах, установка сборки на чистый Мак (macOS 13.7.8), внешнее сканирование
портов, HTTP-коды на публичных адресах, сверка sha256 файлов раздачи с манифестом. Отдельно
записаны грабли: приоритеты каналов читаются из `abilities`, а не из `channels`; в Caddy
`respond` сортируется после `handle`; файлы веб-клиента монтировались поштучно из `overrides/`;
`tccutil reset` снимает рабочее разрешение; ad-hoc подпись обнуляет выданные разрешения macOS
при каждой пересборке.

---

## Где мы сейчас (проверено 26.10.2026, 13:40)

**Живое, отвечает:**

| что | адрес | проверка |
|---|---|---|
| сайт | `https://nanolika.ru/` | 200 |
| веб-Лика | `https://cloud.nanolika.ru/web/` | 200 |
| релей | `cloud.nanolika.ru`, версия 0.26.0 | пересобран и поднят 26.10 около 03:00 |
| бот | `nanolika-bot` на 151 | перезапущен 26.10, `healthy`, отвечает |
| раздача сборок | `/dl/mac/latest.json` | 200, версия 1.0.8 |
| страница цен оператора | случайный адрес за паролем | без пароля 401, старые `/app/prices/` и `/app/menu-admin/` отдают 404 |

**Версии:** оболочка десктопа 1.0.8, релей 0.26.0, рантайм 1.0.5.
**На .7** 1.0.8 стоит и работает: оба разрешения выданы вручную через «+» в панелях, снимок
экрана и мышь проверены вживую.
**Поставщики и деньги:** polza 229,92 CNY, openrouter 15,00 USD, прямой DeepSeek −0,04 CNY
(канал не платит, работает соседний).

**Что живёт на релее с 26.10** (иначе следующая сессия будет искать это заново):
- `DELETE /v1/link/channels/{channel}` — отвязка чатов мессенджера; `POST /v1/bot/unlink` — одного чата.
- Письмо о подключении на адрес аккаунта при каждой привязке (`senders.send_channel_link`).
- QR-подключение живёт 3 минуты (`CHANNEL_START_TTL_S`), набранный код — 10 (`CHANNEL_CODE_TTL_S`).
- `link_taken` отвечает `taken: {label, channel, own}` — по нему бот спрашивает «выйти?».
- Кнопки «Открыть в Telegram / Продолжить в MAX» из веб-клиента **убраны** (их сняла
  параллельная сессия): продолжать беседу в мессенджере надо оттуда, командой `/chats`.

**Что нужно сделать руками, чтобы это увидеть:**
- **Перезапустить приложение NanoLika** — кнопка «Отвязать» в панели подключений уже собрана и
  разложена в обе копии плагина, но работающее приложение держит старый код в памяти.

**Расхождения, которые нельзя забыть:**

1. **Плагин отстал от оболочки.** `harness/dsh-nanomuse/package.json` = 1.0.5,
   `harness/desktop/package.json` = 1.0.8, а правило проекта требует одну версию на оба.
2. **Свежий dmg не выложен, и в манифесте чужой размер.** Локально лежит
   `dist/NanoLika-Desktop-1.0.8-mac-x64.dmg` (419 172 003 байта, sha256 `d6d18908…`), на сервере
   лежит сборка 23:38 (419 165 403), а манифест обещает 419 021 740 и не совпадает ни с чем.
   zip при этом сходится с манифестом точно (`640e78fb…`, 391 461 536). Обновление скриптом на
   такой раздаче может ругаться на размер.
3. ~~**108 коммитов не отправлены на GitHub.**~~ **Закрыто 26.10, 00:50:** ветка
   `feat/voice-ru` отправлена, впереди ноль. Дальше отправлялось после каждой задачи.

---

## Открытые вопросы (одним списком)

Перепроверено 26.10.2026, 13:40. Что закрыто — вычеркнуто тут же, чтобы список не врал.

1. **«Стрелочка вниз» для Мака.** Проверка версии внутри приложения есть только для Windows
   (`windows-updates.ts`). Для macOS клиент обновления готов (`scripts/update-lika.py`: `--check`,
   `--dry-run`, `--force`, sha256, Корзина, перезапуск), но кнопки нет. Мешает одно: работающее
   приложение не заменит само себя, нужен отдельный процесс, который переживёт закрытие.
2. **Developer ID.** Заявка Apple не проходит до оплаты: регион аккаунта Турция, нужен турецкий
   телефон и адрес. Локальный сертификат создан, ключ в связке, доверие не выдано. Пока подпись
   ad-hoc, разрешения macOS слетают при пересборке.
3. **Правка цены продажи на странице цен не сохраняется.** В релее есть только
   `PUT /v1/admin/prices/models/{id}/visibility`, маршрута для `sell_*` нет.
4. **Поиск в приложении.** Провайдер через релей написан (`web-search.ts`, `b32601d9`), но что он
   действительно подхватывается вместо deepseek'овского, вживую не подтверждено.
5. **Письмо о подключении ни разу не уходило живому человеку.** Путь проверен до отправки
   (расшифровка адреса, сборка письма, 2 теста), первое настоящее придёт при первой привязке.
   Стоит посмотреть в Gmail, как выглядит вёрстка.
6. **Кнопка «Отвязать» в приложении** собрана и разложена по обеим копиям плагина, но появится
   **после перезапуска** NanoLika: живое приложение держит старый код в памяти.
7. ~~**Сборка 1.0.8 с русским приветствием на .7** установлена до пересборки.~~ **Закрыто 26.10
   около 04:00:** 1.0.8 встала на .7 полностью, оба разрешения выданы вручную через «+»,
   пробный снимок 1596×672, мышь двигается. Русское приветствие в сборке проверено по
   `\uXXXX`-эскейпам (`startsWith("ru")`), вживую на .7 не смотрели — окно первого запуска
   на .7 уже было пройдено.
8. **Раздача dmg расходится с манифестом** (см. «Где мы сейчас», пункт 2) и **плагин отстал от
   оболочки по версии** (там же, пункт 1). Оба — отдельная мелкая работа, не блокирует ничего.
9. **Заготовка, которая ни к чему не подключена.** Шесть файлов написаны 26.10 к 11:55 (единый
   источник розничных цен, оценка стоимости модели в интерфейсе, свой микрофон без
   `decodeAudioData`), но их никто не импортирует. В приложении этого нет. Либо подключить, либо
   выбросить — но не считать сделанным.

---

## 2026-10-26 — Сравнение со вчера: маршрут, длина цепочки и остановки интерфейса

По просьбе пользователя сравнили те же две сессии за 25/26 октября. Вчера прямой DeepSeek смешивался с облаком, сегодня все сообщения этих сессий идут через облако. У b1 медиана модельного шага 6,1→5,5 сек, но хода 1,3→5,5 мин; у 80c — шаг 4,9→10,2 сек, ход 2,3→2,9 мин. Большой контекст был уже вчера (медианы около 400k), поэтому объяснение «сегодня просто раздулась история» недостаточно. Вчера вечером добавлен ProviderRouter, сегодня TurnBudget. В desktop.log появились два renderer stopped answering. Причинную связь одного изменения со всеми симптомами без контрольного теста не заявляем. Дополнено в runtime-context-audit-2026-10-26.md; живые настройки не менялись.

## 2026-10-26 — Правка четырёх файлов: выполнена, сборка прервана, итога в чате нет

Проверен конкретный ход 467 в b1d02151. До обещанной правки дошёл на шаге 31. Сама правка About/Sections/locales/panels заняла около 5 секунд (+19/-29 строк). Затем diff, повторное чтение сборки и node build.mjs; через 85 секунд сборка прервана, turn/end = aborted/user, итогового ответа нет. Текущий /work пуст. Это разрастание подготовки и проверки с неясным завершением для человека, а не незавершившаяся запись четырёх файлов.

Важное уточнение предыдущего аудита: TurnBudget вызывает тот же sessionController.cancel, который записывает user. Поэтому по этому полю нельзя утверждать, что человек сам нажал «Стоп». Поправлено в runtime-context-audit-2026-10-26.md. Четыре чужие незакоммиченные правки не переписывались и не включались в коммит этого аудита.

## 2026-10-26 — Проверка долгих ходов и ошибочного подсчёта контекста

Попросили проверить две долго работающие беседы в приложении на Mac6 и утверждения другого помощника о контексте, лимитах и пустых поставщиках. Полный проверенный отчёт: [runtime-context-audit-2026-10-26.md](runtime-context-audit-2026-10-26.md).

На момент проверки локальный /work вернул busy: [], /live — turns: []; активных ходов не было. В истории подтверждены ходы по 100 шагов/48 минут и 89 шагов/45 минут, остановленные с причиной aborted/user (она не различает кнопку и автоматический бюджет). Прочитаны 29 журналов без ошибок кадров. Важное исправление: inputTokens не включает cacheReadTokens; полный вход превышал 100k у 19 сессий, а не только у трёх. Пики — 799824, 799506, 619081. Сжатие уже есть, штатный thresholdRatio=0.8 при окне 1M объясняет поздний порог. Текущий maxTokens основных DeepSeek — 64k, не 384k. Ограничение HTTP-тела уже есть — 16 МиБ; изображения допускаются до 5 МиБ, поэтому глобальный лимит 2 МиБ нельзя вводить без разделения типов запросов.

Подтверждено: код переключает маршрут по 402, не запоминая недоступность поставщика; в журнале релея 702 переключения RouterAI/402 за 48ч. Денежная оценка из чужого текста игнорировала кеш, реальные суммы этим аудитом не сверялись. Зависимость от старого DEEPSEEK_API_KEY не доказана. Платные генерации не запускали, действующие лимиты и маршрутизацию в ходе аудита не меняли.

Изменения визуального каталога, розничного расчёта и кабинета восстановлены из удалённой рабочей копии и включены в сборочную ветку коммитом 939bb82a. Параллельные правки Sections/locales сохранены. Предыдущая пометка «заготовки без подключения» описывала промежуточное состояние, а не итог опубликованного каталога.

## 2026-10-26 — Каталог, розничные цены и спокойный бумажный интерфейс

Просили привести к одному порядку административный прайс, кабинет, выбор моделей в вебе и приложении: категории, контекст, избранное, параметры и расчёт по сохранённым ценам.

Сделано: общий модуль model_pricing, сохраняемые в SQLite розничные ставки с ревизиями и аудитом, нерасходующий средства POST /v1/models/quote. Каталог отдаёт скидку аккаунта, контекст и поддержанные конфигурацией параметры. Списания текста и медиа используют розничные ставки; видео фиксирует принятую сумму до запуска. Избранное хранится у аккаунта. Обновлены веб-выбор, кабинет и исходники desktop picker, включая бумажные поверхности, шалфейные акценты, категории, поиск и раскрываемый калькулятор. В административной таблице подробные закупочные колонки включаются отдельно; исправлено перекрытие кнопки сохранения на мобильном экране.

Проверки и ограничения: 81 серверный тест прошёл, Ruff и TypeScript typecheck прошли; сборка плагина и 387 его тестов прошли. См. docs/model-catalogue.md. Браузерные сценарии выбора, избранного и расчёта проверены на 390/768/1440 px; сохранение цены — на 390/1440 px. Это проверки с подставленным API, не доказательство работы промышленного списания. Реальные цены массово не заменяли: модели без отдельной наценки отмечаются legacy_cost, убыточные ставки требуют решения оператора. Не запускали платные генерации и не собирали установщики.

На живом сервере: обновлены административный прайс, кабинет, веб-выбор и модуль расчёта. Релей healthy. Каталог вернул 401 модель с commerce; 17 из них ещё без отдельной розничной ставки. Неавторизованный расчёт возвращает 401. Резервная копия базы: /opt/nanomuse/cloud/backups/model-catalogue-20261026-112439. Кабинет проверен на 390/768/1440 px: исправлены сжатие поиска, перекрытие карточки кнопкой сохранения и лишние нули в ценах.

Важная ловушка: общая рабочая папка параллельно изменялась другой задачей, из-за чего незакоммиченные правки исчезали. Итог перенесён в отдельный worktree nanoMuse-model-catalogue, ветка codex/model-catalogue. Перед будущей сборкой установщиков этот коммит нужно включить в сборочную ветку.

## 2026-10-26 · «Сохрани историю для будущих сессий» — она уже сохраняется здесь

**Просили:** сложить всё сделанное так, чтобы каждая новая сессия начинала с этого.

**Что проверила и нашла.** Файл есть — это он, `docs/WORKLOG.md`, 894 строки. Он уже устроен
как стартовый экран: «Как всё сложилось» (хроника от апреля 2026), «Где мы сейчас» (живое
состояние с проверкой 26.10 04:30), «Открытые вопросы» и датированные записи свежее сверху.
Правило «начинай с журнала» лежит в трёх местах: глобальный `AGENTS.md` этой машины
(раздел «Журнал работы»), `~/.nanolika/desktop/AGENTS.md` и `~/Dev/nanoMuse/AGENTS.md`.
Новой сессии ничего объяснять не нужно — она видит это в инструкциях.

**Что проверила по факту, а не по памяти.** За час 10:17—13:06 закоммичено четыре работающих
дела: «Избранное» в панели моделей (`2b28faf5`), «Мессенджеры» показывают привязанные чаты
(`0cbb3f99`), «Свои модели» отдельной страницей и без пункта про исходный код (`20267207`),
Лика сама открывает панель предпросмотра (`8b260992`). Плюс голос и ширина панели
(`0affd72c`, `6284bc52`, `20657c60`). Это и есть ответ на «что сделано кроме написанного».

**А заготовки так и не подключены** — проверила ещё раз 13:36: `voice.ts` не импортирует никто,
`ModelEstimate` встречается только в собственном файле, `model_pricing.py` не импортирует ни один
питоновский файл, `model-detail.js` не подключён ни в одном HTML. Шесть файлов, 24 КБ, лежат
мёртвыми с 11:53. Это пункт 9 в «Открытых вопросах» — до сих пор открыт.

**Урок, который дороже этой записи.** Спросить «сохрани историю» через час после работы — значит,
что рассказ в чате не заменяет журнал. Пиши запись сразу после задачи, а не по просьбе: чат
умирает, файл остаётся.

**Что осталось.** Пункты 1, 7 и 8 «Открытых вопросов»: стрелочка вниз для Мака (клиент готов,
кнопки нет), пересборка 1.0.8 на .7, раздача dmg расходится с манифестом. Начинать с пункта 8 —
он блокирует проверку остальных.

---

## 2026-10-26 · Час работы после объяснения: сделано много, подключено ничего

**Как проверялось.** `git log`, `git status`, поиск по импортам (`grep` по `voice.ts`,
`ModelEstimate`, `model_pricing`) и чтение `styles.ts`. Ниже — только то, что видно в файлах.

**Что действительно сделано и работает** (всё закоммичено):

| когда | что | коммит |
|---|---|---|
| 11:20 | журнал: «Свои модели» отдельной страницей + правило про `git stash` в общей копии | `d98f4a05` |
| 11:25 | голос: один микрофон вместо двух, защита от пустого звука | `0affd72c` |
| 11:27 | панель предпросмотра больше не сжимает переписку | `6284bc52` |
| 11:41 | голос: `decodeAudioData` не принимает внешний буфер из `Blob` (правка `patch-dsh.mjs`) | `20657c60` |

**Что написано, но не подключено** (шесть файлов, `git status` → untracked, к 11:55):

- `harness/dsh-nanomuse/src/client/voice.ts` — свой микрофон, без `decodeAudioData`
  (Electron на внешних буферах отвечает «External buffers are not allowed»).
- `harness/dsh-nanomuse/src/client/ModelEstimate.tsx` — оценка стоимости модели в интерфейсе.
- `cloud/nanomuse_cloud/model_pricing.py` — единый источник розничных цен.
- `cloud/tests/test_model_pricing.py` — тесты к нему.
- `cloud/nanomuse_cloud/web_client/model-detail.js` — карточка модели с ценами в веб-клиенте.
- `cloud/nanomuse_cloud/web_client/model-library.css` — стили к ней.

Проверка импортов: `voice.ts` не импортирует никто, `ModelEstimate` встречается только в самом
себе и в `styles.ts` (стили `.nm-model-estimate` добавлены — начало встраивания), `model_pricing`
не импортирует ни один питоновский файл, `model-detail.js` никем не подключается. Компонент в
`ModelPicker`/`ModelsSection` не вставлен. **В собранном плагине и в живом релее этого нет.**

**Урок.** Новый файл — это ещё не работа. Перед словом «сделано» надо проверить, что файл
кто-то импортирует и что он попал в сборку: `grep` по имени модуля и по имени класса, затем
`git status`. Иначе получается час работы, которого не видно ни в приложении, ни на сайте.

**Что осталось по этой заготовке.** Либо подключить (вставить `<ModelEstimate>` в список моделей,
импортировать `model_pricing` в `api.py`, подключить `model-detail.js` в HTML веб-клиента и
пересобрать), либо удалить, чтобы не путала. Пока — не считать сделанным, в «Открытых вопросах»
это пункт 9.

---

## 2026-10-26 · «Свои модели» отдельной страницей и урок про git stash в общей копии

**Просили:** убрать пункт «Исходный код NanoLika» из настроек и разобраться, почему в разделе
«Модели» показываются подключённые устройства.

**Почему так вышло.** Кнопка «добавить провайдера» в «Моделях» вела на **всю страницу аккаунта**
(`CloudSection`, часть `account`): там и пароль, и список входов, и баланс. Ключи провайдеров
своего экрана не имели, поэтому человек, нажимая «добавить» рядом с моделями, попадал между
паролем и историей входов. Сделана третья часть `keys`: ключи, способы входа и прокси, ничего
больше; она же появилась в настройках как **«Свои модели»** (порядок 6, сразу после «Моделей»).

**Пункт про исходный код** убран из «Общего»: это ссылка, а не настройка. Ссылки на репозиторий,
релизы, документацию и сообщения об ошибках остались в листе «О программе». Осиротевший ключ
`gnStarSub` удалён из трёх локалей — иначе тест «каждый ключ используется» падает.

**Ошибка по дороге, дороже самих правок.** Проверяя сборку, я сделала `git stash` в дереве, где
**параллельно работала другая сессия**. Стеш вынес её несохранённые правки из дерева (23 файла),
а пока шёл `tsc`, та сессия продолжила писать в часть файлов. `git stash pop` упал на трёх
конфликтах, и работа оказалась в двух местах: старое в стеше, новое в дереве. Восстановила так:
сохранила три новых файла в сторону, вернула из стеша все 23 файла по списку
(`git checkout stash@{0} -- <файл>`), поверх положила три новых, стеш удалила. Проверила:
`tsc` чисто, мои коммиты на месте, работа соседней сессии вернулась.

**Правило на будущее:** в дереве, где может работать вторая сессия, **не делать `git stash`,
`git checkout -- .` и `git reset --hard`**. Для проверки «собирается ли закоммиченное» надо
клонировать в отдельный каталог (`git worktree add /tmp/probe HEAD`), а не трогать общее дерево.
Это дешевле на минуту и не рискует чужой работой.

## 2026-10-26 · «Мессенджеры» врали: у привязанного Telegram показывалось «нет ни одного»

**Как нашлось:** Денис перезапустил приложение, открыл «Настройки → Мессенджеры» и увидел
«Сторонних мессенджеров нет» — на аккаунте, где Telegram привязан. Прислал снимок.

**Причина.** Панель `makeChannelsSection` в `client/Sections.tsx` была **статичным текстом**:
две строки про то, что Лика не читает чужие переписки, и подсказка «подключите бота в разделе
«С телефона»». Она ничего не спрашивала и не могла показать привязку в принципе. То есть врала
дважды: и про Telegram/MAX как способы добраться до Лики, и тем, что советовала действие,
которое человек уже сделал.

**Исправлено:** список берётся из релея (`GET /v1/link/channels` через мост десктопа) — те же
данные, что в разделе «С телефона». Привязка показывается строкой: мессенджер, имя, состояние.
Три состояния вместо двух:
- привязки есть — список;
- привязок нет и релей ответил — прежняя фраза про сторонние мессенджеры;
- релей не ответил — «не удалось проверить аккаунт». Молчание сервера не значит «пусто»; это
  отдельная ошибка, и её нельзя показывать как ответ.

Опрос `link/channels` каждые 5 секунд, чтобы привязка, сделанная с телефона, появилась сама.

**Урок в общую копилку:** статичная строка в интерфейсе врёт так же громко, как неверный код, и
живёт дольше — её никто не перепроверяет, потому что она не падает. Если страница утверждает
«ничего нет», она обязана это чем-то проверять.

## 2026-10-26 · последний вопрос закрыт: «продолжить в мессенджере» делается из мессенджера

**Что было записано как нерешённое:** кнопки «Открыть в Telegram» и «Продолжить в MAX» искали
устройство, объявившее `chat.open_channel`, а такого не объявляет никто — ни плагин, ни рантайм,
ни серверный узел. То есть кнопки были мертвы по построению.

**Как решилось.**
1. **Кнопки из веб-клиента убраны** (это сделала параллельная сессия: `renderConversationActions`
   теперь просто снимает панель). Панели над чатом больше нет — и врать нечему.
2. **Выбор беседы живёт в самом мессенджере.** В боте уже есть `/chats` (и `/chat`): он тянет
   список бесед аккаунта и переключает выбранную — то есть «продолжить эту беседу в Telegram»
   делается из Telegram, а не наоборот. Это и правильнее: человек в этот момент смотрит в
   мессенджер.
3. **Отказ инструмента `conversation_open` для telegram/max честный:** «No single connected
   messenger host is available; use the messenger menu» — и это теперь правда, а не отговорка.

**Вывод на будущее:** если кнопка в одном клиенте требует сущности, которой не существует ни в
одном, это не «недоделанная фича», а лишний путь. Сначала спросить, где действие уже делается,
и не строить второй вход. Здесь уже был `/chats`.

**Сделано заодно:** разделы «Где мы сейчас» и «Открытые вопросы» переписаны фактами на 04:30 —
что живёт на релее с сегодняшнего дня, что надо перезапустить руками, какие расхождения остались
и что вычеркнуто.

## 2026-10-26 · каталог моделей: free, чистое имя, OpenRouter за мостом

**Просили:** пометить бесплатные модели и дать фильтр; отдельной колонкой — чистое имя для
выбора; разобраться, почему в opencode не работает провайдер openrouter.

### Бесплатные — только «у нас», и их пришлось починить в трёх местах

Правило: `free` = ноль рублей **в нашем** предложении. Рынок не считается: «бесплатно у
стороннего хостинга» не значит, что клиент получит даром.

Три поломки подряд, из-за которых бесплатных не было вообще:

1. `fetch_openrouter()` ходил на `openrouter.ai` напрямую — оттуда наш сервер получает
   **403 от Cloudflare**. Теперь адрес берётся из `OPENROUTER_BASE`, по умолчанию
   `http://172.19.0.1:9443/api` (мост на 151 → Рига → openrouter.ai).
2. проверка `val <= 0` выбрасывала бесплатные модели как мусор — у них цена ровно ноль.
   Теперь отбрасывается только `< 0`.
3. каталог читал `console/providers/prices.json` из дерева, а сборщик цен писал его в
   `overrides/providers-prices-20261024/…`. Два разных файла: правишь один, читается другой.
   После `refresh_now()` копировать снимок в дерево (или указать каталогу путь из overrides).

Итог: **22 бесплатных** в каталоге, метка `free` у имени, галочка «только бесплатные».

### Цифры, которые легко перепутать

OpenRouter отдаёт **467 моделей**, а в снимке по ним **1329 строк** — это пары
«модель × единица тарификации» (вход, выход, кэш-чтение, кэш-запись), не модели.
Бесплатных **моделей 20** (17 с `:free` в id), но строк с нулём — **42**. Формулировать
надо «предложений» или «моделей», а не смешивать.

Лимит бесплатных на ключе (живой `/api/v1/auth/key`): `is_free_tier: false`,
`free_model_daily_requests: {used, limit: 1000}` — после пополнения 26.10 это 1000 в сутки,
а не 50, как записано в старом комментарии конфига opencode. Отдельно бывает **429 от
самого провайдера** (например Liquid) — это его лимит, не наш.

### Чистое имя

Колонка «Название для выбора»: человеческое имя из models.dev → Artificial Analysis → наше
меню, без продавца, дат и `preview`, с правильным регистром брендов (`GPT 6 Astra`,
`GLM 5.3 Flash`). Логика — `clean_name()` в `cloud/scripts/model_catalog.py`.

### opencode и OpenRouter: `orfree` — это мост, а не провайдер

`openrouter` в opencode не работал никогда: он ходит на `openrouter.ai` напрямую, а тот
блокирует нашу сеть по IP (`403 Access denied by security policy`). Ключ при этом живой.

Рабочий путь — провайдер `orfree` в `~/.config/opencode/opencode.jsonc`:
`baseURL: http://MacBook-ProDen.local:3460/api/v1`. Это `node gateway.mjs` из
`/Users/Denis/openrouter-gateway` на MacBook Pro Den (.7): крошечный обратный прокси через
локальный rotator `127.0.0.1:3128` (WARP/серверные выходы), ключи не хранит — заголовок
`Authorization` проходит насквозь. Проверено: 467 моделей, ответы чата идут.

Решение Дениса 26.10: **оставить как есть**, `orfree` не переименовывать.

## 2026-10-26 — Вход с телефона и состояние аккаунта на сайте

- Уточнение пользователя: после Google/Яндекс возвращало на главную с кнопкой «Вход», а не показывало ошибку провайдера. Прежняя диагностика OAuth не исправляла эту связку.
- Найдено: главная задавала next обратно на себя; её шапка зависела только от localStorage скрытого cloud iframe, а при пустом/недоступном хранилище считала пользователя вышедшим и стирала общую метку. Плюс auth-header.js отдавался с max-age=3888000 (45 дней): загрузку нового кода нельзя было гарантировать без смены URL.
- Теперь email-код, пароль и оба OAuth ставят host-only Secure HttpOnly SameSite=Lax cookie __Host-nanolika_session с той же ключевой сессией. Cookie не открывает остальные API: они по-прежнему требуют Bearer. Сайт получает только проверенное состояние/почту через /v1/auth/site-session с credentials, без передачи API-ключа в домен витрины. Точные CORS origins, no-store, запрет чужих origins, logout только POST JSON с проверкой Origin и отзывом реального ключа. Старые Bearer-входы мигрируют через защищённый endpoint, iframe остался только совместимым резервом.
- Главная после «Вход» ведёт в /web/, остальные страницы сохраняют целевой возврат. При возвращении на сайт шапка показывает проверенный аккаунт. На 11 опубликованных HTML обновлён URL auth-header.js, чтобы обойти долгий кеш. Содержимое страниц не заменялось. В отдельном репозитории сайта коммит b6cbb2a включает только шапку и версии загрузчика, остальные правки страниц остались нетронутыми.
- Проверки: 27 облачных тестов, включая cookie после Google/Яндекс и почты, CORS, CSRF/тип запроса, отзыв ключа, поддельную cookie и отсутствие доступа cookie к /v1/me. 4 браузерных регрессии: запрет localStorage iframe на mobile/desktop, миграция старого входа, одна публичная метка не даёт входа. Проверен опубликованный путь с телефона до формы с обеими кнопками; страницы и session endpoint отвечают корректно, чужой Origin получает 403. Реальное повторение пользователем пока ожидается.
- Опубликовано: relay backup /opt/nanomuse/cloud/backups/site-session-20261026-025548; site backup /var/www/u3686432/data/backups/site-session-20261026-035637. Изменены только свои участки live-файлов; новый модуль закреплён в обоих путях импорта контейнера. Установщики и боты не пересобирались/не перезапускались.

## 2026-10-26 — Мобильный вход: завершение OAuth и диагностика

- Запрос: в телефонном Яндекс Браузере не удаётся войти через Google/Яндекс; письмо с кодом пользователь впоследствии нашёл в спаме. OAuth остаётся отдельной проблемой, успешная выдача ключа сервером не доказывает вход в браузере.
- Проверено: оба перехода к официальным страницам авторизации открываются с мобильным User-Agent. В серверном журнале есть успешные OAuth-входы, но точная неудачная попытка телефона пока не сопоставлена. DNS у cloud ведёт на российский VPS, лишней AAAA нет. Отправитель кода webhook настроен, review-адреса выключены. SPF, DKIM (селектор dkim) и DMARC опубликованы; без заголовков конкретного письма причину спам-фильтра установить нельзя. Изменений почтовой доставки не делалось.
- Исправлено молчаливое возвращение к форме после OAuth: сбои получения сессии, сети и сохранения браузером теперь различаются и явно показываются. Авторизованные запросы не кешируются. Добавлен no-store к OAuth-редиректам; response-классы импортируются глобально для строковых аннотаций FastAPI. В журнале начала/отказа OAuth только провайдер и булевы признаки наличия code/cookie/state, без значений секретов. Защита state и PKCE не ослаблялась.
- Проверки: 3 изолированных облачных теста Google/Яндекс с выдачей рабочей сессии и отказом повторного callback; 4 браузерных сценария 390px: успешное завершение, ошибка сессии, сеть и запрет localStorage. Ruff прошёл. WebKit этого Playwright не поддерживает macOS 13 на текущем Mac, тесты Chromium с мобильным контекстом не названы реальным тестом Safari/Яндекс.
- Опубликовано на российском relay: бэкап /opt/nanomuse/cloud/backups/mobile-auth-20261026-022738. Патч применён только к своим участкам текущих live-файлов, сохранены расхождения превью, которые уже были на сервере. Боты не перезапускались. Проверочные страницы и список провайдеров отвечают 200.
- Остаётся: пользовательская повторная проверка обоих провайдеров на телефоне. До неё исходный мобильный сбой не считается подтверждённо устранённым. Для спама нужны Authentication-Results/Received-SPF конкретного письма; обещаний доставки во Входящие нет.

## 2026-10-26 · письмо о подключении, QR на три минуты, переспрос «выйти?» и отвязка в приложении

**Просили:** сделать логичнее, добавить отвязку сразу в приложении и пройти сценарий «сканирую
QR в Telegram, попадаю в бота, он переспрашивает: хотите выйти? да/нет, и подключается к
новому профилю». Плюс письмо о новом подключении — согласовано, делать всегда.

**Главное, что было сломано: код в занятом чате молча уходил модели.** `answer_code` сначала
спрашивал у релея `GET /v1/bot/link` и возвращал пустую строку, если чат уже чей-то. То есть
человек снимал свежий QR и упирался в стену: код даже не доезжал до релея. Теперь код
предлагается релею всегда, а релей отвечает `link_taken` **с тем, чей это аккаунт** (`taken:
{label, channel, own}`) — и это стало вопросом, а не отказом.

**Сценарий из задачи теперь работает так:** снимаешь QR → бот видит, что чат привязан к
аккаунту `ba***@gmail.com` → присылает две кнопки «Выйти и подключить» / «Оставить как есть».
«Да» отвязывает старый аккаунт и сразу подключает к новому, одной кнопкой. Если чат сам
является аккаунтом (с него аккаунт начался), кнопок нет: это удаление аккаунта, и его место в
приложении — бот говорит это словами.

**Письмо.** Каждая привязка пишет на почту аккаунта: кто, когда, что это значит, как отвязать.
Раньше единственным следом была строка `account.linked` в журнале событий, которого человек не
видит, — чужой чат мог подключиться и тратить кассу незамеченным. Шлётся всегда (привязка
редкая, цена незамеченной высокая). Сбой письма пишется в лог и не отменяет привязку.
Метод `send_channel_link` есть у обоих отправителей (SMTP и вебхук).

**QR живёт 3 минуты** (`CHANNEL_START_TTL_S`), набранный руками код остался на 10. QR — это
фотография, которую можно сделать украдкой; у того, кто подключает, телефон уже в руке.

**Отвязка в приложении на компьютере:** маршрут `link/unlink` в мосте, `linkUnlink` в клиенте
релея, кнопка «Отвязать» у каждой привязанной строки в панели подключений, с подтверждением.
**Чтобы она появилась, приложение надо перезапустить** — плагин уже собран и разложен в обе
копии (`payload/…/dsh-nanomuse` и `~/.nanolika/desktop/plugins/dsh-nanomuse`).

**Проверено вживую на 151 (релей и бот перезапущены):** `taken` приходит с маской владельца
(`{"label":"tg ***0777","own":false}`); QR отдаёт `expires_in_s: 180`, код по-прежнему 600;
расшифровка адреса аккаунта для письма работает на настоящей строке (`bab***@gmail.com`).
Тестовые аккаунты и привязки убраны; привязка Дениса на месте, бот находит его аккаунт
(`created: false`). Тесты: 495 релея, 387 плагина, 158 каналов.

**Чему научилась на своей же ошибке.** В прошлый раз снесла живую привязку, проверяя отвязку на
«тестовом» чате. Теперь для разрушительных проверок сначала создаётся отдельный чат, и только
на нём что-то ломается. Этот заход так и сделан.

**Осталось:** письмо ни разу не уходило живому человеку — путь проверен до отправки
(расшифровка + сборка письма + юнит-тесты), но первое настоящее письмо придёт при первом
подключении чата. Стоит глянуть, как оно выглядит в Gmail, и поправить вёрстку, если поедет.

## 2026-10-26 · выход из аккаунта в Telegram и MAX: отвязка чата

**Просили:** продумать механизмы выхода из аккаунтов в Телеге и Максе — «вот чего нет».

**Чего не было.** Привязать чат было можно, отвязать нельзя: `db.remove_alias` написан и
рабочий, но маршрута к нему не существовало ни одного, а звать его было некому. Выход из
приложения снимал ключи, а привязка бота живёт отдельной строкой в `account_aliases`:
человек нажимал «выйти со всех устройств» и считал, что вышел везде, а бот продолжал
отвечать с его балансом и памятью. Это не недоделка, а дыра.

**Сделано:**
- релей `DELETE /v1/link/channels/{channel}` — отвязывает чаты одного мессенджера, отвечает
  `{unlinked, channel, before, left}`; `POST /v1/bot/unlink` — один чат по секрету бота;
- бот: `/unlink` (и `/отвязать`, `/развязать`) спрашивает подтверждение и отвязывает только
  со второго раза; пункт «Отвязать этот чат» в меню настроек идёт тем же путём;
- кабинет: кнопка «Отвязать» у каждой привязанной строки, с объяснением последствий;
- «выйти на всех устройствах» теперь говорит правду: привязанные чаты остаются;
- удаление аккаунта чистит `account_aliases` и `link_codes` — раньше строки оставались и бот
  отвечал удалённому человеку.

**Защита от необратимого.** Чат, с которого аккаунт начался, отвязать нельзя: это сам
аккаунт, и отвязка унесла бы историю и кассу. Отказ `409 channel_is_the_account`, бот
объясняет словами. Удаление аккаунта остаётся в приложении, где ему и место.

**Своя же ошибка, найденная проверкой вживую.** Первая версия сравнивала мессенджер аккаунта
с мессенджером чата — и отказывала *любому* Telegram-чату того же аккаунта. Нашлось только
на живом сервере, тесты этого не видели. Исправлено сравнением по `id_hash`: отказать может
только тот чат, с которого аккаунт начался. Коммит `37b32b1a`, тест
`test_a_second_chat_of_the_same_messenger_can_still_leave`.

**Отдельно: я снесла живую привязку Дениса посреди работы.** Проверяла защиту на чате,
который считала «создателем аккаунта», а это оказалась его настоящая привязка `@BabichDen`
к его аккаунту. Восстановила вручную строкой `account_aliases`: хеш считается как
`hmac(CLOUD_SECRET, "telegram:<id чата>")`, `surface=telegram`, `identifier=@BabichDen`;
id чата подтвердила по маске `tg ***8722` в записи. Проверила: бот снова находит его
аккаунт, `created: false`. Вывод на будущее:
**не проверять разрушительные ручки на данных, которых не видел до себя** — для этого нужен
отдельный тестовый чат.

**Грабли деплоя (важно для следующего раза).** Релей берёт `service.py` не из дерева, а из
`/opt/nanomuse/cloud/overrides/auto-routing-20261025/`, а `db.py` и `channel_links.py` — из
собранного образа. Бот берёт **всю** папку каналов из `/opt/nanolika-bot/overrides/channels/`.
То есть правка дерева на 151 не видна ни там, ни там. Порядок: копировать в оверрайд релея,
затем `docker compose build relay && docker compose up -d relay`; для бота — скопировать в
`/opt/nanolika-bot/overrides/channels/` и `docker restart nanolika-bot`. Бэкап старой папки
каналов лежит в `/tmp/channels-backup-unlink`.

**Проверено вживую:** второй чат того же мессенджера отвязывается, чат-создатель получает
`channel_is_the_account` и аккаунт остаётся целым; незнакомый чат — `not_linked`; 485 тестов
релея и 40 тестов каналов зелёные.

**Осталось:** то же для приложения на компьютере — там отвязка пока только через бота и
кабинет; и `chat.open_channel` по-прежнему никто не объявляет (кнопки Telegram/MAX в вебе
работают через QR, а не через хаб).

## 2026-10-26 — QR в приложении, Enter и модели в веб-чате

- Запрос: заменить ручной код подключения Telegram/MAX в компьютерном приложении на QR; отправлять сообщения веба клавишей Enter и добавить выбор модели.
- Приложение: PhoneLink показывает выбранный мессенджер, локально построенный QR и прямую ссылку. Хост передаёт link/start под ключом текущего аккаунта. Срок ссылки, обновление и автоматическое подтверждение привязки; старые кодовые endpoints сохранены. Механизм выхода не менялся.
- Веб: Enter отправляет, Shift+Enter переносит строку; IME и повтор клавиши не отправляют. Список доступных текстовых моделей под полем ввода, выбор хранится отдельно для аккаунта в браузере. Тарифы и расчёты остаются серверными. В запрос и продолжение после инструмента уходит выбранная модель.
- Проверено: build и typecheck модуля приложения; целевые 15 тестов relay/QR и 2 веб-проверки; Playwright 390/768/1440, повторная отправка кнопкой, Enter/Shift+Enter, сохранение выбора, отсутствие горизонтального скролла. Полная проверка нашла устаревшие строки ручного кода, они удалены из словарей. После удаления старых строк полный набор прошёл: 387/387.
- Веб опубликован без перезапуска релея. Бэкап: /opt/nanomuse/cloud/backups/web-composer-20261026-020519. Версионированы вложенные импорты, чтобы старый service worker не подменял новый каталог старым модулем.
- Установщики и мобильные приложения не собирались. Установленный desktop bundle не заменялся; QR-интерфейс попадёт в следующую сборку. Реальное сканирование из новой компьютерной сборки пока не подтверждено.

## 2026-10-26 — Подключение Telegram и MAX по QR и прямой ссылке

- Запрос: убрать ручной ввод кодов; сделать только подключение, не трогая параллельную работу над выходом из старого аккаунта.
- В кабинете «Устройства и каналы» окно Telegram/MAX создаёт QR и кнопку с одной и той же одноразовой ссылкой. Секрет 120 бит, хранится хеш, срок 10 минут, повторное использование запрещено. Никаких сторонних QR-сервисов.
- Боты обрабатывают ссылку до модели: привязка к тому же аккаунту и допуск отправителя в бот без дополнительного кода. Права на инструменты/компьютер не расширяются. MAX сохраняет payload события bot_started.
- Проверено: 54 теста каналов, 23 облачных теста, 4 Node-проверки; Playwright 390/768/1440: QR/ссылка, автостатус подключения, прекращение опроса после закрытия, MAX и Telegram отдельно. Настоящее сканирование пользователем ещё не подтверждено.
- На российском VPS включены только наши изменения подключения; чужие правки выхода не копировались. Резервная копия /opt/nanomuse/cloud/backups/qr-connections-20261026-014601. После перезапуска исправлен второй путь импорта нового модуля relay (site-packages); постоянные подключения файлов добавлены в compose.
- Компьютер: установка и вход с той же почтой; автоматический вход по nanolika:// не добавлялся, установщики не собирались.

## 2026-10-26 · первый вход в вебе: подключения только по желанию

Пользователь указал на три обязательноподобные кнопки над чатом и техническую ошибку
нового аккаунта без устройств. Уточнил, что браузер должен быть самостоятельным, а
подключение Telegram, MAX и компьютера необязательным.

Сделано: постоянная панель каналов удалена из переписки. В меню аккаунта добавлен
переход «Устройства и каналы». Там находятся отдельные инструкции, ссылки на ботов и
загрузку приложения, запрос одноразового кода на десять минут, копирование и проверка
подключения. Компьютер предлагается авторизовать той же почтой. Код запрашивается
только нажатием пользователя. Открытие инструкции не создаёт беседу и не вызывает
модель. Объединение нескольких аккаунтов в этот первый шаг не включено по уточнению
пользователя. Инструкции модели больше не ссылаются на удалённые кнопки над чатом.

Проверено: 19 pytest-проверок кодов привязки и веб-оболочки, 10 Node-проверок
переключения и определения связанных каналов. В Playwright проверены 390, 768 и
1440 px: чистый чат, добровольное подключение из кабинета, выдача кода по нажатию,
ссылки Telegram/MAX, отсутствие горизонтальной прокрутки и ошибок JavaScript.
На опубликованных статических файлах повторён сценарий с фиктивным аккаунтом и
перехваченными API, без реальных списаний, вызовов модели и сообщений клиентам.
Реальная привязка личного мессенджера остаётся действием самого пользователя с его кодом.

Опубликованы статические файлы, релей и боты не перезапускались. Исходники сохранены
в репозитории. Грабли: действующий релей обслуживает веб из установленного каталога
/srv, а каноническая папка дополнительно смонтирована под /app. Обновлена именно
обслуживаемая копия и источники отдельных bind mounts. Для новых файлов добавлены
постоянные mounts, конфигурация Compose проверена. При записи в источник файлового
mount нельзя заменять inode, иначе контейнер продолжит читать прежний файл.

## 2026-10-26 · журнал стал полным: хроника проекта и живое состояние

**Просили:** сохранить историю всего проделанного для будущих сессий; если такого файла нет —
сделать, чтобы все сессии с него начинали.

**Что было.** Файл уже существовал и был подключён правилом в `AGENTS.md`, но покрывал только
25 и 26 октября. Всё, что было раньше (1245 коммитов, 54 тега, 49 версий с апреля), в нём
отсутствовало: новая сессия видела два дня и не знала, откуда взялся продукт.

**Сделано:**
- раздел **«Как всё сложилось»** — хроника от пустого репозитория (25.04) через чужое
  приложение, свой рантайм (22.09), GPL (24.09), четырнадцать выпусков за день (25.09), облако
  и консоль (конец сентября), 1.0.0 Keel (09.10), ребрендинг в NanoLika (17.10), деньги и
  поставщики (20—24.10) до сегодняшнего дня; отдельным абзацем — чем всё это проверялось и
  какие грабли уже записаны;
- раздел **«Где мы сейчас»** — таблица живого с проверенными HTTP-кодами, версии трёх частей,
  остатки поставщиков;
- раздел **«Открытые вопросы»** — семь пунктов одним списком вместо поиска по записям;
- указание в шапке: начинать сессию с этих двух разделов, датированные записи читать за
  причинами и граблями.

**Три расхождения, найденные при сверке (важнее самого текста):**
1. плагин 1.0.5 против оболочки 1.0.8 — правило проекта требует одну версию на оба
   (`harness/dsh-nanomuse/package.json` против `harness/desktop/package.json`);
2. свежий dmg (00:04, 419 172 003 байта, sha256 `d6d18908…`) не выложен, на сервере сборка
   23:38 (419 165 403), а манифест обещает 419 021 740 — не совпадает ни с чем. zip при этом
   сходится точно, поэтому обновление скриптом может ругаться только на dmg;
3. 108 коммитов не отправлены: `origin/feat/voice-ru` стоит на `fe88c715`, вся работа с 23.10
   живёт на одной машине.

**Проверено вживую 26.10, 00:40:** сайт 200, веб-Лика 200, манифест 200 (версия 1.0.8),
страница цен без пароля 401, старые `/app/prices/` и `/app/menu-admin/` 404, релей 0.26.0
поднят 25.10 17:02, zip на сервере совпадает с манифестом по sha256 и размеру.

**Убрано из журнала:** адрес посетителя в примере уведомления заменён на `<адрес>` — правило
проекта запрещает приватные хосты и адреса в документации.

**Осталось:** два расхождения (версия плагина и раздача dmg) не исправлены, только записаны —
это отдельная работа.

**Дополнено тем же вечером, 00:50:** ветка `feat/voice-ru` отправлена на GitHub (109 коммитов,
`origin` на `90426724`). Указатели на журнал добавлены в глобальные правила всех инструментов на
этой машине — Claude Code, Codex, opencode, nanoLika Desktop — и в карту проектов
(`~/Dev/nanolika-map`), чтобы правило читалось из любой сессии, а не только из Лики.

## 2026-10-26 · итог ночи: 1.0.8 на .7, приветствие по-русски, механизм обновления

**На .7 установка прошла полностью.** 1.0.8 встала, оба разрешения выданы вручную через «+» в
панелях («Универсальный доступ» и «Запись экрана»), пробный снимок получен (1596×672), мышь
двинулась. Вывод: на macOS 13.7.8 автоматический запрос helper'а строку в панели не создаёт —
только через «+» руками. Это ограничение старой системы, не наш код.

**Приветствие было английским при русском интерфейсе.** В `src/firstrun.ts` `introLines()`
знала только `zh` и `en`. Добавлена русская пара абзацев, английский остался по умолчанию.
Коммит `447a0b04`.

**Как устроено обновление на macOS (важно для «стрелочки вниз»).**
- Манифест `https://cloud.nanolika.ru/dl/mac/latest.json`: версия, дата, заметки, ссылки на zip и
  dmg для `x64` и `arm64` со sha256.
- `harness/desktop/scripts/update-lika.py` — готовый клиент обновления: сверяет версию с
  Info.plist, качает zip своей архитектуры, проверяет sha256, закрывает приложение, старую копию
  в Корзину, распаковывает в /Applications, запускает. Умеет `--check`, `--dry-run`, `--force`.
- В самом приложении обновление есть **только для Windows** (`windows-updates.ts`,
  `NsisUpdater`). Для macOS внутри приложения кнопки нет: обновляются скриптом.
- Пересборка целиком нужна только при смене нативного кода (Electron-оболочка, Python-runtime,
  helper). Правка плагина (`harness/dsh-nanomuse/src`) — это файлы в `Resources/dsh/node_modules/
  dsh-nanomuse`, их достаточно заменить и перезапустить приложение; .dmg пересобирать не нужно.
## 2026-10-26 · ночь: пересборка 1.0.8 с кнопками входа и разрешением экрана

**Просили:** пересобрать бандл; сделать с записью экрана то же, что получилось с универсальным
доступом (чтобы строка helper'а появлялась в панели); добавить вход через Яндекс и Google как на
сайте; добавить мелким шрифтом «пропустить регистрацию»; исправить «одна модель» на правду о 400
моделях.

**Сделано:**
- онбординг: две кнопки входа через Яндекс и Google (`frSignInGoogle`, `frSignInYandex`), открывают
  системный браузер через `openLink`, возврат по схеме `nanolika://`; мелким шрифтом
  «Пропустить регистрацию и настроить позже» (`frSignInSkip`); текст про модели исправлен на
  «доступны сотни моделей: текст, код, картинки, видео и озвучка»;
- стили `.nm-fr-sso`, `.nm-fr-sso-btn`, `.nm-fr-skip-fine` — вставлены **внутрь** строки CSS
  (первая попытка вставить после закрывающей скобки сломала файл, TypeScript не собирался);
- запись экрана: `nanomuse:permissions:request` теперь просит helper трижды (`/request` с
  `what: screen`), прежде чем открыть панель. Причина: на чистой машине (macOS 13.7.8, .7) строки
  `NanoLika Computer Use` в панели «Запись экрана» нет вовсе, и включить нечего — tccd добавляет
  программу в список только по её собственному запросу;
- пересобран бандл 1.0.8 x64, подписан ad-hoc, `.dmg` 419 МБ залит на сервер.

**Проверено:** плагин 385 тестов зелёные, `tsc` чисто, в собранном `app.asar` новые строки
присутствуют, подпись `Signature=adhoc` у приложения и у helper'а.

**Осталось:**
1. Проверить на .7, что `NanoLika Computer Use` появился в «Запись экрана» после нового запроса.
2. Developer ID по-прежнему нужен для тихой раздачи; Apple блокирует заявку из-за турецкого
   региона (нужен турецкий номер и адрес).

## 2026-10-25 · ночь: подпись macOS и раздача с сайта

**Просили:** разобраться, почему разрешения macOS просят заново; подписать приложение так, чтобы
оно работало у людей с сайта.

**Причина слетающих разрешений — ad-hoc подпись.** Проверено командой `codesign -d -r-`:

```
ad-hoc:         designated => cdhash H"d1ea016f..."     ← привязка к ХЕШУ содержимого
с сертификатом: designated => identifier "io.github.nanomuse.desktop" and certificate root = H"b91bbfdc..."
```

Две РАЗНЫЕ сборки с одним сертификатом дают одинаковое designated requirement, поэтому
разрешение переживает пересборку. При ad-hoc хеш меняется каждый раз, и macOS забывает
приложение.

**В проекте это уже предусмотрено.** `scripts/desktop-app/package-mac.sh` принимает сертификат
через `MAC_CERT_P12_BASE64` и `MAC_CERT_PASSWORD`, ищет в нём строку `Developer ID Application`,
подписывает изнутри наружу (фреймворки, `Resources/dsh`, `runtime`, потом helper и бандл) и
умеет нотаризацию при наличии ключа App Store Connect (`APP_STORE_CONNECT_*`). Локальный
самоподписанный сертификат подойдёт, если назвать его `Developer ID Application: NanoLika Local`:
скрипт ищет эту строку буквально.

**Чего не бывает.** iOS-сертификаты (`iPhone Distribution`, Enterprise) для macOS не годятся:
другой тип, `codesign` их не примет. Чужие ключи из публичных репозиториев (iDeploy,
free-ios-certificates, MoKhajavi75, NexSideloading) Apple отзывает, а использование ключа банка
или университета — уголовная история, не «серая зона».

**iOS-приложение подписано и живёт в TestFlight** под командой `TN43QYW8K4` (апстрима, Guangyi
Liu), сертификат `Apple Distribution` до 2027-10-03. Для macOS нужен свой Developer ID.

**Apple блокирует заявку до оплаты.** На шаге «Review and Accept» → Continue приходит
`An unknown error occurred`. Регион аккаунта — Турция, значит нужен турецкий номер и адрес для
подтверждения, а не карта. До денег дело не доходит.

**Сделано:** страница скачивания уже на сайте и отдаёт сборку 1.0.8
(`/dl/mac/latest.json` → `NanoLika-Desktop-1.0.8-mac-x64.dmg`, 419 МБ, HTTP 200). Добавлена и
задеплоена инструкция «macOS говорит, что не может проверить разработчика»: правый клик →
«Открыть», либо `xattr -d com.apple.quarantine /Applications/NanoLika.app`.

**Что осталось:**
1. Турецкий телефон и адрес для Apple — единственное, что мешает Developer ID.
2. Локальный сертификат `Developer ID Application: NanoLika Local` создан, ключ импортирован в
   связку, сертификат на рабочем столе (`~/Desktop/nanolika-local-cert.pem`). Чтобы заработал,
   нужно доверие: Связка ключей → сертификат → Доверие → Подпись кода → Всегда доверять.

## 2026-10-25 · ночь: правая панель (браузер), каталог моделей, склад

**Просили:** открыть браузер справа от чата, как в opencode/ChatGPT; сделать одну страницу цен
вместо двух; собрать данные о моделях из публичных источников; завести DeepSeek обычным
поставщиком; не прятать прочерки, а добывать данные.

### Браузер в правой панели — где включается (главное)

Вкладка «Браузер» (`⌘⇧T`) — это плагин `@deepseek-ai/dsh-client-ui-sidebar-browser`. Мост
`globalThis.dshDesktop.browser` в оболочке был готов ещё в `4f17e26d`, а вкладки не было.
Причина в бандле `dsh/node_modules/@deepseek-ai/dsh-web-app/cordis.patch.yml`:

```yaml
- id: ui-sidebar-browser
  disabled: !!js "ctx.get('profileContext')?.name !== 'desktop'"
```

`profileContext.name` — это **имя профиля**, а наш профиль зовётся `nanolika` (константа
`PROFILE` в `harness/desktop/src/main.ts`). Условие всегда истинно → вкладка выключена.

**Как проверять, а не гадать.** Не искать имена плагинов в `dsh-web-frontend/dist/assets/*.js` —
клиентские плагины грузятся через `dsh-client-modules`, их имён в бандле нет и быть не должно.
Правильный путь: смотреть `package.json` пакета (`dsh.client.inject`) и строки с
`profileContext` в бандлах:

```sh
grep -rn "profileContext')?.name" harness/desktop/dsh/node_modules/@deepseek-ai/*/cordis.patch.yml
```

**Что сделано:** условие снято в самом бандле шагом сборки `scripts/patch-dsh.mjs`
(пункт 3b), коммит `b546c000`. Профиль `~/.nanolika/desktop/profiles/nanolika/cordis.patch.yml`
тоже содержит `disabled: false` для этой строки — дубль безвреден, но правка в бандле нужна,
чтобы вкладка приезжала с новой сборкой, а не жила только на этой машине.

Телеметрия (`desktop-product-telemetry`, `product-analytics`) гаснет по тому же признаку и
**остаётся выключенной**: это не наши данные, включать не надо.

Порядок, если вкладка снова пропала:

1. `grep -n "ui-sidebar-browser" -A2 <dsh>/node_modules/@deepseek-ai/dsh-web-app/cordis.patch.yml` —
   там должно быть `disabled: false`;
2. `node scripts/patch-dsh.mjs` — переставить, если бандл пересобрали;
3. перезапустить приложение (профиль читается только при старте);
4. правый сайдбар: «Файлы рабочей области» / «Создать терминал» / «Браузер».

### Каталог моделей — где данные

`cloud/scripts/model_catalog.py` сводит три источника: **LiteLLM** (цены за токен и поля
возможностей, ~4300 моделей), **models.dev** (тип модели, модальности, контекст,
open_weights, ~9000 моделей, 232 провайдера) и **Artificial Analysis** (индекс качества).
Наш снимок цен добавляет рубли по поставщикам.

Грабли, на которые уже наступили:

* имена не сходятся между источниками (`seedance/2.5` у продавца,
  `fal_ai/bytedance/seedance-2.5/text-to-video` в LiteLLM,
  `bytedance/seedance-2.5` в models.dev) — есть `slug_variants()`, которая раскладывает имя
  на варианты; без неё половина строк была с прочерками;
* у видео и картинок цена **за секунду или картинку**, а не за токен — читается в `unit_usd`,
  иначе в ячейке прочерк при известной цене;
* «умеет» нельзя выводить из модальностей входа: у видео на входе текст (промпт), и это не
  «умеет текст». Считается один раз в `finalize_caps()` из режима модели;
* склейка имён «на всякий случай» плодит дубли (было 14059 строк вместо 8923) — варианты
  только там, где частей ровно две.

**Российские вендоры** (GigaChat, YandexGPT, T-Bank) машинно не снимаются: Сбер отдаёт тарифы
JS-приложением, Яндекс — за SmartCaptcha. Для них `cloud/scripts/vendor_prices.py` — ручной
прайс с полями «цифра / когда снято / откуда».

Сборка на сервере: `nanolika-model-catalog.timer`, ежедневно 05:20.

### DeepSeek — обычный поставщик, а не «наш шлюз»

Была ошибка: остаток DeepSeek показывался как квота шлюза new-api (998 788 USD). Правильно —
`CLOUD_PROVIDERS` содержит `deepseek: {base: https://api.deepseek.com, key_env: DEEPSEEK_KEY}`,
баланс через `/user/balance` в долларах. Живой остаток: **−0,04 ¥**, `is_available: false` —
модели, которые идут напрямую, не оплачены. Пока не пополнено, они работать не будут.

### Склад (store) — что не лезет в установщик

`https://cloud.nanolika.ru/store` — навыки, капсулы кода, описания MCP; каталог
`index.json`, сборка `cloud/scripts/store_index.py`, капсулы
`cloud/scripts/store_build_capsule.py` (бинарники в git не лежат). В промпте модели есть
раздел «чего нет в этой сборке» со ссылкой и точной командой; после установки строка исчезает.
Первая капсула — `ocr-vision` (macOS Vision через pyobjc, 2,9 МБ).

## 2026-10-25 · вечер: снимки в чате, копии плагина, разрешения экрана

**Просили:** убрать картинки, которые появлялись в чате; сравнить копии плагина с исходником;
разобраться с вынесенным блоком, у которого разрешение на управление.

**Убраны снимки из переписки.** Модуль `client/Trajectory.tsx` вставлял в ленту карточку
траектории с кадрами после каждого движения рук. Выключено одной строкой в
`src/client/index.ts` (`ctx.effect(() => renderTrajectory(...))` заменено на `void
renderTrajectory`), коммит `ad3d4e2e`. Модуль и ручка хоста `/trajectory` остались: вернуть
можно той же строкой.

**Копии плагина разъехались.** Их три: исходник `harness/dsh-nanomuse`, копия в
`payload/dsh/node_modules/dsh-nanomuse` (её грузит dsh, на неё смотрит симлинк профиля) и
внешняя `~/.nanolika/desktop/plugins/dsh-nanomuse`. Внешняя отставала на 34 файла (старые
чанки). Правило: после `node build.mjs` синхронизировать **обе** копии, иначе приложение
возьмёт старую.

**Разрешения macOS.** Вынесенный блок (helper) живёт в
`~/Library/Application Support/nanolika-desktop/Computer Use/NanoLika Computer Use.app` и
своё разрешение имеет: `GET /status` отдаёт `screen: granted, accessibility: true`, а
`POST /screenshot` возвращает живой JPEG 1788×1118. Проверено картинкой, не по логам.
CDHash внешней копии и копии в бандле совпадают (`4e26d7ba…`).

Отдельно выяснено: в пользовательской базе TCC строк `ScreenCapture` и `Accessibility` нет
вовсе — они в системной базе, куда без пароля не заглянуть. Поэтому «галочка стоит», а
записи не видно.

**Поправка 25.10, 20:10.** Читать `AppleEvents = 0` из пользовательской базы как «нажимать
кнопки в чужих окнах нельзя» было неверно: Денис тут же показал, что руки нажимают всё.
Наши руки работают не через System Events, а через собственный helper (CGEvent: клик по
координатам, ввод текста), поэтому `AppleEvents` им не нужен вовсе. Запись про «нужно
разрешить Управление компьютером» снята: это касалось бы только сценариев на AppleScript,
которых у нас нет.

**Почему спрашивало заново.** Оболочка Electron тоже просила Screen Recording и пробовала
`desktopCapturer` (ветка в `requestScreenRecording` и стартовый `ensureMacPermissionsAtLaunch`).
macOS ставила на панель строку **приложения** с разрешением, которым снимок не пользуется,
потому что снимает helper. Коммит `f695d562`: приложение больше не просит запись экрана и не
пробует `desktopCapturer`, единственный проситель — helper. `tsc` и 162 теста оболочки чисто.

**Итог по разрешениям (проверено вживую 19:45).** После включения тумблера в панели:
`GET /status` → `screen: granted, accessibility: true, capture: CoreGraphics`,
`POST /screenshot` → 200, живой JPEG 1788×1118. Вынесенной блок снова снимает экран.

**Урок, который дорого стоил.** `tccutil reset ScreenCapture <bundle>` не «чистит устаревшую
запись», а **снимает разрешение**: helper начал отвечать `screen: denied`, снимок 403, и
человеку пришлось включать тумблер заново. Никогда не применять `tccutil reset` к рабочему
разрешению: сначала проверить живым запросом, действует ли оно.

**Что осталось:**
1. Постоянное решение проблемы слетающих разрешений — подпись Developer ID ($99/год): ad-hoc
   подпись меняет CDHash при каждой пересборке, и разрешение приходится выдавать заново.

**Не «чинить» разрешения, которые не мешают.** Прежде чем просить у человека новое
разрешение, проверить живым действием, что его действительно не хватает: `POST /screenshot`
для экрана, реальный клик для рук. `AppleEvents` — пример разрешения, без которого всё
работает, и просить его было лишним.

## 2026-10-25 · вечер: деньги поставщиков, страница цен, безопасность

**Просили:** разобраться, почему приходят `Insufficient Balance / QUOTA`; починить
переключение между поставщиками; сделать страницу цен понятной (колонки поставщиков,
вендор, контекст, фильтры, группы моделей); убрать цены с публичного адреса; прогнать
внешний аудит; разобраться со сторожем, который сыпал ложными тревогами.

**Причина ошибки 402.** Шлюз `new-api` держит приоритеты каналов в двух таблицах сразу,
`channels` и `abilities`, и загрузчик читает **`abilities`**. Правка `channels.priority`
ничего не меняет. Канал `deepseek-paid` стоял первым с балансом **−0,04 ¥** и отвечал 402,
а failover на 402 в new-api нет: канал только отключается по `auto_ban`. Как поднять
живого поставщика: `UPDATE abilities SET priority=100 WHERE channel_id=7` плюс то же в
`channels`, плюс `auto_ban=1` обоим и опции `AutomaticDisableChannelEnabled` /
`AutomaticEnableChannelEnabled`.

**Что сделано:**
- релей: `unlimited` в аккаунте теперь снимает лимит числа бесед (`sync.py`, тест);
- релей: ответ соседней моделью, когда все поставщики отказали (`provider_routing.py`
  `substitutes`, `MAX_MODEL_SUBSTITUTES`, в ответе `nanomuse.substituted`);
- сторож балансов: `/opt/nanolika-alerts/provider-balance.py` и таймер
  `nanolika-balance.timer` на 151, каждые 5 минут, пишет в Telegram при смене состояния;
- цены: нейтральный адрес `https://cloud.nanolika.ru/<случайные 24 символа>/` за
  basic auth, данные `/v1/admin/prices` тоже за паролем; старые `/app/prices/` и
  `/app/menu-admin/` отдают 404. Логин и пароль — в `/root/ops-access.txt` на 151;
- страница цен: колонки всех поставщиков с ценой входа и выхода, подсветка «сейчас»,
  вендор, контекст, максимальный выход, фильтры с состоянием в адресе, группы-чипы
  (Текст, Код, Картинки, Видео, Озвучка, Эмбеддинги);
- безопасность: Caddy получил `handle @operator_only` вместо `respond` (respond
  сортируется после handle и не выполнялся — из-за этого админка была открыта всем),
  `chat.nanolika.ru` поднят в Caddy, на сайте nanolika.ru появились HSTS, CSP,
  X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy; nuclei 3.11.1 и
  ffuf 2.3.0 прогнаны по cloud и сайту — 0 находок;
- сторож трёх Лик: проверял `chat.nanolika.ru`, которого не было в Caddy. Домен поднят,
  прогон зелёный; MAX показывал `error` — сейчас `connected`;
- уведомления о заходах: счётчик пишет `ip` и `ua`, `notify.py` на Риге показывает
  «08:25 · /index.html · <адрес> · пришёл с cloud.nanolika.ru» и группирует пачку.

**Проверено вживую:** ответ через релей `«Привет»`, `finish=stop`; тарифы 10/20/30;
`/web/app.js` содержит облачко; балансы видны в уведомлении; порты снаружи — только
22, 80, 443.

**Что осталось:**
1. **Поиск в приложении ходит мимо релея** — прямо на `api.deepseek.com`, где −0,04 ¥,
   поэтому `web_search` падает с 402. Нужно поменять эндпоинт в настройках плагина
   (Settings → Plugins → Web search) на наш релей. Это решение владельца.
2. **Пополнить прямой DeepSeek** или держать канал 5 выключенным.
3. **Правка цены продажи на странице цен не сохраняется**: маршрута, который пишет
   `sell_*`, в релее нет. Нужен `PUT /v1/admin/prices/models/{id}/sell`.
4. **Подпись Developer ID.** Ad-hoc подпись меняет CDHash при каждой пересборке, и
   выданные разрешения macOS перестают действовать. Это причина повторяющихся просьб
   дать «Запись экрана» и «Универсальный доступ» заново. Единственное постоянное
   решение — сертификат Apple Developer ($99/год).
5. **Веб-клиент после правок облачности вживую не прогоняли** (правки задеплоены).
6. **Облачность в десктопе** собрана и лежит в payload, но приложение не перезапускали.

## 2026-10-25 · день: привязка, идентичность каналов, темы Telegram

**Просили:** доделать привязку телефона по коду; убрать свалку тем в Telegram; сделать
ответы красивыми (HTML, отдельными сообщениями); разобраться с облачностью бесед.

**Что сделано:**
- дверь бота стала парой «мессенджер + id» (`23c403b6`): MAX больше не наследует
  телеграмный аккаунт с тем же числом, документация в `docs/cloud.md` и `9239cfaa`;
- перебор кодов привязки ограничен окном попыток (`df9a9464`);
- `manager.py` сведён с серверной версией: вернулись `_cloud_key_for`, `_send_to_thread`
  (уведомление об исчерпанном запасе снова доходит), русские «нет/стоп» как отказ,
  потерянный `chat_picker.py` (`72097012`, `4cf56159`, `a76fb293`, `9cea2c4c`);
- темы: служебное сообщение больше не создаёт ни беседу, ни тему; автотема только для
  беседы, где писал человек; всё, что начинается с feed, даёт одну тему `Feed`
  (`93e07bf3`);
- ответы в Telegram: HTML-разметка и резка на отдельные сообщения (`4297322d`);
- облачность беседы: правило «главный всегда в облаке, пришедший из облака только
  удаляется целиком, локальный включается и выключается» (`0ddc5349`), значок облачка и
  меню в десктопе (`3cf5384b`, `2a979a6d`), то же в веб-клиенте (`d53e3e60`,
  `7df7108d`).

**Грабли, которые стоили времени:**
- файлы веб-клиента монтируются в контейнер **поштучно** из
  `overrides/site-auth-…/web_client/`, поэтому обновление дерева не видно на живом
  сайте. То же с `console/prices`.
- в Caddy `respond` идёт **после** `handle`: правило «404 для всех» надо писать как
  `handle @matcher { respond 404 }`, иначе оно не выполняется.
- в таблице `accounts` есть колонка `unlimited`, но лимиты её не читали.

## 2026-10-25 · утро: цены и тарифы

- Розница считается от закупки: базовая ставка = 2,857 × закупка в рублях, чтобы при
  скидке 30 % оставалось 100 % прибыли. Скидки тарифов 10 / 20 / 30 % заданы в `PLANS`
  (в `.env`, а не в `payments.py`: страница `/v1/config` читает их оттуда).
- Курсы: `RUB_PER_USD=80.5381`, `USD_CNY=6.6805`, `TOPUP_FEE_PCT=6` → 1 ¥ = 12,779 ₽.
- Бесплатный дневной лимит поднят до 5 ₽.
- Полный прайс выгружен в `docs/prices-2026-10-25.xlsx`.

Уточнение: язык читается из действующей настройки locale.preference перед каждым запросом пользовательской сессии. Правило применяется ко всем видимым сводкам рассуждений и пояснениям действий, а не только к ошибкам; при отсутствии выбора используется язык пользователя.

## 2026-10-26 — Personal API keys and relay regions

- Added the existing personal-key endpoints to the account UI: create, one-time copy, revoke, per-key spend and API base URL.
- Restricted personal API credentials to model inference and model-list requests; account, device, chat, settings and billing APIs remain sign-in-only.
- Removed personal API credentials from the active-device/session list.
- Documented the current all-provider price router and a region-aware rollout design in docs/relay-regions.md.
- Verification pending: run relay tests and JavaScript syntax checks. No production deployment performed.

## 2026-10-27 — Shared chat list and feed

- Folded dated Feed conversations into a single shared conversation while keeping message history and preserving the chosen archive state.
- Added account-wide archive and restore metadata for shared conversations; web list now separates active chats from archive.
- Reuse one persistent desktop Feed session and render Markdown/legacy safe HTML as formatted messages in web.
- Verification: chat catalog tests (7 passed), desktop TypeScript check and web JavaScript syntax checks passed. Telegram topic tests could not collect because the Mac 6 project environment lacks `loguru`.

## 2026-10-27 — Model catalogue and provider comparison

- Moved the provider balance/comparison area above the active model list and placed its filters beside that table.
- Added an admin-only discovery shelf from the daily catalogue: provider-backed model candidates, descriptions, type, context, capabilities, and quote units; candidates are explicitly not marked sellable until route and nanoLika retail price are configured.
- Category classification now consults catalogue modes so image, video, audio, embedding, and rerank entries do not fall into the text bucket by default. Customer model filter pills include category counts; desktop picker adds retail-price/context/name sorting and type facets where a picker actually contains multiple kinds.
- Removed upstream route IDs and procurement prices from the account model-catalogue response; customer-facing model rates remain the nanoLika retail rates.
- Verification: Python compilation and browser JavaScript syntax checks passed. Full desktop typecheck was interrupted after hanging on the mounted project volume; the test run failed at bundle-import tests under the host's Node 20.19.0 because the project requires Node 22+ (`node:zlib` lacks `zstdDecompressSync`). No production files were deployed.

## 2026-10-27 — Full model registry and availability evidence

- Expanded the admin discovery shelf from only rows with a historical supplier quote to every model in the master catalogue, including retired/reference-only rows.
- Separated model purpose, source metadata provider, old price-snapshot offers, configured relay routes, and models exposed by a configured provider's live routing catalogue. A catalogue entry is explicitly not presented as a successful inference test.
- Code-specialized models are no longer inferred from the generic chat capability; unknown fields are listed per row rather than filled with guessed values. Old price snapshots are marked stale after 48 hours.
- The Relay's current catalog route only parses priced chat-compatible offerings from Polza, RouterAI, OpenRouter, AITunnel, and ProxyAPI. Bothub is named as a possible source in the master-catalog builder, but it has no offer in the current provider price snapshot and is not an implemented provider in the runtime router. This needs its own API credentials, endpoint/schema adapter, and live checks before it can be called a connected supplier.
- Installed Node 22.23.4 in the current Mac user's existing nvm installation for project checks; the system/default Node was not changed. Tests and Git state are tracked separately below after they finish.
## 2026-10-27 — Сверка фактической себестоимости с каталогом поставщиков

**Просили:** на каждом использовании сравнивать реальное списание поставщика с ценой в каталоге,
сообщать о расхождении в Telegram, обновлять цены и выбирать более дешёвую совместимую поставку;
зафиксировать базовый курс доллара, чтобы отчёты не плавали каждый день.

**Сделано в исходниках:**
* маршруты OpenRouter запрашивают фактическую стоимость каждого ответа (`usage.include`), а релей
  сравнивает её с расчётом по каталогу;
* если фактическая сумма больше ожидаемой минимум на 25% и на 0,02 ₽, источник и модель на время
  обновления каталога исключаются из маршрутизации. Каталог принудительно обновляется, а следующий
  запрос выберет более дешёвую совместимую поставку, если она доступна;
* добавлено Telegram-оповещение без текста запроса и данных клиента. Для отправки надо задать
  `CLOUD_PRICE_ALERT_TELEGRAM_TOKEN` и `CLOUD_PRICE_ALERT_TELEGRAM_CHAT_ID` в окружении релея;
* базовый курс для расчётов OpenRouter теперь 90 ₽/$, плюс прежние 5,5% на пополнение кредита.
  Значение можно осознанно переопределить через `CLOUD_ROUTING_USD_RUB`;
* убран повторный ключ в status-карточке роутера.

**Проверено:** 39 тестов роутера и учёта прошли, Ruff чистый. В тесте 0,50 $ при курсе 90 ₽ и
сборе 5,5% дают 47,475 ₽. На production VPS подключение по SSH отклонено с `Permission denied
(publickey)`, поэтому живой релей не обновлялся и настройки Telegram-оповещения на сервере не
проверены.

**Важно о 5/17 ₽:** в исходниках клиентская цена и фактическая себестоимость поставщика — отдельные
величины. Кабинет получает розничные ставки из `model_retail_prices` в SQLite (либо из
`sell_in`/`sell_out` модели), а расход поставщика сохраняется отдельно в `provider_cost_rub`.
Поэтому 5/17 ₽ сами по себе не подтверждают цену закупки; живую запись аккаунта и её настройки
нужно сверить на сервере. Нельзя выводить раздельные входную и выходную ставки из одной суммы
`usage.cost`: она отражает конкретную смесь токенов. При расхождении система блокирует маршрут,
а не выдумывает новые ставки.

## 2026-10-27 — Каталог моделей и быстрый список избранного

## 2026-10-27"��y��y� Pricing and routing overlay deployment

- Source commit: `179ee939 fix(models): clarify provider routing and client prices`.
- Applied the changes to the production overlays actually mounted by the relay: admin catalogue,
  plan price preview, current provider and alternative offers, and provider transport diagnostics.
- Updated the CSS cache version in the served HTML so browsers request the new styles.
- Restarted the relay. `/healthz` returns HTTP 200; the relay serves the updated CSS URL and
  JavaScript containing the route and plan-price UI.
- The previous `git apply` attempt silently skipped untracked overlay files. The changes are now
  applied with a patch tool and verified from the live relay responses.
- Source checks: 54 cloud tests passed, Ruff passed, desktop build and typecheck passed.
  The desktop suite passed 408/409; the remaining failure is the existing expectation in
  `tests/sync.test.mjs`.
- No paid inference request was made during deployment. A short controlled request is still needed
  to confirm the provider charge against the displayed price.
## 2026-10-29 · Isolation audit I12: remove IP from contributed samples

Contribution samples now retain only the app and language hints needed for aggregate views;
IP stays in the separate account/security analytics tables. A one-time SQLite migration
removes IP from existing sample metadata. Admin sample responses and the sample list no
longer expose legacy IP values.

Validation: all 56 `cloud/tests/test_cloud.py` tests passed, including sample retention and
the one-time migration. Ruff check and JavaScript syntax check passed; the changed database
and test files pass Ruff formatting. A whole-file format check for `service.py` still reports
pre-existing formatting in unrelated sections, which were left untouched.

## 2026-10-29 · Isolation audit I11: separate shared-memory access from remote control

Added `[hub] memory_sharing`, defaulting to `true` to preserve shared account memory across
devices. It has its own persisted API setting and Devices-page switch. Turning it off refuses
`memory.add`, `memory.search` and `memory.forget`, regardless of the remote-control setting;
turning remote control off alone still permits shared memory. Conversation history, attachments
and task state are unchanged.

Validation: all 28 `tests/test_hub.py` tests, all 129 web tests, TypeScript typecheck, web
production build, Ruff and formatting passed. The bundle reports its existing large main-chunk
warning; the build completed successfully.

## 2026-10-29 · Isolation audit I10: minimize hub `info` by default

Hub `info` now returns public device details by default. The local account name, home and
working directories, hostname, shell and Python version are included only when remote control
is enabled and that device has a standing `remote_control` grant. This does not change the
account's existing `notify` contract or yet confine file operations to configured roots.

Validation: the two focused hub tests passed; Ruff and formatting passed. The remaining I10
file-root work is not included in this change.

## 2026-10-29 · Isolation audit I10: confine hub file actions

The hub's `files`, `file.get` and `file.put` actions now resolve paths inside the target
device's configured workspace. Relative paths resolve from that workspace; absolute paths are
accepted only when they stay inside it. Parent traversal and symlinks resolving outside the
workspace are refused. Other hub actions and the existing `info`/`notify` contract are
unchanged.

Validation: the two focused hub tests passed, covering file operations inside the workspace,
parent traversal, an outside absolute path and a symlink escape. Ruff check, formatting and
`git diff --check` passed. The repository's ignored `.venv` still points to a removed Python 3.11;
checks ran from an isolated Python 3.13 environment and a temporary source snapshot while the
network share was disconnected.

## 2026-10-29 · Isolation audit I2: route durable memory by customer

Added a memory-store router with a separate SQLite database for each trusted Cloud account.
The existing memory database remains the installation scope and is not copied into accounts.
New Cloud-owned threads share durable customer memory across sessions; legacy ownerless threads
remain session-private once an account is signed in. Telegram/MAX private senders use the relay
account identity after sign-in and otherwise get messenger-specific memory. Group messages use
a group scope and never inherit a member's private account memory. Memory API, Hub calls,
search, scheduled maintenance, feed context and profile facts run in the selected scope.

Validation: the full related runtime set passed (174 passed, 1 skipped), then the final scope,
channel and server set passed (78 passed, 1 skipped). Ruff check/format, `git diff --check` and
mypy across 163 source files passed. Commit `8a72a442`. No production deployment or migration of
legacy memory was made.

## 2026-10-29 · Isolation audit I1: separate conversation workspaces

Uploads and tool-generated files now live in a durable workspace scoped to the conversation.
Attachment paths include the thread ID and are resolved by the file API and web viewer; the
composer uploads to its selected thread. Files and Office tools resolve against the active
workspace, shell/Python run from it, and bubblewrap binds the active session writable while
shared library and extra roots stay read-only. Screenshots, device transfers and browser
downloads also use the active workspace. The installation workspace remains the shared
library for installation-scoped chats; account-scoped chats receive their own library.

Existing history is unchanged. A legacy attachment can still be opened only from a thread
whose timeline references that exact path; unowned files were not migrated or reassigned.
The web client was rebuilt into `nanomuse/server/static`.

Validation: 191 related runtime tests passed, 4 skipped; all 129 web tests passed with lint
and TypeScript checks; the production web build completed; Ruff, formatting, `git diff --check`
and mypy across 164 source files passed. A final post-review rerun passed 68 targeted runtime
tests, skipped 5, and repeated Ruff, formatting and mypy successfully. Code commit `9202ca70`.
Shell/Python remain governed by Sentinel; without bubblewrap they retain their existing
sensitive approval gate. Not deployed.

## 2026-10-29 · Isolation audit I1 follow-up and I5: conversation file lifecycle

Thread workspaces now follow a conversation when account switching archives or restores the
main chat, and deleting a conversation removes only its private workspace. A stable workspace
scope is stored separately from memory ownership, so account changes cannot orphan or redirect
existing files. The regression coverage includes an install-scoped main chat moved across two
account switches.

File retention now sweeps each known conversation's private workspace at startup and hourly.
The server's `keep_files_hours` remains the fallback, and Telegram/MAX owners can set a per-chat
override in Settings. Zero keeps files; the shared legacy workspace and account library are not
swept. In a legacy shared-thread configuration, the per-chat control is hidden and rejected.
Configuration documentation was updated.

Validation: 135 related runtime tests passed, 1 skipped; Ruff check and formatting passed;
mypy reported no issues in all 164 source files. No deployment or file migration was performed.

## 2026-10-29 · Isolation audit I6, I7 and I13

Python runtimes now accept an explicit Telegram/MAX access preset from an authenticated paired
device after the existing remote-control check. The preset is stored on the remote task's own
thread; incoming threads are keyed by both peer device and conversation so two devices cannot
reuse each other's session. When no messenger preset was chosen, the runtime keeps its existing
configured access behavior.

Removed the local absolute workspace path and Sentinel mode from the model's system prompt.
The prompt still tells the model to use paths relative to the active conversation workspace,
and memory, named user paths and active-turn context remain intact. Updated English and Chinese
Hub/Cloud docs to distinguish the online messenger runtime from the Cloud relay: the runtime
can continue the shared conversation when the desktop is off, but the relay itself does not run
the agent and local computer files/processes cannot be checked offline.

Validation: targeted Hub, agent prompt and shared-execution tests passed (9 tests); the broader
Hub/agent/shared-execution set passed 61 tests with one unrelated shared-memory test excluded.
The unfiltered run exposed that test failing because its expected account-scoped memory row was
missing; it is outside these edits and remains for separate investigation. Ruff/format and mypy
over 164 source files passed. No deployment was performed.
