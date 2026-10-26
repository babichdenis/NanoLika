# Model catalogue and retail prices

The operator's price page is `/app/prices/`. Customer model settings are at
`/app/models/?web=1`. The web chat and desktop model pickers use the same relay
catalogue. No provider credential is included in the catalogue.

## Retail source

`model_pricing.py` reads the configured retail rate and any operator override from
SQLite's `model_retail_prices` setting. Wholesale quotes do not overwrite retail
rates. Editing a rate requires the administrative credential and the current row
revision. A stale revision returns 409 instead of replacing another operator's edit.
The audit log records a saved price. No relay restart is needed for a retail edit.

`commerce` in the catalogue contains RUB rates, the active account discount, effective
rates, price revision, context limits and controls supported by the configured model.
`legacy_cost` identifies a model without a separately configured retail price; the
operator page warns about it. It is not a claim that this is a profitable retail rate.

## Estimates and charges

Authenticated `POST /v1/models/quote` accepts a model and quantities such as input and
output tokens, image size, video seconds or spoken characters. It calls no provider,
runs no generation and creates no ledger entry. It is an estimate for the entered
quantities, excluding additional tools and unbudgeted reasoning.

Text, images, speech and transcription apply the account's active tariff discount.
Provider cost and the customer's charge remain separate in the ledger. Video tasks
store their accepted `sale_uy` at submission; a subsequent price or tariff change does
not change that task's charge. Settlement is idempotent. Pending tasks reserve their
accepted sale amount. Old tasks without the new field retain their historical cost.
An older self-hosted relay without a RUB conversion retains CNY budget accounting;
its RUB quote endpoint reports that RUB prices are unavailable.

Controls are based on the configured model, not invented quality tiers. A model that
only has one priced size does not advertise low/medium/high or other unsupported
modes. Wholesale/provider capability validation still applies to a real generation.

## Model preferences

`GET /v1/me/models` includes `shelf.favourites`. `PUT /v1/me/models` accepts a favourites
list and preserves it when an older client writes only enabled models and defaults.
Visibility remains separate from favourites. The web picker refreshes retail data on
open and includes search, vendor, context and favourites filters. Media cards open
parameters and estimates rather than being sent to the text-completion endpoint.

Desktop source uses the same account catalogue and quote endpoint through its loopback
bridge. Its existing own-provider slots remain available. Updating source/building the
plugin does not install a new application or create release installers.
