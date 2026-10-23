# Customer bot gateway — implementation status

2026-10-23

## Product contract

Only an active, paid subscription with device connectivity grants managed bot gateway access. Basic plans, welcome credit, wallet top-ups and legacy unlimited flags do not grant it. Subscription expiry pauses gateway calls and deliveries; it does not delete customer files. AI consumption and monthly connectivity are separate entitlements.

Code, credentials and the durable inbox live on the owner device or owner-selected cloud. The relay stores project metadata and credential fingerprints; bot credentials and incoming messages transit memory. No server-hosted customer Python, durable message queue or document storage is provided. Owner-run code outside our gateway cannot be disabled by this feature.

## Implementation

The bot_projects tool exposes guide/create/connect/status/pause/resume. The nanolika-bots CLI additionally runs the local handler. Token entry uses hidden local input or a vault reference, never chat text. Source changes expose the tool through the desktop MCP bridge with existing approval tickets. The native app has NOT been rebuilt or installed.

The authenticated /v1/bot-projects API checks ownership and paid eligibility on registration, provider calls, incoming hooks and WebSocket activity. Refresh repairs platform subscriptions after reconnect; disconnect is allowed even after expiry. No foreign webhook is silently replaced. An incoming delivery is acknowledged only after the local inbox commits it. Paused deliveries are discarded; offline retries depend on platform limits. Exactly-once external side effects are not guaranteed.

Initial limits: three projects/account, 120 calls/minute/project, 256 KiB JSON bodies, eight transient deliveries, 15-second local acknowledgement timeout. JSON Bot API requests are supported; raw binary uploads and Mini App hosting are not implemented. Channel administration requires owner-granted platform rights. The owner computer and explicit local runner must remain running; there is no automatic OS startup or 24/7 hosting promise.

## Deployment and blockers

The additive gateway overlay is deployed on the Russian VPS. Existing Telegram/MAX services remain separate. Knowledge is installed in the running assistant. DEMO acquiring does not activate real subscriptions; the compatibility lookup intentionally denies access when no paid subscription exists. The operator has a live T-Bank terminal and will provide its configuration location. Production settlement and subscription activation must be connected and verified before enabling customers.

A network check on 2026-10-23 confirmed that direct api.telegram.org access from the Russian VPS times out. The new customer-project gateway still needs an explicit verified Telegram transport through the Riga bridge; existing primary bot delivery is not evidence that this new path works. Do not advertise the new customer gateway as end-to-end ready until transport and a dedicated real bot are tested. MAX also needs a dedicated project acceptance test.

## Verification

49 local project/tool/skill/MCP tests, 43 gateway/plan/payment tests, and 25 isolated desktop cloud tests passed. Desktop TypeScript typecheck passed. Tests use controlled provider responses; no customer bot was registered and no public test post was sent.

## Русское резюме

Файлы бота остаются у клиента. Оплаченный тариф со связью открывает наш шлюз на месяц; окончание тарифа останавливает шлюз, но не удаляет проект. Основной тариф и пополнение баланса не открывают связь. Нужны включённый компьютер и запущенный обработчик. До боевой оплаты, настройки маршрута Telegram через Ригу и проверки отдельного бота запуск для клиентов не объявляем. Приложение не пересобирали.
