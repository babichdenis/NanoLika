# nanoLika website visits

## What the code records

`nanoLika/site/counter.js` sends one event after a page loads to the site's same-origin
`counter.php`. It sends the path, an external referrer host and any `utm_source`,
`utm_medium`, `utm_campaign` and `utm_content` tags. The PHP host supplies the connection
IP and browser user-agent. No cookie is set. The counter does not learn a person's name;
the visitor fingerprint is a short hash of IP and browser, and is only useful for grouping
visits during the 30-minute de-duplication window.

Detailed rows (timestamp, IP, browser, path, source and campaign) are retained for up to
30 days, with an upper bound of 5,000 rows. Aggregates are retained separately. The public
`?stats=1` endpoint contains aggregates only. `?details=1&key=…` is intended for the relay
server only and must never be linked from the website or loaded directly by the browser.
The key lives outside the website root on the PHP host and in `SITE_COUNTER_DETAILS_URL`
on the relay. The admin route `/v1/admin/site-counter` requires the existing admin token.

## Source attribution and Telegram notices

Browsers and messengers often suppress the referring page. Telegram and MAX can be
attributed reliably when their links include `utm_source=telegram` or `utm_source=max`;
the counter also recognizes external Telegram/MAX referrer hosts when they are present.
An untagged or stripped referrer is recorded as direct, not guessed.

The optional site-side Telegram notice reads bot token and chat ID from
`/var/www/u3686432/data/counter-telegram.json` (outside web root). A notice is sent once per
30-minute visitor window and contains only time, page, source and campaign. It deliberately
does not send IP, user-agent, account or message data to Telegram. The bot must be allowed
to message the configured destination. Do not use this alert as proof of a person's identity.

## Watchdog

`cloud/deploy/nanomuse-hk/selfcheck.sh` is the relay watchdog. Health problem strings must
be kept intact; splitting on spaces turns one meaningful problem into misleading fragments.
The script now parses the JSON problem array line by line and uses the generic `ALERT_URL`
only for infrastructure health alerts. Website visit notices are a separate, optional
counter feature and do not change health-check semantics.

## Deployment checklist

1. Publish `counter.php` and `counter.js`; create the admin key and optional Telegram
   destination files outside the web root with permissions readable only by the PHP user.
2. Set `SITE_COUNTER_URL` and `SITE_COUNTER_DETAILS_URL` in the relay environment; never put
   the details URL in page JavaScript, HTML, or a public document.
3. Deploy the relay and open Admin → Visits. Verify that public stats omit `recent`, details
   require the key, the table shows the new rows, and the key is absent from the response.
4. Use UTM-tagged Telegram and MAX links for future campaigns. Previous visits cannot be
   backfilled with a source the browser never sent.
