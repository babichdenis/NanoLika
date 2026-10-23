# Channel delivery and device presence

Device `online` is the live app control socket on the hub. It is not a messenger receipt or a chat-sync delivery indicator. An iPhone app may be offline while Telegram/MAX on that phone receives replies. A server is a separate endpoint, not evidence that the phone is online. `not_allowed` denies remote control; it does not deny chat delivery. Preserve these permission checks.

Telegram and MAX show typing from the local turn's `working` status, including model-only answers. Waiting and idle stop it. MAX refreshes its expiring action every 4.5 seconds and cancels the task before closing the transport. Explicit computer handoffs also start typing. Synced historical messages do not start a new typing task. Delivery errors remain separate from presence.

Native device-tool explanations are prepared in source; installing them requires a later application update. No app package was built for this change.
