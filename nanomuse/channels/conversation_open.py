"""Select an existing account conversation in a paired messenger, without a model turn."""

from __future__ import annotations

import asyncio
from typing import Any

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.telegram_topics import split_chat


async def open_conversation(
    manager: Any, cid: str, channel: str, *, owner: str = ""
) -> dict[str, Any]:
    if channel not in {"telegram", "max"}:
        raise ValueError("Choose Telegram or MAX")
    if not isinstance(cid, str) or not 1 <= len(cid) <= 160:
        raise ValueError("Choose an existing shared conversation")
    svc = manager.svc
    sync = svc.sync
    account = sync.account_id
    if not account or not sync.active:
        raise ValueError("Sign in and enable conversation sync first")
    if channel not in manager.channels or not manager.store.config(channel).enabled:
        raise ValueError("This messenger is not connected")
    pairs = manager.pairing.approved(channel)
    if not owner:
        if len(pairs) != 1:
            raise ValueError("Choose a paired messenger owner explicitly")
        owner = next(iter(pairs))
    entry = pairs.get(owner)
    if not isinstance(entry, dict):
        raise ValueError("The messenger owner is not paired")
    chat = str(entry.get("chat_id") or owner)
    message = InboundMessage(channel=channel, chat_id=chat, sender_id=owner)
    if not manager.menu.private_owner(message):
        raise ValueError("A paired private messenger conversation is required")
    lock = getattr(manager, "_conversation_open_lock", None)
    if lock is None:
        lock = manager._conversation_open_lock = asyncio.Lock()
    async with lock:
        await manager.picker._cloud_read(sync.pull)
        if account != sync.account_id or cid not in sync.catalog.rows:
            raise LookupError("This conversation is not available in this account")
        thread = sync.thread_of(cid)
        if thread is None or not sync._mine(thread):
            raise LookupError("This conversation is not available in this account")
        await manager.picker._cloud_read(
            sync.catalog.open, thread.id, manager.picker.reader(channel, chat)
        )
        if account != sync.account_id or not sync._mine(thread):
            raise LookupError("The signed-in account changed")
        if channel == "telegram":
            if not manager.picker.topics.enabled():
                raise ValueError("Telegram topics are not enabled")
            await manager.picker.topics.open(message, thread)
            topic = next(
                (
                    split_chat(endpoint)[1]
                    for name, endpoint in manager.picker.topics.targets(thread.id)
                    if name == channel and split_chat(endpoint)[0] == chat
                ),
                0,
            )
            if not topic:
                raise ValueError("The topic was not confirmed")
            return {
                "opened": True,
                "channel": channel,
                "cid": cid,
                "title": thread.title,
                "topic_id": topic,
                "client_view_changed": False,
            }
        # MAX has one bot dialogue. Selecting is not clearing or deleting that dialogue.
        manager.picker.bind(message, thread.id)
        for event in thread.timeline.events:
            mid, role = str(event.get("mid") or ""), str(event.get("type") or "")
            if mid and role in {"user", "assistant"}:
                manager.sync_receipts.mark(manager.sync_receipts.key(channel, chat, mid, role))
        await manager.picker.preview(message, thread)
        return {
            "opened": True,
            "channel": channel,
            "cid": cid,
            "title": thread.title,
            "client_view_changed": False,
        }
