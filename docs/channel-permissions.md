# Messenger permissions

Telegram and MAX approval cards expose Allow once, For this conversation, and Always for this action, but only when the pending Sentinel request offers those scopes. These grants are bound to a tool and its target, not blanket permission for every operation. Warning-bearing actions continue to require one-time approval. A callback is accepted only from a paired sender in the chat that received that pending card, with a scope offered for that card. Resolved cards cannot be replayed.

Server-side bot grants and desktop grants belong to separate execution environments. Do not silently copy desktop permissions to the shared server. The receiver continues to enforce its own policy for computer tasks. A Telegram-only user can grant persistent permissions on a card without having a desktop app. Persistent grants can be revoked in the runtime permission settings.

The assistant can develop bot buttons or web-app code in an explicitly available workspace, subject to tools and permissions. This does not grant it infrastructure access, bot credentials, the ability to change its own security policy, or automatic production deployment.


## Readable approval cards

Russian-language cards show the operation category, the original task purpose when available, relevant path/recipient information and explicit cautions. Shell commands are not described as safe or read-only merely because a known program occurs in them. Technical command previews are behind Details. Viewing details never resolves an approval and keeps the decision buttons available. Details use the same paired-chat/pending-card checks and expire with the card. Credential patterns are masked and output is bounded; this is not a guarantee that arbitrary secrets embedded in code can be automatically identified. Missing purpose is not fabricated.

Details expand by editing the original Telegram/MAX card. Decision buttons remain below the expanded text; no separate details message is sent. A bounded in-memory message cache supports this; if absent, a combined replacement card is issued.

## Durable private menu navigation

Telegram/MAX navigation (`home`, `permissions`, `balance`, `language`) uses stable identifiers across restarts. Sensitive changes still use one-use, short-lived tickets bound to paired sender and private chat. Expired action tickets regenerate a fresh menu with an explanation, without granting access. Group/unpaired commands explain how to open the paired private conversation rather than failing silently. Full access retains explicit confirmation; desktop and shared-runtime policy remain separate. MAX acknowledges authenticated callbacks with `/answers` before dispatch.

The authenticated runtime management endpoint `POST /api/channels/{telegram|max}/menu` sends a selected menu through the live manager to an explicitly paired owner. It is not public and does not change permissions. With several owners, sender selection is required.
