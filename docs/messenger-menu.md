# Messenger menu and conversation access

Telegram and MAX expose `/menu`, `/permissions`, `/balance`, `/pay` and `/language` alongside the existing commands. Payment opens the website without putting an account key in a link. Balance comes from the authenticated Cloud account; DEMO credit is separate. If the relay returns no monetary amount, no ruble balance is invented. The selected reply language is stored per conversation (Russian, English, Simplified Chinese).

Menu callbacks are opaque, short-lived, single-use tickets bound to platform, private chat and sender. Only a paired owner or an explicitly allowed identity can use settings. Wildcard chat access does not grant settings control. Menus never disclose balances in group chats. Preferences live outside the agent workspace with mode 0600. A shared-thread runtime cannot enable per-person permission presets.

## Access modes

- Full access: after a separate explicit confirmation, available actions run without repeated approval questions in that conversation, including file changes and data transmission. Hard deny rules, operating-system permissions, secret handling and tool filesystem boundaries remain. This does not give administrator access to a host. Existing local pending permission cards for that same chat can be resolved by the confirmed full-access choice.
- Write in working folder: file writes and appends are allowed only inside the configured workspace, with resolved paths and symlink escape checks. Other action types still require approval. It is not an unrestricted shell sandbox.
- Read only: the local handler refuses mutations, arbitrary shell/Python and unclassified interactive actions. File read/list/search and known read-only tools remain available within their configured roots.
- As configured in the app: clears the messenger-specific local choice; the computer keeps its actual application settings.

The runtime reads the choice at each tool call, before old grants or auto mode can bypass a restriction. Other conversations keep their settings. An already executing tool is not retroactively cancelled by a mode change.

## Computer compatibility

The desktop source implements optional `channel_permissions` on the account Hub. It resolves only the requesting peer's Telegram/MAX conversation and applies the installed permission preset service to that session. Remote-control trust is checked separately. A successful reply returns the applied mode. An old computer must not silently ignore the request: the bot shows an update notice and refuses to delegate under an unconfirmed selected mode. Choose the app-settings option to retain the old workflow. No native installer was built or installed for this change.

## Deployment and checks

The running Telegram/MAX service has the menu and scoped local guard. Provider command menus were verified through both APIs. The existing image was reused, the service was recreated only after checking it had no active task, and the previous channel files and compose definition were backed up. Both channels report connected. Model replies from a real owner and native preset application still require user acceptance in the chat and an updated desktop runtime respectively.

A real bank-hosted DEMO card flow for 200 RUB returned CONFIRMED, credited once to the isolated test wallet, and did not change the real grant. The confirmed checkout displays “Оплачено · тест”. The temporary test identity was disabled and its key revoked. Production acquiring remains disabled.

Python channel and Sentinel regression tests, dedicated menu/ownership/path tests, desktop task tests and TypeScript type checking cover this change. See the test output rather than treating provider mocks as a real end-to-end chat test.


## Rejected-card check

The bank-hosted DEMO card rejection test returned REJECTED. The authenticated checkout shows “Не получилось оплатить”, including after reload. No test-wallet credit or real-grant change occurred. Rejected, authentication-failed, cancelled and expired orders show a plain failure message rather than a technical bank status. No live payment was attempted.
