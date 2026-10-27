# Regional relay split: assessment and rollout

## Current behavior

The relay is one account and billing boundary. ProviderRouter builds a cheapest-first fallback
chain from every configured provider with a key and a fresh compatible offer. There is no
provider-region classification or RU/non-RU routing policy today. The configured model provider
is only a fallback preference; automatic routing may select a compatible offer from any configured
provider.

Moving only the process to another country would not create a reliable regional policy. The provider
call, data location, account ledger, logs, failover chain and support access must be considered
separately.

## Recommended shape

Keep one canonical account identity and ledger in the Russian relay. Add explicit provider metadata
(region, legal entity, documents available, data policy, enabled for automatic routing) to the
provider catalog and admin UI. Automatic routing should compare only providers in the allowed region
set for an account/request, and fail closed when metadata is missing. Record billing documents
against the actual provider and contracting entity.

A second non-Russian inference relay can be introduced as a separately deployed data plane only after
the product policy says which requests may cross that boundary. The Russian relay forwarding a prompt
to it still transfers the request contents outside Russia. Keep account balances and payment records
canonical in Russia; return signed usage and cost metadata to the Russian ledger. Do not replicate the
user account database to the foreign relay.

## Safe rollout

1. Classify every provider and confirm its contracting entity and processing locations.
2. Add region allowlists and test that requests cannot fall back across a disallowed boundary.
3. Deploy a Russian provider pool and run domestic-only requests through it.
4. Add foreign routing as an explicit allowed route with separate logs, secrets and health checks.
5. Verify charges, provider-reported costs and retries remain attached to one account.
6. Enable automatic routing only within each allowed region.

This is an engineering design note, not a legal conclusion. A region label alone does not establish
where data is processed or whether a supplier document is sufficient for a particular accounting use.
