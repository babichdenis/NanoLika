# Automatic supplier routing

nanoLika Cloud chooses a supplier for a text request before sending it. The client keeps the same public model ID and retail price. Supplier selection does not change the customer's plan, balance owner or permissions.

## Price sources

`provider_routing.py` reads structured catalogs from Polza, RouterAI, OpenRouter and AITunnel. It keeps ordinary input, output and cache-read rates separately, in rubles per million tokens. OpenRouter USD rates use the configured funding exchange rate and fee. Published UTC price schedules are evaluated at request time. The chosen quote is frozen for the request.

An OpenAI-compatible list containing only model IDs, such as ProxyAPI's list, is not treated as a price list. No price is inferred from a scraped minimum, a cache rate or another supplier's invoice. Missing quotes remain explicitly unpriced.

Catalogs refresh at startup and every 30 minutes. A failed refresh retains the previous successful quotes for at most the configured age. The cache contains quotes only, no API keys or message contents. A configured model without any known quote retains its original route; a priced model does not silently fall back to an unknown expensive route by default.

## Selection and retries

The estimate combines the current prompt size with an expected output length, capped by the request's output limit. Output length and actual cache hits are unknown beforehand, so this is an estimate, not a guarantee of the cheapest completed answer. The response's actual usage settles the ledger.

Candidates must name the same model/version. Only explicit aliases and supplier catalog vendor identities are used; equal display names do not establish model equivalence. Tools, image input and declared context/output limits are checked. Known FP4/INT4/Q4 endpoints are excluded unless the operator permits them. Free model variants are not silent replacements for paid requests.

Both streamed and ordinary requests use the same chain. Supplier authentication/balance failures, 429, network failures and server errors can move to the next eligible supplier before response delivery. Malformed customer requests are not repeatedly submitted. After a stream starts, an interrupted response is not replayed at another supplier. Partial output is metered once, using reported usage when available.

Fallback quotes above the configured multiple of the cheapest estimate are excluded. This is an expense ceiling, not a retail margin guarantee. The retail price never follows supplier price increases automatically.

## Accounting

Each chat debit records the actual selected supplier, routed model, quote timestamp and cost basis in `ledger.extra.model_accounting`. Supplier-reported `cost_rub` takes precedence. Documented RUB `usage.cost` is accepted only for the corresponding RUB suppliers. OpenRouter `usage.cost` is USD and is converted explicitly; the raw USD amount, exchange rate and funding fee remain recorded. Otherwise the quote and actual token counts provide a clearly labelled catalog estimate.

The cost in the ledger is updated in the same debit as the retail charge. The customer's sale rate and plan discount remain separate. A supplier change never creates a second customer debit. No contents or keys enter routing telemetry.

## Operator page

`/app/prices/` shows current purchase rates, the retail price, eligible routes and the latest request observation. The route preview uses approximately 1000 input and 512 output tokens, without tools or a cache hit. A real request can select another supplier.

The visibility checkbox is saved in the relay database and affects sign-in responses, the customer model list and public catalog views. Hiding is not revoking an existing conversation or deleting history. The earlier menu editor uses the same visibility store for configured models it can identify. Unknown marketplace entries still need an actual model configuration before they can be offered.

The page also shows supplier balances and verification errors. Balances refresh every two minutes. A fresh known zero excludes the supplier; a failed check is unknown, not zero. The ProxyAPI balance endpoint needs the key's balance-query permission. ProxyAPI connections may share one account and must not be summed. OpenRouter credits are USD; the ruble equivalent is labelled as an estimate.

Authenticated operator endpoints:

- `GET /v1/admin/provider-routing`: status, quotes and balances, without secrets.
- `POST /v1/admin/provider-routing/refresh`: refresh quotes and balances.
- `GET /v1/admin/prices`: menu, live quote preview and recent observations.
- `PUT /v1/admin/prices/models/{id}/visibility`: `{"visible": true}` or `false`.

These require `X-Admin-Token`. Customer endpoints do not expose purchase balances or wholesale cost telemetry.

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `CLOUD_AUTO_ROUTING` | `1` | Enable price-based text routing; `0` restores configured routes |
| `CLOUD_ROUTING_USD_RUB` | relay USD rate | Actual funding exchange rate for USD-priced suppliers |
| `CLOUD_ROUTING_USD_FEE_PCT` | `5.5` | Funding fee; replace with the actual effective fee if different |
| `CLOUD_ROUTING_REFRESH_SECONDS` | `1800` | Catalog refresh interval |
| `CLOUD_ROUTING_MAX_AGE_SECONDS` | `86400` | Maximum quote age |
| `CLOUD_ROUTING_EXPECTED_OUTPUT` | `512` | Output token estimate for route comparison |
| `CLOUD_ROUTING_MAX_FALLBACK_MULTIPLE` | `3` | Maximum fallback estimate divided by cheapest estimate |
| `CLOUD_ROUTING_ALLOW_QUANTIZED` | `0` | Allow known reduced-precision endpoints |
| `CLOUD_ROUTING_ALLOW_UNPRICED_FALLBACK` | `0` | Permit an unpriced configured fallback for a priced model |
| `CLOUD_ROUTING_CACHE` | unset | Persistent catalog JSON path, preferably next to the database |
| `OPENROUTER_BALANCE_KEY` | inference key | Optional dedicated key for the credits endpoint |

Generation of images/video and audio still uses its existing adapters and billing units. Text routing must not be reused for media by comparing token prices with image/second/minute rates. The operator table labels those separate units.

Amounts observed in responses are useful to reconcile supplier statements. One total request amount cannot establish separate input/output prices, so unseen models and future request estimates still require current catalogs.


## ProxyAPI catalog (2026-10-25)

`PROXYAPI_KEY` stays in the server environment, shared by the universal and legacy
OpenAI connectors. Only the universal `proxyapi` connector participates in priced
routing, to avoid counting the same supplier twice.

The supplier's `/v1/models` does not publish prices. The router reads explicit
numeric RUB-per-million breakdowns from the public catalog at
https://www.proxyapi.ru/models/browse, then intersects those model IDs with the
key's authenticated model list. This is a guarded website schema, not a stable
pricing API. A schema/network failure keeps previously verified quotes only up
to the configured maximum age. Models with unhandled extra billing dimensions
are excluded. Batch rates and formatted 'from' labels are never used. Tool and
image support must be present in the public catalog. New offers do not enable
customer visibility or change retail rates.

`/proxyapi/balance` needs the key permission “Запрос баланса”. A 403 is unknown
balance, never zero. The newly supplied key passes normal and streamed GPT-5
mini generation, initially lacked this permission; the post-deploy balance check succeeds. Exact `Model not supported`
400 responses can fall back before any response content is delivered.


### Operator page access

The prices sign-in shell is reachable at `/app/prices/` without an IP allowlist.
Its data endpoints still require `X-Admin-Token`; customer login is not sufficient.
`/app/prices` and legacy `/app/admin/prices/` redirect to the HTTPS canonical URL.
The password field accepts the full administrative password, not the six-digit
email OTP. Session-storage denial falls back to memory for the current page.
