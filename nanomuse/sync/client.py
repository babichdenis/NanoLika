"""The relay's conversation-sync API (``/v1/sync/*``, relay 0.19, contract C7), as the
runtime calls it. Thin: every method is one request with the account's key; errors come
back as :class:`nanomuse.cloud.CloudError` (``sync_off`` when the person turned it off on
another device, ``bad_key`` when the sign-in ended)."""

from __future__ import annotations

from typing import Any

from nanomuse.cloud import CloudClient

DEFAULT_PAGE = 500


class SyncClient:
    def __init__(self, cloud: CloudClient):
        self.cloud = cloud

    async def state(self) -> dict[str, Any]:
        """``{enabled, cursor, counts{conversations, messages}, limits{messages, text_bytes}}``."""
        return await self.cloud._request("GET", "/v1/sync/state")

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Off deletes everything the relay stores for the account; on starts empty."""
        return await self.cloud._request("PUT", "/v1/sync/state", {"enabled": bool(enabled)})

    async def changes(
        self, since: int = 0, limit: int = DEFAULT_PAGE, scope: str = "all", tail: int = 0
    ) -> dict[str, Any]:
        """Everything after ``since`` in seq order: ``{cursor, more, conversations[], messages[]}``.
        ``scope="main"`` is the main conversation only; ``tail=K`` with ``since=0`` asks for
        the newest K messages and their conversations (plus ``skipped``) — relay 0.20, C9."""
        query = f"since={int(since)}&limit={int(limit)}"
        if scope != "all":
            query += f"&scope={scope}"
        if tail:
            query += f"&tail={int(tail)}"
        return await self.cloud._request("GET", f"/v1/sync/changes?{query}")

    async def catalog(self, client: str, device: str = "") -> dict[str, Any]:
        from urllib.parse import urlencode

        return await self.cloud._request(
            "GET", "/v1/sync/catalog?" + urlencode({"client": client, "device": device})
        )

    async def messages(
        self, cid: str, after: int = 0, before: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        return await self.cloud._request(
            "GET",
            f"/v1/sync/conversations/{cid}/messages?after={int(after)}&before={int(before)}&limit={int(limit)}",
        )

    async def read(self, cid: str, client: str, through: int) -> dict[str, Any]:
        return await self.cloud._request(
            "POST", f"/v1/sync/conversations/{cid}/read", {"client": client, "through_seq": through}
        )

    async def set_archived(self, cid: str, archived: bool) -> dict[str, Any]:
        return await self.cloud._request(
            "POST", f"/v1/sync/conversations/{cid}/archive", {"archived": archived}
        )

    async def working(self, cid: str, working: bool, device: str) -> None:
        """Presence (C9): a turn started (``True``) or ended (``False``) on that conversation
        here. The relay tells the account's other devices; 204, nothing to read."""
        await self.cloud._request(
            "POST", "/v1/sync/working", {"cid": cid, "working": bool(working), "device": device}
        )

    async def lease(
        self,
        cid: str,
        device: str,
        turn: str,
        ttl: int = 120,
        force: bool = False,
        take: bool = True,
    ) -> dict[str, Any]:
        """Take the chat's turn lease; with `take` another device's live lease is 409 and
        carries the holder, without it the reply says `ok: false`. Old relays have no
        route: the caller treats 404 as "no lease" and goes on."""
        return await self.cloud._request(
            "POST",
            "/v1/sync/lease",
            {
                "cid": cid,
                "device": device,
                "turn": turn,
                "ttl": int(ttl),
                "force": bool(force),
                "take": bool(take),
            },
        )

    async def renew_lease(self, cid: str, device: str, turn: str, ttl: int = 120) -> dict[str, Any]:
        return await self.cloud._request(
            "POST",
            "/v1/sync/lease/renew",
            {"cid": cid, "device": device, "turn": turn, "ttl": int(ttl)},
        )

    async def release_lease(self, cid: str, device: str, turn: str) -> None:
        await self.cloud._request(
            "DELETE", "/v1/sync/lease", {"cid": cid, "device": device, "turn": turn}
        )

    async def claim_delivery(
        self, mid: str, channel: str, chat: str, device: str
    ) -> dict[str, Any]:
        """One machine may deliver one synced message to one channel chat; 409 means another
        machine is doing it. A 404 on an old relay means the relay cannot arbitrate."""
        return await self.cloud._request(
            "POST",
            "/v1/sync/delivery/claim",
            {"mid": mid, "channel": channel, "chat": chat, "device": device},
        )

    async def release_delivery(self, mid: str, channel: str, chat: str, device: str) -> None:
        await self.cloud._request(
            "DELETE",
            "/v1/sync/delivery/claim",
            {"mid": mid, "channel": channel, "chat": chat, "device": device},
        )

    async def push(
        self, device: str, conversations: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """``{cursor, accepted, rejected[{cid|mid, reason, cid_main?}]}``; at most 200 messages."""
        return await self.cloud._request(
            "POST",
            "/v1/sync/changes",
            {"device": device, "conversations": conversations, "messages": messages},
        )

    async def delete_conversation(self, cid: str) -> dict[str, Any]:
        """Tombstone one chat for every device; the relay drops its texts at once."""
        return await self.cloud._request("DELETE", f"/v1/sync/conversations/{cid}")

    async def wipe(self) -> dict[str, Any]:
        """Empty the account's store; the switch stays where it is."""
        return await self.cloud._request("DELETE", "/v1/sync/changes")
